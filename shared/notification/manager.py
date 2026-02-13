"""通知调度器 — 限流 + 线程池异步分发"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from shared.notification.base import BaseNotifier, NotifyEvent, NotifyMessage

logger = logging.getLogger(__name__)

# 不同事件的限流窗口（秒），0 表示不限流
RATE_LIMITS: dict[NotifyEvent, int] = {
    NotifyEvent.ORDER_FILLED: 0,
    NotifyEvent.STRATEGY_STARTED: 10,
    NotifyEvent.STRATEGY_STOPPED: 10,
    NotifyEvent.STRATEGY_ERROR: 60,
    NotifyEvent.ORDER_FAILED: 60,
    NotifyEvent.STOP_LOSS_TRIGGERED: 30,
}
DEFAULT_RATE_LIMIT = 60


class NotifierManager:

    def __init__(self, redis_client=None, max_workers: int = 2):
        self._redis = redis_client
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="notifier",
        )
        self._local_dedup: dict[str, float] = {}
        self._lock = threading.Lock()

    def notify(self, message: NotifyMessage, channels: list[BaseNotifier]) -> None:
        """异步发送通知（不阻塞调用方）"""
        if not channels:
            return
        if self._is_rate_limited(message):
            return
        for ch in channels:
            self._executor.submit(self._safe_send, ch, message)

    def _is_rate_limited(self, message: NotifyMessage) -> bool:
        window = RATE_LIMITS.get(message.event, DEFAULT_RATE_LIMIT)
        if window <= 0:
            return False

        dedup_key = f"notify:rl:{message.user_email}:{message.event.value}:{message.strategy_id or 0}"

        if self._redis:
            try:
                if self._redis.client.set(dedup_key, "1", nx=True, ex=window):
                    return False
                return True
            except Exception:
                pass

        now = time.time()
        with self._lock:
            last_sent = self._local_dedup.get(dedup_key, 0)
            if now - last_sent < window:
                return True
            self._local_dedup[dedup_key] = now
            return False

    @staticmethod
    def _safe_send(channel: BaseNotifier, message: NotifyMessage) -> None:
        try:
            channel.send(message)
        except Exception as e:
            logger.warning(
                "通知发送失败 channel=%s event=%s error=%s",
                channel.channel_type(), message.event.value, e,
            )

    def build_channels_from_redis(self, user_email: str) -> list[BaseNotifier]:
        """从 Redis 加载用户通知渠道实例"""
        if not self._redis:
            return []
        try:
            raw = self._redis.client.get(f"notify:channels:{user_email}")
            if not raw:
                return []
            configs = json.loads(raw)
            return _deserialize_channels(configs)
        except Exception as e:
            logger.debug("加载通知渠道失败 user=%s error=%s", user_email, e)
            return []

    def notify_user(self, message: NotifyMessage) -> None:
        """根据 user_email 自动加载渠道并发送（仅发送订阅了该事件的渠道）"""
        if not self._redis:
            return
        try:
            raw = self._redis.client.get(f"notify:channels:{message.user_email}")
            if not raw:
                return
            configs = json.loads(raw)
        except Exception:
            return

        channels = []
        for cfg in configs:
            if not cfg.get("is_active", True):
                continue
            enabled = cfg.get("enabled_events") or []
            if enabled and message.event.value not in enabled:
                continue
            ch = _make_channel(cfg)
            if ch:
                channels.append(ch)

        self.notify(message, channels)


def _deserialize_channels(configs: list[dict]) -> list[BaseNotifier]:
    channels = []
    for cfg in configs:
        if not cfg.get("is_active", True):
            continue
        ch = _make_channel(cfg)
        if ch:
            channels.append(ch)
    return channels


def _make_channel(cfg: dict) -> Optional[BaseNotifier]:
    from shared.notification.channels import CHANNEL_REGISTRY
    channel_type = cfg.get("channel_type", "")
    cls = CHANNEL_REGISTRY.get(channel_type)
    if not cls:
        return None
    try:
        channel_config = cfg.get("config", {})
        return cls(**channel_config)
    except Exception:
        return None


_manager: Optional[NotifierManager] = None


def get_notifier_manager() -> NotifierManager:
    global _manager
    if _manager is None:
        from shared.core.redis_client import get_redis_client
        try:
            redis_client = get_redis_client()
        except Exception:
            redis_client = None
        _manager = NotifierManager(redis_client=redis_client)
    return _manager
