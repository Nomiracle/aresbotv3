"""Strategy execution Celery task."""
from dataclasses import dataclass
import json
import signal
import threading
import time
from typing import Any, Dict, Optional

from celery import Task

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from worker.celery_app import app
from worker.core.base_strategy import StrategyConfig
from shared.core.redis_client import get_redis_client
from shared.utils.crypto import decrypt_api_secret
from shared.utils.logger import get_logger
from shared.utils.network import get_worker_network_identity
from worker.db import TradeStore
from worker.domain.risk_manager import RiskManager, RiskConfig
from worker.trading_engine import TradingEngine
from worker.exchanges.futures import ExchangeFutures, FUTURES_EXCHANGE_IDS
from worker.exchanges.spot import ExchangeSpot
from worker.strategies.grid_strategy import GridStrategy


logger = get_logger("celery.task")


def _mask_credential(value: str, keep_start: int = 6, keep_end: int = 4) -> str:
    text = (value or "").strip()
    if not text:
        return "<empty>"

    keep_start = max(keep_start, 0)
    keep_end = max(keep_end, 0)
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)

    return f"{text[:keep_start]}{'*' * 6}{text[-keep_end:]}"


@dataclass(frozen=True)
class TaskRuntime:
    strategy_id: int
    task_id: str
    worker_ip: str
    worker_hostname: str
    worker_private_ip: str
    worker_public_ip: str
    worker_ip_location: str


class StrategyStopWatcher:
    """Hybrid stop watcher: pub/sub fast-path + Redis fallback polling."""

    def __init__(
        self,
        redis_client,
        strategy_id: int,
        task_id: str,
        poll_interval_seconds: Optional[float] = None,
    ) -> None:
        self._redis_client = redis_client
        self._strategy_id = strategy_id
        self._task_id = task_id
        env_poll_interval = os.environ.get("STRATEGY_STOP_POLL_INTERVAL", "0.8")
        if poll_interval_seconds is not None:
            base_poll_interval = poll_interval_seconds
        else:
            try:
                base_poll_interval = float(env_poll_interval)
            except ValueError:
                base_poll_interval = 0.8

        self._poll_interval_seconds = max(base_poll_interval, 0.2)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_shutdown = threading.Event()
        self._last_poll_at = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread_shutdown.clear()
        self._thread = threading.Thread(
            target=self._watch_pubsub_loop,
            name=f"strategy-stop-watcher-{self._strategy_id}",
            daemon=True,
        )
        self._thread.start()

    def should_stop(self) -> bool:
        if self._stop_event.is_set():
            return True

        now = time.monotonic()
        if now - self._last_poll_at < self._poll_interval_seconds:
            return False

        self._last_poll_at = now
        should_stop = _should_stop_task(
            self._redis_client,
            self._strategy_id,
            self._task_id,
        )
        if should_stop:
            self._stop_event.set()

        return should_stop

    def stop(self) -> None:
        self._thread_shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _watch_pubsub_loop(self) -> None:
        pubsub = None
        try:
            pubsub = self._redis_client.create_strategy_stop_pubsub(self._strategy_id)
            while not self._thread_shutdown.is_set() and not self._stop_event.is_set():
                message = pubsub.get_message(timeout=0.5)
                if not message:
                    continue

                if message.get("type") != "message":
                    continue

                raw_data = message.get("data")
                payload = _decode_stop_payload(raw_data)
                if not payload:
                    continue

                payload_task_id = str(payload.get("task_id") or "")
                if payload_task_id and payload_task_id != self._task_id:
                    continue

                self._stop_event.set()
                return
        except Exception as err:
            logger.debug(
                "Stop watcher pubsub loop error strategy=%s task=%s error=%s",
                self._strategy_id,
                self._task_id,
                err,
            )
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass


def _decode_stop_payload(raw_data: Any) -> Optional[Dict[str, Any]]:
    if raw_data is None:
        return None

    if isinstance(raw_data, bytes):
        try:
            raw_data = raw_data.decode("utf-8")
        except Exception:
            return None

    if isinstance(raw_data, str):
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    if isinstance(raw_data, dict):
        return raw_data

    return None


def _cleanup_runtime(redis_client, strategy_id: int, task_id: str | None) -> None:
    if task_id:
        redis_client.cleanup_runtime_if_task(strategy_id, task_id)
        return

    redis_client.release_lock(strategy_id)
    redis_client.clear_running_info(strategy_id)


