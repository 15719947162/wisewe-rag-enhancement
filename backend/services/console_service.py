"""
控制台服务模块

本模块负责前端控制台的各类数据查询和操作，主要包括：
1. 系统配置查询与更新
2. 系统告警状态
3. 任务队列状态
4. 统计指标
5. 评估记录查询
6. 查询日志和审计日志
7. 入库任务列表和日志
8. Token 使用统计
9. API Key 管理
10. OpenAPI App 管理

服务层职责：
- 聚合多个数据源的数据
- 转换数据格式为前端展示格式
- 处理敏感配置的脱敏
- 记录审计日志
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from backend.services.evaluation_store import load_evaluations
from backend.services.access_control import filter_tasks_by_identity
from backend.services.ingestion_service import delete_ingestion_task, get_all_tasks, mark_ingestion_task_failed
from backend.services.task_store import get_task_store_diagnostics
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
from core.db.external_system_configs import (
    create_external_system_config,
    delete_external_system_config,
    list_external_system_configs,
    update_external_system_config,
)
from core.db.identity import TENANT_ADMIN_ROLE_CODE, IdentityContext
from core.db.identity import list_identity_sync_runs
from core.db.query_logs import (
    AuditLogRecord,
    append_audit_log,
    fetch_app_usage_report_for_identity,
    fetch_audit_logs,
    fetch_rag_query_logs,
    fetch_token_usage_summary_for_identity,
)
from core.db.query_logs import export_rag_query_logs_csv
from core.db.connection import is_db_available
from core.runtime_settings import (
    RUNTIME_SETTING_SPECS,
    list_runtime_setting_versions,
    resolve_runtime_setting,
    rollback_runtime_settings,
    save_runtime_overrides,
    stringify_runtime_value,
)

EDITABLE_SETTINGS_KEYS = set(RUNTIME_SETTING_SPECS.keys())
SENSITIVE_SETTING_TOKENS = ("api_key", "access_key", "credential", "secret", "password", "token")
MASKED_SETTING_PREFIX = "****"


def _utc_now() -> str:
    """获取当前 UTC 时间（ISO 格式）"""
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
    """
    构建配置项数据结构

    将配置值转换为前端展示格式，包含元信息。

    参数：
        label: 配置项名称
        value: 配置值
        category: 分类
        editable: 是否可编辑
        source: 值来源（env/config/code/db）
        sensitive: 是否敏感（需脱敏）
        has_value: 是否有值

    返回：
        dict: 配置项数据
    """
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
    """
    检查配置项是否为敏感配置

    包含 api_key、access_key、credential、secret、password、token 的配置视为敏感。

    参数：
        key: 配置项名称

    返回：
        bool: 是否敏感
    """
    normalized = key.lower()
    return any(token in normalized for token in SENSITIVE_SETTING_TOKENS)


def _mask_sensitive_value(value: object) -> str:
    """
    脱敏敏感配置值

    只显示最后 4 位，前面用 **** 替换。

    参数：
        value: 原始值

    返回：
        str: 脱敏后的值
    """
    string_value = stringify_runtime_value(value)
    if not string_value:
        return ""
    if len(string_value) <= 4:
        return "****"
    return f"****{string_value[-4:]}"


def _looks_like_masked_sensitive_value(value: str) -> bool:
    """
    检查值是否已被脱敏

    用于更新配置时跳过未修改的敏感值。

    参数：
        value: 值

    返回：
        bool: 是否已被脱敏
    """
    return value.strip().startswith(MASKED_SETTING_PREFIX)


def _setting_effective_mode(key: str) -> str:
    """
    判断配置项的有效模式

    - ops: 运维配置，需要重启服务生效
    - gray: 灰度配置，新请求生效
    - hot: 热配置，立即生效

    参数：
        key: 配置项名称

    返回：
        str: 有效模式
    """
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
    """
    检查配置项是否支持知识库级别覆盖

    只有 hot 和 gray 模式的配置才支持知识库覆盖。

    参数：
        key: 配置项名称
        effective_mode: 有效模式

    返回：
        bool: 是否支持覆盖
    """
    if effective_mode not in {"hot", "gray"}:
        return False
    normalized = key.lower()
    return any(token in normalized for token in ("rag_", "prompt", "chunk", "clean", "quality", "embedding", "rerank"))


def sanitize_console_settings_update(payload: dict) -> dict[str, str]:
    """
    清理并验证配置更新请求

    过滤掉不可编辑的配置和未修改的敏感值。

    参数：
        payload: 原始更新请求

    返回：
        dict[str, str]: 清理后的有效更新
    """
    safe_payload: dict[str, str] = {}
    for key, value in payload.items():
        if key not in EDITABLE_SETTINGS_KEYS or not isinstance(value, str):
            continue
        if _is_sensitive_setting(key) and _looks_like_masked_sensitive_value(value):
            continue
        safe_payload[key] = value
    return safe_payload


def get_settings_payload() -> list[dict]:
    """
    获取系统配置列表

    返回所有可配置项，按分组组织。

    返回：
        list[dict]: 配置分组列表，每个分组包含：
            - id: 分组 ID
            - title: 分组标题
            - description: 分组描述
            - values: 配置项列表

    分组包括：
        - models_common: 通用模型
        - models_embedding: 向量模型
        - models_cleaner: 清洗模型
        - models_chunker: 切片模型
        - models_quality: 质量审核模型
        - models_enhance: 三层增强模型
        - models_rag: 问答生成模型
        - identity_sso: 身份与 SSO
        - parser: 解析配置
        - chunking: 切片参数
        - vector_db: 向量数据库配置
        - storage: 存储配置
        - about: 关于
    """
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
    """
    更新系统配置

    将配置更新保存到数据库覆盖层。

    参数：
        payload: 配置更新字典
        updated_by: 更新来源

    返回：
        dict: 更新结果
    """
    updated = save_runtime_overrides(payload, updated_by=updated_by)
    return {"updated": updated, "count": len(updated)}


def update_console_settings_with_audit(
    payload: dict[str, str],
    *,
    identity: IdentityContext | None = None,
    updated_by: str = "console",
) -> dict[str, object]:
    """
    更新系统配置并记录审计日志

    参数：
        payload: 配置更新字典
        identity: 用户身份上下文
        updated_by: 更新来源

    返回：
        dict: 更新结果
    """
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


def get_console_setting_versions(limit: int = 20) -> list[dict]:
    """
    获取系统设置的版本历史列表

    返回运行时配置的历史版本记录，用于支持配置回滚和审计追踪。
    每个版本记录包含版本 ID、创建时间、创建者、修改的配置项数量等元信息。

    参数：
        limit: 返回版本数量限制，默认为 20 条。用于控制返回数据量，
              避免版本历史过长时的性能问题。

    返回：
        list[dict]: 版本历史列表，每个版本记录包含：
            - version_id: 版本唯一标识
            - created_at: 版本创建时间（ISO 格式）
            - created_by: 创建者标识（console / user_id）
            - key_count: 该版本修改的配置项数量
            - snapshot: 该版本的配置快照（可选）

    使用场景：
        1. 控制台配置历史页面展示，让管理员查看配置变更轨迹
        2. 配置回滚前的版本选择，帮助管理员确认回滚目标
        3. 审计追踪，配合审计日志定位配置变更责任人
    """
    return list_runtime_setting_versions(limit=limit)


def rollback_console_settings_version(
    version_id: str,
    *,
    identity: IdentityContext | None = None,
    updated_by: str = "console",
) -> dict[str, object]:
    """
    回滚系统配置到指定历史版本

    将运行时配置恢复到历史版本状态，用于配置错误恢复或撤销变更。
    执行回滚后会自动记录高风控级别的审计日志。

    参数：
        version_id: 目标版本 ID，从 get_console_setting_versions() 获取。
                   必须是有效的历史版本 ID，否则返回 not_found 状态。
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                  回滚操作通常需要租户管理员或平台管理员权限。
        updated_by: 更新来源标识，默认为 "console"。
                   可用于区分不同来源的回滚操作（如 console / api / scheduled）。

    返回：
        dict: 回滚结果，包含：
            - rolledBack: bool - 是否成功回滚
            - versionId: str - 回滚到的版本 ID
            - updated: list[str] - 被修改的配置项键名列表
            - count: int - 被修改的配置项数量

    使用场景：
        1. 配置变更导致系统异常时的快速恢复
        2. 批量配置修改后需要撤销的场景
        3. 测试环境配置重置为已知良好状态

    注意事项：
        - 回滚是高风险操作，会覆盖当前所有运行时配置
        - 建议在回滚前先创建当前版本的快照
        - 回滚后可能需要重启服务才能生效（取决于配置项的 effective_mode）
    """
    result = rollback_runtime_settings(version_id, updated_by=updated_by)
    append_audit_log(
        AuditLogRecord(
            action="settings.rollback",
            resource_type="settings",
            resource_id=version_id,
            identity=identity,
            outcome="success" if result.get("rolledBack") else "not_found",
            risk_level="high" if result.get("rolledBack") else "medium",
            summary=f"Rolled back runtime settings to version {version_id}",
            metadata={
                "versionId": version_id,
                "rolledBack": bool(result.get("rolledBack")),
                "updatedKeys": list(result.get("updated") or []),
                "updatedCount": int(result.get("count") or 0),
            },
        )
    )
    return result


def get_console_alerts() -> list[dict]:
    """
    获取系统告警列表

    检查系统关键配置是否正常，返回告警信息。

    返回：
        list[dict]: 告警列表，每个告警包含：
            - id: 告警 ID
            - title: 标题
            - description: 描述
            - severity: 严重程度（failed/degraded）
            - area: 影响区域

    检查项：
        - PostgreSQL 是否可用
        - 302AI API Key 是否配置
        - MinerU 官方 API Token 是否配置
        - LLM API Key 是否配置
    """
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
    """
    获取任务队列状态

    返回待处理和失败的任务列表。

    返回：
        list[dict]: 任务队列列表，每个任务包含：
            - id: 任务 ID
            - lane: 队列类型（pending/recent/failed）
            - title: 任务标题
            - subtitle: 副标题
            - status: 状态
            - linkedHref: 跳转链接
            - updatedAt: 更新时间
    """
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
    """
    获取控制台统计指标

    返回知识库数量、文档数量、活跃任务数量。

    参数：
        identity: 用户身份上下文

    返回：
        list[dict]: 指标列表
    """
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
    """
    获取评估记录列表

    查询 RAG 评估记录，支持按知识库筛选。

    参数：
        kb_id: 知识库 ID，不传则查询所有
        identity: 用户身份上下文

    返回：
        list[dict]: 评估记录列表
    """
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
    """
    获取 RAG 查询日志

    支持多种筛选条件查询查询日志。

    参数：
        tenant_id: 租户 ID
        kb_id: 知识库 ID
        request_id: 请求 ID
        actor_id: 操作者 ID
        api_key_id: API Key ID
        pipeline_domain: 管道域
        start_at: 开始时间
        end_at: 结束时间
        limit: 返回数量限制
        identity: 用户身份上下文

    返回：
        list[dict]: 查询日志列表
    """
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
    """
    获取审计日志

    支持多种筛选条件查询审计日志。

    参数：
        tenant_id: 租户 ID
        actor_id: 操作者 ID
        action: 操作类型
        resource_type: 资源类型
        resource_id: 资源 ID
        request_id: 请求 ID
        kb_id: 知识库 ID
        outcome: 结果（success/failure）
        start_at: 开始时间
        end_at: 结束时间
        limit: 返回数量限制
        identity: 用户身份上下文

    返回：
        list[dict]: 审计日志列表
    """
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
    task_id: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    page: int = 1,
    page_size: int = 20,
    identity: IdentityContext | None = None,
) -> dict:
    kb_items = get_knowledge_bases_payload(identity)
    kb_by_id = {str(item["id"]): item for item in kb_items}
    normalized_keyword = (keyword or "").strip().lower()
    normalized_task_id = (task_id or "").strip().lower()
    tasks = [
        _ingestion_task_row(task, kb_by_id.get(str(task.get("kb_id") or "")))
        for task in filter_tasks_by_identity(get_all_tasks(), identity or IdentityContext())
    ]

    if normalized_task_id:
        tasks = [task for task in tasks if normalized_task_id in str(task["id"]).lower()]
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
                    task["id"],
                    task["kbName"],
                    task["kbId"],
                    task["documentName"],
                    task.get("sourceType", ""),
                    task.get("sourceSummary", ""),
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


TASK_QUEUE_ACTIVE_STATUSES = {"pending", "running", "awaiting_confirmation"}
TASK_QUEUE_STALE_SECONDS = 2 * 60 * 60


def get_console_task_queue(identity: IdentityContext | None = None) -> dict:
    """
    获取任务队列的完整状态视图

    返回所有入库任务的队列状态，包括任务统计摘要、健康状态判断和详细任务列表。
    用于任务队列治理和监控场景。

    参数：
        identity: 用户身份上下文，用于权限过滤。非管理员只能看到
                 自己有权限访问的知识库关联的任务。

    返回：
        dict: 任务队列状态，包含：
            - store: dict - 任务存储诊断信息
                - type: 存储类型（memory / postgres）
                - total: 总任务数
                - path: 存储路径（仅文件存储）
            - summary: dict - 任务统计摘要
                - total: 总任务数
                - active: 活跃任务数（pending + running + awaiting_confirmation）
                - pending: 待处理任务数
                - running: 运行中任务数
                - awaitingConfirmation: 等待确认任务数
                - failed: 失败任务数
                - success: 成功任务数
                - stale: 僵尸任务数（长时间未更新的活跃任务）
            - staleThresholdSeconds: int - 判定僵尸任务的时间阈值（秒）
            - items: list[dict] - 任务详细列表，每个任务包含：
                - id / taskId: 任务 ID
                - kbId / kbName: 知识库信息
                - documentName: 文档名称
                - status: 任务状态
                - health: 健康状态（normal / stale / waiting / failed / done）
                - riskReason: 风险原因说明
                - ageSeconds: 任务创建至今的秒数
                - idleSeconds: 任务最后更新至今的秒数
                - canMarkFailed: 是否可以标记为失败
                - canDelete: 是否可以删除
                - canForceDelete: 是否可以强制删除

    使用场景：
        1. 任务队列治理页面，展示所有任务状态和健康度
        2. 运维监控告警，识别僵尸任务和异常任务
        3. 任务清理决策，基于健康状态和空闲时间判断清理策略
        4. 容量规划，统计各状态任务数量趋势

    注意事项：
        - stale 状态判定基于 TASK_QUEUE_STALE_SECONDS 常量（默认 2 小时）
        - awaiting_confirmation 状态的任务需要人工审核切片草稿
        - force delete 会中断正在运行的任务，应谨慎使用
    """
    kb_items = get_knowledge_bases_payload(identity)
    kb_by_id = {str(item["id"]): item for item in kb_items}
    raw_tasks = filter_tasks_by_identity(get_all_tasks(), identity or IdentityContext())
    rows = [_task_queue_row(task, kb_by_id.get(str(task.get("kb_id") or ""))) for task in raw_tasks]

    summary = {
        "total": len(rows),
        "active": len([row for row in rows if row["status"] in TASK_QUEUE_ACTIVE_STATUSES]),
        "pending": len([row for row in rows if row["status"] == "pending"]),
        "running": len([row for row in rows if row["status"] == "running"]),
        "awaitingConfirmation": len([row for row in rows if row["status"] == "awaiting_confirmation"]),
        "failed": len([row for row in rows if row["status"] == "failed"]),
        "success": len([row for row in rows if row["status"] == "success"]),
        "stale": len([row for row in rows if row["health"] == "stale"]),
    }
    return {
        "store": get_task_store_diagnostics(),
        "summary": summary,
        "staleThresholdSeconds": TASK_QUEUE_STALE_SECONDS,
        "items": rows,
    }


def mark_console_task_failed(task_id: str, *, reason: str = "", identity: IdentityContext | None = None) -> dict:
    """
    将活跃任务标记为失败状态

    用于任务队列治理场景，管理员可以手动将长时间未响应或异常的任务
    标记为失败，避免任务卡在 pending/running 状态阻塞队列。
    操作会记录高风控级别的审计日志。

    参数：
        task_id: 任务 ID，必须是有效的入库任务 ID。
                仅允许标记处于 pending、running 或 awaiting_confirmation 状态的任务。
        reason: 标记失败的原因说明，用于记录到任务 error 字段和审计日志。
               如果不提供，默认使用 "管理员在任务队列治理中标记为失败"。
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要租户管理员或平台管理员权限。

    返回：
        dict: 更新后的任务队列行数据，包含：
            - id: 任务 ID
            - status: "failed"（已更新）
            - health: "failed"
            - error: 失败原因（reason 或默认消息）
            - riskReason: "任务失败"
            - 其他任务队列行字段（见 get_console_task_queue）

    异常：
        ValueError: 任务不存在时抛出

    使用场景：
        1. 僵尸任务治理：长时间未更新的 pending/running 任务，
           确认 worker 已停止后手动标记失败释放队列资源
        2. 异常任务处理：解析服务异常导致任务无法继续时，
           管理员手动标记失败避免无限等待
        3. 测试调试：测试环境清理异常任务

    注意事项：
        - 标记失败是高风控操作，应确认任务确实无法继续执行
        - 标记失败后任务状态不可逆，只能删除或重新入库
        - 操作会触发审计日志记录，便于事后追溯
    """
    task = mark_ingestion_task_failed(
        task_id,
        reason=reason or "管理员在任务队列治理中标记为失败",
        failed_by=_actor_id(identity),
    )
    if task is None:
        raise ValueError(f"Task '{task_id}' not found")
    append_audit_log(
        AuditLogRecord(
            action="task_queue.mark_failed",
            resource_type="ingestion_task",
            resource_id=task_id,
            kb_id=str(task.get("kb_id") or "") or None,
            identity=identity,
            outcome="success",
            risk_level="high",
            summary=f"Marked ingestion task {task_id} as failed",
            metadata={"reason": reason or "", "status": task.get("status"), "currentStage": task.get("current_stage")},
        )
    )
    return _task_queue_row(task)


def delete_console_task(task_id: str, *, force: bool = False, identity: IdentityContext | None = None) -> dict:
    """
    删除入库任务记录及相关资源

    从任务存储中删除任务记录，可选是否强制删除正在运行的任务。
    操作会根据是否强制删除记录相应风控级别的审计日志。

    参数：
        task_id: 任务 ID，必须是有效的入库任务 ID。
        force: 是否强制删除。默认为 False。
               - False: 仅允许删除已完成（success/failed）或等待确认的任务
               - True: 允许删除正在运行（pending/running）的任务，
                       会中断正在执行的 worker 进程
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要租户管理员或平台管理员权限。

    返回：
        dict: 删除结果，包含：
            - removed: dict - 被删除的资源清单
                - taskId: 任务 ID
                - task: 任务记录
                - logs: 日志文件路径（如存在）
                - chunks: 切片数据（如已生成且配置清理）
            - 其他 delete_ingestion_task 返回的字段

    异常：
        ValueError: 任务不存在时抛出
        RuntimeError: force=False 且任务状态不允许删除时抛出（由底层函数抛出）

    使用场景：
        1. 任务清理：删除已完成的历史任务，释放存储空间
        2. 异常任务移除：删除失败的任务记录，清理错误状态
        3. 强制中断：force=True 时中断正在运行的异常任务
        4. 测试环境清理：批量删除测试任务

    注意事项：
        - force=True 是高风险操作，可能中断正在执行的 worker
        - 删除操作不可逆，任务数据将永久丢失
        - 建议在删除前确认任务状态和重要性
        - 操作会触发审计日志，force=True 时风险级别为 high
    """
    result = delete_ingestion_task(task_id, force=force)
    if result is None:
        raise ValueError(f"Task '{task_id}' not found")
    append_audit_log(
        AuditLogRecord(
            action="task_queue.delete",
            resource_type="ingestion_task",
            resource_id=task_id,
            identity=identity,
            outcome="success",
            risk_level="high" if force else "medium",
            summary=f"Deleted ingestion task {task_id}",
            metadata={"force": force, "removed": result.get("removed", {})},
        )
    )
    return result


def cleanup_console_tasks(
    *,
    statuses: list[str],
    older_than_seconds: int,
    include_stale_active: bool = False,
    identity: IdentityContext | None = None,
) -> dict:
    """
    批量清理符合条件的入库任务

    根据任务状态和创建时间批量删除任务记录，用于任务队列的定期清理和治理。
    支持清理长时间未更新的活跃任务（僵尸任务）。

    参数：
        statuses: 需要清理的任务状态列表。支持的状态包括：
                 - "success": 成功任务
                 - "failed": 失败任务
                 - "degraded": 降级任务
                 - "empty": 空结果任务
                 - "pending": 待处理任务（需配合 include_stale_active）
                 - "running": 运行中任务（需配合 include_stale_active）
                 - "awaiting_confirmation": 等待确认任务
                 至少需要提供一个有效状态。
        older_than_seconds: 任务年龄阈值（秒）。仅删除创建时间早于此阈值的任务。
                           例如：7200 表示只删除创建时间超过 2 小时的任务。
                           设置为 0 表示不限制年龄。
        include_stale_active: 是否包含长时间未更新的活跃任务（僵尸任务）。
                            - False: 即使指定了 pending/running 状态也不会删除
                            - True: 会使用 force=True 删除符合条件的 pending/running 任务
                            建议与 large older_than_seconds 配合使用，避免误删新任务。
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要租户管理员或平台管理员权限。

    返回：
        dict: 清理结果，包含：
            - deleted: list[str] - 成功删除的任务 ID 列表
            - deletedCount: int - 成功删除的任务数量
            - skipped: list[dict] - 跳过的任务列表，每项包含：
                - taskId: 任务 ID
                - reason: 跳过原因（通常是删除失败的具体错误）
            - skippedCount: int - 跳过的任务数量

    异常：
        ValueError: statuses 为空或不包含有效状态时抛出

    使用场景：
        1. 定期清理：每日定时清理超过 7 天的成功/失败任务
           cleanup_console_tasks(statuses=["success", "failed"], older_than_seconds=7*24*3600)
        2. 僵尸任务治理：清理超过 4 小时未更新的活跃任务
           cleanup_console_tasks(statuses=["pending", "running"], older_than_seconds=4*3600, include_stale_active=True)
        3. 等待确认任务清理：清理长期未处理的草稿确认
           cleanup_console_tasks(statuses=["awaiting_confirmation"], older_than_seconds=24*3600)

    注意事项：
        - include_stale_active=True 是高风险操作，可能中断正在运行的正常任务
        - 建议在业务低峰期执行批量清理
        - 清理操作不可逆，任务数据将永久丢失
        - 操作会触发审计日志，风险级别根据 include_stale_active 决定
    """
    allowed_statuses = {"success", "failed", "degraded", "empty", "pending", "running", "awaiting_confirmation"}
    normalized_statuses = [status for status in statuses if status in allowed_statuses]
    if not normalized_statuses:
        raise ValueError("At least one valid status is required")

    cutoff_age = max(0, int(older_than_seconds or 0))
    deleted: list[str] = []
    skipped: list[dict] = []
    now = datetime.now(timezone.utc)
    for task in get_all_tasks():
        task_id = str(task.get("id") or "")
        status = str(task.get("status") or "pending")
        if status not in normalized_statuses:
            continue
        age_seconds = _seconds_since(task.get("updated_at") or task.get("created_at"), now)
        if age_seconds < cutoff_age:
            continue
        force = bool(include_stale_active and status in TASK_QUEUE_ACTIVE_STATUSES)
        try:
            result = delete_ingestion_task(task_id, force=force)
            if result:
                deleted.append(task_id)
        except RuntimeError as exc:
            skipped.append({"taskId": task_id, "reason": str(exc)})

    append_audit_log(
        AuditLogRecord(
            action="task_queue.cleanup",
            resource_type="ingestion_task",
            identity=identity,
            outcome="success",
            risk_level="high" if include_stale_active else "medium",
            summary=f"Cleaned up {len(deleted)} ingestion tasks",
            metadata={
                "statuses": normalized_statuses,
                "olderThanSeconds": cutoff_age,
                "includeStaleActive": include_stale_active,
                "deleted": deleted,
                "skipped": skipped,
            },
        )
    )
    return {"deleted": deleted, "deletedCount": len(deleted), "skipped": skipped, "skippedCount": len(skipped)}


def _task_queue_row(task: dict, kb: dict | None = None) -> dict:
    """构建任务队列行"""
    row = _ingestion_task_row(task, kb)
    now = datetime.now(timezone.utc)
    age_seconds = _seconds_since(row.get("createdAt"), now)
    idle_seconds = _seconds_since(row.get("updatedAt") or row.get("createdAt"), now)
    status = str(row.get("status") or "pending")
    health = "normal"
    risk_reason = ""
    if status in {"pending", "running"} and idle_seconds >= TASK_QUEUE_STALE_SECONDS:
        health = "stale"
        risk_reason = "任务长时间未更新，可能需要人工确认 worker 状态"
    elif status == "awaiting_confirmation":
        health = "waiting"
        risk_reason = "等待人工确认切片草稿"
    elif status == "failed":
        health = "failed"
        risk_reason = row.get("error") or "任务失败"
    elif status == "success":
        health = "done"
    return {
        **row,
        "ageSeconds": age_seconds,
        "idleSeconds": idle_seconds,
        "health": health,
        "riskReason": risk_reason,
        "canMarkFailed": status in TASK_QUEUE_ACTIVE_STATUSES,
        "canDelete": status not in {"pending", "running"} or bool(task.get("done")),
        "canForceDelete": status in {"pending", "running"} and not bool(task.get("done")),
    }


def _seconds_since(value: object, now: datetime | None = None) -> int:
    """计算时间差（秒）"""
    if not value:
        return 0
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - parsed).total_seconds()))


def _actor_id(identity: IdentityContext | None) -> str:
    """获取操作者 ID"""
    if identity and identity.enforce_access:
        return str(identity.user_id or "console")
    return "console"


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


def get_console_app_usage(
    *,
    limit: int = 20,
    identity: IdentityContext | None = None,
    app_id: str | None = None,
    api_key_id: str | None = None,
    kb_id: str | None = None,
    pipeline_domain: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict:
    """
    获取 OpenAPI 应用的使用统计报告

    返回指定应用或 API Key 的调用统计，用于用量监控、配额管理和计费分析。
    支持按应用、API Key、知识库、时间范围等多维度筛选。

    参数：
        limit: 返回结果数量限制，默认为 20。用于控制返回数据量。
        identity: 用户身份上下文，用于权限校验和数据范围过滤。
                 需要租户管理员或平台管理员权限。
                 非管理员只能查看自己租户的数据，平台管理员可查看所有租户。
        app_id: OpenAPI 应用 ID。指定后仅返回该应用的统计数据。
        api_key_id: API Key ID。指定后仅返回该 API Key 的统计数据。
        kb_id: 知识库 ID。指定后仅返回该知识库相关的调用统计。
        pipeline_domain: 管道域。用于区分不同业务场景的调用，如：
                        - "rag": RAG 问答
                        - "embedding": 向量化服务
                        - "chunking": 切片服务
        start_at: 统计开始时间（ISO 格式 datetime）。用于时间范围筛选。
        end_at: 统计结束时间（ISO 格式 datetime）。用于时间范围筛选。

    返回：
        dict: 应用使用统计，包含：
            - items: list[dict] - 使用统计列表，每项包含：
                - appId: 应用 ID
                - appName: 应用名称
                - apiKeyId: API Key ID
                - apiKeyName: API Key 名称
                - kbId: 知识库 ID
                - kbName: 知识库名称
                - requestCount: 请求总数
                - tokenCount: Token 消耗总数
                - avgLatencyMs: 平均延迟（毫秒）
                - errorCount: 错误请求数
                - successRate: 成功率
                - firstRequestAt: 首次请求时间
                - lastRequestAt: 最后请求时间
            - summary: dict - 汇总统计
                - totalRequests: 总请求数
                - totalTokens: 总 Token 数
                - avgSuccessRate: 平均成功率
            - filters: dict - 当前应用的筛选条件快照

    使用场景：
        1. 用量监控：查看各应用的 API 调用量和 Token 消耗趋势
        2. 配额管理：判断是否接近 API Key 或应用的配额限制
        3. 计费分析：按应用或租户统计 Token 消耗，支持成本分摊
        4. 性能诊断：识别高延迟或高错误率的应用
        5. 安全审计：监控异常调用模式，如短时间内大量请求

    注意事项：
        - 需要 API Key 管理权限才能访问
        - 时间范围查询建议不超过 30 天，避免性能问题
        - 统计数据基于 query_logs 表聚合，可能有几分钟延迟
    """
    _assert_api_key_manager(identity)
    return fetch_app_usage_report_for_identity(
        limit=limit,
        tenant_id=identity.tenant_id if identity and identity.enforce_access else None,
        include_all_tenants=bool(identity and identity.is_platform_admin),
        app_id=app_id,
        api_key_id=api_key_id,
        kb_id=kb_id,
        pipeline_domain=pipeline_domain,
        start_at=start_at,
        end_at=end_at,
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


def get_console_external_system_configs(identity: IdentityContext | None = None) -> list[dict]:
    """
    获取外部系统配置列表

    返回所有外部系统配置记录，主要用于 SSO 集成和身份认证场景。
    外部系统配置用于对接 AI 基座或其他统一身份认证平台。

    参数：
        identity: 用户身份上下文，用于权限校验和数据范围过滤。
                 需要平台管理员或租户管理员权限。
                 非管理员无法访问此接口。

    返回：
        list[dict]: 外部系统配置列表，每个配置包含：
            - id: 配置 ID
            - ssoBaseUrl: SSO 服务基础 URL
            - ssoClientId: SSO 客户端 ID
            - ssoClientSecret: SSO 客户端密钥（已脱敏，显示 ****xxxx）
            - ssoRedirectUri: SSO 回调地址
            - ssoLaunchBaseUrl: SSO 启动基础 URL
            - ssoLaunchPath: SSO 启动路径
            - ssoExchangePath: Token 交换路径
            - ssoUserSnapshotPathTemplate: 用户快照路径模板
            - ssoDeltaPath: 增量同步路径
            - status: 配置状态（active / disabled）
            - createdAt: 创建时间
            - updatedAt: 更新时间
            - createdBy: 创建者

    使用场景：
        1. SSO 配置管理：查看和配置 AI 基座 SSO 接入参数
        2. 多租户配置：为不同租户配置不同的 SSO 接入点
        3. 身份同步：配置用户快照和增量同步接口路径
        4. 配置审计：查看当前生效的外部系统配置

    注意事项：
        - ssoClientSecret 是敏感字段，返回时已脱敏
        - 只有平台管理员或租户管理员可以访问
        - 通常一个租户只有一个活跃的外部系统配置
        - 配置变更后可能需要重启服务或清理缓存才能生效
    """
    _assert_external_system_config_manager(identity)
    return list_external_system_configs(identity)


def create_console_external_system_config(payload: dict, identity: IdentityContext | None = None) -> dict:
    """
    创建外部系统配置

    创建新的外部系统配置记录，用于 SSO 集成和身份认证对接。
    配置创建后会自动记录高风控级别的审计日志。

    参数：
        payload: 配置数据字典，包含：
            - ssoBaseUrl: str（必填）- SSO 服务基础 URL，如 "https://sso.example.com"
            - ssoClientId: str（必填）- SSO 客户端 ID，从 SSO 平台获取
            - ssoClientSecret: str（必填）- SSO 客户端密钥，从 SSO 平台获取
            - ssoRedirectUri: str（必填）- SSO 回调地址，需要与 SSO 平台注册的一致
            - ssoLaunchBaseUrl: str（可选）- SSO 启动基础 URL，用于生成跳转链接
            - ssoLaunchPath: str（必填，默认 "/sso"）- SSO 启动路径
            - ssoExchangePath: str（必填，默认 "/ai/system/internal/sso/exchange"）-
                              Token 交换接口路径
            - ssoUserSnapshotPathTemplate: str（必填，默认
                "/ai/system/internal/identity/snapshot/users/{userId}"）-
                用户快照接口路径模板，支持 {userId} 占位符
            - ssoDeltaPath: str（必填，默认
                "/ai/system/internal/identity/snapshot/delta"）-
                增量同步接口路径
            - status: str（可选，默认 "active"）- 配置状态，可选 active / disabled
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要平台管理员或租户管理员权限。

    返回：
        dict: 创建的配置记录，包含配置 ID 和所有字段（secret 已脱敏）。

    异常：
        ValueError: 必填字段缺失或格式错误时抛出
        PermissionError: 权限不足时抛出

    使用场景：
        1. 首次 SSO 接入：创建 AI 基座 SSO 配置，启用单点登录
        2. 多环境配置：为测试环境和生产环境创建不同的 SSO 配置
        3. 租户隔离：为不同租户配置独立的 SSO 接入点

    注意事项：
        - 创建前需确认 SSO 平台已注册相应的 Client ID
        - ssoRedirectUri 必须与 SSO 平台注册的回调地址完全一致
        - 密钥在数据库中加密存储，返回时已脱敏
        - 创建操作会触发审计日志记录
    """
    _assert_external_system_config_manager(identity)
    result = create_external_system_config(
        sso_base_url=_required_external_config_text(payload.get("ssoBaseUrl"), "ssoBaseUrl"),
        sso_client_id=_required_external_config_text(payload.get("ssoClientId"), "ssoClientId"),
        sso_client_secret=_required_external_config_text(payload.get("ssoClientSecret"), "ssoClientSecret"),
        sso_redirect_uri=_required_external_config_text(payload.get("ssoRedirectUri"), "ssoRedirectUri"),
        sso_launch_base_url=str(payload.get("ssoLaunchBaseUrl") or "").strip(),
        sso_launch_path=_required_external_config_text(payload.get("ssoLaunchPath") or "/sso", "ssoLaunchPath"),
        sso_exchange_path=_required_external_config_text(
            payload.get("ssoExchangePath") or "/ai/system/internal/sso/exchange",
            "ssoExchangePath",
        ),
        sso_user_snapshot_path_template=_required_external_config_text(
            payload.get("ssoUserSnapshotPathTemplate") or "/ai/system/internal/identity/snapshot/users/{userId}",
            "ssoUserSnapshotPathTemplate",
        ),
        sso_delta_path=_required_external_config_text(
            payload.get("ssoDeltaPath") or "/ai/system/internal/identity/snapshot/delta",
            "ssoDeltaPath",
        ),
        status=str(payload.get("status") or "active"),
        identity=identity,
    )
    append_audit_log(
        AuditLogRecord(
            action="external_system_config.create",
            resource_type="external_system_config",
            resource_id=result.get("id"),
            identity=identity,
            outcome="success",
            risk_level="high",
            summary=f"Created external system config {result.get('id')}",
            metadata={
                "ssoBaseUrl": result.get("ssoBaseUrl"),
                "ssoClientId": result.get("ssoClientId"),
                "ssoRedirectUri": result.get("ssoRedirectUri"),
                "ssoLaunchBaseUrl": result.get("ssoLaunchBaseUrl"),
                "ssoLaunchPath": result.get("ssoLaunchPath"),
                "ssoExchangePath": result.get("ssoExchangePath"),
                "ssoUserSnapshotPathTemplate": result.get("ssoUserSnapshotPathTemplate"),
                "ssoDeltaPath": result.get("ssoDeltaPath"),
                "status": result.get("status"),
                "secretProvided": bool(payload.get("ssoClientSecret")),
            },
        )
    )
    return result


def update_console_external_system_config(config_id: str, payload: dict, identity: IdentityContext | None = None) -> dict | None:
    """
    更新外部系统配置

    更新指定的外部系统配置记录，支持部分字段更新。
    配置更新后会自动记录相应风控级别的审计日志。

    参数：
        config_id: 配置 ID，必须是有效的外部系统配置 ID。
        payload: 更新数据字典，支持的字段（均为可选）：
            - ssoBaseUrl: str - SSO 服务基础 URL
            - ssoClientId: str - SSO 客户端 ID
            - ssoClientSecret: str - SSO 客户端密钥（暂不支持通过此接口更新）
            - ssoRedirectUri: str - SSO 回调地址
            - ssoLaunchBaseUrl: str - SSO 启动基础 URL
            - ssoLaunchPath: str - SSO 启动路径
            - ssoExchangePath: str - Token 交换接口路径
            - ssoUserSnapshotPathTemplate: str - 用户快照接口路径模板
            - ssoDeltaPath: str - 增量同步接口路径
            - status: str - 配置状态（active / disabled）
            注意：payload 中不包含的字段不会被更新。
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要平台管理员或租户管理员权限。

    返回：
        dict | None: 更新后的配置记录（secret 已脱敏），如果配置不存在返回 None。

    异常：
        ValueError: 字段值格式错误时抛出（如必填字段传空值）
        PermissionError: 权限不足时抛出

    使用场景：
        1. SSO 配置调整：更新 SSO 服务地址或回调地址
        2. 配置禁用：将 status 设为 disabled，临时禁用 SSO 接入
        3. 路径定制：调整身份同步接口的路径模板
        4. 环境迁移：切换到新的 SSO 服务实例

    注意事项：
        - ssoClientSecret 暂不支持通过此接口更新，需使用专门的密钥轮转接口
        - 更新 ssoRedirectUri 后需同步更新 SSO 平台的注册信息
        - 禁用配置（status=disabled）会中断 SSO 登录，应提前通知用户
        - 更新操作会触发审计日志，禁用配置时风险级别为 high
    """
    _assert_external_system_config_manager(identity)
    sso_base_url = (
        _required_external_config_text(payload.get("ssoBaseUrl"), "ssoBaseUrl")
        if "ssoBaseUrl" in payload
        else None
    )
    sso_client_id = (
        _required_external_config_text(payload.get("ssoClientId"), "ssoClientId")
        if "ssoClientId" in payload
        else None
    )
    sso_redirect_uri = (
        _required_external_config_text(payload.get("ssoRedirectUri"), "ssoRedirectUri")
        if "ssoRedirectUri" in payload
        else None
    )
    sso_launch_base_url = str(payload.get("ssoLaunchBaseUrl") or "").strip() if "ssoLaunchBaseUrl" in payload else None
    sso_launch_path = (
        _required_external_config_text(payload.get("ssoLaunchPath"), "ssoLaunchPath")
        if "ssoLaunchPath" in payload
        else None
    )
    sso_exchange_path = (
        _required_external_config_text(payload.get("ssoExchangePath"), "ssoExchangePath")
        if "ssoExchangePath" in payload
        else None
    )
    sso_user_snapshot_path_template = (
        _required_external_config_text(payload.get("ssoUserSnapshotPathTemplate"), "ssoUserSnapshotPathTemplate")
        if "ssoUserSnapshotPathTemplate" in payload
        else None
    )
    sso_delta_path = (
        _required_external_config_text(payload.get("ssoDeltaPath"), "ssoDeltaPath")
        if "ssoDeltaPath" in payload
        else None
    )
    status = str(payload.get("status")) if "status" in payload else None

    result = update_external_system_config(
        config_id,
        sso_base_url=sso_base_url,
        sso_client_id=sso_client_id,
        sso_redirect_uri=sso_redirect_uri,
        sso_launch_base_url=sso_launch_base_url,
        sso_launch_path=sso_launch_path,
        sso_exchange_path=sso_exchange_path,
        sso_user_snapshot_path_template=sso_user_snapshot_path_template,
        sso_delta_path=sso_delta_path,
        status=status,
        identity=identity,
    )
    if result:
        changed_fields = [field for field in payload.keys() if field != "ssoClientSecret"]
        append_audit_log(
            AuditLogRecord(
                action="external_system_config.update",
                resource_type="external_system_config",
                resource_id=config_id,
                identity=identity,
                outcome="success",
                risk_level="high" if payload.get("status") == "disabled" else "medium",
                summary=f"Updated external system config {config_id}",
                metadata={
                    "changedFields": sorted(changed_fields),
                    "secretUpdated": False,
                },
            )
        )
    return result


def delete_console_external_system_config(config_id: str, identity: IdentityContext | None = None) -> bool:
    """
    删除外部系统配置

    删除指定的外部系统配置记录。删除后配置将永久不可恢复。
    操作会自动记录高风控级别的审计日志。

    参数：
        config_id: 配置 ID，必须是有效的外部系统配置 ID。
        identity: 用户身份上下文，用于权限校验和审计日志记录。
                 需要平台管理员或租户管理员权限。

    返回：
        bool: 是否成功删除。True 表示已删除，False 表示配置不存在。

    异常：
        PermissionError: 权限不足时抛出

    使用场景：
        1. 配置清理：删除测试环境的无效配置
        2. SSO 服务更换：删除旧 SSO 平台的配置，创建新配置
        3. 租户注销：删除租户的 SSO 配置，停止身份认证服务

    注意事项：
        - 删除操作不可逆，配置数据将永久丢失
        - 删除活跃配置（status=active）会导致 SSO 登录失败
        - 建议删除前先将配置设为 disabled，观察一段时间确认无影响后再删除
        - 删除操作会触发高风控级别的审计日志
        - 如果该配置正在被使用（有活跃的 SSO session），删除可能导致用户登录异常
    """
    _assert_external_system_config_manager(identity)
    deleted = delete_external_system_config(config_id, identity)
    if deleted:
        append_audit_log(
            AuditLogRecord(
                action="external_system_config.delete",
                resource_type="external_system_config",
                resource_id=config_id,
                identity=identity,
                outcome="success",
                risk_level="high",
                summary=f"Deleted external system config {config_id}",
            )
        )
    return deleted


def _required_external_config_text(value: object, field_name: str) -> str:
    """验证必填文本字段"""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _assert_external_system_config_manager(identity: IdentityContext | None) -> None:
    """验证外部系统配置管理权限"""
    if identity and identity.enforce_access:
        if identity.is_platform_admin or TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}:
            return
        raise PermissionError("Only super administrators can manage external system configurations")
    raise PermissionError("Only super administrators can manage external system configurations")


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
        "taskId": task.get("id", ""),
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
        "sourceType": task.get("source_type", "file"),
        "sourceSummary": task.get("source_summary", task.get("filename", "")),
        "fastImport": bool(task.get("fast_import")),
        "skippedStages": task.get("skipped_stages", []) if isinstance(task.get("skipped_stages"), list) else [],
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
