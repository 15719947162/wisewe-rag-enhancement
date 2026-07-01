from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.requests import ParsePreviewRequest
from backend.services.access_control import filter_tasks_by_identity, require_kb_access
from backend.services.identity_service import get_current_identity
from backend.services.ingestion_service import get_all_tasks, _task_to_payload
from core.db.identity import IdentityContext
from backend.services.parse_service import get_parse_preview

router = APIRouter()


@router.get("/api/ingestion/tasks")
def ingestion_tasks(
    kb_id: str | None = None,
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    if kb_id:
        require_kb_access(kb_id, identity, action="ingestion_task.list", resource_id=kb_id)
    tasks = get_all_tasks()
    tasks = filter_tasks_by_identity(tasks, identity)
    if kb_id:
        tasks = [task for task in tasks if task.get("kb_id") == kb_id]
    return [_task_to_payload(task) for task in tasks]


@router.post("/api/parse/preview")
def parse_preview(payload: ParsePreviewRequest) -> list[dict]:
    try:
        return get_parse_preview(payload.pdf_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
