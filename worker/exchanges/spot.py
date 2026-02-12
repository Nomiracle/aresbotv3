"""通用现货交易所实现"""

import asyncio
import concurrent.futures
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, List, Optional

from worker.core.base_exchange import (
    BaseExchange,
    EditOrderRequest,
    ExchangeOrder,
    OrderRequest,
    OrderResult,
    OrderStatus,
    TradingRules,
)
from worker.core.log_utils import make_log_prefix
from worker.exchanges.stream.base import StreamManager
from worker.exchanges.stream.ccxt_stream import CcxtStreamManager

logger = logging.getLogger(__name__)


class ExchangeSpot(BaseExchange):
    """通用现货交易所（支持所有 CCXT 交易所）

    特性:
    - 自动检测 WebSocket 支持并创建 CcxtStreamManager
    - 不支持 WS 时走纯 REST 模式
    - 决策逻辑：缓存优先 → REST 兜底
    - 写操作 WS 优先 → REST 降级，WS 自动推送状态更新
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        exchange_id: str = "binance",
        testnet: bool = False,
    ):
        super().__init__(api_key, api_secret, symbol, testnet)

        self.exchange_id = exchange_id
        self._market_symbol = symbol

        timeout_raw = os.environ.get("EXCHANGE_SYNC_TIMEOUT", "10")
        try:
            self._sync_timeout = max(float(timeout_raw), 1.0)
        except ValueError:
            self._sync_timeout = 10.0

        self._trading_rules: Optional[TradingRules] = None
        self._fee_rate: Optional[float] = None
        self._markets_ready = False
        self._markets_last_attempt_at = 0.0
        markets_cooldown_raw = os.environ.get("EXCHANGE_MARKETS_RETRY_COOLDOWN", "5")
        try:
            self._markets_retry_cooldown = max(float(markets_cooldown_raw), 0.5)
        except ValueError:
            self._markets_retry_cooldown = 5.0
        self._log_prefix = make_log_prefix(self._market_symbol, api_key, exchange_id)

        # 创建 CCXT 实例
        self._exchange = self._create_exchange(
            exchange_id, api_key, api_secret, testnet, self._sync_timeout
        )

        # 检测 WS 能力，创建 StreamManager
        self._stream: Optional[StreamManager] = None
        has = getattr(self._exchange, "has", {})
        supports_ws = bool(
            has.get("watchTicker")
            or has.get("watchBidsAsks")
            or has.get("watchOrders")
        )

        if supports_ws:
            self._stream = CcxtStreamManager.acquire(
                exchange=self._exchange,
                api_key=api_key,
                api_secret=api_secret,
                exchange_id=exchange_id,
                testnet=testnet,
            )
            self._stream.start(self._market_symbol)
            logger.info("%s initialized with WebSocket", self._log_prefix)
        else:
            logger.info("%s initialized without WebSocket (REST only)", self._log_prefix)

    # ==================== 工厂方法 ====================

    @staticmethod
    def _create_exchange(
        exchange_id: str,
        api_key: str,
        api_secret: str,
        testnet: bool,
        sync_timeout: float,
    ) -> Any:
        """创建 CCXT/CCXT Pro 实例"""
        timeout_ms = int(sync_timeout * 1000)

        try:
            import ccxt.pro as ccxtpro

            if hasattr(ccxtpro, exchange_id):
                exchange_class = getattr(ccxtpro, exchange_id)
                exchange = exchange_class(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "enableRateLimit": True,
                        "timeout": timeout_ms,
                    }
                )
                if testnet:
                    if exchange_id in ("binance", "binanceusdm", "binancecoinm"):
                        exchange.enable_demo_trading(True)
                    else:
                        exchange.set_sandbox_mode(True)
                return exchange
        except ImportError:
            pass

        import ccxt

        if hasattr(ccxt, exchange_id):
            exchange_class = getattr(ccxt, exchange_id)
            exchange = exchange_class(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                    "timeout": timeout_ms,
                }
            )
            if testnet:
                if exchange_id in ("binance", "binanceusdm", "binancecoinm"):
                    exchange.enable_demo_trading(True)
                else:
                    exchange.set_sandbox_mode(True)
            return exchange

        raise ValueError(f"Unsupported exchange: {exchange_id}")

    # ==================== 读操作（缓存优先 → REST 兜底）====================

    def get_ticker_price(self) -> float:
        if self._stream is not None:
            price = self._stream.get_price(self._market_symbol)
            if price is not None:
                return price

        if not self._ensure_markets_loaded():
            raise TimeoutError(
                "load_markets is cooling down after previous failure"
            )

        try:
            ticker = self._run_sync(
                lambda: self._exchange.fetch_ticker(self._market_symbol)
            )
        except Exception as err:
            if _is_timeout_exception(err):
                raise TimeoutError(str(err)) from err
            raise
        price = _safe_float(ticker.get("last") or ticker.get("close"))
        return price

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        if self._stream is not None:
            cached = self._stream.get_order(order_id)
            if cached is not None and cached.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
            ):
                return cached

        try:
            raw_order = self._run_sync(
                lambda: self._exchange.fetch_order(
                    order_id, self._market_symbol
                )
            )
            return self._to_exchange_order(raw_order)
        except Exception as err:
            logger.warning(
                "%s fetch_order failed order_id=%s: %s",
                self._log_prefix,
                order_id,
                err,
            )
            if self._stream is not None:
                return self._stream.get_order(order_id)
            return None

    def get_open_orders(self) -> List[ExchangeOrder]:
        if self._stream is not None:
            orders = self._stream.get_open_orders(self._market_symbol)
            if orders:
                return orders

        try:
            raw_orders = self._run_sync(
                lambda: self._exchange.fetch_open_orders(self._market_symbol)
            )
            return [
                self._to_exchange_order(o)
                for o in raw_orders
                if isinstance(o, dict)
            ]
        except Exception as err:
            logger.warning(
                "%s fetch_open_orders failed: %s", self._log_prefix, err
            )
            return []

    # ==================== 写操作（WS优先 → REST降级）====================

    def place_batch_orders(self, orders: List[OrderRequest]) -> List[OrderResult]:
        if not orders:
            return []

        has = getattr(self._exchange, "has", {})
        if has.get("createOrderWs"):
            return self._place_one_by_one(orders, use_ws=True)
        if has.get("createOrders"):
            return self._place_batch(orders)

        return self._place_one_by_one(orders)

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        if not order_ids:
            return []

        has = getattr(self._exchange, "has", {})
        if has.get("cancelOrderWs"):
            return self._cancel_one_by_one(order_ids, use_ws=True)
        if has.get("cancelOrders"):
            return self._cancel_batch(order_ids)

        return self._cancel_one_by_one(order_ids)

    # ==================== 批量下单实现 ====================

    def _place_batch(self, orders: List[OrderRequest]) -> List[OrderResult]:
        results: List[OrderResult] = []
        batch_size = 5

        for i in range(0, len(orders), batch_size):
            batch = orders[i : i + batch_size]
            normalized = [self._normalize_create_order(o) for o in batch]

            try:
                response = self._run_sync(
                    lambda b=normalized: self._exchange.create_orders(b)
                )

                if not isinstance(response, list):
                    results.extend(
                        OrderResult(
                            success=False,
                            order_id=None,
                            status=OrderStatus.FAILED,
                            error="unexpected response",
                        )
                        for _ in batch
                    )
                    continue

                for item in response:
                    order_id = item.get("id") or item.get("orderId")
                    if order_id is not None:
                        results.append(
                            OrderResult(
                                success=True,
                                order_id=str(order_id),
                                status=OrderStatus.PLACED,
                            )
                        )
                    else:
                        results.append(
                            OrderResult(
                                success=False,
                                order_id=None,
                                status=OrderStatus.FAILED,
                                error=str(
                                    item.get("msg")
                                    or item.get("error")
                                    or "unknown"
                                ),
                            )
                        )
            except Exception as err:
                logger.warning(
                    "%s batch create_orders failed: %s, fallback to one-by-one",
                    self._log_prefix,
                    err,
                )
                results.extend(self._place_one_by_one(batch))

        return results

    def _place_one_by_one(self, orders: List[OrderRequest], use_ws: bool = False) -> List[OrderResult]:
        normalized_list = [self._normalize_create_order(o) for o in orders]

        def _make_coro(o: Dict[str, Any]):
            method = self._exchange.create_order_ws if use_ws else self._exchange.create_order
            return lambda: method(
                o["symbol"], o["type"], o["side"], o["amount"], o["price"], o.get("params", {}),
            )

        raw_results = self._run_sync_gather(
            [_make_coro(o) for o in normalized_list]
        )

        results: List[OrderResult] = []
        for raw in raw_results:
            if isinstance(raw, Exception):
                logger.warning("%s create_order failed: %s", self._log_prefix, raw)
                results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=str(raw)))
            else:
                order_id = raw.get("id") or raw.get("orderId")
                if order_id is None:
                    results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=f"missing order id: {raw}"))
                else:
                    results.append(OrderResult(success=True, order_id=str(order_id), status=OrderStatus.PLACED))

        return results

    # ==================== 批量撤单实现 ====================

    def _cancel_batch(self, order_ids: List[str]) -> List[OrderResult]:
        try:
            self._run_sync(
                lambda: self._exchange.cancel_orders(
                    order_ids, self._market_symbol
                )
            )
            return [
                OrderResult(
                    success=True,
                    order_id=oid,
                    status=OrderStatus.CANCELLED,
                )
                for oid in order_ids
            ]
        except Exception as err:
            logger.warning(
                "%s batch cancel_orders failed: %s, fallback to one-by-one",
                self._log_prefix,
                err,
            )
            return self._cancel_one_by_one(order_ids)

    def _cancel_one_by_one(self, order_ids: List[str], use_ws: bool = False) -> List[OrderResult]:
        def _make_coro(oid: str):
            method = self._exchange.cancel_order_ws if use_ws else self._exchange.cancel_order
            return lambda: method(oid, self._market_symbol)

        raw_results = self._run_sync_gather(
            [_make_coro(oid) for oid in order_ids]
        )

        results: List[OrderResult] = []
        for idx, raw in enumerate(raw_results):
            oid = order_ids[idx]
            if isinstance(raw, Exception):
                logger.warning("%s cancel_order failed order_id=%s: %s", self._log_prefix, oid, raw)
                results.append(OrderResult(success=False, order_id=oid, status=OrderStatus.FAILED, error=str(raw)))
            else:
                results.append(OrderResult(success=True, order_id=oid, status=OrderStatus.CANCELLED))

        return results

    # ==================== 元数据接口 ====================

    def get_exchange_info(self) -> Dict[str, str]:
        return {"id": self.exchange_id, "name": self.exchange_id, "type": "spot"}

    def get_status_extra(self) -> Dict[str, Any]:
        return {"ws_enabled": self._stream is not None}

    def get_trading_rules(self) -> TradingRules:
        if self._trading_rules is not None:
            return self._trading_rules

        if not self._ensure_markets_loaded(force=True):
            raise TimeoutError("load_markets failed while fetching trading rules")
        market = self._exchange.market(self._market_symbol)

        precision = market.get("precision", {})
        limits = market.get("limits", {})
        precision_mode = getattr(self._exchange, "precisionMode", None)

        tick_size, price_decimals = _build_rules_from_precision(
            precision.get("price", 8), precision_mode
        )
        step_size, qty_decimals = _build_rules_from_precision(
            precision.get("amount", 8), precision_mode
        )
        min_notional = float(
            limits.get("cost", {}).get("min", 0) or 0
        )

        self._trading_rules = TradingRules(
            tick_size=tick_size,
            price_decimals=price_decimals,
            step_size=step_size,
            qty_decimals=qty_decimals,
            min_notional=min_notional,
        )
        logger.info(
            "%s trading rules: tick=%s step=%s min_notional=%s",
            self._log_prefix,
            tick_size,
            step_size,
            min_notional,
        )
        return self._trading_rules

    def get_fee_rate(self) -> float:
        if self._fee_rate is not None:
            return self._fee_rate

        # 优先 fetchTradingFee（Binance 测试网不支持 sapi 费率端点）
        has = getattr(self._exchange, "has", {})
        supports_fetch_fee = bool(has.get("fetchTradingFee"))
        binance_testnet_sapi_unsupported = self.testnet and self.exchange_id in (
            "binance",
            "binanceusdm",
            "binancecoinm",
        )

        if supports_fetch_fee and not binance_testnet_sapi_unsupported:
            try:
                fee_info = self._run_sync(
                    lambda: self._exchange.fetch_trading_fee(self._market_symbol)
                )
                taker_fee = float(fee_info.get("taker", 0) or 0)
                if taker_fee > 0:
                    self._fee_rate = taker_fee
                    logger.info("%s 费率(API): taker=%.4f%%", self._log_prefix, taker_fee * 100)
                    return self._fee_rate
            except Exception as err:
                logger.debug("%s fetch_trading_fee 失败: %s", self._log_prefix, err)
        elif binance_testnet_sapi_unsupported:
            logger.debug(
                "%s testnet 模式跳过 fetch_trading_fee（sapi endpoint 不可用）",
                self._log_prefix,
            )
        else:
            logger.debug("%s fetch_trading_fee 不可用，改用市场费率", self._log_prefix)

        # 降级：从市场信息获取
        try:
            if not self._ensure_markets_loaded(force=True):
                raise TimeoutError("load_markets failed while fetching fee rate")
            market = self._exchange.market(self._market_symbol)
            taker_fee = float(market.get("taker", 0) or 0)
            if taker_fee > 0:
                self._fee_rate = taker_fee
                logger.info("%s 费率(市场): taker=%.4f%%", self._log_prefix, taker_fee * 100)
                return self._fee_rate
        except Exception as err:
            logger.debug("%s 从市场信息获取费率失败: %s", self._log_prefix, err)

        # 兜底默认值
        self._fee_rate = 0.001
        logger.info("%s 费率(默认): taker=%.4f%%", self._log_prefix, self._fee_rate * 100)
        return self._fee_rate

    # ==================== 生命周期 ====================

    def close(self) -> None:
        logger.info("%s closing", self._log_prefix)
        if self._stream is not None:
            self._stream.stop(self._market_symbol)
            if isinstance(self._stream, CcxtStreamManager):
                CcxtStreamManager.release(self._stream)
            self._stream = None
        logger.info("%s closed", self._log_prefix)

    # ==================== 内部工具 ====================

    def _ensure_markets_loaded(self, force: bool = False) -> bool:
        if self._markets_ready:
            return True

        now = time.time()
        if (
            not force
            and now - self._markets_last_attempt_at < self._markets_retry_cooldown
        ):
            return False

        self._markets_last_attempt_at = now

        try:
            self._run_sync(lambda: self._exchange.load_markets())
            self._markets_ready = True
            return True
        except Exception as err:
            if _is_timeout_exception(err):
                logger.warning(
                    "%s load_markets 超时: %s", self._log_prefix, err
                )
            else:
                logger.warning(
                    "%s load_markets 失败: %s", self._log_prefix, err
                )
            return False

    def _run_sync(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        timeout: Optional[float] = None,
    ) -> Any:
        """在 WS 事件循环上执行协程（如果有），否则新建循环"""
        request_timeout = timeout if timeout is not None else self._sync_timeout

        if (
            self._stream is not None
            and isinstance(self._stream, CcxtStreamManager)
            and self._stream._loop is not None
            and self._stream._loop.is_running()
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    coro_factory(), self._stream._loop
                )
                return future.result(timeout=request_timeout)
            except (asyncio.TimeoutError, concurrent.futures.TimeoutError) as err:
                raise TimeoutError(
                    f"sync timeout after {request_timeout:.2f}s"
                ) from err
            except Exception as err:
                if _is_timeout_exception(err):
                    raise TimeoutError(str(err)) from err
                raise

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(coro_factory(), timeout=request_timeout)
            )
        except asyncio.TimeoutError as err:
            raise TimeoutError(
                f"sync timeout after {request_timeout:.2f}s"
            ) from err
        except Exception as err:
            if _is_timeout_exception(err):
                raise TimeoutError(str(err)) from err
            raise
        finally:
            loop.close()

    def _run_sync_gather(
        self,
        coro_factories: List[Callable[[], Awaitable[Any]]],
        timeout: Optional[float] = None,
    ) -> List[Any]:
        """并发执行多个协程，异常不中断其他任务"""
        if not coro_factories:
            return []

        async def _gather():
            return await asyncio.gather(
                *[f() for f in coro_factories], return_exceptions=True
            )

        return self._run_sync(lambda: _gather(), timeout=timeout)

    def edit_batch_orders(self, edits: List[EditOrderRequest]) -> List[OrderResult]:
        """批量改单：优先 editOrderWs → editOrder → cancel + recreate"""
        if not edits:
            return []

        has = getattr(self._exchange, "has", {})
        if has.get("editOrderWs"):
            return self._edit_via_edit_order(edits, use_ws=True)
        if has.get("editOrder"):
            return self._edit_via_edit_order(edits)

        return super().edit_batch_orders(edits)

    def _edit_via_edit_order(self, edits: List[EditOrderRequest], use_ws: bool = False) -> List[OrderResult]:
        """通过 ccxt editOrder/editOrderWs 并发改单"""
        def _make_coro(e: EditOrderRequest):
            method = self._exchange.edit_order_ws if use_ws else self._exchange.edit_order
            return lambda: method(
                e.order_id, self._market_symbol, "limit", e.side.lower(), e.quantity, e.price,
            )

        raw_results = self._run_sync_gather(
            [_make_coro(e) for e in edits]
        )

        results: List[OrderResult] = []
        for idx, raw in enumerate(raw_results):
            if isinstance(raw, Exception):
                logger.warning("%s edit_order failed order_id=%s: %s", self._log_prefix, edits[idx].order_id, raw)
                results.append(OrderResult(success=False, order_id=edits[idx].order_id, status=OrderStatus.FAILED, error=str(raw)))
            else:
                new_id = raw.get("id") or raw.get("orderId")
                if new_id is not None:
                    results.append(OrderResult(success=True, order_id=str(new_id), status=OrderStatus.PLACED))
                else:
                    results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="missing order id"))

        return results

    def _normalize_create_order(self, order: OrderRequest) -> Dict[str, Any]:
        return {
            "symbol": self._market_symbol,
            "type": "limit",
            "side": order.side.lower(),
            "amount": order.quantity,
            "price": order.price,
            "params": {},
        }

    def _to_exchange_order(self, raw_order: Dict[str, Any]) -> ExchangeOrder:
        order_id = str(raw_order.get("id") or raw_order.get("orderId"))
        filled = _safe_float(
            raw_order.get("filled") or raw_order.get("executedQty")
        )
        status = _map_order_status(raw_order.get("status"), filled)

        # 判断手续费是否外部支付（如使用BNB抵扣）
        fee_paid_externally = False
        fee_info = raw_order.get("fee")
        if isinstance(fee_info, dict):
            fee_currency = fee_info.get("currency", "")
            if fee_currency == "BNB":
                fee_paid_externally = True

        return ExchangeOrder(
            order_id=order_id,
            symbol=str(raw_order.get("symbol", self._market_symbol)),
            side=str(raw_order.get("side") or "").lower(),
            price=_safe_float(raw_order.get("price")),
            quantity=_safe_float(
                raw_order.get("amount") or raw_order.get("origQty")
            ),
            filled_quantity=filled,
            status=status,
            fee_paid_externally=fee_paid_externally,
            extra={
                "raw_status": str(raw_order.get("status") or ""),
                "fee": raw_order.get("fee"),
            },
        )



# ==================== 模块级工具函数 ====================


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _is_timeout_exception(err: BaseException) -> bool:
    if isinstance(
        err, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)
    ):
        return True

    return type(err).__name__ in {"RequestTimeout", "ReadTimeout", "TimeoutError"}


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


def _build_rules_from_precision(
    precision_value: object,
    precision_mode: Optional[int],
    default_decimals: int = 8,
) -> tuple[float, int]:
    """根据 CCXT 精度配置生成 (step/tick size, decimals)"""
    try:
        numeric_precision = Decimal(str(precision_value))
    except (InvalidOperation, TypeError, ValueError):
        decimals = default_decimals
        return 10 ** (-decimals), decimals

    if numeric_precision <= 0:
        decimals = default_decimals
        return 10 ** (-decimals), decimals

    try:
        import ccxt.pro as ccxtpro

        decimal_places_mode = getattr(ccxtpro, "DECIMAL_PLACES", None)
        tick_size_mode = getattr(ccxtpro, "TICK_SIZE", None)
    except ImportError:
        import ccxt

        decimal_places_mode = getattr(ccxt, "DECIMAL_PLACES", None)
        tick_size_mode = getattr(ccxt, "TICK_SIZE", None)

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
