from __future__ import annotations

from backend.services import task_store


class _FakeRedis:
    def __init__(self, values: dict[str, str] | None = None, ids: set[str] | None = None) -> None:
        self.values = values or {}
        self.ids = ids or set()

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def smembers(self, key: str) -> set[str]:
        return set(self.ids)


def test_task_store_falls_back_to_memory_when_redis_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(task_store, "_redis_client", None)
    monkeypatch.setattr(task_store, "_redis_available", False)
    monkeypatch.setattr(task_store, "_mem_tasks", {})

    payload = {"id": "task-1", "status": "pending", "file_bytes": None}
    task_store.save_task(payload)

    loaded = task_store.load_task("task-1")
    assert loaded is not None
    assert loaded["id"] == "task-1"
    assert loaded["status"] == "pending"

    all_tasks = task_store.load_all_tasks()
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == "task-1"

    assert task_store.is_redis_available() is False


def test_task_store_load_all_tasks_sorts_by_created_at_desc(monkeypatch) -> None:
    monkeypatch.setattr(task_store, "_redis_client", None)
    monkeypatch.setattr(task_store, "_redis_available", False)
    monkeypatch.setattr(task_store, "_mem_tasks", {})

    task_store.save_task(
        {
            "id": "z-old",
            "status": "success",
            "created_at": "2026-06-10T08:00:00+00:00",
            "updated_at": "2026-06-10T08:30:00+00:00",
            "file_bytes": None,
        }
    )
    task_store.save_task(
        {
            "id": "a-new",
            "status": "pending",
            "created_at": "2026-06-10T09:00:00+00:00",
            "updated_at": "2026-06-10T09:00:00+00:00",
            "file_bytes": None,
        }
    )

    assert [task["id"] for task in task_store.load_all_tasks()] == ["a-new", "z-old"]


def test_task_store_delete_task_removes_memory_record(monkeypatch) -> None:
    monkeypatch.setattr(task_store, "_redis_client", None)
    monkeypatch.setattr(task_store, "_redis_available", False)
    monkeypatch.setattr(task_store, "_mem_tasks", {})

    task_store.save_task({"id": "task-delete", "status": "failed", "file_bytes": None})

    assert task_store.delete_task("task-delete") is True
    assert task_store.load_task("task-delete") is None
    assert task_store.load_all_tasks() == []
    assert task_store.delete_task("task-delete") is False


def test_task_store_uses_redis_as_source_of_truth_when_available(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr(task_store, "_redis_client", fake_redis)
    monkeypatch.setattr(task_store, "_redis_available", True)
    monkeypatch.setattr(
        task_store,
        "_mem_tasks",
        {"stale-task": {"id": "stale-task", "status": "running", "file_bytes": None}},
    )

    assert task_store.load_task("stale-task") is None
    assert task_store.load_all_tasks() == []
