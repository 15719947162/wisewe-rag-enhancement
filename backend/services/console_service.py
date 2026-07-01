from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from backend.services.evaluation_store import load_evaluations
from backend.services.access_control import filter_tasks_by_identity
from backend.services.ingestion_service import get_all_tasks
from backend.services.kb_service import get_documents_payload, get_knowledge_bases_payload
from core.config import load_config
from core.db.api_keys import (
    create_api_key,
    create_openapi_app,
    delete_api_key,
    delete_openapi_app,
    list_api_keys,
    list_openapi_apps,
    rotate_api_key,
    update_api_key,
    update_openapi_app,
)
from core.db.identity import IdentityContext
from core.db.identity import list_identity_sync_runs
from core.db.query_logs import AuditLogRecord, append_audit_log, fetch_audit_logs, fetch_rag_query_logs, fetch_token_usage_summary_for_identity
from core.db.query_logs import export_rag_query_logs_csv
from core.db.connection import is_db_available
from core.runtime_settings import (
    RUNTIME_SETTING_SPECS,
    resolve_runtime_setting,
    save_runtime_overrides,
    stringify_runtime_value,
)

EDITABLE_SETTINGS_KEYS = set(RUNTIME_SETTING_SPECS.keys())
SENSITIVE_SETTING_TOKENS = ("api_key", "access_key", "credential", "secret", "password", "token")
MASKED_SETTING_PREFIX = "****"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry(
    label: str,
    value: object,
    *,
    category: str = "common",
    editable: bool = False,
    source: str = "env",
    sensitive: bool = False,
    has_value: bool | None = None,
) -> dict[str, object]:
    string_value = stringify_runtime_value(value)
    effective_mode = _setting_effective_mode(label)
    return {
        "label": label,
        "value": string_value,
        "category": category,
        "editable": editable,
        "source": source,
        "sensitive": sensitive,
        "hasValue": (string_value != "") if has_value is None else has_value,
        "configScope": "global",
        "effectiveMode": effective_mode,
        "governance": {
            "layer": "global",
            "effectiveMode": effective_mode,
            "editableBy": "platform_admin",
            "kbOverrideSupported": _kb_override_supported(label, effective_mode),
            "apiKeyOverrideSupported": False,
        },
    }


def _is_sensitive_setting(key: str) -> bool:
    normalized = key.lower()
    return any(token in normalized for token in SENSITIVE_SETTING_TOKENS)


def _mask_sensitive_value(value: object) -> str:
    string_value = stringify_runtime_value(value)
    if not string_value:
        return ""
    if len(string_value) <= 4:
        return "****"
    return f"****{string_value[-4:]}"


def _looks_like_masked_sensitive_value(value: str) -> bool:
    return value.strip().startswith(MASKED_SETTING_PREFIX)


def _setting_effective_mode(key: str) -> str:
    normalized = key.lower()
    if _is_sensitive_setting(key):
        return "ops"
    if "system_prompt" in normalized or "prompt_profile" in normalized:
        return "hot"
    if normalized.startswith("rag_") and not any(token in normalized for token in ("model", "base_url")):
        return "hot"
    if any(token in normalized for token in ("model", "provider", "rerank", "embedding", "chunk", "clean", "quality")):
        return "gray"
    if any(token in normalized for token in ("database", "pgvector", "oss_", "endpoint", "host", "port", "storage")):
        return "ops"
    return "hot"


def _kb_override_supported(key: str, effective_mode: str) -> bool:
    if effective_mode not in {"hot", "gray"}:
        return False
    normalized = key.lower()
    return any(token in normalized for token in ("rag_", "prompt", "chunk", "clean", "quality", "embedding", "rerank"))


def sanitize_console_settings_update(payload: dict) -> dict[str, str]:
    safe_payload: dict[str, str] = {}
    for key, value in payload.items():
        if key not in EDITABLE_SETTINGS_KEYS or not isinstance(value, str):
            continue
        if _is_sensitive_setting(key) and _looks_like_masked_sensitive_value(value):
            continue
        safe_payload[key] = value
    return safe_payload


