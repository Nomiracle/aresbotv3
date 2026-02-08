"""AresBot V3 应用入口"""

import logging
from typing import Type

from .config import AppConfig
from .core import BaseExchange, StrategyConfig, StateStore
from .domain import RiskManager, RiskConfig
from .engine import TradingEngine
from .strategies import GridStrategy
from .utils import setup_logger


def create_engine(
    config: AppConfig,
    exchange_class: Type[BaseExchange],
) -> TradingEngine:
    """创建交易引擎

    Args:
        config: 应用配置
        exchange_class: 交易所实现类

    Returns:
        TradingEngine 实例
    """
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    setup_logger("aresbot", level=log_level)

    exchange = exchange_class(
        api_key=config.exchange.api_key,
        api_secret=config.exchange.api_secret,
        symbol=config.exchange.symbol,
        testnet=config.exchange.testnet,
    )

    strategy_config = StrategyConfig(
        symbol=config.trading.symbol,
        quantity=config.trading.quantity,
        offset_percent=config.trading.offset_percent,
        sell_offset_percent=config.trading.sell_offset_percent,
        order_grid=config.trading.order_grid,
        interval=config.trading.interval,
    )
    strategy = GridStrategy(
        config=strategy_config,
        reprice_threshold=config.trading.reprice_threshold,
    )

    risk_config = RiskConfig(
        stop_loss_percent=config.risk.stop_loss_percent,
        stop_loss_delay_seconds=config.risk.stop_loss_delay_seconds,
        max_loss_count=config.risk.max_loss_count,
        loss_window_seconds=config.risk.loss_window_seconds,
        cooldown_seconds=config.risk.cooldown_seconds,
        max_position_count=config.risk.max_position_count,
        max_daily_loss=config.risk.max_daily_loss,
    )
    risk_manager = RiskManager(risk_config)

    state_store = StateStore(db_path=config.db_path)

    engine = TradingEngine(
        strategy=strategy,
        exchange=exchange,
        risk_manager=risk_manager,
        state_store=state_store,
        sync_interval=config.sync_interval,
    )

    return engine


def run(config: AppConfig, exchange_class: Type[BaseExchange]) -> None:
    """运行交易机器人

    Args:
        config: 应用配置
        exchange_class: 交易所实现类
    """
    engine = create_engine(config, exchange_class)

    try:
        engine.start()
    except KeyboardInterrupt:
        engine.stop()
