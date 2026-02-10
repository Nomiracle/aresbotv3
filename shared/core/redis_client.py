"""Redis client for strategy runtime state management."""
import json
import os
import time
from typing import Any, Dict, List, Optional

import redis


class RedisClient:
    """Redis client for managing strategy running states."""

    # Key patterns
    RUNNING_KEY_PREFIX = "strategy:running:"
    LOCK_KEY_PREFIX = "strategy:lock:"
    WORKERS_KEY = "workers:active"
    WORKER_INFO_PREFIX = "worker:info:"

    # TTL settings
    LOCK_TTL = 86400  # 24 hours
    STOPPING_TIMEOUT = int(os.environ.get("STRATEGY_STOPPING_TIMEOUT", "60"))

    def __init__(
        self,
        host: str = None,
        port: int = None,
        password: str = None,
        db: int = None,
    ):
        self._host = host or os.environ.get('REDIS_HOST', 'redis')
        self._port = port or int(os.environ.get('REDIS_PORT', 6379))
        self._password = password or os.environ.get('REDIS_PASSWORD') or None
        self._db = db or int(os.environ.get('REDIS_DB', 0))

        self._client = redis.Redis(
            host=self._host,
            port=self._port,
            password=self._password,
            db=self._db,
            decode_responses=True,
        )

    @property
    def client(self) -> redis.Redis:
        """Get the underlying Redis client."""
        return self._client

    def acquire_lock(self, strategy_id: int, task_id: str) -> bool:
        """
        Acquire a distributed lock for a strategy.
        Returns True if lock acquired, False if already locked.
        """
        lock_key = f"{self.LOCK_KEY_PREFIX}{strategy_id}"
        return bool(self._client.set(lock_key, task_id, nx=True, ex=self.LOCK_TTL))

    def release_lock(self, strategy_id: int) -> bool:
        """Release the distributed lock for a strategy."""
        lock_key = f"{self.LOCK_KEY_PREFIX}{strategy_id}"
        return bool(self._client.delete(lock_key))

    def release_lock_if_holder(self, strategy_id: int, task_id: str) -> bool:
        """Release lock only when the provided task holds it."""
        lock_key = f"{self.LOCK_KEY_PREFIX}{strategy_id}"
        current_holder = self._client.get(lock_key)
        if not current_holder or current_holder != task_id:
            return False
        return bool(self._client.delete(lock_key))

    def get_lock_holder(self, strategy_id: int) -> Optional[str]:
        """Get the task_id holding the lock for a strategy."""
        lock_key = f"{self.LOCK_KEY_PREFIX}{strategy_id}"
        return self._client.get(lock_key)

    def set_running_info(
        self,
        strategy_id: int,
        task_id: str,
        worker_ip: str,
        worker_hostname: str,
        status: str = "running",
        user_email: Optional[str] = None,
        strategy_snapshot: Optional[Dict[str, Any]] = None,
        runtime_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        now = int(time.time())
        snapshot = strategy_snapshot or {}

        self._client.hset(key, mapping={
            "task_id": task_id,
            "worker_ip": worker_ip,
            "worker_hostname": worker_hostname,
            "status": status,
            "user_email": user_email or "",
            "strategy_name": str(snapshot.get("strategy_name") or ""),
            "symbol": str(snapshot.get("symbol") or ""),
            "base_order_size": str(snapshot.get("base_order_size") or ""),
            "buy_price_deviation": str(snapshot.get("buy_price_deviation") or ""),
            "sell_price_deviation": str(snapshot.get("sell_price_deviation") or ""),
            "grid_levels": str(snapshot.get("grid_levels") or "0"),
            "polling_interval": str(snapshot.get("polling_interval") or ""),
            "price_tolerance": str(snapshot.get("price_tolerance") or ""),
            "stop_loss": str(snapshot.get("stop_loss") or ""),
            "stop_loss_delay": str(snapshot.get("stop_loss_delay") or ""),
            "max_open_positions": str(snapshot.get("max_open_positions") or "0"),
            "max_daily_drawdown": str(snapshot.get("max_daily_drawdown") or ""),
            "worker_name": str(snapshot.get("worker_name") or ""),
            "runtime_config": json.dumps(runtime_config or {}, ensure_ascii=False),
            "started_at": now,
            "current_price": 0,
            "pending_buys": 0,
            "pending_sells": 0,
            "position_count": 0,
            "last_error": "",
            "stop_requested_at": 0,
            "updated_at": now,
        })

    def request_strategy_stop(self, strategy_id: int) -> bool:
        """Mark a strategy as stopping.

        Returns True when runtime info exists and stop request is recorded.
        """
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        if not self._client.exists(key):
            return False

        now = int(time.time())
        self._client.hset(key, mapping={
            "status": "stopping",
            "stop_requested_at": now,
            "updated_at": now,
        })
        return True

    def should_stop_strategy_task(self, strategy_id: int, task_id: str) -> bool:
        """Whether the current strategy task should stop cooperatively.

        Stop when runtime info is missing, ownership changed, or status is stopping.
        """
        info = self.get_running_info(strategy_id)
        if not info:
            return True

        running_task_id = info.get("task_id")
        if running_task_id and running_task_id != task_id:
            return True

        return info.get("status") == "stopping"

    def update_running_status(
        self,
        strategy_id: int,
        current_price: float = None,
        pending_buys: int = None,
        pending_sells: int = None,
        position_count: int = None,
        buy_orders: list = None,
        sell_orders: list = None,
        last_error: str = None,
        status: str = None,
    ) -> None:
        """Update the running status for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        update_data = {"updated_at": int(time.time())}

        if current_price is not None:
            update_data["current_price"] = current_price
        if pending_buys is not None:
            update_data["pending_buys"] = pending_buys
        if pending_sells is not None:
            update_data["pending_sells"] = pending_sells
        if position_count is not None:
            update_data["position_count"] = position_count
        if buy_orders is not None:
            update_data["buy_orders"] = json.dumps(buy_orders)
        if sell_orders is not None:
            update_data["sell_orders"] = json.dumps(sell_orders)
        if last_error is not None:
            update_data["last_error"] = last_error
        if status is not None:
            update_data["status"] = status

        self._client.hset(key, mapping=update_data)

    def update_runtime_config(self, strategy_id: int, runtime_config: Dict[str, Any]) -> None:
        """Update runtime strategy config in Redis for hot-reload style workflows."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        self._client.hset(key, mapping={
            "runtime_config": json.dumps(runtime_config, ensure_ascii=False),
            "updated_at": int(time.time()),
        })

    def get_running_info(self, strategy_id: int) -> Optional[Dict]:
        """Get the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        info = self._client.hgetall(key)
        if not info:
            return None

        stop_loss_delay = info.get("stop_loss_delay", "")
        max_open_positions = info.get("max_open_positions", "0")

        try:
            runtime_config = json.loads(info.get("runtime_config", "{}") or "{}")
        except json.JSONDecodeError:
            runtime_config = {}

        try:
            buy_orders = json.loads(info.get("buy_orders", "[]") or "[]")
        except json.JSONDecodeError:
            buy_orders = []

        try:
            sell_orders = json.loads(info.get("sell_orders", "[]") or "[]")
        except json.JSONDecodeError:
            sell_orders = []

        return {
            "task_id": info.get("task_id", ""),
            "worker_ip": info.get("worker_ip", ""),
            "worker_hostname": info.get("worker_hostname", ""),
            "status": info.get("status", ""),
            "user_email": info.get("user_email", ""),
            "strategy_name": info.get("strategy_name", ""),
            "symbol": info.get("symbol", ""),
            "base_order_size": info.get("base_order_size", ""),
            "buy_price_deviation": info.get("buy_price_deviation", ""),
            "sell_price_deviation": info.get("sell_price_deviation", ""),
            "grid_levels": int(info.get("grid_levels", 0) or 0),
            "polling_interval": info.get("polling_interval", ""),
            "price_tolerance": info.get("price_tolerance", ""),
            "stop_loss": info.get("stop_loss") or None,
            "stop_loss_delay": int(stop_loss_delay) if stop_loss_delay else None,
            "max_open_positions": int(max_open_positions or 0),
            "max_daily_drawdown": info.get("max_daily_drawdown") or None,
            "worker_name": info.get("worker_name") or None,
            "runtime_config": runtime_config,
            "started_at": int(info.get("started_at", 0)),
            "current_price": float(info.get("current_price", 0)),
            "pending_buys": int(info.get("pending_buys", 0)),
            "pending_sells": int(info.get("pending_sells", 0)),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "position_count": int(info.get("position_count", 0)),
            "last_error": info.get("last_error", ""),
            "stop_requested_at": int(info.get("stop_requested_at", 0) or 0),
            "updated_at": int(info.get("updated_at", 0)),
        }

    def clear_running_info(self, strategy_id: int) -> None:
        """Clear the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        self._client.delete(key)

    def clear_running_info_if_task(self, strategy_id: int, task_id: str) -> bool:
        """Clear runtime info only when the provided task still owns it."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        current_task_id = self._client.hget(key, "task_id")
        if not current_task_id or current_task_id != task_id:
            return False
        return bool(self._client.delete(key))

    def cleanup_runtime_if_task(self, strategy_id: int, task_id: str) -> None:
        """Safely cleanup lock/runtime records for a specific task."""
        self.release_lock_if_holder(strategy_id, task_id)
        self.clear_running_info_if_task(strategy_id, task_id)

    def get_all_running_strategies(self, user_email: Optional[str] = None) -> List[Dict]:
        """Get all running strategies with their information."""
        keys = self._client.keys(f"{self.RUNNING_KEY_PREFIX}*")
        result = []
        for key in keys:
            strategy_id = int(key.split(":")[-1])
            info = self.get_running_info(strategy_id)
            if info:
                if user_email and info.get("user_email") != user_email:
                    continue
                info["strategy_id"] = strategy_id
                result.append(info)
        return result

    def is_strategy_running(self, strategy_id: int) -> bool:
        """Check if a strategy is currently running."""
        info = self.get_running_info(strategy_id)
        if not info:
            return False

        status = info.get("status")
        if status == "running":
            return True

        if status == "stopping":
            updated_at = int(info.get("updated_at", 0) or 0)
            if updated_at and int(time.time()) - updated_at > self.STOPPING_TIMEOUT:
                self.release_lock(strategy_id)
                self.clear_running_info(strategy_id)
                return False
            return True

        return False

    def register_worker(self, worker_id: str, ip: str = "", hostname: str = "") -> None:
        """Register a worker as active with its info."""
        self._client.sadd(self.WORKERS_KEY, worker_id)
        if ip or hostname:
            key = f"{self.WORKER_INFO_PREFIX}{worker_id}"
            self._client.hset(key, mapping={
                "ip": ip,
                "hostname": hostname,
                "registered_at": int(time.time()),
            })
            self._client.expire(key, 86400)  # 24 hours TTL

    def unregister_worker(self, worker_id: str) -> None:
        """Unregister a worker."""
        self._client.srem(self.WORKERS_KEY, worker_id)
        self._client.delete(f"{self.WORKER_INFO_PREFIX}{worker_id}")

    def get_worker_info(self, worker_id: str) -> Optional[Dict]:
        """Get worker info by ID."""
        key = f"{self.WORKER_INFO_PREFIX}{worker_id}"
        info = self._client.hgetall(key)
        if not info:
            return None
        return {
            "ip": info.get("ip", ""),
            "hostname": info.get("hostname", ""),
            "registered_at": int(info.get("registered_at", 0)),
        }

    def get_active_workers(self) -> List[str]:
        """Get list of active workers."""
        return list(self._client.smembers(self.WORKERS_KEY))

    def get_all_workers_info(self) -> List[Dict]:
        """Get all active workers with their info."""
        worker_ids = self.get_active_workers()
        result = []
        for worker_id in worker_ids:
            info = self.get_worker_info(worker_id) or {}
            result.append({
                "name": worker_id,
                "ip": info.get("ip", ""),
                "hostname": info.get("hostname", worker_id.split("@")[-1] if "@" in worker_id else worker_id),
            })
        return result

    def ping(self) -> bool:
        """Check if Redis connection is alive."""
        try:
            return self._client.ping()
        except redis.ConnectionError:
            return False


# Global singleton instance
_redis_client: Optional[RedisClient] = None


def get_redis_client() -> RedisClient:
    """Get the global Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client
