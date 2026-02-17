"""飞书自定义机器人通知渠道"""
import hashlib
import hmac
import base64
import time

import requests

from shared.notification.base import BaseNotifier, NotifyMessage, EVENT_LABELS


class FeishuNotifier(BaseNotifier):

    def __init__(self, webhook_url: str, secret: str = ""):
        self.webhook_url = webhook_url
        self.secret = secret

    @classmethod
    def channel_type(cls) -> str:
        return "feishu"

    def send(self, message: NotifyMessage) -> bool:
        text = self._format(message)
        payload: dict = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if self.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        try:
            resp = requests.post(
                self.webhook_url, json=payload, timeout=10,
            )
            data = resp.json()
            return data.get("code") == 0
        except Exception:
            return False

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256,
        ).digest()
    @staticmethod
    def _format(msg: NotifyMessage) -> str:
        label = EVENT_LABELS.get(msg.event.value, msg.event.value)
        lines = [f"[{label}] {msg.title}"]
        if msg.symbol:
            tag = f"{msg.exchange} | {msg.symbol}" if msg.exchange else msg.symbol
            lines.append(tag)
        lines.append(msg.body)
        lines.append(msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)
