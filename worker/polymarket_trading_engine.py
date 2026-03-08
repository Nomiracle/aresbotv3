"""Polymarket 专用交易引擎 - 处理市场切换时的状态重置"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from worker.domain.order import Order, OrderState
from worker.trading_engine import TradingEngine


class PolymarketTradingEngine(TradingEngine):
    """在市场切换时清空引擎交易状态，防止旧仓位触发补单."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if hasattr(self.exchange, "on_market_switch"):
            self.exchange.on_market_switch = self._reset_trading_state
        self._delayed_check_timer: Optional[threading.Timer] = None

    def _reset_trading_state(self, liquidation_result: Optional[Dict[str, Any]] = None) -> None:
        """清空所有挂单跟踪和持仓记录，清算持仓入库."""
        old_token_id = getattr(self.exchange, "_token_id", None)
        old_market_slug = getattr(self.exchange, "_market_slug", None)

        with self._lock:
            order_ids = [*self._pending_buys.keys(), *self._pending_sells.keys()]

        if order_ids:
            try:
                results = self.exchange.cancel_batch_orders(order_ids)
                canceled = sum(1 for r in results if r.success)
                self.log.info(
                    "市场切换: 按ID取消挂单 total=%d canceled=%d",
                    len(order_ids), canceled,
                )
            except Exception as e:
                self.log.warning("市场切换: 按ID取消挂单失败: %s", e)

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

        # 启动延迟检查任务
        if old_token_id:
            market_close_buffer = getattr(self.exchange, "_market_close_buffer", 60)
            delay_seconds = max(30, market_close_buffer + 10)
            self._schedule_delayed_market_check(old_token_id, old_market_slug, delay_seconds)

    def _schedule_delayed_market_check(self, old_token_id: str, old_market_slug: Optional[str], delay_seconds: int) -> None:
        """启动延迟检查任务."""
        if self._delayed_check_timer is not None:
            try:
                self._delayed_check_timer.cancel()
            except Exception:
                pass

        def delayed_check():
            try:
                self._check_old_market_state(old_token_id, old_market_slug)
            except Exception as e:
                self.log.error("延迟检查旧市场状态失败: %s", e, exc_info=True)

        self._delayed_check_timer = threading.Timer(delay_seconds, delayed_check)
        self._delayed_check_timer.daemon = True
        self._delayed_check_timer.start()
        slug_display = old_market_slug or old_token_id[:16]
        self.log.info(
            "市场切换: 已启动延迟检查任务，将在%d秒后检查旧市场 slug=%s",
            delay_seconds, slug_display,
        )

    def _check_old_market_state(self, old_token_id: str, old_market_slug: Optional[str]) -> None:
        """检查旧市场的订单和持仓状态."""
        slug_display = old_market_slug or old_token_id[:16]
        self.log.info("延迟检查: 开始检查旧市场状态 slug=%s", slug_display)

        # 查询旧市场的所有订单
        old_market_orders = self.exchange.get_open_orders(token_id=old_token_id)
        if not old_market_orders:
            self.log.info("延迟检查: 旧市场无活跃订单")
        else:
            self.log.warning("延迟检查: 发现旧市场订单 count=%d", len(old_market_orders))

        # 处理订单
        uncanceled_orders = []
        filled_buy_orders = []

        from worker.core.base_exchange import OrderStatus
        for ex_order in old_market_orders:
            if ex_order.status == OrderStatus.PLACED:
                uncanceled_orders.append(ex_order.order_id)
                self.log.warning(
                    "延迟检查: 发现未取消订单 order_id=%s side=%s price=%s qty=%s",
                    ex_order.order_id, ex_order.side, ex_order.price, ex_order.quantity,
                )
            elif ex_order.status == OrderStatus.FILLED and ex_order.side == "buy":
                filled_buy_orders.append(ex_order)
                self.log.warning(
                    "延迟检查: 发现已成交买单 order_id=%s price=%s qty=%s",
                    ex_order.order_id, ex_order.price, ex_order.filled_quantity,
                )

        # 取消未取消的订单
        if uncanceled_orders:
            try:
                results = self.exchange.cancel_batch_orders(uncanceled_orders)
                canceled = sum(1 for r in results if r.success)
                self.log.warning(
                    "延迟检查: 取消未取消订单 total=%d canceled=%d",
                    len(uncanceled_orders), canceled,
                )
            except Exception as e:
                self.log.error("延迟检查: 取消订单失败: %s", e)

        # 处理已成交的买单
        if filled_buy_orders:
            self._handle_filled_buy_orders(filled_buy_orders, old_token_id)

        # 检查并清算持仓
        self._check_and_liquidate_old_token_balance(old_token_id, slug_display)

        self.log.info("延迟检查: 完成旧市场状态检查")

    def _handle_filled_buy_orders(self, filled_buy_orders: list, old_token_id: str) -> None:
        """处理已成交的买单：记录到数据库."""
        for ex_order in filled_buy_orders:
            try:
                grid_index = 1
                if self._current_price and self._current_price > 0:
                    grid_index = self.strategy.infer_grid_index_from_price(
                        order_price=ex_order.price,
                        current_price=self._current_price,
                        is_buy=True,
                    )

                buy_order = Order(
                    order_id=ex_order.order_id,
                    symbol=ex_order.symbol or self.strategy.config.symbol,
                    side="buy",
                    price=ex_order.price,
                    quantity=ex_order.quantity,
                    grid_index=grid_index,
                    state=OrderState.FILLED,
                )
                buy_order.update_fill(ex_order.filled_quantity, ex_order.price)
                self._save_trade(
                    buy_order,
                    ex_order.price,
                    raw_order_info={
                        "market_switch_delayed_check": True,
                        "old_token_id": old_token_id,
                    },
                )
                self.log.warning(
                    "延迟检查: 买单已成交，记录到数据库 order_id=%s price=%s qty=%s grid=%s",
                    ex_order.order_id, ex_order.price, ex_order.filled_quantity, grid_index,
                )
            except Exception as e:
                self.log.error("延迟检查: 保存买单交易失败 order_id=%s error=%s", ex_order.order_id, e)

    def _check_and_liquidate_old_token_balance(self, old_token_id: str, slug_display: str) -> None:
        """检查并清算旧市场的 token 余额."""
        try:
            # 查询旧 token 的余额
            balance = self._get_token_balance(old_token_id)
            if balance < 1.0:
                self.log.debug("延迟检查: 旧市场无持仓 slug=%s balance=%s", slug_display, balance)
                return

            self.log.warning(
                "延迟检查: 发现旧市场持仓 slug=%s balance=%s，尝试平仓",
                slug_display, balance,
            )

            # 尝试以市价单平仓
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            signed = self.exchange._client.create_market_order(
                MarketOrderArgs(
                    token_id=old_token_id,
                    amount=balance,
                    side=SELL,
                    price=0.01,  # 滑点保护
                    order_type=OrderType.FOK,
                ),
            )
            raw_response = self.exchange._post_order(signed, OrderType.FOK)

            # 查询成交价
            fill_price = self.exchange._query_fill_price(raw_response)
            self.log.warning(
                "延迟检查: 旧市场持仓已平仓 slug=%s qty=%s fill_price=%s",
                slug_display, balance, fill_price,
            )

            # 记录到数据库（作为卖出交易，但无法关联到具体买单）
            if self.state_store:
                sell_order = Order(
                    order_id=f"delayed-liquidation-{old_token_id[:8]}-{int(time.time())}",
                    symbol=self.strategy.config.symbol,
                    side="sell",
                    price=fill_price,
                    quantity=balance,
                    grid_index=0,
                    state=OrderState.FILLED,
                    related_order_id=None,
                )
                sell_order.update_fill(balance, fill_price)
                self._save_trade(
                    sell_order,
                    fill_price,
                    raw_order_info={
                        "delayed_liquidation": True,
                        "old_token_id": old_token_id,
                    },
                )
        except Exception as e:
            self.log.error("延迟检查: 清算旧市场持仓失败 slug=%s error=%s", slug_display, e)

    def _get_token_balance(self, token_id: str) -> float:
        """查询指定 token 的余额（复用 exchange 的方法）."""
        if hasattr(self.exchange, "_get_token_balance"):
            return self.exchange._get_token_balance(token_id)
        return 0.0
