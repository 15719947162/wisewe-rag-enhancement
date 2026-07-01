from __future__ import annotations

CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE_KNOWLEDGE_BASES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    description TEXT,
    default_strategy VARCHAR(100) NOT NULL DEFAULT 'hierarchical',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

ALTER_KNOWLEDGE_BASES_DEFAULT_STRATEGY_SQL = """
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS default_strategy VARCHAR(100) NOT NULL DEFAULT 'hierarchical';
"""

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

CREATE_KNOWLEDGE_BASES_TENANT_OWNER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_tenant_owner
ON knowledge_bases(tenant_id, owner_user_id)
WHERE deleted_at IS NULL;
"""

CREATE_KNOWLEDGE_BASES_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_status
ON knowledge_bases(status)
WHERE deleted_at IS NULL;
"""

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

CREATE_CONSOLE_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS console_settings (
    key VARCHAR(200) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(100) DEFAULT 'console'
);
"""

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

ALTER_IDENTITY_SYNC_RUNS_HTTP_METADATA_SQL = """
ALTER TABLE kb_identity_sync_runs
    ADD COLUMN IF NOT EXISTS sync_mode VARCHAR(50) NOT NULL DEFAULT 'mysql_bootstrap',
    ADD COLUMN IF NOT EXISTS deleted_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_sync_at TEXT,
    ADD COLUMN IF NOT EXISTS max_updated_at TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_version VARCHAR(100),
    ADD COLUMN IF NOT EXISTS has_more BOOLEAN NOT NULL DEFAULT FALSE;
"""

CREATE_IDENTITY_USERS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_users_tenant
ON kb_identity_users(tenant_id);
"""

CREATE_IDENTITY_ROLES_TENANT_CODE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_roles_tenant_code
ON kb_identity_roles(tenant_id, role_code);
"""

CREATE_IDENTITY_USER_ROLES_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_identity_user_roles_user
ON kb_identity_user_roles(tenant_id, user_id);
"""

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

CREATE_AUTH_SESSIONS_USER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_auth_sessions_user
ON kb_auth_sessions(tenant_id, user_id, expires_at DESC)
WHERE revoked_at IS NULL;
"""

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

CREATE_SSO_USED_CREDENTIALS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_sso_used_credentials_expires
ON kb_sso_used_credentials(expires_at);
"""

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

CREATE_RAG_QUERY_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_rag_query_logs_scope
ON kb_rag_query_logs(tenant_id, kb_id, actor_id, created_at DESC);
"""

CREATE_RAG_QUERY_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_rag_query_logs_request
ON kb_rag_query_logs(request_id);
"""

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

CREATE_LLM_CALL_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_llm_call_logs_scope
ON kb_llm_call_logs(tenant_id, kb_id, pipeline_domain, pipeline_stage, created_at DESC);
"""

CREATE_LLM_CALL_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_llm_call_logs_request
ON kb_llm_call_logs(request_id);
"""

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

CREATE_TOKEN_USAGE_HOURLY_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_token_usage_hourly_scope
ON kb_token_usage_hourly(tenant_id, kb_id, pipeline_domain, hour_bucket DESC);
"""

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

CREATE_AUDIT_LOGS_SCOPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_scope
ON kb_audit_logs(tenant_id, actor_id, action, created_at DESC);
"""

CREATE_AUDIT_LOGS_RESOURCE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_resource
ON kb_audit_logs(resource_type, resource_id, created_at DESC);
"""

CREATE_AUDIT_LOGS_REQUEST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_audit_logs_request
ON kb_audit_logs(request_id);
"""

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

ALTER_API_KEYS_STRONG_VALIDATION_SQL = """
ALTER TABLE kb_api_keys
    ADD COLUMN IF NOT EXISTS require_signature BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS allowed_ips JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS rpm_limit INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS daily_request_limit INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS app_id VARCHAR(64);
"""

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

CREATE_OPENAPI_APPS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_openapi_apps_tenant
ON kb_openapi_apps(tenant_id, status, created_at DESC)
WHERE deleted_at IS NULL;
"""

CREATE_API_KEYS_TENANT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_keys_tenant
ON kb_api_keys(tenant_id, status, created_at DESC)
WHERE deleted_at IS NULL;
"""

