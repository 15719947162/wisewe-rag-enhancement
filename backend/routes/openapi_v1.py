"""
OpenAPI v1 接口路由模块

这个模块提供了标准化的 OpenAPI 接口,用于外部系统集成。
主要功能包括:
- 知识库管理(查询知识库列表)
- 文档导入(上传 PDF 并处理)
- RAG 查询(向量检索和图谱检索)

所有 OpenAPI 接口都需要 API Key 认证,支持签名验证机制。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.identity_service import get_current_identity
from backend.services.ingestion_service import create_task, get_task, run_pipeline_and_confirm, run_pipeline_real, _task_to_payload
from backend.services.kb_service import get_knowledge_bases_payload
from backend.services.rag_service import run_graph_rag_query, run_rag_query
from core.chunker import list_strategies
from core.db.api_keys import ApiKeyAuthResult, ApiKeyError, ApiKeySignaturePayload, authenticate_api_key
from core.db.identity import IdentityContext
from core.db.query_logs import AuditLogRecord, append_audit_log
from core.parser.provider import PDF_PARSER_CHANNELS
from core.prompts import LAYOUT_KEY_MAP, SUBJECT_KEY_MAP


router = APIRouter()

# 文件上传大小限制:500MB
MAX_OPENAPI_UPLOAD_SIZE = 500 * 1024 * 1024

# 文档主题类型选项(用于前端下拉框)
SUBJECT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in SUBJECT_KEY_MAP.items()
]

# 文档布局类型选项(用于前端下拉框)
LAYOUT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in LAYOUT_KEY_MAP.items()
]


class OpenApiQueryRequest(BaseModel):
    """OpenAPI 向量检索请求参数"""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)     # 查询文本
    kb_id: str | None = Field(default=None, max_length=255)    # 知识库ID
    top_k: int = Field(default=8, ge=1, le=20)                # 返回数量
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)    # 最小相似度
    use_llm_check: bool = False                                # 是否用 LLM 过滤
    use_llm_score: bool = False                                # 是否用 LLM 评分


class OpenApiGraphQueryRequest(BaseModel):
    """OpenAPI 图谱检索请求参数"""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)     # 查询文本
    kb_id: str | None = Field(default=None, max_length=255)    # 知识库ID
    top_k: int = Field(default=5, ge=1, le=20)                # 返回数量
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)    # 最小相似度
    explain: bool = False                                       # 是否返回解释
    intent: str | None = Field(default=None, max_length=100)   # 用户意图


class PromptAppendRequest(BaseModel):
    """提示词追加请求(用于自定义清洗/质检提示词)"""
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="append", pattern="^append$")    # 模式:只支持追加
    content: str = Field(..., min_length=1, max_length=2000)   # 提示词内容


@router.get("/openapi/v1/knowledge-bases")
async def openapi_knowledge_bases(
    request: Request,
    scope: str = Query(default="mine", pattern="^(mine|tenant|all)$"),
    user_id: str | None = Query(default=None, max_length=255),
    role_code: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    查询知识库列表

    根据 API Key 的权限范围,返回可访问的知识库列表。
    支持按范围、用户、角色过滤。

    参数:
        scope: 查询范围
            - mine: 我的知识库
            - tenant: 租户的所有知识库
            - all: 所有知识库(需要特殊权限)
        user_id: 按用户ID过滤
        role_code: 按角色代码过滤
        page: 页码,从 1 开始
        page_size: 每页数量

    认证方式:
        - Authorization: Bearer <api_key>
        - 或 X-API-Key: <api_key>
        - 可选签名验证(通过 X-KB-* 头)

    返回值:
        JSONResponse: 知识库列表
            - requestId: 请求ID
            - data: 包含知识库数组分页数据

    使用场景:
        - 外部系统查询知识库
        - 集成到第三方应用
        - 自动化脚本查询

    错误情况:
        - 401: API Key 无效
        - 403: 权限不足
    """
    request_id = _request_id()
    guard = _guard_openapi_capability(
        request_id,
        capability="kb.list",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=b"",
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(guard, JSONResponse):
        return guard

    items = get_knowledge_bases_payload(guard.identity)
    allowed = set(guard.kb_ids)
    filtered = [item for item in items if item.get("id") in allowed]
    if scope == "mine":
        filtered = [
            item for item in filtered
            if not user_id or str(item.get("ownerUserId") or item.get("createdBy") or "") == user_id
        ]
    elif scope == "all" and "kb.list.all" not in guard.capabilities:
        return _error_response(403, "CAPABILITY_DENIED", "API Key lacks kb.list.all capability", request_id=request_id)

    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]
    data = {
        "scope": scope,
        "userId": user_id,
        "roleCode": role_code,
        "items": page_items,
        "total": len(filtered),
        "page": page,
        "pageSize": page_size,
    }
    return JSONResponse({"requestId": request_id, "data": data})



