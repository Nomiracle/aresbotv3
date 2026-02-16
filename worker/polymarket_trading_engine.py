"""Polymarket 专用交易引擎 - 处理市场切换时的状态重置"""

from __future__ import annotations

from worker.trading_engine import TradingEngine


class PolymarketTradingEngine(TradingEngine):
    """在市场切换时清空引擎交易状态，防止旧仓位触发补单."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if hasattr(self.exchange, "on_market_switch"):
            self.exchange.on_market_switch = self._reset_trading_state

    def _reset_trading_state(self) -> None:
        """清空所有挂单跟踪和持仓记录."""
        with self._lock:
            buy_count = len(self._pending_buys)
            sell_count = len(self._pending_sells)
            pos_count = self.position_tracker.get_position_count()
            self._pending_buys.clear()
            self._pending_sells.clear()
            self._stop_loss_triggered.clear()
        self.position_tracker.clear()
        self.log.info(
            "市场切换: 清空引擎状态 buys=%d sells=%d positions=%d",
            buy_count, sell_count, pos_count,
        )
