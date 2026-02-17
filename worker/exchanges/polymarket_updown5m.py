"""Polymarket UpDown 5m exchange adapter (BTC only).

继承 15m 实现，仅覆盖周期和 slug 相关逻辑。
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

_MARKET_PERIOD_SECONDS = 5 * 60
_DEFAULT_MARKET_CLOSE_BUFFER = 60


class PolymarketUpDown5m(PolymarketUpDown15m):
    """Polymarket 5m UpDown market (BTC only)."""

    _SUPPORTED_MARKETS = ("btc",)

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
        market_close_buffer: Optional[int] = None,
    ):
        market_prefix = symbol.strip().split("-", 1)[0].lower()
        if market_prefix not in self._SUPPORTED_MARKETS:
            raise ValueError(
                f"5m market only supports {self._SUPPORTED_MARKETS}, got: {market_prefix}"
            )
        if market_close_buffer is None:
            market_close_buffer = _DEFAULT_MARKET_CLOSE_BUFFER
        super().__init__(api_key, api_secret, symbol, testnet, market_close_buffer)

    @classmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        return {
            "id": "polymarket_updown5m",
            "name": "Polymarket UpDown 5m",
            "type": "prediction",
        }

    def _aligned_timestamp(self, offset: int = 0) -> int:
        """计算第 offset 个5分钟周期的开始时间戳 (ET对齐)."""
        now = datetime.now(_ET_TZ)
        base = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
        return int((base + timedelta(minutes=5 * offset)).timestamp())

    def _get_market_token_by_timestamp(self, timestamp: int) -> Optional[str]:
        slug = f"{self._market_prefix}-updown-5m-{timestamp}"
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
