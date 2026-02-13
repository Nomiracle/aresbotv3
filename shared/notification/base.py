"""通知系统核心抽象"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class NotifyEvent(Enum):
    """通知事件类型"""
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_STOPPED = "strategy_stopped"
    STRATEGY_ERROR = "strategy_error"
    ORDER_FILLED = "order_filled"
    ORDER_FAILED = "order_failed"
    STOP_LOSS_TRIGGERED = "stop_loss_triggered"


# 事件中文标签（前端展示 + 消息格式化）
EVENT_LABELS: dict[str, str] = {
    NotifyEvent.STRATEGY_STARTED.value: "策略启动",
    NotifyEvent.STRATEGY_STOPPED.value: "策略停止",
    NotifyEvent.STRATEGY_ERROR.value: "策略异常",
    NotifyEvent.ORDER_FILLED.value: "订单成交",
    NotifyEvent.ORDER_FAILED.value: "下单失败",
    NotifyEvent.STOP_LOSS_TRIGGERED.value: "止损触发",
}


@dataclass
class NotifyMessage:
    """通知消息"""
    event: NotifyEvent
    title: str
    body: str
    user_email: str
    strategy_id: Optional[int] = None
    symbol: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class BaseNotifier(ABC):
    """通知渠道抽象基类"""

    @classmethod
    @abstractmethod
    def channel_type(cls) -> str:
        """渠道标识，如 'telegram', 'dingtalk', 'feishu'"""

    @abstractmethod
    def send(self, message: NotifyMessage) -> bool:
        """发送通知，返回是否成功"""
