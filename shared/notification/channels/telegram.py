"""Telegram 通知渠道"""
import requests

from shared.notification.base import BaseNotifier, NotifyMessage, EVENT_LABELS


class TelegramNotifier(BaseNotifier):

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @classmethod
    def channel_type(cls) -> str:
        return "telegram"

    def send(self, message: NotifyMessage) -> bool:
        text = self._format(message)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _format(msg: NotifyMessage) -> str:
        label = EVENT_LABELS.get(msg.event.value, msg.event.value)
        lines = [f"<b>[{label}] {msg.title}</b>"]
        if msg.symbol:
            tag = f"{msg.exchange} | {msg.symbol}" if msg.exchange else msg.symbol
            lines.append(tag)
        lines.append(msg.body)
        lines.append(msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)
