from typing import List, Mapping, Optional, Sequence

from worker.core.base_strategy import Signal, TradeDecision
from worker.domain.order import Order
from worker.domain.position import PositionEntry
from worker.strategies.grid_strategy import GridStrategy


class BilateralGridStrategy(GridStrategy):
    """双边网格策略

    做多侧（正 grid_index）完全复用父类 GridStrategy。
    做空侧（负 grid_index）在当前价格上方挂卖单开空，
    成交后在下方挂买单平空。
    """

    def should_short_batch(
        self,
        current_price: float,
        pending_short_opens: Mapping[str, Order],
        pending_short_closes: Mapping[str, Order],
        short_positions: Sequence[PositionEntry] = (),
    ) -> List[TradeDecision]:
        """在当前价格上方批量生成做空开仓决策"""
        active_opens = len(pending_short_opens)
        active_closes = len(pending_short_closes)
        total = active_opens + active_closes

        if total >= self.config.order_grid:
            return []

        used_indices = {
            order.grid_index
            for order in pending_short_opens.values()
            if order.grid_index < 0
        }
        used_close_indices = {
            order.grid_index
            for order in pending_short_closes.values()
            if order.grid_index < 0
        }
        used_position_indices = {
            pos.grid_index for pos in short_positions if pos.grid_index < 0
        }

        decisions: List[TradeDecision] = []
        for i in range(1, self.config.order_grid + 1):
            grid_index = -i
            if grid_index in used_indices or grid_index in used_close_indices or grid_index in used_position_indices:
                continue
            if total + len(decisions) >= self.config.order_grid:
                break

            short_price = self._calculate_short_price(current_price, grid_index)
            decisions.append(TradeDecision(
                signal=Signal.SELL,
                price=short_price,
                quantity=self.config.quantity,
                grid_index=grid_index,
                reason=f"做空网格{abs(grid_index)}开仓",
            ))

        return decisions

    def should_close_short(
        self,
        open_price: float,
        open_quantity: float,
        current_price: float,
    ) -> Optional[TradeDecision]:
        """做空成交后生成买单平仓决策"""
        close_price = open_price * (1 - self.config.sell_offset_percent / 100.0)
        return TradeDecision(
            signal=Signal.BUY,
            price=close_price,
            quantity=open_quantity,
            reason="做空成交，挂买单平仓",
        )

    def should_reprice_short(
        self,
        order_price: float,
        current_price: float,
        grid_index: int,
    ) -> Optional[float]:
        """做空开仓单改价"""
        target_price = self._calculate_short_price(current_price, grid_index)
        if target_price is None or target_price <= 0:
            return None

        diff_pct = abs(order_price - target_price) / target_price * 100
        if diff_pct > self.config.reprice_threshold:
            return target_price
        return None

    def _calculate_short_price(self, current_price: float, grid_index: int) -> float:
        """计算做空开仓价格：当前价格上方"""
        offset = abs(grid_index) * self.config.offset_percent / 100.0
        return current_price * (1 + offset)
