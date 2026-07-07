"""
控制台管理路由模块

这个模块提供了控制台管理后台的所有接口,包括:
- 系统概览和监控(指标、告警、队列)
- 查询日志和审计日志
- 导入任务管理
- API Key 管理
- OpenAPI 应用管理
- 系统配置管理
- Token 用量统计

这些接口主要用于管理员在控制台中查看和管理系统。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.services.access_control import require_kb_access, require_task_access
from backend.services.document_export_service import build_csv_content_disposition
from backend.services.console_service import (
    EDITABLE_SETTINGS_KEYS,
    create_console_api_key,
    create_console_external_system_config,
    create_console_openapi_app,
    delete_console_api_key,
    cleanup_console_tasks,
    delete_console_task,
    delete_console_external_system_config,
    delete_console_openapi_app,
    export_console_query_logs,
    get_console_alerts,
    get_console_app_usage,
    get_console_audit_logs,
    get_console_api_keys,
    get_console_external_system_configs,
    get_console_setting_versions,
    get_console_task_queue,
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
    mark_console_task_failed,
    rotate_console_api_key,
    rollback_console_settings_version,
    sanitize_console_settings_update,
    update_console_api_key,
    update_console_external_system_config,
    update_console_openapi_app,
    update_console_settings_with_audit as persist_console_settings,
)
from backend.services.ingestion_service import backfill_ingestion_llm_usage, get_task
from backend.services.identity_service import audit_access_denied, assert_fresh_identity_snapshot, get_current_identity
from core.db.identity import TENANT_ADMIN_ROLE_CODE, IdentityContext

router = APIRouter()


class ApiKeyCreatePayload(BaseModel):
    """API Key 创建请求参数"""
    model_config = ConfigDict(extra="forbid")

    appId: str | None = Field(default=None, max_length=64)              # 应用ID
    name: str = Field(..., min_length=1, max_length=100)                # API Key 名称
    kbIds: list[str] = Field(default_factory=list, max_length=20)         # 授权的知识库ID列表
    capabilities: list[str] = Field(default_factory=lambda: ["rag.query", "rag.graph_query"], min_length=1)  # 权限列表
    requireSignature: bool = True                                        # 是否要求签名验证
    allowedIps: list[str] = Field(default_factory=list, max_length=50)  # IP 白名单
    rpmLimit: int = Field(default=0, ge=0)                             # 每分钟请求限制
    dailyRequestLimit: int = Field(default=0, ge=0)                    # 每日请求限制
    concurrentLimit: int = Field(default=0, ge=0)                      # 并发限制
    monthlyRequestLimit: int = Field(default=0, ge=0)                  # 每月请求限制
    note: str = Field(default="", max_length=500)                       # 备注说明
    expiresAt: datetime | None = None                                   # 过期时间


class OpenApiAppCreatePayload(BaseModel):
    """OpenAPI 应用创建请求参数"""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)  # 应用名称
    note: str = Field(default="", max_length=500)         # 备注说明


class OpenApiAppUpdatePayload(BaseModel):
    """OpenAPI 应用更新请求参数"""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)    # 应用名称
    status: str | None = Field(default=None, pattern="^(active|disabled)$") # 状态
    note: str | None = Field(default=None, max_length=500)                  # 备注说明


class ExternalSystemConfigCreatePayload(BaseModel):
    """外部系统配置创建请求参数"""
    model_config = ConfigDict(extra="forbid")

    ssoBaseUrl: str = Field(..., min_length=1, max_length=2048)
    ssoClientId: str = Field(..., min_length=1, max_length=255)
    ssoClientSecret: str = Field(..., min_length=1, max_length=2048)
    ssoRedirectUri: str = Field(..., min_length=1, max_length=2048)
    ssoLaunchBaseUrl: str = Field(default="", max_length=2048)
    ssoLaunchPath: str = Field(default="/sso", min_length=1, max_length=512)
    ssoExchangePath: str = Field(default="/ai/system/internal/sso/exchange", min_length=1, max_length=512)
    ssoUserSnapshotPathTemplate: str = Field(
        default="/ai/system/internal/identity/snapshot/users/{userId}",
        min_length=1,
        max_length=512,
    )
    ssoDeltaPath: str = Field(default="/ai/system/internal/identity/snapshot/delta", min_length=1, max_length=512)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class ExternalSystemConfigUpdatePayload(BaseModel):
    """外部系统配置更新请求参数"""
    model_config = ConfigDict(extra="forbid")

    ssoBaseUrl: str | None = Field(default=None, min_length=1, max_length=2048)
    ssoClientId: str | None = Field(default=None, min_length=1, max_length=255)
    ssoRedirectUri: str | None = Field(default=None, min_length=1, max_length=2048)
    ssoLaunchBaseUrl: str | None = Field(default=None, max_length=2048)
    ssoLaunchPath: str | None = Field(default=None, min_length=1, max_length=512)
    ssoExchangePath: str | None = Field(default=None, min_length=1, max_length=512)
    ssoUserSnapshotPathTemplate: str | None = Field(default=None, min_length=1, max_length=512)
    ssoDeltaPath: str | None = Field(default=None, min_length=1, max_length=512)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class ApiKeyUpdatePayload(BaseModel):
    """API Key 更新请求参数"""
    model_config = ConfigDict(extra="forbid")

    appId: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    kbIds: list[str] | None = Field(default=None, max_length=20)
    capabilities: list[str] | None = Field(default=None, min_length=1)
    requireSignature: bool | None = None
    allowedIps: list[str] | None = Field(default=None, max_length=50)
    rpmLimit: int | None = Field(default=None, ge=0)
    dailyRequestLimit: int | None = Field(default=None, ge=0)
    concurrentLimit: int | None = Field(default=None, ge=0)
    monthlyRequestLimit: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    expiresAt: datetime | None = None


class TaskQueueMarkFailedPayload(BaseModel):
    """任务队列标记失败请求参数"""
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class TaskQueueCleanupPayload(BaseModel):
    """任务队列清理请求参数"""
    model_config = ConfigDict(extra="forbid")

    statuses: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    olderThanSeconds: int = Field(default=7 * 24 * 60 * 60, ge=0)
    includeStaleActive: bool = False


@router.get("/api/console/overview-metrics")
def overview_metrics(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """获取控制台概览指标"""
    try:
        return get_console_metrics(identity if identity.enforce_access else None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/alerts")
def console_alerts() -> list[dict]:
    """获取系统告警列表"""
    try:
        return get_console_alerts()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/queue")
def console_queue() -> list[dict]:
    """获取处理队列状态"""
    try:
        return get_console_queue()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/evaluations")
def console_evaluations(
    kb_id: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    """获取评估结果列表"""
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
    """获取查询日志列表"""
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
    """获取审计日志列表"""
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
    """获取身份同步日志"""
    _assert_super_manager(identity, "view identity sync logs")
    try:
        return get_console_identity_sync_logs(limit=limit, identity=identity)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _is_super_manager(identity: IdentityContext) -> bool:
    return identity.enforce_access and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}


def _assert_super_manager(identity: IdentityContext, action: str) -> None:
    if not identity.enforce_access:
        audit_access_denied(
            identity,
            action=action,
            resource_type="console_admin",
            reason_code="NOT_AUTHENTICATED",
            risk_level="medium",
        )
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _is_super_manager(identity):
        audit_access_denied(
            identity,
            action=action,
            resource_type="console_admin",
            reason_code="SUPER_MANAGER_REQUIRED",
            risk_level="medium",
            metadata={"requiredRole": TENANT_ADMIN_ROLE_CODE},
        )
        raise HTTPException(status_code=403, detail=f"Only super administrators can {action}")


@router.get("/api/console/ingestion-tasks")
def console_ingestion_tasks(
    keyword: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """获取导入任务列表"""
    try:
        return get_console_ingestion_tasks(
            keyword=keyword,
            task_id=task_id,
            status=status,
            strategy=strategy,
            page=page,
            page_size=page_size,
            identity=identity if identity.enforce_access else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/ingestion-logs/latest")
def console_latest_ingestion_log(
    kb_id: str | None = Query(default=None),
    max_lines: int = Query(default=500, ge=1, le=2000),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """获取最新的导入日志"""
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
    """回填导入任务的 LLM 用量"""
    assert_fresh_identity_snapshot(
        identity,
        action="ingestion_llm_usage.backfill",
        resource_type="ingestion_task",
        resource_id=task_id,
    )
    if identity.enforce_access and not (identity.is_tenant_admin or identity.is_platform_admin):
        audit_access_denied(
            identity,
            action="ingestion_llm_usage.backfill",
            resource_type="ingestion_task",
            resource_id=task_id,
            reason_code="ADMIN_REQUIRED",
            risk_level="medium",
        )
        raise HTTPException(status_code=403, detail="Only tenant or platform administrators can backfill usage")
    try:
        if identity.enforce_access:
            require_task_access(get_task(task_id), identity, action="ingestion_llm_usage.backfill")
        return backfill_ingestion_llm_usage(task_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/task-queue")
def console_task_queue(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取任务队列状态

    查询系统任务队列的当前状态，包括正在处理的任务、等待中的任务等。
    用于监控系统负载和任务执行情况。

    参数:
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 任务队列状态
            - pending: 等待中的任务数量
            - running: 正在执行的任务数量
            - completed: 已完成的任务数量（统计周期内）
            - failed: 失败的任务数量
            - items: 任务详情列表
            - limits: 并发限制配置

    使用场景:
        - 系统管理员监控任务队列
        - 查看系统处理负载
        - 发现和处理积压任务

    权限要求:
        - 仅超级管理员可访问

    请求示例:
        ```bash
        GET /api/console/task-queue
        Authorization: Bearer <session_token>
        ```

    响应示例:
        ```json
        {
          "pending": 5,
          "running": 3,
          "completed": 120,
          "failed": 2,
          "items": [
            {
              "taskId": "task_001",
              "kbId": "kb_demo",
              "filename": "document.pdf",
              "status": "running",
              "startedAt": "2026-07-06T10:00:00Z",
              "progress": 45
            }
          ],
          "limits": {
            "maxConcurrent": 10,
            "maxPerTenant": 5
          }
        }
        ```

    错误情况:
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 503: 服务不可用
    """
    _assert_super_manager(identity, "view task queue")
    try:
        return get_console_task_queue(identity)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/task-queue/{task_id}/mark-failed")
