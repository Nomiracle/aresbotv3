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

    def get_exchange_info(self) -> Dict[str, str]:
        return {"id": self.exchange_id, "name": self.exchange_id, "type": "futures"}

    def ensure_hedge_mode(self) -> None:
        """确保账户为双向持仓模式（hedge mode），bilateral 策略需要"""
        has = getattr(self._exchange, "has", {})
        if not has.get("setPositionMode"):
            return

        def _try_set() -> bool:
            """尝试设置双向持仓，成功或已是双向返回 True"""
            try:
                self._run_sync(lambda: self._exchange.set_position_mode(True))
                logger.info("%s 已设置双向持仓模式", self._log_prefix)
                return True
            except Exception as err:
                msg = str(err)
                if "-4059" in msg or "No need to change" in msg:
                    logger.debug("%s 已处于双向持仓模式", self._log_prefix)
                    return True
                return False

        if _try_set():
            return

        # 首次失败（通常 -4068：存在挂单/持仓），取消当前 symbol 挂单后重试
        logger.info("%s 切换双向持仓模式需先取消挂单，正在清理…", self._log_prefix)
        try:
            raw_orders = self._run_sync(
                lambda: self._exchange.fetch_open_orders(self._market_symbol)
            )
            order_ids = [o["id"] for o in raw_orders if isinstance(o, dict) and o.get("id")]
            if order_ids:
                for oid in order_ids:
                    try:
                        self._run_sync(lambda _oid=oid: self._exchange.cancel_order(_oid, self._market_symbol))
                    except Exception:
                        pass
                logger.info("%s 已取消 %s 笔挂单", self._log_prefix, len(order_ids))
        except Exception as err:
            logger.warning("%s 清理挂单失败: %s", self._log_prefix, err)

        if _try_set():
            return

        raise RuntimeError(
            f"{self._log_prefix} 无法切换双向持仓模式，请手动在交易所关闭所有持仓和挂单后重试"
        )

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
