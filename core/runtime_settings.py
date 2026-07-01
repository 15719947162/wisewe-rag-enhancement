from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from core.db.connection import get_db_connection
from core.db.init_db import ensure_db_schema

SettingCategory = Literal["common", "advanced"]
SettingSource = Literal["env", "config", "code", "system", "db"]
SettingScope = Literal["env", "config"]
SettingValueType = Literal["str", "bool", "int", "float"]


@dataclass(frozen=True)
class RuntimeSettingSpec:
    key: str
    category: SettingCategory
    scope: SettingScope
    value_type: SettingValueType = "str"
    env_name: str | None = None
    config_path: tuple[str, ...] | None = None
    default: Any = ""


_RUNTIME_SETTING_SPECS: tuple[RuntimeSettingSpec, ...] = (
    RuntimeSettingSpec(
        "PDF_PARSER_PROVIDER",
        category="common",
        scope="env",
        env_name="PDF_PARSER_PROVIDER",
        default="mineru",
    ),
    RuntimeSettingSpec("PDF_PARSER_FALLBACKS", category="advanced", scope="env", env_name="PDF_PARSER_FALLBACKS"),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_KEY_RETRIES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_KEY_RETRIES",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS",
        value_type="int",
        default=60,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_ENDPOINT",
        category="common",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_ENDPOINT",
        default="docmind-api.cn-hangzhou.aliyuncs.com",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_TIMEOUT",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_TIMEOUT",
        value_type="int",
        default=1800,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_POLL_INTERVAL",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_POLL_INTERVAL",
        value_type="float",
        default=3,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT",
        category="common",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT",
        default="markdown,visualLayoutInfo",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE",
        default="VLM",
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_LAYOUT_STEP_SIZE",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_LAYOUT_STEP_SIZE",
        value_type="int",
        default=3000,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES",
        value_type="int",
        default=2,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY",
        value_type="float",
        default=2,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS",
        value_type="float",
        default=90,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED",
        category="common",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB",
        value_type="float",
        default=150,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES",
        value_type="int",
        default=50,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD",
        value_type="int",
        default=33,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY",
        value_type="int",
        default=4,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD",
        value_type="int",
        default=20,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES",
        value_type="int",
        default=2,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES",
        value_type="int",
        default=5,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE",
        category="advanced",
        scope="env",
        env_name="ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec("302AI_API_BASE", category="common", scope="env", env_name="302AI_API_BASE"),
    RuntimeSettingSpec("302AI_API_KEY", category="advanced", scope="env", env_name="302AI_API_KEY"),
    RuntimeSettingSpec(
        "AI_BASE_SSO_BASE_URL",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_BASE_URL",
        default="",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_CLIENT_ID",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_CLIENT_ID",
        default="rag-client",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_CLIENT_SECRET",
        category="advanced",
        scope="env",
        env_name="AI_BASE_SSO_CLIENT_SECRET",
        default="",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_REDIRECT_URI",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_REDIRECT_URI",
        default="",
    ),
    RuntimeSettingSpec(
        "KB_CONSOLE_BASE_URL",
        category="common",
        scope="env",
        env_name="KB_CONSOLE_BASE_URL",
        default="",
    ),
    RuntimeSettingSpec(
        "KB_SESSION_TTL_SECONDS",
        category="advanced",
        scope="env",
        env_name="KB_SESSION_TTL_SECONDS",
        value_type="int",
        default=4 * 60 * 60,
    ),
    RuntimeSettingSpec(
        "KB_LEGACY_HEADER_AUTH_ENABLED",
        category="advanced",
        scope="env",
        env_name="KB_LEGACY_HEADER_AUTH_ENABLED",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "AI_BASE_IDENTITY_SYNC_ENABLED",
        category="advanced",
        scope="env",
        env_name="AI_BASE_IDENTITY_SYNC_ENABLED",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS",
        category="advanced",
        scope="env",
        env_name="AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS",
        value_type="int",
        default=300,
    ),
    RuntimeSettingSpec(
        "AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP",
        category="advanced",
        scope="env",
        env_name="AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS",
        category="advanced",
        scope="env",
        env_name="KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS",
        value_type="int",
        default=600,
    ),
    RuntimeSettingSpec(
        "KB_CORS_ALLOW_ORIGINS",
        category="common",
        scope="env",
        env_name="KB_CORS_ALLOW_ORIGINS",
        default="",
    ),
    RuntimeSettingSpec(
        "KB_CORS_ALLOW_HEADERS",
        category="common",
        scope="env",
        env_name="KB_CORS_ALLOW_HEADERS",
        default="",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_LAUNCH_BASE_URL",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_LAUNCH_BASE_URL",
        default="",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_LAUNCH_PATH",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_LAUNCH_PATH",
        default="/sso",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_EXCHANGE_PATH",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_EXCHANGE_PATH",
        default="/ai/system/internal/sso/exchange",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE",
        default="/ai/system/internal/identity/snapshot/users/{userId}",
    ),
    RuntimeSettingSpec(
        "AI_BASE_SSO_DELTA_PATH",
        category="common",
        scope="env",
        env_name="AI_BASE_SSO_DELTA_PATH",
        default="/ai/system/internal/identity/snapshot/delta",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_API_BASE",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_API_BASE",
        default="https://mineru.net",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_API_TOKEN",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_API_TOKEN",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_MODEL_VERSION",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_MODEL_VERSION",
        default="vlm",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_TIMEOUT",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_TIMEOUT",
        value_type="int",
        default=1800,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_POLL_INTERVAL",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_POLL_INTERVAL",
        value_type="float",
        default=3,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_ENABLE_FORMULA",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_ENABLE_FORMULA",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_ENABLE_TABLE",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_ENABLE_TABLE",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_LANGUAGE",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_LANGUAGE",
        default="ch",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_IS_OCR",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_IS_OCR",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_EXTRA_FORMATS",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_EXTRA_FORMATS",
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_NO_CACHE",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_NO_CACHE",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_CACHE_TOLERANCE",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_CACHE_TOLERANCE",
        value_type="int",
        default=900,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SUBMIT_RETRY_ATTEMPTS",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SUBMIT_RETRY_ATTEMPTS",
        value_type="int",
        default=3,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_POLL_RETRY_ATTEMPTS",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_POLL_RETRY_ATTEMPTS",
        value_type="int",
        default=5,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_ENABLED",
        category="common",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_ENABLED",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_MIN_FILE_MB",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_MIN_FILE_MB",
        value_type="float",
        default=180,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_MIN_PAGES",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_MIN_PAGES",
        value_type="int",
        default=201,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD",
        value_type="int",
        default=180,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY",
        value_type="int",
        default=2,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD",
        value_type="float",
        default=180,
    ),
    RuntimeSettingSpec(
        "MINERU_OFFICIAL_SHARDING_TEXT_SAMPLE_PAGES",
        category="advanced",
        scope="env",
        env_name="MINERU_OFFICIAL_SHARDING_TEXT_SAMPLE_PAGES",
        value_type="int",
        default=5,
    ),
    RuntimeSettingSpec("DATABASE_URL", category="advanced", scope="env", env_name="DATABASE_URL"),
    RuntimeSettingSpec("LLM_API_KEY", category="advanced", scope="env", env_name="LLM_API_KEY"),
    RuntimeSettingSpec("LLM_API_KEY_POOL", category="advanced", scope="env", env_name="LLM_API_KEY_POOL"),
    RuntimeSettingSpec("LLM_BASE_URL", category="common", scope="env", env_name="LLM_BASE_URL"),
    RuntimeSettingSpec(
        "LLM_CLEANER_ENABLED",
        category="common",
        scope="env",
        env_name="LLM_CLEANER_ENABLED",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec("LLM_CLEANER_API_KEY", category="advanced", scope="env", env_name="LLM_CLEANER_API_KEY"),
    RuntimeSettingSpec("LLM_CLEANER_BASE_URL", category="advanced", scope="env", env_name="LLM_CLEANER_BASE_URL"),
    RuntimeSettingSpec(
        "LLM_CLEANER_MODEL",
        category="advanced",
        scope="env",
        env_name="LLM_CLEANER_MODEL",
        default="qwen-plus",
    ),
    RuntimeSettingSpec(
        "LLM_CLEANER_SYSTEM_PROMPT",
        category="advanced",
        scope="env",
        env_name="LLM_CLEANER_SYSTEM_PROMPT",
        default="你是教材文档清洗助手。请保留教材正文、标题、图注、表注、公式说明和题干；只移除页眉页脚、噪声字符、重复水印和明显无意义内容。不要改写事实，不要删除图片或表格占位。",
    ),
    RuntimeSettingSpec(
        "LLM_CHUNKER_SYSTEM_PROMPT",
        category="advanced",
        scope="env",
        env_name="LLM_CHUNKER_SYSTEM_PROMPT",
        default="你是教材语义切片助手。请按知识点、步骤、例题、图表说明的完整语义切分文本；每个切片应能独立支撑检索和引用，避免把相邻但无关的知识点混在一起。",
    ),
    RuntimeSettingSpec(
        "LLM_QUALITY_GATE_SYSTEM_PROMPT",
        category="advanced",
        scope="env",
        env_name="LLM_QUALITY_GATE_SYSTEM_PROMPT",
        default="你是教材切片质量审核助手。请判断切片是否包含可用于问答的有效知识；表格、图片、公式、图注和题目即使文字较短也应视为可能有效。只过滤乱码、空内容和明显解析噪声。",
    ),
    RuntimeSettingSpec(
        "LLM_QUALITY_GATE_ENABLED",
        category="common",
        scope="env",
        env_name="LLM_QUALITY_GATE_ENABLED",
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec("LLM_QUALITY_GATE_API_KEY", category="advanced", scope="env", env_name="LLM_QUALITY_GATE_API_KEY"),
    RuntimeSettingSpec("LLM_QUALITY_GATE_BASE_URL", category="advanced", scope="env", env_name="LLM_QUALITY_GATE_BASE_URL"),
    RuntimeSettingSpec(
        "LLM_QUALITY_GATE_MODEL",
        category="advanced",
        scope="env",
        env_name="LLM_QUALITY_GATE_MODEL",
        default="qwen-plus",
    ),
    RuntimeSettingSpec(
        "LLM_QUALITY_GATE_MIN_SCORE",
        category="advanced",
        scope="env",
        env_name="LLM_QUALITY_GATE_MIN_SCORE",
        value_type="int",
        default=3,
    ),
    RuntimeSettingSpec(
        "LLM_ENHANCE_SYSTEM_PROMPT",
        category="advanced",
        scope="env",
        env_name="LLM_ENHANCE_SYSTEM_PROMPT",
        default="你是教材切片增强助手。请基于原始切片生成简洁摘要、可能问题、关键实体和关系三元组。不得补充原文没有的事实；图片和表格要优先说明其编号、主题、字段/元素和可用于回答的问题。",
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_MODEL",
        category="common",
        scope="env",
        env_name="LLM_EMBEDDING_MODEL",
        default="text-embedding-v3",
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_BATCH_SIZE",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_BATCH_SIZE",
        value_type="int",
        default=10,
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_MAX_CONCURRENCY",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_MAX_CONCURRENCY",
        value_type="int",
        default=10,
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_MAX_RETRIES",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_MAX_RETRIES",
        value_type="int",
        default=2,
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_API_KEY_POOL",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_API_KEY_POOL",
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_KEY_RETRIES",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_KEY_RETRIES",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "LLM_EMBEDDING_KEY_COOLDOWN_SECONDS",
        category="advanced",
        scope="env",
        env_name="LLM_EMBEDDING_KEY_COOLDOWN_SECONDS",
        value_type="int",
        default=30,
    ),
    RuntimeSettingSpec(
        "RAG_RETRIEVAL_SNAPSHOT",
        category="advanced",
        scope="env",
        env_name="RAG_RETRIEVAL_SNAPSHOT",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "INGESTION_READY_MODE",
        category="common",
        scope="env",
        env_name="INGESTION_READY_MODE",
        default="full",
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_ENHANCE_MODE",
        category="common",
        scope="env",
        env_name="HIERARCHICAL_ENHANCE_MODE",
        default="parallel_ordered",
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_TEXT_ENHANCE_WORKERS",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_TEXT_ENHANCE_WORKERS",
        value_type="int",
        default=16,
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_TABLE_ENHANCE_WORKERS",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_TABLE_ENHANCE_WORKERS",
        value_type="int",
        default=3,
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_IMAGE_ENHANCE_WORKERS",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_IMAGE_ENHANCE_WORKERS",
        value_type="int",
        default=4,
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_ENHANCE_MAX_CONCURRENCY",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_ENHANCE_MAX_CONCURRENCY",
        value_type="int",
        default=22,
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_REUSE_LLM_CLIENTS",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_REUSE_LLM_CLIENTS",
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec("VL_API_KEY_POOL", category="advanced", scope="env", env_name="VL_API_KEY_POOL"),
    RuntimeSettingSpec(
        "HIERARCHICAL_ENHANCE_KEY_RETRIES",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_ENHANCE_KEY_RETRIES",
        value_type="int",
        default=1,
    ),
    RuntimeSettingSpec(
        "HIERARCHICAL_KEY_COOLDOWN_SECONDS",
        category="advanced",
        scope="env",
        env_name="HIERARCHICAL_KEY_COOLDOWN_SECONDS",
        value_type="int",
        default=30,
    ),
    RuntimeSettingSpec("OSS_ACCESS_KEY_ID", category="advanced", scope="env", env_name="OSS_ACCESS_KEY_ID"),
    RuntimeSettingSpec(
        "OSS_ACCESS_KEY_SECRET",
        category="advanced",
        scope="env",
        env_name="OSS_ACCESS_KEY_SECRET",
    ),
    RuntimeSettingSpec("OSS_BUCKET", category="common", scope="env", env_name="OSS_BUCKET"),
    RuntimeSettingSpec("OSS_ENDPOINT", category="common", scope="env", env_name="OSS_ENDPOINT"),
    RuntimeSettingSpec("PGVECTOR_DB", category="advanced", scope="env", env_name="PGVECTOR_DB", default="rag_db"),
    RuntimeSettingSpec("PGVECTOR_HOST", category="advanced", scope="env", env_name="PGVECTOR_HOST", default="localhost"),
    RuntimeSettingSpec("PGVECTOR_PASSWORD", category="advanced", scope="env", env_name="PGVECTOR_PASSWORD"),
    RuntimeSettingSpec("PGVECTOR_PORT", category="advanced", scope="env", env_name="PGVECTOR_PORT", default="5432"),
    RuntimeSettingSpec("PGVECTOR_USER", category="advanced", scope="env", env_name="PGVECTOR_USER", default="postgres"),
    RuntimeSettingSpec("RAG_LLM_API_KEY", category="advanced", scope="env", env_name="RAG_LLM_API_KEY"),
    RuntimeSettingSpec("RAG_LLM_BASE_URL", category="advanced", scope="env", env_name="RAG_LLM_BASE_URL"),
    RuntimeSettingSpec("RAG_LLM_MODEL", category="common", scope="env", env_name="RAG_LLM_MODEL", default="qwen-max"),
    RuntimeSettingSpec(
        "RAG_SYSTEM_PROMPT",
        category="advanced",
        scope="env",
        env_name="RAG_SYSTEM_PROMPT",
        default="你是严格基于教材知识库的问答助手。只能依据给定上下文回答，并使用 [1][2] 标注引用。若上下文不足以支撑答案，请直接说明“当前资料不足，无法可靠回答”，不要猜测。回答后列出引用文档和位置。",
    ),
    RuntimeSettingSpec(
        "parser.cloud.parse_method",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "parse_method"),
        default="auto",
    ),
    RuntimeSettingSpec(
        "parser.cloud.version",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "version"),
        default="2.5",
    ),
    RuntimeSettingSpec(
        "parser.cloud.timeout",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "timeout"),
        value_type="int",
        default=1800,
    ),
    RuntimeSettingSpec(
        "parser.cloud.poll_interval",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "poll_interval"),
        value_type="float",
        default=3,
    ),
    RuntimeSettingSpec(
        "parser.cloud.enable_formula",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "enable_formula"),
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "parser.cloud.enable_table_html",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "enable_table_html"),
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "parser.cloud.language",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "language"),
        default="ch",
    ),
    RuntimeSettingSpec(
        "parser.cloud.is_ocr",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "is_ocr"),
        value_type="bool",
        default=False,
    ),
    RuntimeSettingSpec(
        "parser.cloud.model_version",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "model_version"),
        default="v2",
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.enabled",
        category="common",
        scope="config",
        config_path=("parser", "cloud", "sharding", "enabled"),
        value_type="bool",
        default=True,
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.min_pages",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "sharding", "min_pages"),
        value_type="int",
        default=120,
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.min_file_mb",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "sharding", "min_file_mb"),
        value_type="float",
        default=80,
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.pages_per_shard",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "sharding", "pages_per_shard"),
        value_type="int",
        default=20,
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.max_concurrency",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "sharding", "max_concurrency"),
        value_type="int",
        default=4,
    ),
    RuntimeSettingSpec(
        "parser.cloud.sharding.text_sample_pages",
        category="advanced",
        scope="config",
        config_path=("parser", "cloud", "sharding", "text_sample_pages"),
        value_type="int",
        default=5,
    ),
)

