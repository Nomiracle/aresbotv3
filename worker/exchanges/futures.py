"""合约交易所实现（基于 CCXT）"""

from typing import Any, Dict

from worker.core.base_exchange import OrderRequest
from worker.exchanges.spot import ExchangeSpot

FUTURES_EXCHANGE_IDS = {"binanceusdm", "binancecoinm"}


class ExchangeFutures(ExchangeSpot):
    """合约交易所实现，复用通用 CCXT 读写能力。"""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        exchange_id: str = "binanceusdm",
        testnet: bool = False,
    ):
        normalized_exchange_id = exchange_id.strip().lower()
        if normalized_exchange_id not in FUTURES_EXCHANGE_IDS:
            raise ValueError(f"Unsupported futures exchange: {exchange_id}")
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            exchange_id=normalized_exchange_id,
            testnet=testnet,
        )

    def get_exchange_info(self) -> Dict[str, str]:
        return {"id": self.exchange_id, "name": self.exchange_id, "type": "futures"}

    def _normalize_create_order(self, order: OrderRequest) -> Dict[str, Any]:
        normalized = super()._normalize_create_order(order)
        params: Dict[str, Any] = dict(normalized.get("params") or {})

        if order.side.lower() == "sell":
            params["reduceOnly"] = True

        normalized["params"] = params
        return normalized
