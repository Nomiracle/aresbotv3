"""Strategy execution Celery task."""
from dataclasses import dataclass
import signal
import socket
import time
from typing import Any, Dict

from celery import Task

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from worker.celery_app import app
from shared.config import TradingConfig
from shared.core.redis_client import get_redis_client
from shared.utils.crypto import decrypt_api_secret
from shared.utils.logger import get_logger
from worker.state_store import StateStore
from worker.domain.risk_manager import RiskManager, RiskConfig
from worker.engine.trading_engine import TradingEngine
from worker.exchanges.binance_spot import BinanceSpot
from worker.strategies.grid_strategy import GridStrategy


logger = get_logger("celery.task")


@dataclass(frozen=True)
class TaskRuntime:
    strategy_id: int
    task_id: str
    worker_ip: str
    worker_hostname: str


def _cleanup_runtime(redis_client, strategy_id: int) -> None:
    redis_client.release_lock(strategy_id)
    redis_client.clear_running_info(strategy_id)


def _persist_runtime_status(redis_client, strategy_id: int, status: Dict[str, Any]) -> None:
    redis_client.update_running_status(
        strategy_id=strategy_id,
        current_price=status.get("current_price"),
        pending_buys=status.get("pending_buys"),
        pending_sells=status.get("pending_sells"),
        position_count=status.get("position_count"),
    )


def get_worker_ip() -> str:
    """Get the IP address of the current worker."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())


def _is_task_active(task_id: str) -> bool:
    """Check whether a Celery task is still active/reserved/scheduled."""
    if not task_id:
        return False

    inspect = app.control.inspect()
    states = [
        inspect.active() or {},
        inspect.reserved() or {},
        inspect.scheduled() or {},
    ]

    for payload in states:
        for tasks in payload.values():
            for task in tasks:
                if task.get("id") == task_id:
                    return True
    return False


class StrategyTask(Task):
    """Custom Celery task for strategy execution."""

    name = "worker.tasks.strategy_task.run_strategy"
    max_retries = 0  # No retries for long-running tasks

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Handle task failure."""
        strategy_id = kwargs.get("strategy_id") or (args[0] if args else None)
        if strategy_id:
            redis_client = get_redis_client()
            redis_client.update_running_status(
                strategy_id=strategy_id,
                status="error",
                last_error=str(exc),
            )
            _cleanup_runtime(redis_client, strategy_id)
        logger.error(f"Strategy task {task_id} failed: {exc}")


