"""做空网格策略：纯做空模式，覆盖做多侧方法返回空值。"""
from typing import List, Optional

from worker.core.base_strategy import TradeDecision
from worker.strategies.bilateral_grid_strategy import BilateralGridStrategy


class ShortGridStrategy(BilateralGridStrategy):
    """纯做空网格策略

    继承 BilateralGridStrategy，覆盖做多侧方法使其返回空值，
    引擎自然只执行做空侧逻辑。
    """

    def should_buy(self, current_price: float, buy_price: float) -> Optional[TradeDecision]:
        return None

    def should_buy_batch(self, current_price: float, **kwargs) -> List[TradeDecision]:
        return []

    def should_sell(self, buy_price: float, current_price: float) -> Optional[TradeDecision]:
        return None

    def should_reprice(self, order_price: float, current_price: float, grid_index: int = 0) -> Optional[float]:
        return None
