"""
数据库 Schema 定义模块

本模块定义了 RAG 知识库系统的所有数据库表结构和索引。
这些 SQL 语句用于在 PostgreSQL 数据库中初始化表结构。

## 核心业务表

1. **knowledge_bases** - 知识库主表，存储知识库元信息
2. **documents** - 文档表，记录上传的 PDF 文档
3. **chunks** - 切片表，存储文档切片及其向量嵌入（核心表）
4. **entities** - 实体表，存储从切片中提取的实体

## 身份认证与权限表

- kb_identity_* - 租户、用户、角色相关表（从外部系统同步）
- kb_auth_sessions - 用户会话表
- kb_api_keys - API 密钥管理表

## 日志与监控表

- kb_rag_query_logs - RAG 查询日志
- kb_llm_call_logs - LLM 调用日志
- kb_token_usage_hourly - Token 用量统计
- kb_audit_logs - 审计日志

## 知识图谱表

- chunk_relations - 切片间关系
- kg_triples - 知识图谱三元组
- entity_mentions - 实体提及关系

## 向量搜索

使用 pgvector 扩展实现向量相似度搜索，
通过 HNSW 索引加速高维向量的近似最近邻查询。
"""
from __future__ import annotations

# =============================================================================
# pgvector 扩展
# =============================================================================
# pgvector 是 PostgreSQL 的向量扩展，支持：
# - vector 类型：存储高维向量（本系统使用 1024 维）
# - 向量相似度运算：余弦距离、欧氏距离、内积
# - HNSW 索引：高效的近似最近邻搜索
#
# 使用场景：存储文本 embedding 向量，支持语义相似度检索
CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

# =============================================================================
# 知识库主表 (knowledge_bases)
# =============================================================================
# 存储知识库的基本信息和治理字段。
#
# 主要字段：
# - id: 知识库唯一标识（业务生成，非自增）
# - name: 知识库名称
# - description: 知识库描述
# - default_strategy: 默认切片策略（如 hierarchical）
#
# 治理字段（多租户支持）：
# - tenant_id: 租户 ID，用于多租户隔离
# - owner_user_id: 所有者用户 ID
# - status: 知识库状态（active/archived/deleted）
# - deleted_at: 软删除时间戳
#
# 约束：
# - 主键: id
CREATE_KNOWLEDGE_BASES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    description TEXT,
    default_strategy VARCHAR(100) NOT NULL DEFAULT 'hierarchical',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# 迁移：添加 default_strategy 字段（兼容旧数据）
ALTER_KNOWLEDGE_BASES_DEFAULT_STRATEGY_SQL = """
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS default_strategy VARCHAR(100) NOT NULL DEFAULT 'hierarchical';
"""

# 迁移：添加多租户治理字段
# 这些字段支持多租户隔离、所有者转移、软删除等功能
ALTER_KNOWLEDGE_BASES_GOVERNANCE_SQL = """
ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS created_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS owner_transferred_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS owner_transferred_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS owner_status VARCHAR(50) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS owner_invalid_reason VARCHAR(50),
    ADD COLUMN IF NOT EXISTS status VARCHAR(50) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
"""

# 索引：按租户+所有者查询知识库（过滤已删除）
# 使用场景：获取用户拥有的知识库列表
CREATE_KNOWLEDGE_BASES_TENANT_OWNER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_tenant_owner
ON knowledge_bases(tenant_id, owner_user_id)
WHERE deleted_at IS NULL;
"""

# 索引：按状态查询知识库（过滤已删除）
# 使用场景：获取活跃/归档的知识库列表
CREATE_KNOWLEDGE_BASES_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_status
ON knowledge_bases(status)
WHERE deleted_at IS NULL;
"""

# =============================================================================
# 文档表 (documents)
# =============================================================================
# 存储上传到知识库的文档元信息。
#
# 主要字段：
# - id: 文档 UUID（自动生成）
# - kb_id: 所属知识库 ID（外键关联 knowledge_bases）
# - filename: 原始文件名
# - file_hash: 文件内容哈希（SHA-256），用于去重
# - chunk_count: 该文档的切片数量
#
# 来源追踪字段：
# - source_storage: 存储类型（oss/local/unknown）
# - source_path: 原始存储路径
# - source_url: 原始 URL（如适用）
# - parser_provider: 解析器提供商（如 mineru_302ai）
#
# 约束：
# - 主键: id
# - 外键: kb_id → knowledge_bases(id) ON DELETE CASCADE
# - 唯一约束: (kb_id, file_hash) 防止同一知识库重复上传相同文件
CREATE_DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id VARCHAR(255) NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    chunk_count INTEGER DEFAULT 0,
    source_storage VARCHAR(50) DEFAULT 'unknown',
    source_path TEXT,
    source_url TEXT,
    parser_provider VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(kb_id, file_hash)
);
"""

# 以下为迁移脚本，添加来源追踪字段（兼容旧数据）
ALTER_DOCUMENTS_SOURCE_STORAGE_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_storage VARCHAR(50) DEFAULT 'unknown';
"""

ALTER_DOCUMENTS_SOURCE_PATH_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path TEXT;
"""

ALTER_DOCUMENTS_SOURCE_URL_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT;
"""

