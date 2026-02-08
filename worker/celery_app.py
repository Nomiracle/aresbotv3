"""Celery application configuration."""
import os
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from celery import Celery

from shared.utils.crypto import init_encryption


def _init_encryption():
    """Initialize encryption from config or environment."""
    encryption_key = os.environ.get("ENCRYPTION_KEY", "")
    if not encryption_key:
        config_path = os.environ.get("CONFIG_PATH", "config.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
                encryption_key = config.get("security", {}).get("encryption_key", "")
    if encryption_key:
        init_encryption(encryption_key)


_init_encryption()

# Redis connection settings
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
REDIS_DB = os.environ.get('REDIS_DB', '0')

# Build Redis URL
if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Allow override via environment
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', REDIS_URL)

# Create Celery app
app = Celery(
    'aresbot',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=['worker.tasks.strategy_task'],
)

# Celery configuration
app.conf.update(
    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time per worker
    task_acks_late=True,  # Acknowledge task after completion
    task_reject_on_worker_lost=True,  # Reject task if worker dies

    # Task result settings
    result_expires=86400,  # 24 hours

    # Task tracking
    task_track_started=True,

    # Retry settings
    task_default_retry_delay=60,
    task_max_retries=3,
)
