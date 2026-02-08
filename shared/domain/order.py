from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import threading


class OrderState(Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


# 状态转换规则
_TRANSITIONS = {
    OrderState.PENDING: {OrderState.PLACED, OrderState.FAILED},
    OrderState.PLACED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
    },
}


@dataclass
class Order:
    """订单实体 - 内置状态机和锁保护"""

    order_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    grid_index: int = 1
    state: OrderState = OrderState.PENDING
    filled_quantity: float = 0
    filled_price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    related_order_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    @property
    def is_active(self) -> bool:
        return self.state in {
            OrderState.PENDING,
            OrderState.PLACED,
            OrderState.PARTIALLY_FILLED,
        }

    @property
    def is_filled(self) -> bool:
        return self.state == OrderState.FILLED

    @property
    def is_buy(self) -> bool:
        return self.side == "buy"

    def can_transition_to(self, new_state: OrderState) -> bool:
        """检查是否可以转换到新状态"""
        allowed = _TRANSITIONS.get(self.state, set())
        return new_state in allowed

    def transition_to(
        self,
        new_state: OrderState,
        filled_quantity: Optional[float] = None,
        filled_price: Optional[float] = None,
    ) -> bool:
        """原子状态转换"""
        with self._lock:
            if not self.can_transition_to(new_state):
                return False

            self.state = new_state
            self.updated_at = datetime.now()

            if filled_quantity is not None:
                self.filled_quantity = filled_quantity
            if filled_price is not None:
                self.filled_price = filled_price

            return True

    def update_fill(self, filled_qty: float, filled_price: float) -> OrderState:
        """更新成交信息，返回新状态"""
        with self._lock:
            self.filled_quantity = filled_qty
            self.filled_price = filled_price
            self.updated_at = datetime.now()

            if filled_qty >= self.quantity:
                self.state = OrderState.FILLED
            elif filled_qty > 0:
                self.state = OrderState.PARTIALLY_FILLED

            return self.state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "grid_index": self.grid_index,
            "state": self.state.value,
            "filled_quantity": self.filled_quantity,
            "filled_price": self.filled_price,
            "remaining_quantity": self.remaining_quantity,
            "created_at": self.created_at.isoformat(),
            "related_order_id": self.related_order_id,
        }
