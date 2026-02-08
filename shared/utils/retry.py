from dataclasses import dataclass
from functools import wraps
from typing import Tuple, Type, Callable, Any
import time
import random
import logging

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True
    exceptions: Tuple[Type[Exception], ...] = (Exception,)


def with_retry(
    config: RetryConfig = None,
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """指数退避重试装饰器

    用法:
        @with_retry(max_attempts=3, backoff_factor=2.0)
        def my_function():
            ...

        # 或使用配置对象
        @with_retry(config=RetryConfig(max_attempts=5))
        def my_function():
            ...
    """
    if config is None:
        config = RetryConfig(
            max_attempts=max_attempts,
            backoff_factor=backoff_factor,
            exceptions=exceptions,
        )

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = config.base_delay
            last_exception = None

            for attempt in range(config.max_attempts):
                try:
                    return func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    if attempt == config.max_attempts - 1:
                        logger.error(
                            f"{func.__name__} 重试{config.max_attempts}次后失败: {e}"
                        )
                        raise

                    actual_delay = delay
                    if config.jitter:
                        actual_delay = delay * (0.5 + random.random())

                    logger.warning(
                        f"{func.__name__} 第{attempt + 1}次失败，{actual_delay:.1f}秒后重试: {e}"
                    )
                    time.sleep(actual_delay)

                    delay = min(delay * config.backoff_factor, config.max_delay)

            raise last_exception

        return wrapper

    return decorator


def parse_rate_limit_wait(error_message: str) -> float:
    """从限流错误消息中解析等待时间"""
    import re

    patterns = [
        r"retry after (\d+)",
        r"wait (\d+) seconds",
        r"(\d+)s",
    ]

    for pattern in patterns:
        match = re.search(pattern, error_message.lower())
        if match:
            return float(match.group(1))

    return 60.0
