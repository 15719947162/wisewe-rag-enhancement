"""
OpenAPI v1 接口路由模块

这个模块提供了标准化的 OpenAPI 接口,用于外部系统集成。
主要功能包括:
- 知识库管理(查询、创建、更新、删除知识库)
- 文档导入(上传 PDF 并处理)
- 文档查询
- RAG 查询(向量检索和图谱检索)
- 网页抓取导入
- 备份 CSV 导入
- 使用量查询

所有 OpenAPI 接口都需要 API Key 认证,支持签名验证机制和并发控制。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.ingestion_service import (
    SKIPPED_FAST_IMPORT_STAGES,
    SOURCE_TYPE_BACKUP_CSV,
    SOURCE_TYPE_FILE,
    SOURCE_TYPE_WEBPAGE,
    create_task,
    get_task,
    is_allowed_file_document,
    is_backup_csv_filename,
    run_pipeline_and_confirm,
    run_pipeline_real,
    _task_to_payload,
)
from backend.services.kb_service import (
    create_knowledge_base_payload,
    delete_knowledge_base_payload,
    get_documents_payload,
    get_knowledge_bases_payload,
    update_knowledge_base_payload,
)
from backend.services.rag_service import run_graph_rag_query, run_rag_query
from core.chunker import list_strategies
from core.db.api_keys import (
    ApiKeyAuthResult,
    ApiKeyError,
    ApiKeySignaturePayload,
    acquire_api_key_concurrency_slot,
    authenticate_api_key,
    release_api_key_concurrency_slot,
)
from core.db.identity import IdentityContext
from core.db.query_logs import (
    AuditLogRecord,
    append_audit_log,
    fetch_processing_cost_task_detail_for_identity,
    refresh_processing_cost_estimates,
)
from core.parser.provider import PDF_PARSER_CHANNELS
from core.prompts import LAYOUT_KEY_MAP, SUBJECT_KEY_MAP


router = APIRouter()
MAX_OPENAPI_UPLOAD_SIZE = 500 * 1024 * 1024
SUBJECT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in SUBJECT_KEY_MAP.items()
]
LAYOUT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in LAYOUT_KEY_MAP.items()
]
SOURCE_TYPE_OPTIONS = [
    {
        "value": SOURCE_TYPE_FILE,
        "label": "file",
        "description": "Upload PDF, image, or Office files through /openapi/v1/ingestion/upload.",
        "endpoint": "/openapi/v1/ingestion/upload",
        "capability": "ingestion.upload.file",
        "legacyCapability": "ingestion.upload",
    },
    {
        "value": SOURCE_TYPE_WEBPAGE,
        "label": "webpage",
        "description": "Submit a webpage crawl task through /openapi/v1/ingestion/webpage.",
        "endpoint": "/openapi/v1/ingestion/webpage",
        "capability": "ingestion.webpage",
    },
    {
        "value": SOURCE_TYPE_BACKUP_CSV,
        "label": "backup_csv",
        "description": "Restore a wisewe-rag-backup-v1 CSV through /openapi/v1/ingestion/backup-csv.",
        "endpoint": "/openapi/v1/ingestion/backup-csv",
        "capability": "ingestion.backup_csv",
    },
]
FILE_DOCUMENT_TYPES = [
    {"value": "pdf", "extensions": [".pdf"], "description": "PDF document parser pipeline."},
    {"value": "image", "extensions": [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"], "description": "Image document adapter pipeline."},
    {"value": "office", "extensions": [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"], "description": "Office document adapter pipeline."},
]
WEBPAGE_LIMITS = {
    "maxDepth": {"default": 1, "min": 0, "max": 2},
    "maxPages": {"default": 10, "min": 1, "max": 50},
    "maxPageBytes": {"default": 2 * 1024 * 1024, "min": 64 * 1024, "max": 5 * 1024 * 1024},
    "timeoutSeconds": {"default": 12, "min": 3, "max": 30},
}


class OpenApiQueryRequest(BaseModel):
    """OpenAPI 向量检索请求参数"""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)
    kb_id: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=8, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    use_llm_check: bool = False
    use_llm_score: bool = False


class OpenApiGraphQueryRequest(BaseModel):
    """OpenAPI 图谱检索请求参数"""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)
    kb_id: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    explain: bool = False
    intent: str | None = Field(default=None, max_length=100)


class PromptAppendRequest(BaseModel):
    """提示词追加请求"""
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="append", pattern="^append$")
    content: str = Field(..., min_length=1, max_length=2000)


class OpenApiKnowledgeBaseCreateRequest(BaseModel):
    """OpenAPI 知识库创建请求参数"""
    model_config = ConfigDict(extra="forbid")

    kb_id: str | None = Field(default=None, max_length=255)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical", max_length=100)


class OpenApiKnowledgeBaseUpdateRequest(BaseModel):
    """OpenAPI 知识库更新请求参数"""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical", max_length=100)


class OpenApiWebpageIngestionRequest(BaseModel):
    """OpenAPI 网页抓取请求参数"""
    model_config = ConfigDict(extra="forbid")

    kb_id: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2048)
    chunk_strategy: str = Field(default="hierarchical", max_length=100)
    subject_type: str = Field(default="general", max_length=100)
    layout_type: str = Field(default="single_column", max_length=100)
    max_depth: int = Field(default=1, ge=0, le=2)
    max_pages: int = Field(default=10, ge=1, le=50)
    same_domain_only: bool = True
    include_patterns: list[str] = Field(default_factory=list, max_length=20)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=20)
    max_page_bytes: int = Field(default=2 * 1024 * 1024, ge=64 * 1024, le=5 * 1024 * 1024)
    timeout_seconds: int = Field(default=12, ge=3, le=30)


OPENAPI_KB_CREATE_EXAMPLE = {
    "kb_id": "kb_demo",
    "name": "示例知识库",
    "description": "用于 OpenAPI 联调的知识库",
    "strategy": "hierarchical",
}
OPENAPI_KB_UPDATE_EXAMPLE = {
    "name": "示例知识库（更新）",
    "description": "通过 OpenAPI 更新后的知识库说明",
    "strategy": "hierarchical",
}
OPENAPI_WEBPAGE_INGESTION_EXAMPLE = {
    "kb_id": "kb_demo",
    "url": "https://example.com/docs",
    "chunk_strategy": "hierarchical",
    "subject_type": "general",
    "layout_type": "single_column",
    "max_depth": 1,
    "max_pages": 10,
    "same_domain_only": True,
    "include_patterns": [],
    "exclude_patterns": [],
    "max_page_bytes": 2097152,
    "timeout_seconds": 12,
}
OPENAPI_RAG_QUERY_EXAMPLE = {
    "query": "请概括这份教材的核心知识点",
    "kb_id": "kb_demo",
    "top_k": 8,
    "min_score": 0.3,
    "use_llm_check": False,
    "use_llm_score": False,
}
OPENAPI_GRAPH_QUERY_EXAMPLE = {
    "query": "这份教材里相关概念之间有什么关系？",
    "kb_id": "kb_demo",
    "top_k": 5,
    "min_score": 0.3,
    "explain": True,
    "intent": "concept",
}


def _absolute_openapi_image_urls(value: object, request: Request) -> object:
    """将相对图片 URL 转换为绝对 URL"""
    if isinstance(value, list):
        return [_absolute_openapi_image_urls(item, request) for item in value]
    if not isinstance(value, dict):
        return value

    base_url = str(request.base_url).rstrip("/")
    output: dict = {}
    for key, item in value.items():
        if key == "imageUrl" and isinstance(item, str):
            lowered = item.lower()
            if item and not lowered.startswith(("http://", "https://", "data:")):
                path = item if item.startswith("/") else f"/{item}"
                output[key] = f"{base_url}{path}"
            else:
                output[key] = item
            continue
        output[key] = _absolute_openapi_image_urls(item, request)
    return output


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
    """查询知识库列表"""
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
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id="*",
        capability="kb.list",
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
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
        items = get_knowledge_bases_payload(guard.identity)
        allowed = set(guard.kb_ids)
        filtered = items if not allowed else [item for item in items if item.get("id") in allowed]
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
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.post("/openapi/v1/knowledge-bases", status_code=201)
async def openapi_create_knowledge_base(
    request: Request,
    payload: OpenApiKnowledgeBaseCreateRequest = Body(..., examples=[OPENAPI_KB_CREATE_EXAMPLE]),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    创建知识库（OpenAPI）

    通过 OpenAPI 接口创建新的知识库。创建成功后可以使用该知识库进行文档导入和 RAG 查询。
    此接口需要 API Key 认证并强制要求签名验证。

    参数:
        request: FastAPI 请求对象
        payload: 知识库创建参数
            - kb_id: 知识库 ID（可选，不提供则自动生成）
            - name: 知识库名称（必填，最大 100 字符）
            - description: 知识库描述（可选，最大 500 字符）
            - strategy: 切片策略，默认 "hierarchical"
        authorization: Bearer Token 认证头
        x_api_key: API Key 认证头（二选一）
        x_kb_timestamp: 签名时间戳
        x_kb_nonce: 签名随机数
        x_kb_body_sha256: 请求体 SHA256 哈希
        x_kb_signature: 签名值
        x_forwarded_for: 客户端真实 IP

    返回值:
        JSONResponse: 创建结果
            - requestId: 请求 ID
            - data: 创建的知识库信息
                - id: 知识库 ID
                - name: 知识库名称
                - description: 描述
                - strategy: 切片策略
                - createdAt: 创建时间

    使用场景:
        - 自动化创建知识库
        - 批量创建知识库
        - 与其他系统集成

    权限要求:
        - 需要 kb.create 权限
        - 强制签名验证

    请求示例:
        ```bash
        POST /openapi/v1/knowledge-bases
        Authorization: Bearer <api_key>
        X-KB-Timestamp: 1720252800
        X-KB-Nonce: abc123xyz
        X-KB-Body-SHA256: e3b0c44298fc1c149afbf4c8996fb924...
        X-KB-Signature: 3045022100...
        Content-Type: application/json

        {
          "kb_id": "kb_demo",
          "name": "示例知识库",
          "description": "用于 OpenAPI 联调的知识库",
          "strategy": "hierarchical"
        }
        ```

    响应示例:
        ```json
        {
          "requestId": "req_abc123",
          "data": {
            "id": "kb_demo",
            "name": "示例知识库",
            "description": "用于 OpenAPI 联调的知识库",
            "strategy": "hierarchical",
            "createdAt": "2026-07-06T10:00:00Z"
          }
        }
        ```

    错误情况:
        - 401: API Key 无效或签名验证失败
        - 403: 权限不足
        - 422: 请求参数验证失败
        - 429: 超过并发限制
        - 503: 服务不可用
    """
    request_id = _request_id()
    body = await request.body()

    signature = _signature_payload(
        request=request,
        body=body,
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    guard = _guard_openapi_capability(
        request_id,
        capability="kb.create",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=_client_ip(request, x_forwarded_for),
        force_signature=True,
    )
    if isinstance(guard, JSONResponse):
        return guard

    kb_id = (payload.kb_id or "").strip() or uuid.uuid4().hex[:24]
    name = payload.name.strip()
    if not name:
        return _error_response(
            422,
            "VALIDATION_ERROR",
            "Request payload validation failed",
            request_id=request_id,
            details={"errors": [{"loc": ["name"], "msg": "name cannot be blank"}]},
        )
    strategy = _normalize_option(payload.strategy, set(list_strategies()), "hierarchical")
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="kb.create",
        signature=signature,
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
        data = create_knowledge_base_payload(
            kb_id,
            name,
            payload.description.strip(),
            strategy,
            guard.identity,
        )
        return JSONResponse({"requestId": request_id, "data": data}, status_code=201)
    except Exception as exc:
        return _error_response(503, "OPENAPI_KB_CREATE_FAILED", str(exc), request_id=request_id)
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.put("/openapi/v1/knowledge-bases/{kb_id}")
async def openapi_update_knowledge_base(
    kb_id: str,
    request: Request,
    payload: OpenApiKnowledgeBaseUpdateRequest = Body(..., examples=[OPENAPI_KB_UPDATE_EXAMPLE]),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    更新知识库（OpenAPI）

    通过 OpenAPI 接口更新已存在的知识库信息。可以更新知识库的名称、描述和切片策略。
    此接口需要 API Key 认证并强制要求签名验证。

    参数:
        kb_id: 知识库 ID（路径参数）
        request: FastAPI 请求对象
        payload: 知识库更新参数
            - name: 知识库名称（必填，最大 100 字符）
            - description: 知识库描述（可选，最大 500 字符）
            - strategy: 切片策略，默认 "hierarchical"
        authorization: Bearer Token 认证头
        x_api_key: API Key 认证头（二选一）
        x_kb_timestamp: 签名时间戳
        x_kb_nonce: 签名随机数
        x_kb_body_sha256: 请求体 SHA256 哈希
        x_kb_signature: 签名值
        x_forwarded_for: 客户端真实 IP

    返回值:
        JSONResponse: 更新结果
            - requestId: 请求 ID
            - data: 更新后的知识库信息

    权限要求:
        - 需要 kb.update 权限
        - 强制签名验证

    错误情况:
        - 401: API Key 无效或签名验证失败
        - 403: 权限不足
        - 404: 知识库不存在
        - 422: 请求参数验证失败
        - 429: 超过并发限制
        - 503: 服务不可用
    """
    request_id = _request_id()
    body = await request.body()
    signature = _signature_payload(
        request=request,
        body=body,
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    client_ip = _client_ip(request, x_forwarded_for)
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability="kb.update",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=client_ip,
        force_signature=True,
    )
    if isinstance(guard, JSONResponse):
        return guard

    strategy = _normalize_option(payload.strategy, set(list_strategies()), "hierarchical")
    name = payload.name.strip()
    if not name:
        return _error_response(
            422,
            "VALIDATION_ERROR",
            "Request payload validation failed",
            request_id=request_id,
            details={"errors": [{"loc": ["name"], "msg": "name cannot be blank"}]},
        )
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="kb.update",
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
        try:
            data = update_knowledge_base_payload(
                kb_id,
                name,
                payload.description.strip(),
                strategy,
                guard.identity,
            )
        except ValueError as exc:
            return _error_response(404, "KB_NOT_FOUND", str(exc), request_id=request_id)
        return JSONResponse({"requestId": request_id, "data": data})
    except Exception as exc:
        return _error_response(503, "OPENAPI_KB_UPDATE_FAILED", str(exc), request_id=request_id)
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.delete("/openapi/v1/knowledge-bases/{kb_id}")
async def openapi_delete_knowledge_base(
    kb_id: str,
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
    删除知识库（OpenAPI）

    通过 OpenAPI 接口删除指定的知识库。删除后知识库及其所有文档将不可恢复。
    此接口需要 API Key 认证并强制要求签名验证。

    参数:
        kb_id: 知识库 ID（路径参数）
        request: FastAPI 请求对象
        authorization: Bearer Token 认证头
        x_api_key: API Key 认证头（二选一）
        x_kb_timestamp: 签名时间戳
        x_kb_nonce: 签名随机数
        x_kb_body_sha256: 请求体 SHA256 哈希
        x_kb_signature: 签名值
        x_forwarded_for: 客户端真实 IP

    返回值:
        JSONResponse: 删除结果
            - requestId: 请求 ID
            - data: 删除状态
                - deleted: 是否删除成功
                - kbId: 知识库 ID

    权限要求:
        - 需要 kb.delete 权限
        - 强制签名验证

    错误情况:
        - 401: API Key 无效或签名验证失败
        - 403: 权限不足
        - 404: 知识库不存在
        - 429: 超过并发限制
        - 503: 服务不可用
    """
    request_id = _request_id()
    signature = _signature_payload(
        request=request,
        body=b"",
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    client_ip = _client_ip(request, x_forwarded_for)
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability="kb.delete",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=client_ip,
        force_signature=True,
    )
    if isinstance(guard, JSONResponse):
        return guard

    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="kb.delete",
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
        data = delete_knowledge_base_payload(kb_id, guard.identity)
        if not data.get("deleted"):
            return _error_response(404, "KB_NOT_FOUND", f"Knowledge base '{kb_id}' not found", request_id=request_id)
        return JSONResponse({"requestId": request_id, "data": {"deleted": True, "kbId": kb_id}})
    except Exception as exc:
        return _error_response(503, "OPENAPI_KB_DELETE_FAILED", str(exc), request_id=request_id)
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.get("/openapi/v1/documents")
async def openapi_documents(
    request: Request,
    kb_id: str = Query(..., min_length=1, max_length=255),
    document_id: str | None = Query(default=None, max_length=255),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    查询文档列表（OpenAPI）

    查询指定知识库下的文档列表，或查询单个文档详情。
    此接口需要 API Key 认证。

    参数:
        request: FastAPI 请求对象
        kb_id: 知识库 ID（必填）
        document_id: 文档 ID（可选，指定则返回单个文档详情）
        authorization: Bearer Token 认证头
        x_api_key: API Key 认证头（二选一）
        x_kb_timestamp: 签名时间戳
        x_kb_nonce: 签名随机数
        x_kb_body_sha256: 请求体 SHA256 哈希
        x_kb_signature: 签名值
        x_forwarded_for: 客户端真实 IP

    返回值:
        JSONResponse: 文档列表或文档详情
            - requestId: 请求 ID
            - data: 文档列表或文档对象

    权限要求:
        - 需要 document.read 权限

    错误情况:
        - 401: API Key 无效
        - 403: 权限不足或知识库绑定校验失败
        - 404: 文档不存在
        - 429: 超过并发限制
        - 503: 服务不可用
    """
    request_id = _request_id()
    signature = _signature_payload(
        request=request,
        body=b"",
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    client_ip = _client_ip(request, x_forwarded_for)
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability="document.read",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(guard, JSONResponse):
        return guard

    target_document_id = (document_id or "").strip()
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="document.read",
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
        documents = get_documents_payload(kb_id, guard.identity)
        if target_document_id:
            matches = [item for item in documents if item.get("id") == target_document_id]
            if not matches:
                return _error_response(404, "DOCUMENT_NOT_FOUND", "Document not found or not accessible", request_id=request_id)
            return JSONResponse({"requestId": request_id, "data": matches[0]})

        return JSONResponse({"requestId": request_id, "data": documents})
    except Exception as exc:
        return _error_response(503, "OPENAPI_DOCUMENTS_FAILED", str(exc), request_id=request_id)
    finally:
        _release_openapi_concurrency(guard, concurrency)


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
    """获取文档导入选项配置"""
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
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id="*",
        capability="ingestion.options",
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
    if isinstance(concurrency, JSONResponse):
        return concurrency

    try:
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
            "sourceTypes": SOURCE_TYPE_OPTIONS,
            "chunkStrategies": [{"value": value, "label": _strategy_label(value)} for value in list_strategies()],
            "subjectTypes": SUBJECT_OPTIONS,
            "layoutTypes": LAYOUT_OPTIONS,
            "parserProviders": parser_providers,
            "fileDocumentTypes": FILE_DOCUMENT_TYPES,
            "webpageLimits": WEBPAGE_LIMITS,
            "backupCsv": {
                "schemaVersion": "wisewe-rag-backup-v1",
                "endpoint": "/openapi/v1/ingestion/backup-csv",
                "capability": "ingestion.backup_csv",
                "fastImport": True,
                "skippedStages": SKIPPED_FAST_IMPORT_STAGES,
            },
            "signatureBodyHash": {
                "json": "Hash the exact JSON bytes sent in the request body.",
                "file": "For multipart file upload endpoints, hash the uploaded file bytes.",
                "empty": "GET requests use the SHA-256 of an empty byte string.",
            },
            "notes": [
                "parser_provider 当前作为 OpenAPI 请求元数据接收；真实执行仍以运行时 PDF_PARSER_PROVIDER 管道为准，单次覆盖需后续管道改造。",
                "清洗 / 质检提示词第一期只允许 append 型补充，不允许 raw system prompt replace。",
            ],
        }
        return JSONResponse({"requestId": request_id, "data": data})
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.get("/openapi/v1/ingestion/tasks/{task_id}")
async def openapi_ingestion_task(
    task_id: str,
    request: Request,
    kb_id: str | None = Query(default=None, max_length=255),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """查询导入任务详情"""
    request_id = _request_id()
    task = get_task(task_id)
    if not task:
        return _error_response(404, "TASK_NOT_FOUND", "Task not found or not accessible", request_id=request_id)
    task_kb_id = str(task.get("kb_id") or "").strip()
    requested_kb_id = str(kb_id or "").strip()
    signature = _signature_payload(
        request=request,
        body=b"",
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    client_ip = _client_ip(request, x_forwarded_for)
    guard = _guard_openapi_capability(
        request_id,
        capability="ingestion.read",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(guard, JSONResponse):
        return guard
    if guard.kb_ids:
        if task_kb_id not in guard.kb_ids:
            return _openapi_kb_binding_denied(
                guard,
                request_id=request_id,
                kb_id=task_kb_id,
                capability="ingestion.read",
                signature=signature,
                client_ip=client_ip,
            )
    elif not requested_kb_id or requested_kb_id != task_kb_id:
        return _openapi_kb_binding_denied(
            guard,
            request_id=request_id,
            kb_id=task_kb_id,
            capability="ingestion.read",
            signature=signature,
            client_ip=client_ip,
        )
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=task_kb_id,
        capability="ingestion.read",
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency
    try:
        return JSONResponse({"requestId": request_id, "data": _task_to_payload(task)})
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.get("/openapi/v1/usage/tasks/{task_id}")
async def openapi_usage_task(
    task_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """
    查询任务使用量明细（OpenAPI）

    查询指定任务的处理成本明细，包括 Token 消耗、处理时间等信息。
    此接口需要 API Key 认证，且只能查询绑定知识库的任务或租户范围内的任务。

    参数:
        task_id: 任务 ID（路径参数）
        request: FastAPI 请求对象
        limit: 返回记录数量限制（默认 100，最大 200）
        authorization: Bearer Token 认证头
        x_api_key: API Key 认证头（二选一）
        x_kb_timestamp: 签名时间戳
        x_kb_nonce: 签名随机数
        x_kb_body_sha256: 请求体 SHA256 哈希
        x_kb_signature: 签名值
        x_forwarded_for: 客户端真实 IP

    返回值:
        JSONResponse: 使用量明细
            - requestId: 请求 ID
            - data: 任务成本明细数据
                - taskId: 任务 ID
                - records: 处理成本记录列表
                - totalTokens: 总 Token 消耗
                - totalCost: 总成本

    权限要求:
        - 需要 usage.read 权限
        - API Key 必须绑定知识库或属于某个租户

    错误情况:
        - 401: API Key 无效
        - 403: 权限不足或未绑定知识库
        - 429: 超过并发限制
        - 503: 服务不可用
    """
    request_id = _request_id()
    signature = _signature_payload(
        request=request,
        body=b"",
        timestamp=x_kb_timestamp,
        nonce=x_kb_nonce,
        body_sha256=x_kb_body_sha256,
        signature=x_kb_signature,
    )
    client_ip = _client_ip(request, x_forwarded_for)
    guard = _guard_openapi_capability(
        request_id,
        capability="usage.read",
        authorization=authorization,
        x_api_key=x_api_key,
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(guard, JSONResponse):
        return guard
    if not guard.kb_ids and not guard.identity.tenant_id:
        return _openapi_kb_binding_denied(
            guard,
            request_id=request_id,
            kb_id=None,
            capability="usage.read",
            signature=signature,
            client_ip=client_ip,
        )
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id="*",
        capability="usage.read",
        signature=signature,
        client_ip=client_ip,
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency
    try:
        refresh_processing_cost_estimates()
        data = fetch_processing_cost_task_detail_for_identity(
            task_id,
            tenant_id=guard.identity.tenant_id,
            include_all_tenants=False,
            visible_kb_ids=list(guard.kb_ids) if guard.kb_ids else None,
            limit=limit,
        )
        return JSONResponse({"requestId": request_id, "data": data})
    except Exception as exc:
        return _error_response(503, "OPENAPI_USAGE_TASK_FAILED", str(exc), request_id=request_id)
    finally:
        _release_openapi_concurrency(guard, concurrency)


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
    """通过 OpenAPI 上传文档"""
    request_id = _request_id()
    content = await file.read()
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability=("ingestion.upload.file", "ingestion.upload"),
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
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="ingestion.upload",
        signature=_signature_payload(
            request=request,
            body=content,
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency
    identity = guard.identity if isinstance(guard, ApiKeyAuthResult) else None

    try:
        prompt_policy, prompt_error = _validate_prompt_overrides(
            cleaning_prompt_mode=cleaning_prompt_mode,
            cleaning_prompt_content=cleaning_prompt_content,
            quality_prompt_mode=quality_prompt_mode,
            quality_prompt_content=quality_prompt_content,
            capabilities=guard.capabilities,
        )
        if prompt_error:
            return _error_response(403, "PROMPT_OVERRIDE_DENIED", prompt_error, request_id=request_id)
        if not file.filename or not is_allowed_file_document(file.filename):
            return _error_response(422, "VALIDATION_ERROR", "Only PDF, image and Office files are supported", request_id=request_id)
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
            source_type=SOURCE_TYPE_FILE,
            source_summary=file.filename,
            api_key_id=guard.api_key_id,
            app_id=guard.app_id,
        )
        task = get_task(task_id)
        if task is not None:
            task["openapi"] = {
                "request_id": request_id,
                "api_key_id": guard.api_key_id,
                "app_id": guard.app_id,
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
            "sourceType": SOURCE_TYPE_FILE,
            "promptPolicy": prompt_policy,
        }
        return JSONResponse({"requestId": request_id, "data": data}, status_code=202)
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.post("/openapi/v1/ingestion/webpage", status_code=202)
async def openapi_ingestion_webpage(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: OpenApiWebpageIngestionRequest = Body(..., examples=[OPENAPI_WEBPAGE_INGESTION_EXAMPLE]),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """网页抓取导入"""
    request_id = _request_id()
    body = await request.body()
    guard = _guard_openapi_call(
        payload.kb_id,
        IdentityContext(),
        request_id,
        capability="ingestion.webpage",
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
        force_signature=True,
    )
    if isinstance(guard, JSONResponse):
        return guard
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=payload.kb_id,
        capability="ingestion.webpage",
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
    if isinstance(concurrency, JSONResponse):
        return concurrency
    identity = guard.identity if isinstance(guard, ApiKeyAuthResult) else None
    try:
        strategy = _normalize_option(payload.chunk_strategy, set(list_strategies()), "hierarchical")
        subject = _normalize_option(payload.subject_type, set(SUBJECT_KEY_MAP), "general")
        layout = _normalize_option(payload.layout_type, set(LAYOUT_KEY_MAP), "single_column")
        task_id = create_task(
            payload.kb_id,
            payload.url,
            strategy,
            subject_type=subject,
            layout_type=layout,
            identity=identity,
            source_type=SOURCE_TYPE_WEBPAGE,
            source_summary=payload.url,
            source_url=payload.url,
            source_options=payload.model_dump(),
            api_key_id=guard.api_key_id,
            app_id=guard.app_id,
        )
        background_tasks.add_task(run_pipeline_real, task_id)
        return JSONResponse(
            {
                "requestId": request_id,
                "data": {
                    "taskId": task_id,
                    "kbId": payload.kb_id,
                    "status": "pending",
                    "url": payload.url,
                    "strategy": strategy,
                    "subjectType": subject,
                    "layoutType": layout,
                    "sourceType": SOURCE_TYPE_WEBPAGE,
                },
            },
            status_code=202,
        )
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.post("/openapi/v1/ingestion/backup-csv", status_code=202)
async def openapi_ingestion_backup_csv(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    kb_id: str = Form(..., max_length=255),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """备份 CSV 导入"""
    request_id = _request_id()
    content = await file.read()
    guard = _guard_openapi_call(
        kb_id,
        IdentityContext(),
        request_id,
        capability="ingestion.backup_csv",
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
    concurrency = _acquire_openapi_concurrency_or_error(
        guard,
        request_id=request_id,
        kb_id=kb_id,
        capability="ingestion.backup_csv",
        signature=_signature_payload(
            request=request,
            body=content,
            timestamp=x_kb_timestamp,
            nonce=x_kb_nonce,
            body_sha256=x_kb_body_sha256,
            signature=x_kb_signature,
        ),
        client_ip=_client_ip(request, x_forwarded_for),
    )
    if isinstance(concurrency, JSONResponse):
        return concurrency
    identity = guard.identity if isinstance(guard, ApiKeyAuthResult) else None
    try:
        if not file.filename or not is_backup_csv_filename(file.filename):
            return _error_response(422, "VALIDATION_ERROR", "Only system backup CSV files are supported", request_id=request_id)
        if len(content) > MAX_OPENAPI_UPLOAD_SIZE:
            return _error_response(422, "PAYLOAD_TOO_LARGE", "File size cannot exceed 500MB", request_id=request_id)
        task_id = create_task(
            kb_id,
            file.filename,
            "backup_csv",
            file_bytes=content,
            identity=identity,
            source_type=SOURCE_TYPE_BACKUP_CSV,
            source_summary=file.filename,
            fast_import=True,
            skipped_stages=SKIPPED_FAST_IMPORT_STAGES,
            api_key_id=guard.api_key_id,
            app_id=guard.app_id,
        )
        background_tasks.add_task(run_pipeline_real, task_id)
        return JSONResponse(
            {
                "requestId": request_id,
                "data": {
                    "taskId": task_id,
                    "kbId": kb_id,
                    "status": "pending",
                    "filename": file.filename,
                    "sourceType": SOURCE_TYPE_BACKUP_CSV,
                    "skippedStages": SKIPPED_FAST_IMPORT_STAGES,
                },
            },
            status_code=202,
        )
    finally:
        _release_openapi_concurrency(guard, concurrency)


@router.post("/openapi/v1/rag/query")
async def openapi_rag_query(
    request: Request,
    payload: OpenApiQueryRequest = Body(..., examples=[OPENAPI_RAG_QUERY_EXAMPLE]),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """OpenAPI 向量检索接口"""
    request_id = _request_id()
    body = await request.body()
    guard = _guard_openapi_call(
        payload.kb_id,
        IdentityContext(),
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
    concurrency = _acquire_openapi_concurrency_or_error(
        auth_result,
        request_id=request_id,
        kb_id=payload.kb_id,
        capability="rag.query",
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
    if isinstance(concurrency, JSONResponse):
        return concurrency

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
    finally:
        _release_openapi_concurrency(auth_result, concurrency)
    result = _absolute_openapi_image_urls(result, request)
    result["requestId"] = request_id
    return JSONResponse({"requestId": request_id, "data": result})


@router.post("/openapi/v1/rag/graph-query")
async def openapi_graph_rag_query(
    request: Request,
    payload: OpenApiGraphQueryRequest = Body(..., examples=[OPENAPI_GRAPH_QUERY_EXAMPLE]),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_kb_timestamp: str | None = Header(default=None, alias="X-KB-Timestamp"),
    x_kb_nonce: str | None = Header(default=None, alias="X-KB-Nonce"),
    x_kb_body_sha256: str | None = Header(default=None, alias="X-KB-Body-SHA256"),
    x_kb_signature: str | None = Header(default=None, alias="X-KB-Signature"),
    x_forwarded_for: str | None = Header(default=None, alias="X-Forwarded-For"),
) -> JSONResponse:
    """OpenAPI 图谱检索接口"""
    request_id = _request_id()
    body = await request.body()
    guard = _guard_openapi_call(
        payload.kb_id,
        IdentityContext(),
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
    auth_result = guard if isinstance(guard, ApiKeyAuthResult) else None
    if isinstance(guard, ApiKeyAuthResult):
        identity = guard.identity
    concurrency = _acquire_openapi_concurrency_or_error(
        auth_result,
        request_id=request_id,
        kb_id=payload.kb_id,
        capability="rag.graph_query",
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
    if isinstance(concurrency, JSONResponse):
        return concurrency

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
    finally:
        _release_openapi_concurrency(auth_result, concurrency)
    result = _absolute_openapi_image_urls(result, request)
    result["requestId"] = request_id
    return JSONResponse({"requestId": request_id, "data": result})


def _guard_openapi_call(
    kb_id: str | None,
    identity: IdentityContext,
    request_id: str,
    *,
    capability: str | tuple[str, ...],
    authorization: str | None = None,
    x_api_key: str | None = None,
    signature: ApiKeySignaturePayload | None = None,
    client_ip: str | None = None,
    force_signature: bool = False,
) -> JSONResponse | ApiKeyAuthResult | None:
    """验证 OpenAPI 调用权限"""
    if not (kb_id or "").strip():
        return _error_response(400, "KB_ID_REQUIRED", "kb_id is required for OpenAPI calls", request_id=request_id)
    capabilities = (capability,) if isinstance(capability, str) else tuple(capability)
    if not capabilities:
        return _error_response(403, "CAPABILITY_DENIED", "API Key lacks the required capability", request_id=request_id)
    api_key = _extract_api_key(authorization, x_api_key)
    if api_key:
        for index, item in enumerate(capabilities):
            try:
                result = authenticate_api_key(
                    api_key,
                    kb_id=kb_id or "",
                    capability=item,
                    signature=signature,
                    client_ip=client_ip,
                    force_signature=force_signature,
                )
                return result
            except ApiKeyError as exc:
                can_try_next = exc.code == "CAPABILITY_DENIED" and index < len(capabilities) - 1
                if can_try_next:
                    continue
                _audit_openapi_denied(
                    exc,
                    request_id=request_id,
                    kb_id=kb_id,
                    capability=item,
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
    force_signature: bool = False,
) -> JSONResponse | ApiKeyAuthResult:
    """验证 OpenAPI 能力权限"""
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
            force_signature=force_signature,
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


def _openapi_kb_binding_denied(
    auth_result: ApiKeyAuthResult,
    *,
    request_id: str,
    kb_id: str | None,
    capability: str,
    signature: ApiKeySignaturePayload | None,
    client_ip: str | None,
) -> JSONResponse:
    """返回知识库绑定校验失败的错误响应"""
    exc = ApiKeyError(
        "KB_BINDING_DENIED",
        "API Key is not bound to this knowledge base",
        api_key_id=auth_result.api_key_id,
    )
    _audit_openapi_denied(
        exc,
        request_id=request_id,
        kb_id=kb_id,
        capability=capability,
        signature=signature,
        client_ip=client_ip,
    )
    return _error_response(403, exc.code, exc.message, request_id=request_id)


def _acquire_openapi_concurrency_or_error(
    auth_result: ApiKeyAuthResult | None,
    *,
    request_id: str,
    kb_id: str | None,
    capability: str,
    signature: ApiKeySignaturePayload | None,
    client_ip: str | None,
) -> bool | JSONResponse:
    """
    获取 OpenAPI 并发槽位

    尝试为当前 API Key 获取一个并发槽位，用于并发控制。
    如果 API Key 配置了并发限制且已达到上限，则返回错误响应。

    并发控制机制：
    1. 每个 API Key 可以配置最大并发数（concurrentLimit）
    2. 每个请求开始时获取槽位，结束时释放
    3. 超过限制的请求返回 429 错误

    参数:
        auth_result: API Key 认证结果，包含并发限制配置
        request_id: 请求 ID，用于日志追踪
        kb_id: 知识库 ID
        capability: 请求的能力权限
        signature: 签名信息（用于审计日志）
        client_ip: 客户端 IP

    返回值:
        bool | JSONResponse:
            - True: 成功获取槽位
            - False: 无需并发控制（未配置限制）
            - JSONResponse: 并发超限错误响应

    使用场景:
        - OpenAPI 接口的并发控制
        - 防止 API Key 滥用
        - 保护系统资源

    错误响应示例:
        ```json
        {
          "requestId": "req_abc123",
          "error": {
            "code": "CONCURRENCY_LIMITED",
            "message": "API Key has reached concurrency limit",
            "details": {
              "limit": 10,
              "current": 10
            }
          }
        }
        ```

    注意事项:
        - 获取槽位后必须调用 _release_openapi_concurrency 释放
        - 建议在 try-finally 块中确保释放
    """
    if auth_result is None or auth_result.concurrent_limit <= 0:
        return False
    try:
        return acquire_api_key_concurrency_slot(auth_result.api_key_id, auth_result.concurrent_limit)
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


def _release_openapi_concurrency(auth_result: ApiKeyAuthResult | None, acquired: bool | JSONResponse) -> None:
    """
    释放 OpenAPI 并发槽位

    释放之前获取的并发槽位，允许其他请求使用。
    这是并发控制的清理函数，必须在请求结束时调用。

    参数:
        auth_result: API Key 认证结果
        acquired: 槽位获取结果（来自 _acquire_openapi_concurrency_or_error）
            - True: 之前成功获取了槽位，需要释放
            - False 或 JSONResponse: 未获取槽位，无需操作

    使用场景:
        - OpenAPI 请求结束时释放资源
        - 确保 finally 块中调用以防止资源泄漏

    代码示例:
        ```python
        concurrency = _acquire_openapi_concurrency_or_error(...)
        if isinstance(concurrency, JSONResponse):
            return concurrency
        try:
            # 执行业务逻辑
            return do_something()
        finally:
            _release_openapi_concurrency(auth_result, concurrency)
        ```

    注意事项:
        - 必须在 try-finally 中调用，确保异常时也能释放
        - 只释放成功获取的槽位（acquired == True）
        - 重复释放是安全的（不会产生副作用）
    """
    if auth_result is None or not isinstance(acquired, bool) or not acquired:
        return
    release_api_key_concurrency_slot(auth_result.api_key_id)


def _extract_api_key(authorization: str | None, x_api_key: str | None) -> str:
    """从请求头提取 API Key"""
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
    """构建签名验证载荷"""
    raw_query = str(request.url.query or "")
    path = request.url.path
    path_with_query = f"{path}?{raw_query}" if raw_query else path
    alternate_paths = (path,) if path_with_query != path else ()
    return ApiKeySignaturePayload(
        method=request.method,
        path=path_with_query,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
        signature=signature,
        alternate_paths=alternate_paths,
    )


def _client_ip(request: Request, x_forwarded_for: str | None) -> str:
    """获取客户端真实 IP"""
    forwarded = (x_forwarded_for or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _api_key_status(code: str) -> int:
    """根据错误码返回 HTTP 状态码"""
    if code in {"API_KEY_REQUIRED", "INVALID_API_KEY", "API_KEY_DISABLED", "API_KEY_EXPIRED"}:
        return 401
    if code in {"KB_BINDING_DENIED", "CAPABILITY_DENIED"}:
        return 403
    if code in {"SIGNATURE_REQUIRED", "INVALID_SIGNATURE", "BODY_HASH_MISMATCH", "TIMESTAMP_EXPIRED", "INVALID_TIMESTAMP", "INVALID_NONCE", "NONCE_REPLAYED"}:
        return 401
    if code == "IP_NOT_ALLOWED":
        return 403
    if code in {"RATE_LIMITED", "QUOTA_EXCEEDED", "CONCURRENCY_LIMITED", "MONTHLY_QUOTA_EXCEEDED"}:
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
    """记录 OpenAPI 拒绝访问审计日志"""
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
    """根据错误码评估风险等级"""
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
    if code in {
        "RATE_LIMITED",
        "QUOTA_EXCEEDED",
        "CONCURRENCY_LIMITED",
        "MONTHLY_QUOTA_EXCEEDED",
        "CAPABILITY_DENIED",
        "KB_BINDING_DENIED",
    }:
        return "medium"
    return "low"


def _mask_client_ip(value: str | None) -> str:
    """脱敏客户端 IP 地址"""
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
    """获取切片策略的中文标签"""
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
    """检查解析器提供者的可用性"""
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
    """标准化选项值，无效值返回默认值"""
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
    """验证提示词覆盖权限"""
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
    """生成请求 ID"""
    return str(uuid.uuid4())


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    details: dict | None = None,
) -> JSONResponse:
    """构建错误响应"""
    payload = {
        "requestId": request_id or _request_id(),
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
    return JSONResponse(payload, status_code=status_code)


def _json_safe_error_value(value: object) -> object:
    """将错误值转换为 JSON 安全格式"""
    if isinstance(value, bytes):
        return "<redacted bytes>"
    if isinstance(value, bytearray):
        return "<redacted bytes>"
    if isinstance(value, dict):
        return {
            str(key): ("<redacted>" if key == "input" else _json_safe_error_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_safe_error_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_error_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def validation_error_response(exc_errors: list[dict]) -> JSONResponse:
    """构建验证错误响应"""
    return _error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request payload validation failed",
        details={"errors": _json_safe_error_value(exc_errors)},
    )
