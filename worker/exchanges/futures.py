"""合约交易所实现（基于 CCXT）"""

import logging
from typing import Any, Dict, List

from shared.exchanges import FUTURES_EXCHANGE_IDS
from worker.core.base_exchange import ExchangeOrder, OrderRequest
from worker.exchanges.spot import ExchangeSpot

logger = logging.getLogger(__name__)


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
        self._ensure_hedge_mode()

    def get_exchange_info(self) -> Dict[str, str]:
        return {"id": self.exchange_id, "name": self.exchange_id, "type": "futures"}

    def _ensure_hedge_mode(self) -> None:
        """确保账户为双向持仓模式（hedge mode），bilateral 策略需要"""
        has = getattr(self._exchange, "has", {})
        if not has.get("setPositionMode"):
            return
        try:
            self._run_sync(lambda: self._exchange.set_position_mode(True))
            logger.info("%s 已设置双向持仓模式", self._log_prefix)
        except Exception as err:
            msg = str(err)
            # "No need to change position side" 表示已经是双向模式
            if "-4059" in msg or "No need to change" in msg:
                logger.debug("%s 已处于双向持仓模式", self._log_prefix)
            else:
                logger.warning("%s 设置双向持仓模式失败: %s", self._log_prefix, err)

    def get_open_orders(self) -> List[ExchangeOrder]:
        """合约版 get_open_orders：WS 失败时自动降级到 REST"""
        if self._stream is not None:
            orders = self._stream.get_open_orders(self._market_symbol)
            if orders:
                return orders

        has = getattr(self._exchange, "has", {})
        if has.get("fetchOpenOrdersWs"):
            try:
                raw_orders = self._run_sync(
                    lambda: self._exchange.fetch_open_orders_ws(self._market_symbol)
                )
                return [
                    self._to_exchange_order(o)
                    for o in raw_orders
                    if isinstance(o, dict)
                ]
            except Exception:
                pass  # WS 失败，降级到 REST

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

    def _normalize_create_order(self, order: OrderRequest) -> Dict[str, Any]:
        normalized = super()._normalize_create_order(order)
        params: Dict[str, Any] = dict(normalized.get("params") or {})

        # 合并 OrderRequest.params（positionSide、reduceOnly 等）
        params.update(order.params)

        # 仅在没有显式设置时，对 sell 单默认 reduceOnly（兼容单边策略）
        if order.side.lower() == "sell" and "reduceOnly" not in params and "positionSide" not in params:
            params["reduceOnly"] = True

        normalized["params"] = params
        return normalized
