"""双边交易引擎 - 在 TradingEngine 基础上增加做空侧管理"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from worker.core.base_exchange import EditOrderRequest, OrderRequest, OrderStatus
from worker.domain.order import Order, OrderState
from worker.domain.position import PositionEntry
from worker.strategies.bilateral_grid_strategy import BilateralGridStrategy
from worker.trading_engine import TradingEngine


@dataclass
class ShortPositionEntry:
    order_id: str
    symbol: str
    quantity: float
    entry_price: float
    grid_index: int
    created_at: datetime = field(default_factory=datetime.now)


class BilateralTradingEngine(TradingEngine):
    """双边交易引擎

    做多侧由父类 TradingEngine 管理。
    做空侧由本类独立管理，使用负 grid_index 区分。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending_short_opens: Dict[str, Order] = {}
        self._pending_short_closes: Dict[str, Order] = {}
        self._short_positions: Dict[str, ShortPositionEntry] = {}

    @property
    def _bilateral_strategy(self) -> BilateralGridStrategy:
        return self.strategy  # type: ignore[return-value]

    # ==================== Override: 新订单 ====================

    def _check_new_orders(self) -> None:
        """做多侧 + 做空侧开仓"""
        # 做多侧：注入 positionSide=LONG
        self._check_long_orders()
        # 做空侧
        self._check_short_orders()

    def _check_long_orders(self) -> None:
        """做多侧开仓（复用父类逻辑，注入 positionSide）"""
        with self._lock:
            pending_buys = dict(self._pending_buys)
            pending_sells = dict(self._pending_sells)

        can_open, reason = self.risk_manager.can_open_position(
            self.position_tracker.get_position_count()
        )
        if not can_open:
            self.log.debug("跳过做多开仓: %s", reason)
            return

        positions = self.position_tracker.get_all_positions()
        decisions = self.strategy.should_buy_batch(
            current_price=self._current_price,
            pending_buy_orders=pending_buys,
            pending_sell_orders=pending_sells,
            positions=positions,
        )
        if not decisions:
            return

        rules = self._rules
        order_requests: list[OrderRequest] = []
        decision_map: list = []

        for decision in decisions:
            aligned_price = self.exchange.align_price(decision.price, rules)
            aligned_qty = self.exchange.align_quantity(decision.quantity, rules)
            order_requests.append(OrderRequest(
                side="buy", price=aligned_price, quantity=aligned_qty,
                params={"positionSide": "LONG"},
            ))
            decision_map.append((decision, aligned_price, aligned_qty))

        self.log.debug("批量下做多买单 count=%s", len(order_requests))
        results = self.exchange.place_batch_orders(order_requests)

        with self._lock:
            for idx, result in enumerate(results):
                decision, aligned_price, aligned_qty = decision_map[idx]
                if result.success and result.order_id:
                    placed_price = result.filled_price if result.filled_price is not None else aligned_price
                    placed_qty = result.filled_quantity if result.filled_quantity is not None else aligned_qty
                    order = Order(
                        order_id=result.order_id,
                        symbol=self.strategy.config.symbol,
                        side="buy", price=placed_price, quantity=placed_qty,
                        grid_index=decision.grid_index, state=OrderState.PLACED,
                    )
                    self._pending_buys[result.order_id] = order
                    self.log.info("做多买单已下: %s, 价格=%s, 网格=%s", result.order_id, placed_price, decision.grid_index)
                else:
                    error_msg = result.error or "下单失败"
                    self.log.warning("做多买单失败 price=%s error=%s", aligned_price, error_msg)

    def _check_short_orders(self) -> None:
        """做空侧开仓"""
        with self._lock:
            pending_short_opens = dict(self._pending_short_opens)
            pending_short_closes = dict(self._pending_short_closes)
            short_positions = list(self._short_positions.values())

        short_pos_entries = [
            PositionEntry(
                symbol=sp.symbol, quantity=sp.quantity,
                entry_price=sp.entry_price, order_id=sp.order_id,
                grid_index=sp.grid_index, created_at=sp.created_at,
            )
            for sp in short_positions
        ]

        decisions = self._bilateral_strategy.should_short_batch(
            current_price=self._current_price,
            pending_short_opens=pending_short_opens,
            pending_short_closes=pending_short_closes,
            short_positions=short_pos_entries,
        )
        if not decisions:
            return

        rules = self._rules
        order_requests: list[OrderRequest] = []
        decision_map: list = []

        for decision in decisions:
            aligned_price = self.exchange.align_price(decision.price, rules)
            aligned_qty = self.exchange.align_quantity(decision.quantity, rules)
            order_requests.append(OrderRequest(
                side="sell", price=aligned_price, quantity=aligned_qty,
                params={"positionSide": "SHORT"},
            ))
            decision_map.append((decision, aligned_price, aligned_qty))

        self.log.debug("批量下做空卖单 count=%s", len(order_requests))
        results = self.exchange.place_batch_orders(order_requests)

        with self._lock:
            for idx, result in enumerate(results):
                decision, aligned_price, aligned_qty = decision_map[idx]
                if result.success and result.order_id:
                    placed_price = result.filled_price if result.filled_price is not None else aligned_price
                    placed_qty = result.filled_quantity if result.filled_quantity is not None else aligned_qty
                    order = Order(
                        order_id=result.order_id,
                        symbol=self.strategy.config.symbol,
                        side="sell", price=placed_price, quantity=placed_qty,
                        grid_index=decision.grid_index, state=OrderState.PLACED,
                    )
                    self._pending_short_opens[result.order_id] = order
                    self.log.info("做空卖单已下: %s, 价格=%s, 网格=%s", result.order_id, placed_price, decision.grid_index)
                else:
                    error_msg = result.error or "下单失败"
                    self.log.warning("做空卖单失败 price=%s error=%s", aligned_price, error_msg)

    # ==================== Override: 订单同步 ====================

    def _sync_orders(self) -> None:
        """做多侧（注入 positionSide）+ 做空侧订单同步"""
        # 做多侧：调用父类同步
        self._sync_long_orders()
        # 做空侧
        self._sync_short_orders()

    def _sync_long_orders(self) -> None:
        """做多侧同步 - 复用父类逻辑但卖单注入 positionSide=LONG"""
        try:
            exchange_orders = self.exchange.get_open_orders()
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            with self._lock:
                pending_ids = list(self._pending_buys.keys()) + list(self._pending_sells.keys())

            sell_requests: list[OrderRequest] = []
            sell_meta: list[Order] = []
            rules = self._rules
            fee_rate = self._fee

            for order_id in pending_ids:
                ex_order = exchange_order_map.get(order_id)
                if ex_order is None:
                    ex_order = self.exchange.get_order(order_id)
                    if ex_order is None:
                        continue

                if ex_order.status == OrderStatus.FILLED:
                    with self._lock:
                        buy_order = self._pending_buys.pop(order_id, None)
                        sell_order = self._pending_sells.pop(order_id, None) if buy_order is None else None

                    if buy_order is not None:
                        buy_order.update_fill(ex_order.filled_quantity, ex_order.price)
                        buy_order.extra['fee'] = ex_order.extra.get('fee')
                        buy_order.extra['fee_paid_externally'] = ex_order.fee_paid_externally
                        raw_order_info = self._build_raw_order_info(ex_order)
                        buy_order.extra['raw_order_info'] = raw_order_info
                        filled_price = ex_order.price
                        self._save_trade(buy_order, filled_price, raw_order_info=raw_order_info)
                        self.position_tracker.add_position(
                            order_id=buy_order.order_id, symbol=buy_order.symbol,
                            quantity=buy_order.filled_quantity, entry_price=filled_price,
                            grid_index=buy_order.grid_index,
                        )
                        decision = self.strategy.should_sell(
                            buy_price=filled_price,
                            buy_quantity=buy_order.filled_quantity,
                            current_price=self._current_price,
                        )
                        if decision:
                            sell_qty = buy_order.filled_quantity if ex_order.fee_paid_externally else buy_order.filled_quantity * (1 - fee_rate)
                            aligned_price = self.exchange.align_price(decision.price, rules)
                            aligned_qty = self.exchange.align_quantity(sell_qty, rules)
                            sell_requests.append(OrderRequest(
                                side="sell", price=aligned_price, quantity=aligned_qty,
                                params={"positionSide": "LONG", "reduceOnly": True},
                            ))
                            sell_meta.append(buy_order)
                        self.log.info("做多买单成交: %s, 价格=%s", buy_order.order_id, filled_price)
                        self._emit_notify("order_filled", f"做多买单成交 #{buy_order.grid_index or ''}", f"价格: {filled_price}")

                    elif sell_order is not None:
                        self._handle_sell_filled(sell_order, ex_order)

                elif ex_order.status == OrderStatus.CANCELLED:
                    with self._lock:
                        self._pending_buys.pop(order_id, None)
                        self._pending_sells.pop(order_id, None)

                elif ex_order.status == OrderStatus.PARTIALLY_FILLED:
                    with self._lock:
                        order = self._pending_buys.get(order_id) or self._pending_sells.get(order_id)
                        if order:
                            order.update_fill(ex_order.filled_quantity, ex_order.price)

            if sell_requests:
                results = self.exchange.place_batch_orders(sell_requests)
                with self._lock:
                    for idx, result in enumerate(results):
                        buy_order = sell_meta[idx]
                        if result.success and result.order_id:
                            placed_price = result.filled_price if result.filled_price is not None else sell_requests[idx].price
                            placed_qty = result.filled_quantity if result.filled_quantity is not None else sell_requests[idx].quantity
                            sell_order = Order(
                                order_id=result.order_id, symbol=self.strategy.config.symbol,
                                side="sell", price=placed_price, quantity=placed_qty,
                                grid_index=buy_order.grid_index, state=OrderState.PLACED,
                                related_order_id=buy_order.order_id,
                            )
                            self._pending_sells[result.order_id] = sell_order
                        else:
                            self.log.warning("做多卖单失败 buy=%s error=%s", buy_order.order_id, result.error)

        except Exception as e:
            self.log.warning("做多侧同步失败: %s", e, exc_info=True)

    def _sync_short_orders(self) -> None:
        """做空侧订单同步"""
        try:
            exchange_orders = self.exchange.get_open_orders()
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            with self._lock:
                pending_ids = list(self._pending_short_opens.keys()) + list(self._pending_short_closes.keys())

            close_requests: list[OrderRequest] = []
            close_meta: list[Order] = []
            rules = self._rules

            for order_id in pending_ids:
                ex_order = exchange_order_map.get(order_id)
                if ex_order is None:
                    ex_order = self.exchange.get_order(order_id)
                    if ex_order is None:
                        continue

                if ex_order.status == OrderStatus.FILLED:
                    with self._lock:
                        short_open = self._pending_short_opens.pop(order_id, None)
                        short_close = self._pending_short_closes.pop(order_id, None) if short_open is None else None

                    if short_open is not None:
                        # 做空开仓成交 → 记录空头持仓 → 下买单平仓
                        short_open.update_fill(ex_order.filled_quantity, ex_order.price)
                        filled_price = ex_order.price
                        raw_order_info = self._build_raw_order_info(ex_order)
                        self._save_trade(short_open, filled_price, raw_order_info=raw_order_info)

                        with self._lock:
                            self._short_positions[short_open.order_id] = ShortPositionEntry(
                                order_id=short_open.order_id, symbol=short_open.symbol,
                                quantity=short_open.filled_quantity, entry_price=filled_price,
                                grid_index=short_open.grid_index,
                            )

                        decision = self._bilateral_strategy.should_close_short(
                            open_price=filled_price,
                            open_quantity=short_open.filled_quantity,
                            current_price=self._current_price,
                        )
                        if decision:
                            aligned_price = self.exchange.align_price(decision.price, rules)
                            aligned_qty = self.exchange.align_quantity(decision.quantity, rules)
                            close_requests.append(OrderRequest(
                                side="buy", price=aligned_price, quantity=aligned_qty,
                                params={"positionSide": "SHORT", "reduceOnly": True},
                            ))
                            close_meta.append(short_open)

                        self.log.info("做空开仓成交: %s, 价格=%s", short_open.order_id, filled_price)
                        self._emit_notify("order_filled", f"做空开仓 #{abs(short_open.grid_index)}", f"价格: {filled_price}")

                    elif short_close is not None:
                        # 做空平仓成交 → 计算 PnL → 移除持仓
                        short_close.update_fill(ex_order.filled_quantity, ex_order.price)
                        filled_price = ex_order.price

                        with self._lock:
                            sp = self._short_positions.pop(short_close.related_order_id, None)

                        pnl = None
                        if sp:
                            pnl = (sp.entry_price - filled_price) * short_close.filled_quantity
                            self.risk_manager.record_trade_result(pnl)

                        self._save_trade(short_close, filled_price, pnl=pnl, raw_order_info=self._build_raw_order_info(ex_order))
                        self.log.info("做空平仓成交: %s, 价格=%s, 盈亏=%s", short_close.order_id, filled_price, pnl)
                        pnl_str = f"{pnl:+.6f}" if pnl is not None else "N/A"
                        self._emit_notify("order_filled", f"做空平仓 #{abs(short_close.grid_index)}", f"价格: {filled_price}, 盈亏: {pnl_str}")

                elif ex_order.status == OrderStatus.CANCELLED:
                    with self._lock:
                        self._pending_short_opens.pop(order_id, None)
                        self._pending_short_closes.pop(order_id, None)

                elif ex_order.status == OrderStatus.PARTIALLY_FILLED:
                    with self._lock:
                        order = self._pending_short_opens.get(order_id) or self._pending_short_closes.get(order_id)
                        if order:
                            order.update_fill(ex_order.filled_quantity, ex_order.price)

            if close_requests:
                results = self.exchange.place_batch_orders(close_requests)
                with self._lock:
                    for idx, result in enumerate(results):
                        open_order = close_meta[idx]
                        if result.success and result.order_id:
                            placed_price = result.filled_price if result.filled_price is not None else close_requests[idx].price
                            placed_qty = result.filled_quantity if result.filled_quantity is not None else close_requests[idx].quantity
                            close_order = Order(
                                order_id=result.order_id, symbol=self.strategy.config.symbol,
                                side="buy", price=placed_price, quantity=placed_qty,
                                grid_index=open_order.grid_index, state=OrderState.PLACED,
                                related_order_id=open_order.order_id,
                            )
                            self._pending_short_closes[result.order_id] = close_order
                            self.log.info("做空平仓单已下: %s, 价格=%s", result.order_id, placed_price)
                        else:
                            self.log.warning("做空平仓单失败 open=%s error=%s", open_order.order_id, result.error)

        except Exception as e:
            self.log.warning("做空侧同步失败: %s", e, exc_info=True)

    # ==================== Override: 改价 ====================

    def _check_reprice(self) -> None:
        """做多侧 + 做空侧改价"""
        self._check_reprice_long()
        self._check_reprice_short()

    def _check_reprice_long(self) -> None:
        """做多侧改价（复用父类逻辑）"""
        super()._check_reprice()

    def _check_reprice_short(self) -> None:
        """做空侧开仓单改价"""
        with self._lock:
            short_opens = list(self._pending_short_opens.values())

        edit_requests: list[EditOrderRequest] = []
        order_map: list[Order] = []
        rules = self._rules

        for order in short_opens:
            new_price = self._bilateral_strategy.should_reprice_short(
                order_price=order.price,
                current_price=self._current_price,
                grid_index=order.grid_index,
            )
            if new_price:
                aligned_price = self.exchange.align_price(new_price, rules)
                edit_requests.append(EditOrderRequest(
                    order_id=order.order_id, side="sell",
                    price=aligned_price, quantity=order.quantity,
                ))
                order_map.append(order)

        if not edit_requests:
            return

        self.log.debug("做空侧改价 count=%s", len(edit_requests))
        results = self.exchange.edit_batch_orders(edit_requests)

        with self._lock:
            for idx, result in enumerate(results):
                old_order = order_map[idx]
                new_price = edit_requests[idx].price
                self._pending_short_opens.pop(old_order.order_id, None)

                if result.success and result.order_id:
                    new_order = Order(
                        order_id=result.order_id, symbol=old_order.symbol,
                        side="sell", price=new_price, quantity=old_order.quantity,
                        grid_index=old_order.grid_index, state=OrderState.PLACED,
                    )
                    self._pending_short_opens[result.order_id] = new_order
                    self.log.info("做空改价成功: %s -> %s, 价格=%s", old_order.order_id, result.order_id, new_price)
                else:
                    self.log.warning("做空改价失败: %s", old_order.order_id)

    # ==================== Override: 止损 ====================

    def _check_stop_loss(self) -> None:
        """做多侧 + 做空侧止损"""
        super()._check_stop_loss()
        self._check_short_stop_loss()

    def _check_short_stop_loss(self) -> None:
        """做空侧止损：价格上涨超过阈值"""
        stop_loss_pct = self.risk_manager.config.stop_loss_percent
        if stop_loss_pct is None:
            return

        with self._lock:
            short_positions = list(self._short_positions.values())

        for sp in short_positions:
            if sp.order_id in self._stop_loss_triggered:
                continue
            # 做空止损：当前价格 > 入场价 * (1 + stop_loss%)
            threshold = sp.entry_price * (1 + stop_loss_pct / 100.0)
            if self._current_price > threshold:
                self._stop_loss_triggered.append(sp.order_id)
                self._execute_short_stop_loss(sp)

    def _execute_short_stop_loss(self, sp: ShortPositionEntry) -> None:
        """执行做空止损"""
        self.log.warning("做空止损触发: %s, 入场=%s, 当前=%s", sp.order_id, sp.entry_price, self._current_price)
        self._emit_notify("stop_loss_triggered", "做空止损触发", f"持仓: {sp.order_id}, 入场: {sp.entry_price}")

        # 取消对应的平仓挂单
        cancel_ids = []
        with self._lock:
            for order_id, order in list(self._pending_short_closes.items()):
                if order.related_order_id == sp.order_id:
                    cancel_ids.append(order_id)
                    self._pending_short_closes.pop(order_id, None)

        if cancel_ids:
            self.exchange.cancel_batch_orders(cancel_ids)

        # 市价平仓（用略高于当前价的限价单）
        rules = self._rules
        stop_price = self.exchange.align_price(self._current_price * 1.001, rules)
        stop_qty = self.exchange.align_quantity(sp.quantity, rules)

        if stop_qty <= 0:
            return

        results = self.exchange.place_batch_orders([
            OrderRequest(side="buy", price=stop_price, quantity=stop_qty,
                         params={"positionSide": "SHORT", "reduceOnly": True})
        ])
        if results and results[0].success:
            self.log.info("做空止损单已下: %s", results[0].order_id)

    # ==================== Override: 定期同步 ====================

    def _periodic_sync(self) -> None:
        """父类定期同步 + 做空侧清理"""
        super()._periodic_sync()

    # ==================== Override: 停止 ====================

    def stop(self) -> None:
        """停止引擎，取消所有挂单"""
        self.log.info("停止双边交易引擎")
        self._running = False

        with self._lock:
            order_ids = [
                *self._pending_buys.keys(),
                *self._pending_sells.keys(),
                *self._pending_short_opens.keys(),
                *self._pending_short_closes.keys(),
            ]
        if order_ids:
            self.exchange.cancel_batch_orders(order_ids)
        with self._lock:
            self._pending_buys.clear()
            self._pending_sells.clear()
            self._pending_short_opens.clear()
            self._pending_short_closes.clear()

        self._update_status(force=True, source="stop")
        try:
            self.exchange.close()
        except Exception as e:
            self.log.warning("关闭交易所连接失败: %s", e)

    # ==================== Override: 恢复挂单 ====================

    def _recover_open_orders(self) -> None:
        """恢复挂单 - 做多侧用父类，做空侧暂不恢复（需要 positionSide 信息）"""
        super()._recover_open_orders()

    # ==================== Override: 状态更新 ====================

    def _update_status(self, force: bool = False, source: str = "periodic") -> None:
        """更新状态，包含做空侧统计"""
        if self.on_status_update is None:
            return

        import time
        now = time.time()
        if not force and now - self._last_status_update_time < self._status_update_interval:
            return
        self._last_status_update_time = now

        with self._lock:
            pending_buys_snapshot = dict(self._pending_buys)
            pending_sells_snapshot = dict(self._pending_sells)
            short_opens_snapshot = dict(self._pending_short_opens)
            short_closes_snapshot = dict(self._pending_short_closes)
            short_position_count = len(self._short_positions)

        buy_orders = [{"price": o.price, "quantity": o.quantity} for o in pending_buys_snapshot.values()]
        sell_orders = [{"price": o.price, "quantity": o.quantity} for o in pending_sells_snapshot.values()]
        short_open_orders = [{"price": o.price, "quantity": o.quantity} for o in short_opens_snapshot.values()]
        short_close_orders = [{"price": o.price, "quantity": o.quantity} for o in short_closes_snapshot.values()]

        # 合并做空侧订单到主字段，前端统一展示
        all_buy_orders = buy_orders + short_close_orders
        all_sell_orders = sell_orders + short_open_orders

        exchange_info = self.exchange.get_exchange_info() or {}
        exchange_id = str(exchange_info.get("id") or "unknown")

        status = {
            "exchange": exchange_id,
            "current_price": self._current_price,
            "pending_buys": len(pending_buys_snapshot) + len(short_closes_snapshot),
            "pending_sells": len(pending_sells_snapshot) + len(short_opens_snapshot),
            "position_count": self.position_tracker.get_position_count() + short_position_count,
            "buy_orders": all_buy_orders,
            "sell_orders": all_sell_orders,
            "last_error": self._last_error,
            "extra_status": {
                "short_open_count": len(short_opens_snapshot),
                "short_close_count": len(short_closes_snapshot),
                "short_position_count": short_position_count,
            },
        }

        try:
            self.on_status_update(status)
        except Exception as e:
            self.log.warning("状态更新回调失败: %s", e)
