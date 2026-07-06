"""
运行时设置管理模块
==================

本模块实现了集中式的运行时配置管理，支持从多个来源（环境变量、配置文件、数据库）读取设置，
并提供优先级机制和动态覆盖能力。

核心概念
--------
1. **设置规格 (RuntimeSettingSpec)**：每个设置项的元数据定义，包括：
   - key: 设置的唯一标识符
   - category: 分类（common/advanced），用于UI分组展示
   - scope: 作用域（env/config），决定设置存储位置
   - value_type: 值类型（str/bool/int/float）
   - env_name: 对应的环境变量名
   - config_path: 在配置文件中的路径（嵌套路径用元组表示）
   - default: 默认值

2. **设置来源优先级**（从高到低）：
   - db: 数据库中的控制台设置（最高优先级）
   - env: 环境变量
   - config: 配置文件（config.yaml）
   - code: 代码中定义的默认值（最低优先级）

3. **作用域区分**：
   - env 作用域：设置存储在环境变量中，进程启动时生效
   - config 作用域：设置存储在配置文件中，需要重新加载配置

使用示例
--------
>>> from core.runtime_settings import resolve_runtime_setting, save_runtime_overrides
>>>
>>> # 读取单个设置（带来源追踪）
>>> value, source = resolve_runtime_setting("LLM_EMBEDDING_MODEL")
>>> print(f"模型: {value}, 来源: {source}")
>>>
>>> # 通过控制台修改设置
>>> save_runtime_overrides({"LLM_EMBEDDING_MODEL": "text-embedding-v3"}, updated_by="admin")
>>>
>>> # 应用环境变量覆盖（服务启动时调用）
>>> from core.runtime_settings import apply_runtime_env_overrides
>>> apply_runtime_env_overrides()

架构说明
--------
- 所有设置规格在模块加载时定义为不可变元组 _RUNTIME_SETTING_SPECS
- 运行时通过 RUNTIME_SETTING_SPECS 字典快速查找
- 数据库覆盖存储在 console_settings 表中，支持 JSONB 类型的复杂值
- 环境变量覆盖会实时修改 os.environ，影响当前进程行为
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from core.db.connection import get_db_connection
from core.db.init_db import ensure_db_schema

# ============================================================================
# 类型定义
# ============================================================================

# 设置分类：common 为常用设置，advanced 为高级设置
SettingCategory = Literal["common", "advanced"]

# 设置来源：标识值的实际来源，用于调试和审计
SettingSource = Literal["env", "config", "code", "system", "db"]

# 设置作用域：决定设置存储和生效的方式
# - env: 存储在环境变量，进程级别生效
# - config: 存储在配置文件，需要配置重载
SettingScope = Literal["env", "config"]

# 设置值类型：用于类型转换和验证
SettingValueType = Literal["str", "bool", "int", "float"]

# ============================================================================
# 数据模型
# ============================================================================

@dataclass(frozen=True)
class RuntimeSettingSpec:
    """
    运行时设置规格定义

    定义单个设置项的元数据，包括其标识、分类、类型和存储位置。
    使用 frozen=True 确保规格在运行时不可修改，保证配置一致性。

    属性
    ----
    key : str
        设置的唯一标识符，格式通常为大写字母下划线分隔（如 LLM_API_KEY）
        对于配置文件中的设置，使用点号路径格式（如 parser.cloud.timeout）

    category : SettingCategory
        设置分类，用于UI展示分组：
        - "common": 常用设置，用户可能需要频繁修改
        - "advanced": 高级设置，通常保持默认值

    scope : SettingScope
        设置作用域，决定设置的存储和生效方式：
        - "env": 环境变量作用域，通过 os.environ 存取
        - "config": 配置文件作用域，通过 config.yaml 存取

    value_type : SettingValueType
        值的数据类型，用于类型转换：
        - "str": 字符串（默认）
        - "bool": 布尔值
        - "int": 整数
        - "float": 浮点数

    env_name : str | None
        对应的环境变量名，scope="env" 时必须设置
        例如：key="LLM_API_KEY" -> env_name="LLM_API_KEY"

    config_path : tuple[str, ...] | None
        在配置文件中的嵌套路径，scope="config" 时必须设置
        例如：key="parser.cloud.timeout" -> config_path=("parser", "cloud", "timeout")

    default : Any
        默认值，当所有其他来源都没有值时使用
        类型应与 value_type 匹配

    示例
    ----
    >>> # 环境变量类型设置
    >>> spec = RuntimeSettingSpec(
    ...     "LLM_EMBEDDING_MODEL",
    ...     category="common",
    ...     scope="env",
    ...     env_name="LLM_EMBEDDING_MODEL",
    ...     default="text-embedding-v3"
    ... )

    >>> # 配置文件类型设置
    >>> spec = RuntimeSettingSpec(
    ...     "parser.cloud.timeout",
    ...     category="advanced",
    ...     scope="config",
    ...     config_path=("parser", "cloud", "timeout"),
    ...     value_type="int",
    ...     default=1800
    ... )
    """
    key: str
    category: SettingCategory
    scope: SettingScope
    value_type: SettingValueType = "str"
    env_name: str | None = None
    config_path: tuple[str, ...] | None = None
    default: Any = ""


# ============================================================================
# 设置规格注册表
# ============================================================================

# 所有运行时设置的完整规格定义
# 按功能分组：PDF解析器、LLM配置、嵌入模型、存储配置、系统设置等
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

# 从规格元组构建查找字典，便于通过 key 快速获取规格
# 注意：使用 dict comprehension 确保每个 key 只出现一次
RUNTIME_SETTING_SPECS: dict[str, RuntimeSettingSpec] = {
    spec.key: spec for spec in _RUNTIME_SETTING_SPECS
}

# ============================================================================
# 类型转换工具函数
# ============================================================================

def _coerce_value(value: Any, value_type: SettingValueType) -> Any:
    """
    将值强制转换为指定类型

    根据设置的 value_type 将输入值转换为正确的 Python 类型。
    主要用于从字符串来源（环境变量、数据库）读取值时的类型转换。

    参数
    ----
    value : Any
        待转换的值，通常是字符串或已转换的正确类型

    value_type : SettingValueType
        目标类型，支持 "str"、"bool"、"int"、"float"

    返回
    ----
    Any
        转换后的值

    类型转换规则
    ------------
    - bool: 接受 "1"、"true"、"yes"、"on"（不区分大小写）为 True
    - int: 调用 int() 转换
    - float: 调用 float() 转换
    - str: None 转为空字符串，其他调用 str()

    示例
    ----
    >>> _coerce_value("true", "bool")
    True
    >>> _coerce_value("42", "int")
    42
    >>> _coerce_value(None, "str")
    ""
    """
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
    """
    将运行时值转换为字符串表示

    用于将值写回环境变量或日志输出。与 _coerce_value 配合使用，
    确保值可以安全地存储在环境变量中。

    参数
    ----
    value : Any
        任意类型的值

    返回
    ----
    str
        字符串表示

    转换规则
    --------
    - None: 返回空字符串
    - bool: True -> "true", False -> "false"
    - 其他: 调用 str()

    示例
    ----
    >>> stringify_runtime_value(True)
    "true"
    >>> stringify_runtime_value(42)
    "42"
    >>> stringify_runtime_value(None)
    ""
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ============================================================================
# 嵌套配置访问工具
# ============================================================================