@router.get("/openapi/v1/ingestion/options")
async def openapi_ingestion_options(
    request: Request,
    include_unavailable: bool = Query(default=True),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    获取文档导入选项配置

    返回所有可用的导入选项,包括切片策略、文档主题类型、文档布局类型、
    PDF 解析器等。前端可以根据这些选项构建配置界面。

    参数:
        include_unavailable: 是否包含不可用的选项,默认 True

    返回值:
        JSONResponse: 可配置选项列表
            - chunkStrategies: 切片策略列表
            - subjectTypes: 文档主题类型列表
            - layoutTypes: 文档布局类型列表
            - parserProviders: PDF 解析器列表(包含可用性标识)
            - notes: 重要说明

    使用场景:
        - 前端获取配置选项
        - 集成系统查询支持的参数
        - 检查解析器可用性
    """
    request_id = _request_id()
    guard = _guard_openapi_capability(
        request_id,
        capability="ingestion.options",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=b"",
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(guard, JSONResponse):
        return guard

    parser_providers = []
    for channel in PDF_PARSER_CHANNELS.values():
        available, reason = _parser_provider_availability(channel.key)
        if available or include_unavailable:
            parser_providers.append(
                {
                    "value": channel.key,
                    "label": channel.label,
                    "description": channel.description,
                    "available": available,
                    "reason": reason,
                }
            )
    data = {
        "chunkStrategies": [{"value": value, "label": _strategy_label(value)} for value in list_strategies()],
        "subjectTypes": SUBJECT_OPTIONS,
        "layoutTypes": LAYOUT_OPTIONS,
        "parserProviders": parser_providers,
        "notes": [
            "parser_provider 当前作为 OpenAPI 请求元数据接收；真实执行仍以运行时 PDF_PARSER_PROVIDER 管道为准，单次覆盖需后续管道改造。",
            "清洗 / 质检提示词第一期只允许 append 型补充，不允许 raw system prompt replace。",
        ],
    }
    return JSONResponse({"requestId": request_id, "data": data})



@router.get("/openapi/v1/ingestion/tasks/{task_id}")
async def openapi_ingestion_task(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    查询导入任务详情

    通过任务 ID 查询导入任务的详细状态和信息。

    参数:
        task_id: 任务 ID

    返回值:
        JSONResponse: 任务详情
            - requestId: 请求ID
            - data: 任务信息(状态、文件名、策略等)

    使用场景:
        - 外部系统查询导入进度
        - 自动化脚本监控任务状态
        - 回调通知时查询结果

    错误情况:
        - 404: 任务不存在
    """
    request_id = _request_id()
    task = get_task(task_id)
    if not task:
        return _error_response(404, "TASK_NOT_FOUND", "Task not found or not accessible", request_id=request_id)
    guard = _guard_openapi_call(
        str(task.get("kb_id") or ""),
        IdentityContext(),
        request_id,
        capability="ingestion.read",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=b"",
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(guard, JSONResponse):
        return guard
    return JSONResponse({"requestId": request_id, "data": _task_to_payload(task)})



@router.post("/openapi/v1/ingestion/upload", status_code=202)
async def openapi_ingestion_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    kb_id: str = Form(..., max_length=255),
    chunk_strategy: str = Form(default="hierarchical", max_length=100),
    subject_type: str = Form(default="general", max_length=100),
    layout_type: str = Form(default="single_column", max_length=100),
    parser_provider: str | None = Form(default=None, max_length=100),
    auto_confirm: bool = Form(default=False),
    cleaning_prompt_mode: str | None = Form(default=None, max_length=20),
    cleaning_prompt_content: str | None = Form(default=None, max_length=2000),
    quality_prompt_mode: str | None = Form(default=None, max_length=20),
    quality_prompt_content: str | None = Form(default=None, max_length=2000),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    通过 OpenAPI 上传 PDF 文档

    这是外部系统集成的主要入口,用于上传 PDF 并启动处理流程。
    支持自定义切片策略、清洗提示词、质检提示词等参数。

    参数:
        file: 上传的 PDF 文件
        kb_id: 目标知识库 ID(必填)
        chunk_strategy: 切片策略,默认 "hierarchical"
        subject_type: 文档主题类型,默认 "general"
        layout_type: 文档布局类型,默认 "single_column"
        parser_provider: PDF 解析器(可选)
        auto_confirm: 是否自动确认,默认 False
        cleaning_prompt_mode: 清洗提示词模式(只支持 "append")
        cleaning_prompt_content: 清洗提示词内容
        quality_prompt_mode: 质检提示词模式(只支持 "append")
        quality_prompt_content: 质检提示词内容

    认证方式:
        - 必须使用 API Key 认证
        - 强制要求签名验证(通过 X-KB-* 头)

    返回值:
        JSONResponse: 任务信息
            - requestId: 请求ID
            - data: 包含 taskId、状态等

    使用场景:
        - 外部系统自动化导入
        - 批量文档处理
        - 集成到业务流程

    错误情况:
        - 401: API Key 无效或签名验证失败
        - 403: 权限不足
        - 422: 文件格式错误或文件过大
    """
    request_id = _request_id()
    content = await file.read()
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability="ingestion.upload",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=content,
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
        force_signature=True,
    )
    if isinstance(guard, JSONResponse):
        return guard
    identity = guard.identity if isinstance(guard, ApiKeyAuthResult) else None

    prompt_policy, prompt_error = _validate_prompt_overrides(
        cleaning_prompt_mode=cleaning_prompt_mode,
        cleaning_prompt_content=cleaning_prompt_content,
        quality_prompt_mode=quality_prompt_mode,
        quality_prompt_content=quality_prompt_content,
        capabilities=guard.capabilities,
    )
    if prompt_error:
        return _error_response(403, "PROMPT_OVERRIDE_DENIED", prompt_error, request_id=request_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return _error_response(422, "VALIDATION_ERROR", "Only PDF files are supported", request_id=request_id)
    if len(content) > MAX_OPENAPI_UPLOAD_SIZE:
        return _error_response(422, "PAYLOAD_TOO_LARGE", "File size cannot exceed 500MB", request_id=request_id)

    strategy = _normalize_option(chunk_strategy, set(list_strategies()), "hierarchical")
    subject = _normalize_option(subject_type, set(SUBJECT_KEY_MAP), "general")
    layout = _normalize_option(layout_type, set(LAYOUT_KEY_MAP), "single_column")
    task_id = create_task(
        kb_id,
        file.filename,
        strategy,
        file_bytes=content,
        subject_type=subject,
        layout_type=layout,
        identity=identity,
    )
    task = get_task(task_id)
    if task is not None:
        task["openapi"] = {
            "request_id": request_id,
            "api_key_id": guard.api_key_id,
            "parser_provider_requested": parser_provider or "",
            "prompt_policy": prompt_policy,
        }
        from backend.services.task_store import save_task

        save_task(task)
    if auto_confirm:
        background_tasks.add_task(run_pipeline_and_confirm, task_id)
    else:
        background_tasks.add_task(run_pipeline_real, task_id)
    data = {
        "taskId": task_id,
        "kbId": kb_id,
        "status": "pending",
        "filename": file.filename,
        "strategy": strategy,
        "subjectType": subject,
        "layoutType": layout,
        "parserProviderRequested": parser_provider or "",
        "parserProviderEffective": "runtime PDF_PARSER_PROVIDER",
        "autoConfirm": auto_confirm,
        "promptPolicy": prompt_policy,
    }
    return JSONResponse({"requestId": request_id, "data": data}, status_code=202)



@router.post("/openapi/v1/rag/query")
async def openapi_rag_query(
    request: Request,
    identity: IdentityContext = Depends(get_current_identity),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    OpenAPI 向量检索接口

    在指定知识库中进行向量相似度检索,返回与查询文本最相关的文档片段。
    这是 RAG 系统的核心查询接口。

    请求体参数:
        query: 查询文本(必填,最长 4000 字符)
        kb_id: 知识库 ID
        top_k: 返回数量,默认 8,最大 20
        min_score: 最小相似度,默认 0.3
        use_llm_check: 是否用 LLM 过滤结果
        use_llm_score: 是否用 LLM 重新评分

    返回值:
        JSONResponse: 检索结果
            - requestId: 请求ID
            - data: 包含匹配的文档片段列表

    使用场景:
        - 外部系统集成 RAG 能力
        - 构建智能问答机器人
        - 文档搜索功能

    错误情况:
        - 401: API Key 无效
        - 403: 权限不足
        - 404: 知识库不存在
        - 503: 查询失败
    """
    request_id = _request_id()
    body = await request.body()
    try:
        payload = OpenApiQueryRequest.model_validate_json(body or b"{}")
    except ValidationError as exc:
        return validation_error_response(exc.errors())
    guard = _guard_openapi_call(
        payload.kb_id,
        identity,
        request_id,
        capability="rag.query",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=body,
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    auth_result = None
    if isinstance(guard, JSONResponse):
        return guard
    if isinstance(guard, ApiKeyAuthResult):
        auth_result = guard
        identity = auth_result.identity

    query_payload = QueryRequest(
        query=payload.query,
        kb_id=payload.kb_id or "",
        top_k=payload.top_k,
        min_score=payload.min_score,
        use_llm_check=payload.use_llm_check,
        use_llm_score=payload.use_llm_score,
    )
    try:
        result = run_rag_query(query_payload, identity, request_id=request_id, pipeline_domain="openapi")
    except ValueError as exc:
        return _error_response(404, "KB_NOT_FOUND", str(exc), request_id=request_id)
    except Exception as exc:
        return _error_response(503, "OPENAPI_QUERY_FAILED", str(exc), request_id=request_id)
    result["requestId"] = request_id
    return JSONResponse({"requestId": request_id, "data": result})



@router.post("/openapi/v1/rag/graph-query")
async def openapi_graph_rag_query(
    request: Request,
    identity: IdentityContext = Depends(get_current_identity),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    OpenAPI 图谱检索接口

    结合知识图谱进行检索,不仅返回相似文档片段,还提供实体关系和推理路径。
    相比向量检索,图谱检索能提供更结构化、更可解释的结果。

    请求体参数:
        query: 查询文本(必填,最长 4000 字符)
        kb_id: 知识库 ID
        top_k: 返回数量,默认 5,最大 20
        min_score: 最小相似度,默认 0.3
        explain: 是否返回推理路径解释
        intent: 用户意图(可选)

    返回值:
        JSONResponse: 图谱检索结果
            - requestId: 请求ID
            - data: 包含匹配片段、实体关系、推理路径

    使用场景:
        - 需要理解实体关系的查询
        - 需要可解释性的检索结果
        - 复杂知识推理场景

    错误情况:
        - 401: API Key 无效
        - 403: 权限不足
        - 404: 知识库不存在
        - 503: 查询失败
    """
    request_id = _request_id()
    body = await request.body()
    try:
        payload = OpenApiGraphQueryRequest.model_validate_json(body or b"{}")
    except ValidationError as exc:
        return validation_error_response(exc.errors())
    guard = _guard_openapi_call(
        payload.kb_id,
        identity,
        request_id,
        capability="rag.graph_query",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=_signature_payload(
            request=request,
            body=body,
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(guard, JSONResponse):
        return guard
    if isinstance(guard, ApiKeyAuthResult):
        identity = guard.identity

    graph_payload = GraphQueryRequest(
        query=payload.query,
        kb_id=payload.kb_id or "",
        top_k=payload.top_k,
        min_score=payload.min_score,
        explain=payload.explain,
        intent=payload.intent,
    )
    try:
        result = run_graph_rag_query(graph_payload, identity, request_id=request_id, pipeline_domain="openapi")
    except ValueError as exc:
        return _error_response(404, "KB_NOT_FOUND", str(exc), request_id=request_id)
    except Exception as exc:
        return _error_response(503, "OPENAPI_GRAPH_QUERY_FAILED", str(exc), request_id=request_id)
    result["requestId"] = request_id
    return JSONResponse({"requestId": request_id, "data": result})


def _guard_openapi_call(
    kb_id: str | None,
    identity: IdentityContext,
    request_id: str,
    *,
    capability: str,
    authorization: str | None = None,
    x_api_key: str | None = None,
    signature: ApiKeySignaturePayload | None = None,
    client_ip: str | None = None,
    force_signature: bool = False,
) -> JSONResponse | ApiKeyAuthResult | None:
    if not (kb_id or "").strip():
        return _error_response(400, "KB_ID_REQUIRED", "kb_id is required for OpenAPI calls", request_id=request_id)
    api_key = _extract_api_key(authorization, x_api_key)
    if api_key:
        try:
            result = authenticate_api_key(
                api_key,
                kb_id=kb_id or "",
                capability=capability,
                signature=signature,
                client_ip=client_ip,
                force_signature=force_signature,
            )
            return result
        except ApiKeyError as exc:
            _audit_openapi_denied(
                exc,
                request_id=request_id,
                kb_id=kb_id,
                capability=capability,
                signature=signature,
                client_ip=client_ip,
            )
            return _error_response(_api_key_status(exc.code), exc.code, exc.message, request_id=request_id)
    if not identity.enforce_access:
        return _error_response(
            401,
            "API_KEY_REQUIRED",
            "OpenAPI authentication is required; pass Authorization: Bearer <api_key> or X-API-Key",
            request_id=request_id,
        )
    return None


def _guard_openapi_capability(
    request_id: str,
    *,
    capability: str,
    authorization: str | None = None,
    x_api_key: str | None = None,
    signature: ApiKeySignaturePayload | None = None,
    client_ip: str | None = None,
) -> JSONResponse | ApiKeyAuthResult:
    api_key = _extract_api_key(authorization, x_api_key)
    if not api_key:
        return _error_response(
            401,
            "API_KEY_REQUIRED",
            "OpenAPI authentication is required; pass Authorization: Bearer <api_key> or X-API-Key",
            request_id=request_id,
        )
    try:
        return authenticate_api_key(
            api_key,
            kb_id="*",
            capability=capability,
            signature=signature,
            client_ip=client_ip,
        )
    except ApiKeyError as exc:
        _audit_openapi_denied(
            exc,
            request_id=request_id,
            kb_id="*",
            capability=capability,
            signature=signature,
            client_ip=client_ip,
        )
        return _error_response(_api_key_status(exc.code), exc.code, exc.message, request_id=request_id)


def _extract_api_key(authorization: str | None, x_api_key: str | None) -> str:
    header_key = (x_api_key or "").strip()
    if header_key:
        return header_key
    value = (authorization or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _signature_payload(
    *,
    request: Request,
    body: bytes,
    timestamp: str | None,
    nonce: str | None,
    body_sha256: str | None,
    signature: str | None,
) -> ApiKeySignaturePayload:
    return ApiKeySignaturePayload(
        method=request.method,
        path=request.url.path,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
        signature=signature,
    )


def _client_ip(request: Request, x_forwarded_for: str | None) -> str:
    forwarded = (x_forwarded_for or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _api_key_status(code: str) -> int:
    if code in {"API_KEY_REQUIRED", "INVALID_API_KEY", "API_KEY_DISABLED", "API_KEY_EXPIRED"}:
        return 401
    if code in {"KB_BINDING_DENIED", "CAPABILITY_DENIED"}:
        return 403
    if code in {"SIGNATURE_REQUIRED", "INVALID_SIGNATURE", "BODY_HASH_MISMATCH", "TIMESTAMP_EXPIRED", "INVALID_TIMESTAMP", "INVALID_NONCE", "NONCE_REPLAYED"}:
        return 401
    if code == "IP_NOT_ALLOWED":
        return 403
    if code == "RATE_LIMITED":
        return 429
    if code == "QUOTA_EXCEEDED":
        return 429
    if code == "KB_ID_REQUIRED":
        return 400
    if code in {"TASK_NOT_FOUND"}:
        return 404
    if code in {"PAYLOAD_TOO_LARGE"}:
        return 413
    return 401


def _audit_openapi_denied(
    exc: ApiKeyError,
    *,
    request_id: str,
    kb_id: str | None,
    capability: str,
    signature: ApiKeySignaturePayload | None,
    client_ip: str | None,
) -> None:
    append_audit_log(
        AuditLogRecord(
            action="openapi.auth_denied",
            resource_type="openapi",
            resource_id=capability,
            request_id=request_id,
            kb_id=kb_id,
            api_key_id=exc.api_key_id,
            outcome="denied",
            risk_level=_openapi_denied_risk(exc.code),
            summary=f"OpenAPI call denied: {exc.code}",
            metadata={
                "errorCode": exc.code,
                "capability": capability,
                "path": signature.path if signature else "",
                "method": signature.method if signature else "",
                "clientIpMasked": _mask_client_ip(client_ip),
                "hasSignatureHeaders": bool(
                    signature
                    and signature.timestamp
                    and signature.nonce
                    and signature.body_sha256
                    and signature.signature
                ),
            },
        )
    )


def _openapi_denied_risk(code: str) -> str:
    if code in {
        "INVALID_SIGNATURE",
        "BODY_HASH_MISMATCH",
        "TIMESTAMP_EXPIRED",
        "INVALID_TIMESTAMP",
        "INVALID_NONCE",
        "NONCE_REPLAYED",
        "IP_NOT_ALLOWED",
    }:
        return "high"
    if code in {"RATE_LIMITED", "QUOTA_EXCEEDED", "CAPABILITY_DENIED", "KB_BINDING_DENIED"}:
        return "medium"
    return "low"


def _mask_client_ip(value: str | None) -> str:
    ip = (value or "").split(",", 1)[0].strip()
    if not ip:
        return ""
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:3] + ["***"])
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3] + ["*"])
    return "***"


def _strategy_label(value: str) -> str:
    labels = {
        "paragraph": "段落切片",
        "fixed_length": "固定长度",
        "semantic": "语义切片",
        "separator": "分隔符切片",
        "llm": "LLM 切片",
        "hierarchical": "三层切片",
    }
    return labels.get(value, value)


def _parser_provider_availability(provider: str) -> tuple[bool, str]:
    import os

    required_env = {
        "mineru": "302AI_API_KEY",
        "mineru_official": "MINERU_OFFICIAL_API_TOKEN",
        "ali_document_mind": "ALIYUN_DOCUMENT_MIND_AK",
    }
    key = required_env.get(provider)
    if key and not os.getenv(key):
        return False, f"缺少 {key}"
    return True, ""


def _normalize_option(value: str | None, allowed: set[str], fallback: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def _validate_prompt_overrides(
    *,
    cleaning_prompt_mode: str | None,
    cleaning_prompt_content: str | None,
    quality_prompt_mode: str | None,
    quality_prompt_content: str | None,
    capabilities: tuple[str, ...],
) -> tuple[dict, str | None]:
    policy = {
        "cleaning": "system_default",
        "quality": "system_default",
    }
    if cleaning_prompt_content:
        if cleaning_prompt_mode != "append":
            return policy, "Cleaning prompt override only supports append mode"
        if "ingestion.clean_prompt.append" not in capabilities:
            return policy, "This API Key cannot override cleaning prompt"
        policy["cleaning"] = "system_default_plus_append"
    if quality_prompt_content:
        if quality_prompt_mode != "append":
            return policy, "Quality prompt override only supports append mode"
        if "ingestion.quality_prompt.append" not in capabilities:
            return policy, "This API Key cannot override quality prompt"
        policy["quality"] = "system_default_plus_append"
    return policy, None


def _request_id() -> str:
    return str(uuid.uuid4())


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    details: dict | None = None,
) -> JSONResponse:
    payload = {
        "requestId": request_id or _request_id(),
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
    return JSONResponse(payload, status_code=status_code)


def validation_error_response(exc_errors: list[dict]) -> JSONResponse:
    return _error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request payload validation failed",
        details={"errors": exc_errors},
    )
