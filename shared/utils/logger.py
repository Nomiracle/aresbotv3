import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from typing import Optional


def setup_file_logging(
    log_dir: str,
    worker_name: str,
    level: int = logging.INFO,
    backup_count: int = 30,  # 保留 30 天
) -> None:
    """配置按日期轮转的文件日志"""
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"{worker_name}.log")
    format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(format_string)

    # 按天轮转，保留 backup_count 天
    handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",      # 每天午夜轮转
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"  # 轮转后文件后缀格式
    handler.setLevel(level)
    handler.setFormatter(formatter)

    # 添加到 root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)


def setup_logger(
    name: str = "aresbot",
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """配置日志"""
    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(format_string)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    return logging.getLogger(f"aresbot.{name}")
