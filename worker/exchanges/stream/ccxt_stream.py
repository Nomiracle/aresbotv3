"""基于 CCXT Pro 的数据流管理器实现"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from worker.core.base_exchange import ExchangeOrder, OrderStatus
from worker.exchanges.stream.base import StreamManager

logger = logging.getLogger(__name__)

# SharedKey: (api_key, api_secret, exchange_id, testnet)
SharedKey = Tuple[str, str, str, bool]

MAX_ORDER_CACHE_SIZE = 1000
MAX_ERROR_LOG_CACHE = 100
PRICE_MAX_AGE_SECONDS = 5.0
RECONCILE_INTERVAL_CALLS = 3
RECONCILE_INTERVAL_SECONDS = 30.0
ERROR_LOG_INTERVAL = 2.0


class CcxtStreamManager(StreamManager):
    """基于 CCXT Pro WebSocket 的数据流管理器

    特性:
    - 同一 (api_key, api_secret, exchange_id, testnet) 共享一个实例
    - 通过 acquire/release 管理引用计数
    - WS 线程写入缓存，外部同步读取
    - 内置对账逻辑
    """

    _pool_lock = threading.Lock()
    _pool: Dict[SharedKey, "CcxtStreamManager"] = {}

    # ==================== acquire / release ====================

    @classmethod
    def acquire(
        cls,
        exchange: Any,
        api_key: str,
        api_secret: str,
        exchange_id: str,
        testnet: bool,
    ) -> "CcxtStreamManager":
        """获取或创建共享实例，ref_count+1"""
        key: SharedKey = (api_key, api_secret, exchange_id, testnet)
        with cls._pool_lock:
            instance = cls._pool.get(key)
            if instance is not None and instance._running:
                instance._ref_count += 1
                logger.debug(
                    "[%s] reuse stream, ref_count=%d",
                    exchange_id,
                    instance._ref_count,
                )
                return instance

            if instance is not None:
                logger.warning(
                    "[%s] replacing stale stream instance", exchange_id
                )
                cls._pool.pop(key, None)

            instance = cls(
                key=key,
                exchange=exchange,
                exchange_id=exchange_id,
            )
            instance._ref_count = 1
            cls._pool[key] = instance
            instance._start_ws_thread()
            logger.info(
                "[%s] created stream, ref_count=1", exchange_id
            )
            return instance

    @classmethod
    def release(cls, instance: "CcxtStreamManager") -> None:
        """ref_count-1，归零则销毁"""
        with cls._pool_lock:
            instance._ref_count -= 1
            remaining = instance._ref_count
            logger.debug(
                "[%s] release stream, ref_count=%d",
                instance._exchange_id,
                remaining,
            )
            if remaining > 0:
                return
            cls._pool.pop(instance._key, None)

        instance._shutdown()

    # ==================== 初始化 ====================

    def __init__(
        self,
        key: SharedKey,
        exchange: Any,
        exchange_id: str,
    ) -> None:
        self._key = key
        self._exchange = exchange
        self._exchange_id = exchange_id
        self._ref_count = 0

        # 缓存
        self._prices: Dict[str, Tuple[float, float]] = {}
        self._orders: Dict[str, ExchangeOrder] = {}
        self._lock = threading.Lock()

        # 订阅的交易对
        self._subscribed_symbols: Set[str] = set()

        # WS 线程
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._running = False

        # 对账状态
        self._reconcile_call_count = 0
        self._last_reconcile_time = 0.0

        # 统计计数器
        self._stats_ticker_msgs = 0
        self._stats_price_updates = 0
        self._stats_order_msgs = 0
        self._stats_started_at = time.time()

        # 错误日志限流
        self._error_log_cache: Dict[str, float] = {}

    # ==================== StreamManager 接口 ====================

    def start(self, symbol: str) -> None:
        with self._lock:
            self._subscribed_symbols.add(symbol)
        logger.debug("[%s] subscribed symbol: %s", self._exchange_id, symbol)

    def stop(self, symbol: str) -> None:
        with self._lock:
            self._subscribed_symbols.discard(symbol)
        logger.debug("[%s] unsubscribed symbol: %s", self._exchange_id, symbol)

    def get_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            entry = self._prices.get(symbol)
            if entry is None:
                return None
            price, ts = entry
            if time.time() - ts > PRICE_MAX_AGE_SECONDS:
                return None
            return price

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        with self._lock:
            return self._orders.get(order_id)

    def get_open_orders(self, symbol: str) -> List[ExchangeOrder]:
        self._maybe_reconcile(symbol)

        with self._lock:
            return [
                o
                for o in self._orders.values()
                if o.symbol == symbol
                and o.status
                in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
            ]

    # ==================== 对账逻辑 ====================

    def _maybe_reconcile(self, symbol: str) -> None:
        """按策略判断是否需要对账"""
        self._reconcile_call_count += 1
        now = time.time()

        should = (
            self._reconcile_call_count % RECONCILE_INTERVAL_CALLS == 0
            or now - self._last_reconcile_time > RECONCILE_INTERVAL_SECONDS
        )
        if not should:
            return

        self._last_reconcile_time = now
        self._do_reconcile(symbol)

    def _do_reconcile(self, symbol: str) -> None:
        """执行对账"""
        try:
            rest_orders = self._run_on_loop(
                self._exchange.fetch_open_orders(symbol)
            )
        except Exception as err:
            self._log_error_throttled(
                "reconcile_fetch", "reconcile fetch_open_orders failed: %s", err
            )
            return

        rest_ids: Set[str] = set()
        for raw_order in rest_orders:
            if not isinstance(raw_order, dict):
                continue
            order = self._normalize_order(raw_order, symbol)
            if order is None:
                continue
            rest_ids.add(order.order_id)
            with self._lock:
                self._orders[order.order_id] = order

        with self._lock:
            stale_ids = [
                o.order_id
                for o in self._orders.values()
                if o.symbol == symbol
                and o.status
                in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
                and o.order_id not in rest_ids
            ]

        for order_id in stale_ids:
            try:
                raw_order = self._run_on_loop(
                    self._exchange.fetch_order(order_id, symbol)
                )
                order = self._normalize_order(raw_order, symbol)
                if order is not None:
                    with self._lock:
                        self._orders[order.order_id] = order
            except Exception as err:
                error_text = str(err).lower()
                is_not_found = any(
                    marker in error_text
                    for marker in (
                        "unknown order",
                        "order does not exist",
                        "order not found",
                        "not found",
                        "-2013",
                    )
                )
                if is_not_found:
                    with self._lock:
                        cached = self._orders.get(order_id)
                        if cached is not None:
                            cached.status = OrderStatus.CANCELLED
                    logger.info(
                        "[%s] reconcile: order %s not found, marked cancelled",
                        self._exchange_id,
                        order_id,
                    )
                else:
                    self._log_error_throttled(
                        f"reconcile_order_{order_id}",
                        "reconcile fetch_order %s failed: %s",
                        order_id,
                        err,
                    )

    # ==================== WS 线程管理 ====================

    def _start_ws_thread(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"WS-{self._exchange_id}-{self._key[0][:8]}",
        )
        self._thread.start()
        self._loop_ready.wait(timeout=5.0)

    def _shutdown(self) -> None:
        logger.info("[%s] shutting down stream", self._exchange_id)
        self._running = False

        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._close_exchange(), loop
                )
                future.result(timeout=3.0)
            except Exception:
                pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        logger.info("[%s] stream shut down", self._exchange_id)

    async def _close_exchange(self) -> None:
        try:
            await asyncio.wait_for(self._exchange.close(), timeout=2.0)
        except Exception:
            pass

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._loop_exception_handler)

        self._loop = loop
        self._loop_ready.set()

        try:
            loop.run_until_complete(self._ws_main())
        except Exception as err:
            logger.debug("[%s] ws loop error: %s", self._exchange_id, err)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            try:
                loop.run_until_complete(
                    asyncio.wait_for(self._exchange.close(), timeout=2.0)
                )
            except Exception:
                pass

            loop.close()
            self._loop = None
            self._running = False
            self._loop_ready.clear()
            logger.info("[%s] ws loop closed", self._exchange_id)

    def _loop_exception_handler(
        self,
        loop: asyncio.AbstractEventLoop,
        context: Dict[str, Any],
    ) -> None:
        exception = context.get("exception")
        if isinstance(exception, asyncio.CancelledError):
            handle_text = repr(context.get("handle", ""))
            if "ccxt" in handle_text or "after_interrupt" in handle_text:
                return
        loop.default_exception_handler(context)

    async def _ws_main(self) -> None:
        tasks = [
            asyncio.create_task(self._watch_ticker()),
            asyncio.create_task(self._watch_orders()),
            asyncio.create_task(self._log_stats_loop()),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    "[%s] ws task error: %s", self._exchange_id, result
                )

    # ==================== WS 监听循环 ====================

    async def _watch_ticker(self) -> None:
        last_prices: Dict[str, float] = {}

        while self._running:
            try:
                with self._lock:
                    symbols = list(self._subscribed_symbols)

                if not symbols:
                    await asyncio.sleep(0.5)
                    continue

                bids_asks = await self._exchange.watch_bids_asks(symbols)
                self._stats_ticker_msgs += 1

                if not isinstance(bids_asks, dict):
                    continue

                for symbol, data in bids_asks.items():
                    if not isinstance(data, dict):
                        continue

                    bid = data.get("bid")
                    ask = data.get("ask")
                    if bid is None or ask is None:
                        continue

                    price = (float(bid) + float(ask)) / 2
                    if price <= 0:
                        continue

                    prev = last_prices.get(symbol)
                    if prev is not None and abs(price - prev) < 1e-12:
                        continue

                    last_prices[symbol] = price
                    self._stats_price_updates += 1
                    with self._lock:
                        self._prices[symbol] = (price, time.time())

            except asyncio.CancelledError:
                break
            except Exception as err:
                if self._running:
                    self._log_error_throttled(
                        f"watch_ticker_{type(err).__name__}",
                        "watch_ticker error: %s",
                        err,
                    )
                await asyncio.sleep(1)

    async def _watch_orders(self) -> None:
        while self._running:
            try:
                raw_orders = await self._exchange.watch_orders()

                if raw_orders is None:
                    continue
                if isinstance(raw_orders, dict):
                    raw_orders = [raw_orders]
                if not isinstance(raw_orders, list):
                    continue

                with self._lock:
                    subscribed = {
                        s.upper() for s in self._subscribed_symbols
                    }

                for raw_order in raw_orders:
                    if not isinstance(raw_order, dict):
                        continue
                    self._stats_order_msgs += 1

                    order_symbol = str(raw_order.get("symbol", ""))
                    if order_symbol.upper() not in subscribed:
                        continue

                    order = self._normalize_order(raw_order, order_symbol)
                    if order is None:
                        continue

                    if order.status == OrderStatus.FILLED:
                        logger.info(
                            "[%s] order_filled id=%s side=%s price=%s qty=%s",
                            order_symbol,
                            order.order_id,
                            order.side,
                            order.price,
                            order.filled_quantity,
                        )
                    elif order.status == OrderStatus.CANCELLED:
                        logger.info(
                            "[%s] order_cancelled id=%s side=%s",
                            order_symbol,
                            order.order_id,
                            order.side,
                        )

                    with self._lock:
                        self._orders[order.order_id] = order
                        self._cleanup_old_orders()

            except asyncio.CancelledError:
                break
            except Exception as err:
                if self._running:
                    self._log_error_throttled(
                        f"watch_orders_{type(err).__name__}",
                        "watch_orders error: %s",
                        err,
                    )
                await asyncio.sleep(1)

    # ==================== 统计日志 ====================

    async def _log_stats_loop(self) -> None:
        """每 10 秒输出一次缓存统计"""
        while self._running:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break

            with self._lock:
                total_orders = len(self._orders)
                active_orders = 0
                filled_orders = 0
                partial_orders = 0
                cancelled_orders = 0
                for o in self._orders.values():
                    if o.status == OrderStatus.FILLED:
                        filled_orders += 1
                    elif o.status == OrderStatus.PARTIALLY_FILLED:
                        partial_orders += 1
                    elif o.status == OrderStatus.CANCELLED:
                        cancelled_orders += 1
                    elif o.status == OrderStatus.PLACED:
                        active_orders += 1
                terminal_orders = filled_orders + cancelled_orders
                price_count = len(self._prices)
                symbols_count = len(self._subscribed_symbols)

            elapsed = max(time.time() - self._stats_started_at, 1e-9)
            logger.info(
                "[%s] stream_stats symbols=%d prices=%d "
                "orders_cache=%d active=%d filled=%d partial=%d terminal=%d "
                "ticker_msgs=%d(%.1f/s) price_updates=%d(%.1f/s) order_msgs=%d(%.1f/s)",
                self._exchange_id,
                symbols_count,
                price_count,
                total_orders,
                active_orders,
                filled_orders,
                partial_orders,
                terminal_orders,
                self._stats_ticker_msgs,
                self._stats_ticker_msgs / elapsed,
                self._stats_price_updates,
                self._stats_price_updates / elapsed,
                self._stats_order_msgs,
                self._stats_order_msgs / elapsed,
            )

    # ==================== 工具方法 ====================

    def _run_on_loop(self, coro: Any) -> Any:
        """在 WS 事件循环上执行协程（同步阻塞）"""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("ws loop not running")

        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=10.0)

    @staticmethod
    def _normalize_order(
        raw_order: Dict[str, Any], default_symbol: str
    ) -> Optional[ExchangeOrder]:
        order_id = raw_order.get("id") or raw_order.get("orderId")
        if order_id is None:
            return None

        filled = _safe_float(
            raw_order.get("filled") or raw_order.get("executedQty")
        )
        status = _map_order_status(raw_order.get("status"), filled)

        return ExchangeOrder(
            order_id=str(order_id),
            symbol=str(raw_order.get("symbol") or default_symbol),
            side=str(raw_order.get("side") or "").lower(),
            price=_safe_float(raw_order.get("price")),
            quantity=_safe_float(
                raw_order.get("amount") or raw_order.get("origQty")
            ),
            filled_quantity=filled,
            status=status,
            extra={
                "raw_status": str(raw_order.get("status") or ""),
                "fee": raw_order.get("fee"),
            },
        )

    def _cleanup_old_orders(self) -> None:
        """清理旧订单（必须持有 _lock）"""
        if len(self._orders) <= MAX_ORDER_CACHE_SIZE:
            return

        completed = [
            (oid, o)
            for oid, o in self._orders.items()
            if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED)
        ]

        if len(completed) > MAX_ORDER_CACHE_SIZE // 2:
            completed.sort(key=lambda x: x[1].order_id)
            for oid, _ in completed[: len(completed) // 2]:
                self._orders.pop(oid, None)

    def _log_error_throttled(
        self, error_key: str, message: str, *args: object
    ) -> None:
        """限流错误日志"""
        now = time.time()
        last_ts = self._error_log_cache.get(error_key, 0.0)
        if now - last_ts < ERROR_LOG_INTERVAL:
            return

        self._error_log_cache[error_key] = now

        if len(self._error_log_cache) > MAX_ERROR_LOG_CACHE:
            cutoff = now - ERROR_LOG_INTERVAL * 10
            self._error_log_cache = {
                k: v for k, v in self._error_log_cache.items() if v > cutoff
            }

        logger.warning(f"[{self._exchange_id}] " + message, *args)


# ==================== 模块级工具函数 ====================


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _map_order_status(raw_status: object, filled: float) -> OrderStatus:
    status = str(raw_status or "").lower()
    if status in {"closed", "filled"}:
        return OrderStatus.FILLED
    if status in {"canceled", "cancelled", "expired"}:
        return OrderStatus.CANCELLED
    if status in {"rejected", "failed"}:
        return OrderStatus.FAILED
    if status in {"partially_filled", "partial"}:
        return OrderStatus.PARTIALLY_FILLED
    if status in {"open", "new"}:
        if filled > 0:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.PLACED
    if filled > 0:
        return OrderStatus.PARTIALLY_FILLED
    return OrderStatus.PLACED
