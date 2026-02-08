"""Celery client for API service."""
import os

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


def send_run_strategy(strategy_id: int, account_data: dict, strategy_config: dict) -> str:
    """Send run_strategy task to worker, return task_id."""
    result = celery_app.send_task(
        TASK_RUN_STRATEGY,
        kwargs={
            'strategy_id': strategy_id,
            'account_data': account_data,
            'strategy_config': strategy_config,
        },
    )
    return result.id


def revoke_task(task_id: str, terminate: bool = True) -> None:
    """Revoke a running task."""
    celery_app.control.revoke(task_id, terminate=terminate, signal='SIGTERM')
