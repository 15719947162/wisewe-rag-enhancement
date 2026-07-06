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
    create_console_openapi_app,
    delete_console_api_key,
    delete_console_openapi_app,
    export_console_query_logs,
    get_console_alerts,
    get_console_audit_logs,
    get_console_api_keys,
    get_console_openapi_apps,
    delete_console_openapi_app,
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
    """API Key 创建请求参数"""
    model_config = ConfigDict(extra="forbid")

    appId: str | None = Field(default=None, max_length=64)              # 应用ID
    name: str = Field(..., min_length=1, max_length=100)                # API Key 名称
    kbIds: list[str] = Field(..., min_length=1, max_length=20)         # 授权的知识库ID列表
    capabilities: list[str] = Field(default_factory=lambda: ["rag.query", "rag.graph_query"], min_length=1)  # 权限列表
    requireSignature: bool = True                                        # 是否要求签名验证
    allowedIps: list[str] = Field(default_factory=list, max_length=50)  # IP 白名单
    rpmLimit: int = Field(default=0, ge=0)                             # 每分钟请求限制
    dailyRequestLimit: int = Field(default=0, ge=0)                    # 每日请求限制
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


class ApiKeyUpdatePayload(BaseModel):
    """API Key 更新请求参数"""
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
    """
    获取控制台概览指标

    返回系统整体运行指标,包括知识库数量、文档数量、查询次数等。
    用于控制台首页展示系统概览。

    返回值:
        list[dict]: 指标列表,每个指标包含:
            - label: 指标名称
            - value: 指标值
            - trend: 趋势(可选)

    使用场景:
        - 控制台首页展示
        - 系统监控大盘
    """
    try:
        return get_console_metrics(identity if identity.enforce_access else None)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.get("/api/console/alerts")
def console_alerts() -> list[dict]:
    """
    获取系统告警列表

    返回当前系统中需要关注的告警信息,如任务失败、资源不足等。

    返回值:
        list[dict]: 告警列表,每个告警包含:
            - level: 告警级别(warning/error/critical)
            - message: 告警消息
            - timestamp: 时间戳

    使用场景:
        - 控制台告警展示
        - 系统健康监控
    """
    try:
        return get_console_alerts()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/queue")
def console_queue() -> list[dict]:
    """
    获取处理队列状态

    返回当前正在处理或等待处理的任务队列状态。

    返回值:
        list[dict]: 队列任务列表

    使用场景:
        - 监控系统负载
        - 查看任务排队情况
    """
    try:
        return get_console_queue()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/evaluations")