ALTER_DOCUMENTS_PARSER_PROVIDER_SQL = """
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_provider VARCHAR(100);
"""

# =============================================================================
# 控制台配置表 (console_settings)
# =============================================================================
# 存储前端控制台的配置项，以键值对形式保存 JSON 配置。
#
# 使用场景：
# - 保存用户的切片策略偏好
# - 保存界面显示设置
# - 保存其他控制台级别的配置
CREATE_CONSOLE_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS console_settings (
    key VARCHAR(200) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(100) DEFAULT 'console'
);
"""

# =============================================================================
# 身份认证表（从外部系统同步）
# =============================================================================
# 以下表用于存储从外部身份系统（如 MySQL）同步过来的租户、用户、角色数据。
# 命名规范：kb_identity_* 表示身份相关表。
#
# 数据流向：
# 外部 MySQL → 同步任务 → kb_identity_* 表 → 认证/授权

# 租户表：存储租户基本信息
CREATE_IDENTITY_TENANTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_identity_tenants (
    tenant_id VARCHAR(64) PRIMARY KEY,
    tenant_name VARCHAR(255) NOT NULL,
    tenant_code VARCHAR(100),
    tenant_status VARCHAR(50) NOT NULL,
    raw_status VARCHAR(50),
    contact_name VARCHAR(255),
    contact_mobile_masked VARCHAR(100),
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 用户表：存储用户基本信息
CREATE_IDENTITY_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_identity_users (
    user_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    username VARCHAR(100) NOT NULL,
    display_name VARCHAR(255),
    mobile_masked VARCHAR(100),
    email_masked VARCHAR(255),
    user_status VARCHAR(50) NOT NULL,
    raw_status VARCHAR(50),
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 角色表：存储角色定义
CREATE_IDENTITY_ROLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_identity_roles (
    role_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64),
    role_code VARCHAR(100) NOT NULL,
    role_name VARCHAR(255) NOT NULL,
    role_status VARCHAR(50) NOT NULL,
    raw_status VARCHAR(50),
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 用户-角色关联表：多对多关系
CREATE_IDENTITY_USER_ROLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_identity_user_roles (
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role_id VARCHAR(64) NOT NULL,
    relation_status VARCHAR(50) NOT NULL,
    source_relation_id VARCHAR(64),
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id, role_id)
);
"""

# 同步任务日志表：记录每次同步的执行情况
CREATE_IDENTITY_SYNC_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_identity_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    sync_mode VARCHAR(50) NOT NULL DEFAULT 'mysql_bootstrap',
    source_host VARCHAR(255),
    source_schema VARCHAR(255),
    requested_limit INTEGER NOT NULL,
    tenants_count INTEGER NOT NULL DEFAULT 0,
    users_count INTEGER NOT NULL DEFAULT 0,
    roles_count INTEGER NOT NULL DEFAULT 0,
    user_roles_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    max_updated_at TEXT,
    snapshot_version VARCHAR(100),
    has_more BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(50) NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
"""

# 迁移：添加 HTTP 同步相关字段
ALTER_IDENTITY_SYNC_RUNS_HTTP_METADATA_SQL = """
ALTER TABLE kb_identity_sync_runs
    ADD COLUMN IF NOT EXISTS sync_mode VARCHAR(50) NOT NULL DEFAULT 'mysql_bootstrap',
    ADD COLUMN IF NOT EXISTS deleted_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_sync_at TEXT,
    ADD COLUMN IF NOT EXISTS max_updated_at TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_version VARCHAR(100),
    ADD COLUMN IF NOT EXISTS has_more BOOLEAN NOT NULL DEFAULT FALSE;
"""

# 索引：按租户查询用户
CREATE_IDENTITY_USERS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_users_tenant
ON kb_identity_users(tenant_id);
"""

# 索引：按租户+角色代码查询角色
CREATE_IDENTITY_ROLES_TENANT_CODE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_roles_tenant_code
ON kb_identity_roles(tenant_id, role_code);
"""

# 索引：按用户查询其角色关联
CREATE_IDENTITY_USER_ROLES_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_user_roles_user
ON kb_identity_user_roles(tenant_id, user_id);
"""

# =============================================================================
# 认证会话表 (kb_auth_sessions)
# =============================================================================
# 存储用户登录会话信息，支持 SSO 和 Token 认证。
#
# 主要字段：
# - session_hash: 会话哈希（主键，由 token 派生）
# - tenant_id/user_id: 租户和用户标识
# - role_codes: 用户角色列表（JSONB 数组）
# - is_tenant_admin: 是否为租户管理员
# - expires_at: 会话过期时间
# - revoked_at: 会话撤销时间（用于强制登出）
#
# 安全特性：
# - credential_fingerprint: 凭证指纹，用于检测凭证重放
# - identity_snapshot_version: 身份快照版本，用于身份变更检测
CREATE_AUTH_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_auth_sessions (
    session_hash VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    username VARCHAR(100),
    display_name VARCHAR(255),
    tenant_name VARCHAR(255),
    role_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_tenant_admin BOOLEAN NOT NULL DEFAULT FALSE,
    auth_source VARCHAR(50) NOT NULL,
    credential_fingerprint VARCHAR(128),
    identity_snapshot_version VARCHAR(100),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);
