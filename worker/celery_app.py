"""Celery application configuration."""
import os
import socket
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from celery import Celery
from celery.signals import worker_ready, worker_shutdown

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


def _get_worker_ip() -> str:
    """Get the IP address of the current worker."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())


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


# Worker registration signals
@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Register worker when it's ready and set up dedicated queue."""
    from shared.core.redis_client import get_redis_client
    worker_name = sender.hostname
    worker_ip = _get_worker_ip()
    hostname = socket.gethostname()

    # 动态添加以 worker 名称命名的专属队列
    # 这样任务可以通过指定 queue=worker_name 发送到特定 worker
    sender.app.amqp.queues.select_add(worker_name)

    redis_client = get_redis_client()
    redis_client.register_worker(worker_name, ip=worker_ip, hostname=hostname)
    print(f"Worker {worker_name} registered with IP {worker_ip}, listening on queue: {worker_name}")


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """Unregister worker when it shuts down."""
    from shared.core.redis_client import get_redis_client
    worker_name = sender.hostname
    redis_client = get_redis_client()
    redis_client.unregister_worker(worker_name)
    print(f"Worker {worker_name} unregistered")
