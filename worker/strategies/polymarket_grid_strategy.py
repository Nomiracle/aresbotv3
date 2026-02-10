from typing import Optional

from worker.strategies.grid_strategy import GridStrategy


class PolymarketGridStrategy(GridStrategy):
    """Polymarket 网格策略（加法偏移）。"""

    _MIN_PRICE = 0.01
    _MAX_PRICE = 0.99

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