CREATE_API_KEYS_HASH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_keys_hash
ON kb_api_keys(key_hash)
WHERE deleted_at IS NULL;
"""

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

CREATE_API_KEY_NONCES_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_key_nonces_expires
ON kb_api_key_nonces(expires_at);
"""

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

CREATE_API_KEY_USAGE_WINDOWS_UPDATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_api_key_usage_windows_updated
ON kb_api_key_usage_windows(updated_at DESC);
"""

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

CREATE_CHUNK_DRAFTS_TASK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_task ON chunk_drafts(task_id);
"""

CREATE_CHUNK_DRAFTS_KB_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_kb ON chunk_drafts(kb_id);
"""

CREATE_CHUNK_DRAFTS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_drafts_expires ON chunk_drafts(expires_at);
"""

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

ALTER_CHUNKS_IMAGE_PATH_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS image_path TEXT;
"""

ALTER_CHUNKS_SEARCH_TEXT_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_text TEXT;
"""

ALTER_CHUNKS_SEARCH_VECTOR_SQL = """
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;
"""

CREATE_CHUNKS_SEARCH_VECTOR_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunks_search_vector
ON chunks USING GIN(search_vector);
"""

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

CREATE_CHUNK_RELATIONS_SRC_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_src ON chunk_relations(kb_id, src_id, rel_type);
"""

CREATE_CHUNK_RELATIONS_DST_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_dst ON chunk_relations(kb_id, dst_id, rel_type);
"""

CREATE_CHUNK_RELATIONS_KB_TYPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chunk_relations_kb_type ON chunk_relations(kb_id, rel_type);
"""

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

CREATE_KG_TRIPLES_S_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_s ON kg_triples(kb_id, s);
"""

CREATE_KG_TRIPLES_O_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_o ON kg_triples(kb_id, o);
"""

CREATE_KG_TRIPLES_CHUNK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kg_triples_chunk ON kg_triples(source_chunk);
"""

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

CREATE_ENTITIES_KB_NAME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entities_kb_name ON entities(kb_id, name);
"""

CREATE_ENTITIES_KB_TYPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entities_kb_type ON entities(kb_id, type);
"""

CREATE_ENTITY_MENTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL,
    kb_id VARCHAR(255) NOT NULL,
    PRIMARY KEY (entity_id, chunk_id)
);
"""

CREATE_ENTITY_MENTIONS_CHUNK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_entity_mentions_chunk ON entity_mentions(chunk_id);
"""

CREATE_HNSW_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
ON chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
"""

