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


def get_active_workers() -> List[Dict]:
    """Get list of active Celery workers with their info."""
    inspect = celery_app.control.inspect()
    ping_result = inspect.ping() or {}
    stats_result = inspect.stats() or {}

    workers = []
    for worker_name, ping_data in ping_result.items():
        stats = stats_result.get(worker_name, {})
        # worker_name format: worker@hostname
        hostname = worker_name.split('@')[-1] if '@' in worker_name else worker_name
        # 尝试从 broker 连接信息获取 IP
        broker_info = stats.get('broker', {})
        worker_ip = broker_info.get('hostname', '')
        workers.append({
            'name': worker_name,
            'hostname': hostname,
            'ip': worker_ip,
            'concurrency': stats.get('pool', {}).get('max-concurrency', 0),
            'active_tasks': len(stats.get('pool', {}).get('processes', [])),
        })
    return workers


def send_run_strategy(
    strategy_id: int,
    account_data: dict,
    strategy_config: dict,
    worker_name: Optional[str] = None,
) -> str:
    """Send run_strategy task to worker, return task_id."""
    options = {}
    if worker_name:
        options['queue'] = worker_name

    result = celery_app.send_task(
        TASK_RUN_STRATEGY,
        kwargs={
            'strategy_id': strategy_id,
            'account_data': account_data,
            'strategy_config': strategy_config,
        },
        **options,
    )
    return result.id


def revoke_task(task_id: str, terminate: bool = True) -> None:
    """Revoke a running task."""
    celery_app.control.revoke(task_id, terminate=terminate, signal='SIGTERM')
