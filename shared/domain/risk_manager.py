from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from datetime import datetime, timedelta
import threading


@dataclass
class RiskConfig:
    """风控配置"""

    stop_loss_percent: Optional[float] = None
    stop_loss_delay_seconds: Optional[int] = None
    max_loss_count: int = 3
    loss_window_seconds: int = 300
    cooldown_seconds: int = 3600
    max_position_count: int = 10
    max_daily_loss: Optional[float] = None


class RiskManager:
    """风控管理器"""

    def __init__(self, config: RiskConfig):
        self.config = config
        self._loss_trades: List[Tuple[datetime, float]] = []
        self._cooldown_until: Optional[datetime] = None
        self._daily_loss: float = 0
        self._daily_reset_date: datetime = datetime.now().date()
        self._lock = threading.Lock()

    def can_open_position(self, current_position_count: int) -> Tuple[bool, str]:
        """检查是否允许开仓"""
        with self._lock:
            self._reset_daily_if_needed()

            if self._is_in_cooldown():
                remaining = (self._cooldown_until - datetime.now()).seconds
                return False, f"冷却期中，剩余{remaining}秒"

            if current_position_count >= self.config.max_position_count:
                return False, f"持仓数量已达上限{self.config.max_position_count}"

            if self.config.max_daily_loss and self._daily_loss >= self.config.max_daily_loss:
                return False, f"日亏损已达上限{self.config.max_daily_loss}"

            return True, "允许开仓"

    def check_stop_loss(
        self,
        entry_price: float,
        current_price: float,
        entry_time: datetime,
    ) -> Tuple[bool, str]:
        """检查是否需要止损"""
        if self.config.stop_loss_percent:
            loss_pct = (entry_price - current_price) / entry_price * 100
            if loss_pct >= self.config.stop_loss_percent:
                return True, f"价格止损触发，亏损{loss_pct:.2f}%"

        if self.config.stop_loss_delay_seconds:
            elapsed = (datetime.now() - entry_time).total_seconds()
            if elapsed > self.config.stop_loss_delay_seconds:
                return True, f"时间止损触发，持仓{elapsed:.0f}秒"

        return False, ""

    def record_trade_result(self, pnl: float) -> None:
        """记录交易结果"""
        with self._lock:
            self._reset_daily_if_needed()

            if pnl < 0:
                self._loss_trades.append((datetime.now(), pnl))
                self._daily_loss += abs(pnl)
                self._clean_old_trades()
                self._check_cooldown_trigger()

    def _is_in_cooldown(self) -> bool:
        if self._cooldown_until is None:
            return False
        if datetime.now() >= self._cooldown_until:
            self._cooldown_until = None
            return False
        return True

    def _clean_old_trades(self) -> None:
        cutoff = datetime.now() - timedelta(seconds=self.config.loss_window_seconds)
        self._loss_trades = [(t, p) for t, p in self._loss_trades if t > cutoff]

    def _check_cooldown_trigger(self) -> None:
        if len(self._loss_trades) >= self.config.max_loss_count:
            self._cooldown_until = datetime.now() + timedelta(
                seconds=self.config.cooldown_seconds
            )
            self._loss_trades.clear()

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now().date()
        if today > self._daily_reset_date:
            self._daily_loss = 0
            self._daily_reset_date = today

    def get_status(self) -> dict:
        """获取风控状态"""
        with self._lock:
            return {
                "in_cooldown": self._is_in_cooldown(),
                "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
                "recent_losses": len(self._loss_trades),
                "daily_loss": self._daily_loss,
            }
