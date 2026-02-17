"""钉钉自定义机器人通知渠道"""
import hashlib
import hmac
import base64
import time
import urllib.parse

import requests

from shared.notification.base import BaseNotifier, NotifyMessage, EVENT_LABELS

DINGTALK_BASE_URL = "https://oapi.dingtalk.com/robot/send?access_token="


class DingTalkNotifier(BaseNotifier):

    def __init__(
        self,
        access_token: str = "",
        webhook_url: str = "",
        secret: str = "",
        keyword: str = "ares",
    ):
        # 兼容两种配置方式：access_token 或完整 webhook_url
        if access_token:
            self.webhook_url = DINGTALK_BASE_URL + access_token
        elif webhook_url:
            self.webhook_url = webhook_url
        else:
            self.webhook_url = ""
        self.secret = secret
        self.keyword = keyword

    @classmethod
    def channel_type(cls) -> str:
        return "dingtalk"

    def send(self, message: NotifyMessage) -> bool:
        if not self.webhook_url:
            return False
        url = self._sign_url() if self.secret else self.webhook_url
        text = self._format(message)
        # 钉钉机器人关键字安全设置：消息中需包含关键字
        if self.keyword:
            text = f"[{self.keyword}] {text}"
        try:
            resp = requests.post(url, json={
                "msgtype": "text",
                "text": {"content": text},
            }, timeout=10)
            data = resp.json()
            return data.get("errcode") == 0
        except Exception:
            return False

    def _sign_url(self) -> str:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

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
