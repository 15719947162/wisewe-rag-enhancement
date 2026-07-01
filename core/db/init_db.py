from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Callable

from dotenv import load_dotenv

from core.db.connection import get_db_connection
from core.db.schema import INIT_SQLS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_DESCRIPTIONS = [
    "Creating pgvector extension...",
    "Creating knowledge_bases table...",
    "Ensuring knowledge_bases default_strategy column...",
    "Ensuring knowledge_bases governance columns...",
    "Creating knowledge_bases tenant/owner index...",
    "Creating knowledge_bases status index...",
    "Creating documents table...",
    "Ensuring documents source_storage column...",
    "Ensuring documents source_path column...",
    "Ensuring documents source_url column...",
    "Ensuring documents parser_provider column...",
    "Creating console_settings table...",
    "Creating kb_identity_tenants table...",
    "Creating kb_identity_users table...",
    "Creating kb_identity_roles table...",
    "Creating kb_identity_user_roles table...",
    "Creating kb_identity_sync_runs table...",
    "Ensuring kb_identity_sync_runs HTTP metadata columns...",
    "Creating kb_identity_users tenant index...",
    "Creating kb_identity_roles tenant/code index...",
    "Creating kb_identity_user_roles user index...",
    "Creating kb_auth_sessions table...",
    "Creating kb_auth_sessions user index...",
    "Creating kb_sso_used_credentials table...",
    "Creating kb_sso_used_credentials expires index...",
    "Creating kb_rag_query_logs table...",
    "Creating kb_rag_query_logs scope index...",
    "Creating kb_rag_query_logs request index...",
    "Creating kb_llm_call_logs table...",
    "Creating kb_llm_call_logs scope index...",
    "Creating kb_llm_call_logs request index...",
    "Creating kb_token_usage_hourly table...",
    "Creating kb_token_usage_hourly scope index...",
    "Creating kb_audit_logs table...",
    "Creating kb_audit_logs scope index...",
    "Creating kb_audit_logs resource index...",
    "Creating kb_audit_logs request index...",
    "Creating kb_api_keys table...",
    "Ensuring kb_api_keys strong validation columns...",
    "Creating kb_openapi_apps table...",
    "Creating kb_openapi_apps tenant index...",
    "Creating kb_api_keys tenant index...",
    "Creating kb_api_keys hash index...",
    "Creating kb_api_key_nonces table...",
    "Creating kb_api_key_nonces expires index...",
    "Creating kb_api_key_usage_windows table...",
    "Creating kb_api_key_usage_windows updated index...",
    "Creating chunk_drafts table...",
    "Ensuring chunk_drafts related_ids column...",
    "Ensuring chunk_drafts enhanced_text column...",
    "Ensuring chunk_drafts extracted_entities column...",
    "Ensuring chunk_drafts extracted_triples column...",
    "Ensuring chunk_drafts relations column...",
    "Ensuring chunk_drafts image_path column...",
    "Creating chunks table...",
    "Ensuring chunks image_path column...",
    "Ensuring chunks search_text column...",
    "Ensuring chunks search_vector column...",
    "Creating chunk_relations table...",
    "Creating HNSW index on chunks.embedding...",
    "Creating kb_id index on chunks...",
    "Creating chunks search_vector index...",
    "Creating chunk_drafts task index...",
    "Creating chunk_drafts kb index...",
    "Creating chunk_drafts expires index...",
    "Creating chunk_relations src index...",
    "Creating chunk_relations dst index...",
    "Creating chunk_relations kb/type index...",
    "Creating kg_triples table...",
    "Creating kg_triples s index...",
    "Creating kg_triples o index...",
    "Creating kg_triples chunk index...",
    "Creating entities table...",
    "Creating entities kb/name index...",
    "Creating entities kb/type index...",
    "Creating entity_mentions table...",
    "Creating entity_mentions chunk index...",
]

_SCHEMA_READY = False
_SCHEMA_LOCK = Lock()


def _run_init_sqls(conn, emit: Callable[[str], None] | None = None) -> None:
    with conn.cursor() as cur:
        for desc, sql in zip(_DESCRIPTIONS, INIT_SQLS):
            if emit is not None:
                emit(desc)
            cur.execute(sql)


def ensure_db_schema(conn=None) -> bool:
    """Ensure the PostgreSQL schema exists.

    Returns True when init SQLs were executed in this process, False when a
    previous call already marked the schema as ready.
    """
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return False

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return False

        owns_connection = conn is None
        if owns_connection:
            conn = get_db_connection()
            conn.autocommit = True

        try:
            _run_init_sqls(conn)
            _SCHEMA_READY = True
            return True
        finally:
            if owns_connection:
                conn.close()


def main() -> None:
    try:
        conn = get_db_connection()
        conn.autocommit = True
    except Exception as exc:
        print(f"FAILED Connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        _run_init_sqls(conn, emit=print)
        print("OK Database initialized successfully")
    except Exception as exc:
        print(f"FAILED Initialization failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
