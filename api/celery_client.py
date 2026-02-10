"""Celery client for API service."""
import os
from typing import List, Dict, Optional

from celery import Celery

REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
REDIS_DB = os.environ.get('REDIS_DB', '0')

if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', REDIS_URL)

celery_app = Celery(
    'aresbot',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

# Task name constant
TASK_RUN_STRATEGY = 'worker.tasks.strategy_task.run_strategy'


def _env_flag(name: str) -> Optional[bool]:
    value = os.environ.get(name)
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _should_terminate_revoke() -> bool:
    """Return whether revoke should force terminate running tasks."""
    forced = _env_flag("CELERY_REVOKE_TERMINATE")
    if forced is not None:
        return forced

    pool = os.environ.get("CELERY_POOL", "threads").strip().lower()
    return pool not in {"threads"}


def get_active_workers() -> List[Dict]:
    """Get list of active Celery workers with their info."""
    from shared.core.redis_client import get_redis_client

    inspect = celery_app.control.inspect()
    ping_result = inspect.ping() or {}
    stats_result = inspect.stats() or {}
    active_result = inspect.active() or {}
    redis_client = get_redis_client()

    workers = []
    for worker_name in ping_result:
        stats = stats_result.get(worker_name, {})
        # 从 Redis 获取 worker 详细信息
        worker_info = redis_client.get_worker_info(worker_name) or {}
        # worker_name format: worker@hostname
        hostname = worker_info.get("hostname") or (worker_name.split('@')[-1] if '@' in worker_name else worker_name)
        worker_public_ip = worker_info.get("public_ip", "")
        worker_private_ip = worker_info.get("private_ip", "")
        worker_ip = worker_public_ip or worker_info.get("ip", "") or worker_private_ip
        workers.append({
            'name': worker_name,
            'hostname': hostname,
            'ip': worker_ip,
            'private_ip': worker_private_ip,
            'public_ip': worker_public_ip,
            'ip_location': worker_info.get("ip_location", ""),
            'concurrency': stats.get('pool', {}).get('max-concurrency', 0),
            'active_tasks': len(active_result.get(worker_name, [])),
        })
    return workers


def send_run_strategy(
    strategy_id: int,
    account_data: dict,
    strategy_config: dict,
    strategy_runtime: Optional[dict] = None,
    worker_name: Optional[str] = None,
) -> str:
    """Send run_strategy task to worker, return task_id.

    Args:
        strategy_id: 策略ID
        account_data: 账户数据
        strategy_config: 策略配置
        worker_name: 指定 worker 名称 (如 celery@hostname)，为空则自动分配到默认队列
    """
    options = {}

    # 如果指定了 worker，发送到该 worker 的专属队列
    # Worker 启动时会自动监听以自己名字命名的队列
    if worker_name:
        options['queue'] = worker_name

    result = celery_app.send_task(
        TASK_RUN_STRATEGY,
        kwargs={
            'strategy_id': strategy_id,
            'account_data': account_data,
            'strategy_config': strategy_config,
            'strategy_runtime': strategy_runtime or {},
        },
        **options,
    )
    return result.id


def revoke_task(task_id: str, terminate: Optional[bool] = None) -> None:
    """Revoke a running task."""
    should_terminate = _should_terminate_revoke() if terminate is None else terminate
    celery_app.control.revoke(task_id, terminate=should_terminate, signal='SIGTERM')
