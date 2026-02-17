"""Polymarket UpDown 1d exchange adapter.

继承 15m 实现，覆盖周期、slug 生成和对齐逻辑。
Slug 格式: bitcoin-up-or-down-on-february-17
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

from worker.exchanges.polymarket_updown15m import (
    PolymarketUpDown15m,
    _ET_TZ,
    _fetch_gamma_event,
    _safe_json_list,
    _select_token_id,
)

_MARKET_PERIOD_SECONDS = 24 * 60 * 60
_DEFAULT_MARKET_CLOSE_BUFFER = 1800

_MARKET_NAME_MAP: Dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "xrp": "xrp",
}


class PolymarketUpDown1d(PolymarketUpDown15m):
    """Polymarket 1d UpDown market."""

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {
            "id": "polymarket_updown1d",
            "name": "Polymarket UpDown 1d",
            "type": "prediction",
        }

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
        market_close_buffer: Optional[int] = None,
    ):
        if market_close_buffer is None:
            market_close_buffer = _DEFAULT_MARKET_CLOSE_BUFFER
        super().__init__(api_key, api_secret, symbol, testnet, market_close_buffer)

    def _aligned_timestamp(self, offset: int = 0) -> int:
        """计算第 offset 天的开始时间戳 (ET午夜对齐)."""
        now = datetime.now(_ET_TZ)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int((base + timedelta(days=offset)).timestamp())

    def _build_slug(self, timestamp: int) -> str:
        """构建1d市场slug, 格式: bitcoin-up-or-down-on-february-17"""
        dt = datetime.fromtimestamp(timestamp, tz=_ET_TZ)
        market_name = _MARKET_NAME_MAP.get(self._market_prefix, self._market_prefix)
        month = dt.strftime("%B").lower()
        day = dt.day
        return f"{market_name}-up-or-down-on-{month}-{day}"

    def _get_market_token_by_timestamp(self, timestamp: int) -> Optional[str]:
        slug = self._build_slug(timestamp)
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
