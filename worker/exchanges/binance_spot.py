"""币安现货交易所实现 - 使用CCXT Pro"""

import asyncio
import logging
import os
import threading
import time
import weakref
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import ccxt.pro as ccxtpro

from worker.core.base_exchange import (
    BaseExchange,
    ExchangeOrder,
    OrderResult,
    OrderStatus,
    TradingRules,
)


logger = logging.getLogger(__name__)

# SharedKey: (api_key, api_secret, testnet, exchange_name)
SharedKey = Tuple[str, str, bool, str]

# Memory optimization constants
MAX_ORDER_CACHE_SIZE = 1000
MAX_ERROR_LOG_CACHE = 100


@dataclass
class _WsCallbacks:
    symbol: str
    on_price_update: Callable[[float], None]
    on_order_update: Callable[[Dict[str, Any]], None]


@dataclass
class _SharedWsContext:
    key: SharedKey
    log_prefix: str
    exchange: Any
    sync_timeout: float
    # Reference count for this context
    ref_count: int = 0
    # callbacks keyed by subscriber_id, each has symbol info
    callbacks: Dict[int, _WsCallbacks] = field(default_factory=dict)
    loop: Optional[asyncio.AbstractEventLoop] = None
    thread: Optional[threading.Thread] = None
    running: bool = True
    tasks: List[asyncio.Task[Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # last_price per symbol
    last_prices: Dict[str, float] = field(default_factory=dict)
    # subscribed symbols
    subscribed_symbols: set = field(default_factory=set)
    error_log_cache: Dict[str, float] = field(default_factory=dict)
    error_log_interval: float = 2.0


class BinanceSpot(BaseExchange):
    """币安现货交易所

    内部使用共享 WebSocket 获取:
    - 市场价格 (watchBidsAsks)
    - 订单更新 (watchOrders)

    注意：WebSocket 管理与回调分发均为内部逻辑，不对外暴露 start/stop 接口。
    """

    _EXCHANGE_NAME = "binance_spot"
    _shared_lock = threading.Lock()
    _shared_contexts: Dict[SharedKey, _SharedWsContext] = {}

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
    ):
        super().__init__(api_key, api_secret, symbol, testnet)
        self._market_symbol = self._to_market_symbol(symbol)
        self._rest_symbol = self._market_symbol.replace("/", "")

        timeout_raw = os.environ.get("BINANCE_SYNC_TIMEOUT", "10")
        try:
            self._sync_timeout = max(float(timeout_raw), 1.0)
        except ValueError:
            self._sync_timeout = 10.0

        self._current_price: Optional[float] = None
        self._orders_cache: Dict[str, ExchangeOrder] = {}
        self._trading_rules: Optional[TradingRules] = None
        self._open_orders_reconcile_every_calls = 3
        self._open_orders_call_count = 0

        self._price_lock = threading.Lock()
        self._orders_lock = threading.Lock()

        api_key_prefix = (api_key or "")[:8]
        self._log_prefix = f"[{self._market_symbol}] [{api_key_prefix}]"
        self._subscriber_id = id(self)

        self._shared_key = self._make_shared_key(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        self._shared_context = self._acquire_shared_context(
            key=self._shared_key,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            sync_timeout=self._sync_timeout,
            log_prefix=self._log_prefix,
        )
        self._exchange = self._shared_context.exchange
        self._register_callbacks()
        self._finalizer = weakref.finalize(
            self,
            BinanceSpot._finalize_instance,
            self._shared_key,
            self._subscriber_id,
            self._market_symbol,
        )

        logger.info("%s BinanceSpot initialized testnet=%s", self._log_prefix, testnet)

    def __del__(self) -> None:
        finalizer = getattr(self, "_finalizer", None)
        if finalizer and finalizer.alive:
            finalizer()

    @staticmethod
    def _to_market_symbol(symbol: str) -> str:
        raw_symbol = symbol.strip().upper()
        if "/" in raw_symbol:
            return raw_symbol
        if raw_symbol.endswith("USDT") and len(raw_symbol) > 4:
            return f"{raw_symbol[:-4]}/USDT"
        return raw_symbol

    @staticmethod
    def _mask_credential(value: str, keep_prefix: int = 4, keep_suffix: int = 4) -> str:
        raw_value = str(value or "")
        if not raw_value:
            return ""

        if len(raw_value) <= keep_prefix + keep_suffix:
            return raw_value

        return f"{raw_value[:keep_prefix]}***{raw_value[-keep_suffix:]}"

    @classmethod
    def _make_shared_key(
        cls,
        api_key: str,
        api_secret: str,
        testnet: bool,
    ) -> SharedKey:
        return (
            api_key,
            api_secret,
            bool(testnet),
            cls._EXCHANGE_NAME,
        )

    @classmethod
    def _create_exchange(
        cls,
        api_key: str,
        api_secret: str,
        testnet: bool,
        sync_timeout: float,
    ) -> Any:
        timeout_ms = int(sync_timeout * 1000)
        exchange = ccxtpro.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": timeout_ms,
            "options": {
                "defaultType": "spot",
            },
        })
        if testnet:
            exchange.enable_demo_trading(True)
        return exchange

    @classmethod
    def _acquire_shared_context(
        cls,
        key: SharedKey,
        api_key: str,
        api_secret: str,
        testnet: bool,
        sync_timeout: float,
        log_prefix: str,
    ) -> _SharedWsContext:
        with cls._shared_lock:
            context = cls._shared_contexts.get(key)
            if context is not None:
                # Increment reference count
                context.ref_count += 1
                logger.debug("%s reusing shared context, ref_count=%d", log_prefix, context.ref_count)
                return context

            secret_preview = cls._mask_credential(api_secret, keep_prefix=4, keep_suffix=4)
            logger.info("%s creating shared exchange testnet=%s api_secret=%s", log_prefix, testnet, secret_preview)

            exchange = cls._create_exchange(
                api_key=api_key,
                api_secret=api_secret,
                testnet=testnet,
                sync_timeout=sync_timeout,
            )
            context = _SharedWsContext(
                key=key,
                log_prefix=log_prefix,
                exchange=exchange,
                sync_timeout=sync_timeout,
                ref_count=1,  # Initialize reference count
            )
            context.thread = threading.Thread(
                target=cls._run_shared_loop,
                args=(key,),
                daemon=True,
                name=f"BinanceSpotWS-{api_key[:8]}",
            )
            cls._shared_contexts[key] = context
            context.thread.start()
            logger.info("%s shared ws thread started: %s ref_count=1", log_prefix, context.thread.name)
            return context

    def _register_callbacks(self) -> None:
        with self._shared_context.lock:
            self._shared_context.callbacks[self._subscriber_id] = _WsCallbacks(
                symbol=self._market_symbol,
                on_price_update=self._on_shared_price,
                on_order_update=self._on_shared_order,
            )
            # Add symbol to subscribed set
            self._shared_context.subscribed_symbols.add(self._market_symbol)

    def _on_shared_price(self, price: float) -> None:
        if price <= 0:
            return
        with self._price_lock:
            self._current_price = price

    def _on_shared_order(self, order: Dict[str, Any]) -> None:
        self._update_order_cache(order)

    @classmethod
    def _should_log_error(cls, context: _SharedWsContext, error_key: str) -> bool:
        current_ts = time.time()
        with context.lock:
            # Cleanup expired entries when cache grows too large
            if len(context.error_log_cache) > MAX_ERROR_LOG_CACHE:
                cutoff = current_ts - context.error_log_interval * 10
                context.error_log_cache = {
                    k: v for k, v in context.error_log_cache.items()
                    if v > cutoff
                }

            last_ts = context.error_log_cache.get(error_key, 0.0)
            if current_ts - last_ts >= context.error_log_interval:
                context.error_log_cache[error_key] = current_ts
                return True
        return False

    @classmethod
    async def _cancel_context_tasks(cls, context: _SharedWsContext) -> None:
        with context.lock:
            tasks = list(context.tasks)
        if not tasks:
            return

        for task in tasks:
            if not task.done():
                task.cancel()

        # Wait with timeout to avoid blocking indefinitely
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            logger.debug("%s cancel tasks timeout, forcing", context.log_prefix)

        with context.lock:
            context.tasks = []

    @classmethod
    async def _shutdown_context(cls, context: _SharedWsContext) -> None:
        await cls._cancel_context_tasks(context)
        try:
            await asyncio.wait_for(context.exchange.close(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.debug("%s close exchange timeout", context.log_prefix)
        except Exception as err:
            logger.debug("%s close exchange failed: %s", context.log_prefix, err)

    @classmethod
    def _finalize_instance(cls, key: SharedKey, subscriber_id: int, symbol: str) -> None:
        with cls._shared_lock:
            context = cls._shared_contexts.get(key)
            if context is None:
                return

            # Decrement reference count
            context.ref_count -= 1
            remaining_refs = context.ref_count

        with context.lock:
            context.callbacks.pop(subscriber_id, None)
            # Remove symbol if no other callbacks need it
            symbol_still_needed = any(
                cb.symbol == symbol for cb in context.callbacks.values()
            )
            if not symbol_still_needed:
                context.subscribed_symbols.discard(symbol)

        logger.debug(
            "%s finalize subscriber=%d symbol=%s ref_count=%d",
            context.log_prefix,
            subscriber_id,
            symbol,
            remaining_refs,
        )

        # Only cleanup when reference count reaches 0
        if remaining_refs > 0:
            return

        # Mark as not running and get loop/thread references
        with context.lock:
            context.running = False
            loop = context.loop
            thread = context.thread

        logger.info("%s ref_count=0, shutting down shared context", context.log_prefix)

        # Try to shutdown gracefully if loop is still running
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(cls._shutdown_context(context), loop)
                future.result(timeout=min(context.sync_timeout, 5.0))
            except Exception:
                # Ignore shutdown errors - loop may have closed already
                pass

        # Wait for thread to finish
        if thread and thread.is_alive():
            thread.join(timeout=3)

        with cls._shared_lock:
            current = cls._shared_contexts.get(key)
            if current is context:
                cls._shared_contexts.pop(key, None)
                logger.info("%s shared context removed from cache", context.log_prefix)

    @classmethod
    def _dispatch_price(cls, context: _SharedWsContext, symbol: str, price: float) -> None:
        with context.lock:
            callbacks = [
                cb for cb in context.callbacks.values()
                if cb.symbol == symbol
            ]
        for callback in callbacks:
            try:
                callback.on_price_update(price)
            except Exception as err:
                logger.warning("%s on_price_update callback error: %s", context.log_prefix, err)

    @classmethod
    def _dispatch_order(cls, context: _SharedWsContext, order: Dict[str, Any]) -> None:
        order_symbol = order.get("symbol", "")
        with context.lock:
            callbacks = [
                cb for cb in context.callbacks.values()
                if cb.symbol == order_symbol
            ]
        for callback in callbacks:
            try:
                callback.on_order_update(order)
            except Exception as err:
                logger.warning("%s on_order_update callback error: %s", context.log_prefix, err)


    @staticmethod
    def _should_ignore_ws_cancelled_error(loop_context: Dict[str, Any]) -> bool:
        """Ignore noisy ccxt websocket cancellation callbacks during shutdown."""
        exception = loop_context.get("exception")
        if not isinstance(exception, asyncio.CancelledError):
            return False

        handle = loop_context.get("handle")
        if handle is None:
            return False

        handle_text = repr(handle)
        return (
            "ccxt/async_support/base/ws/client.py" in handle_text
            or "ccxt/async_support/base/ws/future.py" in handle_text
            or "after_interrupt" in handle_text
            or "Future.race" in handle_text
        )

    @classmethod
    def _build_loop_exception_handler(
        cls,
    ) -> Callable[[asyncio.AbstractEventLoop, Dict[str, Any]], None]:
        def _handler(loop: asyncio.AbstractEventLoop, loop_context: Dict[str, Any]) -> None:
            if cls._should_ignore_ws_cancelled_error(loop_context):
                return

            loop.default_exception_handler(loop_context)

        return _handler

    @classmethod
    def _run_shared_loop(cls, key: SharedKey) -> None:
        with cls._shared_lock:
            context = cls._shared_contexts.get(key)
        if context is None:
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(cls._build_loop_exception_handler())
        with context.lock:
            context.loop = loop

        try:
            loop.run_until_complete(cls._ws_main(context))
        except Exception as err:
            logger.debug("%s shared ws loop error: %s", context.log_prefix, err)
        finally:
            # Cancel all pending tasks before closing loop
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

            # Close exchange connection
            try:
                loop.run_until_complete(
                    asyncio.wait_for(context.exchange.close(), timeout=2.0)
                )
            except Exception:
                pass

            loop.close()
            with context.lock:
                context.loop = None
                context.tasks = []
            logger.info("%s shared ws loop closed", context.log_prefix)

    @classmethod
    async def _ws_main(cls, context: _SharedWsContext) -> None:
        with context.lock:
            context.tasks = [
                asyncio.create_task(cls._watch_ticker(context)),
                asyncio.create_task(cls._watch_orders(context)),
            ]
            tasks = list(context.tasks)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        with context.lock:
            context.tasks = []

        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.warning("%s shared ws task error: %s", context.log_prefix, result)

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_order_not_found_error(err: Exception) -> bool:
        error_text = str(err).lower()
        return any(
            marker in error_text
            for marker in (
                "unknown order",
                "order does not exist",
                "order not found",
                "not found",
                "-2013",
            )
        )

    @classmethod
    async def _watch_ticker(cls, context: _SharedWsContext) -> None:
        """Watch ticker for all subscribed symbols."""
        msg_count = 0
        update_count = 0
        started_at = time.time()
        last_log_time = started_at

        while context.running:
            try:
                # Get current subscribed symbols
                with context.lock:
                    symbols = list(context.subscribed_symbols)

                if not symbols:
                    await asyncio.sleep(0.5)
                    continue

                bids_asks = await context.exchange.watch_bids_asks(symbols)
                msg_count += 1

                if not isinstance(bids_asks, dict):
                    continue

                # Process each symbol's data
                for symbol, symbol_data in bids_asks.items():
                    if not isinstance(symbol_data, dict):
                        continue

                    bid = symbol_data.get("bid")
                    ask = symbol_data.get("ask")
                    if bid is None or ask is None:
                        continue

                    current_price = (cls._safe_float(bid) + cls._safe_float(ask)) / 2
                    if current_price <= 0:
                        continue

                    with context.lock:
                        last_price = context.last_prices.get(symbol)
                        price_changed = (
                            last_price is None
                            or abs(current_price - last_price) > 1e-12
                        )
                        if price_changed:
                            context.last_prices[symbol] = current_price
                            update_count += 1

                    if price_changed:
                        cls._dispatch_price(context, symbol, current_price)

                now = time.time()
                if now - last_log_time >= 30:
                    elapsed = max(now - started_at, 1e-9)
                    with context.lock:
                        symbol_count = len(context.subscribed_symbols)
                    logger.debug(
                        "%s ws_ticker symbols=%s msgs=%s(%.2f/s) updates=%s(%.2f/s)",
                        context.log_prefix,
                        symbol_count,
                        msg_count,
                        msg_count / elapsed,
                        update_count,
                        update_count / elapsed,
                    )
                    last_log_time = now
            except asyncio.CancelledError:
                break
            except Exception as err:
                if context.running:
                    error_key = f"watch_bids_asks_{type(err).__name__}"
                    if cls._should_log_error(context, error_key):
                        logger.warning("%s watch_bids_asks failed: %s", context.log_prefix, err)
                    await asyncio.sleep(1)

    @staticmethod
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

    @classmethod
    async def _watch_orders(cls, context: _SharedWsContext) -> None:
        """Watch orders for the account (all symbols)."""
        while context.running:
            try:
                # watch_orders without symbol watches all orders for the account
                raw_orders = await context.exchange.watch_orders()

                # Normalize raw_orders to a list of dicts
                if raw_orders is None:
                    continue
                if isinstance(raw_orders, dict):
                    orders = [raw_orders]
                elif isinstance(raw_orders, list):
                    orders = [item for item in raw_orders if isinstance(item, dict)]
                else:
                    # Unexpected type (e.g. str), skip
                    logger.debug(
                        "%s watch_orders unexpected type: %s",
                        context.log_prefix,
                        type(raw_orders).__name__,
                    )
                    continue

                for raw_order in orders:
                    order = cls._normalize_order_payload_ws(raw_order)
                    if order is None:
                        continue

                    order_symbol = str(order.get("symbol", ""))
                    normalized_order_symbol = order_symbol.upper()
                    if (
                        "/" not in normalized_order_symbol
                        and normalized_order_symbol.endswith("USDT")
                        and len(normalized_order_symbol) > 4
                    ):
                        normalized_order_symbol = f"{normalized_order_symbol[:-4]}/USDT"

                    with context.lock:
                        subscribed_symbols = {
                            str(symbol).upper()
                            for symbol in context.subscribed_symbols
                        }

                    if normalized_order_symbol not in subscribed_symbols:
                        logger.debug(
                            "%s skip unrelated order update symbol=%s id=%s",
                            context.log_prefix,
                            order_symbol,
                            order.get("id") or order.get("orderId"),
                        )
                        continue

                    order_symbol = normalized_order_symbol
                    order["symbol"] = normalized_order_symbol
                    filled = cls._safe_float(order.get("filled") or order.get("executedQty"))
                    status = cls._map_order_status(order.get("status"), filled)

                    if status == OrderStatus.FILLED:
                        fee = order.get("fee")
                        fee_currency = ""
                        if isinstance(fee, dict):
                            fee_currency = str(fee.get("currency") or "").upper()
                        logger.info(
                            "[%s] order_filled id=%s side=%s price=%s qty=%s filled=%s fee_currency=%s",
                            order_symbol,
                            order.get("id") or order.get("orderId"),
                            order.get("side"),
                            order.get("price"),
                            order.get("amount") or order.get("origQty"),
                            filled,
                            fee_currency,
                        )
                    elif status == OrderStatus.CANCELLED:
                        logger.info(
                            "[%s] order_cancelled id=%s side=%s status=%s",
                            order_symbol,
                            order.get("id") or order.get("orderId"),
                            order.get("side"),
                            str(order.get("status") or "").lower(),
                        )
                    else:
                        logger.debug(
                            "[%s] order_update id=%s side=%s status=%s price=%s qty=%s filled=%s",
                            order_symbol,
                            order.get("id") or order.get("orderId"),
                            order.get("side"),
                            str(order.get("status") or "").lower(),
                            order.get("price"),
                            order.get("amount") or order.get("origQty"),
                            filled,
                        )

                    cls._dispatch_order(context, order)
            except asyncio.CancelledError:
                break
            except Exception as err:
                if context.running:
                    error_key = f"watch_orders_{type(err).__name__}"
                    if cls._should_log_error(context, error_key):
                        logger.warning("%s watch_orders failed: %s", context.log_prefix, err)
                    await asyncio.sleep(1)

    @staticmethod
    def _normalize_order_payload_ws(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize order payload from WebSocket updates."""
        order_id = order.get("id") or order.get("orderId")
        if order_id is None:
            return None

        normalized = dict(order)
        normalized["id"] = str(order_id)
        normalized["orderId"] = str(order_id)
        # Ensure symbol is present
        raw_symbol = order.get("symbol") or ""
        normalized["symbol"] = str(raw_symbol)
        if "side" in normalized and normalized["side"] is not None:
            normalized["side"] = str(normalized["side"]).lower()
        return normalized

    def _update_order_cache(self, order: Dict[str, Any]) -> Optional[ExchangeOrder]:
        order_id_value = order.get("id") or order.get("orderId")
        if order_id_value is None:
            self._log_warning("skip order cache update, missing order id: %s", order)
            return None

        order_id = str(order_id_value)
        filled = self._safe_float(order.get("filled") or order.get("executedQty"))
        status = self._map_order_status(order.get("status"), filled)

        exchange_order = ExchangeOrder(
            order_id=order_id,
            symbol=str(order.get("symbol") or self._market_symbol),
            side=str(order.get("side") or "").lower(),
            price=self._safe_float(order.get("price")),
            quantity=self._safe_float(order.get("amount") or order.get("origQty")),
            filled_quantity=filled,
            status=status,
            extra={
                "raw_status": str(order.get("status") or ""),
                "info": order.get("info"),
                "fee": order.get("fee"),
            },
        )

        with self._orders_lock:
            self._orders_cache[order_id] = exchange_order
            # Cleanup old completed orders when cache grows too large
            self._cleanup_old_orders()
        return exchange_order

    def _cleanup_old_orders(self) -> None:
        """Clean up old completed orders to prevent memory growth.

        Must be called with self._orders_lock held.
        """
        if len(self._orders_cache) <= MAX_ORDER_CACHE_SIZE:
            return

        # Find completed orders
        completed = [
            (oid, o) for oid, o in self._orders_cache.items()
            if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED)
        ]

        if len(completed) > MAX_ORDER_CACHE_SIZE // 2:
            # Sort by order_id (older orders have smaller IDs) and remove older half
            completed.sort(key=lambda x: x[1].order_id)
            for oid, _ in completed[:len(completed) // 2]:
                self._orders_cache.pop(oid, None)

    def _get_shared_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        with self._shared_context.lock:
            loop = self._shared_context.loop
        if loop and loop.is_running():
            return loop
        return None

    def _run_sync(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        timeout: Optional[float] = None,
    ) -> Any:
        request_timeout = timeout if timeout is not None else self._sync_timeout
        loop = self._get_shared_loop()

        if loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
                return future.result(timeout=request_timeout)
            except asyncio.TimeoutError as err:
                self._log_warning("_run_sync timeout after %.2fs", request_timeout)
                raise TimeoutError(f"_run_sync timeout after {request_timeout:.2f}s") from err
            except RuntimeError as err:
                self._log_warning("_run_sync shared loop runtime error, fallback to temp loop: %s", err)

        temp_loop = asyncio.new_event_loop()
        try:
            return temp_loop.run_until_complete(asyncio.wait_for(coro_factory(), timeout=request_timeout))
        except asyncio.TimeoutError as err:
            self._log_warning("_run_sync timeout after %.2fs", request_timeout)
            raise TimeoutError(f"_run_sync timeout after {request_timeout:.2f}s") from err
        finally:
            temp_loop.close()

    def _log_debug(self, message: str, *args: object) -> None:
        logger.debug("%s " + message, self._log_prefix, *args)

    def _log_info(self, message: str, *args: object) -> None:
        logger.info("%s " + message, self._log_prefix, *args)

    def _log_warning(self, message: str, *args: object) -> None:
        logger.warning("%s " + message, self._log_prefix, *args)

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {"id": "binance_spot", "name": "Binance Spot", "type": "spot"}

    @staticmethod
    def _build_rules_from_precision(
        precision_value: object,
        precision_mode: Optional[int],
        default_decimals: int = 8,
    ) -> tuple[float, int]:
        """根据CCXT精度配置生成 (step/tick size, decimals)。"""
        try:
            numeric_precision = Decimal(str(precision_value))
        except (InvalidOperation, TypeError, ValueError):
            decimals = default_decimals
            return 10 ** (-decimals), decimals

        if numeric_precision <= 0:
            decimals = default_decimals
            return 10 ** (-decimals), decimals

        decimal_places_mode = getattr(ccxtpro, "DECIMAL_PLACES", None)
        tick_size_mode = getattr(ccxtpro, "TICK_SIZE", None)

        if decimal_places_mode is not None and precision_mode == decimal_places_mode:
            decimals = max(int(numeric_precision), 0)
            return 10 ** (-decimals), decimals

        if tick_size_mode is not None and precision_mode == tick_size_mode:
            normalized = numeric_precision.normalize()
            decimals = max(-normalized.as_tuple().exponent, 0)
            return float(numeric_precision), decimals

        if isinstance(precision_value, int):
            decimals = max(precision_value, 0)
            return 10 ** (-decimals), decimals

        normalized = numeric_precision.normalize()
        decimals = max(-normalized.as_tuple().exponent, 0)
        return float(numeric_precision), decimals

    def get_trading_rules(self) -> TradingRules:
        """获取交易规则"""
        if self._trading_rules:
            self._log_debug("use cached trading rules")
            return self._trading_rules

        self._log_debug("loading trading rules from exchange")
        self._run_sync(lambda: self._exchange.load_markets())
        market = self._exchange.market(self._market_symbol)

        precision = market.get("precision", {})
        limits = market.get("limits", {})
        precision_mode = getattr(self._exchange, "precisionMode", None)

        tick_size, price_decimals = self._build_rules_from_precision(
            precision.get("price", 8),
            precision_mode,
            default_decimals=8,
        )
        step_size, qty_decimals = self._build_rules_from_precision(
            precision.get("amount", 8),
            precision_mode,
            default_decimals=8,
        )
        min_notional = limits.get("cost", {}).get("min", 0) or 0

        self._trading_rules = TradingRules(
            tick_size=tick_size,
            price_decimals=price_decimals,
            step_size=step_size,
            qty_decimals=qty_decimals,
            min_notional=min_notional,
        )
        self._log_info(
            "trading rules loaded tick=%s step=%s min_notional=%s",
            tick_size,
            step_size,
            min_notional,
        )
        return self._trading_rules

    def get_fee_rate(self) -> float:
        return 0.001  # 0.1%

    def get_ticker_price(self) -> float:
        """获取当前价格 - 优先使用WS缓存"""
        with self._price_lock:
            if self._current_price is not None:
                self._log_debug("ticker from ws cache: %s", self._current_price)
                return self._current_price

        ticker = self._run_sync(
            lambda: self._exchange.fetch_ticker(self._market_symbol),
            timeout=self._sync_timeout,
        )
        last_price = ticker.get("last") or ticker.get("close")
        parsed_price = self._safe_float(last_price)
        if parsed_price > 0:
            with self._price_lock:
                self._current_price = parsed_price

        self._log_debug("ticker from rest: %s", last_price)
        return parsed_price

    def place_batch_orders(self, orders: List[Dict]) -> List[OrderResult]:
        """批量下单。

        优先级：
        1) create_orders (if exchange.has("create_orders"))
        2) create_order_ws (逐单)
        3) create_order (逐单)
        """
        if not orders:
            return []

        self._log_debug("place_batch_orders called count=%s", len(orders))

        if not self._supports_exchange_method("create_orders", "createOrders"):
            if self._supports_exchange_method("create_order_ws", "createOrderWs"):
                self._log_info("create_orders unsupported, fallback to create_order_ws one-by-one")
                return self._place_orders_one_by_one(orders, use_ws=True)

            self._log_info("create_orders/create_order_ws unsupported, fallback to create_order one-by-one")
            return self._place_orders_one_by_one(orders, use_ws=False)

        results = []
        for i in range(0, len(orders), 5):
            batch = orders[i:i + 5]
            batch_orders: List[Dict[str, Any]] = []
            for order in batch:
                amount = order.get("amount", order.get("quantity"))
                order_type = str(order.get("type", "limit")).lower()
                order_params = order.get("params") if isinstance(order.get("params"), dict) else {}
                normalized_params = dict(order_params)
                for field_name in ("timeInForce", "postOnly", "triggerPrice"):
                    if field_name in order and field_name not in normalized_params:
                        normalized_params[field_name] = order[field_name]

                batch_orders.append({
                    "symbol": str(order.get("symbol", self._market_symbol)),
                    "type": order_type,
                    "side": str(order["side"]).lower(),
                    "amount": amount,
                    "price": order.get("price"),
                    "params": normalized_params,
                })

            try:
                response = self._run_sync(
                    lambda: self._exchange.create_orders(batch_orders),
                    timeout=self._sync_timeout,
                )
                if not isinstance(response, list):
                    self._log_warning("create_orders unexpected response=%s", response)
                    results.extend([
                        OrderResult(
                            success=False,
                            order_id=None,
                            status=OrderStatus.FAILED,
                            error="create_orders unexpected response",
                        )
                        for _ in batch
                    ])
                    continue

                for item in response:
                    order_id = item.get("id") or item.get("orderId")
                    if order_id is not None:
                        if isinstance(item, dict):
                            self._update_order_cache(item)
                        results.append(OrderResult(
                            success=True,
                            order_id=str(order_id),
                            status=OrderStatus.PLACED,
                        ))
                    else:
                        self._log_warning("batch order failed response=%s", item)
                        results.append(OrderResult(
                            success=False,
                            order_id=None,
                            status=OrderStatus.FAILED,
                            error=str(item.get("msg") or item.get("error") or "Unknown error"),
                        ))
            except Exception as err:
                self._log_debug("batch create_orders unavailable: %s", err)

                # Some exchanges expose create_orders but do not support it for spot.
                # Degrade gracefully to per-order endpoints.
                if self._supports_exchange_method("create_order_ws", "createOrderWs"):
                    self._log_debug("fallback to create_order_ws one-by-one")
                    results.extend(self._place_orders_one_by_one(batch, use_ws=True))
                else:
                    self._log_debug("fallback to create_order one-by-one")
                    results.extend(self._place_orders_one_by_one(batch, use_ws=False))

        success_count = sum(1 for result in results if result.success)
        self._log_debug("place_batch_orders finished success=%s total=%s", success_count, len(results))
        return results

    def _supports_exchange_method(self, has_key: str, method_name: str) -> bool:
        """Check exchange capability from `exchange.has` and actual method existence."""
        has_map = getattr(self._exchange, "has", {})
        supports_from_has = bool(has_map.get(has_key) or has_map.get(method_name))
        supports_from_attr = callable(getattr(self._exchange, method_name, None))
        return supports_from_has and supports_from_attr

    @staticmethod
    def _normalize_order_payload(order: Dict[str, Any], default_symbol: str) -> Dict[str, Any]:
        """Normalize order payload for one-by-one endpoints."""
        amount = order.get("amount", order.get("quantity"))
        order_type = str(order.get("type", "limit")).lower()
        order_params = order.get("params") if isinstance(order.get("params"), dict) else {}
        params = dict(order_params)
        for field_name in ("timeInForce", "postOnly", "triggerPrice"):
            if field_name in order and field_name not in params:
                params[field_name] = order[field_name]

        return {
            "symbol": str(order.get("symbol", default_symbol)),
            "type": order_type,
            "side": str(order["side"]).lower(),
            "amount": amount,
            "price": order.get("price"),
            "params": params,
        }

    def _place_orders_one_by_one(self, orders: List[Dict], use_ws: bool) -> List[OrderResult]:
        """Fallback path for per-order creation via ws/rest endpoints."""
        results: List[OrderResult] = []
        method_name = "create_order_ws" if use_ws else "create_order"

        for raw_order in orders:
            order = self._normalize_order_payload(raw_order, self._market_symbol)

            try:
                if use_ws:
                    response = self._run_sync(
                        lambda order=order: self._exchange.create_order_ws(
                            order["symbol"],
                            order["type"],
                            order["side"],
                            order["amount"],
                            order["price"],
                            order["params"],
                        ),
                        timeout=self._sync_timeout,
                    )
                else:
                    response = self._run_sync(
                        lambda order=order: self._exchange.create_order(
                            order["symbol"],
                            order["type"],
                            order["side"],
                            order["amount"],
                            order["price"],
                            order["params"],
                        ),
                        timeout=self._sync_timeout,
                    )

                order_id = response.get("id") or response.get("orderId")
                if order_id is None:
                    raise ValueError(f"{method_name} missing order id response={response}")

                if isinstance(response, dict):
                    self._update_order_cache(response)
                results.append(OrderResult(
                    success=True,
                    order_id=str(order_id),
                    status=OrderStatus.PLACED,
                ))
            except Exception as err:
                self._log_warning("%s fallback failed: %s", method_name, err)
                results.append(OrderResult(
                    success=False,
                    order_id=None,
                    status=OrderStatus.FAILED,
                    error=str(err),
                ))

        success_count = sum(1 for result in results if result.success)
        self._log_debug("%s fallback finished success=%s total=%s", method_name, success_count, len(results))
        return results

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        """批量取消订单

        优先级：
        1) cancel_orders (批量取消)
        2) cancel_order_ws (逐个 ws 取消)
        3) cancel_order (逐个 rest 取消)
        """
        if not order_ids:
            return []

        self._log_debug("cancel_batch_orders called count=%s", len(order_ids))

        # 1. 尝试 cancel_orders (批量取消)
        if self._supports_exchange_method("cancel_orders", "cancelOrders"):
            try:
                self._run_sync(
                    lambda: self._exchange.cancel_orders(order_ids, self._market_symbol),
                    timeout=self._sync_timeout,
                )
                results = []
                for order_id in order_ids:
                    with self._orders_lock:
                        if order_id in self._orders_cache:
                            self._orders_cache[order_id].status = OrderStatus.CANCELLED
                    results.append(OrderResult(
                        success=True,
                        order_id=order_id,
                        status=OrderStatus.CANCELLED,
                    ))
                self._log_info("cancel_batch_orders via cancel_orders success count=%s", len(results))
                return results
            except Exception as err:
                self._log_debug("cancel_orders not supported or failed: %s, trying fallback", err)

        # 2. 尝试 cancel_order_ws (逐个 ws 取消)
        if self._supports_exchange_method("cancel_order_ws", "cancelOrderWs"):
            try:
                results = self._cancel_orders_one_by_one(order_ids, use_ws=True)
                success_count = sum(1 for r in results if r.success)
                self._log_info("cancel_batch_orders via cancel_order_ws success=%s total=%s", success_count, len(results))
                return results
            except Exception as err:
                self._log_debug("cancel_order_ws failed: %s, trying fallback", err)

        # 3. 尝试 cancel_order (逐个 rest 取消)
        if self._supports_exchange_method("cancel_order", "cancelOrder"):
            try:
                results = self._cancel_orders_one_by_one(order_ids, use_ws=False)
                success_count = sum(1 for r in results if r.success)
                self._log_info("cancel_batch_orders via cancel_order success=%s total=%s", success_count, len(results))
                return results
            except Exception as err:
                self._log_warning("cancel_order failed: %s", err)
                return [
                    OrderResult(
                        success=False,
                        order_id=order_id,
                        status=OrderStatus.FAILED,
                        error=str(err),
                    )
                    for order_id in order_ids
                ]

        # 所有方法都不支持
        self._log_warning("cancel_batch_orders: no supported cancel method available")
        return [
            OrderResult(
                success=False,
                order_id=order_id,
                status=OrderStatus.FAILED,
                error="No supported cancel method",
            )
            for order_id in order_ids
        ]

    def _cancel_orders_one_by_one(self, order_ids: List[str], use_ws: bool) -> List[OrderResult]:
        """逐个取消订单"""
        results: List[OrderResult] = []
        method_name = "cancel_order_ws" if use_ws else "cancel_order"

        for order_id in order_ids:
            try:
                if use_ws:
                    self._run_sync(
                        lambda oid=order_id: self._exchange.cancel_order_ws(oid, self._market_symbol),
                        timeout=self._sync_timeout,
                    )
                else:
                    self._run_sync(
                        lambda oid=order_id: self._exchange.cancel_order(oid, self._market_symbol),
                        timeout=self._sync_timeout,
                    )

                with self._orders_lock:
                    if order_id in self._orders_cache:
                        self._orders_cache[order_id].status = OrderStatus.CANCELLED

                results.append(OrderResult(
                    success=True,
                    order_id=order_id,
                    status=OrderStatus.CANCELLED,
                ))
            except Exception as err:
                self._log_warning("%s failed order_id=%s: %s", method_name, order_id, err)
                results.append(OrderResult(
                    success=False,
                    order_id=order_id,
                    status=OrderStatus.FAILED,
                    error=str(err),
                ))

        return results

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        """查询订单 - 优先使用WS缓存"""
        with self._orders_lock:
            cached_order = self._orders_cache.get(order_id)
        if cached_order is not None and cached_order.status not in {
            OrderStatus.PLACED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            self._log_debug("get_order from cache order_id=%s", order_id)
            return cached_order

        try:
            self._log_debug("get_order from rest order_id=%s", order_id)
            order = self._run_sync(
                lambda: self._exchange.fetch_order(order_id, self._market_symbol),
                timeout=self._sync_timeout,
            )
            self._update_order_cache(order)
            with self._orders_lock:
                return self._orders_cache.get(order_id)
        except Exception as err:
            self._log_warning("get_order failed order_id=%s error=%s", order_id, err)
            return cached_order

    def get_open_orders(self) -> List[ExchangeOrder]:
        """获取未完成订单"""
        with self._orders_lock:
            self._open_orders_call_count += 1
            should_refresh_from_rest = (
                self._open_orders_call_count % self._open_orders_reconcile_every_calls == 0
            )
            cached_orders = list(self._orders_cache.values())
        open_orders = [
            order
            for order in cached_orders
            if order.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
        ]
        if open_orders and not should_refresh_from_rest:
            self._log_debug("get_open_orders from cache count=%s", len(open_orders))
            return open_orders

        started_at = time.time()
        try:
            self._log_debug("get_open_orders from rest")
            orders = self._run_sync(
                lambda: self._exchange.fetch_open_orders(self._market_symbol),
                timeout=self._sync_timeout,
            )

            rest_open_ids: set[str] = set()
            for order in orders:
                if isinstance(order, dict):
                    exchange_order = self._update_order_cache(order)
                    if exchange_order is not None:
                        rest_open_ids.add(exchange_order.order_id)

            with self._orders_lock:
                stale_open_ids = [
                    order.order_id
                    for order in self._orders_cache.values()
                    if order.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
                    and order.order_id not in rest_open_ids
                ]

            for stale_order_id in stale_open_ids:
                try:
                    self._log_debug("reconcile stale open order from rest order_id=%s", stale_order_id)
                    order = self._run_sync(
                        lambda oid=stale_order_id: self._exchange.fetch_order(oid, self._market_symbol),
                        timeout=self._sync_timeout,
                    )
                    if isinstance(order, dict):
                        self._update_order_cache(order)
                except Exception as err:
                    if self._is_order_not_found_error(err):
                        self._log_info(
                            "reconcile stale order not found, mark cancelled order_id=%s",
                            stale_order_id,
                        )
                        with self._orders_lock:
                            cached = self._orders_cache.get(stale_order_id)
                            if cached is not None:
                                cached.status = OrderStatus.CANCELLED
                    else:
                        self._log_warning(
                            "reconcile stale order failed, keep cache order_id=%s error=%s",
                            stale_order_id,
                            err,
                        )

            with self._orders_lock:
                updated_orders = list(self._orders_cache.values())
            results = [
                order
                for order in updated_orders
                if order.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
            ]
            self._log_debug(
                "get_open_orders from rest count=%s cost=%.3fs",
                len(results),
                time.time() - started_at,
            )
            return results
        except Exception as err:
            self._log_warning(
                "get_open_orders failed after %.3fs: %s",
                time.time() - started_at,
                err,
            )
            return []

    def get_balance(self, asset: str) -> float:
        """获取资产余额"""
        balance = self._run_sync(
            lambda: self._exchange.fetch_balance(),
            timeout=self._sync_timeout,
        )
        free_balance = balance.get(asset, {}).get("free", 0)
        self._log_debug("get_balance asset=%s free=%s", asset, free_balance)
        return free_balance

    def close(self) -> None:
        """关闭交易所连接，释放资源"""
        self._log_info("closing exchange connection")
        finalizer = getattr(self, "_finalizer", None)
        if finalizer and finalizer.alive:
            finalizer()
        self._log_info("exchange connection closed")
