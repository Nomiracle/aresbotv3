from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any
from enum import Enum
import threading
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    PRICE_UPDATE = "price_update"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FAILED = "order_failed"
    POSITION_CHANGED = "position_changed"
    STREAM_CONNECTED = "stream_connected"
    STREAM_DISCONNECTED = "stream_disconnected"


@dataclass
class Event:
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0


class EventBus:
    """事件总线 - 发布/订阅模式解耦组件通信"""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """订阅事件"""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """取消订阅"""
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type] = [
                    h for h in self._subscribers[event_type] if h != handler
                ]

    def publish(self, event: Event) -> None:
        """发布事件"""
        with self._lock:
            handlers = self._subscribers.get(event.type, []).copy()

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"事件处理异常: {event.type.value}, {e}")

    def clear(self) -> None:
        """清空所有订阅"""
        with self._lock:
            self._subscribers.clear()
