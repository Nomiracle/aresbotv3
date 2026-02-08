"""Multi-strategy engine manager."""
import asyncio
import concurrent.futures
from typing import Dict, Optional

from ..config import ExchangeConfig, TradingConfig, RiskSettings
from ..core.state_store import StateStore
from ..db.models import ExchangeAccount, Strategy
from ..domain.risk_manager import RiskManager, RiskConfig
from .trading_engine import TradingEngine
from ..exchanges.binance_spot import BinanceSpot
from ..strategies.grid_strategy import GridStrategy
from ..utils.crypto import decrypt_api_secret
from ..utils.logger import get_logger


class EngineManager:
    """Manages multiple trading engine instances."""

    def __init__(self, db_path: str = "trades.db"):
        self._engines: Dict[int, TradingEngine] = {}
        self._futures: Dict[int, concurrent.futures.Future] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=20)
        self._db_path = db_path
        self._logger = get_logger()

    def _create_engine(
        self,
        strategy: Strategy,
        account: ExchangeAccount,
    ) -> TradingEngine:
        """Create a trading engine for a strategy."""
        # Decrypt API credentials
        api_key = decrypt_api_secret(account.api_key)
        api_secret = decrypt_api_secret(account.api_secret)

        # Build configs from strategy
        exchange_config = ExchangeConfig(
            api_key=api_key,
            api_secret=api_secret,
            symbol=strategy.symbol,
            testnet=account.testnet,
        )

        trading_config = TradingConfig(
            symbol=strategy.symbol,
            quantity=float(strategy.base_order_size),
            offset_percent=float(strategy.buy_price_deviation),
            sell_offset_percent=float(strategy.sell_price_deviation),
            order_grid=strategy.grid_levels,
            interval=float(strategy.polling_interval),
            reprice_threshold=float(strategy.price_tolerance),
        )

        risk_settings = RiskSettings(
            stop_loss_percent=float(strategy.stop_loss) if strategy.stop_loss else None,
            stop_loss_delay_seconds=strategy.stop_loss_delay,
            max_position_count=strategy.max_open_positions,
            max_daily_loss=float(strategy.max_daily_drawdown) if strategy.max_daily_drawdown else None,
        )

        # Create exchange instance
        exchange = BinanceSpot(exchange_config)

        # Create strategy instance
        grid_strategy = GridStrategy(trading_config)

        # Create risk manager
        risk_config = RiskConfig(
            stop_loss_percent=risk_settings.stop_loss_percent,
            stop_loss_delay_seconds=risk_settings.stop_loss_delay_seconds,
            max_position_count=risk_settings.max_position_count,
            max_daily_loss=risk_settings.max_daily_loss,
        )
        risk_manager = RiskManager(risk_config)

        # Create state store with strategy-specific path
        state_store = StateStore(f"{self._db_path}_{strategy.id}")

        # Build engine
        engine = TradingEngine(
            strategy=grid_strategy,
            exchange=exchange,
            risk_manager=risk_manager,
            state_store=state_store,
            sync_interval=60,
        )

        return engine

    async def start_strategy(
        self,
        strategy: Strategy,
        account: ExchangeAccount,
    ) -> None:
        """Start a strategy's trading engine."""
        if strategy.id in self._engines:
            self._logger.warning(f"Strategy {strategy.id} already running")
            return

        engine = self._create_engine(strategy, account)
        self._engines[strategy.id] = engine

        # Run engine in thread pool
        future = self._executor.submit(engine.start)
        self._futures[strategy.id] = future

        self._logger.info(f"Started strategy {strategy.id}: {strategy.name}")

    async def stop_strategy(self, strategy_id: int) -> None:
        """Stop a strategy's trading engine."""
        if strategy_id not in self._engines:
            self._logger.warning(f"Strategy {strategy_id} not running")
            return

        engine = self._engines[strategy_id]
        engine.stop()

        # Wait for future to complete
        future = self._futures.get(strategy_id)
        if future:
            try:
                future.result(timeout=5)
            except concurrent.futures.TimeoutError:
                self._logger.warning(f"Strategy {strategy_id} stop timeout")
            except Exception as e:
                self._logger.error(f"Strategy {strategy_id} stop error: {e}")

        self._engines.pop(strategy_id, None)
        self._futures.pop(strategy_id, None)

        self._logger.info(f"Stopped strategy {strategy_id}")

    async def stop_all(self) -> None:
        """Stop all running strategies."""
        strategy_ids = list(self._engines.keys())
        for strategy_id in strategy_ids:
            await self.stop_strategy(strategy_id)
        self._executor.shutdown(wait=False)

    def is_running(self, strategy_id: int) -> bool:
        """Check if a strategy is running."""
        return strategy_id in self._engines

    def get_status(self, strategy_id: int) -> dict:
        """Get status of a strategy."""
        if strategy_id not in self._engines:
            return {"is_running": False}

        engine = self._engines[strategy_id]
        return {
            "is_running": True,
            "current_price": engine.current_price,
            "pending_buys": len(engine.pending_buys),
            "pending_sells": len(engine.pending_sells),
            "position_count": engine.position_tracker.get_position_count(),
        }

    def get_all_status(self) -> Dict[int, dict]:
        """Get status of all strategies."""
        return {
            strategy_id: self.get_status(strategy_id)
            for strategy_id in self._engines
        }
