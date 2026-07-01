from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.services.access_control import require_kb_access, require_task_access
from backend.services.document_export_service import build_csv_content_disposition
from backend.services.console_service import (
    EDITABLE_SETTINGS_KEYS,
    create_console_api_key,
    create_console_openapi_app,
    delete_console_api_key,
    delete_console_openapi_app,
    export_console_query_logs,
    get_console_alerts,
    get_console_audit_logs,
    get_console_api_keys,
    get_console_openapi_apps,
    get_console_evaluations,
    get_console_identity_sync_logs,
    get_console_ingestion_tasks,
    get_latest_ingestion_log,
    get_console_metrics,
    get_console_query_logs,
    get_console_queue,
    get_console_token_usage,
    get_settings_payload,
    rotate_console_api_key,
    sanitize_console_settings_update,
    update_console_api_key,
    update_console_openapi_app,
    update_console_settings_with_audit as persist_console_settings,
)
from backend.services.ingestion_service import backfill_ingestion_llm_usage, get_task
from backend.services.identity_service import assert_fresh_identity_snapshot, get_current_identity
from core.db.identity import TENANT_ADMIN_ROLE_CODE, IdentityContext

router = APIRouter()


class ApiKeyCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appId: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    kbIds: list[str] = Field(..., min_length=1, max_length=20)
    capabilities: list[str] = Field(default_factory=lambda: ["rag.query", "rag.graph_query"], min_length=1)
    requireSignature: bool = True
    allowedIps: list[str] = Field(default_factory=list, max_length=50)
    rpmLimit: int = Field(default=0, ge=0)
    dailyRequestLimit: int = Field(default=0, ge=0)
    note: str = Field(default="", max_length=500)
    expiresAt: datetime | None = None


class OpenApiAppCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class OpenApiAppUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    note: str | None = Field(default=None, max_length=500)


class ApiKeyUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appId: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    kbIds: list[str] | None = Field(default=None, min_length=1, max_length=20)
    capabilities: list[str] | None = Field(default=None, min_length=1)
    requireSignature: bool | None = None
    allowedIps: list[str] | None = Field(default=None, max_length=50)
    rpmLimit: int | None = Field(default=None, ge=0)
    dailyRequestLimit: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    expiresAt: datetime | None = None


