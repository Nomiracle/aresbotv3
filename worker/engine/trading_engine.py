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
    StateStore,
    TradeRecord,
    OrderStatus,
    ExchangeOrder,
)
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
        state_store: Optional[StateStore] = None,
        sync_interval: int = 60,
    ):
        self.strategy = strategy
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.state_store = state_store or StateStore()
        self.event_bus = EventBus()
        self.position_tracker = PositionTracker()
        self.position_syncer = PositionSyncer(
            exchange=exchange,
            position_tracker=self.position_tracker,
        )

        self._pending_buys: Dict[str, Order] = {}
        self._pending_sells: Dict[str, Order] = {}
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
        logger.info(f"启动交易引擎: {self.strategy.config.symbol}")
        self._running = True
        self._run_loop()

    def stop(self) -> None:
        """停止交易引擎"""
        logger.info("停止交易引擎")
        self._running = False

    def _run_loop(self) -> None:
        """主循环"""
        loop_index = 0
        while self._running:
            loop_index += 1
            loop_started_at = time.time()
            try:
                logger.debug("主循环开始 #%s symbol=%s", loop_index, self.strategy.config.symbol)
                self._fetch_price()

                if self._current_price is None:
                    logger.debug("主循环等待价格 #%s", loop_index)
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
                logger.debug(
                    "主循环完成 #%s price=%s buys=%s sells=%s positions=%s cost=%.3fs",
                    loop_index,
                    self._current_price,
                    pending_buys,
                    pending_sells,
                    self.position_tracker.get_position_count(),
                    time.time() - loop_started_at,
                )

                time.sleep(self.strategy.config.interval)

            except Exception as e:
                logger.exception("主循环异常 #%s: %s", loop_index, e)
                self._update_status(force=True, source="loop_exception")
                time.sleep(1)

        logger.info("主循环已退出 symbol=%s", self.strategy.config.symbol)
        self._update_status(force=True, source="loop_exit")

    def _fetch_price(self) -> None:
        """获取当前价格"""
        try:
            price = self.exchange.get_ticker_price()
            if price and price > 0:
                self._current_price = price
        except Exception as e:
            logger.warning(f"获取价格失败: {e}")

    def _sync_orders(self) -> None:
        """同步订单状态"""
        try:
            exchange_orders = self.exchange.get_open_orders()
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            with self._lock:
                self._check_order_status(self._pending_buys, exchange_order_map)
                self._check_order_status(self._pending_sells, exchange_order_map)

        except Exception as e:
            logger.warning(f"同步订单失败: {e}")

    def _check_order_status(
        self,
        pending_orders: Dict[str, Order],
        exchange_order_map: Dict[str, ExchangeOrder]
    ) -> None:
        """检查订单状态变化"""
        for order_id in list(pending_orders.keys()):
            ex_order = exchange_order_map.get(order_id)

            if ex_order is None:
                order = self.exchange.get_order(order_id)
                if order and order.status == OrderStatus.FILLED:
                    self._handle_order_filled(order)
                elif order and order.status == OrderStatus.CANCELLED:
                    self._handle_order_cancelled(order)
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
            logger.debug("跳过新开仓: %s", reason)
            return

        decision = self.strategy.should_buy(
            current_price=self._current_price,
            active_buy_orders=active_buys,
            active_sell_orders=active_sells,
        )

        if decision:
            logger.debug(
                "生成买单决策 price=%s qty=%s grid=%s",
                decision.price,
                decision.quantity,
                decision.grid_index,
            )
            self._place_buy_order(decision.price, decision.quantity, decision.grid_index)
        else:
            logger.debug(
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

        logger.debug(
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
            logger.info(f"买单已下: {result.order_id}, 价格={aligned_price}, 数量={aligned_qty}")
        else:
            logger.debug("买单下单失败 aligned_price=%s aligned_qty=%s", aligned_price, aligned_qty)

    def _place_sell_order(self, buy_order: Order, price: float) -> None:
        """下卖单"""
        rules = self.exchange.get_trading_rules()
        fee_rate = self.exchange.get_fee_rate()
        sell_qty = buy_order.filled_quantity * (1 - fee_rate)
        aligned_price = self.exchange.align_price(price, rules)
        aligned_qty = self.exchange.align_quantity(sell_qty, rules)

        logger.debug(
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
            logger.info(f"卖单已下: {result.order_id}, 价格={aligned_price}, 数量={aligned_qty}")
        else:
            logger.debug("卖单下单失败 buy_order=%s aligned_price=%s", buy_order.order_id, aligned_price)

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

        logger.info(f"买单成交: {order.order_id}, 价格={filled_price}, 数量={order.filled_quantity}")

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

        logger.info(f"卖单成交: {order.order_id}, 价格={filled_price}, 盈亏={pnl}")

    def _on_order_cancelled(self, event: Event) -> None:
        """订单取消处理"""
        ex_order: ExchangeOrder = event.data["order"]
        order_id = ex_order.order_id

        with self._lock:
            self._pending_buys.pop(order_id, None)
            self._pending_sells.pop(order_id, None)

        logger.info(f"订单已取消: {order_id}")

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

        logger.debug("触发改价 cancel_count=%s", len(to_cancel))

        # 批量取消
        self.exchange.cancel_batch_orders(to_cancel)

        # 批量下新单
        place_params = [{'side': p['side'], 'price': p['price'], 'quantity': p['quantity']} for p in to_place]
        results = self.exchange.place_batch_orders(place_params)

        # 更新本地订单
        with self._lock:
            for i, result in enumerate(results):
                if result.success and result.order_id:
                    old_order = to_place[i]['_order']
                    new_price = to_place[i]['_new_price']

                    # 移除旧订单
                    if old_order.side == 'buy':
                        self._pending_buys.pop(old_order.order_id, None)
                    else:
                        self._pending_sells.pop(old_order.order_id, None)

                    # 添加新订单
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

                    logger.info(f"订单改价成功: {old_order.order_id} -> {result.order_id}, 新价格={new_price}")

    def _check_stop_loss(self) -> None:
        """检查止损"""
        positions = self.position_tracker.get_all_positions()

        for pos in positions:
            should_stop, reason = self.risk_manager.check_stop_loss(
                entry_price=pos.entry_price,
                current_price=self._current_price,
                entry_time=pos.created_at,
            )
            if should_stop:
                self._execute_stop_loss(pos, reason)

    def _execute_stop_loss(self, position, reason: str) -> None:
        """执行止损"""
        logger.warning(f"触发止损: {position.order_id}, 原因={reason}")

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

        results = self.exchange.place_batch_orders([{
            'side': 'sell',
            'price': stop_price,
            'quantity': stop_qty,
        }])

        if results and results[0].success:
            logger.info(f"止损单已下: {results[0].order_id}")

    def _periodic_sync(self) -> None:
        """定期同步"""
        now = time.time()
        if now - self._last_sync_time < self._sync_interval:
            return

        self._last_sync_time = now

        with self._lock:
            pending_sells_copy = dict(self._pending_sells)

        logger.debug("触发定期同步 pending_sells=%s", len(pending_sells_copy))
        self.position_syncer.sync(pending_sells_copy)

    def _update_status(self, force: bool = False, source: str = "periodic") -> None:
        """更新状态到外部（用于分布式部署）"""
        if self.on_status_update is None:
            return

        now = time.time()
        if not force and now - self._last_status_update_time < self._status_update_interval:
            return

        self._last_status_update_time = now

        with self._lock:
            status = {
                "current_price": self._current_price,
                "pending_buys": len(self._pending_buys),
                "pending_sells": len(self._pending_sells),
                "position_count": self.position_tracker.get_position_count(),
            }

        try:
            self.on_status_update(status)
            logger.debug(
                "状态已更新 source=%s force=%s price=%s buys=%s sells=%s positions=%s",
                source,
                force,
                status["current_price"],
                status["pending_buys"],
                status["pending_sells"],
                status["position_count"],
            )
        except Exception as e:
            logger.warning(f"状态更新回调失败: {e}")

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