def get_settings_payload() -> list[dict]:
    config = load_config()
    runtime_entries = {}
    for key in EDITABLE_SETTINGS_KEYS:
        value, source = resolve_runtime_setting(key, config=config)
        sensitive = _is_sensitive_setting(key)
        runtime_entries[key] = _entry(
            key,
            _mask_sensitive_value(value) if sensitive else value,
            category=RUNTIME_SETTING_SPECS[key].category,
            editable=True,
            source=source,
            sensitive=sensitive,
            has_value=stringify_runtime_value(value) != "",
        )

    parser_cfg = config.get("parser", {})
    parser_oss_cfg = parser_cfg.get("oss", {})
    output_cfg = config.get("output", {})
    pgvector_cfg = config.get("pgvector", {})

    return [
        {
            "id": "models_common",
            "title": "通用模型",
            "description": "默认 LLM 服务地址与通用密钥，供未单独配置的清洗、切片、增强等功能复用。",
            "values": [
                runtime_entries["LLM_BASE_URL"],
                runtime_entries["LLM_API_KEY"],
            ],
        },
        {
            "id": "models_embedding",
            "title": "向量模型",
            "description": "负责切片向量化与向量检索的 embedding 模型配置。",
            "values": [
                runtime_entries["LLM_EMBEDDING_MODEL"],
                runtime_entries["LLM_EMBEDDING_BATCH_SIZE"],
                runtime_entries["LLM_EMBEDDING_MAX_CONCURRENCY"],
                runtime_entries["LLM_EMBEDDING_MAX_RETRIES"],
                runtime_entries["LLM_EMBEDDING_API_KEY_POOL"],
                runtime_entries["LLM_EMBEDDING_KEY_RETRIES"],
                runtime_entries["LLM_EMBEDDING_KEY_COOLDOWN_SECONDS"],
                runtime_entries["RAG_RETRIEVAL_SNAPSHOT"],
            ],
        },
        {
            "id": "models_cleaner",
            "title": "清洗模型",
            "description": "负责解析后文本清洗、噪声过滤与教材内容保留的模型和系统提示词。",
            "values": [
                runtime_entries["LLM_CLEANER_ENABLED"],
                runtime_entries["LLM_CLEANER_MODEL"],
                runtime_entries["LLM_CLEANER_BASE_URL"],
                runtime_entries["LLM_CLEANER_API_KEY"],
                runtime_entries["LLM_CLEANER_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "models_chunker",
            "title": "切片模型",
            "description": "负责 LLM 切片边界判断的系统提示词，强调知识点完整性与可引用粒度。",
            "values": [
                runtime_entries["LLM_CHUNKER_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "models_quality",
            "title": "质量审核模型",
            "description": "负责切片质量门控，避免过滤掉短图注、表格、公式和题目等有效教材证据。",
            "values": [
                runtime_entries["LLM_QUALITY_GATE_ENABLED"],
                runtime_entries["LLM_QUALITY_GATE_MODEL"],
                runtime_entries["LLM_QUALITY_GATE_BASE_URL"],
                runtime_entries["LLM_QUALITY_GATE_API_KEY"],
                runtime_entries["LLM_QUALITY_GATE_MIN_SCORE"],
                runtime_entries["LLM_QUALITY_GATE_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "models_enhance",
            "title": "三层增强模型",
            "description": "负责 parent/child/enhanced 链路中的切片摘要、图片描述、表格摘要和结构化抽取提示词。",
            "values": [
                runtime_entries["LLM_ENHANCE_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "models_rag",
            "title": "问答生成模型",
            "description": "负责在线问答生成与引用约束。建议明确要求基于上下文回答，不足时直说无法可靠回答。",
            "values": [
                runtime_entries["RAG_LLM_MODEL"],
                runtime_entries["RAG_LLM_BASE_URL"],
                runtime_entries["RAG_LLM_API_KEY"],
                runtime_entries["RAG_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "identity_sso",
            "title": "身份与 SSO",
            "description": "AI 基座身份接入、SSO 跳转与身份快照同步路径配置。支持服务端配置化管理接口前缀，避免硬编码。",
            "values": [
                runtime_entries["AI_BASE_SSO_BASE_URL"],
                runtime_entries["AI_BASE_SSO_CLIENT_ID"],
                runtime_entries["AI_BASE_SSO_CLIENT_SECRET"],
                runtime_entries["AI_BASE_SSO_REDIRECT_URI"],
                runtime_entries["AI_BASE_SSO_LAUNCH_BASE_URL"],
                runtime_entries["AI_BASE_SSO_LAUNCH_PATH"],
                runtime_entries["AI_BASE_SSO_EXCHANGE_PATH"],
                runtime_entries["AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE"],
                runtime_entries["AI_BASE_SSO_DELTA_PATH"],
                runtime_entries["KB_CONSOLE_BASE_URL"],
                runtime_entries["KB_SESSION_TTL_SECONDS"],
                runtime_entries["KB_LEGACY_HEADER_AUTH_ENABLED"],
                runtime_entries["AI_BASE_IDENTITY_SYNC_ENABLED"],
                runtime_entries["AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS"],
                runtime_entries["AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP"],
                runtime_entries["KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS"],
                runtime_entries["KB_CORS_ALLOW_ORIGINS"],
                runtime_entries["KB_CORS_ALLOW_HEADERS"],
            ],
        },
        {
            "id": "parser",
            "title": "解析配置",
            "description": "MinerU 云解析关键参数与 302AI 接入配置。支持覆盖 parse_method、OCR、语言、模型版本等。",
            "values": [
                _entry("PARSER_MODE", parser_cfg.get("mode", "cloud"), source="config"),
                runtime_entries["PDF_PARSER_PROVIDER"],
                runtime_entries["PDF_PARSER_FALLBACKS"],
                runtime_entries["parser.cloud.parse_method"],
                runtime_entries["parser.cloud.version"],
                runtime_entries["parser.cloud.timeout"],
                runtime_entries["parser.cloud.poll_interval"],
                runtime_entries["parser.cloud.enable_formula"],
                runtime_entries["parser.cloud.enable_table_html"],
                runtime_entries["parser.cloud.language"],
                runtime_entries["parser.cloud.is_ocr"],
                runtime_entries["parser.cloud.model_version"],
                runtime_entries["parser.cloud.sharding.enabled"],
                runtime_entries["parser.cloud.sharding.min_pages"],
                runtime_entries["parser.cloud.sharding.min_file_mb"],
                runtime_entries["parser.cloud.sharding.pages_per_shard"],
                runtime_entries["parser.cloud.sharding.max_concurrency"],
                runtime_entries["parser.cloud.sharding.text_sample_pages"],
                runtime_entries["302AI_API_BASE"],
                runtime_entries["302AI_API_KEY"],
                runtime_entries["MINERU_OFFICIAL_API_BASE"],
                runtime_entries["MINERU_OFFICIAL_API_TOKEN"],
                runtime_entries["MINERU_OFFICIAL_MODEL_VERSION"],
                runtime_entries["MINERU_OFFICIAL_TIMEOUT"],
                runtime_entries["MINERU_OFFICIAL_POLL_INTERVAL"],
                runtime_entries["MINERU_OFFICIAL_ENABLE_FORMULA"],
                runtime_entries["MINERU_OFFICIAL_ENABLE_TABLE"],
                runtime_entries["MINERU_OFFICIAL_LANGUAGE"],
                runtime_entries["MINERU_OFFICIAL_IS_OCR"],
                runtime_entries["MINERU_OFFICIAL_EXTRA_FORMATS"],
                runtime_entries["MINERU_OFFICIAL_NO_CACHE"],
                runtime_entries["MINERU_OFFICIAL_CACHE_TOLERANCE"],
                runtime_entries["MINERU_OFFICIAL_SUBMIT_RETRY_ATTEMPTS"],
                runtime_entries["MINERU_OFFICIAL_POLL_RETRY_ATTEMPTS"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_ENABLED"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_MIN_FILE_MB"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_MIN_PAGES"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD"],
                runtime_entries["MINERU_OFFICIAL_SHARDING_TEXT_SAMPLE_PAGES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_ENDPOINT"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_TIMEOUT"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_POLL_INTERVAL"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_KEY_RETRIES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_LAYOUT_STEP_SIZE"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE"],
                runtime_entries["ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE"],
            ],
        },
        {
            "id": "chunking",
            "title": "切片参数",
            "description": "展示当前切片策略的默认参数与提示词入口，便于核对运行时行为。",
            "values": [
                _entry("DEFAULT_INGESTION_STRATEGY", "hierarchical", source="code"),
                _entry("FIXED_LENGTH_CHUNK_SIZE", 1000, source="code"),
                _entry("FIXED_LENGTH_OVERLAP", 50, source="code"),
                _entry("PARAGRAPH_MIN_CHARS", 64, source="code"),
                _entry("PARAGRAPH_MAX_CHARS", 512, source="code"),
                _entry("SEMANTIC_MAX_CHUNK_SIZE", 1000, source="code"),
                _entry("LLM_MAX_CHUNK_SIZE", 800, source="code"),
                _entry("HIERARCHICAL_CHILD_MAX_CHARS", 600, source="code"),
                runtime_entries["INGESTION_READY_MODE"],
                runtime_entries["HIERARCHICAL_ENHANCE_MODE"],
                runtime_entries["HIERARCHICAL_TEXT_ENHANCE_WORKERS"],
                runtime_entries["HIERARCHICAL_TABLE_ENHANCE_WORKERS"],
                runtime_entries["HIERARCHICAL_IMAGE_ENHANCE_WORKERS"],
                runtime_entries["HIERARCHICAL_ENHANCE_MAX_CONCURRENCY"],
                runtime_entries["HIERARCHICAL_REUSE_LLM_CLIENTS"],
                runtime_entries["LLM_API_KEY_POOL"],
                runtime_entries["VL_API_KEY_POOL"],
                runtime_entries["HIERARCHICAL_ENHANCE_KEY_RETRIES"],
                runtime_entries["HIERARCHICAL_KEY_COOLDOWN_SECONDS"],
                runtime_entries["LLM_CHUNKER_SYSTEM_PROMPT"],
                runtime_entries["LLM_ENHANCE_SYSTEM_PROMPT"],
            ],
        },
        {
            "id": "vector_db",
            "title": "向量数据库配置",
            "description": "PostgreSQL / pgvector 连接配置与默认知识库。数据库连接字段支持持久覆盖。",
            "values": [
                _entry("PGVECTOR_ENABLED", pgvector_cfg.get("enabled", False), source="config"),
                _entry("PGVECTOR_DEFAULT_KB_ID", pgvector_cfg.get("default_kb_id", "default"), source="config"),
                _entry("DB_AVAILABLE", is_db_available(), source="system"),
                runtime_entries["DATABASE_URL"],
                runtime_entries["PGVECTOR_HOST"],
                runtime_entries["PGVECTOR_PORT"],
                runtime_entries["PGVECTOR_DB"],
                runtime_entries["PGVECTOR_USER"],
                runtime_entries["PGVECTOR_PASSWORD"],
            ],
        },
        {
            "id": "storage",
            "title": "存储配置",
            "description": "OSS 上传、本地输出目录与解析结果存储配置。",
            "values": [
                runtime_entries["OSS_ACCESS_KEY_ID"],
                runtime_entries["OSS_ACCESS_KEY_SECRET"],
                runtime_entries["OSS_ENDPOINT"],
                runtime_entries["OSS_BUCKET"],
                _entry("PARSER_OSS_PREFIX", parser_oss_cfg.get("prefix", "mineru-uploads"), source="config"),
                _entry("PARSER_OSS_URL_EXPIRY", parser_oss_cfg.get("url_expiry", 3600), source="config"),
                _entry("OUTPUT_DIR", output_cfg.get("dir", "data/output"), source="config"),
                _entry("OUTPUT_ENCODING", output_cfg.get("encoding", "utf-8-sig"), source="config"),
            ],
        },
        {
            "id": "about",
            "title": "关于",
            "description": "当前控制台展示的是代码、配置文件、环境变量与数据库覆盖层的聚合视图。",
            "values": [
                _entry("PROJECT_NAME", "wisewe-rag-simple", source="system"),
                _entry("SETTINGS_GROUP_COUNT", 13, source="system"),
                _entry("CONFIG_FILE", "config.yaml", source="system"),
                _entry("SETTINGS_PRIORITY", "DB override > env > config > code", source="system"),
                _entry("PLANNING_WORKFLOW", "GSD + 共享账本", source="system"),
                _entry("TECH_STACK", "Python / Next.js / pgvector / MinerU", source="system"),
            ],
        },
    ]


def update_console_settings(payload: dict[str, str], updated_by: str = "console") -> dict[str, object]:
    updated = save_runtime_overrides(payload, updated_by=updated_by)
    return {"updated": updated, "count": len(updated)}


def update_console_settings_with_audit(
    payload: dict[str, str],
    *,
    identity: IdentityContext | None = None,
    updated_by: str = "console",
) -> dict[str, object]:
    result = update_console_settings(payload, updated_by=updated_by)
    updated = list(result.get("updated") or [])
    append_audit_log(
        AuditLogRecord(
            action="settings.update",
            resource_type="settings",
            resource_id="runtime",
            identity=identity,
            outcome="success",
            risk_level="medium" if updated else "low",
            summary=f"Updated {len(updated)} runtime settings",
            metadata={"updatedKeys": updated, "updatedCount": len(updated)},
        )
    )
    return result


def get_console_alerts() -> list[dict]:
    alerts: list[dict] = []

    if not is_db_available():
        alerts.append(
            {
                "id": "alert-db-unavailable",
                "title": "PostgreSQL 不可用",
                "description": "知识库、文档、检索与统计接口将无法返回真实数据。",
                "severity": "failed",
                "area": "storage",
            }
        )

    parser_provider = str(resolve_runtime_setting("PDF_PARSER_PROVIDER")[0] or "mineru")
    if parser_provider == "mineru" and not resolve_runtime_setting("302AI_API_KEY")[0]:
        alerts.append(
            {
                "id": "alert-mineru-key-missing",
                "title": "302AI API Key 未配置",
                "description": "真实 MinerU 云解析无法执行，文档入库会直接失败。",
                "severity": "degraded",
                "area": "ingestion",
            }
        )
    if parser_provider == "mineru_official" and not resolve_runtime_setting("MINERU_OFFICIAL_API_TOKEN")[0]:
        alerts.append(
            {
                "id": "alert-mineru-official-token-missing",
                "title": "MinerU 官方 API Token 未配置",
                "description": "当前解析渠道为 mineru_official，但官方 MinerU 精准解析 Token 为空，文档入库会失败。",
                "severity": "degraded",
                "area": "ingestion",
            }
        )

    if not resolve_runtime_setting("LLM_API_KEY")[0] and not resolve_runtime_setting("RAG_LLM_API_KEY")[0]:
        alerts.append(
            {
                "id": "alert-llm-key-missing",
                "title": "LLM API Key 未配置",
                "description": "向量化与 RAG 生成链路无法返回真实结果。",
                "severity": "degraded",
                "area": "rag",
            }
        )

    return alerts


def get_console_queue() -> list[dict]:
    tasks = get_all_tasks()
    queue: list[dict] = []

    for task in tasks:
        status = task.get("status", "pending")
        if status == "success":
            continue

        lane = "recent"
        if status == "failed":
            lane = "failed"
        elif status in {"pending", "running", "awaiting_confirmation"}:
            lane = "pending"

        queue.append(
            {
                "id": task["id"],
                "lane": lane,
                "title": task.get("filename", task["id"]),
                "subtitle": f"{task.get('kb_id', 'default')} / {task.get('strategy', 'unknown')}",
                "status": status,
                "linkedHref": "/ingestion",
                "updatedAt": task.get("updated_at", "") or _utc_now(),
            }
        )

    return queue


def get_console_metrics(identity: IdentityContext | None = None) -> list[dict]:
    kb_count = len(get_knowledge_bases_payload(identity))
    doc_count = len(get_documents_payload(identity=identity))
    active_task_count = len(
        [task for task in get_all_tasks() if task.get("status") in {"pending", "running", "awaiting_confirmation"}]
    )
    return [
        {"label": "知识库", "value": str(kb_count), "helper": "来自 knowledge_bases 表", "delta": "实时统计", "tone": "good"},
        {"label": "文档", "value": str(doc_count), "helper": "来自 documents 表", "delta": "实时统计", "tone": "neutral"},
        {"label": "活跃任务", "value": str(active_task_count), "helper": "来自任务流与待确认草稿", "delta": "实时统计", "tone": "neutral"},
    ]


def get_console_evaluations(kb_id: str | None = None, identity: IdentityContext | None = None) -> list[dict]:
    records = load_evaluations()
    if identity and identity.enforce_access:
        visible_kb_ids = {str(item["id"]) for item in get_knowledge_bases_payload(identity)}
        records = [record for record in records if str(record.get("kbId") or "") in visible_kb_ids]
    if kb_id:
        records = [record for record in records if record.get("kbId") == kb_id]
    return records


def get_console_query_logs(
    *,
    tenant_id: str | None = None,
    kb_id: str | None = None,
    request_id: str | None = None,
    actor_id: str | None = None,
    api_key_id: str | None = None,
    pipeline_domain: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 50,
    identity: IdentityContext | None = None,
) -> list[dict]:
    records = fetch_rag_query_logs(
        tenant_id=tenant_id,
        kb_id=kb_id,
        request_id=request_id,
        actor_id=actor_id,
        api_key_id=api_key_id,
        pipeline_domain=pipeline_domain,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return _filter_records_by_visible_kbs(records, identity)


def get_console_audit_logs(
    *,
    tenant_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    kb_id: str | None = None,
    outcome: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 50,
    identity: IdentityContext | None = None,
) -> list[dict]:
    records = fetch_audit_logs(
        tenant_id=tenant_id,
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
    )
    return _filter_records_by_visible_kbs(records, identity)


def get_console_identity_sync_logs(limit: int = 100, identity: IdentityContext | None = None) -> list[dict]:
    return list_identity_sync_runs(limit=limit)


def get_console_ingestion_tasks(
    *,
    keyword: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    page: int = 1,
    page_size: int = 20,
    identity: IdentityContext | None = None,
) -> dict:
    kb_items = get_knowledge_bases_payload(identity)
    kb_by_id = {str(item["id"]): item for item in kb_items}
    normalized_keyword = (keyword or "").strip().lower()
    tasks = [
        _ingestion_task_row(task, kb_by_id.get(str(task.get("kb_id") or "")))
        for task in filter_tasks_by_identity(get_all_tasks(), identity or IdentityContext())
    ]

    if status:
        tasks = [task for task in tasks if task["status"] == status]
    if strategy:
        tasks = [task for task in tasks if task["strategy"] == strategy]
    if normalized_keyword:
        tasks = [
            task
            for task in tasks
            if normalized_keyword in " ".join(
                [
                    task["kbName"],
                    task["kbId"],
                    task["documentName"],
                ]
            ).lower()
        ]

    page_size = max(1, min(int(page_size or 20), 100))
    total = len(tasks)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), page_count))
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": tasks[start:end],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "pageCount": page_count,
    }


def get_latest_ingestion_log(
    *,
    kb_id: str | None = None,
    max_lines: int = 500,
    identity: IdentityContext | None = None,
) -> dict:
    kb_items = get_knowledge_bases_payload(identity)
    kb_by_id = {str(item["id"]): item for item in kb_items}
    tasks = filter_tasks_by_identity(get_all_tasks(), identity or IdentityContext())
    if kb_id:
        tasks = [task for task in tasks if task.get("kb_id") == kb_id]

    if not tasks:
        return {"task": None, "lines": [], "lineCount": 0, "truncated": False}

    latest = sorted(tasks, key=lambda task: str(task.get("updated_at") or task.get("created_at") or ""), reverse=True)[0]
    task_row = _ingestion_task_row(latest, kb_by_id.get(str(latest.get("kb_id") or "")))
    log_path = Path("data/logs") / f"{latest.get('id')}.log"
    lines: list[str] = []
    if log_path.is_file():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    max_lines = max(1, min(int(max_lines or 500), 2000))
    truncated = len(lines) > max_lines
    return {
        "task": task_row,
        "lines": lines[-max_lines:],
        "lineCount": len(lines),
        "truncated": truncated,
        "logPath": os.fspath(log_path),
    }


def get_console_token_usage(
    limit: int = 10,
    identity: IdentityContext | None = None,
    pipeline_domain: str | None = None,
) -> dict:
    return fetch_token_usage_summary_for_identity(
        limit=limit,
        tenant_id=identity.tenant_id if identity and identity.enforce_access else None,
        include_all_tenants=bool(identity and identity.is_platform_admin),
        pipeline_domain=pipeline_domain,
    )


def _filter_records_by_visible_kbs(records: list[dict], identity: IdentityContext | None = None) -> list[dict]:
    if not identity or not identity.enforce_access or identity.is_tenant_admin or identity.is_platform_admin:
        return records
    visible_kb_ids = {str(item["id"]) for item in get_knowledge_bases_payload(identity)}
    return [record for record in records if str(record.get("kbId") or record.get("kb_id") or "") in visible_kb_ids]


def export_console_query_logs(
    *,
    tenant_id: str | None = None,
    kb_id: str | None = None,
    request_id: str | None = None,
    actor_id: str | None = None,
    api_key_id: str | None = None,
    pipeline_domain: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 10000,
    identity: IdentityContext | None = None,
) -> tuple[str, bytes]:
    result = export_rag_query_logs_csv(
        tenant_id=tenant_id,
        kb_id=kb_id,
        request_id=request_id,
        actor_id=actor_id,
        api_key_id=api_key_id,
        pipeline_domain=pipeline_domain,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    append_audit_log(
        AuditLogRecord(
            action="query_logs.export",
            resource_type="query_logs",
            identity=identity,
            kb_id=kb_id,
            outcome="success",
            risk_level="medium",
            summary="Exported query logs CSV",
            metadata={
                "tenantId": tenant_id,
                "kbId": kb_id,
                "requestId": request_id,
                "actorId": actor_id,
                "apiKeyId": api_key_id,
                "pipelineDomain": pipeline_domain,
                "startAt": start_at.isoformat() if start_at else "",
                "endAt": end_at.isoformat() if end_at else "",
                "limit": limit,
                "filename": result[0],
                "bytes": len(result[1]),
            },
        )
    )
    return result


def get_console_api_keys(identity: IdentityContext | None = None) -> list[dict]:
    _assert_api_key_manager(identity)
    return list_api_keys(identity)


def get_console_openapi_apps(identity: IdentityContext | None = None) -> list[dict]:
    _assert_api_key_manager(identity)
    return list_openapi_apps(identity)


def create_console_openapi_app(payload: dict, identity: IdentityContext | None = None) -> dict:
    _assert_api_key_manager(identity)
    result = create_openapi_app(
        name=str(payload.get("name") or ""),
        note=str(payload.get("note") or ""),
        identity=identity,
    )
    append_audit_log(
        AuditLogRecord(
            action="openapi_app.create",
            resource_type="openapi_app",
            resource_id=result.get("id"),
            identity=identity,
            outcome="success",
            risk_level="medium",
            summary=f"Created OpenAPI app {result.get('id')}",
            metadata={"name": result.get("name"), "note": result.get("note")},
        )
    )
    return result


def update_console_openapi_app(app_id: str, payload: dict, identity: IdentityContext | None = None) -> dict | None:
    _assert_api_key_manager(identity)
    result = update_openapi_app(
        app_id,
        name=payload.get("name") if "name" in payload else None,
        status=payload.get("status") if "status" in payload else None,
        note=payload.get("note") if "note" in payload else None,
        identity=identity,
    )
    if result:
        append_audit_log(
            AuditLogRecord(
                action="openapi_app.update",
                resource_type="openapi_app",
                resource_id=app_id,
                identity=identity,
                outcome="success",
                risk_level="medium",
                summary=f"Updated OpenAPI app {app_id}",
                metadata={"changedFields": sorted(payload.keys())},
            )
        )
    return result


def delete_console_openapi_app(app_id: str, identity: IdentityContext | None = None) -> bool:
    _assert_api_key_manager(identity)
    deleted = delete_openapi_app(app_id, identity)
    if deleted:
        append_audit_log(
            AuditLogRecord(
                action="openapi_app.delete",
                resource_type="openapi_app",
                resource_id=app_id,
                identity=identity,
                outcome="success",
                risk_level="medium",
                summary=f"Deleted OpenAPI app {app_id}",
            )
        )
    return deleted


def create_console_api_key(payload: dict, identity: IdentityContext | None = None) -> dict:
    _assert_api_key_manager(identity)
    result = create_api_key(
        name=str(payload.get("name") or ""),
        kb_ids=list(payload.get("kbIds") or []),
        capabilities=list(payload.get("capabilities") or []),
        require_signature=bool(payload.get("requireSignature", True)),
        allowed_ips=list(payload.get("allowedIps") or []),
        rpm_limit=int(payload.get("rpmLimit") or 0),
        daily_request_limit=int(payload.get("dailyRequestLimit") or 0),
        app_id=payload.get("appId"),
        note=str(payload.get("note") or ""),
        expires_at=payload.get("expiresAt"),
        identity=identity,
    )
    append_audit_log(
        AuditLogRecord(
            action="api_key.create",
            resource_type="api_key",
            resource_id=result.get("id"),
            api_key_id=result.get("id"),
            identity=identity,
            outcome="success",
            risk_level="high",
            summary=f"Created API Key {result.get('id')}",
            metadata={
                "name": result.get("name"),
                "kbIds": result.get("kbIds", []),
                "capabilities": result.get("capabilities", []),
                "requireSignature": result.get("requireSignature"),
                "allowedIps": result.get("allowedIps", []),
                "rpmLimit": result.get("rpmLimit"),
                "dailyRequestLimit": result.get("dailyRequestLimit"),
                "appId": result.get("appId"),
                "expiresAt": result.get("expiresAt"),
            },
        )
    )
    return result


def update_console_api_key(key_id: str, payload: dict, identity: IdentityContext | None = None) -> dict | None:
    _assert_api_key_manager(identity)
    result = update_api_key(
        key_id,
        name=payload.get("name") if "name" in payload else None,
        status=payload.get("status") if "status" in payload else None,
        kb_ids=list(payload.get("kbIds")) if "kbIds" in payload and payload.get("kbIds") is not None else None,
        capabilities=(
            list(payload.get("capabilities"))
            if "capabilities" in payload and payload.get("capabilities") is not None
            else None
        ),
        require_signature=payload.get("requireSignature") if "requireSignature" in payload else None,
        allowed_ips=list(payload.get("allowedIps")) if "allowedIps" in payload and payload.get("allowedIps") is not None else None,
        rpm_limit=payload.get("rpmLimit") if "rpmLimit" in payload else None,
        daily_request_limit=payload.get("dailyRequestLimit") if "dailyRequestLimit" in payload else None,
        app_id=payload.get("appId") if "appId" in payload else None,
        app_id_provided="appId" in payload,
        note=payload.get("note") if "note" in payload else None,
        expires_at=payload.get("expiresAt") if "expiresAt" in payload else None,
        expires_at_provided="expiresAt" in payload,
        identity=identity,
    )
    if result:
        append_audit_log(
            AuditLogRecord(
                action="api_key.update",
                resource_type="api_key",
                resource_id=key_id,
                api_key_id=key_id,
                identity=identity,
                outcome="success",
                risk_level="high" if payload.get("status") == "disabled" else "medium",
                summary=f"Updated API Key {key_id}",
                metadata={"changedFields": sorted(payload.keys())},
            )
        )
    return result


def rotate_console_api_key(key_id: str, identity: IdentityContext | None = None) -> dict | None:
    _assert_api_key_manager(identity)
    result = rotate_api_key(key_id, identity)
    if result:
        append_audit_log(
            AuditLogRecord(
                action="api_key.rotate",
                resource_type="api_key",
                resource_id=key_id,
                api_key_id=key_id,
                identity=identity,
                outcome="success",
                risk_level="high",
                summary=f"Rotated API Key {key_id}",
                metadata={"keyPrefix": result.get("keyPrefix"), "keySuffix": result.get("keySuffix")},
            )
        )
    return result


def delete_console_api_key(key_id: str, identity: IdentityContext | None = None) -> bool:
    _assert_api_key_manager(identity)
    deleted = delete_api_key(key_id, identity)
    if deleted:
        append_audit_log(
            AuditLogRecord(
                action="api_key.delete",
                resource_type="api_key",
                resource_id=key_id,
                api_key_id=key_id,
                identity=identity,
                outcome="success",
                risk_level="high",
                summary=f"Deleted API Key {key_id}",
            )
        )
    return deleted


def _assert_api_key_manager(identity: IdentityContext | None) -> None:
    if identity and identity.enforce_access and not (identity.is_tenant_admin or identity.is_platform_admin):
        raise PermissionError("Only tenant or platform administrators can manage API Keys")


def _ingestion_task_row(task: dict, kb: dict | None = None) -> dict:
    stages = list((task.get("stages") or {}).values())
    stage_payload = []
    total_latency_ms = 0
    for key in ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]:
        stage = (task.get("stages") or {}).get(key, {})
        latency_ms = int(stage.get("latency_ms", 0) or 0)
        total_latency_ms += latency_ms
        stage_payload.append(
            {
                "key": key,
                "label": _stage_label(key),
                "status": stage.get("status", "pending"),
                "progress": int(stage.get("progress", 0) or 0),
                "latencyMs": latency_ms,
                "inputCount": int(stage.get("input_count", 0) or 0),
                "outputCount": int(stage.get("output_count", 0) or 0),
                "reason": stage.get("message", "") or "",
                "metrics": stage.get("metrics", {}) if isinstance(stage.get("metrics"), dict) else {},
            }
        )

    kb_id = str(task.get("kb_id") or "")
    actor_id = str(task.get("actor_id") or task.get("created_by") or "")
    return {
        "id": task.get("id", ""),
        "kbId": kb_id,
        "kbName": (kb or {}).get("name") or kb_id,
        "documentName": task.get("filename", ""),
        "status": task.get("status", "pending"),
        "strategy": task.get("strategy", ""),
        "createdAt": task.get("created_at", ""),
        "updatedAt": task.get("updated_at", ""),
        "actorId": actor_id,
        "actorName": actor_id or "未记录",
        "parseMethod": task.get("parse_provider") or task.get("parse_method") or "mineru",
        "chunkCount": int(task.get("chunk_count", 0) or 0),
        "totalLatencyMs": total_latency_ms,
        "currentStage": task.get("current_stage") or "",
        "error": task.get("error") or "",
        "stages": stage_payload,
        "chunkTimings": task.get("chunk_timings", {}) if isinstance(task.get("chunk_timings"), dict) else {},
    }


def _stage_label(key: str) -> str:
    return {
        "upload": "上传",
        "parse": "解析",
        "clean": "清洗",
        "chunk": "切片",
        "quality": "质检",
        "embedding": "向量化",
        "export": "入库",
    }.get(key, key)
