from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import logging
import math

from worker.core.log_utils import make_log_prefix

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OrderRequest:
    """下单请求"""
    side: str          # 'buy' | 'sell'
    price: float
    quantity: float
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EditOrderRequest:
    """改单请求"""
    order_id: str
    side: str          # 'buy' | 'sell'
    price: float
    quantity: float


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    status: OrderStatus
    filled_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    remaining_quantity: Optional[float] = None
    error: Optional[str] = None
    suppress_notify: bool = False


@dataclass
class Position:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0


@dataclass
class TradingRules:
    tick_size: float
    price_decimals: int
    step_size: float
    qty_decimals: int
    min_notional: float = 0


@dataclass
class ExchangeOrder:
    order_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    filled_quantity: float = 0
    status: OrderStatus = OrderStatus.PLACED
    extra: Dict[str, Any] = field(default_factory=dict)
    fee_paid_externally: bool = False


class BaseExchange(ABC):
    """交易所抽象基类

    职责：执行订单操作，提供数据流
    内建：自动重连、指数退避重试、限流处理
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        testnet: bool = False,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol
        self.testnet = testnet

    @property
    def log_prefix(self) -> str:
        """获取日志前缀 [SYMBOL] [API_KEY_PREFIX] [EXCHANGE_ID]"""
        exchange_id = self.get_exchange_info().get("id", "unknown")
        return make_log_prefix(self.symbol, self.api_key, exchange_id)

    @classmethod
    @abstractmethod
    def get_exchange_info(cls) -> Dict[str, str]:
        """获取交易所信息 {'id': str, 'name': str, 'type': 'spot'|'futures'|'prediction'}"""
        pass

    @abstractmethod
    def get_trading_rules(self) -> TradingRules:
        """获取交易规则"""
        pass

    @abstractmethod
    def get_fee_rate(self) -> float:
        """获取手续费率"""
        pass

    @abstractmethod
    def place_batch_orders(self, orders: List[OrderRequest]) -> List[OrderResult]:
        """批量下单"""
        pass

    @abstractmethod
    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        """批量取消订单"""
        pass

    def edit_batch_orders(self, edits: List[EditOrderRequest]) -> List[OrderResult]:
        """批量改单，默认实现：cancel + recreate + 失败重试/对账，子类可覆写"""
        if not edits:
            return []

        cancel_ids = [e.order_id for e in edits]
        cancel_results = self.cancel_batch_orders(cancel_ids)

        cancelled_set = {
            r.order_id for r in cancel_results if r.success and r.order_id
        }

        new_orders = [
            OrderRequest(side=e.side, price=e.price, quantity=e.quantity)
            for e in edits
            if e.order_id in cancelled_set
        ]
        if not new_orders:
            return [
                OrderResult(success=False, order_id=e.order_id, status=OrderStatus.FAILED, error="cancel failed")
                for e in edits
            ]

        place_results = self.place_batch_orders(new_orders)

        # 对失败的下单重试一次 (处理瞬时网络错误)
        retry_indices = [
            i for i, r in enumerate(place_results) if not r.success
        ]
        if retry_indices:
            retry_orders = [new_orders[i] for i in retry_indices]
            retry_results = self.place_batch_orders(retry_orders)
            for j, idx in enumerate(retry_indices):
                if j < len(retry_results) and retry_results[j].success:
                    place_results[idx] = retry_results[j]

        # 重试后仍失败的，对账查询交易所确认是否实际成功
        still_failed = [
            i for i, r in enumerate(place_results) if not r.success
        ]
        if still_failed:
            matched = self._reconcile_failed_placements(
                [new_orders[i] for i in still_failed],
            )
            for j, idx in enumerate(still_failed):
                if j < len(matched) and matched[j] is not None:
                    place_results[idx] = matched[j]

        results: List[OrderResult] = []
        place_idx = 0
        for edit in edits:
            if edit.order_id in cancelled_set:
                if place_idx < len(place_results):
                    results.append(place_results[place_idx])
                    place_idx += 1
                else:
                    results.append(OrderResult(success=False, order_id=None, status=OrderStatus.FAILED, error="no place result"))
            else:
                results.append(OrderResult(success=False, order_id=edit.order_id, status=OrderStatus.FAILED, error="cancel failed"))

        return results

    def _reconcile_failed_placements(
        self, failed_orders: List[OrderRequest],
    ) -> List[Optional[OrderResult]]:
        """对账：查询交易所挂单，尝试匹配失败的下单请求。

        按 (side, price) 匹配，找到则视为下单实际成功。
        """
        results: List[Optional[OrderResult]] = [None] * len(failed_orders)
        try:
            open_orders = self.get_open_orders()
        except Exception:
            return results

        # 按 (side, price) 索引交易所挂单
        candidates: Dict[tuple, List[ExchangeOrder]] = {}
        for o in open_orders:
            key = (o.side.lower(), round(o.price, 8))
            candidates.setdefault(key, []).append(o)

        for i, req in enumerate(failed_orders):
            key = (req.side.lower(), round(req.price, 8))
            matched_list = candidates.get(key)
            if matched_list:
                matched = matched_list.pop(0)
                results[i] = OrderResult(
                    success=True,
                    order_id=matched.order_id,
                    status=OrderStatus.PLACED,
                    filled_price=matched.price,
                    filled_quantity=matched.quantity,
                )
                logger.info(
                    "reconcile: matched failed placement side=%s price=%s -> order_id=%s",
                    req.side, req.price, matched.order_id,
                )
        return results

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[ExchangeOrder]:
        """查询单个订单"""
        pass

    @abstractmethod
    def get_open_orders(self) -> List[ExchangeOrder]:
        """获取所有未完成订单"""
        pass

    def get_positions(self) -> List[Position]:
        """获取持仓（现货返回空，合约需实现）"""
        return []

    @abstractmethod
    def get_ticker_price(self) -> float:
        """获取当前价格"""
        pass

    def get_status_extra(self) -> Dict[str, Any]:
        """返回交易所相关的扩展运行状态。"""
        return {}

    def align_price(self, price: float, rules: Optional[TradingRules] = None) -> float:
        """价格对齐"""
        if rules is None:
            rules = self.get_trading_rules()
        aligned = math.floor(price / rules.tick_size) * rules.tick_size
        return round(aligned, rules.price_decimals)

    def align_quantity(self, qty: float, rules: Optional[TradingRules] = None) -> float:
        """数量对齐"""
        if rules is None:
            rules = self.get_trading_rules()
        aligned = math.floor(qty / rules.step_size) * rules.step_size
        return round(aligned, rules.qty_decimals)

    def close(self) -> None:
        """关闭交易所连接，释放资源（子类可覆盖）"""
        pass
