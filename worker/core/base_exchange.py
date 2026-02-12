from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import math

from worker.core.log_utils import make_log_prefix


class OrderStatus(Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    status: OrderStatus
    filled_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    remaining_quantity: Optional[float] = None
    error: Optional[str] = None


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
    def place_batch_orders(self, orders: List[Dict]) -> List[OrderResult]:
        """批量下单

        Args:
            orders: [{'side': str, 'price': float, 'quantity': float}, ...]
        """
        pass

    @abstractmethod
    def cancel_batch_orders(self, order_ids: List[str]) -> List[OrderResult]:
        """批量取消订单"""
        pass

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
