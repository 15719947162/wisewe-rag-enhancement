from __future__ import annotations

import json
import os
from typing import Any

# TTL for task data in Redis: 7 days
_TASK_TTL = 7 * 24 * 3600
_KEY_PREFIX = "wisewe:task:"
_ALL_KEY = "wisewe:task_ids"
_redis_client: Any = None
_redis_available: bool | None = None  # None = not yet checked
_mem_tasks: dict[str, dict] = {}


def _clone_task(task: dict) -> dict:
    return json.loads(json.dumps(task, ensure_ascii=False, default=str))


def _get_redis():
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as _redis

        client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        _redis_available = True
        return _redis_client
    except Exception:
        _redis_available = False
        _redis_client = None
        return None


def _serialize(task: dict) -> str:
    # file_bytes is not JSON-serializable; strip before storing
    safe = {k: v for k, v in task.items() if k != "file_bytes"}
    return json.dumps(safe, ensure_ascii=False, default=str)


def _deserialize(raw: str) -> dict:
    task = json.loads(raw)
    task.setdefault("file_bytes", None)
    return task


def _task_time_sort_key(task: dict) -> tuple[str, str]:
    return (str(task.get("created_at") or task.get("updated_at") or ""), str(task.get("id", "")))


def _sort_tasks_newest_first(tasks: list[dict]) -> list[dict]:
    return sorted(tasks, key=_task_time_sort_key, reverse=True)


def save_task(task: dict) -> None:
    task_id = task["id"]
    _mem_tasks[task_id] = _clone_task(task)

    r = _get_redis()
    if r is None:
        return

    r.setex(f"{_KEY_PREFIX}{task_id}", _TASK_TTL, _serialize(task))
    r.sadd(_ALL_KEY, task_id)
    r.expire(_ALL_KEY, _TASK_TTL)


def load_task(task_id: str) -> dict | None:
    r = _get_redis()
    if r is None:
        task = _mem_tasks.get(task_id)
        return _clone_task(task) if task else None

    raw = r.get(f"{_KEY_PREFIX}{task_id}")
    if raw:
        return _deserialize(raw)
    _mem_tasks.pop(task_id, None)
    return None


def load_all_tasks() -> list[dict]:
    r = _get_redis()
    if r is None:
        return _sort_tasks_newest_first([_clone_task(task) for task in _mem_tasks.values()])

    ids = r.smembers(_ALL_KEY)
    tasks_by_id: dict[str, dict] = {}
    for tid in ids:
        raw = r.get(f"{_KEY_PREFIX}{tid}")
        if raw:
            tasks_by_id[tid] = _deserialize(raw)

    for task_id in list(_mem_tasks):
        if task_id not in tasks_by_id:
            _mem_tasks.pop(task_id, None)

    return _sort_tasks_newest_first(list(tasks_by_id.values()))


def delete_task(task_id: str) -> bool:
    deleted = _mem_tasks.pop(task_id, None) is not None

    r = _get_redis()
    if r is None:
        return deleted

    removed = r.delete(f"{_KEY_PREFIX}{task_id}")
    r.srem(_ALL_KEY, task_id)
    return deleted or bool(removed)


def update_task_field(task_id: str, **fields) -> None:
    """Partial update: load -> merge -> save."""
    task = load_task(task_id)
    if task is None:
        return
    task.update(fields)
    save_task(task)


def is_redis_available() -> bool:
    return _get_redis() is not None
