"""Polymarket UpDown 15m exchange adapter.

参考v1实现，通过slug直接查询gamma API获取市场token。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from worker.core.base_exchange import (
    BaseExchange,
    ExchangeOrder,
    OrderResult,
    OrderStatus,
    TradingRules,
)

logger = logging.getLogger(__name__)

_ET_TZ = pytz.timezone("America/New_York")
_GAMMA_API_BASE = "https://gamma-api.polymarket.com"
_CLOB_HOST = "https://clob.polymarket.com"
_CHAIN_ID = 137
_MARKET_PERIOD_SECONDS = 15 * 60


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
        self._market_close_buffer = max(int(market_close_buffer or 0), 0)

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

        self._trading_rules = TradingRules(
            tick_size=0.01,
            price_decimals=2,
            step_size=1.0,
            qty_decimals=0,
            min_notional=0,
        )

        # 初始化 ClobClient
        private_key = api_secret
        if private_key.startswith("0x"):
            private_key = private_key[2:]

        self._client = ClobClient(
            host=_CLOB_HOST,
            key=private_key,
            chain_id=_CHAIN_ID,
            signature_type=2,
            funder=api_key,
        )
        try:
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
        except Exception as e:
            logger.warning("%s API credentials setup failed: %s", self.log_prefix, e)

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
        return {
            "market_slug": self._market_slug,
            "token_id": self._token_id,
            "market_end_time": self._market_end_time,
            "seconds_until_close": self._seconds_until_close(),
            "is_closing": self._is_closing,
            "condition_id": self._condition_id,
        }

    def get_ticker_price(self) -> float:
        self._ensure_market_valid()
        if not self._token_id:
            raise RuntimeError("token_id is not initialized")

        midpoint_data = self._client.get_midpoint(self._token_id)
        price = float(midpoint_data.get("mid", 0))
        if price <= 0:
            raise RuntimeError("Polymarket ticker price is invalid")
        return price

    def place_batch_orders(self, orders: List[Dict]) -> List[OrderResult]:
        if not orders:
            return []

        self._ensure_market_valid()
        if self._is_market_closing_soon():
            return [
                OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="market is closing soon")
                for _ in orders
            ]

        results: List[OrderResult] = []
        for order in orders:
            side = str(order.get("side", "")).upper().strip()
            price = float(order.get("price", 0))
            quantity = float(order.get("quantity", 0))

            if side not in {"BUY", "SELL"}:
                results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=f"unsupported side: {side}"))
                continue
            if price <= 0 or quantity <= 0:
                results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="price and quantity must be positive"))
                continue

            try:
                resp = self._place_order(side, price, quantity)
                order_id = resp.get("id") or resp.get("orderID") or resp.get("order_id")
                if not order_id:
                    raise RuntimeError(f"no order_id in response: {resp}")

                exchange_order = ExchangeOrder(
                    order_id=str(order_id),
                    symbol=self.symbol,
                    side=side.lower(),
                    price=price,
                    quantity=quantity,
                    status=OrderStatus.PLACED,
                )
                with self._orders_lock:
                    self._orders_cache[exchange_order.order_id] = exchange_order

                results.append(OrderResult(success=True, order_id=exchange_order.order_id, status=OrderStatus.PLACED))
            except Exception as e:
                logger.warning("%s place order failed: %s", self.log_prefix, e)
                results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error=str(e)))

        return results

    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        if not order_ids:
            return []
        self._ensure_market_valid()

        results: List[OrderResult] = []
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
        self._ensure_market_valid()
        try:
            raw = self._client.get_order(order_id)
            if isinstance(raw, dict):
                return self._normalize_order(raw)
        except Exception:
            pass

        with self._orders_lock:
            return self._orders_cache.get(order_id)

    def get_open_orders(self) -> List[ExchangeOrder]:
        self._ensure_market_valid()
        if not self._token_id:
            return []

        try:
            raw_orders = self._client.get_orders()
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
        pass

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

    def _calculate_current_timestamp(self) -> int:
        """计算当前15分钟周期的开始时间戳 (ET对齐)."""
        now = datetime.now(_ET_TZ)
        current_15min = (now.minute // 15) * 15
        aligned = now.replace(minute=current_15min, second=0, microsecond=0)
        return int(aligned.timestamp())

    def _calculate_next_timestamp(self) -> int:
        """计算下一个15分钟周期的开始时间戳 (ET对齐)."""
        now = datetime.now(_ET_TZ)
        next_15min = ((now.minute // 15) + 1) * 15
        if next_15min >= 60:
            aligned = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            aligned = now.replace(minute=next_15min, second=0, microsecond=0)
        return int(aligned.timestamp())

    def _get_market_token_by_timestamp(self, timestamp: int) -> Optional[str]:
        """通过slug查询gamma API获取市场token_id (参考v1实现)."""
        slug = f"{self._market_prefix}-updown-15m-{timestamp}"
        try:
            resp = requests.get(f"{_GAMMA_API_BASE}/events?slug={slug}", timeout=10)
            if resp.status_code != 200:
                return None

            events = resp.json()
            if not events:
                return None

            market = events[0].get("markets", [{}])[0]
            self._condition_id = market.get("conditionId") or market.get("condition_id")

            clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))

            # 查找匹配的outcome token
            for i, outcome in enumerate(outcomes):
                if outcome.lower() == self._outcome.lower() and i < len(clob_token_ids):
                    self._market_slug = slug
                    self._market_end_time = timestamp + _MARKET_PERIOD_SECONDS
                    return clob_token_ids[i]

            # 兜底: 使用第一个token
            if clob_token_ids:
                self._market_slug = slug
                self._market_end_time = timestamp + _MARKET_PERIOD_SECONDS
                return clob_token_ids[0]

        except Exception as e:
            logger.warning("%s query market %s failed: %s", self.log_prefix, slug, e)
        return None

    def _get_next_market_token(self, max_retries: int = 6) -> Optional[str]:
        """获取下一个市场的token_id, 支持重试."""
        next_ts = self._calculate_next_timestamp()
        for attempt in range(max_retries):
            token_id = self._get_market_token_by_timestamp(next_ts)
            if token_id:
                return token_id
            if attempt < max_retries - 1:
                time.sleep(2)
        return None

    def _refresh_market(self) -> None:
        """刷新到当前或下一个市场."""
        ts = self._calculate_current_timestamp()
        token_id = self._get_market_token_by_timestamp(ts)
        if not token_id:
            raise RuntimeError(f"failed to resolve market for {self.symbol} at timestamp {ts}")

        if token_id != self._token_id:
            logger.info(
                "%s switched market token_id=%s slug=%s end_time=%s",
                self.log_prefix, token_id, self._market_slug, self._market_end_time,
            )
        self._token_id = token_id

    def _ensure_market_valid(self) -> None:
        """确保当前市场有效, 如果即将关闭则处理切换."""
        if not self._token_id:
            self._refresh_market()

        if self._is_market_closing_soon():
            self._handle_market_closing()

    def _seconds_until_close(self) -> int:
        """距离市场关闭的秒数."""
        if not self._market_end_time:
            return 0
        left = self._market_end_time - int(datetime.now(timezone.utc).timestamp())
        return max(0, left)

    def _is_market_closing_soon(self) -> bool:
        if self._market_close_buffer <= 0 or not self._market_end_time:
            return False
        return 0 <= self._seconds_until_close() <= self._market_close_buffer

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

            # 取消所有买单
            self._cancel_buy_orders()

            # 清算持仓
            self._liquidate_position()

            # 切换到新市场
            old_token = self._token_id
            new_token = self._get_next_market_token()
            if new_token and new_token != old_token:
                self._token_id = new_token
                with self._orders_lock:
                    self._orders_cache.clear()
                logger.info("%s switched to new market token_id=%s slug=%s", self.log_prefix, new_token, self._market_slug)
            elif not new_token:
                # 刷新当前市场
                self._refresh_market()
        finally:
            with self._market_lock:
                self._is_closing = False

    def _cancel_buy_orders(self) -> None:
        """取消当前市场的所有买单."""
        try:
            open_orders = self.get_open_orders()
            buy_ids = [o.order_id for o in open_orders if o.side == "buy"]
            if buy_ids:
                self.cancel_batch_orders(buy_ids)
        except Exception as e:
            logger.warning("%s cancel buy orders failed: %s", self.log_prefix, e)

    def _liquidate_position(self) -> None:
        """以IOC市价单清算当前token持仓."""
        if not self._token_id:
            return
        try:
            balance = self._get_token_balance(self._token_id)
            if balance < 1.0:
                return

            order_args = OrderArgs(
                price=0.01,
                size=balance,
                side=SELL,
                token_id=self._token_id,
            )
            signed = self._client.create_order(order_args)
            self._client.post_order(signed, order_type=OrderType.FOK)
            logger.info("%s liquidated position qty=%s", self.log_prefix, balance)
        except Exception as e:
            logger.warning("%s liquidate position failed: %s", self.log_prefix, e)

    def _get_token_balance(self, token_id: str) -> float:
        """查询指定token的可用余额."""
        try:
            balance = self._client.get_balance_allowance(token_id)
            if isinstance(balance, dict):
                return float(balance.get("balance", 0))
            return float(balance or 0)
        except Exception:
            return 0.0

    # ── 下单 ──────────────────────────────────────────────────────

    def _place_order(self, side: str, price: float, quantity: float) -> Dict[str, Any]:
        """下限价单."""
        if not self._token_id:
            raise RuntimeError("token_id is not initialized")
        if self._is_market_closing_soon():
            raise RuntimeError("market is closing soon")

        clob_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            price=price,
            size=quantity,
            side=clob_side,
            token_id=self._token_id,
        )
        signed = self._client.create_order(order_args)
        resp = self._client.post_order(signed, order_type=OrderType.GTC)

        if isinstance(resp, dict):
            return resp
        raise RuntimeError(f"unexpected order response: {resp}")

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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
