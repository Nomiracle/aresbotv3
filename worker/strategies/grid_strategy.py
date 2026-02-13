from typing import List, Mapping, Optional, Sequence
import logging

from worker.core.base_strategy import BaseStrategy, Signal, StrategyConfig, TradeDecision
from worker.core.log_utils import PrefixAdapter
from worker.domain.order import Order
from worker.domain.position import PositionEntry


class GridStrategy(BaseStrategy):
    """网格策略实现"""

    def __init__(self, config: StrategyConfig, log_prefix: str = ""):
        super().__init__(config)
        if log_prefix:
            self.logger = PrefixAdapter(
                logging.getLogger(__name__),
                {"prefix": log_prefix},
            )
        else:
            self.logger = logging.LoggerAdapter(
                logging.getLogger(__name__),
                {"symbol": config.symbol},
            )

    def should_buy(
        self,
        current_price: float,
        pending_buy_orders: Mapping[str, Order],
        pending_sell_orders: Mapping[str, Order],
    ) -> Optional[TradeDecision]:
        """判断是否需要下买单"""
        active_buy_orders = len(pending_buy_orders)
        active_sell_orders = len(pending_sell_orders)
        total_orders = active_buy_orders + active_sell_orders

        if total_orders >= self.config.order_grid:
            self.logger.debug(
                "skip buy total_orders=%s grid=%s price=%s",
                total_orders,
                self.config.order_grid,
                current_price,
            )
            return None

        used_indices = {
            order.grid_index
            for order in pending_buy_orders.values()
            if order.grid_index > 0
        }
        grid_index = 1
        while grid_index in used_indices and grid_index <= self.config.order_grid:
            grid_index += 1

        if grid_index > self.config.order_grid:
            self.logger.debug(
                "skip buy no free grid slot used=%s grid=%s",
                sorted(used_indices),
                self.config.order_grid,
            )
            return None

        buy_price = self._calculate_buy_price(
            current_price=current_price,
            grid_index=grid_index,
        )

        self.logger.debug(
            "buy decision grid=%s current_price=%s buy_price=%s qty=%s",
            grid_index,
            current_price,
            buy_price,
            self.config.quantity,
        )

        return TradeDecision(
            signal=Signal.BUY,
            price=buy_price,
            quantity=self.config.quantity,
            grid_index=grid_index,
            reason=f"网格{grid_index}买入",
        )

    def should_buy_batch(
        self,
        current_price: float,
        pending_buy_orders: Mapping[str, Order],
        pending_sell_orders: Mapping[str, Order],
        positions: Sequence[PositionEntry] = (),
    ) -> List[TradeDecision]:
        """批量生成所有空闲网格槽位的买单决策"""
        active_buy_orders = len(pending_buy_orders)
        active_sell_orders = len(pending_sell_orders)
        total_orders = active_buy_orders + active_sell_orders

        if total_orders >= self.config.order_grid:
            return []

        used_indices = {
            order.grid_index
            for order in pending_buy_orders.values()
            if order.grid_index > 0
        }
        # sell 订单也占用 grid 槽位
        used_sell_indices = {
            order.grid_index
            for order in pending_sell_orders.values()
            if order.grid_index > 0
        }
        # 持仓占用 grid 槽位（防止卖单失败后重复下买单）
        used_position_indices = {
            pos.grid_index for pos in positions if pos.grid_index > 0
        }

        decisions: List[TradeDecision] = []
        for grid_index in range(1, self.config.order_grid + 1):
            if grid_index in used_indices or grid_index in used_sell_indices or grid_index in used_position_indices:
                continue
            if total_orders + len(decisions) >= self.config.order_grid:
                break

            buy_price = self._calculate_buy_price(
                current_price=current_price,
                grid_index=grid_index,
            )
            decisions.append(TradeDecision(
                signal=Signal.BUY,
                price=buy_price,
                quantity=self.config.quantity,
                grid_index=grid_index,
                reason=f"网格{grid_index}买入",
            ))

        return decisions

    def should_sell(
        self,
        buy_price: float,
        buy_quantity: float,
        current_price: float,
    ) -> Optional[TradeDecision]:
        """买单成交后判断卖单"""
        sell_price = self._calculate_sell_price(
            buy_price=buy_price,
            current_price=current_price,
        )

        return TradeDecision(
            signal=Signal.SELL,
            price=sell_price,
            quantity=buy_quantity,
            reason="买单成交，挂卖单",
        )

    def should_reprice(
        self,
        order_price: float,
        current_price: float,
        is_buy: bool,
        grid_index: int = 1,
    ) -> Optional[float]:
        """判断是否需要改价"""
        target_price = self._calculate_reprice_target_price(
            current_price=current_price,
            is_buy=is_buy,
            grid_index=grid_index,
        )
        if target_price is None or target_price <= 0:
            return None

        diff_pct = abs(order_price - target_price) / target_price * 100

        if diff_pct > self.config.reprice_threshold:
            self.logger.debug(
                "reprice %s old=%s target=%s diff_pct=%.4f threshold=%.4f",
                "buy" if is_buy else "sell",
                order_price,
                target_price,
                diff_pct,
                self.config.reprice_threshold,
            )
            return target_price

        self.logger.debug(
            "keep %s old=%s target=%s diff_pct=%.4f threshold=%.4f",
            "buy" if is_buy else "sell",
            order_price,
            target_price,
            diff_pct,
            self.config.reprice_threshold,
        )

        return None

    def _calculate_buy_price(self, current_price: float, grid_index: int) -> float:
        offset = grid_index * self.config.offset_percent / 100.0
        return current_price * (1 - offset)

    def _calculate_sell_price(self, buy_price: float, current_price: float) -> float:
        del current_price
        offset = self.config.sell_offset_percent / 100.0
        return buy_price * (1 + offset)

    def _calculate_reprice_target_price(
        self,
        current_price: float,
        is_buy: bool,
        grid_index: int,
    ) -> Optional[float]:
        if not is_buy:
            return None
        return self._calculate_buy_price(current_price=current_price, grid_index=grid_index)
