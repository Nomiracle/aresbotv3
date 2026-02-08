from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import threading


@dataclass
class PositionEntry:
    """单个持仓条目"""

    symbol: str
    quantity: float
    entry_price: float
    order_id: str
    grid_index: int
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def cost(self) -> float:
        return self.quantity * self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.quantity


class PositionTracker:
    """仓位追踪器 - 管理内存中的持仓"""

    def __init__(self):
        self._positions: Dict[str, PositionEntry] = {}
        self._lock = threading.Lock()

    def add_position(
        self,
        order_id: str,
        symbol: str,
        quantity: float,
        entry_price: float,
        grid_index: int,
    ) -> None:
        """添加持仓（买单成交时调用）"""
        with self._lock:
            self._positions[order_id] = PositionEntry(
                symbol=symbol,
                quantity=quantity,
                entry_price=entry_price,
                order_id=order_id,
                grid_index=grid_index,
            )

    def remove_position(self, order_id: str) -> Optional[PositionEntry]:
        """移除持仓（卖单成交时调用）"""
        with self._lock:
            return self._positions.pop(order_id, None)

    def get_position(self, order_id: str) -> Optional[PositionEntry]:
        """获取单个持仓"""
        with self._lock:
            return self._positions.get(order_id)

    def get_all_positions(self, symbol: Optional[str] = None) -> List[PositionEntry]:
        """获取所有持仓"""
        with self._lock:
            positions = list(self._positions.values())
            if symbol:
                positions = [p for p in positions if p.symbol == symbol]
            return positions

    def get_total_quantity(self, symbol: str) -> float:
        """获取指定交易对的总持仓量"""
        with self._lock:
            return sum(
                p.quantity for p in self._positions.values() if p.symbol == symbol
            )

    def get_total_cost(self, symbol: str) -> float:
        """获取指定交易对的总成本"""
        with self._lock:
            return sum(p.cost for p in self._positions.values() if p.symbol == symbol)

    def get_unrealized_pnl(self, symbol: str, current_price: float) -> float:
        """获取未实现盈亏"""
        with self._lock:
            return sum(
                p.unrealized_pnl(current_price)
                for p in self._positions.values()
                if p.symbol == symbol
            )

    def get_position_count(self, symbol: Optional[str] = None) -> int:
        """获取持仓数量"""
        with self._lock:
            if symbol:
                return sum(1 for p in self._positions.values() if p.symbol == symbol)
            return len(self._positions)

    def clear(self, symbol: Optional[str] = None) -> None:
        """清空持仓"""
        with self._lock:
            if symbol:
                self._positions = {
                    k: v for k, v in self._positions.items() if v.symbol != symbol
                }
            else:
                self._positions.clear()
