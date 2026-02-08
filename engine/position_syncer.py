from typing import Dict, List, Optional
import logging

from ..core import BaseExchange, ExchangeOrder
from ..domain import Order, PositionTracker

logger = logging.getLogger(__name__)


class PositionSyncer:
    """仓位同步器 - 缓存与交易所同步、防抖、自动补单"""

    def __init__(
        self,
        exchange: BaseExchange,
        position_tracker: PositionTracker,
        missing_threshold: int = 2,
    ):
        self.exchange = exchange
        self.position_tracker = position_tracker
        self.missing_threshold = missing_threshold
        self._missing_counts: Dict[str, int] = {}

    def sync(self, pending_sells: Dict[str, Order]) -> None:
        """执行同步"""
        try:
            exchange_orders = self.exchange.get_open_orders()

            if not exchange_orders and pending_sells:
                logger.warning("交易所返回空订单列表，跳过同步")
                return

            self._sync_orders(pending_sells, exchange_orders)
            self._sync_positions(pending_sells)

        except Exception as e:
            logger.error(f"同步异常: {e}")

    def _sync_orders(
        self,
        pending_sells: Dict[str, Order],
        exchange_orders: List[ExchangeOrder],
    ) -> None:
        """同步订单缓存与交易所"""
        exchange_order_ids = {o.order_id for o in exchange_orders}

        for order_id in list(pending_sells.keys()):
            if order_id not in exchange_order_ids:
                self._missing_counts[order_id] = self._missing_counts.get(order_id, 0) + 1

                if self._missing_counts[order_id] >= self.missing_threshold:
                    logger.info(f"订单连续缺失{self.missing_threshold}轮，标记删除: {order_id}")
                    del self._missing_counts[order_id]
            else:
                self._missing_counts.pop(order_id, None)

    def _sync_positions(self, pending_sells: Dict[str, Order]) -> None:
        """同步持仓与卖单"""
        positions = self.position_tracker.get_all_positions()

        sell_related_ids = {
            order.related_order_id
            for order in pending_sells.values()
            if order.related_order_id
        }

        positions_without_sells = [
            pos for pos in positions if pos.order_id not in sell_related_ids
        ]

        if positions_without_sells:
            logger.warning(
                f"发现{len(positions_without_sells)}个持仓无对应卖单，需要补单"
            )
            for pos in positions_without_sells:
                logger.info(f"持仓无卖单: order_id={pos.order_id}, qty={pos.quantity}")

        position_order_ids = {pos.order_id for pos in positions}
        excess_sells = [
            order
            for order in pending_sells.values()
            if order.related_order_id and order.related_order_id not in position_order_ids
        ]

        if excess_sells:
            logger.warning(f"发现{len(excess_sells)}个多余卖单，需要取消")
            for order in excess_sells:
                logger.info(f"多余卖单: order_id={order.order_id}")

    def get_positions_without_sells(
        self, pending_sells: Dict[str, Order]
    ) -> List:
        """获取没有对应卖单的持仓"""
        positions = self.position_tracker.get_all_positions()
        sell_related_ids = {
            order.related_order_id
            for order in pending_sells.values()
            if order.related_order_id
        }
        return [pos for pos in positions if pos.order_id not in sell_related_ids]

    def get_excess_sells(
        self, pending_sells: Dict[str, Order]
    ) -> List[Order]:
        """获取多余的卖单"""
        positions = self.position_tracker.get_all_positions()
        position_order_ids = {pos.order_id for pos in positions}
        return [
            order
            for order in pending_sells.values()
            if order.related_order_id and order.related_order_id not in position_order_ids
        ]

    def clear_missing_counts(self) -> None:
        """清空缺失计数"""
        self._missing_counts.clear()