def _persist_runtime_status(redis_client, strategy_id: int, status: Dict[str, Any]) -> None:
    redis_client.update_running_status(
        strategy_id=strategy_id,
        exchange=status.get("exchange"),
        current_price=status.get("current_price"),
        pending_buys=status.get("pending_buys"),
        pending_sells=status.get("pending_sells"),
        position_count=status.get("position_count"),
        buy_orders=status.get("buy_orders"),
        sell_orders=status.get("sell_orders"),
        last_error=status.get("last_error") or "",
        extra_status=status.get("extra_status"),
    )


def _should_stop_task(redis_client, strategy_id: int, task_id: str) -> bool:
    return redis_client.should_stop_strategy_task(strategy_id=strategy_id, task_id=task_id)


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
            _cleanup_runtime(redis_client, strategy_id, task_id)
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
    network_identity = get_worker_network_identity()
    runtime = TaskRuntime(
        strategy_id=strategy_id,
        task_id=self.request.id,
        worker_ip=network_identity.worker_ip,
        worker_hostname=network_identity.hostname,
        worker_private_ip=network_identity.private_ip,
        worker_public_ip=network_identity.public_ip,
        worker_ip_location=network_identity.ip_location,
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
        worker_private_ip=runtime.worker_private_ip,
        worker_public_ip=runtime.worker_public_ip,
        worker_ip_location=runtime.worker_ip_location,
        status="running",
        user_email=runtime_data.get("user_email"),
        strategy_snapshot=runtime_data.get("strategy_snapshot", {}),
        runtime_config=runtime_data.get("runtime_config", strategy_config),
    )

    logger.info(
        f"Starting strategy {runtime.strategy_id} on worker {runtime.worker_hostname} ({runtime.worker_ip}), "
        f"task_id={runtime.task_id}"
    )

    # 只在主线程中注册信号处理器
    is_main_thread = threading.current_thread() is threading.main_thread()

    engine: TradingEngine | None = None
    stop_watcher: StrategyStopWatcher | None = None
    try:
        # 3. Build and run the trading engine
        user_email = runtime_data.get("user_email", "")
        _sync_notify_channels_to_redis(redis_client, user_email)
        engine = _create_engine(
            strategy_id=runtime.strategy_id,
            account_data=account_data,
            strategy_config=strategy_config,
            redis_client=redis_client,
            user_email=user_email,
        )
        stop_watcher = StrategyStopWatcher(
            redis_client=redis_client,
            strategy_id=runtime.strategy_id,
            task_id=runtime.task_id,
        )
        stop_watcher.start()
        engine.should_stop = stop_watcher.should_stop

        def _handle_sigterm(signum, frame):
            logger.info(f"Strategy {strategy_id} received SIGTERM, stopping engine")
            try:
                if engine:
                    engine.stop()
            except Exception as err:
                logger.error(f"Strategy {strategy_id} stop on SIGTERM failed: {err}")

        if is_main_thread:
            signal.signal(signal.SIGTERM, _handle_sigterm)

        _send_lifecycle_notify(user_email, strategy_id, strategy_config.get("symbol", ""), "strategy_started", "策略已启动")
        engine.start()

        if is_main_thread:
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
        _send_lifecycle_notify(user_email, strategy_id, strategy_config.get("symbol", ""), "strategy_error", f"策略异常: {e}")
        raise

    finally:
        if is_main_thread:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm_handler)
            except Exception:
                pass

        if stop_watcher:
            stop_watcher.stop()

        _send_lifecycle_notify(user_email, strategy_id, strategy_config.get("symbol", ""), "strategy_stopped", "策略已停止")

        # 4. Stop engine (cancel orders + close exchange)
        if engine:
            try:
                engine.stop()
            except Exception as err:
                logger.warning(f"Strategy {strategy_id} engine.stop() failed: {err}")

        # 5. Cleanup Redis
        _cleanup_runtime(redis_client, strategy_id, runtime.task_id)
        logger.info(f"Strategy {strategy_id} stopped and cleaned up")


def _send_lifecycle_notify(
    user_email: str, strategy_id: int, symbol: str,
    event_value: str, body: str,
) -> None:
    """发送策略生命周期通知"""
    if not user_email:
        return
    try:
        from shared.notification.base import NotifyEvent, NotifyMessage, EVENT_LABELS
        from shared.notification.manager import get_notifier_manager
        event = NotifyEvent(event_value)
        title = EVENT_LABELS.get(event_value, event_value)
        msg = NotifyMessage(
            event=event,
            title=title,
            body=body,
            user_email=user_email,
            strategy_id=strategy_id,
            symbol=symbol,
        )
        get_notifier_manager().notify_user(msg)
    except Exception as e:
        logger.debug("生命周期通知发送失败 strategy=%s: %s", strategy_id, e)


