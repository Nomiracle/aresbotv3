"""AresBot V3 - 简洁、解耦、稳定的交易系统"""

from .core import (
    BaseStrategy,
    BaseExchange,
    EventBus,
    StateStore,
    TradeDecision,
    Signal,
    StrategyConfig,
    OrderResult,
    OrderStatus,
)
from .domain import Order, OrderState, PositionTracker, RiskManager, RiskConfig
from .engine import TradingEngine, PositionSyncer, EngineManager
from .strategies import GridStrategy
from .utils import with_retry, setup_logger

__version__ = "3.0.0"

__all__ = [
    # Core
    "BaseStrategy",
    "BaseExchange",
    "EventBus",
    "StateStore",
    "TradeDecision",
    "Signal",
    "StrategyConfig",
    "OrderResult",
    "OrderStatus",
    # Domain
    "Order",
    "OrderState",
    "PositionTracker",
    "RiskManager",
    "RiskConfig",
    # Engine
    "TradingEngine",
    "PositionSyncer",
    "EngineManager",
    # Strategies
    "GridStrategy",
    # Utils
    "with_retry",
    "setup_logger",
]