@app.task(base=StrategyTask, bind=True)
def run_strategy(
    self,
    strategy_id: int,
    account_data: Dict[str, Any],
    strategy_config: Dict[str, Any],
    strategy_runtime: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Run a trading strategy as a Celery task.

    Args:
        strategy_id: Database ID of the strategy
        account_data: Exchange account credentials and settings
        strategy_config: Strategy configuration parameters

    Returns:
        Task result with execution statistics
    """
    runtime = TaskRuntime(
        strategy_id=strategy_id,
        task_id=self.request.id,
        worker_ip=get_worker_ip(),
        worker_hostname=socket.gethostname(),
    )
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    redis_client = get_redis_client()

    # 1. Try to acquire distributed lock
    if not redis_client.acquire_lock(runtime.strategy_id, runtime.task_id):
        existing_task = redis_client.get_lock_holder(strategy_id)

        # stale lock cleanup: lock holder task no longer exists in cluster
        if existing_task and not _is_task_active(existing_task):
            logger.warning(
                f"Strategy {strategy_id} lock holder task {existing_task} not active, cleaning stale lock"
            )
            redis_client.release_lock(strategy_id)

            if redis_client.acquire_lock(runtime.strategy_id, runtime.task_id):
                logger.info(f"Strategy {strategy_id} acquired lock after stale cleanup")
            else:
                current_holder = redis_client.get_lock_holder(strategy_id)
                logger.info(
                    f"Skip duplicate start for strategy {strategy_id}, lock holder={current_holder}"
                )
                return {
                    "strategy_id": runtime.strategy_id,
                    "task_id": runtime.task_id,
                    "status": "skipped_already_running",
                    "existing_task_id": current_holder,
                }
        else:
            logger.info(
                f"Skip duplicate start for strategy {strategy_id}, lock holder={existing_task}"
            )
            return {
                "strategy_id": runtime.strategy_id,
                "task_id": runtime.task_id,
                "status": "skipped_already_running",
                "existing_task_id": existing_task,
            }

    # 2. Save running instance info to Redis
    runtime_data = strategy_runtime or {}

    redis_client.set_running_info(
        strategy_id=runtime.strategy_id,
        task_id=runtime.task_id,
        worker_ip=runtime.worker_ip,
        worker_hostname=runtime.worker_hostname,
        status="running",
        user_email=runtime_data.get("user_email"),
        strategy_snapshot=runtime_data.get("strategy_snapshot", {}),
        runtime_config=runtime_data.get("runtime_config", strategy_config),
    )

    logger.info(
        f"Starting strategy {runtime.strategy_id} on worker {runtime.worker_hostname} ({runtime.worker_ip}), "
        f"task_id={runtime.task_id}"
    )

    try:
        # 3. Build and run the trading engine
        engine = _create_engine(strategy_id, account_data, strategy_config, redis_client)

        def _handle_sigterm(signum, frame):
            logger.info(f"Strategy {strategy_id} received SIGTERM, stopping engine")
            try:
                engine.stop()
            except Exception as err:
                logger.error(f"Strategy {strategy_id} stop on SIGTERM failed: {err}")

        signal.signal(signal.SIGTERM, _handle_sigterm)
        engine.start()

        signal.signal(signal.SIGTERM, previous_sigterm_handler)

        return {
            "strategy_id": runtime.strategy_id,
            "task_id": runtime.task_id,
            "worker_ip": runtime.worker_ip,
            "status": "stopped",
        }

    except Exception as e:
        logger.error(f"Strategy {strategy_id} error: {e}")
        redis_client.update_running_status(
            strategy_id=strategy_id,
            status="error",
            last_error=str(e),
        )
        raise

    finally:
        try:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
        except Exception:
            pass

        # 4. Cleanup
        _cleanup_runtime(redis_client, strategy_id)
        logger.info(f"Strategy {strategy_id} stopped and cleaned up")


def _create_engine(
    strategy_id: int,
    account_data: Dict[str, Any],
    strategy_config: Dict[str, Any],
    redis_client,
) -> TradingEngine:
    """Create a trading engine instance."""
    # Decrypt API credentials
    api_key = decrypt_api_secret(account_data["api_key"])
    api_secret = decrypt_api_secret(account_data["api_secret"])

    # Build trading config
    trading_config = TradingConfig(
        symbol=strategy_config["symbol"],
        quantity=float(strategy_config["base_order_size"]),
        offset_percent=float(strategy_config["buy_price_deviation"]),
        sell_offset_percent=float(strategy_config["sell_price_deviation"]),
        order_grid=strategy_config["grid_levels"],
        interval=float(strategy_config["polling_interval"]),
        reprice_threshold=float(strategy_config["price_tolerance"]),
    )

    # Build risk settings
    risk_config = RiskConfig(
        stop_loss_percent=float(strategy_config["stop_loss"]) if strategy_config.get("stop_loss") else None,
        stop_loss_delay_seconds=strategy_config.get("stop_loss_delay"),
        max_position_count=strategy_config["max_open_positions"],
        max_daily_loss=float(strategy_config["max_daily_drawdown"]) if strategy_config.get("max_daily_drawdown") else None,
    )

    # Create instances
    exchange = BinanceSpot(
        api_key=api_key,
        api_secret=api_secret,
        symbol=strategy_config["symbol"],
        testnet=account_data.get("testnet", False),
    )
    grid_strategy = GridStrategy(trading_config)
    risk_manager = RiskManager(risk_config)
    state_store = StateStore(f"trades_{strategy_id}.db")

    # Build engine
    engine = TradingEngine(
        strategy=grid_strategy,
        exchange=exchange,
        risk_manager=risk_manager,
        state_store=state_store,
        sync_interval=60,
    )

    # Set up status update callback
    def on_status_update(status: Dict[str, Any]) -> None:
        _persist_runtime_status(redis_client, strategy_id, status)

    engine.on_status_update = on_status_update

    return engine
