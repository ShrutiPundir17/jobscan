"""
Run a Celery task either on the worker queue or inside this process.

TASK_MODE=worker  -> task.delay(...) (a Celery worker consumes Redis).
TASK_MODE=inline  -> no worker exists (e.g. Render free): the task body runs in
                     a single background thread, so the HTTP request returns at
                     once and heavy work (Chromium) never runs two at a time.

Inline work is lost if the process restarts; every task here is written for
that (auto-apply states go stale -> failed/unconfirmed, embeddings are retried
by the next scan, notifications are best-effort).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jobagent-task")
    return _executor


def _run(task: Any, args: tuple, kwargs: dict) -> Any:
    try:
        return task(*args, **kwargs)
    except Exception:  # noqa: BLE001 — never let a background failure vanish silently
        logger.exception("inline_task_failed task=%s", getattr(task, "name", task))
        return None


def dispatch(task: Any, *args: Any, **kwargs: Any) -> Future | Any:
    """Queue `task` according to TASK_MODE. Raises if the worker queue is unreachable."""
    if settings.task_mode == "inline":
        logger.info("inline_task_queued task=%s", getattr(task, "name", task))
        return _pool().submit(_run, task, args, kwargs)
    return task.delay(*args, **kwargs)