RUNTIME_SETTING_SPECS: dict[str, RuntimeSettingSpec] = {
    spec.key: spec for spec in _RUNTIME_SETTING_SPECS
}


def _coerce_value(value: Any, value_type: SettingValueType) -> Any:
    if value_type == "bool":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value is None:
        return ""
    return str(value)


def stringify_runtime_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _read_nested(config: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _write_nested(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = config
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


def _load_overrides_from_db() -> dict[str, Any]:
    try:
        conn = get_db_connection()
    except Exception:
        return {}

    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT key, value FROM console_settings")
            rows = cur.fetchall()
    except Exception:
        return {}
    finally:
        conn.close()

    return {
        str(key): value
        for key, value in rows
        if str(key) in RUNTIME_SETTING_SPECS
    }


def load_runtime_overrides() -> dict[str, Any]:
    return _load_overrides_from_db()


def apply_runtime_env_overrides() -> dict[str, Any]:
    overrides = _load_overrides_from_db()
    for key, value in overrides.items():
        spec = RUNTIME_SETTING_SPECS.get(key)
        if spec and spec.scope == "env" and spec.env_name:
            os.environ[spec.env_name] = stringify_runtime_value(value)
    return overrides


def merge_runtime_config_overrides(config: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(config)
    overrides = _load_overrides_from_db()
    for key, value in overrides.items():
        spec = RUNTIME_SETTING_SPECS.get(key)
        if spec and spec.scope == "config" and spec.config_path:
            _write_nested(merged, spec.config_path, _coerce_value(value, spec.value_type))
    return merged


def resolve_runtime_setting(
    key: str,
    *,
    config: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[Any, SettingSource]:
    spec = RUNTIME_SETTING_SPECS[key]
    active_overrides = overrides if overrides is not None else _load_overrides_from_db()

    if key in active_overrides:
        override_value = _coerce_value(active_overrides[key], spec.value_type)
        if spec.value_type == "str" and override_value == "" and spec.default != "":
            return spec.default, "code"
        return override_value, "db"

    if spec.scope == "env" and spec.env_name:
        env_value = os.environ.get(spec.env_name)
        if env_value is not None and env_value != "":
            return _coerce_value(env_value, spec.value_type), "env"
        return spec.default, "code"

    if spec.scope == "config" and spec.config_path:
        base_config = config or {}
        value = _read_nested(base_config, spec.config_path, spec.default)
        return _coerce_value(value, spec.value_type), "config"

    return spec.default, "code"


def save_runtime_overrides(payload: dict[str, str], updated_by: str = "console") -> list[str]:
    if not payload:
        return []

    try:
        conn = get_db_connection()
    except Exception as exc:
        raise RuntimeError("Database unavailable while saving console settings") from exc

    updated: list[str] = []
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            for key, raw_value in payload.items():
                spec = RUNTIME_SETTING_SPECS.get(key)
                if spec is None or not isinstance(raw_value, str):
                    continue

                if spec.scope == "config" and raw_value.strip() == "":
                    cur.execute("DELETE FROM console_settings WHERE key = %s", (key,))
                    updated.append(key)
                    continue

                value = _coerce_value(raw_value, spec.value_type)
                cur.execute(
                    """
                    INSERT INTO console_settings(key, value, updated_by)
                    VALUES(%s, %s::jsonb, %s)
                    ON CONFLICT(key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    """,
                    (key, json.dumps(value, ensure_ascii=False), updated_by),
                )
                if spec.scope == "env" and spec.env_name:
                    os.environ[spec.env_name] = stringify_runtime_value(value)
                updated.append(key)
        conn.commit()
    except Exception as exc:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise RuntimeError("Failed to persist console settings") from exc
    finally:
        conn.close()

    return updated