@router.get("/api/console/overview-metrics")
def overview_metrics(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        return get_console_metrics(identity if identity.enforce_access else None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/alerts")
def console_alerts() -> list[dict]:
    try:
        return get_console_alerts()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/queue")
def console_queue() -> list[dict]:
    try:
        return get_console_queue()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/evaluations")
def console_evaluations(
    kb_id: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    try:
        return get_console_evaluations(kb_id=kb_id, identity=identity if identity.enforce_access else None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/query-logs")
def console_query_logs(
    kb_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    api_key_id: str | None = Query(default=None),
    pipeline_domain: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    try:
        return get_console_query_logs(
            tenant_id=identity.tenant_id if identity.enforce_access else None,
            kb_id=kb_id,
            request_id=request_id,
            actor_id=actor_id,
            api_key_id=api_key_id,
            pipeline_domain=pipeline_domain,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            identity=identity if identity.enforce_access else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/audit-logs")
def console_audit_logs(
    actor_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    kb_id: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    try:
        return get_console_audit_logs(
            tenant_id=identity.tenant_id if identity.enforce_access else None,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            kb_id=kb_id,
            outcome=outcome,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            identity=identity if identity.enforce_access else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/identity-sync-logs")
def console_identity_sync_logs(
    limit: int = Query(default=100, ge=1, le=200),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    _assert_super_manager(identity, "view identity sync logs")
    try:
        return get_console_identity_sync_logs(limit=limit, identity=identity)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _is_super_manager(identity: IdentityContext) -> bool:
    return identity.enforce_access and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}


def _assert_super_manager(identity: IdentityContext, action: str) -> None:
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _is_super_manager(identity):
        raise HTTPException(status_code=403, detail=f"Only super administrators can {action}")


@router.get("/api/console/ingestion-tasks")
def console_ingestion_tasks(
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        return get_console_ingestion_tasks(
            keyword=keyword,
            status=status,
            strategy=strategy,
            page=page,
            page_size=page_size,
            identity=identity if identity.enforce_access else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/ingestion-logs/latest")
def console_latest_ingestion_log(
    kb_id: str | None = Query(default=None),
    max_lines: int = Query(default=500, ge=1, le=2000),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        return get_latest_ingestion_log(
            kb_id=kb_id,
            max_lines=max_lines,
            identity=identity if identity.enforce_access else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/ingestion-tasks/{task_id}/backfill-llm-usage")
def backfill_ingestion_llm_usage_route(
    task_id: str,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    assert_fresh_identity_snapshot(
        identity,
        action="ingestion_llm_usage.backfill",
        resource_type="ingestion_task",
        resource_id=task_id,
    )
    if identity.enforce_access and not (identity.is_tenant_admin or identity.is_platform_admin):
        raise HTTPException(status_code=403, detail="Only tenant or platform administrators can backfill usage")
    try:
        if identity.enforce_access:
            require_task_access(get_task(task_id), identity, action="ingestion_llm_usage.backfill")
        return backfill_ingestion_llm_usage(task_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/query-logs/export.csv")
def export_console_query_logs_route(
    kb_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    api_key_id: str | None = Query(default=None),
    pipeline_domain: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=10000, ge=1, le=100000),
    identity: IdentityContext = Depends(get_current_identity),
) -> Response:
    assert_fresh_identity_snapshot(
        identity,
        action="query_logs.export",
        resource_type="query_logs",
        kb_id=kb_id,
    )
    try:
        if identity.enforce_access:
            if not (identity.is_tenant_admin or identity.is_platform_admin):
                if not kb_id:
                    raise HTTPException(status_code=403, detail="kb_id is required for query log export")
                require_kb_access(kb_id, identity, action="query_logs.export", resource_type="query_logs", resource_id=kb_id)
        filename, content = export_console_query_logs(
            tenant_id=identity.tenant_id if identity.enforce_access else None,
            kb_id=kb_id,
            request_id=request_id,
            actor_id=actor_id,
            api_key_id=api_key_id,
            pipeline_domain=pipeline_domain,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            identity=identity if identity.enforce_access else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": build_csv_content_disposition(filename)},
    )


@router.get("/api/console/token-usage")
def console_token_usage(
    limit: int = Query(default=10, ge=1, le=50),
    pipeline_domain: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        return get_console_token_usage(
            limit=limit,
            identity=identity if identity.enforce_access else None,
            pipeline_domain=pipeline_domain,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/api-keys")
def console_api_keys(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        return get_console_api_keys(identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/openapi-apps")
def console_openapi_apps(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        return get_console_openapi_apps(identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/openapi-apps", status_code=201)
def create_openapi_app_route(
    payload: OpenApiAppCreatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    assert_fresh_identity_snapshot(identity, action="openapi_app.create", resource_type="openapi_app")
    try:
        return create_console_openapi_app(payload.model_dump(), identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/api/console/openapi-apps/{app_id}")
def update_openapi_app_route(
    app_id: str,
    payload: OpenApiAppUpdatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    assert_fresh_identity_snapshot(identity, action="openapi_app.update", resource_type="openapi_app", resource_id=app_id)
    try:
        result = update_console_openapi_app(
            app_id,
            payload.model_dump(exclude_unset=True),
            identity if identity.enforce_access else None,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"OpenAPI app '{app_id}' not found")
    return result


@router.delete("/api/console/openapi-apps/{app_id}")
def delete_openapi_app_route(app_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    assert_fresh_identity_snapshot(identity, action="openapi_app.delete", resource_type="openapi_app", resource_id=app_id)
    try:
        deleted = delete_console_openapi_app(app_id, identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"OpenAPI app '{app_id}' not found")
    return {"deleted": True, "id": app_id}


@router.post("/api/console/api-keys", status_code=201)
def create_api_key_route(
    payload: ApiKeyCreatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    assert_fresh_identity_snapshot(identity, action="api_key.create", resource_type="api_key")
    try:
        return create_console_api_key(payload.model_dump(), identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/api/console/api-keys/{key_id}")
def update_api_key_route(
    key_id: str,
    payload: ApiKeyUpdatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    assert_fresh_identity_snapshot(identity, action="api_key.update", resource_type="api_key", resource_id=key_id)
    try:
        result = update_console_api_key(
            key_id,
            payload.model_dump(exclude_unset=True),
            identity if identity.enforce_access else None,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"API Key '{key_id}' not found")
    return result


@router.post("/api/console/api-keys/{key_id}/rotate")
def rotate_api_key_route(key_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    assert_fresh_identity_snapshot(identity, action="api_key.rotate", resource_type="api_key", resource_id=key_id)
    try:
        result = rotate_console_api_key(key_id, identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"API Key '{key_id}' not found")
    return result


@router.delete("/api/console/api-keys/{key_id}")
def delete_api_key_route(key_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    assert_fresh_identity_snapshot(identity, action="api_key.delete", resource_type="api_key", resource_id=key_id)
    try:
        deleted = delete_console_api_key(key_id, identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"API Key '{key_id}' not found")
    return {"deleted": True, "id": key_id}


@router.get("/api/console/settings")
def console_settings() -> list[dict]:
    return get_settings_payload()


@router.put("/api/console/settings")
def update_console_settings(payload: dict, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    safe_payload = sanitize_console_settings_update(payload)
    assert_fresh_identity_snapshot(identity, action="settings.update", resource_type="settings", resource_id="runtime")
    try:
        return persist_console_settings(
            safe_payload,
            identity=identity if identity.enforce_access else None,
            updated_by=identity.user_id if identity.enforce_access else "console",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