def _read_nested(config: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    """
    从嵌套字典中读取值

    根据路径元组安全地从多层嵌套字典中读取值。
    如果路径不存在或中间节点不是字典，返回默认值。

    参数
    ----
    config : dict[str, Any]
        配置字典（可能包含嵌套结构）

    path : tuple[str, ...]
        路径元组，例如 ("parser", "cloud", "timeout")

    default : Any
        路径不存在时的默认返回值

    返回
    ----
    Any
        路径指向的值，或默认值

    示例
    ----
    >>> config = {"parser": {"cloud": {"timeout": 1800}}}
    >>> _read_nested(config, ("parser", "cloud", "timeout"), 300)
    1800
    >>> _read_nested(config, ("parser", "cloud", "missing"), 300)
    300
    """
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _write_nested(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """
    向嵌套字典写入值

    根据路径元组向多层嵌套字典写入值。
    如果中间节点不存在，会自动创建空字典。

    参数
    ----
    config : dict[str, Any]
        配置字典（将被原地修改）

    path : tuple[str, ...]
        路径元组，例如 ("parser", "cloud", "timeout")

    value : Any
        要写入的值

    注意
    ----
    此函数会原地修改 config 字典

    示例
    ----
    >>> config = {}
    >>> _write_nested(config, ("parser", "cloud", "timeout"), 1800)
    >>> config
    {"parser": {"cloud": {"timeout": 1800}}}
    """
    current = config
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


# ============================================================================
# 数据库访问层
# ============================================================================

def _load_overrides_from_db() -> dict[str, Any]:
    """
    从数据库加载控制台设置覆盖

    从 console_settings 表读取所有已保存的设置覆盖。
    这些覆盖具有最高优先级，会覆盖环境变量和配置文件中的值。

    返回
    ----
    dict[str, Any]
        设置键值对字典，只包含在 RUNTIME_SETTING_SPECS 中注册的设置

    异常处理
    --------
    - 数据库连接失败：返回空字典，不抛出异常
    - 数据库查询失败：返回空字典，不抛出异常
    - 确保即使数据库不可用，系统仍可使用默认配置运行

    数据库表结构
    ------------
    console_settings 表：
    - key: TEXT PRIMARY KEY
    - value: JSONB（存储任意 JSON 值）
    - updated_by: TEXT（最后修改者）
    - updated_at: TIMESTAMP（最后修改时间）

    示例
    ----
    >>> overrides = _load_overrides_from_db()
    >>> print(overrides)
    {"LLM_EMBEDDING_MODEL": "text-embedding-v3", "parser.cloud.timeout": 1800}
    """
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


# ============================================================================
# 公共 API
# ============================================================================

def load_runtime_overrides() -> dict[str, Any]:
    """
    加载运行时设置覆盖（公共接口）

    这是 _load_overrides_from_db 的公共包装器，
    提供更清晰的语义，用于外部调用。

    返回
    ----
    dict[str, Any]
        数据库中的设置覆盖字典

    用途
    ----
    用于查看当前有哪些设置被覆盖，而不是实际应用覆盖。
    如果需要应用覆盖到环境变量，请使用 apply_runtime_env_overrides。

    示例
    ----
    >>> overrides = load_runtime_overrides()
    >>> print(f"当前有 {len(overrides)} 个设置被覆盖")
    """
    return _load_overrides_from_db()


def apply_runtime_env_overrides() -> dict[str, Any]:
    """
    应用环境变量作用域的设置覆盖

    将数据库中 scope="env" 的设置覆盖应用到当前进程的环境变量。
    这会使数据库中的覆盖值立即生效，无需重启进程。

    返回
    ----
    dict[str, Any]
        被应用的设置覆盖字典（包括所有作用域）

    使用时机
    --------
    - 应用启动时，在加载配置之前调用
    - 通过控制台修改设置后，刷新进程环境

    覆盖机制
    --------
    1. 从数据库读取所有覆盖
    2. 筛选 scope="env" 的设置
    3. 将值转换为字符串后写入 os.environ
    4. 其他模块读取环境变量时将获得覆盖后的值

    示例
    ----
    >>> # 服务启动时
    >>> overrides = apply_runtime_env_overrides()
    >>> print(f"应用了 {len(overrides)} 个环境变量覆盖")

    注意
    ----
    此操作会修改进程级环境变量，影响所有后续的环境变量读取。
    """
    overrides = _load_overrides_from_db()
    for key, value in overrides.items():
        spec = RUNTIME_SETTING_SPECS.get(key)
        if spec and spec.scope == "env" and spec.env_name:
            os.environ[spec.env_name] = stringify_runtime_value(value)
    return overrides


def merge_runtime_config_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """
    合并配置文件作用域的设置覆盖

    将数据库中 scope="config" 的设置覆盖合并到配置字典中。
    返回一个新的配置字典，不影响原配置。

    参数
    ----
    config : dict[str, Any]
        原始配置字典（从 config.yaml 加载）

    返回
    ----
    dict[str, Any]
        合并了数据库覆盖的新配置字典

    使用时机
    --------
    - 加载配置文件后，应用数据库覆盖
    - 控制台修改配置文件类型的设置后重新加载配置

    合并机制
    --------
    1. 深拷贝原配置字典
    2. 从数据库读取 scope="config" 的覆盖
    3. 根据 config_path 将值写入正确的嵌套位置
    4. 返回合并后的新字典

    示例
    ----
    >>> from core.config import load_config
    >>> config = load_config()
    >>> config = merge_runtime_config_overrides(config)
    >>> # 现在配置包含数据库中的覆盖值

    注意
    ----
    此函数返回新字典，不会修改传入的 config 参数。
    """
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
    """
    解析单个运行时设置的值和来源

    按照优先级顺序解析设置值，并返回值及其来源。
    这是读取单个设置的标准方法，支持来源追踪。

    参数
    ----
    key : str
        设置的唯一标识符，必须在 RUNTIME_SETTING_SPECS 中注册

    config : dict[str, Any] | None
        配置字典，用于 scope="config" 的设置
        如果为 None，则只从数据库和环境变量读取

    overrides : dict[str, Any] | None
        预加载的覆盖字典，避免重复数据库查询
        如果为 None，会自动调用 _load_overrides_from_db()

    返回
    ----
    tuple[Any, SettingSource]
        (值, 来源) 元组
        来源可能是："db"、"env"、"config"、"code"

    优先级顺序
    ----------
    1. db: 数据库中的覆盖（最高优先级）
    2. env: 环境变量（仅 scope="env" 的设置）
    3. config: 配置文件（仅 scope="config" 的设置）
    4. code: 代码中的默认值（最低优先级）

    特殊处理
    --------
    - 如果数据库覆盖是空字符串且默认值非空，会回退到默认值
    - 这允许用户"清除"数据库覆盖而不设置具体值

    示例
    ----
    >>> # 简单使用
    >>> model, source = resolve_runtime_setting("LLM_EMBEDDING_MODEL")
    >>> print(f"使用模型: {model}，来源: {source}")

    >>> # 传入预加载的覆盖（性能优化）
    >>> overrides = load_runtime_overrides()
    >>> config = load_config()
    >>> timeout, source = resolve_runtime_setting(
    ...     "parser.cloud.timeout",
    ...     config=config,
    ...     overrides=overrides
    ... )

    异常
    ----
    KeyError: 如果 key 不在 RUNTIME_SETTING_SPECS 中
    """
    spec = RUNTIME_SETTING_SPECS[key]
    active_overrides = overrides if overrides is not None else _load_overrides_from_db()

    # 优先级 1：数据库覆盖
    if key in active_overrides:
        override_value = _coerce_value(active_overrides[key], spec.value_type)
        # 特殊处理：空字符串回退到默认值
        if spec.value_type == "str" and override_value == "" and spec.default != "":
            return spec.default, "code"
        return override_value, "db"

    # 优先级 2：环境变量（仅 scope="env"）
    if spec.scope == "env" and spec.env_name:
        env_value = os.environ.get(spec.env_name)
        if env_value is not None and env_value != "":
            return _coerce_value(env_value, spec.value_type), "env"
        return spec.default, "code"

    # 优先级 3：配置文件（仅 scope="config"）
    if spec.scope == "config" and spec.config_path:
        base_config = config or {}
        value = _read_nested(base_config, spec.config_path, spec.default)
        return _coerce_value(value, spec.value_type), "config"

    # 优先级 4：默认值
    return spec.default, "code"


def save_runtime_overrides(payload: dict[str, str], updated_by: str = "console") -> list[str]:
    """
    保存运行时设置覆盖到数据库

    将控制台修改的设置持久化到数据库，并实时更新环境变量（如果适用）。
    支持增量更新和删除操作。

    参数
    ----
    payload : dict[str, str]
        设置键值对，值为字符串格式
        - 空字符串表示删除该设置的覆盖
        - 其他值会被转换为正确的类型后保存

    updated_by : str
        修改者标识，用于审计追踪
        默认为 "console"，可设置为用户名或系统标识

    返回
    ----
    list[str]
        成功更新的设置键列表

    操作逻辑
    --------
    1. 连接数据库并确保表结构存在
    2. 遍历 payload 中的每个设置：
       a. 验证设置已注册
       b. 空字符串 -> 删除数据库覆盖
       c. 非空值 -> 类型转换后 UPSERT 到数据库
       d. 如果是环境变量作用域，立即更新 os.environ
    3. 提交事务并返回更新的键列表

    事务处理
    --------
    - 所有更新在单个事务中完成
    - 任何错误都会导致完整回滚
    - 确保数据一致性

    示例
    ----
    >>> # 更新多个设置
    >>> updated = save_runtime_overrides({
    ...     "LLM_EMBEDDING_MODEL": "text-embedding-v3",
    ...     "LLM_EMBEDDING_BATCH_SIZE": "20"
    ... }, updated_by="admin")
    >>> print(f"更新了: {updated}")

    >>> # 删除一个覆盖
    >>> updated = save_runtime_overrides({
    ...     "LLM_EMBEDDING_MODEL": ""  # 空字符串删除
    ... })
    >>> print(f"删除了: {updated}")

    异常
    ----
    RuntimeError: 数据库不可用或保存失败

    注意
    ----
    - 对于 scope="env" 的设置，会立即更新当前进程的环境变量
    - 对于 scope="config" 的设置，需要重新加载配置才能生效
    """
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

                # 空字符串表示删除覆盖
                if spec.scope == "config" and raw_value.strip() == "":
                    cur.execute("DELETE FROM console_settings WHERE key = %s", (key,))
                    updated.append(key)
                    continue

                # 类型转换并保存
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
                # 对于环境变量作用域，立即更新 os.environ
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
