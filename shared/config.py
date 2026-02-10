from dataclasses import dataclass
from typing import Optional

from worker.core.base_strategy import StrategyConfig


@dataclass
class ExchangeConfig:
    api_key: str
    api_secret: str
    symbol: str
    testnet: bool = False


@dataclass
class RiskSettings:
    stop_loss_percent: Optional[float] = None
    stop_loss_delay_seconds: Optional[int] = None
    max_loss_count: int = 3
    loss_window_seconds: int = 300
    cooldown_seconds: int = 3600
    max_position_count: int = 10
    max_daily_loss: Optional[float] = None


@dataclass
class AppConfig:
    exchange: ExchangeConfig
    trading: StrategyConfig
    risk: RiskSettings
    db_path: str = "trades.db"
    sync_interval: int = 60
    log_level: str = "INFO"