"""

# 索引：按用户查询活跃会话（过滤已撤销）
# 使用场景：查看用户当前活跃的登录设备
CREATE_AUTH_SESSIONS_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_auth_sessions_user
ON kb_auth_sessions(tenant_id, user_id, expires_at DESC)
WHERE revoked_at IS NULL;
"""

# =============================================================================
# SSO 已用凭证表 (kb_sso_used_credentials)
# =============================================================================
# 记录已使用的 SSO 凭证，防止重放攻击。
#
# 工作原理：
# 1. SSO 登录时，凭证（如 SAML Assertion）被计算指纹
# 2. 指纹存入此表，并设置过期时间
# 3. 后续请求检查指纹是否存在，存在则拒绝
CREATE_SSO_USED_CREDENTIALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_sso_used_credentials (
    credential_fingerprint VARCHAR(128) PRIMARY KEY,
    credential_type VARCHAR(50) NOT NULL,
    tenant_id VARCHAR(64),
    user_id VARCHAR(64),
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);
"""

# 索引：按过期时间查询，用于清理过期凭证
CREATE_SSO_USED_CREDENTIALS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_sso_used_credentials_expires
ON kb_sso_used_credentials(expires_at);
"""

# =============================================================================
# RAG 查询日志表 (kb_rag_query_logs)
# =============================================================================
# 记录 RAG 查询请求的详细日志，用于审计和分析。
#
# 主要字段：
# - request_id: 请求唯一标识
# - pipeline_domain/pipeline_stage: 管道域和阶段
# - query_hash: 查询文本哈希（保护隐私）
# - query_summary/answer_summary: 查询和回答摘要
# - relevance_score/faithfulness_score: 相关性和忠实度评分
# - prompt_tokens/completion_tokens: Token 用量
# - latency_ms: 响应延迟（毫秒）
# - status/error_code: 请求状态和错误码
CREATE_RAG_QUERY_LOGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_rag_query_logs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL UNIQUE,
    pipeline_domain VARCHAR(50) NOT NULL,
    pipeline_stage VARCHAR(50) NOT NULL DEFAULT 'query',
    tenant_id VARCHAR(64),
    actor_id VARCHAR(64),
    kb_id VARCHAR(255) NOT NULL,
    api_key_id VARCHAR(100),
    query_hash VARCHAR(64) NOT NULL,
    query_summary TEXT,
    answer_summary TEXT,
    cannot_answer BOOLEAN NOT NULL DEFAULT FALSE,
    relevance_score REAL,
    faithfulness_score REAL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    error_code VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 索引：按作用域查询日志（租户+知识库+用户+时间）
# 使用场景：管理员查看租户下的查询历史
CREATE_RAG_QUERY_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_rag_query_logs_scope
ON kb_rag_query_logs(tenant_id, kb_id, actor_id, created_at DESC);
"""

# 索引：按请求 ID 查询
# 使用场景：追踪单个请求的完整日志链
CREATE_RAG_QUERY_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_rag_query_logs_request
ON kb_rag_query_logs(request_id);
"""

# =============================================================================
# LLM 调用日志表 (kb_llm_call_logs)
# =============================================================================
# 记录每次 LLM API 调用的详细信息，用于成本分析和性能监控。
#
# 主要字段：
# - request_id: 关联的请求 ID
# - pipeline_domain/pipeline_stage: 调用阶段（如 embedding/chat）
# - feature_name: 功能名称（如 rag_query/chunk_enhance）
# - provider/model_name: 提供商和模型名称
# - prompt_tokens/completion_tokens: Token 用量
# - latency_ms: 调用延迟
CREATE_LLM_CALL_LOGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_llm_call_logs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64),
    tenant_id VARCHAR(64),
    actor_id VARCHAR(64),
    kb_id VARCHAR(255),
    api_key_id VARCHAR(100),
    pipeline_domain VARCHAR(50) NOT NULL,
    pipeline_stage VARCHAR(50) NOT NULL,
    feature_name VARCHAR(100) NOT NULL,
    provider VARCHAR(100) NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    model_version VARCHAR(100),
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    error_code VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 索引：按作用域查询日志
CREATE_LLM_CALL_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_llm_call_logs_scope
ON kb_llm_call_logs(tenant_id, kb_id, pipeline_domain, pipeline_stage, created_at DESC);
"""

# 索引：按请求 ID 查询
CREATE_LLM_CALL_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_llm_call_logs_request
ON kb_llm_call_logs(request_id);
"""

