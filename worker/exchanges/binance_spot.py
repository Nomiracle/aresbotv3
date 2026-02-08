"""币安现货交易所实现 - 使用CCXT Pro"""

import asyncio
import logging
import os
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional
import ccxt.pro as ccxtpro

from worker.base_exchange import (
    BaseExchange, OrderResult, OrderStatus, ExchangeOrder, TradingRules
)


logger = logging.getLogger(__name__)


class BinanceSpot(BaseExchange):
    """币安现货交易所

    内部使用WebSocket获取:
    - 市场价格 (watchTicker)
    - 订单更新 (watchOrders)
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
    ):
        super().__init__(api_key, api_secret, symbol, testnet)

        # CCXT Pro 实例
        self._exchange = ccxtpro.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        })
        if testnet:
            self._exchange.set_sandbox_mode(True)

        # WebSocket 数据缓存
        self._current_price: Optional[float] = None
        self._orders_cache: Dict[str, ExchangeOrder] = {}
        self._trading_rules: Optional[TradingRules] = None

        # WebSocket 控制
        self._ws_running = False
        self._ws_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        timeout_raw = os.environ.get("BINANCE_SYNC_TIMEOUT", "10")
        try:
            self._sync_timeout = max(float(timeout_raw), 1.0)
        except ValueError:
            self._sync_timeout = 10.0

        api_key_prefix = (api_key or "")[:8]
        self._log_prefix = f"[{self.symbol}] [{api_key_prefix}]"
        logger.info("%s BinanceSpot initialized testnet=%s", self._log_prefix, testnet)

    def _log_debug(self, message: str, *args) -> None:
        logger.debug("%s " + message, self._log_prefix, *args)

    def _log_info(self, message: str, *args) -> None:
        logger.info("%s " + message, self._log_prefix, *args)

    def _log_warning(self, message: str, *args) -> None:
        logger.warning("%s " + message, self._log_prefix, *args)

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {'id': 'binance_spot', 'name': 'Binance Spot', 'type': 'spot'}

    def start_ws(self) -> None:
        """启动WebSocket线程"""
        if self._ws_running:
            self._log_debug("start_ws skipped: already running")
            return
        self._ws_running = True
        self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self._ws_thread.start()
        self._log_info("WebSocket thread started")

    def stop_ws(self) -> None:
        """停止WebSocket"""
        self._ws_running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._log_info("WebSocket stop requested")

    def _run_ws_loop(self) -> None:
        """WebSocket事件循环"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_main())
        except Exception as err:
            self._log_warning("WebSocket loop error: %s", err)
        finally:
            self._loop.close()
            self._log_info("WebSocket loop closed")

    async def _ws_main(self) -> None:
        """WebSocket主任务"""
        await asyncio.gather(
            self._watch_ticker(),
            self._watch_orders(),
            return_exceptions=True
        )

    async def _watch_ticker(self) -> None:
        """监听价格"""
        while self._ws_running:
            try:
                ticker = await self._exchange.watch_ticker(self.symbol)
                self._current_price = ticker.get('last') or ticker.get('close')
            except Exception as err:
                self._log_warning("watch_ticker failed: %s", err)
                await asyncio.sleep(1)

    async def _watch_orders(self) -> None:
        """监听订单更新"""
        while self._ws_running:
            try:
                orders = await self._exchange.watch_orders(self.symbol)
                for order in orders:
                    self._update_order_cache(order)
            except Exception as err:
                self._log_warning("watch_orders failed: %s", err)
                await asyncio.sleep(1)

    def _update_order_cache(self, order: dict) -> None:
        """更新订单缓存"""
        status_map = {
            'open': OrderStatus.PLACED,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELLED,
        }
        filled = order.get('filled', 0)

        if order['status'] == 'open' and filled > 0:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = status_map.get(order['status'], OrderStatus.PLACED)

        self._orders_cache[order['id']] = ExchangeOrder(
            order_id=order['id'],
            symbol=order['symbol'],
            side=order['side'],
            price=order['price'],
            quantity=order['amount'],
            filled_quantity=filled,
            status=status,
        )

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

        decimal_places_mode = getattr(ccxtpro, 'DECIMAL_PLACES', None)
        tick_size_mode = getattr(ccxtpro, 'TICK_SIZE', None)

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
        self._run_sync(self._exchange.load_markets())
        market = self._exchange.market(self.symbol)

        precision = market.get('precision', {})
        limits = market.get('limits', {})
        precision_mode = getattr(self._exchange, 'precisionMode', None)

        tick_size, price_decimals = self._build_rules_from_precision(
            precision.get('price', 8),
            precision_mode,
            default_decimals=8,
        )
        step_size, qty_decimals = self._build_rules_from_precision(
            precision.get('amount', 8),
            precision_mode,
            default_decimals=8,
        )
        min_notional = limits.get('cost', {}).get('min', 0) or 0

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
        if self._current_price:
            self._log_debug("ticker from ws cache: %s", self._current_price)
            return self._current_price
        # 使用同步方法获取价格
        ticker = self._run_sync(self._exchange.fetch_ticker(self.symbol), timeout=self._sync_timeout)
        last_price = ticker['last']
        self._log_debug("ticker from rest: %s", last_price)
        return last_price

    def _run_sync(self, coro, timeout: Optional[float] = None):
        """在同步环境中运行异步协程"""
        request_timeout = timeout if timeout is not None else self._sync_timeout

        if self._loop and self._loop.is_running():
            import concurrent.futures
            future = concurrent.futures.Future()

            def callback():
                try:
                    result = asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=request_timeout)
                    future.set_result(result)
                except Exception as err:
                    future.set_exception(err)

            self._loop.call_soon_threadsafe(callback)
            return future.result(timeout=request_timeout)
        else:
            # 创建新的事件循环运行
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(asyncio.wait_for(coro, timeout=request_timeout))
            except asyncio.TimeoutError as err:
                self._log_warning("_run_sync timeout after %.2fs", request_timeout)
                raise TimeoutError(f"_run_sync timeout after {request_timeout:.2f}s") from err
            finally:
                loop.close()

    def place_batch_orders(self, orders: List[Dict]) -> List[OrderResult]:
        """批量下单 - 使用币安batchOrders API（最多5个）"""
        if not orders:
            return []

        self._log_debug("place_batch_orders called count=%s", len(orders))
        results = []
        for i in range(0, len(orders), 5):
            batch = orders[i:i+5]
            batch_params = []
            for o in batch:
                batch_params.append({
                    'symbol': self.symbol.replace('/', ''),
                    'side': o['side'].upper(),
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'quantity': o['quantity'],
                    'price': o['price'],
                })

            try:
                resp = self._run_sync(self._exchange.private_post_batch_orders({
                    'batchOrders': self._exchange.json(batch_params)
                }), timeout=self._sync_timeout)
                for item in resp:
                    if 'orderId' in item:
                        results.append(OrderResult(
                            success=True,
                            order_id=str(item['orderId']),
                            status=OrderStatus.PLACED,
                        ))
                    else:
                        self._log_warning("batch order failed response=%s", item)
                        results.append(OrderResult(
                            success=False,
                            order_id=None,
                            status=OrderStatus.FAILED,
                            error=item.get('msg', 'Unknown error'),
                        ))
            except Exception as err:
                self._log_warning("batch order request error: %s", err)
                results.extend([
                    OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=str(err))
                    for _ in batch
                ])
        success_count = sum(1 for result in results if result.success)
        self._log_debug("place_batch_orders finished success=%s total=%s", success_count, len(results))
        return results

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        """批量取消订单"""
        if not order_ids:
            return []

        self._log_debug("cancel_batch_orders called count=%s", len(order_ids))
        try:
            self._run_sync(self._exchange.cancel_orders(order_ids, self.symbol), timeout=self._sync_timeout)
            results = []
            for oid in order_ids:
                if oid in self._orders_cache:
                    self._orders_cache[oid].status = OrderStatus.CANCELLED
                results.append(OrderResult(
                    success=True,
                    order_id=oid,
                    status=OrderStatus.CANCELLED,
                ))
            self._log_debug("cancel_batch_orders success count=%s", len(results))
            return results
        except Exception as err:
            self._log_warning("cancel_batch_orders error: %s", err)
            return [
                OrderResult(success=False, order_id=oid, status=OrderStatus.FAILED, error=str(err))
                for oid in order_ids
            ]

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        """查询订单 - 优先使用WS缓存"""
        if order_id in self._orders_cache:
            self._log_debug("get_order from cache order_id=%s", order_id)
            return self._orders_cache[order_id]
        # 回退到REST
        try:
            self._log_debug("get_order from rest order_id=%s", order_id)
            order = self._run_sync(self._exchange.fetch_order(order_id, self.symbol), timeout=self._sync_timeout)
            self._update_order_cache(order)
            return self._orders_cache.get(order_id)
        except Exception as err:
            self._log_warning("get_order failed order_id=%s error=%s", order_id, err)
            return None

    def get_open_orders(self) -> List[ExchangeOrder]:
        """获取未完成订单"""
        # 从缓存获取
        open_orders = [
            o for o in self._orders_cache.values()
            if o.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
        ]
        if open_orders:
            self._log_debug("get_open_orders from cache count=%s", len(open_orders))
            return open_orders
        # 回退到REST
        started_at = time.time()
        try:
            self._log_debug("get_open_orders from rest")
            orders = self._run_sync(self._exchange.fetch_open_orders(self.symbol), timeout=self._sync_timeout)
            for order in orders:
                self._update_order_cache(order)
            results = [
                o for o in self._orders_cache.values()
                if o.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
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
        balance = self._run_sync(self._exchange.fetch_balance(), timeout=self._sync_timeout)
        free_balance = balance.get(asset, {}).get('free', 0)
        self._log_debug("get_balance asset=%s free=%s", asset, free_balance)
        return free_balance
