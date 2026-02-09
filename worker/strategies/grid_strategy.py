from typing import Optional
import logging

from worker.core.base_strategy import BaseStrategy, Signal, StrategyConfig, TradeDecision


logger = logging.getLogger(__name__)


class GridStrategy(BaseStrategy):
    """网格策略实现"""

    def __init__(
        self,
        config: StrategyConfig,
        reprice_threshold: float = 0.5,
    ):
        super().__init__(config)
        self.reprice_threshold = reprice_threshold

    def should_buy(
        self,
        current_price: float,
        active_buy_orders: int,
        active_sell_orders: int,
    ) -> Optional[TradeDecision]:
        """判断是否需要下买单"""
        total_orders = active_buy_orders + active_sell_orders

        if total_orders >= self.config.order_grid:
            logger.debug(
                "GridStrategy skip buy total_orders=%s grid=%s price=%s",
                total_orders,
                self.config.order_grid,
                current_price,
            )
            return None

        grid_index = total_orders + 1
        buy_price = self.calculate_buy_price(current_price, grid_index)

        logger.debug(
            "GridStrategy buy decision grid=%s current_price=%s buy_price=%s qty=%s",
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

    def should_sell(
        self,
        buy_price: float,
        buy_quantity: float,
        current_price: float,
    ) -> Optional[TradeDecision]:
        """买单成交后判断卖单"""
        sell_price = self.calculate_sell_price(buy_price, current_price)

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
        if is_buy:
            target_price = self.calculate_buy_price(current_price, grid_index)
            diff_pct = abs(order_price - target_price) / target_price * 100

            if diff_pct > self.reprice_threshold:
                logger.debug(
                    "GridStrategy reprice buy old=%s target=%s diff_pct=%.4f threshold=%.4f",
                    order_price,
                    target_price,
                    diff_pct,
                    self.reprice_threshold,
                )
                return target_price

            logger.debug(
                "GridStrategy keep buy old=%s target=%s diff_pct=%.4f threshold=%.4f",
                order_price,
                target_price,
                diff_pct,
                self.reprice_threshold,
            )

        return None

    def calculate_buy_price(self, current_price: float, grid_index: int = 1) -> float:
        """计算买入价格（负偏移，低于市价）"""
        offset = grid_index * self.config.offset_percent / 100.0
        return current_price * (1 - offset)

    def calculate_sell_price(self, buy_price: float, current_price: float) -> float:
        """计算卖出价格（正偏移，高于买入价）"""
        offset = self.config.sell_offset_percent / 100.0
        return buy_price * (1 + offset)
