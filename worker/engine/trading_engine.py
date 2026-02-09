from typing import Dict, Optional
from datetime import datetime
import threading
import logging
import time

from worker import (
    BaseStrategy,
    BaseExchange,
    EventBus,
    Event,
    EventType,
    OrderStatus,
    ExchangeOrder,
)
from worker.db import TradeStore, TradeRecord
from worker.domain import Order, OrderState, PositionTracker, RiskManager
from worker.engine.position_syncer import PositionSyncer

logger = logging.getLogger(__name__)


class TradingEngine:
    """交易主引擎 - 协调所有组件"""

    def __init__(
        self,
        strategy: BaseStrategy,
        exchange: BaseExchange,
        risk_manager: RiskManager,
        state_store: Optional[TradeStore] = None,
        sync_interval: int = 60,
    ):
        self.strategy = strategy
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.state_store = state_store
        self.event_bus = EventBus()
        self.position_tracker = PositionTracker()
        self.position_syncer = PositionSyncer(
            exchange=exchange,
            position_tracker=self.position_tracker,
        )

        self._log_prefix = exchange.log_prefix

        self._pending_buys: Dict[str, Order] = {}
        self._pending_sells: Dict[str, Order] = {}
        self._stop_loss_triggered: set[str] = set()
        self._lock = threading.Lock()

        self._running = False
        self._current_price: Optional[float] = None
        self._sync_interval = sync_interval
        self._last_sync_time = 0
        self._last_status_update_time = 0
        self._status_update_interval = 5  # Update status every 5 seconds

        # Status update callback for distributed deployment
        self.on_status_update: Optional[callable] = None

        self._setup_event_handlers()

    def _setup_event_handlers(self) -> None:
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
        self.event_bus.subscribe(EventType.ORDER_PARTIALLY_FILLED, self._on_order_partially_filled)
        self.event_bus.subscribe(EventType.ORDER_CANCELLED, self._on_order_cancelled)

    def start(self) -> None:
        """启动交易引擎"""
        self._log_info("启动交易引擎")
        self._recover_open_orders()
        self._running = True
        self._run_loop()

    def stop(self) -> None:
        """停止交易引擎"""
        self._log_info("停止交易引擎")
        self._running = False
        self._cancel_all_pending_orders()
        self._update_status(force=True, source="stop")
        # 释放交易所连接
        try:
            self.exchange.close()
        except Exception as e:
            self._log_warning("关闭交易所连接失败: %s", e)

    def _cancel_all_pending_orders(self) -> None:
        """停止时取消所有挂单"""
        with self._lock:
            order_ids = [*self._pending_buys.keys(), *self._pending_sells.keys()]

        if order_ids:
            self.exchange.cancel_batch_orders(order_ids)

        with self._lock:
            self._pending_buys.clear()
            self._pending_sells.clear()

    def _run_loop(self) -> None:
        """主循环"""
        loop_index = 0
        while self._running:
            loop_index += 1
            loop_started_at = time.time()
            try:
                self._log_debug("主循环开始 #%s", loop_index)
                self._fetch_price()

                if self._current_price is None or self._current_price <= 0:
                    self._log_debug("主循环等待价格 #%s", loop_index)
                    self._update_status(force=True, source="loop_no_price")
                    time.sleep(0.1)
                    continue

                self._sync_orders()
                self._check_new_orders()
                self._check_reprice()
                self._check_stop_loss()
                self._periodic_sync()

                # 每轮主循环结束后强制刷新一次状态（Redis）
                self._update_status(force=True, source="loop_complete")

                with self._lock:
                    pending_buys = len(self._pending_buys)
                    pending_sells = len(self._pending_sells)
                self._log_debug(
                    "主循环完成 #%s price=%s buys=%s sells=%s positions=%s cost=%.3fs",
                    loop_index,
                    self._current_price,
                    pending_buys,
                    pending_sells,
                    self.position_tracker.get_position_count(),
                    time.time() - loop_started_at,
                )

                time.sleep(max(float(self.strategy.config.interval), 0.1))

            except Exception as e:
                self._log_exception("主循环异常 #%s: %s", loop_index, e)
                self._update_status(force=True, source="loop_exception")
                time.sleep(1)

        self._log_info("主循环已退出")
        self._update_status(force=True, source="loop_exit")

    def _fetch_price(self) -> None:
        """获取当前价格"""
        try:
            price = self.exchange.get_ticker_price()
            if isinstance(price, (int, float)) and price > 0:
                self._current_price = price
        except Exception as e:
            self._log_warning("获取价格失败: %s", e)

    def _recover_open_orders(self) -> None:
        """启动时恢复挂单缓存"""
        try:
            open_orders = self.exchange.get_open_orders()
        except Exception as err:
            self._log_warning("恢复挂单失败: %s", err)
            return

        if not open_orders:
            return

        recovered_buys: Dict[str, Order] = {}
        recovered_sells: Dict[str, Order] = {}
        for exchange_order in open_orders:
            order = Order(
                order_id=exchange_order.order_id,
                symbol=exchange_order.symbol,
                side=exchange_order.side,
                price=exchange_order.price,
                quantity=exchange_order.quantity,
                state=OrderState.PLACED,
                filled_quantity=exchange_order.filled_quantity,
                filled_price=exchange_order.price,
                related_order_id=(exchange_order.extra or {}).get("related_order_id"),
            )
            if order.side == "buy":
                recovered_buys[order.order_id] = order
            elif order.side == "sell":
                recovered_sells[order.order_id] = order

        with self._lock:
            self._pending_buys.update(recovered_buys)
            self._pending_sells.update(recovered_sells)

        self._log_info("启动恢复挂单 buys=%s sells=%s", len(recovered_buys), len(recovered_sells))

    def _sync_orders(self) -> None:
        """同步订单状态"""
        try:
            exchange_orders = self.exchange.get_open_orders()
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            with self._lock:
                self._check_order_status(self._pending_buys, exchange_order_map)
                self._check_order_status(self._pending_sells, exchange_order_map)

        except Exception as e:
            self._log_warning("同步订单失败: %s", e)

    def _check_order_status(
        self,
        pending_orders: Dict[str, Order],
        exchange_order_map: Dict[str, ExchangeOrder]
    ) -> None:
        """检查订单状态变化"""
        for order_id in list(pending_orders.keys()):
            ex_order = exchange_order_map.get(order_id)
            if ex_order is None:
                ex_order = self.exchange.get_order(order_id)
                if ex_order is None:
                    continue

            if ex_order.status == OrderStatus.FILLED:
                self._handle_order_filled(ex_order)
            elif ex_order.status == OrderStatus.CANCELLED:
                self._handle_order_cancelled(ex_order)
            elif ex_order.status == OrderStatus.PARTIALLY_FILLED:
                self._handle_order_partially_filled(ex_order)

    def _handle_order_filled(self, ex_order: ExchangeOrder) -> None:
        """处理订单成交"""
        self.event_bus.publish(Event(
            type=EventType.ORDER_FILLED,
            data={"order": ex_order},
            timestamp=time.time(),
        ))

    def _handle_order_partially_filled(self, ex_order: ExchangeOrder) -> None:
        """处理部分成交"""
        self.event_bus.publish(Event(
            type=EventType.ORDER_PARTIALLY_FILLED,
            data={"order": ex_order},
            timestamp=time.time(),
        ))

    def _handle_order_cancelled(self, ex_order: ExchangeOrder) -> None:
        """处理订单取消"""
        self.event_bus.publish(Event(
            type=EventType.ORDER_CANCELLED,
            data={"order": ex_order},
            timestamp=time.time(),
        ))

    def _check_new_orders(self) -> None:
        """检查是否需要下新单"""
        with self._lock:
            active_buys = len(self._pending_buys)
            active_sells = len(self._pending_sells)

        can_open, reason = self.risk_manager.can_open_position(
            self.position_tracker.get_position_count()
        )
        if not can_open:
            self._log_debug("跳过新开仓: %s", reason)
            return

        decision = self.strategy.should_buy(
            current_price=self._current_price,
            active_buy_orders=active_buys,
            active_sell_orders=active_sells,
        )

        if decision:
            self._log_debug(
                "生成买单决策 price=%s qty=%s grid=%s",
                decision.price,
                decision.quantity,
                decision.grid_index,
            )
            self._place_buy_order(decision.price, decision.quantity, decision.grid_index)
        else:
            self._log_debug(
                "无新买单决策 active_buys=%s active_sells=%s price=%s",
                active_buys,
                active_sells,
                self._current_price,
            )

    def _place_buy_order(self, price: float, quantity: float, grid_index: int) -> None:
        """下买单"""
        rules = self.exchange.get_trading_rules()
        aligned_price = self.exchange.align_price(price, rules)
        aligned_qty = self.exchange.align_quantity(quantity, rules)

        self._log_debug(
            "准备下买单 raw_price=%s raw_qty=%s aligned_price=%s aligned_qty=%s grid=%s",
            price,
            quantity,
            aligned_price,
            aligned_qty,
            grid_index,
        )

        results = self.exchange.place_batch_orders([{
            'side': 'buy',
            'price': aligned_price,
            'quantity': aligned_qty,
        }])

        if results and results[0].success and results[0].order_id:
            result = results[0]
            order = Order(
                order_id=result.order_id,
                symbol=self.strategy.config.symbol,
                side="buy",
                price=aligned_price,
                quantity=aligned_qty,
                grid_index=grid_index,
                state=OrderState.PLACED,
            )
            with self._lock:
                self._pending_buys[result.order_id] = order
            self._log_info("买单已下: %s, 价格=%s, 数量=%s", result.order_id, aligned_price, aligned_qty)
        else:
            self._log_debug("买单下单失败 aligned_price=%s aligned_qty=%s", aligned_price, aligned_qty)

    def _place_sell_order(self, buy_order: Order, price: float) -> None:
        """下卖单"""
        rules = self.exchange.get_trading_rules()
        fee_rate = self.exchange.get_fee_rate()
        sell_qty = buy_order.filled_quantity * (1 - fee_rate)
        aligned_price = self.exchange.align_price(price, rules)
        aligned_qty = self.exchange.align_quantity(sell_qty, rules)

        self._log_debug(
            "准备下卖单 buy_order=%s raw_price=%s aligned_price=%s aligned_qty=%s",
            buy_order.order_id,
            price,
            aligned_price,
            aligned_qty,
        )

        results = self.exchange.place_batch_orders([{
            'side': 'sell',
            'price': aligned_price,
            'quantity': aligned_qty,
        }])

        if results and results[0].success and results[0].order_id:
            result = results[0]
            order = Order(
                order_id=result.order_id,
                symbol=self.strategy.config.symbol,
                side="sell",
                price=aligned_price,
                quantity=aligned_qty,
                grid_index=buy_order.grid_index,
                state=OrderState.PLACED,
                related_order_id=buy_order.order_id,
            )
            with self._lock:
                self._pending_sells[result.order_id] = order
            self._log_info("卖单已下: %s, 价格=%s, 数量=%s", result.order_id, aligned_price, aligned_qty)
        else:
            self._log_debug("卖单下单失败 buy_order=%s aligned_price=%s", buy_order.order_id, aligned_price)

    def _on_order_filled(self, event: Event) -> None:
        """订单完全成交处理"""
        ex_order: ExchangeOrder = event.data["order"]
        order_id = ex_order.order_id

        with self._lock:
            if order_id in self._pending_buys:
                buy_order = self._pending_buys.pop(order_id)
                self._handle_buy_filled(buy_order, ex_order)
            elif order_id in self._pending_sells:
                sell_order = self._pending_sells.pop(order_id)
                self._handle_sell_filled(sell_order, ex_order)

    def _on_order_partially_filled(self, event: Event) -> None:
        """订单部分成交处理"""
        ex_order: ExchangeOrder = event.data["order"]
        order_id = ex_order.order_id

        with self._lock:
            if order_id in self._pending_buys:
                order = self._pending_buys[order_id]
                old_filled = order.filled_quantity
                order.update_fill(ex_order.filled_quantity, ex_order.price)

                new_filled = ex_order.filled_quantity - old_filled
                if new_filled > 0:
                    self._save_partial_fill(order, new_filled, ex_order.price)

            elif order_id in self._pending_sells:
                order = self._pending_sells[order_id]
                old_filled = order.filled_quantity
                order.update_fill(ex_order.filled_quantity, ex_order.price)

                new_filled = ex_order.filled_quantity - old_filled
                if new_filled > 0:
                    self._save_partial_fill(order, new_filled, ex_order.price)

    def _handle_buy_filled(self, order: Order, ex_order: ExchangeOrder) -> None:
        """处理买单成交"""
        order.update_fill(ex_order.filled_quantity, ex_order.price)
        filled_price = ex_order.price

        self._save_trade(order, filled_price)

        self.position_tracker.add_position(
            order_id=order.order_id,
            symbol=order.symbol,
            quantity=order.filled_quantity,
            entry_price=filled_price,
            grid_index=order.grid_index,
        )

        decision = self.strategy.should_sell(
            buy_price=filled_price,
            buy_quantity=order.filled_quantity,
            current_price=self._current_price,
        )
        if decision:
            self._place_sell_order(order, decision.price)

        self._log_info("买单成交: %s, 价格=%s, 数量=%s", order.order_id, filled_price, order.filled_quantity)

    def _handle_sell_filled(self, order: Order, ex_order: ExchangeOrder) -> None:
        """处理卖单成交"""
        order.update_fill(ex_order.filled_quantity, ex_order.price)
        filled_price = ex_order.price

        position = self.position_tracker.remove_position(order.related_order_id)
        pnl = None
        if position:
            pnl = (filled_price - position.entry_price) * order.filled_quantity
            self.risk_manager.record_trade_result(pnl)

        self._save_trade(order, filled_price, pnl)

        self._log_info("卖单成交: %s, 价格=%s, 盈亏=%s", order.order_id, filled_price, pnl)

    def _on_order_cancelled(self, event: Event) -> None:
        """订单取消处理"""
        ex_order: ExchangeOrder = event.data["order"]
        order_id = ex_order.order_id

        with self._lock:
            self._pending_buys.pop(order_id, None)
            self._pending_sells.pop(order_id, None)

        self._log_info("订单已取消: %s", order_id)

    def _save_trade(self, order: Order, filled_price: float, pnl: Optional[float] = None) -> None:
        """保存成交记录"""
        fee_rate = self.exchange.get_fee_rate()
        fee = order.filled_quantity * filled_price * fee_rate

        trade = TradeRecord(
            id=None,
            symbol=order.symbol,
            side=order.side,
            price=filled_price,
            quantity=order.filled_quantity,
            fee=fee,
            pnl=pnl,
            order_id=order.order_id,
            grid_index=order.grid_index,
            related_order_id=order.related_order_id,
            created_at=datetime.now(),
        )
        self.state_store.save_trade(trade)

    def _save_partial_fill(self, order: Order, quantity: float, price: float) -> None:
        """保存部分成交记录"""
        fee_rate = self.exchange.get_fee_rate()
        fee = quantity * price * fee_rate

        trade = TradeRecord(
            id=None,
            symbol=order.symbol,
            side=order.side,
            price=price,
            quantity=quantity,
            fee=fee,
            pnl=None,
            order_id=order.order_id,
            grid_index=order.grid_index,
            related_order_id=order.related_order_id,
            created_at=datetime.now(),
        )
        self.state_store.save_trade(trade)

    def _check_reprice(self) -> None:
        """检查是否需要改价"""
        with self._lock:
            buy_orders = list(self._pending_buys.values())
            sell_orders = list(self._pending_sells.values())

        # 收集需要改价的订单
        to_cancel = []
        to_place = []

        rules = self.exchange.get_trading_rules()

        for order in buy_orders:
            new_price = self.strategy.should_reprice(
                order_price=order.price,
                current_price=self._current_price,
                is_buy=True,
            )
            if new_price:
                aligned_price = self.exchange.align_price(new_price, rules)
                to_cancel.append(order.order_id)
                to_place.append({
                    'side': 'buy',
                    'price': aligned_price,
                    'quantity': order.quantity,
                    '_order': order,
                    '_new_price': aligned_price,
                })

        for order in sell_orders:
            new_price = self.strategy.should_reprice(
                order_price=order.price,
                current_price=self._current_price,
                is_buy=False,
            )
            if new_price:
                aligned_price = self.exchange.align_price(new_price, rules)
                to_cancel.append(order.order_id)
                to_place.append({
                    'side': 'sell',
                    'price': aligned_price,
                    'quantity': order.quantity,
                    '_order': order,
                    '_new_price': aligned_price,
                })

        if not to_cancel:
            return

        self._log_debug("触发改价 cancel_count=%s", len(to_cancel))

        # 批量取消
        cancel_results = self.exchange.cancel_batch_orders(to_cancel)
        cancelled_ids = {
            result.order_id
            for result in cancel_results
            if result.success and result.order_id
        }
        place_candidates = [
            payload for payload in to_place if payload['_order'].order_id in cancelled_ids
        ]

        if not place_candidates:
            return

        # 批量下新单
        place_params = [
            {'side': p['side'], 'price': p['price'], 'quantity': p['quantity']}
            for p in place_candidates
        ]
        results = self.exchange.place_batch_orders(place_params)

        # 更新本地订单
        with self._lock:
            for index, payload in enumerate(place_candidates):
                old_order = payload['_order']
                new_price = payload['_new_price']
                result = results[index] if index < len(results) else None

                if old_order.side == 'buy':
                    self._pending_buys.pop(old_order.order_id, None)
                else:
                    self._pending_sells.pop(old_order.order_id, None)

                if result and result.success and result.order_id:
                    new_order = Order(
                        order_id=result.order_id,
                        symbol=old_order.symbol,
                        side=old_order.side,
                        price=new_price,
                        quantity=old_order.quantity,
                        grid_index=old_order.grid_index,
                        state=OrderState.PLACED,
                        related_order_id=old_order.related_order_id,
                    )
                    if new_order.side == 'buy':
                        self._pending_buys[result.order_id] = new_order
                    else:
                        self._pending_sells[result.order_id] = new_order

                    self._log_info("订单改价成功: %s -> %s, 新价格=%s", old_order.order_id, result.order_id, new_price)
                else:
                    self._log_warning("订单改价重下失败，已移除旧单: %s", old_order.order_id)

    def _check_stop_loss(self) -> None:
        """检查止损"""
        positions = self.position_tracker.get_all_positions()

        for pos in positions:
            if pos.order_id in self._stop_loss_triggered:
                continue
            should_stop, reason = self.risk_manager.check_stop_loss(
                entry_price=pos.entry_price,
                current_price=self._current_price,
                entry_time=pos.created_at,
            )
            if should_stop:
                self._stop_loss_triggered.add(pos.order_id)
                self._execute_stop_loss(pos, reason)

    def _execute_stop_loss(self, position, reason: str) -> None:
        """执行止损"""
        self._log_warning("触发止损: %s, 原因=%s", position.order_id, reason)

        # 取消相关卖单
        cancel_ids = []
        with self._lock:
            for order_id, order in list(self._pending_sells.items()):
                if order.related_order_id == position.order_id:
                    cancel_ids.append(order_id)
                    self._pending_sells.pop(order_id, None)

        if cancel_ids:
            self.exchange.cancel_batch_orders(cancel_ids)

        # 下止损单
        rules = self.exchange.get_trading_rules()
        stop_price = self.exchange.align_price(self._current_price * 0.999, rules)
        stop_qty = self.exchange.align_quantity(position.quantity, rules)

        if stop_qty <= 0:
            self._log_warning("止损数量无效，跳过下单: %s", position.order_id)
            return

        results = self.exchange.place_batch_orders([{
            'side': 'sell',
            'price': stop_price,
            'quantity': stop_qty,
        }])

        if results and results[0].success:
            self._log_info("止损单已下: %s", results[0].order_id)

    def _periodic_sync(self) -> None:
        """定期同步"""
        now = time.time()
        if now - self._last_sync_time < self._sync_interval:
            return

        self._last_sync_time = now

        with self._lock:
            pending_sells_copy = dict(self._pending_sells)

        self._log_debug("触发定期同步 pending_sells=%s", len(pending_sells_copy))
        self.position_syncer.sync(pending_sells_copy)
        self._repair_positions_and_orders(pending_sells_copy)

    def _repair_positions_and_orders(self, pending_sells: Dict[str, Order]) -> None:
        """最小修复：补卖单、取消多余卖单"""
        positions_without_sells = self.position_syncer.get_positions_without_sells(pending_sells)
        for pos in positions_without_sells:
            decision = self.strategy.should_sell(
                buy_price=pos.entry_price,
                buy_quantity=pos.quantity,
                current_price=self._current_price,
            )
            if decision:
                buy_order = Order(
                    order_id=pos.order_id,
                    symbol=pos.symbol,
                    side='buy',
                    price=pos.entry_price,
                    quantity=pos.quantity,
                    grid_index=pos.grid_index,
                    state=OrderState.FILLED,
                    filled_quantity=pos.quantity,
                    filled_price=pos.entry_price,
                )
                self._place_sell_order(buy_order, decision.price)

        excess_sells = self.position_syncer.get_excess_sells(pending_sells)
        if not excess_sells:
            return

        cancel_ids = [order.order_id for order in excess_sells]
        self.exchange.cancel_batch_orders(cancel_ids)
        with self._lock:
            for order_id in cancel_ids:
                self._pending_sells.pop(order_id, None)

    def _update_status(self, force: bool = False, source: str = "periodic") -> None:
        """更新状态到外部（用于分布式部署）"""
        if self.on_status_update is None:
            return

        now = time.time()
        if not force and now - self._last_status_update_time < self._status_update_interval:
            return

        self._last_status_update_time = now

        with self._lock:
            # 构建挂单详情列表：[{price, quantity}, ...]
            buy_orders = [
                {"price": o.price, "quantity": o.quantity}
                for o in self._pending_buys.values()
            ]
            sell_orders = [
                {"price": o.price, "quantity": o.quantity}
                for o in self._pending_sells.values()
            ]

            status = {
                "current_price": self._current_price,
                "pending_buys": len(self._pending_buys),
                "pending_sells": len(self._pending_sells),
                "position_count": self.position_tracker.get_position_count(),
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
            }

        try:
            self.on_status_update(status)
            self._log_debug(
                "状态已更新 source=%s force=%s price=%s buys=%s sells=%s positions=%s",
                source,
                force,
                status["current_price"],
                status["pending_buys"],
                status["pending_sells"],
                status["position_count"],
            )
        except Exception as e:
            self._log_warning("状态更新回调失败: %s", e)

    def get_status(self) -> dict:
        """获取引擎状态"""
        with self._lock:
            return {
                "running": self._running,
                "current_price": self._current_price,
                "pending_buys": len(self._pending_buys),
                "pending_sells": len(self._pending_sells),
                "positions": self.position_tracker.get_position_count(),
                "risk": self.risk_manager.get_status(),
            }

    @property
    def current_price(self) -> Optional[float]:
        """获取当前价格"""
        return self._current_price

    @property
    def pending_buys(self) -> Dict[str, Order]:
        """获取挂买单"""
        with self._lock:
            return dict(self._pending_buys)

    @property
    def pending_sells(self) -> Dict[str, Order]:
        """获取挂卖单"""
        with self._lock:
            return dict(self._pending_sells)

    def _log_debug(self, message: str, *args: object) -> None:
        logger.debug("%s " + message, self._log_prefix, *args)

    def _log_info(self, message: str, *args: object) -> None:
        logger.info("%s " + message, self._log_prefix, *args)

    def _log_warning(self, message: str, *args: object) -> None:
        logger.warning("%s " + message, self._log_prefix, *args)

    def _log_exception(self, message: str, *args: object) -> None:
        logger.exception("%s " + message, self._log_prefix, *args)
