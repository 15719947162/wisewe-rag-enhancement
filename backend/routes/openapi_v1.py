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
MAX_OPENAPI_UPLOAD_SIZE = 500 * 1024 * 1024
SUBJECT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in SUBJECT_KEY_MAP.items()
]
LAYOUT_OPTIONS = [
    {"value": key, "label": label}
    for key, label in LAYOUT_KEY_MAP.items()
]


class OpenApiQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)
    kb_id: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=8, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    use_llm_check: bool = False
    use_llm_score: bool = False


class OpenApiGraphQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)
    kb_id: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    explain: bool = False
    intent: str | None = Field(default=None, max_length=100)


class PromptAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="append", pattern="^append$")
    content: str = Field(..., min_length=1, max_length=2000)


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