# =============================================================================
# Token 用量小时统计表 (kb_token_usage_hourly)
# =============================================================================
# 按小时聚合 Token 使用量，用于用量统计和成本分析。
#
# 聚合维度：
# - hour_bucket: 小时时间桶
# - tenant_id/kb_id/api_key_id: 作用域
# - pipeline_domain/pipeline_stage/feature_name: 功能维度
#
# 统计指标：
# - request_count: 请求次数
# - prompt_tokens/completion_tokens/total_tokens: Token 用量
# - latency_ms_sum: 总延迟
# - error_count: 错误次数
# - estimated_cost: 预估成本
CREATE_TOKEN_USAGE_HOURLY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_token_usage_hourly (
    hour_bucket TIMESTAMPTZ NOT NULL,
    tenant_id VARCHAR(64) NOT NULL DEFAULT '',
    kb_id VARCHAR(255) NOT NULL DEFAULT '',
    api_key_id VARCHAR(100) NOT NULL DEFAULT '',
    pipeline_domain VARCHAR(50) NOT NULL,
    pipeline_stage VARCHAR(50) NOT NULL,
    feature_name VARCHAR(100) NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    latency_ms_sum BIGINT NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    estimated_cost NUMERIC(18, 6) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hour_bucket, tenant_id, kb_id, api_key_id, pipeline_domain, pipeline_stage, feature_name)
);
"""

# 索引：按作用域查询统计
CREATE_TOKEN_USAGE_HOURLY_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_token_usage_hourly_scope
ON kb_token_usage_hourly(tenant_id, kb_id, pipeline_domain, hour_bucket DESC);
"""

# =============================================================================
# 审计日志表 (kb_audit_logs)
# =============================================================================
# 记录系统中的关键操作，用于安全审计和合规。
#
# 主要字段：
# - action: 操作类型（如 create_kb/delete_document）
# - resource_type/resource_id: 资源类型和 ID
# - actor_id/actor_name: 操作者信息
# - outcome: 操作结果（success/failure）
# - risk_level: 风险等级（low/medium/high）
# - metadata: 操作详情（JSONB）
CREATE_AUDIT_LOGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64),
    tenant_id VARCHAR(64),
    actor_id VARCHAR(64),
    actor_name VARCHAR(255),
    actor_source VARCHAR(50),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(255),
    kb_id VARCHAR(255),
    api_key_id VARCHAR(100),
    outcome VARCHAR(50) NOT NULL DEFAULT 'success',
    risk_level VARCHAR(20) NOT NULL DEFAULT 'low',
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# 索引：按作用域查询审计日志
CREATE_AUDIT_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_scope
ON kb_audit_logs(tenant_id, actor_id, action, created_at DESC);
"""

# 索引：按资源查询审计日志
CREATE_AUDIT_LOGS_RESOURCE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_resource
ON kb_audit_logs(resource_type, resource_id, created_at DESC);
"""

# 索引：按请求 ID 查询
CREATE_AUDIT_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_request
ON kb_audit_logs(request_id);
"""

# =============================================================================
# API 密钥管理表 (kb_api_keys)
# =============================================================================
# 存储用于 API 访问的密钥信息，支持细粒度权限控制。
#
# 主要字段：
# - id: 密钥 ID（前缀 + 随机部分）
# - name: 密钥名称
# - key_hash: 密钥哈希（SHA-256，不存储明文）
# - key_prefix/key_suffix: 用于识别（如 sk-xxx...abc）
# - status: 状态（active/revoked/expired）
# - kb_ids: 授权的知识库 ID 列表（JSONB 数组）
# - capabilities: 授权的能力列表（如 read/write）
#
# 安全特性：
# - require_signature: 是否要求请求签名
# - allowed_ips: IP 白名单（JSONB 数组）
# - rpm_limit: 每分钟请求限制
# - daily_request_limit: 每日请求限制
CREATE_API_KEYS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_api_keys (
    id VARCHAR(64) PRIMARY KEY,
    app_id VARCHAR(64),
    name VARCHAR(100) NOT NULL,
    tenant_id VARCHAR(64),
    created_by VARCHAR(64),
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(24) NOT NULL,
    key_suffix VARCHAR(12) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    kb_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    require_signature BOOLEAN NOT NULL DEFAULT TRUE,
    allowed_ips JSONB NOT NULL DEFAULT '[]'::jsonb,
    rpm_limit INTEGER NOT NULL DEFAULT 0,
    daily_request_limit INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
"""

# 迁移：添加强验证字段
ALTER_API_KEYS_STRONG_VALIDATION_SQL = """
ALTER TABLE kb_api_keys
    ADD COLUMN IF NOT EXISTS require_signature BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS allowed_ips JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS rpm_limit INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS daily_request_limit INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS app_id VARCHAR(64);
"""

