"""Celery tasks module."""
from .strategy_task import run_strategy

__all__ = ["run_strategy"]
