"""Polymarket UpDown 15m exchange adapter.

参考v1实现，通过slug直接查询gamma API获取市场token。
"""

from __future__ import annotations

import json
import logging
import string
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pytz
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OpenOrderParams, OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY, SELL

from worker.core.base_exchange import (
    BaseExchange,
    ExchangeOrder,
    OrderRequest,
    OrderResult,
    OrderStatus,
    TradingRules,
)
from worker.exchanges.stream.base import StreamManager
from worker.exchanges.stream.polymarket_stream import PolymarketStreamManager

logger = logging.getLogger(__name__)

_ET_TZ = pytz.timezone("America/New_York")
_GAMMA_API_BASE = "https://gamma-api.polymarket.com"
_CLOB_HOST = "https://clob.polymarket.com"
_CHAIN_ID = 137
_MARKET_PERIOD_SECONDS = 15 * 60
_BALANCE_SCALE = 1_000_000
_DEFAULT_MARKET_CLOSE_BUFFER = 180
_PRE_SWITCH_GUARD_SECONDS = 10.0
_POST_SWITCH_GUARD_SECONDS = 10.0
_MAX_EFFECTIVE_TAKER_FEE_RATE = 0.016
_SELL_FEE_GAP_TOLERANCE = 0.002
_SELL_BALANCE_BUFFER = 0.0001
_MIN_POLY_PRICE = 0.01
_MAX_POLY_PRICE = 0.99
_TRANSIENT_ORDER_ERROR_MARKERS = (
    "market is closing",
    "market switched, waiting for fresh quotes",
    "waiting for fresh quotes",
)


