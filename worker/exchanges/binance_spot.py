"""币安现货交易所实现 - 使用CCXT Pro"""

import asyncio
import threading
from typing import Dict, List, Optional
import ccxt.pro as ccxtpro

from worker.base_exchange import (
    BaseExchange, OrderResult, OrderStatus, ExchangeOrder, TradingRules
)


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

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {'id': 'binance_spot', 'name': 'Binance Spot', 'type': 'spot'}

    def start_ws(self) -> None:
        """启动WebSocket线程"""
        if self._ws_running:
            return
        self._ws_running = True
        self._ws_thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self._ws_thread.start()

    def stop_ws(self) -> None:
        """停止WebSocket"""
        self._ws_running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_ws_loop(self) -> None:
        """WebSocket事件循环"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_main())
        except Exception:
            pass
        finally:
            self._loop.close()

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
            except Exception:
                await asyncio.sleep(1)

    async def _watch_orders(self) -> None:
        """监听订单更新"""
        while self._ws_running:
            try:
                orders = await self._exchange.watch_orders(self.symbol)
                for order in orders:
                    self._update_order_cache(order)
            except Exception:
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

    def get_trading_rules(self) -> TradingRules:
        """获取交易规则"""
        if self._trading_rules:
            return self._trading_rules

        self._exchange.load_markets()
        market = self._exchange.market(self.symbol)

        precision = market.get('precision', {})
        limits = market.get('limits', {})

        price_decimals = precision.get('price', 8)
        qty_decimals = precision.get('amount', 8)
        tick_size = 10 ** -price_decimals
        step_size = 10 ** -qty_decimals
        min_notional = limits.get('cost', {}).get('min', 0) or 0

        self._trading_rules = TradingRules(
            tick_size=tick_size,
            price_decimals=price_decimals,
            step_size=step_size,
            qty_decimals=qty_decimals,
            min_notional=min_notional,
        )
        return self._trading_rules

    def get_fee_rate(self) -> float:
        return 0.001  # 0.1%

    def get_ticker_price(self) -> float:
        """获取当前价格 - 优先使用WS缓存"""
        if self._current_price:
            return self._current_price
        ticker = self._exchange.fetch_ticker(self.symbol)
        return ticker['last']

    def place_batch_orders(self, orders: List[Dict]) -> List[OrderResult]:
        """批量下单 - 使用币安batchOrders API（最多5个）"""
        if not orders:
            return []

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
                resp = self._exchange.private_post_batch_orders({
                    'batchOrders': self._exchange.json(batch_params)
                })
                for item in resp:
                    if 'orderId' in item:
                        results.append(OrderResult(
                            success=True,
                            order_id=str(item['orderId']),
                            status=OrderStatus.PLACED,
                        ))
                    else:
                        results.append(OrderResult(
                            success=False,
                            order_id=None,
                            status=OrderStatus.FAILED,
                            error=item.get('msg', 'Unknown error'),
                        ))
            except Exception as e:
                results.extend([
                    OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=str(e))
                    for _ in batch
                ])
        return results

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        """批量取消订单"""
        if not order_ids:
            return []

        try:
            self._exchange.cancel_orders(order_ids, self.symbol)
            results = []
            for oid in order_ids:
                if oid in self._orders_cache:
                    self._orders_cache[oid].status = OrderStatus.CANCELLED
                results.append(OrderResult(
                    success=True,
                    order_id=oid,
                    status=OrderStatus.CANCELLED,
                ))
            return results
        except Exception as e:
            return [
                OrderResult(success=False, order_id=oid, status=OrderStatus.FAILED, error=str(e))
                for oid in order_ids
            ]

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        """查询订单 - 优先使用WS缓存"""
        if order_id in self._orders_cache:
            return self._orders_cache[order_id]
        # 回退到REST
        try:
            order = self._exchange.fetch_order(order_id, self.symbol)
            self._update_order_cache(order)
            return self._orders_cache.get(order_id)
        except Exception:
            return None

    def get_open_orders(self) -> List[ExchangeOrder]:
        """获取未完成订单"""
        # 从缓存获取
        open_orders = [
            o for o in self._orders_cache.values()
            if o.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
        ]
        if open_orders:
            return open_orders
        # 回退到REST
        try:
            orders = self._exchange.fetch_open_orders(self.symbol)
            for order in orders:
                self._update_order_cache(order)
            return [
                o for o in self._orders_cache.values()
                if o.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
            ]
        except Exception:
            return []

    def get_balance(self, asset: str) -> float:
        """获取资产余额"""
        balance = self._exchange.fetch_balance()
        return balance.get(asset, {}).get('free', 0)
