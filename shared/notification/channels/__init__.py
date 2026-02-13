from shared.notification.channels.telegram import TelegramNotifier
from shared.notification.channels.dingtalk import DingTalkNotifier
from shared.notification.channels.feishu import FeishuNotifier
from shared.notification.base import BaseNotifier
from typing import Type

CHANNEL_REGISTRY: dict[str, Type[BaseNotifier]] = {
    "telegram": TelegramNotifier,
    "dingtalk": DingTalkNotifier,
    "feishu": FeishuNotifier,
}

__all__ = [
    "CHANNEL_REGISTRY",
    "TelegramNotifier",
    "DingTalkNotifier",
    "FeishuNotifier",
]