class PolymarketUpDown15m(BaseExchange):
    """Polymarket 15m UpDown market exchange implementation."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
        market_close_buffer: Optional[int] = None,
    ):
        super().__init__(api_key, api_secret, symbol, testnet)

        self._market_prefix, self._outcome = self._parse_symbol(symbol)
        if market_close_buffer is None:
            self._market_close_buffer = _DEFAULT_MARKET_CLOSE_BUFFER
        else:
            self._market_close_buffer = max(int(market_close_buffer), 0)

        # 市场状态
        self._market_slug: Optional[str] = None
        self._token_id: Optional[str] = None
        self._market_end_time: Optional[int] = None  # unix timestamp
        self._condition_id: Optional[str] = None

        # 并发控制
        self._orders_lock = threading.Lock()
        self._orders_cache: Dict[str, ExchangeOrder] = {}
        self._market_lock = threading.Lock()
        self._is_closing = False
        self._last_market_switch_ts: float = 0.0
        self.on_market_switch: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None

        self._trading_rules = TradingRules(
            tick_size=0.01,
            price_decimals=2,
            step_size=0.0001,
            qty_decimals=4,
            min_notional=0,
        )

        # 初始化 ClobClient
        private_key = _normalize_polymarket_private_key(api_secret)
        funder = (api_key or "").strip()
        if not funder:
            raise ValueError("polymarket funder address is required")

        self._client = ClobClient(
            host=_CLOB_HOST,
            key=private_key,
            chain_id=_CHAIN_ID,
            signature_type=2,
            funder=funder,
        )
        try:
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            self._api_creds = creds
        except Exception as e:
            logger.warning("%s API credentials setup failed: %s", self.log_prefix, e)
            self._api_creds = None

        # 初始化 StreamManager
        self._stream: Optional[StreamManager] = None

        # 获取初始市场
        self._refresh_market()

    # ── BaseExchange 接口实现 ──────────────────────────────────────

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {
            "id": "polymarket_updown15m",
            "name": "Polymarket UpDown 15m",
            "type": "prediction",
        }

    def get_trading_rules(self) -> TradingRules:
        return self._trading_rules

    def get_fee_rate(self) -> float:
        return 0.0

    def get_status_extra(self) -> Dict[str, Any]:
        seconds_until_close = self._seconds_until_close()
        return {
            "market_slug": self._market_slug,
            "token_id": self._token_id,
            "market_end_time": self._market_end_time,
            "seconds_until_close": seconds_until_close,
            "market_close_buffer": self._market_close_buffer,
            "seconds_until_switch": max(0, seconds_until_close - self._market_close_buffer),
            "is_closing": self._is_closing,
            "switch_guard_active": not self._is_switch_guard_passed(),
            "condition_id": self._condition_id,
            "ws_enabled": self._stream is not None,
        }

    def get_ticker_price(self) -> float:
        self._ensure_market_valid()
        if not self._token_id:
            raise RuntimeError("token_id is not initialized")

        # 缓存优先
        if self._stream is not None:
            price = self._stream.get_price(self._token_id)
            if price is not None:
                return price

        # REST 兜底
        midpoint_data = self._client.get_midpoint(self._token_id)
        price = float(midpoint_data.get("mid", 0))
        if price <= 0:
            raise RuntimeError("Polymarket ticker price is invalid")
        return price

    def place_batch_orders(self, orders: List[OrderRequest]) -> List[OrderResult]:
        if not orders:
            return []

        try:
            self._ensure_market_valid()
        except Exception as err:
            error_text = str(err)
            suppress_notify = self._should_suppress_order_notify(error_text)
            log_fn = logger.info if suppress_notify else logger.warning
            log_fn("%s place orders precheck failed: %s", self.log_prefix, error_text)
            return [
                OrderResult(
                    success=False,
                    order_id=None,
                    status=OrderStatus.FAILED,
                    error=error_text,
                    suppress_notify=suppress_notify,
                )
                for _ in orders
            ]

        if not self._is_market_tradeable():
            return [
                OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="market is closing", suppress_notify=True)
                for _ in orders
            ]
        if not self._is_switch_guard_passed():
            return [
                OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="market switched, waiting for fresh quotes", suppress_notify=True)
                for _ in orders
            ]

        results: List[OrderResult] = [None] * len(orders)
        batch_args: List[PostOrdersArgs] = []
        batch_indices: List[int] = []
        batch_meta: List[tuple] = []  # (side, price, quantity)

        for i, order in enumerate(orders):
            side = order.side.upper().strip()
            requested_price = order.price
            requested_quantity = order.quantity

            if side not in {"BUY", "SELL"}:
                results[i] = OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=f"unsupported side: {side}")
                continue
            if requested_price <= 0 or requested_quantity <= 0:
                results[i] = OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="price and quantity must be positive")
                continue

            try:
                price = self._guard_maker_price(side=side, requested_price=requested_price)
                quantity = requested_quantity
                if side == "SELL":
                    quantity = self._resolve_sell_quantity(requested_quantity=quantity)
                signed = self._create_signed_order(side, price, quantity)
                batch_args.append(PostOrdersArgs(order=signed, orderType=OrderType.GTC))
                batch_indices.append(i)
                batch_meta.append((side, price, quantity))
            except Exception as e:
                error_text = str(e)
                suppress_notify = self._should_suppress_order_notify(error_text)
                log_fn = logger.info if suppress_notify else logger.warning
                log_fn("%s place order pre-sign failed: %s", self.log_prefix, error_text)
                results[i] = OrderResult(
                    success=False, order_id=None, status=OrderStatus.FAILED,
                    error=error_text, suppress_notify=suppress_notify,
                )

        if not batch_args:
            return [r for r in results if r is not None]

        try:
            resp = self._client.post_orders(batch_args)
            order_results = self._parse_batch_order_response(resp, batch_meta)
        except Exception as e:
            error_text = str(e)
            suppress_notify = self._should_suppress_order_notify(error_text)
            log_fn = logger.info if suppress_notify else logger.warning
            log_fn("%s post_orders batch failed: %s", self.log_prefix, error_text)
            order_results = [
                OrderResult(
                    success=False, order_id=None, status=OrderStatus.FAILED,
                    error=error_text, suppress_notify=suppress_notify,
                )
            ] * len(batch_args)

        for j, idx in enumerate(batch_indices):
            result = order_results[j] if j < len(order_results) else OrderResult(
                success=False, order_id=None, status=OrderStatus.FAILED, error="missing response",
            )
            results[idx] = result
            if result.success and result.order_id:
                side, price, quantity = batch_meta[j]
                exchange_order = ExchangeOrder(
                    order_id=result.order_id,
                    symbol=self.symbol,
                    side=side.lower(),
                    price=price,
                    quantity=quantity,
                    status=OrderStatus.PLACED,
                )
                with self._orders_lock:
                    self._orders_cache[exchange_order.order_id] = exchange_order

        return [r for r in results if r is not None]

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        if not order_ids:
            return []
        try:
            self._ensure_market_valid()
        except Exception as err:
            error_text = str(err)
            suppress_notify = self._should_suppress_order_notify(error_text)
            log_fn = logger.info if suppress_notify else logger.warning
            log_fn("%s cancel orders precheck failed: %s", self.log_prefix, error_text)
            return [
                OrderResult(
                    success=False,
                    order_id=order_id,
                    status=OrderStatus.FAILED,
                    error=error_text,
                    suppress_notify=suppress_notify,
                )
                for order_id in order_ids
            ]

        try:
            resp = self._client.cancel_orders(order_ids)
            canceled = set()
            if isinstance(resp, dict):
                canceled = set(resp.get("canceled", []) or [])
            elif isinstance(resp, list):
                canceled = set(resp)

            results: List[OrderResult] = []
            for order_id in order_ids:
                if order_id in canceled or canceled == set():
                    # 无法区分时默认成功
                    with self._orders_lock:
                        if order_id in self._orders_cache:
                            self._orders_cache[order_id].status = OrderStatus.CANCELLED
                    results.append(OrderResult(success=True, order_id=order_id, status=OrderStatus.CANCELLED))
                else:
                    results.append(OrderResult(success=False, order_id=order_id, status=OrderStatus.FAILED, error="not in canceled list"))
            return results
        except Exception as e:
            logger.warning("%s cancel_orders batch failed: %s, falling back to single cancel", self.log_prefix, e)

        # 批量失败时逐个取消
        results = []
        for order_id in order_ids:
            try:
                self._client.cancel(order_id)
                with self._orders_lock:
                    if order_id in self._orders_cache:
                        self._orders_cache[order_id].status = OrderStatus.CANCELLED
                results.append(OrderResult(success=True, order_id=order_id, status=OrderStatus.CANCELLED))
            except Exception as e:
                logger.warning("%s cancel order failed order_id=%s err=%s", self.log_prefix, order_id, e)
                results.append(OrderResult(success=False, order_id=order_id, status=OrderStatus.FAILED, error=str(e)))
        return results

    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        # 缓存优先: 终态订单直接返回
        if self._stream is not None:
            cached = self._stream.get_order(order_id)
            if cached is not None and cached.status in (
                OrderStatus.FILLED, OrderStatus.CANCELLED,
            ):
                return cached

        self._ensure_market_valid()
        try:
            raw = self._client.get_order(order_id)
            if isinstance(raw, dict):
                return self._normalize_order(raw)
        except Exception:
            pass

        # REST 失败时回退到缓存
        if self._stream is not None:
            return self._stream.get_order(order_id)

        with self._orders_lock:
            return self._orders_cache.get(order_id)

    def get_open_orders(self) -> List[ExchangeOrder]:
        self._ensure_market_valid()
        if not self._token_id:
            return []

        # 缓存优先
        if self._stream is not None:
            orders = self._stream.get_open_orders(self._token_id)
            if orders:
                return orders

        # REST 兜底
        try:
            raw_orders = self._client.get_orders(
                params=OpenOrderParams(asset_id=self._token_id),
            )
        except Exception:
            with self._orders_lock:
                return [o for o in self._orders_cache.values() if o.status in {OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED}]

        if not isinstance(raw_orders, list):
            raw_orders = []

        result: List[ExchangeOrder] = []
        for raw in raw_orders:
            if not isinstance(raw, dict):
                continue
            order = self._normalize_order(raw)
            if order is None:
                continue
            # 只保留当前 token_id 的订单
            token_id = (order.extra or {}).get("token_id", "")
            if self._token_id and token_id and token_id != self._token_id:
                continue
            if order.status in {OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED}:
                result.append(order)
                with self._orders_lock:
                    self._orders_cache[order.order_id] = order

        return result

    def close(self) -> None:
        if self._stream is not None:
            if self._token_id:
                self._stream.stop(self._token_id)
            if isinstance(self._stream, PolymarketStreamManager):
                PolymarketStreamManager.release(self._stream)
            self._stream = None

    # ── 市场管理 ──────────────────────────────────────────────────

    @staticmethod
    def _parse_symbol(symbol: str) -> tuple[str, str]:
        """解析 symbol, 格式 'btc-Up' 或 'eth-Down'."""
        parts = symbol.strip().split("-", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid symbol format: {symbol}, expected 'market-outcome'")

        market_prefix = parts[0].lower()
        outcome = parts[1].capitalize()
        if outcome not in {"Up", "Down"}:
            raise ValueError(f"invalid outcome: {outcome}, expected 'Up' or 'Down'")
        return market_prefix, outcome

    def _aligned_timestamp(self, offset: int = 0) -> int:
        """计算第 offset 个15分钟周期的开始时间戳 (ET对齐). 0=当前, 1=下一个."""
        now = datetime.now(_ET_TZ)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        return int((base + timedelta(minutes=15 * offset)).timestamp())

    def _get_market_token_by_timestamp(self, timestamp: int) -> Optional[str]:
        """通过slug查询gamma API获取市场token_id (参考v1实现)."""
        slug = f"{self._market_prefix}-updown-15m-{timestamp}"
        event = _fetch_gamma_event(slug)
        if not event:
            return None

        market = (event.get("markets") or [{}])[0] if isinstance(event, dict) else {}
        if not isinstance(market, dict):
            return None

        self._condition_id = market.get("conditionId") or market.get("condition_id")

        clob_token_ids = _safe_json_list(market.get("clobTokenIds"))
        outcomes = _safe_json_list(market.get("outcomes"))
        token_id = _select_token_id(outcomes, clob_token_ids, self._outcome)
        if not token_id:
            return None

        self._market_slug = slug
        self._market_end_time = timestamp + _MARKET_PERIOD_SECONDS
        return token_id

    def _get_next_market_token(self, max_retries: int = 6) -> Optional[str]:
        """获取下一个市场的token_id, 支持重试."""
        next_ts = self._aligned_timestamp(1)
        for attempt in range(max_retries):
            token_id = self._get_market_token_by_timestamp(next_ts)
            if token_id:
                return token_id
            if attempt < max_retries - 1:
                time.sleep(2)
        return None

    def _refresh_market(self) -> None:
        """刷新到当前或下一个市场."""
        ts = self._aligned_timestamp()
        token_id = self._get_market_token_by_timestamp(ts)
        if not token_id:
            token_id = self._get_next_market_token()
        if not token_id:
            raise RuntimeError(f"failed to resolve market for {self.symbol} at timestamp {ts}")

        if token_id != self._token_id:
            old_token = self._token_id
            logger.info(
                "%s switched market token_id=%s slug=%s end_time=%s",
                self.log_prefix, token_id, self._market_slug, self._market_end_time,
            )

            # 更新 stream 订阅
            self._switch_stream_subscription(old_token, token_id)

        self._token_id = token_id

    def _switch_stream_subscription(self, old_token: Optional[str], new_token: str) -> None:
        """切换 stream 订阅 (市场轮换时调用)"""
        if self._stream is None:
            # 首次: 通过 acquire 获取共享实例
            self._stream = PolymarketStreamManager.acquire(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_creds=self._api_creds,
            )

        # 取消旧 token 订阅
        if old_token:
            self._stream.stop(old_token)

        # 订阅新 token
        if isinstance(self._stream, PolymarketStreamManager):
            self._stream.clear_orders_for_token(old_token or "")
            self._stream.set_display_symbol(new_token, self.symbol)

        self._stream.start(new_token)
        self._last_market_switch_ts = time.time()
        logger.info("%s stream switched to token_id=%s", self.log_prefix, new_token[:16])

    def _ensure_market_valid(self) -> None:
        """确保当前市场有效, 如果即将关闭则处理切换."""
        if not self._token_id:
            self._refresh_market()

        if self._should_rollover_market():
            self._handle_market_closing()

    def _seconds_until_close(self) -> int:
        """距离市场关闭的秒数."""
        if not self._market_end_time:
            return 0
        return max(0, int(self._market_end_time - time.time()))

    def _should_rollover_market(self) -> bool:
        if not self._market_end_time:
            return False
        seconds_left = self._seconds_until_close()
        if seconds_left <= 0:
            return True
        return self._market_close_buffer > 0 and seconds_left <= self._market_close_buffer

    def _should_suppress_order_notify(self, error_message: str) -> bool:
        if not self._is_market_tradeable():
            return True
        if not self._is_switch_guard_passed():
            return True

        normalized_error = (error_message or "").lower().strip()
        return any(marker in normalized_error for marker in _TRANSIENT_ORDER_ERROR_MARKERS)

    def _is_market_tradeable(self) -> bool:
        """市场是否允许继续挂单。

        切市场前 _PRE_SWITCH_GUARD_SECONDS 秒禁止下单，避免刚下就被切换流程取消。
        """
        if not self._market_end_time:
            return True
        seconds_left = self._seconds_until_close()
        if seconds_left <= 0:
            return False
        return seconds_left > max(3, self._market_close_buffer + _PRE_SWITCH_GUARD_SECONDS)

    def _is_switch_guard_passed(self) -> bool:
        """市场切换后短暂等待，避免沿用旧市场价格挂单。"""
        if self._last_market_switch_ts <= 0:
            return True
        elapsed = time.time() - self._last_market_switch_ts
        if elapsed >= _POST_SWITCH_GUARD_SECONDS:
            return True
        if not self._token_id or not isinstance(self._stream, PolymarketStreamManager):
            return False
        return self._stream.has_fresh_price_since(self._token_id, self._last_market_switch_ts)

    def _handle_market_closing(self) -> None:
        """市场即将关闭: 取消买单, 清算持仓, 切换到新市场."""
        with self._market_lock:
            if self._is_closing:
                return
            self._is_closing = True

        try:
            logger.warning(
                "%s market closing soon token_id=%s seconds_left=%s",
                self.log_prefix, self._token_id, self._seconds_until_close(),
            )

            # 取消旧市场所有订单
            self._cancel_all_market_orders()

            # 清算持仓
            liquidation_result = self._liquidate_position()

            # 通知引擎清空交易状态
            if self.on_market_switch:
                self.on_market_switch(liquidation_result)

            # 切换到新市场
            old_token = self._token_id
            new_token = self._get_next_market_token()
            if new_token and new_token != old_token:
                self._token_id = new_token
                with self._orders_lock:
                    self._orders_cache.clear()
                self._switch_stream_subscription(old_token, new_token)
                logger.info("%s switched to new market token_id=%s slug=%s", self.log_prefix, new_token, self._market_slug)
            elif not new_token:
                # 刷新当前市场
                self._refresh_market()
        finally:
            with self._market_lock:
                self._is_closing = False

    def _cancel_all_market_orders(self) -> None:
        """使用 cancel_market_orders 取消旧市场的所有订单."""
        if not self._token_id:
            return
        try:
            resp = self._client.cancel_market_orders(asset_id=self._token_id)
            canceled = resp.get("canceled", []) if isinstance(resp, dict) else []
            logger.info(
                "%s canceled all market orders token_id=%s canceled=%s",
                self.log_prefix, self._token_id[:16], len(canceled),
            )
        except Exception as e:
            logger.warning("%s cancel_market_orders failed: %s", self.log_prefix, e)

    def _liquidate_position(self) -> Optional[Dict[str, Any]]:
        """以IOC市价单清算当前token持仓, 返回清算结果."""
        if not self._token_id:
            return None
        try:
            balance = self._get_token_balance(self._token_id)
            if balance < 1.0:
                return None

            order_args = OrderArgs(
                price=0.01,
                size=balance,
                side=SELL,
                token_id=self._token_id,
            )
            signed = self._client.create_order(order_args)
            raw_response = self._post_order(signed, OrderType.FOK)
            logger.info("%s liquidated position qty=%s", self.log_prefix, balance)
            return {
                "quantity": balance,
                "price": 0.01,
                "raw_response": raw_response,
            }
        except Exception as e:
            logger.warning("%s liquidate position failed: %s", self.log_prefix, e)
            return None

    def _get_token_balance(self, token_id: str) -> float:
        """查询指定token的可用余额."""
        try:
            balance = None

            # py-clob-client 版本差异：有的版本要求 BalanceAllowanceParams。
            try:
                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                )
                balance = self._client.get_balance_allowance(params)
            except Exception:
                balance = self._client.get_balance_allowance(token_id)

            if isinstance(balance, dict):
                raw = balance.get("balance", 0)
            else:
                raw = balance or 0

            value = float(raw)
            # 余额通常是 1e6 精度（参考 v1 实现），做一个保守的缩放。
            if value >= _BALANCE_SCALE:
                return value / _BALANCE_SCALE
            return value
        except Exception:
            return 0.0

    # ── 下单 ──────────────────────────────────────────────────────

    def _guard_maker_price(self, side: str, requested_price: float) -> float:
        """避免价格穿透盘口导致变成 taker。"""
        adjusted_price = self.align_price(
            self._clamp_price(requested_price),
            self._trading_rules,
        )
        if not self._token_id or not isinstance(self._stream, PolymarketStreamManager):
            return adjusted_price

        top_of_book = self._stream.get_top_of_book(self._token_id)
        if top_of_book is None:
            return adjusted_price

        best_bid, best_ask = top_of_book
        tick_size = self._trading_rules.tick_size

        if side == "BUY" and best_ask > 0 and adjusted_price >= best_ask:
            adjusted_price = self.align_price(
                self._clamp_price(best_ask - tick_size),
                self._trading_rules,
            )
            if adjusted_price >= best_ask:
                raise ValueError(
                    f"buy price would cross best ask: requested={requested_price} best_ask={best_ask}"
                )
            logger.info(
                "%s maker guard adjusted buy price %.4f -> %.4f (best_ask=%.4f)",
                self.log_prefix,
                requested_price,
                adjusted_price,
                best_ask,
            )
            return adjusted_price

        if side == "SELL" and best_bid > 0 and adjusted_price <= best_bid:
            adjusted_price = self.align_price(
                self._clamp_price(best_bid + tick_size),
                self._trading_rules,
            )
            if adjusted_price <= best_bid:
                raise ValueError(
                    f"sell price would cross best bid: requested={requested_price} best_bid={best_bid}"
                )
            logger.info(
                "%s maker guard adjusted sell price %.4f -> %.4f (best_bid=%.4f)",
                self.log_prefix,
                requested_price,
                adjusted_price,
                best_bid,
            )
            return adjusted_price

        return adjusted_price

    def _resolve_sell_quantity(self, requested_quantity: float, max_wait: int = 30) -> float:
        """卖单数量防失败：根据可用余额动态缩量。"""
        if not self._token_id:
            raise ValueError("token_id is not initialized")

        start = time.time()
        last_balance = 0.0

        while time.time() - start < max_wait:
            balance = self._get_token_balance(self._token_id)
            last_balance = balance
            adjusted = self._adjust_sell_quantity_for_balance(
                requested_quantity=requested_quantity,
                available_balance=balance,
                force=False,
            )
            if adjusted is not None:
                return adjusted
            time.sleep(1)

        adjusted = self._adjust_sell_quantity_for_balance(
            requested_quantity=requested_quantity,
            available_balance=last_balance,
            force=True,
        )
        if adjusted is not None:
            return adjusted

        raise ValueError(
            f"token balance insufficient: {last_balance:.4f}/{requested_quantity:.4f} after {max_wait}s"
        )

    def _adjust_sell_quantity_for_balance(
        self,
        requested_quantity: float,
        available_balance: float,
        force: bool,
    ) -> Optional[float]:
        if available_balance <= 0:
            return None

        if available_balance >= requested_quantity:
            return self.align_quantity(requested_quantity, self._trading_rules)

        gap = requested_quantity - available_balance
        gap_ratio = gap / max(requested_quantity, 1e-9)
        fee_gap_threshold = _MAX_EFFECTIVE_TAKER_FEE_RATE + _SELL_FEE_GAP_TOLERANCE
        likely_fee = 0 < gap_ratio <= fee_gap_threshold

        if not likely_fee and not force:
            logger.debug(
                "%s sell balance pending qty=%.4f balance=%.4f gap_ratio=%.4f",
                self.log_prefix,
                requested_quantity,
                available_balance,
                gap_ratio,
            )
            return None

        safe_balance = max(0.0, available_balance - _SELL_BALANCE_BUFFER)
        adjusted_quantity = self.align_quantity(safe_balance, self._trading_rules)
        if adjusted_quantity <= 0:
            adjusted_quantity = self.align_quantity(available_balance, self._trading_rules)
        if adjusted_quantity <= 0:
            return None

        if likely_fee:
            logger.info(
                "%s sell qty adjusted for taker fee requested=%.4f balance=%.4f adjusted=%.4f gap_ratio=%.4f",
                self.log_prefix,
                requested_quantity,
                available_balance,
                adjusted_quantity,
                gap_ratio,
            )
        else:
            logger.warning(
                "%s sell qty clamped to balance requested=%.4f balance=%.4f adjusted=%.4f gap_ratio=%.4f",
                self.log_prefix,
                requested_quantity,
                available_balance,
                adjusted_quantity,
                gap_ratio,
            )

        return adjusted_quantity

    def _create_signed_order(self, side: str, price: float, quantity: float) -> Any:
        """创建并签名订单 (不提交)."""
        if not self._token_id:
            raise RuntimeError("token_id is not initialized")
        if not self._is_market_tradeable():
            raise RuntimeError("market is closing")

        clob_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            price=price,
            size=quantity,
            side=clob_side,
            token_id=self._token_id,
        )
        return self._client.create_order(order_args)

    @staticmethod
    def _parse_batch_order_response(
        resp: Any, batch_meta: List[tuple],
    ) -> List[OrderResult]:
        """解析 post_orders 批量响应."""
        # 响应可能是 list[dict] 或 dict
        if isinstance(resp, list):
            items = resp
        elif isinstance(resp, dict):
            items = resp.get("orders") or resp.get("results") or [resp]
        else:
            items = []

        results: List[OrderResult] = []
        for j in range(len(batch_meta)):
            if j < len(items) and isinstance(items[j], dict):
                item = items[j]
                order_id = str(
                    item.get("id") or item.get("orderID") or item.get("order_id") or ""
                )
                if order_id:
                    side, price, quantity = batch_meta[j]
                    results.append(OrderResult(
                        success=True,
                        order_id=order_id,
                        status=OrderStatus.PLACED,
                        filled_quantity=quantity,
                        filled_price=price,
                    ))
                else:
                    error = item.get("error") or item.get("message") or f"no order_id: {item}"
                    results.append(OrderResult(
                        success=False, order_id=None,
                        status=OrderStatus.FAILED, error=str(error),
                    ))
            else:
                results.append(OrderResult(
                    success=False, order_id=None,
                    status=OrderStatus.FAILED, error="missing response item",
                ))
        return results

    def _post_order(self, signed_order: Any, order_type: OrderType) -> Any:
        """兼容不同 py-clob-client 版本的 post_order 签名."""
        try:
            # 常见版本：post_order(order, OrderType)
            return self._client.post_order(signed_order, order_type)
        except TypeError:
            # 部分版本：post_order(order, order_type=OrderType)
            return self._client.post_order(signed_order, order_type=order_type)

    # ── 订单标准化 ────────────────────────────────────────────────

    def _normalize_order(self, raw: Dict[str, Any]) -> Optional[ExchangeOrder]:
        order_id = str(raw.get("id") or raw.get("order_id") or raw.get("orderID") or "")
        if not order_id:
            return None

        side = str(raw.get("side", "buy")).lower()
        price = _safe_float(raw.get("price") or raw.get("limit_price", 0))
        quantity = _safe_float(raw.get("size") or raw.get("original_size") or raw.get("quantity", 0))
        filled = _safe_float(raw.get("size_matched") or raw.get("filled_size") or raw.get("filled", 0))
        status = self._map_status(str(raw.get("status", "open")).lower(), filled, quantity)

        extra: Dict[str, Any] = {}
        token_id = raw.get("asset_id") or raw.get("token_id")
        if token_id:
            extra["token_id"] = str(token_id)
        extra["raw_order"] = raw

        return ExchangeOrder(
            order_id=order_id,
            symbol=self.symbol,
            side=side,
            price=price,
            quantity=quantity,
            filled_quantity=filled,
            status=status,
            extra=extra,
        )

    @staticmethod
    def _map_status(raw: str, filled: float, quantity: float) -> OrderStatus:
        if raw in {"filled", "matched", "complete", "completed"}:
            return OrderStatus.FILLED
        if raw in {"canceled", "cancelled", "expired"}:
            return OrderStatus.CANCELLED
        if raw in {"rejected", "failed", "error"}:
            return OrderStatus.FAILED
        if raw in {"partially_filled", "partial"}:
            return OrderStatus.PARTIALLY_FILLED
        if quantity > 0 and filled >= quantity:
            return OrderStatus.FILLED
        if filled > 0:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.PLACED

    @staticmethod
    def _clamp_price(price: float) -> float:
        return min(max(price, _MIN_POLY_PRICE), _MAX_POLY_PRICE)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _fetch_gamma_event(slug: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(f"{_GAMMA_API_BASE}/events?slug={slug}", timeout=10)
        if resp.status_code != 200:
            return None

        payload = resp.json()
        if not payload or not isinstance(payload, list):
            return None
        event = payload[0]
        return event if isinstance(event, dict) else None
    except Exception as err:
        logger.warning("gamma query failed slug=%s err=%s", slug, err)
        return None


def _select_token_id(outcomes: list, token_ids: list, desired_outcome: str) -> str | None:
    normalized = desired_outcome.lower()
    for idx, outcome in enumerate(outcomes):
        if not isinstance(outcome, str):
            continue
        if outcome.lower() == normalized and idx < len(token_ids):
            return str(token_ids[idx])
    if token_ids:
        return str(token_ids[0])
    return None


def _normalize_polymarket_private_key(raw_secret: str) -> str:
    private_key = (raw_secret or "").strip()
    if private_key.lower().startswith("0x"):
        private_key = private_key[2:]

    if len(private_key) != 64 or any(ch not in string.hexdigits for ch in private_key):
        raise ValueError(
            "invalid polymarket private key: expected 64 hex chars (optionally prefixed with 0x)"
        )

    return private_key
