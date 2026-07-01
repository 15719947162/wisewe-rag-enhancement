from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.services.access_control import require_chunk_draft_access, require_kb_access, require_task_access
from backend.services.identity_service import get_current_identity
from backend.schemas.requests import ChunkDraftMergeRequest, ChunkDraftUpdateRequest
from backend.services.chunk_draft_service import (
    delete_chunk_draft,
    list_chunk_drafts,
    merge_chunk_drafts,
    update_chunk_draft,
)
from core.db.identity import IdentityContext
from backend.services.ingestion_service import (
    confirm_pipeline,
    create_task,
    delete_ingestion_task,
    get_task,
    reset_task_for_retry,
    run_pipeline_and_confirm,
    run_pipeline_real,
    stream_task_events,
    _task_to_payload,
)

router = APIRouter()

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB


@router.post("/api/ingestion/upload", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    kb_id: str = "default",
    strategy: str = "hierarchical",
    subject_type: str = "general",
    layout_type: str = "single_column",
    auto_confirm: bool = False,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="只支持 PDF 文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail="文件大小不能超过 500MB")

    require_kb_access(kb_id, identity, action="ingestion.upload", resource_id=kb_id)
    task_id = create_task(
        kb_id,
        file.filename,
        strategy,
        file_bytes=content,
        subject_type=subject_type,
        layout_type=layout_type,
        identity=identity if identity.enforce_access else None,
    )
    if auto_confirm:
        background_tasks.add_task(run_pipeline_and_confirm, task_id)
    else:
        background_tasks.add_task(run_pipeline_real, task_id)

    return {
        "task_id": task_id,
        "status": "pending",
        "filename": file.filename,
        "mode": "real",
        "auto_confirm": auto_confirm,
    }


@router.get("/api/ingestion/tasks/{task_id}")
def get_ingestion_task(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="ingestion_task.read")
    return _task_to_payload(task)


@router.delete("/api/ingestion/tasks/{task_id}")
def delete_ingestion_task_endpoint(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        if identity.enforce_access:
            task = get_task(task_id)
            require_task_access(task, identity, action="ingestion_task.delete")
        result = delete_ingestion_task(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return result


@router.get("/api/ingestion/stream/{task_id}")
async def stream_ingestion(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> StreamingResponse:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="ingestion_task.stream")

    return StreamingResponse(
        stream_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/ingestion/tasks/{task_id}/retry", status_code=202)
async def retry_ingestion_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    if identity.enforce_access:
        existing_task = get_task(task_id)
        require_task_access(existing_task, identity, action="ingestion_task.retry")
    task = reset_task_for_retry(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    background_tasks.add_task(run_pipeline_real, task_id)
    return {"task_id": task_id, "status": "pending", "retried": True, "mode": "real"}


@router.get("/api/ingestion/chunks/preview/{task_id}")
def preview_chunk_drafts(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="chunk_draft.preview")
    drafts = list_chunk_drafts(task_id)
    return {"taskId": task_id, "status": task.get("status", "pending"), "items": drafts, "count": len(drafts)}


@router.put("/api/ingestion/chunks/{draft_id}")
def edit_chunk_draft(
    draft_id: str,
    payload: ChunkDraftUpdateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    require_chunk_draft_access(draft_id, identity, action="chunk_draft.update")
    updated = update_chunk_draft(draft_id, payload.content)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return updated


@router.delete("/api/ingestion/chunks/{draft_id}")
def remove_chunk_draft(draft_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    require_chunk_draft_access(draft_id, identity, action="chunk_draft.delete")
    deleted = delete_chunk_draft(draft_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return {"deleted": True, "draftId": draft_id}


@router.post("/api/ingestion/chunks/merge")
def merge_drafts(
    payload: ChunkDraftMergeRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    task = get_task(payload.task_id)
    require_task_access(task, identity, action="chunk_draft.merge")
    for draft_id in payload.draft_ids:
        require_chunk_draft_access(draft_id, identity, action="chunk_draft.merge")
    merged = merge_chunk_drafts(payload.task_id, payload.draft_ids)
    if not merged:
        raise HTTPException(status_code=400, detail="无法合并指定草稿")
    return merged


@router.post("/api/ingestion/chunks/confirm/{task_id}")
async def confirm_chunk_drafts(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="chunk_draft.confirm")
    try:
        return await confirm_pipeline(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
