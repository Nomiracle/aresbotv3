"""Polymarket 专用交易引擎 - 处理市场切换时的状态重置"""

from __future__ import annotations

from typing import Any, Dict, Optional

from worker.domain.order import Order, OrderState
from worker.trading_engine import TradingEngine


class PolymarketTradingEngine(TradingEngine):
    """在市场切换时清空引擎交易状态，防止旧仓位触发补单."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if hasattr(self.exchange, "on_market_switch"):
            self.exchange.on_market_switch = self._reset_trading_state

    def _reset_trading_state(self, liquidation_result: Optional[Dict[str, Any]] = None) -> None:
        """清空所有挂单跟踪和持仓记录，清算持仓入库."""
        # 清空前先为所有持仓生成卖出记录
        if liquidation_result and self.state_store:
            positions = self.position_tracker.get_all_positions()
            for pos in positions:
                pnl = (liquidation_result["price"] - pos.entry_price) * pos.quantity
                self.risk_manager.record_trade_result(pnl)
                sell_order = Order(
                    order_id=f"liquidation-{pos.order_id}",
                    symbol=pos.symbol,
                    side="sell",
                    price=liquidation_result["price"],
                    quantity=pos.quantity,
                    grid_index=pos.grid_index,
                    state=OrderState.FILLED,
                    related_order_id=pos.order_id,
                )
                sell_order.update_fill(pos.quantity, liquidation_result["price"])
                self._save_trade(sell_order, liquidation_result["price"], pnl=pnl)
                self.log.info(
                    "清算入库: %s, 价格=%s, 盈亏=%s",
                    sell_order.order_id, liquidation_result["price"], pnl,
                )

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