# =============================================================================
# OpenAPI 应用表 (kb_openapi_apps)
# =============================================================================
# 存储使用 API 密钥的应用信息。
#
# 用于管理和追踪 API 密钥所属的应用程序。
CREATE_OPENAPI_APPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_openapi_apps (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    tenant_id VARCHAR(64),
    owner_user_id VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
"""

# 索引：按租户查询活跃应用
CREATE_OPENAPI_APPS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_openapi_apps_tenant
ON kb_openapi_apps(tenant_id, status, created_at DESC)
WHERE deleted_at IS NULL;
"""

# 索引：按租户查询活跃密钥
CREATE_API_KEYS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_keys_tenant
ON kb_api_keys(tenant_id, status, created_at DESC)
WHERE deleted_at IS NULL;
"""

# 索引：按密钥哈希查询（用于认证）
CREATE_API_KEYS_HASH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_keys_hash
ON kb_api_keys(key_hash)
WHERE deleted_at IS NULL;
"""

# =============================================================================
# API 密钥 Nonce 表 (kb_api_key_nonces)
# =============================================================================
# 存储已使用的 Nonce，防止请求重放攻击。
#
# 工作原理：
# 1. 客户端生成唯一 Nonce 并包含在签名请求中
# 2. 服务端验证 Nonce 未被使用
# 3. 验证通过后将 Nonce 存入此表
# 4. 过期后自动清理
CREATE_API_KEY_NONCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_api_key_nonces (
    api_key_id VARCHAR(64) NOT NULL,
    nonce VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (api_key_id, nonce)
);
"""

# 索引：按过期时间查询，用于清理过期 Nonce
CREATE_API_KEY_NONCES_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_key_nonces_expires
ON kb_api_key_nonces(expires_at);
"""

# =============================================================================
# API 密钥用量窗口表 (kb_api_key_usage_windows)
# =============================================================================
# 存储限流窗口内的请求统计，支持滑动窗口限流。
#
# 字段说明：
# - window_type: 窗口类型（如 minute/hour/day）
# - window_start: 窗口起始时间
# - request_count: 请求次数
# - token_count: Token 数量
CREATE_API_KEY_USAGE_WINDOWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_api_key_usage_windows (
    api_key_id VARCHAR(64) NOT NULL,
    window_type VARCHAR(20) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    token_count BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_key_id, window_type, window_start)
);
"""

# 索引：按更新时间查询，用于清理过期窗口
CREATE_API_KEY_USAGE_WINDOWS_UPDATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_key_usage_windows_updated
ON kb_api_key_usage_windows(updated_at DESC);
"""

# =============================================================================
# 切片草稿表 (chunk_drafts)
# =============================================================================
# 存储临时的切片草稿，用于前端预览和编辑。
#
# 使用场景：
# 1. 用户上传 PDF 后，解析结果存入此表
# 2. 用户在前端预览、编辑切片
# 3. 用户确认后，草稿转为正式切片（写入 chunks 表）
# 4. 未确认的草稿自动过期清理
#
# 主要字段：
# - task_id: 任务 ID（关联解析任务）
# - kb_id: 知识库 ID
# - chunk_id: 正式切片 ID（确认后）
# - content: 切片内容
# - strategy/layer: 切片策略和层级
# - extracted_entities/extracted_triples: 提取的实体和三元组
# - user_edited/is_deleted: 用户编辑和删除标记
# - expires_at: 过期时间（默认 24 小时）
CREATE_CHUNK_DRAFTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunk_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id VARCHAR(100) NOT NULL,
    kb_id VARCHAR(255) NOT NULL,
    chunk_id UUID,
    chunk_index INTEGER,
    content TEXT NOT NULL,
    source VARCHAR(500),
    page INTEGER,
    strategy VARCHAR(100),
    layer VARCHAR(50),
    title VARCHAR(500),
    parent_id UUID,
    related_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    enhanced_text TEXT,
    extracted_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    extracted_triples JSONB NOT NULL DEFAULT '[]'::jsonb,
    relations JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_table_chunk BOOLEAN DEFAULT FALSE,
    is_image_chunk BOOLEAN DEFAULT FALSE,
    image_path TEXT,
    user_edited BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours'
);
"""

# 索引：按任务 ID 查询草稿
CREATE_CHUNK_DRAFTS_TASK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_task ON chunk_drafts(task_id);
"""

# 索引：按知识库 ID 查询草稿
CREATE_CHUNK_DRAFTS_KB_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_kb ON chunk_drafts(kb_id);
"""

# 索引：按过期时间查询，用于清理过期草稿
CREATE_CHUNK_DRAFTS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_expires ON chunk_drafts(expires_at);
"""

# 以下为迁移脚本，添加新字段（兼容旧数据）
ALTER_CHUNK_DRAFTS_RELATED_IDS_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS related_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
"""

ALTER_CHUNK_DRAFTS_ENHANCED_TEXT_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS enhanced_text TEXT;
"""

ALTER_CHUNK_DRAFTS_EXTRACTED_ENTITIES_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS extracted_entities JSONB NOT NULL DEFAULT '[]'::jsonb;
"""

ALTER_CHUNK_DRAFTS_EXTRACTED_TRIPLES_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS extracted_triples JSONB NOT NULL DEFAULT '[]'::jsonb;
"""

