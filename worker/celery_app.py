"""Celery application configuration."""
import logging
import os
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading

import yaml
from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    celeryd_after_setup,
    worker_process_init,
    worker_ready,
    worker_shutdown,
)

from shared.utils.crypto import init_encryption
from shared.utils.network import get_worker_network_identity


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


def _configure_ccxt_logger() -> None:
    """Keep CCXT loggers at INFO to avoid verbose HTTP DEBUG output."""
    ccxt_logger_names = (
        "ccxt",
        "ccxt.base",
        "ccxt.base.exchange",
        "ccxt.pro",
    )
    for logger_name in ccxt_logger_names:
        logging.getLogger(logger_name).setLevel(logging.INFO)


def _setup_worker_file_logging(logger: logging.Logger | None = None) -> None:
    """Attach rotating file handler for worker logs."""
    from shared.utils.logger import setup_file_logging

    log_dir = os.environ.get("WORKER_LOG_DIR", "/app/logs")
    worker_name = os.environ.get("WORKER_NAME", "worker")
    log_level_name = os.environ.get("CELERY_LOG_LEVEL", "info").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    setup_file_logging(log_dir, worker_name, level=log_level, logger=logger)


_init_encryption()
_configure_ccxt_logger()

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


# Worker setup & registration signals
@celeryd_after_setup.connect
def on_celeryd_after_setup(sender=None, instance=None, **kwargs):
    """Add dedicated queue for this worker before consumers start."""
    if not instance or not sender:
        return

    # sender format: celery@hostname
    worker_name = str(sender)
    instance.app.amqp.queues.select_add(worker_name)
    print(f"Worker {worker_name} added dedicated queue before startup")


@worker_process_init.connect
def on_worker_process_init(**kwargs):
    """Apply logger levels in each forked worker process."""
    _configure_ccxt_logger()
    _setup_worker_file_logging()


@after_setup_logger.connect
def on_after_setup_logger(logger, *args, **kwargs):
    """Attach file logging after Celery configures main logger."""
    _setup_worker_file_logging(logger)


@after_setup_task_logger.connect
def on_after_setup_task_logger(logger, *args, **kwargs):
    """Attach file logging after Celery configures task logger."""
    _setup_worker_file_logging(logger)


_heartbeat_stop_event = threading.Event()

# 心跳间隔：默认 6 小时，确保在 24h TTL 过期前多次续期
_HEARTBEAT_INTERVAL = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "21600"))


def _heartbeat_loop(worker_name: str) -> None:
    """Periodically re-register worker to refresh Redis info TTL."""
    from shared.core.redis_client import get_redis_client
    from shared.utils.version import get_worker_version

    heartbeat_logger = logging.getLogger("worker.heartbeat")
    while not _heartbeat_stop_event.wait(timeout=_HEARTBEAT_INTERVAL):
        try:
            identity = get_worker_network_identity()
            version = get_worker_version()
            redis_client = get_redis_client()
            redis_client.register_worker(
                worker_name,
                ip=identity.worker_ip,
                hostname=identity.hostname,
                private_ip=identity.private_ip,
                public_ip=identity.public_ip,
                ip_location=identity.ip_location,
                version=version,
            )
            heartbeat_logger.debug("heartbeat refreshed for %s", worker_name)
        except Exception as err:
            heartbeat_logger.warning("heartbeat failed for %s: %s", worker_name, err)


def _start_heartbeat(worker_name: str) -> None:
    _heartbeat_stop_event.clear()
    t = threading.Thread(target=_heartbeat_loop, args=(worker_name,), daemon=True)
    t.start()


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Register worker when it's ready."""
    from shared.core.redis_client import get_redis_client
    from shared.utils.version import get_worker_version

    worker_name = os.environ.get("WORKER_NAME") or sender.hostname
    identity = get_worker_network_identity(force_refresh=True)
    version = get_worker_version()

    redis_client = get_redis_client()
    redis_client.register_worker(
        worker_name,
        ip=identity.worker_ip,
        hostname=identity.hostname,
        private_ip=identity.private_ip,
        public_ip=identity.public_ip,
        ip_location=identity.ip_location,
        version=version,
    )
    print(
        f"Worker {worker_name} registered: "
        f"version={version} "
        f"egress={identity.public_ip or '-'} private={identity.private_ip or '-'} "
        f"location={identity.ip_location or '-'}"
    )

    # 启动心跳线程，定期刷新 Redis 中的 worker 注册信息 TTL
    _start_heartbeat(worker_name)


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """Unregister worker when it shuts down."""
    _heartbeat_stop_event.set()
    from shared.core.redis_client import get_redis_client
    worker_name = os.environ.get("WORKER_NAME") or sender.hostname
    redis_client = get_redis_client()
    redis_client.unregister_worker(worker_name)
    print(f"Worker {worker_name} unregistered")
