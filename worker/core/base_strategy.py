from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from worker.domain.order import Order


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


@dataclass
class TradeDecision:
    signal: Signal
    price: float
    quantity: float
    grid_index: int = 1
    reason: str = ""


@dataclass
class StrategyConfig:
    symbol: str
    quantity: float
    offset_percent: float
    sell_offset_percent: float
    order_grid: int = 1
    interval: float = 1.0
    reprice_threshold: float = 0.5


class BaseStrategy(ABC):
    """策略抽象基类

    职责：根据市场状态生成交易信号
    不负责：订单执行、状态持久化、风控检查
    """

    def __init__(self, config: StrategyConfig):
        self.config = config

    @abstractmethod
    def should_buy(
        self,
        current_price: float,
        pending_buy_orders: Mapping[str, "Order"],
        pending_sell_orders: Mapping[str, "Order"],
    ) -> Optional[TradeDecision]:
        """判断是否需要下买单"""
        pass

    @abstractmethod
    def should_sell(
        self,
        buy_price: float,
        buy_quantity: float,
        current_price: float,
    ) -> Optional[TradeDecision]:
        """买单成交后判断卖单"""
        pass

    @abstractmethod
    def should_reprice(
        self,
        order_price: float,
        current_price: float,
        is_buy: bool,
        grid_index: int = 1,
    ) -> Optional[float]:
        """判断是否需要改价，返回新价格或None"""
        pass
