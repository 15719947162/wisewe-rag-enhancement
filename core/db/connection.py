from __future__ import annotations

import os
from urllib.parse import quote_plus


def get_db_url() -> str:
    """Build PostgreSQL connection URL from environment variables.

    Priority:
      1. DATABASE_URL env var (if set, return directly)
      2. Assemble from PGVECTOR_* individual vars
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    user = os.environ.get("PGVECTOR_USER", "postgres")
    password = os.environ.get("PGVECTOR_PASSWORD", "")
    host = os.environ.get("PGVECTOR_HOST", "localhost")
    port = os.environ.get("PGVECTOR_PORT", "5432")
    db = os.environ.get("PGVECTOR_DB", "rag_db")

    if password:
        return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"
    return f"postgresql://{quote_plus(user)}@{host}:{port}/{db}"


def get_db_connection():
    """Return a psycopg2 connection using environment-based config."""
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError(
            "psycopg2 is required for pgvector support. "
            "Install with: pip install psycopg2-binary"
        ) from exc

    url = get_db_url()
    try:
        conn = psycopg2.connect(url, client_encoding="UTF8")
        return conn
    except Exception as exc:
        raise ConnectionError(
            f"Cannot connect to PostgreSQL: {exc}\n"
            "请检查 .env 中的 PGVECTOR_* 配置"
        ) from exc


def is_db_available() -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        conn = get_db_connection()
        conn.close()
        return True
    except Exception:
        return False