def mark_task_queue_failed_route(
    task_id: str,
    payload: TaskQueueMarkFailedPayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    标记任务为失败状态

    将正在执行或卡住的任务手动标记为失败状态，释放队列资源。
    通常用于处理异常任务或长时间无响应的任务。

    参数:
        task_id: 任务 ID（路径参数）
        payload: 标记请求体
            - reason: 失败原因说明（可选，最大 500 字符）
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 操作结果
            - taskId: 任务 ID
            - status: 更新后的状态（"failed"）
            - markedAt: 标记时间
            - reason: 失败原因

    使用场景:
        - 处理卡住的导入任务
        - 强制结束异常任务
        - 释放队列资源

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        POST /api/console/task-queue/task_001/mark-failed
        Authorization: Bearer <session_token>
        Content-Type: application/json

        {
          "reason": "任务执行超时，手动终止"
        }
        ```

    响应示例:
        ```json
        {
          "taskId": "task_001",
          "status": "failed",
          "markedAt": "2026-07-06T10:30:00Z",
          "reason": "任务执行超时，手动终止"
        }
        ```

    错误情况:
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 404: 任务不存在
        - 503: 服务不可用
    """
    _assert_super_manager(identity, "mark task queue item failed")
    try:
        return mark_console_task_failed(task_id, reason=payload.reason, identity=identity)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/console/task-queue/{task_id}")
def delete_task_queue_item_route(
    task_id: str,
    force: bool = Query(default=False),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    删除任务队列项

    从任务队列中删除指定的任务记录。可用于清理已完成或失败的历史任务，
    或强制删除正在执行的任务（需要 force=true）。

    参数:
        task_id: 任务 ID（路径参数）
        force: 是否强制删除。默认 False。
            - False: 只能删除已完成或失败的任务
            - True: 可以删除正在执行的任务（谨慎使用）
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 删除结果
            - deleted: 是否删除成功
            - taskId: 任务 ID
            - status: 删除前的任务状态

    使用场景:
        - 清理已完成的历史任务
        - 删除失败的任务记录
        - 强制终止卡住的任务（force=true）

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        # 删除已完成的任务
        DELETE /api/console/task-queue/task_001

        # 强制删除正在执行的任务
        DELETE /api/console/task-queue/task_001?force=true
        Authorization: Bearer <session_token>
        ```

    响应示例:
        ```json
        {
          "deleted": true,
          "taskId": "task_001",
          "status": "completed"
        }
        ```

    错误情况:
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 404: 任务不存在
        - 409: 任务正在执行且未设置 force=true
        - 503: 服务不可用

    注意事项:
        - 使用 force=true 删除正在执行的任务可能导致数据不完整
        - 建议先使用 mark-failed 标记失败后再删除
    """
    _assert_super_manager(identity, "delete task queue item")
    try:
        return delete_console_task(task_id, force=force, identity=identity)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/task-queue/cleanup")
def cleanup_task_queue_route(
    payload: TaskQueueCleanupPayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    批量清理任务队列

    根据指定条件批量清理任务队列中的历史记录，释放存储空间。
    支持按状态和时间范围筛选要清理的任务。

    参数:
        payload: 清理请求体
            - statuses: 要清理的状态列表，如 ["completed", "failed"]
            - olderThanSeconds: 清理多久之前的任务，默认 7 天（604800 秒）
            - includeStaleActive: 是否清理卡住的活跃任务（长时间无更新），默认 False
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 清理结果
            - deleted: 删除的任务数量
            - statuses: 清理的状态列表
            - olderThanSeconds: 时间筛选条件
            - staleActiveCleaned: 清理的卡住任务数量

    使用场景:
        - 定期清理历史任务记录
        - 释放存储空间
        - 清理积压的失败任务
        - 处理长时间无响应的任务

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        POST /api/console/task-queue/cleanup
        Authorization: Bearer <session_token>
        Content-Type: application/json

        {
          "statuses": ["completed", "failed"],
          "olderThanSeconds": 604800,
          "includeStaleActive": false
        }
        ```

    响应示例:
        ```json
        {
          "deleted": 150,
          "statuses": ["completed", "failed"],
          "olderThanSeconds": 604800,
          "staleActiveCleaned": 0
        }
        ```

    错误情况:
        - 400: 请求参数无效（如 statuses 为空）
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 503: 服务不可用
    """
    _assert_super_manager(identity, "cleanup task queue")
    try:
        return cleanup_console_tasks(
            statuses=payload.statuses,
            older_than_seconds=payload.olderThanSeconds,
            include_stale_active=payload.includeStaleActive,
            identity=identity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    """导出查询日志为 CSV"""
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
                    audit_access_denied(
                        identity,
                        action="query_logs.export",
                        resource_type="query_logs",
                        reason_code="KB_ID_REQUIRED_FOR_NON_ADMIN_EXPORT",
                        risk_level="medium",
                    )
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
    except HTTPException:
        raise
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
    """获取 Token 用量统计"""
    try:
        return get_console_token_usage(
            limit=limit,
            identity=identity if identity.enforce_access else None,
            pipeline_domain=pipeline_domain,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/app-usage")
def console_app_usage(
    app_id: str | None = Query(default=None),
    api_key_id: str | None = Query(default=None),
    kb_id: str | None = Query(default=None),
    pipeline_domain: str | None = Query(default=None),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """获取应用用量统计"""
    try:
        return get_console_app_usage(
            limit=limit,
            identity=identity if identity.enforce_access else None,
            app_id=app_id,
            api_key_id=api_key_id,
            kb_id=kb_id,
            pipeline_domain=pipeline_domain,
            start_at=start_at,
            end_at=end_at,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/api-keys")
def console_api_keys(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """获取 API Key 列表"""
    try:
        return get_console_api_keys(identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/openapi-apps")
def console_openapi_apps(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """获取 OpenAPI 应用列表"""
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
    """创建 OpenAPI 应用"""
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
    """更新 OpenAPI 应用"""
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
    """删除 OpenAPI 应用"""
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
    """创建 API Key"""
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
    """更新 API Key"""
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
    """轮换 API Key 密钥"""
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
    """删除 API Key"""
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


@router.get("/api/console/external-system-configs")
def console_external_system_configs(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """
    获取外部系统配置列表

    查询系统中配置的外部系统集成信息，主要用于 SSO 单点登录配置。
    返回配置列表但不包含敏感信息（如密钥）。

    参数:
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        list[dict]: 外部系统配置列表
            - configId: 配置 ID
            - ssoBaseUrl: SSO 服务基础 URL
            - ssoClientId: 客户端 ID
            - ssoRedirectUri: 回调地址
            - status: 配置状态（"active" | "disabled"）
            - createdAt: 创建时间
            - updatedAt: 更新时间

    使用场景:
        - 管理员查看 SSO 配置
        - 检查外部系统集成状态
        - 配置管理审计

    权限要求:
        - 仅超级管理员可访问

    请求示例:
        ```bash
        GET /api/console/external-system-configs
        Authorization: Bearer <session_token>
        ```

    响应示例:
        ```json
        [
          {
            "configId": "cfg_001",
            "ssoBaseUrl": "https://ai-base.example.com",
            "ssoClientId": "client_abc123",
            "ssoRedirectUri": "https://rag.example.com/api/auth/ai-base/callback",
            "ssoLaunchPath": "/sso",
            "status": "active",
            "createdAt": "2026-07-01T00:00:00Z",
            "updatedAt": "2026-07-06T10:00:00Z"
          }
        ]
        ```

    错误情况:
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 503: 服务不可用

    安全提示:
        - 响应中不包含 ssoClientSecret 等敏感字段
    """
    _assert_super_manager(identity, "view external system configurations")
    try:
        return get_console_external_system_configs(identity)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/external-system-configs", status_code=201)
def create_external_system_config_route(
    payload: ExternalSystemConfigCreatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    创建外部系统配置

    创建新的外部系统集成配置，用于配置 AI Base SSO 单点登录。
    创建成功后，系统将能够与指定的 AI Base 实例进行身份认证集成。

    参数:
        payload: 创建请求体
            - ssoBaseUrl: SSO 服务基础 URL（必填，最大 2048 字符）
            - ssoClientId: OAuth 客户端 ID（必填，最大 255 字符）
            - ssoClientSecret: OAuth 客户端密钥（必填，最大 2048 字符）
            - ssoRedirectUri: OAuth 回调地址（必填，最大 2048 字符）
            - ssoLaunchBaseUrl: SSO 启动页基础 URL（可选）
            - ssoLaunchPath: SSO 启动路径，默认 "/sso"
            - ssoExchangePath: Token 交换路径
            - ssoUserSnapshotPathTemplate: 用户快照路径模板
            - ssoDeltaPath: 增量同步路径
            - status: 状态，默认 "active"
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 创建的配置信息（不含密钥）
            - configId: 新创建的配置 ID
            - ssoBaseUrl: SSO 服务基础 URL
            - ssoClientId: 客户端 ID
            - status: 配置状态
            - createdAt: 创建时间

    使用场景:
        - 首次配置 AI Base SSO 集成
        - 添加新的外部身份提供商
        - 多租户环境的 SSO 配置

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        POST /api/console/external-system-configs
        Authorization: Bearer <session_token>
        Content-Type: application/json

        {
          "ssoBaseUrl": "https://ai-base.example.com",
          "ssoClientId": "client_abc123",
          "ssoClientSecret": "secret_xyz789",
          "ssoRedirectUri": "https://rag.example.com/api/auth/ai-base/callback",
          "ssoLaunchPath": "/sso",
          "status": "active"
        }
        ```

    响应示例:
        ```json
        {
          "configId": "cfg_new001",
          "ssoBaseUrl": "https://ai-base.example.com",
          "ssoClientId": "client_abc123",
          "status": "active",
          "createdAt": "2026-07-06T10:00:00Z"
        }
        ```

    错误情况:
        - 400: 请求参数无效
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 503: 服务不可用

    安全提示:
        - ssoClientSecret 会被加密存储，响应中不会返回
    """
    _assert_super_manager(identity, "create external system configurations")
    try:
        return create_console_external_system_config(payload.model_dump(), identity)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/api/console/external-system-configs/{config_id}")
def update_external_system_config_route(
    config_id: str,
    payload: ExternalSystemConfigUpdatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    更新外部系统配置

    更新已存在的外部系统集成配置。支持部分更新，只传需要修改的字段。

    参数:
        config_id: 配置 ID（路径参数）
        payload: 更新请求体（所有字段可选）
            - ssoBaseUrl: SSO 服务基础 URL
            - ssoClientId: OAuth 客户端 ID
            - ssoRedirectUri: OAuth 回调地址
            - ssoLaunchBaseUrl: SSO 启动页基础 URL
            - ssoLaunchPath: SSO 启动路径
            - ssoExchangePath: Token 交换路径
            - ssoUserSnapshotPathTemplate: 用户快照路径模板
            - ssoDeltaPath: 增量同步路径
            - status: 状态（"active" | "disabled"）
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 更新后的配置信息（不含密钥）
            - configId: 配置 ID
            - 更新的字段...
            - updatedAt: 更新时间

    使用场景:
        - 修改 SSO 服务地址
        - 更新客户端凭证
        - 启用/禁用配置
        - 调整回调地址

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        PATCH /api/console/external-system-configs/cfg_001
        Authorization: Bearer <session_token>
        Content-Type: application/json

        {
          "status": "disabled"
        }
        ```

    响应示例:
        ```json
        {
          "configId": "cfg_001",
          "ssoBaseUrl": "https://ai-base.example.com",
          "ssoClientId": "client_abc123",
          "status": "disabled",
          "updatedAt": "2026-07-06T11:00:00Z"
        }
        ```

    错误情况:
        - 400: 请求参数无效
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 404: 配置不存在
        - 503: 服务不可用
    """
    _assert_super_manager(identity, "update external system configurations")
    try:
        result = update_console_external_system_config(
            config_id,
            payload.model_dump(exclude_unset=True),
            identity,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"External system config '{config_id}' not found")
    return result


@router.delete("/api/console/external-system-configs/{config_id}")
def delete_external_system_config_route(
    config_id: str,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    删除外部系统配置

    删除指定的外部系统集成配置。删除后，相关的 SSO 登录将不可用，
    请确保没有用户正在使用此配置。

    参数:
        config_id: 配置 ID（路径参数）
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 删除结果
            - deleted: 是否删除成功
            - id: 被删除的配置 ID

    使用场景:
        - 移除不再使用的外部系统集成
        - 清理测试配置
        - 更换 SSO 服务商前的清理

    权限要求:
        - 仅超级管理员可操作

    请求示例:
        ```bash
        DELETE /api/console/external-system-configs/cfg_001
        Authorization: Bearer <session_token>
        ```

    响应示例:
        ```json
        {
          "deleted": true,
          "id": "cfg_001"
        }
        ```

    错误情况:
        - 401: 未登录
        - 403: 非超级管理员，权限不足
        - 404: 配置不存在
        - 503: 服务不可用

    注意事项:
        - 删除操作不可恢复
        - 建议先禁用（status: disabled）观察一段时间后再删除
    """
    _assert_super_manager(identity, "delete external system configurations")
    try:
        deleted = delete_console_external_system_config(config_id, identity)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"External system config '{config_id}' not found")
    return {"deleted": True, "id": config_id}


@router.get("/api/console/settings")
def console_settings() -> list[dict]:
    """获取系统配置"""
    return get_settings_payload()


@router.put("/api/console/settings")
def update_console_settings(payload: dict, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """更新系统配置"""
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


@router.get("/api/console/settings/versions")
def console_settings_versions(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    """获取系统配置版本列表"""
    try:
        return get_console_setting_versions(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/settings/versions/{version_id}/rollback")
def rollback_console_settings(version_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """回滚系统配置版本"""
    assert_fresh_identity_snapshot(identity, action="settings.rollback", resource_type="settings", resource_id=version_id)
    try:
        return rollback_console_settings_version(
            version_id,
            identity=identity if identity.enforce_access else None,
            updated_by=identity.user_id if identity.enforce_access else "console",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