def console_evaluations(
    kb_id: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    """
    获取评估结果列表

    返回系统中的 RAG 评估结果,可以按知识库过滤。

    参数:
        kb_id: 知识库ID,可选,用于过滤特定知识库的评估

    返回值:
        list[dict]: 评估结果列表

    使用场景:
        - 查看历史评估记录
        - 对比不同配置的效果
    """
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
    """
    获取查询日志列表

    查询系统的 RAG 查询日志,支持多条件过滤。
    用于分析用户查询行为、问题诊断等。

    参数:
        kb_id: 知识库ID过滤
        request_id: 请求ID过滤
        actor_id: 操作者ID过滤
        api_key_id: API Key ID 过滤
        pipeline_domain: 管道域过滤(console/openapi)
        start_at: 开始时间
        end_at: 结束时间
        limit: 返回数量限制,默认 50,最大 200

    返回值:
        list[dict]: 查询日志列表

    使用场景:
        - 查看用户查询历史
        - 分析查询模式
        - 问题诊断和调试
    """
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
    """
    获取审计日志列表

    查询系统的审计日志,记录了所有重要操作,包括创建、修改、删除等。
    用于安全审计、问题追溯、合规检查等。

    参数:
        actor_id: 操作者ID过滤
        action: 操作类型过滤(如 create、update、delete)
        resource_type: 资源类型过滤(如 knowledge_base、document)
        resource_id: 资源ID过滤
        request_id: 请求ID过滤
        kb_id: 知识库ID过滤
        outcome: 结果过滤(success/failure)
        start_at: 开始时间
        end_at: 结束时间
        limit: 返回数量限制,默认 50,最大 200

    返回值:
        list[dict]: 审计日志列表

    使用场景:
        - 安全审计
        - 操作追溯
        - 问题诊断
        - 合规检查
    """
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
    """
    获取导入任务列表

    查询文档导入任务列表,支持按关键字、状态、策略过滤和分页。

    参数:
        keyword: 关键字搜索(匹配文件名)
        status: 任务状态过滤(pending/running/success/error)
        strategy: 切片策略过滤
        page: 页码,从 1 开始
        page_size: 每页数量,默认 20,最大 100

    返回值:
        dict: 分页结果
            - items: 任务列表
            - total: 总数量
            - page: 当前页
            - pageSize: 每页数量

    使用场景:
        - 控制台任务列表展示
        - 监控导入任务状态
        - 查找特定任务
    """
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
    """
    获取最新的导入日志

    返回最近一次导入任务的详细日志,用于调试和问题诊断。

    参数:
        kb_id: 知识库ID过滤,可选
        max_lines: 最大返回行数,默认 500,最大 2000

    返回值:
        dict: 日志内容
            - content: 日志文本
            - lines: 行数

    使用场景:
        - 查看导入任务详细执行过程
        - 排查导入失败原因
        - 调试导入流程
    """
    try:
        return get_latest_ingestion_log(
            kb_id=kb_id,
            max_lines=max_lines,
            identity=identity if identity.enforce_access else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/console/token-usage")
def console_token_usage(
    limit: int = Query(default=10, ge=1, le=50),
    pipeline_domain: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    获取 Token 用量统计

    统计系统的 LLM Token 使用量,用于成本分析和用量监控。

    参数:
        limit: 返回数量,默认 10,最大 50
        pipeline_domain: 管道域过滤(console/openapi)

    返回值:
        dict: Token 用量统计
            - total: 总用量
            - byModel: 按模型分组统计
            - byDay: 按天分组统计

    使用场景:
        - 成本分析
        - 用量监控
        - 预算管理
    """
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
    """
    获取 API Key 列表

    查询当前用户可访问的所有 API Key。

    返回值:
        list[dict]: API Key 列表,每个包含:
            - id: API Key ID
            - name: 名称
            - status: 状态
            - capabilities: 权限列表
            - createdAt: 创建时间

    使用场景:
        - 管理 API Key
        - 查看权限配置
    """
    try:
        return get_console_api_keys(identity if identity.enforce_access else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/console/api-keys", status_code=201)
def create_api_key_route(
    payload: ApiKeyCreatePayload,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    创建新的 API Key

    创建一个 API Key,用于外部系统访问 OpenAPI 接口。
    需要指定授权的知识库和权限范围。

    参数:
        payload: 创建参数,包含名称、知识库列表、权限等

    返回值:
        dict: 创建的 API Key 信息,包含:
            - id: API Key ID
            - key: 密钥(只在创建时返回一次)
            - name: 名称
            - status: 状态

    使用场景:
        - 为外部系统创建访问凭证
        - 配置 API 访问权限

    权限要求:
        - 需要管理 API Key 的权限
    """
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
    """
    更新 API Key 配置

    修改 API Key 的配置,如名称、权限、IP白名单等。
    不能修改密钥本身,如需更换密钥请使用轮换接口。

    参数:
        key_id: API Key ID
        payload: 更新参数

    返回值:
        dict: 更新后的 API Key 信息

    使用场景:
        - 修改权限范围
        - 调整访问限制
        - 启用/禁用 API Key
    """
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
    """
    轮换 API Key 密钥

    生成新的密钥并替换旧密钥,旧密钥立即失效。
    用于密钥泄露后的应急处理或定期轮换。

    参数:
        key_id: API Key ID

    返回值:
        dict: 新的密钥信息
            - id: API Key ID
            - key: 新密钥(只返回一次)

    使用场景:
        - 密钥泄露后紧急更换
        - 定期安全轮换
    """
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
    """
    删除 API Key

    永久删除指定的 API Key,删除后无法恢复。

    参数:
        key_id: API Key ID

    返回值:
        dict: 删除结果

    使用场景:
        - 清理不再使用的 API Key
        - 撤销访问权限
    """
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
    """
    获取系统配置

    返回当前系统的可配置项和值,用于控制台展示和编辑。

    返回值:
        list[dict]: 配置项列表

    使用场景:
        - 查看系统配置
        - 配置管理界面
    """
    return get_settings_payload()


@router.put("/api/console/settings")
def update_console_settings(payload: dict, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    更新系统配置

    修改系统配置项。只有特定的配置项允许修改,其他会被忽略。
    修改会记录审计日志。

    参数:
        payload: 配置更新字典

    返回值:
        dict: 更新后的配置

    使用场景:
        - 调整系统参数
        - 优化系统性能
    """
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
