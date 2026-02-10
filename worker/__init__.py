"""Worker module for AresBot."""
from worker.core.base_exchange import (
    BaseExchange,
    OrderStatus,
    OrderResult,
    ExchangeOrder,
    TradingRules,
)
from worker.core.base_strategy import (
    BaseStrategy,
    Signal,
    TradeDecision,
    StrategyConfig,
)

# Keep worker package import lightweight. TradingEngine pulls optional deps
# (DB drivers, etc.), so expose it only when available.
try:
    from worker.trading_engine import TradingEngine  # type: ignore
except Exception:  # pragma: no cover
    TradingEngine = None  # type: ignore

__all__ = [
    "BaseExchange",
    "OrderStatus",
    "OrderResult",
    "ExchangeOrder",
    "TradingRules",
    "BaseStrategy",
    "Signal",
    "TradeDecision",
    "StrategyConfig",
]

if TradingEngine is not None:
    __all__.append("TradingEngine")
