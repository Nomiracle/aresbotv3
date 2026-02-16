from collections.abc import Callable
from typing import Any, Dict, Optional
from collections import deque
from datetime import datetime
import threading
import logging
import time

from worker.core.base_exchange import BaseExchange, EditOrderRequest, ExchangeOrder, OrderRequest, OrderStatus, TradingRules
from worker.core.base_strategy import BaseStrategy
from worker.core.log_utils import PrefixAdapter
from worker.db import TradeStore, TradeRecord
from worker.domain import Order, OrderState, PositionTracker, RiskManager
from worker.engine.position_syncer import PositionSyncer

_logger = logging.getLogger(__name__)


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
        self.position_tracker = PositionTracker()
        self.position_syncer = PositionSyncer(
            exchange=exchange,
            position_tracker=self.position_tracker,
        )

        self.log = PrefixAdapter(_logger, {"prefix": exchange.log_prefix})

        self._pending_buys: Dict[str, Order] = {}
        self._pending_sells: Dict[str, Order] = {}
        self._stop_loss_triggered: deque[str] = deque(maxlen=1000)
        self._lock = threading.Lock()

        self._running = False
        self._current_price: Optional[float] = None
        self._trading_rules: Optional[TradingRules] = None
        self._fee_rate: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_error_time: float = 0
        self._error_retain_seconds: float = 30
        self._sync_interval = sync_interval
        self._last_sync_time = 0
        self._last_status_update_time = 0
        self._status_update_interval = 5
        self.should_stop: Optional[Callable[[], bool]] = None
        self._stop_signal_logged = False

        self.on_status_update: Optional[callable] = None
        self.on_notify: Optional[callable] = None

    @property
    def _rules(self) -> TradingRules:
        rules = self._trading_rules
        if rules is None:
            rules = self.exchange.get_trading_rules()
            self._trading_rules = rules
        return rules

    @property
    def _fee(self) -> float:
        rate = self._fee_rate
        if rate is None:
            rate = self.exchange.get_fee_rate()
            self._fee_rate = rate
        return rate

    def start(self) -> None:
        """启动交易引擎"""
        self.log.info("启动交易引擎")
        self._recover_open_orders()
        self._running = True
        self._run_loop()

    def stop(self) -> None:
        """停止交易引擎"""
        self.log.info("停止交易引擎")
        self._running = False

        # 取消所有挂单
        with self._lock:
            order_ids = [*self._pending_buys.keys(), *self._pending_sells.keys()]
        if order_ids:
            self.exchange.cancel_batch_orders(order_ids)
        with self._lock:
            self._pending_buys.clear()
            self._pending_sells.clear()

        self._update_status(force=True, source="stop")
        try:
            self.exchange.close()
        except Exception as e:
            self.log.warning("关闭交易所连接失败: %s", e)

    def _emit_notify(self, event, title: str, body: str) -> None:
        """安全地发送通知"""
        if self.on_notify is None:
            return
        try:
            self.on_notify(event, title, body)
        except Exception as e:
            self.log.debug("通知发送失败: %s", e)

    def _run_loop(self) -> None:
        """主循环"""
        loop_index = 0
        while self._running:
            if self._apply_external_stop():
                break

            loop_index += 1
            loop_started_at = time.time()
            try:
                self.log.debug("主循环开始 #%s", loop_index)

                # 获取价格
                try:
                    price = self.exchange.get_ticker_price()
                    if isinstance(price, (int, float)) and price > 0:
                        self._current_price = price
                except TimeoutError as e:
                    self.log.warning("获取价格超时: %s", e)
                    self._current_price = None
                    self._last_error = f"获取价格超时: {e}"
                    self._last_error_time = time.time()
                except Exception as e:
                    self.log.warning("获取价格失败: %s", e, exc_info=True)
                    self._current_price = None
                    self._last_error = f"获取价格失败: {e}"
                    self._last_error_time = time.time()
                t_price = time.time()

                if self._current_price is None or self._current_price <= 0:
                    self.log.debug("主循环等待价格 #%s", loop_index)
                    self._update_status(force=True, source="loop_no_price")
                    if self._sleep_with_stop_check(0.1):
                        break
                    continue

                self._sync_orders()
                t_sync = time.time()

                self._check_new_orders()
                t_new = time.time()

                self._check_reprice()
                t_reprice = time.time()

                self._check_stop_loss()
                t_stoploss = time.time()

                self._periodic_sync()
                t_psync = time.time()

                if self._last_error and time.time() - self._last_error_time > self._error_retain_seconds:
                    self._last_error = None
                self._update_status(force=True, source="loop_complete")

                with self._lock:
                    pending_buys = len(self._pending_buys)
                    pending_sells = len(self._pending_sells)

                total = t_psync - loop_started_at
                self.log.info(
                    "循环#%s 价格=%s 买单=%s 卖单=%s 持仓=%s "
                    "| 行情 %.0fms 同步 %.0fms 开仓 %.0fms 改价 %.0fms 止损 %.0fms 定期 %.0fms | 合计 %.0fms",
                    loop_index,
                    self._current_price,
                    pending_buys,
                    pending_sells,
                    self.position_tracker.get_position_count(),
                    (t_price - loop_started_at) * 1000,
                    (t_sync - t_price) * 1000,
                    (t_new - t_sync) * 1000,
                    (t_reprice - t_new) * 1000,
                    (t_stoploss - t_reprice) * 1000,
                    (t_psync - t_stoploss) * 1000,
                    total * 1000,
                )

                if self._sleep_with_stop_check(
                    max(float(self.strategy.config.interval), 0.1),
                ):
                    break

            except Exception as e:
                self.log.exception("主循环异常 #%s: %s", loop_index, e)
                self._last_error = str(e)
                self._last_error_time = time.time()
                self._update_status(force=True, source="loop_exception")
                self._emit_notify("strategy_error", "策略异常", str(e))
                if self._sleep_with_stop_check(1):
                    break

        self.log.info("主循环已退出")
        self._update_status(force=True, source="loop_exit")

    def _apply_external_stop(self) -> bool:
        """检查外部停止信号"""
        if not self._running:
            return True

        if self.should_stop is None:
            return False

        try:
            should_stop = bool(self.should_stop())
        except Exception as err:
            self.log.warning("检查停止信号失败: %s", err)
            return False

        if not should_stop:
            return False

        if not self._stop_signal_logged:
            self.log.info("收到外部停止信号，退出主循环")
            self._stop_signal_logged = True

        self._running = False
        self._update_status(force=True, source="external_stop")
        return True

    def _sleep_with_stop_check(self, duration: float) -> bool:
        """分片睡眠，支持提前退出"""
        remaining = max(float(duration), 0.0)
        if remaining == 0:
            return self._apply_external_stop()

        step = 0.2
        while remaining > 0:
            if self._apply_external_stop():
                return True
            sleep_for = min(step, remaining)
            time.sleep(sleep_for)
            remaining -= sleep_for

        return self._apply_external_stop()

    def _recover_open_orders(self) -> None:
        """启动时恢复挂单缓存"""
        try:
            open_orders = self.exchange.get_open_orders()
        except Exception as err:
            self.log.warning("恢复挂单失败: %s", err)
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

        self.log.info("启动恢复挂单 buys=%s sells=%s", len(recovered_buys), len(recovered_sells))

    def _sync_orders(self) -> None:
        """同步订单状态（批量下卖单）"""
        try:
            exchange_orders = self.exchange.get_open_orders()
            exchange_order_map = {o.order_id: o for o in exchange_orders}

            with self._lock:
                pending_ids = list(self._pending_buys.keys()) + list(self._pending_sells.keys())

            # 收集买单成交后需要下的卖单
            sell_requests: list[OrderRequest] = []
            sell_meta: list[Order] = []  # 对应的买单

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
                        self._save_trade(
                            buy_order,
                            filled_price,
                            raw_order_info=raw_order_info,
                        )
                        self.position_tracker.add_position(
                            order_id=buy_order.order_id,
                            symbol=buy_order.symbol,
                            quantity=buy_order.filled_quantity,
                            entry_price=filled_price,
                            grid_index=buy_order.grid_index,
                        )
                        decision = self.strategy.should_sell(
                            buy_price=filled_price,
                            buy_quantity=buy_order.filled_quantity,
                            current_price=self._current_price,
                        )
                        if decision:
                            if ex_order.fee_paid_externally:
                                sell_qty = buy_order.filled_quantity
                                self.log.debug("手续费外部支付，卖单数量=%s", sell_qty)
                            else:
                                sell_qty = buy_order.filled_quantity * (1 - fee_rate)
                                self.log.debug("手续费内部扣除，买入=%s 费率=%s 卖单=%s",
                                              buy_order.filled_quantity, fee_rate, sell_qty)
                            aligned_price = self.exchange.align_price(decision.price, rules)
                            aligned_qty = self.exchange.align_quantity(sell_qty, rules)
                            sell_requests.append(OrderRequest(side="sell", price=aligned_price, quantity=aligned_qty))
                            sell_meta.append(buy_order)
                        self.log.info("买单成交: %s, 价格=%s, 数量=%s, 外部手续费=%s",
                                      buy_order.order_id, filled_price, buy_order.filled_quantity,
                                      ex_order.fee_paid_externally)
                        self._emit_notify(
                            "order_filled",
                            f"买单成交 #{buy_order.grid_index or ''}",
                            f"价格: {filled_price}, 数量: {buy_order.filled_quantity}",
                        )

                    elif sell_order is not None:
                        self._handle_sell_filled(sell_order, ex_order)

                elif ex_order.status == OrderStatus.CANCELLED:
                    with self._lock:
                        self._pending_buys.pop(order_id, None)
                        self._pending_sells.pop(order_id, None)
                    self.log.info("订单已取消: %s", order_id)

                elif ex_order.status == OrderStatus.PARTIALLY_FILLED:
                    with self._lock:
                        order = self._pending_buys.get(order_id) or self._pending_sells.get(order_id)
                        if order is None:
                            continue
                        old_filled = order.filled_quantity
                        order.update_fill(ex_order.filled_quantity, ex_order.price)
                        new_filled = ex_order.filled_quantity - old_filled

                    if new_filled > 0:
                        self._save_trade(
                            order,
                            ex_order.price,
                            quantity=new_filled,
                            raw_order_info=self._build_raw_order_info(ex_order),
                        )

            # 批量下卖单
            if sell_requests:
                self.log.debug("批量下卖单 count=%s", len(sell_requests))
                results = self.exchange.place_batch_orders(sell_requests)
                latest_order_error: Optional[str] = None
                all_suppressed = True
                with self._lock:
                    for idx, result in enumerate(results):
                        buy_order = sell_meta[idx]
                        if result.success and result.order_id:
                            placed_price = result.filled_price if result.filled_price is not None else sell_requests[idx].price
                            placed_qty = result.filled_quantity if result.filled_quantity is not None else sell_requests[idx].quantity
                            sell_order = Order(
                                order_id=result.order_id,
                                symbol=self.strategy.config.symbol,
                                side="sell",
                                price=placed_price,
                                quantity=placed_qty,
                                grid_index=buy_order.grid_index,
                                state=OrderState.PLACED,
                                related_order_id=buy_order.order_id,
                            )
                            self._pending_sells[result.order_id] = sell_order
                            self.log.info("卖单已下: %s, 价格=%s, 数量=%s", result.order_id, placed_price, placed_qty)
                        else:
                            error_msg = result.error or "下单失败"
                            latest_order_error = f"卖单下单失败: {error_msg}"
                            if not result.suppress_notify:
                                all_suppressed = False
                            self.log.warning("卖单下单失败 buy_order=%s error=%s", buy_order.order_id, error_msg)

                if latest_order_error is not None:
                    self._last_error = latest_order_error
                    self._last_error_time = time.time()
                    self._update_status(force=True, source="sync_sell_order_failed")
                    if not all_suppressed:
                        self._emit_notify("order_failed", "卖单下单失败", latest_order_error)

        except Exception as e:
            self.log.warning("同步订单失败: %s", e, exc_info=True)
            self._last_error = f"同步订单失败: {e}"
            self._last_error_time = time.time()
            self._update_status(force=True, source="sync_orders_failed")

    def _check_new_orders(self) -> None:
        """检查是否需要下新单（批量）"""
        with self._lock:
            active_buys = len(self._pending_buys)
            active_sells = len(self._pending_sells)
            pending_buys = dict(self._pending_buys)
            pending_sells = dict(self._pending_sells)

        can_open, reason = self.risk_manager.can_open_position(
            self.position_tracker.get_position_count()
        )
        if not can_open:
            self.log.debug("跳过新开仓: %s", reason)
            return

        positions = self.position_tracker.get_all_positions()
        decisions = self.strategy.should_buy_batch(
            current_price=self._current_price,
            pending_buy_orders=pending_buys,
            pending_sell_orders=pending_sells,
            positions=positions,
        )

        if not decisions:
            self.log.debug(
                "无新买单决策 active_buys=%s active_sells=%s price=%s",
                active_buys,
                active_sells,
                self._current_price,
            )
            return

        rules = self._rules
        order_requests: list[OrderRequest] = []
        decision_map: list = []  # 保持与 order_requests 对应

        for decision in decisions:
            aligned_price = self.exchange.align_price(decision.price, rules)
            aligned_qty = self.exchange.align_quantity(decision.quantity, rules)
            order_requests.append(OrderRequest(side="buy", price=aligned_price, quantity=aligned_qty))
            decision_map.append((decision, aligned_price, aligned_qty))

        self.log.debug("批量下买单 count=%s", len(order_requests))
        results = self.exchange.place_batch_orders(order_requests)
        latest_order_error: Optional[str] = None
        all_suppressed = True

        with self._lock:
            for idx, result in enumerate(results):
                decision, aligned_price, aligned_qty = decision_map[idx]
                if result.success and result.order_id:
                    placed_price = result.filled_price if result.filled_price is not None else aligned_price
                    placed_qty = result.filled_quantity if result.filled_quantity is not None else aligned_qty
                    order = Order(
                        order_id=result.order_id,
                        symbol=self.strategy.config.symbol,
                        side="buy",
                        price=placed_price,
                        quantity=placed_qty,
                        grid_index=decision.grid_index,
                        state=OrderState.PLACED,
                    )
                    self._pending_buys[result.order_id] = order
                    self.log.info("买单已下: %s, 价格=%s, 数量=%s, 网格=%s", result.order_id, placed_price, placed_qty, decision.grid_index)
                else:
                    error_msg = result.error or "下单失败"
                    latest_order_error = f"买单下单失败: {error_msg}"
                    if not result.suppress_notify:
                        all_suppressed = False
                    self.log.warning("买单下单失败 price=%s qty=%s error=%s", aligned_price, aligned_qty, error_msg)

        if latest_order_error is not None:
            self._last_error = latest_order_error
            self._last_error_time = time.time()
            self._update_status(force=True, source="buy_order_failed")
            if not all_suppressed:
                self._emit_notify("order_failed", "买单下单失败", latest_order_error)

    def _place_sell_order(self, buy_order: Order, price: float) -> Optional[Order]:
        """下卖单"""
        rules = self._rules
        fee_rate = self._fee

        fee_paid_externally = buy_order.extra.get('fee_paid_externally', False)
        if fee_paid_externally:
            sell_qty = buy_order.filled_quantity
        else:
            sell_qty = buy_order.filled_quantity * (1 - fee_rate)
        aligned_price = self.exchange.align_price(price, rules)
        aligned_qty = self.exchange.align_quantity(sell_qty, rules)

        self.log.debug(
            "准备下卖单 buy_order=%s raw_price=%s aligned_price=%s aligned_qty=%s",
            buy_order.order_id, price, aligned_price, aligned_qty,
        )

        results = self.exchange.place_batch_orders([
            OrderRequest(side="sell", price=aligned_price, quantity=aligned_qty)
        ])

        if results and results[0].success and results[0].order_id:
            result = results[0]
            placed_price = result.filled_price if result.filled_price is not None else aligned_price
            placed_qty = result.filled_quantity if result.filled_quantity is not None else aligned_qty
            order = Order(
                order_id=result.order_id,
                symbol=self.strategy.config.symbol,
                side="sell",
                price=placed_price,
                quantity=placed_qty,
                grid_index=buy_order.grid_index,
                state=OrderState.PLACED,
                related_order_id=buy_order.order_id,
            )
            with self._lock:
                self._pending_sells[result.order_id] = order
            self.log.info("卖单已下: %s, 价格=%s, 数量=%s", result.order_id, placed_price, placed_qty)
            return order
        else:
            error_msg = results[0].error if results and results[0].error else "下单失败"
            suppress = results[0].suppress_notify if results else False
            self._last_error = f"卖单下单失败: {error_msg}"
            self._last_error_time = time.time()
            self.log.warning("卖单下单失败 buy_order=%s aligned_price=%s error=%s", buy_order.order_id, aligned_price, error_msg)
            self._update_status(force=True, source="sell_order_failed")
            if not suppress:
                self._emit_notify("order_failed", "卖单下单失败", f"买单: {buy_order.order_id}, 错误: {error_msg}")
            return None

    def _handle_sell_filled(self, order: Order, ex_order: ExchangeOrder) -> None:
        """处理卖单成交"""
        order.update_fill(ex_order.filled_quantity, ex_order.price)
        filled_price = ex_order.price

        position = self.position_tracker.remove_position(order.related_order_id)
        pnl = None
        if position:
            pnl = (filled_price - position.entry_price) * order.filled_quantity
            self.risk_manager.record_trade_result(pnl)

        self._save_trade(
            order,
            filled_price,
            pnl=pnl,
            raw_order_info=self._build_raw_order_info(ex_order),
        )

        self.log.info("卖单成交: %s, 价格=%s, 盈亏=%s", order.order_id, filled_price, pnl)
        pnl_str = f"{pnl:+.6f}" if pnl is not None else "N/A"
        self._emit_notify(
            "order_filled",
            f"卖单成交 #{order.grid_index or ''}",
            f"价格: {filled_price}, 盈亏: {pnl_str}",
        )

    def _save_trade(
        self, order: Order, price: float,
        pnl: Optional[float] = None,
        quantity: Optional[float] = None,
        raw_order_info: Optional[dict[str, Any]] = None,
    ) -> None:
        """保存成交记录（完全成交或部分成交）"""
        qty = quantity if quantity is not None else order.filled_quantity
        fee_rate = self._fee
        trade = TradeRecord(
            id=None,
            symbol=order.symbol,
            side=order.side,
            price=price,
            quantity=qty,
            fee=qty * price * fee_rate,
            pnl=pnl,
            order_id=order.order_id,
            grid_index=order.grid_index,
            related_order_id=order.related_order_id,
            raw_order_info=raw_order_info,
            created_at=datetime.now(),
        )
        self.state_store.save_trade(trade)

    @staticmethod
    def _build_raw_order_info(exchange_order: ExchangeOrder) -> dict[str, Any]:
        extra = exchange_order.extra if isinstance(exchange_order.extra, dict) else {}
        raw_order = extra.get("raw_order")
        if isinstance(raw_order, dict):
            return dict(raw_order)

        payload: dict[str, Any] = {
            "order_id": exchange_order.order_id,
            "symbol": exchange_order.symbol,
            "side": exchange_order.side,
            "price": exchange_order.price,
            "quantity": exchange_order.quantity,
            "filled_quantity": exchange_order.filled_quantity,
            "status": exchange_order.status.value,
            "fee_paid_externally": exchange_order.fee_paid_externally,
        }
        if extra:
            payload["extra"] = extra
        return payload

    def _check_reprice(self) -> None:
        """检查是否需要改价（使用 edit_batch_orders）"""
        with self._lock:
            buy_orders = list(self._pending_buys.values())
            sell_orders = list(self._pending_sells.values())

        edit_requests: list[EditOrderRequest] = []
        order_map: list[Order] = []  # 与 edit_requests 对应

        rules = self._rules

        for order in buy_orders:
            new_price = self.strategy.should_reprice(
                order_price=order.price,
                current_price=self._current_price,
                is_buy=True,
                grid_index=order.grid_index,
            )
            if new_price:
                aligned_price = self.exchange.align_price(new_price, rules)
                edit_requests.append(EditOrderRequest(
                    order_id=order.order_id, side="buy", price=aligned_price, quantity=order.quantity,
                ))
                order_map.append(order)

        for order in sell_orders:
            new_price = self.strategy.should_reprice(
                order_price=order.price,
                current_price=self._current_price,
                is_buy=False,
                grid_index=order.grid_index,
            )
            if new_price:
                aligned_price = self.exchange.align_price(new_price, rules)
                edit_requests.append(EditOrderRequest(
                    order_id=order.order_id, side="sell", price=aligned_price, quantity=order.quantity,
                ))
                order_map.append(order)

        if not edit_requests:
            return

        self.log.debug("触发改价 count=%s", len(edit_requests))
        results = self.exchange.edit_batch_orders(edit_requests)

        with self._lock:
            for idx, result in enumerate(results):
                old_order = order_map[idx]
                new_price = edit_requests[idx].price

                if old_order.side == 'buy':
                    self._pending_buys.pop(old_order.order_id, None)
                else:
                    self._pending_sells.pop(old_order.order_id, None)

                if result.success and result.order_id:
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

                    self.log.info("订单改价成功 [%s]: %s -> %s, 新价格=%s", old_order.side, old_order.order_id, result.order_id, new_price)
                else:
                    self.log.warning(
                        "订单改价失败 [%s] old=%s error=%s, 网格位置已丢失将在下一轮补单",
                        old_order.side, old_order.order_id, result.error,
                    )

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
                self._stop_loss_triggered.append(pos.order_id)
                self._execute_stop_loss(pos, reason)

    def _execute_stop_loss(self, position, reason: str) -> None:
        """执行止损"""
        self.log.warning("触发止损: %s, 原因=%s", position.order_id, reason)
        self._emit_notify(
            "stop_loss_triggered",
            "止损触发",
            f"持仓: {position.order_id}, 原因: {reason}",
        )

        cancel_ids = []
        with self._lock:
            for order_id, order in list(self._pending_sells.items()):
                if order.related_order_id == position.order_id:
                    cancel_ids.append(order_id)
                    self._pending_sells.pop(order_id, None)

        if cancel_ids:
            self.exchange.cancel_batch_orders(cancel_ids)

        rules = self._rules
        stop_price = self.exchange.align_price(self._current_price * 0.999, rules)
        stop_qty = self.exchange.align_quantity(position.quantity, rules)

        if stop_qty <= 0:
            self.log.warning("止损数量无效，跳过下单: %s", position.order_id)
            return

        results = self.exchange.place_batch_orders([
            OrderRequest(side="sell", price=stop_price, quantity=stop_qty)
        ])

        if results and results[0].success:
            self.log.info("止损单已下: %s", results[0].order_id)

    def _periodic_sync(self) -> None:
        """定期同步"""
        now = time.time()
        if now - self._last_sync_time < self._sync_interval:
            return

        self._last_sync_time = now

        with self._lock:
            pending_sells_copy = dict(self._pending_sells)

        self.log.debug("触发定期同步 pending_sells=%s", len(pending_sells_copy))

        # 内存统计
        try:
            import psutil
            process = psutil.Process()
            mem = process.memory_info()
            orders_cache_size = len(self.exchange._orders_cache) if hasattr(self.exchange, '_orders_cache') else 0
            self.log.info(
                "Memory: RSS=%dMB orders_cache=%d positions=%d stop_loss_triggered=%d",
                mem.rss // 1024 // 1024,
                orders_cache_size,
                self.position_tracker.get_position_count(),
                len(self._stop_loss_triggered),
            )
        except ImportError:
            pass
        except Exception as e:
            self.log.debug("Memory stats error: %s", e)

        self.position_syncer.sync(pending_sells_copy)
        self._repair_positions_and_orders(pending_sells_copy)

    def _repair_positions_and_orders(self, pending_sells: Dict[str, Order]) -> None:
        """最小修复：批量补卖单、取消多余卖单"""
        positions_without_sells = self.position_syncer.get_positions_without_sells(pending_sells)

        rules = self._rules
        fee_rate = self._fee
        sell_requests: list[OrderRequest] = []
        sell_meta: list[tuple] = []  # (pos, buy_order)

        for pos in positions_without_sells:
            decision = self.strategy.should_sell(
                buy_price=pos.entry_price,
                buy_quantity=pos.quantity,
                current_price=self._current_price,
            )
            if decision:
                ex_order = self.exchange.get_order(pos.order_id)
                fee_paid_externally = ex_order.fee_paid_externally if ex_order else False
                if fee_paid_externally:
                    sell_qty = pos.quantity
                else:
                    sell_qty = pos.quantity * (1 - fee_rate)
                aligned_price = self.exchange.align_price(decision.price, rules)
                aligned_qty = self.exchange.align_quantity(sell_qty, rules)
                sell_requests.append(OrderRequest(side="sell", price=aligned_price, quantity=aligned_qty))
                sell_meta.append(pos)

        if sell_requests:
            self.log.debug("批量补卖单 count=%s", len(sell_requests))
            results = self.exchange.place_batch_orders(sell_requests)
            latest_repair_error: Optional[str] = None
            with self._lock:
                for idx, result in enumerate(results):
                    pos = sell_meta[idx]
                    if result.success and result.order_id:
                        placed_price = result.filled_price if result.filled_price is not None else sell_requests[idx].price
                        placed_qty = result.filled_quantity if result.filled_quantity is not None else sell_requests[idx].quantity
                        sell_order = Order(
                            order_id=result.order_id,
                            symbol=pos.symbol,
                            side="sell",
                            price=placed_price,
                            quantity=placed_qty,
                            grid_index=pos.grid_index,
                            state=OrderState.PLACED,
                            related_order_id=pos.order_id,
                        )
                        self._pending_sells[result.order_id] = sell_order
                        self.log.info("补卖单已下: %s, 价格=%s, 数量=%s", result.order_id, placed_price, placed_qty)
                    else:
                        error_msg = result.error or "下单失败"
                        latest_repair_error = f"补卖单下单失败: {error_msg}"
                        self.log.warning(
                            "补卖单下单失败 position_order=%s price=%s qty=%s error=%s",
                            pos.order_id,
                            sell_requests[idx].price,
                            sell_requests[idx].quantity,
                            error_msg,
                        )

            if latest_repair_error is not None:
                self._last_error = latest_repair_error
                self._last_error_time = time.time()
                self._update_status(force=True, source="repair_sell_order_failed")

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
            pending_buys_snapshot = dict(self._pending_buys)
            pending_sells_snapshot = dict(self._pending_sells)

        buy_orders = [
            {"price": o.price, "quantity": o.quantity}
            for o in pending_buys_snapshot.values()
        ]
        sell_orders = [
            {"price": o.price, "quantity": o.quantity}
            for o in pending_sells_snapshot.values()
        ]

        exchange_info = self.exchange.get_exchange_info() or {}
        exchange_id = str(exchange_info.get("id") or "unknown")

        strategy_extra: dict[str, Any] = {}
        try:
            strategy_extra = self.strategy.get_status_extra(
                current_price=self._current_price,
                pending_buy_orders=pending_buys_snapshot,
                pending_sell_orders=pending_sells_snapshot,
            )
        except Exception as err:
            self.log.debug("获取策略扩展状态失败: %s", err)

        exchange_extra: dict[str, Any] = {}
        try:
            exchange_extra = self.exchange.get_status_extra()
        except Exception as err:
            self.log.debug("获取交易所扩展状态失败: %s", err)

        extra_status = {
            **(strategy_extra or {}),
            **(exchange_extra or {}),
        }

        status = {
            "exchange": exchange_id,
            "current_price": self._current_price,
            "pending_buys": len(pending_buys_snapshot),
            "pending_sells": len(pending_sells_snapshot),
            "position_count": self.position_tracker.get_position_count(),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "last_error": self._last_error,
            "extra_status": extra_status,
        }

        try:
            self.on_status_update(status)
            self.log.debug(
                "状态已更新 source=%s force=%s price=%s buys=%s sells=%s positions=%s",
                source, force,
                status["current_price"],
                status["pending_buys"],
                status["pending_sells"],
                status["position_count"],
            )
        except Exception as e:
            self.log.warning("状态更新回调失败: %s", e)