def _sync_notify_channels_to_redis(redis_client, user_email: str) -> None:
    """启动策略时，从 DB 加载用户通知渠道配置到 Redis"""
    if not user_email:
        return
    try:
        from worker.db.trade_store import build_sync_database_url
        from sqlalchemy import create_engine, text
        import json as _json
        engine = create_engine(build_sync_database_url(), pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT channel_type, name, config, enabled_events, is_active FROM notification_channel WHERE user_email = :email"),
                {"email": user_email},
            ).fetchall()
        if not rows:
            return
        data = []
        for row in rows:
            config_val = row[2]
            if isinstance(config_val, str):
                config_val = _json.loads(config_val)
            events_val = row[3]
            if isinstance(events_val, str):
                events_val = _json.loads(events_val)
            data.append({
                "channel_type": row[0],
                "name": row[1],
                "config": config_val,
                "enabled_events": events_val or [],
                "is_active": bool(row[4]),
            })
        redis_client.client.set(f"notify:channels:{user_email}", _json.dumps(data))
    except Exception as e:
        logger.debug("同步通知渠道到 Redis 失败 user=%s: %s", user_email, e)


def _create_engine(
    strategy_id: int,
    account_data: Dict[str, Any],
    strategy_config: Dict[str, Any],
    redis_client,
    user_email: str = "",
) -> TradingEngine:
    """Create a trading engine instance."""
    # Decrypt API credentials
    api_key = decrypt_api_secret(account_data["api_key"])
    api_secret = decrypt_api_secret(account_data["api_secret"])
    exchange_name = str(account_data.get("exchange") or "binance").strip().lower()

    logger.info(
        "Strategy %s credentials exchange=%s api_key=%s api_secret=%s",
        strategy_id,
        exchange_name,
        _mask_credential(api_key),
        _mask_credential(api_secret),
    )

    # Build trading config
    trading_config = StrategyConfig(
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
        stop_loss_delay_seconds=int(strategy_config["stop_loss_delay"]) if strategy_config.get("stop_loss_delay") else None,
        max_position_count=strategy_config["max_open_positions"],
        max_daily_loss=float(strategy_config["max_daily_drawdown"]) if strategy_config.get("max_daily_drawdown") else None,
    )

    if exchange_name == "polymarket_updown15m":
        try:
            from worker.exchanges.polymarket_updown15m import PolymarketUpDown15m
            from worker.strategies.polymarket_grid_strategy import PolymarketGridStrategy
        except ModuleNotFoundError as err:
            if err.name == "websocket":
                raise RuntimeError(
                    "Missing dependency 'websocket-client' for Polymarket exchange"
                ) from err
            raise

        exchange = PolymarketUpDown15m(
            api_key=api_key,
            api_secret=api_secret,
            symbol=strategy_config["symbol"],
            testnet=account_data.get("testnet", False),
            market_close_buffer=int(strategy_config.get("stop_loss_delay") or 180),
        )
        strategy_impl = PolymarketGridStrategy(trading_config)
    else:
        exchange_cls = ExchangeFutures if exchange_name in FUTURES_EXCHANGE_IDS else ExchangeSpot
        exchange = exchange_cls(
            api_key=api_key,
            api_secret=api_secret,
            symbol=strategy_config["symbol"],
            exchange_id=exchange_name,
            testnet=account_data.get("testnet", False),
        )
        strategy_impl = GridStrategy(trading_config, log_prefix=exchange.log_prefix)

    risk_manager = RiskManager(risk_config)
    state_store = TradeStore(strategy_id)

    # Build engine
    engine = TradingEngine(
        strategy=strategy_impl,
        exchange=exchange,
        risk_manager=risk_manager,
        state_store=state_store,
        sync_interval=60,
    )

    # Set up status update callback
    def on_status_update(status: Dict[str, Any]) -> None:
        _persist_runtime_status(redis_client, strategy_id, status)

    engine.on_status_update = on_status_update

    # Set up notification callback
    if user_email:
        symbol = strategy_config.get("symbol", "")

        def on_notify(event_value: str, title: str, body: str) -> None:
            try:
                from shared.notification.base import NotifyEvent, NotifyMessage
                from shared.notification.manager import get_notifier_manager
                event = NotifyEvent(event_value)
                msg = NotifyMessage(
                    event=event,
                    title=title,
                    body=body,
                    user_email=user_email,
                    strategy_id=strategy_id,
                    symbol=symbol,
                )
                get_notifier_manager().notify_user(msg)
            except Exception as e:
                logger.debug("通知发送失败 strategy=%s: %s", strategy_id, e)

        engine.on_notify = on_notify

    return engine
