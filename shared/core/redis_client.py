"""Redis client for strategy runtime state management."""
import os
import time
from typing import Dict, List, Optional

import redis


class RedisClient:
    """Redis client for managing strategy running states."""

    # Key patterns
    RUNNING_KEY_PREFIX = "strategy:running:"
    LOCK_KEY_PREFIX = "strategy:lock:"
    WORKERS_KEY = "workers:active"

    # TTL settings
    LOCK_TTL = 86400  # 24 hours

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
    ) -> None:
        """Set the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        now = int(time.time())
        self._client.hset(key, mapping={
            "task_id": task_id,
            "worker_ip": worker_ip,
            "worker_hostname": worker_hostname,
            "status": status,
            "started_at": now,
            "current_price": 0,
            "pending_buys": 0,
            "pending_sells": 0,
            "position_count": 0,
            "last_error": "",
            "updated_at": now,
        })

    def update_running_status(
        self,
        strategy_id: int,
        current_price: float = None,
        pending_buys: int = None,
        pending_sells: int = None,
        position_count: int = None,
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
        if last_error is not None:
            update_data["last_error"] = last_error
        if status is not None:
            update_data["status"] = status

        self._client.hset(key, mapping=update_data)

    def get_running_info(self, strategy_id: int) -> Optional[Dict]:
        """Get the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        info = self._client.hgetall(key)
        if not info:
            return None

        return {
            "task_id": info.get("task_id", ""),
            "worker_ip": info.get("worker_ip", ""),
            "worker_hostname": info.get("worker_hostname", ""),
            "status": info.get("status", ""),
            "started_at": int(info.get("started_at", 0)),
            "current_price": float(info.get("current_price", 0)),
            "pending_buys": int(info.get("pending_buys", 0)),
            "pending_sells": int(info.get("pending_sells", 0)),
            "position_count": int(info.get("position_count", 0)),
            "last_error": info.get("last_error", ""),
            "updated_at": int(info.get("updated_at", 0)),
        }

    def clear_running_info(self, strategy_id: int) -> None:
        """Clear the running information for a strategy."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        self._client.delete(key)

    def get_all_running_strategies(self) -> List[Dict]:
        """Get all running strategies with their information."""
        keys = self._client.keys(f"{self.RUNNING_KEY_PREFIX}*")
        result = []
        for key in keys:
            strategy_id = int(key.split(":")[-1])
            info = self.get_running_info(strategy_id)
            if info:
                info["strategy_id"] = strategy_id
                result.append(info)
        return result

    def is_strategy_running(self, strategy_id: int) -> bool:
        """Check if a strategy is currently running."""
        key = f"{self.RUNNING_KEY_PREFIX}{strategy_id}"
        return self._client.exists(key) > 0

    def register_worker(self, worker_id: str) -> None:
        """Register a worker as active."""
        self._client.sadd(self.WORKERS_KEY, worker_id)

    def unregister_worker(self, worker_id: str) -> None:
        """Unregister a worker."""
        self._client.srem(self.WORKERS_KEY, worker_id)

    def get_active_workers(self) -> List[str]:
        """Get list of active workers."""
        return list(self._client.smembers(self.WORKERS_KEY))

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
