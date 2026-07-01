"""Unified task-scoped logger factory.

Usage:
    from core.logging import get_task_logger
    logger = get_task_logger(task_id)
    logger.info("OSS upload started")
"""
from __future__ import annotations

import logging
import os
from logging import Logger
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join("data", "logs")
_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# In-memory registry to avoid duplicate handlers
_loggers: dict[str, Logger] = {}


def get_task_logger(task_id: str) -> Logger:
    """Return a logger that writes to data/logs/<task_id>.log."""
    if task_id in _loggers:
        return _loggers[task_id]

    os.makedirs(_LOG_DIR, exist_ok=True)
    log_path = os.path.join(_LOG_DIR, f"{task_id}.log")

    logger = logging.getLogger(f"task.{task_id}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=2, encoding="utf-8")
        fh.setFormatter(_FORMATTER)
        logger.addHandler(fh)

    _loggers[task_id] = logger
    return logger


def close_task_logger(task_id: str) -> None:
    """Flush and close handlers for a completed task."""
    logger = _loggers.pop(task_id, None)
    if logger:
        for handler in logger.handlers[:]:
            handler.flush()
            handler.close()
            logger.removeHandler(handler)