CREATE_KB_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS chunks_kb_id_idx ON chunks (kb_id);
"""

INIT_SQLS = [
    CREATE_EXTENSION_SQL,
    CREATE_KNOWLEDGE_BASES_TABLE_SQL,
    ALTER_KNOWLEDGE_BASES_DEFAULT_STRATEGY_SQL,
    ALTER_KNOWLEDGE_BASES_GOVERNANCE_SQL,
    CREATE_KNOWLEDGE_BASES_TENANT_OWNER_INDEX_SQL,
    CREATE_KNOWLEDGE_BASES_STATUS_INDEX_SQL,
    CREATE_DOCUMENTS_TABLE_SQL,
    ALTER_DOCUMENTS_SOURCE_STORAGE_SQL,
    ALTER_DOCUMENTS_SOURCE_PATH_SQL,
    ALTER_DOCUMENTS_SOURCE_URL_SQL,
    ALTER_DOCUMENTS_PARSER_PROVIDER_SQL,
    CREATE_CONSOLE_SETTINGS_TABLE_SQL,
    CREATE_IDENTITY_TENANTS_TABLE_SQL,
    CREATE_IDENTITY_USERS_TABLE_SQL,
    CREATE_IDENTITY_ROLES_TABLE_SQL,
    CREATE_IDENTITY_USER_ROLES_TABLE_SQL,
    CREATE_IDENTITY_SYNC_RUNS_TABLE_SQL,
    ALTER_IDENTITY_SYNC_RUNS_HTTP_METADATA_SQL,
    CREATE_IDENTITY_USERS_TENANT_INDEX_SQL,
    CREATE_IDENTITY_ROLES_TENANT_CODE_INDEX_SQL,
    CREATE_IDENTITY_USER_ROLES_USER_INDEX_SQL,
    CREATE_AUTH_SESSIONS_TABLE_SQL,
    CREATE_AUTH_SESSIONS_USER_INDEX_SQL,
    CREATE_SSO_USED_CREDENTIALS_TABLE_SQL,
    CREATE_SSO_USED_CREDENTIALS_EXPIRES_INDEX_SQL,
    CREATE_RAG_QUERY_LOGS_TABLE_SQL,
    CREATE_RAG_QUERY_LOGS_SCOPE_INDEX_SQL,
    CREATE_RAG_QUERY_LOGS_REQUEST_INDEX_SQL,
    CREATE_LLM_CALL_LOGS_TABLE_SQL,
    CREATE_LLM_CALL_LOGS_SCOPE_INDEX_SQL,
    CREATE_LLM_CALL_LOGS_REQUEST_INDEX_SQL,
    CREATE_TOKEN_USAGE_HOURLY_TABLE_SQL,
    CREATE_TOKEN_USAGE_HOURLY_SCOPE_INDEX_SQL,
    CREATE_AUDIT_LOGS_TABLE_SQL,
    CREATE_AUDIT_LOGS_SCOPE_INDEX_SQL,
    CREATE_AUDIT_LOGS_RESOURCE_INDEX_SQL,
    CREATE_AUDIT_LOGS_REQUEST_INDEX_SQL,
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
    CREATE_CHUNK_DRAFTS_TABLE_SQL,
    ALTER_CHUNK_DRAFTS_RELATED_IDS_SQL,
    ALTER_CHUNK_DRAFTS_ENHANCED_TEXT_SQL,
    ALTER_CHUNK_DRAFTS_EXTRACTED_ENTITIES_SQL,
    ALTER_CHUNK_DRAFTS_EXTRACTED_TRIPLES_SQL,
    ALTER_CHUNK_DRAFTS_RELATIONS_SQL,
    ALTER_CHUNK_DRAFTS_IMAGE_PATH_SQL,
    CREATE_CHUNKS_TABLE_SQL,
    ALTER_CHUNKS_IMAGE_PATH_SQL,
    ALTER_CHUNKS_SEARCH_TEXT_SQL,
    ALTER_CHUNKS_SEARCH_VECTOR_SQL,
    CREATE_CHUNK_RELATIONS_TABLE_SQL,
    CREATE_HNSW_INDEX_SQL,
    CREATE_KB_INDEX_SQL,
    CREATE_CHUNKS_SEARCH_VECTOR_INDEX_SQL,
    CREATE_CHUNK_DRAFTS_TASK_INDEX_SQL,
    CREATE_CHUNK_DRAFTS_KB_INDEX_SQL,
    CREATE_CHUNK_DRAFTS_EXPIRES_INDEX_SQL,
    CREATE_CHUNK_RELATIONS_SRC_INDEX_SQL,
    CREATE_CHUNK_RELATIONS_DST_INDEX_SQL,
    CREATE_CHUNK_RELATIONS_KB_TYPE_INDEX_SQL,
    CREATE_KG_TRIPLES_TABLE_SQL,
    CREATE_KG_TRIPLES_S_INDEX_SQL,
    CREATE_KG_TRIPLES_O_INDEX_SQL,
    CREATE_KG_TRIPLES_CHUNK_INDEX_SQL,
    CREATE_ENTITIES_TABLE_SQL,
    CREATE_ENTITIES_KB_NAME_INDEX_SQL,
    CREATE_ENTITIES_KB_TYPE_INDEX_SQL,
    CREATE_ENTITY_MENTIONS_TABLE_SQL,
    CREATE_ENTITY_MENTIONS_CHUNK_INDEX_SQL,
]
