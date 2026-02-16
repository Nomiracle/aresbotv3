from typing import List, Mapping, Optional, Sequence

from worker.core.base_strategy import Signal, TradeDecision
from worker.domain.order import Order
from worker.domain.position import PositionEntry
from worker.strategies.grid_strategy import GridStrategy


class PolymarketGridStrategy(GridStrategy):
    """Polymarket 网格策略（加法偏移）。"""

    _MIN_PRICE = 0.01
    _MAX_PRICE = 0.99

    def should_buy_batch(
        self,
        current_price: float,
        pending_buy_orders: Mapping[str, Order],
        pending_sell_orders: Mapping[str, Order],
        positions: Sequence[PositionEntry] = (),
    ) -> List[TradeDecision]:
        decisions = super().should_buy_batch(
            current_price, pending_buy_orders, pending_sell_orders, positions,
        )
        # 价格触底后多个网格会 clamp 到同一价格，去重只保留第一个
        seen_prices: set[float] = set()
        unique: List[TradeDecision] = []
        for d in decisions:
            if d.price not in seen_prices:
                seen_prices.add(d.price)
                unique.append(d)
        return unique

    def _calculate_buy_price(self, current_price: float, grid_index: int) -> float:
        price_offset = grid_index * self.config.offset_percent / 100.0
        return self._clamp_price(current_price - price_offset)

    def _calculate_sell_price(self, buy_price: float, current_price: float) -> float:
        del current_price
        price_offset = self.config.sell_offset_percent / 100.0
        return self._clamp_price(buy_price + price_offset)

    def _calculate_reprice_target_price(
        self,
        current_price: float,
        is_buy: bool,
        grid_index: int,
    ) -> Optional[float]:
        if is_buy:
            return self._calculate_buy_price(current_price=current_price, grid_index=grid_index)
        return self._clamp_price(current_price + self.config.sell_offset_percent / 100.0)

    @classmethod
    def _clamp_price(cls, price: float) -> float:
        bounded_price = min(max(price, cls._MIN_PRICE), cls._MAX_PRICE)
        return round(bounded_price, 2)