ALTER_CHUNK_DRAFTS_RELATIONS_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS relations JSONB NOT NULL DEFAULT '[]'::jsonb;
"""

ALTER_CHUNK_DRAFTS_IMAGE_PATH_SQL = """
ALTER TABLE chunk_drafts ADD COLUMN IF NOT EXISTS image_path TEXT;
"""

# =============================================================================
# 切片表 (chunks) - 核心业务表
# =============================================================================
# 存储文档切片及其向量嵌入，是 RAG 检索的核心数据表。
#
# 主要字段：
# - id: 切片 UUID（主键）
# - kb_id: 所属知识库 ID
# - document_id: 所属文档 ID（外键，级联删除）
# - content: 切片文本内容
# - source/page: 来源文件名和页码
# - chunk_index: 切片序号（文档内）
# - strategy: 切片策略（fixed_length/paragraph/semantic/llm/hierarchical）
# - title: 标题（从层级结构提取）
# - char_count: 字符数
#
# 内容类型标记：
# - is_table_chunk: 是否为表格切片
# - is_image_chunk: 是否为图片切片
# - image_path: 图片路径（仅图片切片）
#
# 层级结构（hierarchical 策略）：
# - layer: 层级（parent/child/enhanced）
# - parent_id: 父切片 ID
# - related_ids: 关联切片 ID（JSON 数组，如表格切片关联文本切片）
#
# 检索字段：
# - search_text: 用于全文检索的文本（可包含增强内容）
# - search_vector: PostgreSQL 全文检索向量（tsvector）
# - embedding: 向量嵌入（1024 维，用于语义检索）
#
# 约束：
# - 主键: id
# - 外键: document_id → documents(id) ON DELETE CASCADE
CREATE_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    kb_id VARCHAR(255) NOT NULL,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    source VARCHAR(500),
    page INTEGER,
    chunk_index INTEGER,
    strategy VARCHAR(100),
    title VARCHAR(500),
    char_count INTEGER,
    is_table_chunk BOOLEAN DEFAULT FALSE,
    is_image_chunk BOOLEAN DEFAULT FALSE,
    image_path TEXT,
    layer VARCHAR(50) DEFAULT 'child',
    parent_id UUID,
    related_ids TEXT,
    search_text TEXT,
    search_vector tsvector,
    embedding vector(1024),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# 迁移脚本：添加新字段
ALTER_CHUNKS_IMAGE_PATH_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS image_path TEXT;
"""

ALTER_CHUNKS_SEARCH_TEXT_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_text TEXT;
"""

ALTER_CHUNKS_SEARCH_VECTOR_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;
"""

# 索引：全文检索索引（GIN 索引）
# GIN（Generalized Inverted Index）适合 tsvector 类型，
# 支持高效的全文检索查询（如 @@ 操作符）
CREATE_CHUNKS_SEARCH_VECTOR_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunks_search_vector
ON chunks USING GIN(search_vector);
"""

# =============================================================================
# 切片关系表 (chunk_relations)
# =============================================================================
# 存储切片之间的语义关系，用于增强检索和知识图谱构建。
#
# 主要字段：
# - kb_id: 知识库 ID
# - src_id/dst_id: 源切片和目标切片 ID
# - rel_type: 关系类型（如 semantic_similarity/citation/continuation）
# - weight: 关系权重（0-1）
# - source: 关系来源（如 llm_embedding/manual）
# - evidence: 关系证据（LLM 推理依据）
#
# 约束：
# - 唯一约束: (kb_id, src_id, dst_id, rel_type) 防止重复关系
CREATE_CHUNK_RELATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunk_relations (
    id BIGSERIAL PRIMARY KEY,
    kb_id VARCHAR(255) NOT NULL,
    src_id UUID NOT NULL,
    dst_id UUID NOT NULL,
    rel_type VARCHAR(100) NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    source VARCHAR(50) NOT NULL,
    evidence TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kb_id, src_id, dst_id, rel_type)
);
"""

# 索引：按源切片查询关系
CREATE_CHUNK_RELATIONS_SRC_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_src ON chunk_relations(kb_id, src_id, rel_type);
"""

# 索引：按目标切片查询关系
CREATE_CHUNK_RELATIONS_DST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_dst ON chunk_relations(kb_id, dst_id, rel_type);
"""

# 索引：按知识库+关系类型查询
CREATE_CHUNK_RELATIONS_KB_TYPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_kb_type ON chunk_relations(kb_id, rel_type);
"""

# =============================================================================
# 知识图谱三元组表 (kg_triples)
# =============================================================================
# 存储从切片中提取的知识图谱三元组（实体-关系-实体）。
#
# 三元组格式：(subject, predicate, object)
# - s (subject): 主体实体
# - p (predicate): 关系/谓词
# - o (object): 客体实体
#
# 主要字段：
# - kb_id: 知识库 ID
# - s/p/o: 三元组各部分
# - confidence: 提取置信度（0-1）
# - source_chunk: 来源切片 ID
CREATE_KG_TRIPLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kg_triples (
    id BIGSERIAL PRIMARY KEY,
    kb_id VARCHAR(255) NOT NULL,
    s TEXT NOT NULL,
    p TEXT NOT NULL,
    o TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_chunk UUID NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# 索引：按主体实体查询（用于图谱查询）
CREATE_KG_TRIPLES_S_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_s ON kg_triples(kb_id, s);
"""

# 索引：按客体实体查询（用于反向图谱查询）
CREATE_KG_TRIPLES_O_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_o ON kg_triples(kb_id, o);
"""

# 索引：按来源切片查询（用于追溯三元组来源）
CREATE_KG_TRIPLES_CHUNK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_chunk ON kg_triples(source_chunk);
"""

