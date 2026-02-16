"""基于 Polymarket 原生 WebSocket 的数据流管理器

参考 v1 NativePolymarketSpot 的 WebSocket 逻辑:
- market WS: wss://ws-subscriptions-clob.polymarket.com/ws/market (价格)
- user WS: wss://ws-subscriptions-clob.polymarket.com/ws/user (订单)
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

import websocket

from worker.core.base_exchange import ExchangeOrder, OrderStatus
from worker.core.log_utils import make_log_prefix
from worker.exchanges.stream.base import StreamManager

logger = logging.getLogger(__name__)

_WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
PRICE_MAX_AGE_SECONDS = 5.0
MAX_ORDER_CACHE_SIZE = 1000
ERROR_LOG_INTERVAL = 2.0
_PING_INTERVAL = 10
_RECONNECT_BASE_DELAY = 2.0
_RECONNECT_MAX_DELAY = 30.0
_STATS_LOG_INTERVAL = 30.0

# 共享池 key: (api_key, api_secret)
SharedKey = Tuple[str, str]


class PolymarketStreamManager(StreamManager):
    """Polymarket 原生 WebSocket 数据流管理器

    特性:
    - 同一 (api_key, api_secret) 共享一个实例
    - 通过 acquire/release 管理引用计数
    - 双 WS 连接: market (价格) + user (订单)
    - 支持动态 subscribe/unsubscribe token_id
    - 线程安全的缓存读写
    - 自动重连 (websocket-client 内置)
    """

    _pool_lock = threading.Lock()
    _pool: Dict[SharedKey, "PolymarketStreamManager"] = {}

    # ==================== acquire / release ====================

    @classmethod
    def acquire(
        cls,
        api_key: str,
        api_secret: str,
        api_creds: Any,
    ) -> "PolymarketStreamManager":
        """获取或创建共享实例，ref_count+1"""
        key: SharedKey = (api_key, api_secret)
        with cls._pool_lock:
            instance = cls._pool.get(key)
            if instance is not None and instance._running:
                instance._ref_count += 1
                logger.debug(
                    "%s reuse stream, ref_count=%d",
                    instance._log_prefix, instance._ref_count,
                )
                return instance

            if instance is not None:
                logger.warning(
                    "%s replacing stale stream instance", instance._log_prefix,
                )
                cls._pool.pop(key, None)

            instance = cls(key=key, api_creds=api_creds)
            instance._ref_count = 1
            cls._pool[key] = instance
            instance._running = True
            instance._start_ws_threads()
            logger.info(
                "%s created stream, ref_count=1", instance._log_prefix,
            )
            return instance

    @classmethod
    def release(cls, instance: "PolymarketStreamManager") -> None:
        """ref_count-1，归零则销毁"""
        with cls._pool_lock:
            instance._ref_count -= 1
            remaining = instance._ref_count
            logger.debug(
                "%s release stream, ref_count=%d",
                instance._log_prefix, remaining,
            )
            if remaining > 0:
                return
            cls._pool.pop(instance._key, None)

        instance.shutdown()

    # ==================== 初始化 ====================

    def __init__(self, key: SharedKey, api_creds: Any) -> None:
        self._key = key
        self._api_key = key[0]
        self._api_creds = api_creds
        self._ref_count = 0

        api_key_prefix = (self._api_key or "")[:8]
        self._log_prefix = f"[{api_key_prefix}] [polymarket]"

        # 缓存: symbol -> (price, timestamp)
        self._prices: Dict[str, tuple[float, float]] = {}
        # 缓存: symbol -> (best_bid, best_ask, timestamp)
        self._best_quotes: Dict[str, tuple[float, float, float]] = {}
        # 缓存: order_id -> ExchangeOrder
        self._orders: Dict[str, ExchangeOrder] = {}
        self._lock = threading.Lock()

        # 订阅的 token_id 集合 (symbol 在 polymarket 中就是 token_id)
        self._subscribed_symbols: Set[str] = set()
        # symbol(token_id) -> display_symbol 的映射 (如 "btc-Up")
        self._symbol_display_map: Dict[str, str] = {}

        # 已处理的成交订单 (去重, FIFO 淘汰)
        self._filled_order_ids: OrderedDict[str, None] = OrderedDict()

        # WS 连接
        self._ws_market: Optional[websocket.WebSocketApp] = None
        self._ws_user: Optional[websocket.WebSocketApp] = None
        self._ws_market_thread: Optional[threading.Thread] = None
        self._ws_user_thread: Optional[threading.Thread] = None
        self._ws_stats_thread: Optional[threading.Thread] = None
        self._ws_market_connected = False
        self._ws_user_connected = False
        self._running = False

        # 统计
        self._stats_price_updates = 0
        self._stats_order_msgs = 0
        self._stats_market_msgs = 0
        self._stats_market_unrecognized = 0
        self._stats_started_at = time.time()
        self._first_unrecognized_msg: Optional[str] = None

        # 错误日志限流
        self._error_log_cache: Dict[str, float] = {}

    # ==================== StreamManager 接口 ====================

    def start(self, symbol: str) -> None:
        with self._lock:
            is_new = symbol not in self._subscribed_symbols
            self._subscribed_symbols.add(symbol)

        if is_new and self._ws_market_connected:
            self._subscribe_market_tokens([symbol])

        logger.info("%s subscribed token_id=%s", self._log_prefix, symbol[:16])

    def stop(self, symbol: str) -> None:
        with self._lock:
            was_present = symbol in self._subscribed_symbols
            self._subscribed_symbols.discard(symbol)
            self._symbol_display_map.pop(symbol, None)
            self._prices.pop(symbol, None)
            self._best_quotes.pop(symbol, None)

        if was_present and self._ws_market_connected:
            self._unsubscribe_market_tokens([symbol])

        logger.debug("%s unsubscribed token_id=%s", self._log_prefix, symbol[:16])

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
        with self._lock:
            return [
                o
                for o in self._orders.values()
                if o.extra.get("token_id") == symbol
                and o.status in (OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED)
            ]

    def get_top_of_book(self, symbol: str) -> Optional[tuple[float, float]]:
        with self._lock:
            entry = self._best_quotes.get(symbol)
            if entry is None:
                return None
            bid, ask, ts = entry
            if time.time() - ts > PRICE_MAX_AGE_SECONDS:
                return None
            return bid, ask

    def has_fresh_price_since(self, symbol: str, since_ts: float) -> bool:
        with self._lock:
            entry = self._prices.get(symbol)
            if entry is None:
                return False
            _, updated_at = entry
        return updated_at >= since_ts and (time.time() - updated_at) <= PRICE_MAX_AGE_SECONDS

    # ==================== 扩展方法 ====================

    def set_display_symbol(self, token_id: str, display_symbol: str) -> None:
        """设置 token_id 到 display_symbol 的映射"""
        with self._lock:
            self._symbol_display_map[token_id] = display_symbol

    def clear_orders_for_token(self, token_id: str) -> None:
        """清空指定 token_id 的订单缓存 (市场切换时调用)"""
        with self._lock:
            to_remove = [
                oid for oid, o in self._orders.items()
                if o.extra.get("token_id") == token_id
            ]
            for oid in to_remove:
                self._orders.pop(oid, None)

    def shutdown(self) -> None:
        """关闭所有 WS 连接"""
        logger.info("%s shutting down stream", self._log_prefix)
        self._running = False
        if self._ws_market:
            self._ws_market.close()
        if self._ws_user:
            self._ws_user.close()
        for t in (self._ws_market_thread, self._ws_user_thread, self._ws_stats_thread):
            if t and t.is_alive():
                t.join(timeout=3.0)
        logger.info("%s stream shut down", self._log_prefix)

    def _subscribe_market_tokens(self, token_ids: List[str]) -> None:
        """动态订阅 market WS token_ids"""
        try:
            if self._ws_market:
                self._ws_market.send(json.dumps({
                    "assets_ids": token_ids, "type": "market",
                }))
                # 同时发送 operation:subscribe 格式以兼容动态订阅
                self._ws_market.send(json.dumps({
                    "assets_ids": token_ids, "operation": "subscribe",
                }))
        except Exception as err:
            self._log_error_throttled(
                "market_subscribe", "market subscribe error: %s", err,
            )

    def _unsubscribe_market_tokens(self, token_ids: List[str]) -> None:
        """动态取消订阅 market WS token_ids"""
        try:
            if self._ws_market:
                self._ws_market.send(json.dumps({
                    "assets_ids": token_ids, "operation": "unsubscribe",
                }))
        except Exception as err:
            self._log_error_throttled(
                "market_unsubscribe", "market unsubscribe error: %s", err,
            )

    # ==================== WS 线程管理 ====================

    def _start_ws_threads(self) -> None:
        self._ws_market_thread = threading.Thread(
            target=self._run_market_ws, daemon=True,
            name=f"PM-Market-{self._api_key[:8]}",
        )
        self._ws_user_thread = threading.Thread(
            target=self._run_user_ws, daemon=True,
            name=f"PM-User-{self._api_key[:8]}",
        )
        self._ws_stats_thread = threading.Thread(
            target=self._log_stats_loop, daemon=True,
            name=f"PM-Stats-{self._api_key[:8]}",
        )
        self._ws_market_thread.start()
        self._ws_user_thread.start()
        self._ws_stats_thread.start()
        logger.info("%s WS threads started", self._log_prefix)

    # ==================== Market WS (价格) ====================

    def _run_market_ws(self) -> None:
        delay = _RECONNECT_BASE_DELAY
        while self._running:
            try:
                self._ws_market = websocket.WebSocketApp(
                    _WS_MARKET_URL,
                    on_message=self._on_market_message,
                    on_error=self._on_market_error,
                    on_close=self._on_market_close,
                    on_open=self._on_market_open,
                )
                self._ws_market.run_forever(reconnect=3)
                delay = _RECONNECT_BASE_DELAY  # 正常断开重置退避
            except Exception as err:
                self._log_error_throttled(
                    "market_ws_run", "market WS run error: %s", err,
                )
            if self._running:
                time.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    def _on_market_open(self, ws: Any) -> None:
        self._ws_market_connected = True
        logger.info("%s market WS connected", self._log_prefix)
        with self._lock:
            token_ids = list(self._subscribed_symbols)
        if token_ids:
            msg = {"assets_ids": token_ids, "type": "market"}
            ws.send(json.dumps(msg))
            logger.info(
                "%s market WS subscribed %d tokens: %s",
                self._log_prefix, len(token_ids),
                [t[:16] for t in token_ids],
            )
        else:
            logger.info(
                "%s market WS connected but no tokens to subscribe yet",
                self._log_prefix,
            )
        # 重置首条未识别消息，便于重连后重新捕获
        self._first_unrecognized_msg = None
        threading.Thread(
            target=self._ping_ws, args=(ws, "market"), daemon=True,
        ).start()

    def _on_market_message(self, ws: Any, message: str) -> None:
        if message == "PONG":
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        self._stats_market_msgs += 1

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process_market_data(item)
        elif isinstance(data, dict):
            self._process_market_data(data)

    def _process_market_data(self, data: Dict[str, Any]) -> None:
        changes = self._extract_market_price_changes(data)
        if not changes:
            self._stats_market_unrecognized += 1
            if self._first_unrecognized_msg is None:
                snippet = json.dumps(data, ensure_ascii=False)[:300]
                self._first_unrecognized_msg = snippet
                logger.warning(
                    "%s market msg unrecognized (first): %s",
                    self._log_prefix, snippet,
                )
            return

        for change in changes:
            raw_asset_id = change.get("asset_id") or change.get("assetId")
            asset_id = str(raw_asset_id or "").strip()
            if not asset_id:
                continue

            with self._lock:
                if asset_id not in self._subscribed_symbols:
                    continue

            best_bid = _safe_float(
                change.get("best_bid") or change.get("bestBid") or change.get("bid")
            )
            best_ask = _safe_float(
                change.get("best_ask") or change.get("bestAsk") or change.get("ask")
            )
            now_ts = time.time()

            with self._lock:
                self._best_quotes[asset_id] = (best_bid, best_ask, now_ts)

            if best_bid > 0 and best_ask > 0:
                mid_price = (best_bid + best_ask) / 2
            elif best_bid > 0:
                mid_price = best_bid
            elif best_ask > 0:
                mid_price = best_ask
            else:
                mid_price = _safe_float(
                    change.get("mid")
                    or change.get("price")
                    or change.get("last_price")
                )

            if mid_price > 0:
                self._stats_price_updates += 1
                with self._lock:
                    self._prices[asset_id] = (mid_price, now_ts)

    @staticmethod
    def _extract_market_price_changes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        event_type = str(data.get("event_type") or "").strip().lower()

        # 标准新格式: event_type="price_change" + price_changes 列表
        if event_type == "price_change":
            price_changes = data.get("price_changes")
            if isinstance(price_changes, list) and price_changes:
                return [item for item in price_changes if isinstance(item, dict)]

            # 兼容旧格式: event_type="price_change" + changes 列表 + 根级 asset_id
            changes = data.get("changes")
            if isinstance(changes, list) and changes:
                root_asset_id = data.get("asset_id") or data.get("assetId")
                result = []
                for item in changes:
                    if not isinstance(item, dict):
                        continue
                    # 旧格式 changes 中没有 asset_id，从根级注入
                    if root_asset_id and "asset_id" not in item and "assetId" not in item:
                        item = {**item, "asset_id": root_asset_id}
                    result.append(item)
                return result

            return []

        # 兼容: 无 event_type 但有 price_changes 列表
        if isinstance(data.get("price_changes"), list):
            return [
                item
                for item in data.get("price_changes", [])
                if isinstance(item, dict)
            ]

        # 兼容: 无 event_type 但有 changes 列表
        if isinstance(data.get("changes"), list):
            root_asset_id = data.get("asset_id") or data.get("assetId")
            result = []
            for item in data.get("changes", []):
                if not isinstance(item, dict):
                    continue
                if root_asset_id and "asset_id" not in item and "assetId" not in item:
                    item = {**item, "asset_id": root_asset_id}
                result.append(item)
            return result

        # 单条价格数据: 直接包含 asset_id/assetId
        if "asset_id" in data or "assetId" in data:
            return [data]

        return []

    def _on_market_error(self, ws: Any, error: Any) -> None:
        self._ws_market_connected = False
        self._log_error_throttled("market_ws_error", "market WS error: %s", error)

    def _on_market_close(self, ws: Any, close_status_code: Any, close_msg: Any) -> None:
        self._ws_market_connected = False
        logger.debug("%s market WS closed code=%s", self._log_prefix, close_status_code)

    # ==================== User WS (订单) ====================

    def _run_user_ws(self) -> None:
        delay = _RECONNECT_BASE_DELAY
        while self._running:
            try:
                self._ws_user = websocket.WebSocketApp(
                    _WS_USER_URL,
                    on_message=self._on_user_message,
                    on_error=self._on_user_error,
                    on_close=self._on_user_close,
                    on_open=self._on_user_open,
                )
                self._ws_user.run_forever(reconnect=3)
                delay = _RECONNECT_BASE_DELAY
            except Exception as err:
                self._log_error_throttled(
                    "user_ws_run", "user WS run error: %s", err,
                )
            if self._running:
                time.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    def _on_user_open(self, ws: Any) -> None:
        self._ws_user_connected = True
        logger.info("%s user WS connected", self._log_prefix)
        if not self._api_creds:
            logger.warning("%s user WS: no api_creds, skip auth", self._log_prefix)
            return

        # 传空 markets 列表，接收该账户所有订单事件，由消息处理器按 token_id 过滤
        msg = {
            "auth": {
                "apiKey": self._api_creds.api_key,
                "secret": self._api_creds.api_secret,
                "passphrase": self._api_creds.api_passphrase,
            },
            "markets": [],
            "type": "user",
        }
        ws.send(json.dumps(msg))
        logger.debug("%s user WS authenticated", self._log_prefix)
        threading.Thread(
            target=self._ping_ws, args=(ws, "user"), daemon=True,
        ).start()

    def _on_user_message(self, ws: Any, message: str) -> None:
        if message == "PONG":
            return
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        event_type = data.get("event_type")
        if event_type == "order":
            self._process_order_event(data)
        elif event_type == "trade":
            self._process_trade_event(data)

    def _process_order_event(self, data: Dict[str, Any]) -> None:
        """处理订单事件 (参考 v1 _process_order_event)"""
        order_type = data.get("type")
        order_id = data.get("id")
        asset_id = str(data.get("asset_id", ""))

        with self._lock:
            if asset_id not in self._subscribed_symbols:
                return
            display_symbol = self._symbol_display_map.get(asset_id, asset_id)

        side = data.get("side", "").lower()
        price = _safe_float(data.get("price"))
        original_size = _safe_float(data.get("original_size"))
        size_matched = _safe_float(data.get("size_matched"))

        if order_type == "CANCELLATION":
            self._stats_order_msgs += 1
            order = ExchangeOrder(
                order_id=order_id, symbol=display_symbol, side=side,
                price=price, quantity=original_size,
                filled_quantity=size_matched, status=OrderStatus.CANCELLED,
                extra={"token_id": asset_id, "raw_order": data},
            )
            with self._lock:
                self._orders[order_id] = order
            logger.info("%s order_cancelled id=%s", self._log_prefix, order_id)

        elif order_type == "PLACEMENT":
            self._stats_order_msgs += 1
            order = ExchangeOrder(
                order_id=order_id, symbol=display_symbol, side=side,
                price=price, quantity=original_size,
                filled_quantity=0, status=OrderStatus.PLACED,
                extra={"token_id": asset_id, "raw_order": data},
            )
            with self._lock:
                self._orders[order_id] = order

        elif order_type == "UPDATE":
            self._stats_order_msgs += 1
            if original_size > 0 and size_matched >= original_size:
                if self._is_already_filled(order_id):
                    return
                status = OrderStatus.FILLED
                logger.info(
                    "%s order_filled(UPDATE) id=%s side=%s price=%s qty=%s",
                    self._log_prefix, order_id, side, price, size_matched,
                )
            elif size_matched > 0:
                status = OrderStatus.PARTIALLY_FILLED
            else:
                status = OrderStatus.PLACED

            order = ExchangeOrder(
                order_id=order_id, symbol=display_symbol, side=side,
                price=price, quantity=original_size,
                filled_quantity=size_matched, status=status,
                extra={"token_id": asset_id, "raw_order": data},
            )
            with self._lock:
                self._orders[order_id] = order
                self._cleanup_old_orders()

    def _process_trade_event(self, data: Dict[str, Any]) -> None:
        """处理交易事件 (参考 v1 _process_trade_event)"""
        status = data.get("status")
        if status != "MATCHED":
            return

        asset_id = str(data.get("asset_id", ""))
        with self._lock:
            if asset_id and asset_id not in self._subscribed_symbols:
                return
            display_symbol = self._symbol_display_map.get(asset_id, asset_id)

        trader_side = data.get("trader_side")
        side = data.get("side", "").lower()
        price = _safe_float(data.get("price"))
        size = _safe_float(data.get("size"))

        if trader_side == "MAKER":
            my_address = self._api_key.lower()
            for maker_order in data.get("maker_orders", []):
                if maker_order.get("maker_address", "").lower() != my_address:
                    continue
                order_id = maker_order.get("order_id")
                if not order_id or self._is_already_filled(order_id):
                    continue

                matched = _safe_float(maker_order.get("matched_amount"))
                order_side = maker_order.get("side", "").lower()
                order_price = _safe_float(maker_order.get("price"))
                self._stats_order_msgs += 1

                order = ExchangeOrder(
                    order_id=order_id, symbol=display_symbol, side=order_side,
                    price=order_price, quantity=matched,
                    filled_quantity=matched, status=OrderStatus.FILLED,
                    extra={"token_id": asset_id, "raw_order": maker_order},
                )
                with self._lock:
                    self._orders[order_id] = order
                    self._cleanup_old_orders()
                logger.info(
                    "%s order_filled(MAKER) id=%s side=%s price=%s qty=%s",
                    self._log_prefix, order_id[:16], order_side, order_price, matched,
                )
        else:
            # TAKER
            taker_order_id = data.get("taker_order_id")
            if not taker_order_id or self._is_already_filled(taker_order_id):
                return
            self._stats_order_msgs += 1

            order = ExchangeOrder(
                order_id=taker_order_id, symbol=display_symbol, side=side,
                price=price, quantity=size,
                filled_quantity=size, status=OrderStatus.FILLED,
                extra={"token_id": asset_id, "raw_order": data},
            )
            with self._lock:
                self._orders[taker_order_id] = order
                self._cleanup_old_orders()
            logger.info(
                "%s order_filled(TAKER) id=%s side=%s price=%s qty=%s",
                self._log_prefix, taker_order_id[:16], side, price, size,
            )

    def _on_user_error(self, ws: Any, error: Any) -> None:
        self._ws_user_connected = False
        self._log_error_throttled("user_ws_error", "user WS error: %s", error)

    def _on_user_close(self, ws: Any, close_status_code: Any, close_msg: Any) -> None:
        self._ws_user_connected = False
        logger.debug("%s user WS closed code=%s", self._log_prefix, close_status_code)

    # ==================== 工具方法 ====================

    def _is_already_filled(self, order_id: str) -> bool:
        with self._lock:
            if order_id in self._filled_order_ids:
                return True
            self._filled_order_ids[order_id] = None
            # FIFO 淘汰: 超限时删除最早插入的一半
            if len(self._filled_order_ids) > MAX_ORDER_CACHE_SIZE:
                for _ in range(MAX_ORDER_CACHE_SIZE // 2):
                    self._filled_order_ids.popitem(last=False)
            return False

    def _cleanup_old_orders(self) -> None:
        """清理旧订单 (必须持有 _lock)"""
        if len(self._orders) <= MAX_ORDER_CACHE_SIZE:
            return
        completed = [
            (oid, o) for oid, o in self._orders.items()
            if o.status in (OrderStatus.FILLED, OrderStatus.CANCELLED)
        ]
        if len(completed) > MAX_ORDER_CACHE_SIZE // 2:
            for oid, _ in completed[: len(completed) // 2]:
                self._orders.pop(oid, None)

    def _ping_ws(self, ws: Any, name: str) -> None:
        """手动发送文本 PING 保活 (Polymarket 需要文本 PING 而非协议级 ping)"""
        while self._running:
            try:
                ws.send("PING")
            except Exception:
                break
            time.sleep(_PING_INTERVAL)

    def _log_stats_loop(self) -> None:
        """定期输出缓存统计"""
        while self._running:
            time.sleep(_STATS_LOG_INTERVAL)
            if not self._running:
                break

            with self._lock:
                symbols = list(self._subscribed_symbols)
                all_orders = list(self._orders.values())
                price_count = len(self._prices)
                filled_ids_count = len(self._filled_order_ids)

            elapsed = max(time.time() - self._stats_started_at, 1e-9)

            for symbol in symbols:
                sym_orders = [o for o in all_orders if o.extra.get("token_id") == symbol]
                active = sum(1 for o in sym_orders if o.status == OrderStatus.PLACED)
                filled = sum(1 for o in sym_orders if o.status == OrderStatus.FILLED)
                display = self._symbol_display_map.get(symbol, symbol[:16])
                logger.info(
                    "%s [%s] stream_stats prices=%d "
                    "orders=%d active=%d filled=%d filled_ids=%d "
                    "price_updates=%d(%.1f/s) order_msgs=%d(%.1f/s) "
                    "market_msgs=%d unrecognized=%d "
                    "market_ws=%s user_ws=%s ref_count=%d",
                    self._log_prefix, display, price_count,
                    len(sym_orders), active, filled, filled_ids_count,
                    self._stats_price_updates,
                    self._stats_price_updates / elapsed,
                    self._stats_order_msgs,
                    self._stats_order_msgs / elapsed,
                    self._stats_market_msgs,
                    self._stats_market_unrecognized,
                    "up" if self._ws_market_connected else "down",
                    "up" if self._ws_user_connected else "down",
                    self._ref_count,
                )

    def _log_error_throttled(self, key: str, msg: str, *args: object) -> None:
        now = time.time()
        if now - self._error_log_cache.get(key, 0.0) < ERROR_LOG_INTERVAL:
            return
        self._error_log_cache[key] = now
        if len(self._error_log_cache) > 100:
            cutoff = now - ERROR_LOG_INTERVAL * 10
            self._error_log_cache = {
                k: v for k, v in self._error_log_cache.items() if v > cutoff
            }
        logger.warning(f"{self._log_prefix} " + msg, *args)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
