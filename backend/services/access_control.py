from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext
from core.db.knowledge_base import get_knowledge_base, list_knowledge_bases
from core.db.query_logs import AuditLogRecord, append_audit_log


def require_kb_access(
    kb_id: str | None,
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str = "knowledge_base",
    resource_id: str | None = None,
) -> None:
    if not identity.enforce_access:
        return
    normalized_kb_id = str(kb_id or "").strip()
    if not normalized_kb_id or get_knowledge_base(normalized_kb_id, identity) is None:
        _audit_denied(identity, action=action, resource_type=resource_type, resource_id=resource_id, kb_id=normalized_kb_id)
        raise HTTPException(status_code=404, detail=f"{resource_type} not found")


def require_document_access(document_id: str, identity: IdentityContext, *, action: str) -> None:
    if not identity.enforce_access:
        return
    kb_id = get_document_kb_id(document_id)
    if not kb_id:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
    require_kb_access(kb_id, identity, action=action, resource_type="document", resource_id=document_id)


def require_task_access(task: dict | None, identity: IdentityContext, *, action: str) -> None:
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not identity.enforce_access:
        return
    task_id = str(task.get("id") or "")
    kb_id = str(task.get("kb_id") or "")
    require_kb_access(kb_id, identity, action=action, resource_type="ingestion_task", resource_id=task_id)


def require_chunk_draft_access(draft_id: str, identity: IdentityContext, *, action: str) -> None:
    if not identity.enforce_access:
        return
    scope = get_chunk_draft_scope(draft_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    require_kb_access(
        scope["kb_id"],
        identity,
        action=action,
        resource_type="chunk_draft",
        resource_id=draft_id,
    )


def filter_tasks_by_identity(tasks: Iterable[dict], identity: IdentityContext) -> list[dict]:
    task_list = list(tasks)
    if not identity.enforce_access:
        return task_list
    visible_kb_ids = {str(item["id"]) for item in list_knowledge_bases(identity)}
    return [task for task in task_list if str(task.get("kb_id") or "") in visible_kb_ids]


def get_document_kb_id(document_id: str) -> str | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kb_id
                FROM documents
                WHERE id::text = %s
                LIMIT 1
                """,
                (document_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] is not None else None


def get_chunk_draft_scope(draft_id: str) -> dict[str, str] | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, kb_id
                FROM chunk_drafts
                WHERE id::text = %s
                LIMIT 1
                """,
                (draft_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"task_id": str(row[0] or ""), "kb_id": str(row[1] or "")}


def _audit_denied(
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    kb_id: str | None,
) -> None:
    try:
        append_audit_log(
            AuditLogRecord(
                action="access.denied",
                resource_type=resource_type,
                resource_id=resource_id,
                kb_id=kb_id,
                identity=identity,
                outcome="denied",
                risk_level="medium",
                summary=f"Rejected {action} because resource is not accessible",
                metadata={
                    "reasonCode": "RESOURCE_NOT_ACCESSIBLE",
                    "action": action,
                    "resourceType": resource_type,
                    "resourceId": resource_id,
                    "kbId": kb_id,
                },
            )
        )
    except Exception:
        pass