# =============================================================================
# 实体表 (entities) - 核心业务表
# =============================================================================
# 存储从切片中提取的实体信息，用于实体识别和图谱构建。
#
# 主要字段：
# - id: 实体 UUID（主键）
# - kb_id: 知识库 ID
# - name: 实体名称（主名）
# - aliases: 别名列表（数组，如 ["公司A", "A公司"]）
# - type: 实体类型（如 Person/Organization/Location/Concept）
# - definition: 实体定义/描述
# - embedding: 实体向量嵌入（用于实体相似度检索）
#
# 约束：
# - 主键: id
# - 唯一约束: (kb_id, name) 防止同一知识库重复实体
CREATE_ENTITIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY,
    kb_id VARCHAR(255) NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    type TEXT NOT NULL,
    definition TEXT,
    embedding vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kb_id, name)
);
"""

# 索引：按知识库+名称查询（用于实体查找）
CREATE_ENTITIES_KB_NAME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entities_kb_name ON entities(kb_id, name);
"""

# 索引：按知识库+类型查询（用于实体分类统计）
CREATE_ENTITIES_KB_TYPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entities_kb_type ON entities(kb_id, type);
"""

# =============================================================================
# 实体提及表 (entity_mentions)
# =============================================================================
# 存储实体在切片中的提及关系（实体被哪些切片引用）。
#
# 主要字段：
# - entity_id: 实体 ID（外键，级联删除）
# - chunk_id: 切片 ID
# - kb_id: 知识库 ID
#
# 约束：
# - 主键: (entity_id, chunk_id) 防止重复提及
# - 外键: entity_id → entities(id) ON DELETE CASCADE
CREATE_ENTITY_MENTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL,
    kb_id VARCHAR(255) NOT NULL,
    PRIMARY KEY (entity_id, chunk_id)
);
"""

# 索引：按切片查询提及（用于获取切片中的所有实体）
CREATE_ENTITY_MENTIONS_CHUNK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entity_mentions_chunk ON entity_mentions(chunk_id);
"""

# =============================================================================
# 向量索引设计
# =============================================================================
# 以下索引用于加速向量相似度检索，是 RAG 系统的核心性能优化。

# HNSW 索引：用于向量相似度检索
# HNSW（Hierarchical Navigable Small World）是一种高效的近似最近邻算法。
#
# 参数说明：
# - m = 16: 每个节点的最大连接数，影响召回率和索引大小
#   值越大，召回率越高，但索引越大
# - ef_construction = 64: 构建索引时的动态候选列表大小
#   值越大，索引质量越好，但构建时间越长
#
# 使用场景：
# - 语义搜索：SELECT * FROM chunks ORDER BY embedding <=> query_vector LIMIT 10
# - 相似切片推荐
#
# 性能特点：
# - 查询复杂度: O(log n)
# - 召回率: 95%+（取决于参数）
# - 空间开销: 约为原始向量的 1.5-2 倍
CREATE_HNSW_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
ON chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
"""

