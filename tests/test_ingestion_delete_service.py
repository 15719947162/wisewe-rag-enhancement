from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import ingestion_service
from backend.services import task_store


def _task(task_id: str, source_path: str, status: str = "failed", done: bool = True) -> dict:
    return {
        "id": task_id,
        "kb_id": "default",
        "filename": "demo.pdf",
        "strategy": "hierarchical",
        "source_path": source_path,
        "status": status,
        "done": done,
        "stages": {},
        "created_at": "",
        "updated_at": "",
    }


def test_delete_ingestion_task_removes_persisted_files_and_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(task_store, "_redis_client", None)
    monkeypatch.setattr(task_store, "_redis_available", False)
    monkeypatch.setattr(task_store, "_mem_tasks", {})

    uploads = tmp_path / "data" / "uploads"
    logs = tmp_path / "data" / "logs"
    uploads.mkdir(parents=True)
    logs.mkdir(parents=True)
    source = uploads / "task-delete.pdf"
    log_file = logs / "task-delete.log"
    source.write_bytes(b"%PDF-1.4")
    log_file.write_text("done", encoding="utf-8")
    task_store.save_task(_task("task-delete", str(source)))

    with patch("backend.services.ingestion_service.clear_chunk_drafts", return_value=3):
        result = ingestion_service.delete_ingestion_task("task-delete")

    assert result is not None
    assert result["deleted"] is True
    assert result["removed"] == {
        "sourceFile": True,
        "logFile": True,
        "chunkDrafts": 3,
        "taskRecord": True,
    }
    assert not source.exists()
    assert not log_file.exists()
    assert task_store.load_task("task-delete") is None


def test_delete_ingestion_task_rejects_active_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(task_store, "_redis_client", None)
    monkeypatch.setattr(task_store, "_redis_available", False)
    monkeypatch.setattr(task_store, "_mem_tasks", {})

    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True)
    source = uploads / "task-running.pdf"
    source.write_bytes(b"%PDF-1.4")
    task_store.save_task(_task("task-running", str(source), status="running", done=False))

    with pytest.raises(RuntimeError):
        ingestion_service.delete_ingestion_task("task-running")

    assert source.exists()
    assert task_store.load_task("task-running") is not None