# 知识库 ID 索引：用于按知识库筛选切片
# 使用场景：
# - 获取某知识库下的所有切片
# - 删除某知识库时级联查询
CREATE_KB_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS chunks_kb_id_idx ON chunks (kb_id);
"""

# =============================================================================
# 初始化 SQL 列表
# =============================================================================
# 按依赖顺序排列的所有初始化 SQL 语句。
#
# 执行顺序说明：
# 1. 扩展安装（vector）
# 2. 核心表创建（knowledge_bases → documents → chunks）
# 3. 身份认证相关表
# 4. 日志和监控相关表
# 5. API 密钥管理相关表
# 6. 切片草稿表
# 7. 知识图谱相关表
# 8. 索引创建
#
# 注意：ALTER 语句使用 IF NOT EXISTS，可安全重复执行
INIT_SQLS = [
    # 扩展安装
    CREATE_EXTENSION_SQL,
    # 知识库表
    CREATE_KNOWLEDGE_BASES_TABLE_SQL,
    ALTER_KNOWLEDGE_BASES_DEFAULT_STRATEGY_SQL,
    ALTER_KNOWLEDGE_BASES_GOVERNANCE_SQL,
    CREATE_KNOWLEDGE_BASES_TENANT_OWNER_INDEX_SQL,
    CREATE_KNOWLEDGE_BASES_STATUS_INDEX_SQL,
    # 文档表
    CREATE_DOCUMENTS_TABLE_SQL,
    ALTER_DOCUMENTS_SOURCE_STORAGE_SQL,
    ALTER_DOCUMENTS_SOURCE_PATH_SQL,
    ALTER_DOCUMENTS_SOURCE_URL_SQL,
    ALTER_DOCUMENTS_PARSER_PROVIDER_SQL,
    # 控制台设置表
    CREATE_CONSOLE_SETTINGS_TABLE_SQL,
    # 身份认证表
    CREATE_IDENTITY_TENANTS_TABLE_SQL,
    CREATE_IDENTITY_USERS_TABLE_SQL,
    CREATE_IDENTITY_ROLES_TABLE_SQL,
    CREATE_IDENTITY_USER_ROLES_TABLE_SQL,
    CREATE_IDENTITY_SYNC_RUNS_TABLE_SQL,
    ALTER_IDENTITY_SYNC_RUNS_HTTP_METADATA_SQL,
    CREATE_IDENTITY_USERS_TENANT_INDEX_SQL,
    CREATE_IDENTITY_ROLES_TENANT_CODE_INDEX_SQL,
    CREATE_IDENTITY_USER_ROLES_USER_INDEX_SQL,
    # 会话表
    CREATE_AUTH_SESSIONS_TABLE_SQL,
    CREATE_AUTH_SESSIONS_USER_INDEX_SQL,
    # SSO 凭证表
    CREATE_SSO_USED_CREDENTIALS_TABLE_SQL,
    CREATE_SSO_USED_CREDENTIALS_EXPIRES_INDEX_SQL,
    # RAG 查询日志表
    CREATE_RAG_QUERY_LOGS_TABLE_SQL,
    CREATE_RAG_QUERY_LOGS_SCOPE_INDEX_SQL,
    CREATE_RAG_QUERY_LOGS_REQUEST_INDEX_SQL,
    # LLM 调用日志表
    CREATE_LLM_CALL_LOGS_TABLE_SQL,
    CREATE_LLM_CALL_LOGS_SCOPE_INDEX_SQL,
    CREATE_LLM_CALL_LOGS_REQUEST_INDEX_SQL,
    # Token 用量统计表
    CREATE_TOKEN_USAGE_HOURLY_TABLE_SQL,
    CREATE_TOKEN_USAGE_HOURLY_SCOPE_INDEX_SQL,
    # 审计日志表
    CREATE_AUDIT_LOGS_TABLE_SQL,
    CREATE_AUDIT_LOGS_SCOPE_INDEX_SQL,
    CREATE_AUDIT_LOGS_RESOURCE_INDEX_SQL,
    CREATE_AUDIT_LOGS_REQUEST_INDEX_SQL,
    # API 密钥表
    CREATE_API_KEYS_TABLE_SQL,
    ALTER_API_KEYS_STRONG_VALIDATION_SQL,
    CREATE_OPENAPI_APPS_TABLE_SQL,
    CREATE_OPENAPI_APPS_TENANT_INDEX_SQL,
    CREATE_API_KEYS_TENANT_INDEX_SQL,
    CREATE_API_KEYS_HASH_INDEX_SQL,
    CREATE_API_KEY_NONCES_TABLE_SQL,
    CREATE_API_KEY_NONCES_EXPIRES_INDEX_SQL,
    CREATE_API_KEY_USAGE_WINDOWS_TABLE_SQL,
    CREATE_API_KEY_USAGE_WINDOWS_UPDATED_INDEX_SQL,
    # 切片草稿表
    CREATE_CHUNK_DRAFTS_TABLE_SQL,
    ALTER_CHUNK_DRAFTS_RELATED_IDS_SQL,
    ALTER_CHUNK_DRAFTS_ENHANCED_TEXT_SQL,
    ALTER_CHUNK_DRAFTS_EXTRACTED_ENTITIES_SQL,
    ALTER_CHUNK_DRAFTS_EXTRACTED_TRIPLES_SQL,
    ALTER_CHUNK_DRAFTS_RELATIONS_SQL,
    ALTER_CHUNK_DRAFTS_IMAGE_PATH_SQL,
    # 切片表（核心）
    CREATE_CHUNKS_TABLE_SQL,
    ALTER_CHUNKS_IMAGE_PATH_SQL,
    ALTER_CHUNKS_SEARCH_TEXT_SQL,
    ALTER_CHUNKS_SEARCH_VECTOR_SQL,
    # 切片关系表
    CREATE_CHUNK_RELATIONS_TABLE_SQL,
    # 向量索引
    CREATE_HNSW_INDEX_SQL,
    CREATE_KB_INDEX_SQL,
    CREATE_CHUNKS_SEARCH_VECTOR_INDEX_SQL,
    # 切片草稿索引
    CREATE_CHUNK_DRAFTS_TASK_INDEX_SQL,
    CREATE_CHUNK_DRAFTS_KB_INDEX_SQL,
    CREATE_CHUNK_DRAFTS_EXPIRES_INDEX_SQL,
    # 切片关系索引
    CREATE_CHUNK_RELATIONS_SRC_INDEX_SQL,
    CREATE_CHUNK_RELATIONS_DST_INDEX_SQL,
    CREATE_CHUNK_RELATIONS_KB_TYPE_INDEX_SQL,
    # 知识图谱三元组表
    CREATE_KG_TRIPLES_TABLE_SQL,
    CREATE_KG_TRIPLES_S_INDEX_SQL,
    CREATE_KG_TRIPLES_O_INDEX_SQL,
    CREATE_KG_TRIPLES_CHUNK_INDEX_SQL,
    # 实体表
    CREATE_ENTITIES_TABLE_SQL,
    CREATE_ENTITIES_KB_NAME_INDEX_SQL,
    CREATE_ENTITIES_KB_TYPE_INDEX_SQL,
    CREATE_ENTITY_MENTIONS_TABLE_SQL,
    CREATE_ENTITY_MENTIONS_CHUNK_INDEX_SQL,
]
