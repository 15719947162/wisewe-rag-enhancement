from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.connection import get_db_connection  # noqa: E402
from core.db.init_db import ensure_db_schema  # noqa: E402


REQUIRED_SOURCE_TABLES = ("system_tenant", "sys_user", "sys_role", "sys_user_role")


@dataclass(frozen=True)
class SourceRow:
    tenant_id: str
    tenant_name: str
    tenant_code: str | None
    tenant_status: str
    tenant_raw_status: str
    tenant_contact_name: str | None
    tenant_contact_mobile_masked: str | None
    tenant_updated_at: Any
    user_id: str
    username: str
    display_name: str | None
    mobile_masked: str | None
    email_masked: str | None
    user_status: str
    user_raw_status: str
    user_updated_at: Any
    role_id: str
    role_code: str
    role_name: str
    role_status: str
    role_raw_status: str
    role_updated_at: Any
    user_role_id: str | None
    user_role_status: str
    user_role_updated_at: Any


def mask_mobile(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-4:]}"
    if text:
        return "***"
    return None


def mask_email(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "@" not in text:
        return "***"
    local, domain = text.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def normalize_tenant_status(value: Any) -> str:
    return "active" if str(value).strip().lower() == "1" else "disabled"


def normalize_user_or_role_status(value: Any) -> str:
    return "active" if str(value).strip().lower() == "0" else "disabled"


def connect_mysql(args: argparse.Namespace):
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ImportError("PyMySQL is required. Install with: pip install PyMySQL") from exc

    return pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_db or None,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
    )


def discover_mysql_schema(conn, requested_db: str | None = None) -> str:
    if requested_db:
        return requested_db
    placeholders = ", ".join(["%s"] * len(REQUIRED_SOURCE_TABLES))
    sql = f"""
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name IN ({placeholders})
        GROUP BY table_schema
        HAVING COUNT(DISTINCT table_name) = %s
        ORDER BY table_schema
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (*REQUIRED_SOURCE_TABLES, len(REQUIRED_SOURCE_TABLES)))
        row = cur.fetchone()
    if not row:
        raise RuntimeError(
            "Cannot find a MySQL schema containing system_tenant, sys_user, sys_role, and sys_user_role"
        )
    return str(row.get("table_schema") or row.get("TABLE_SCHEMA"))


def _q(schema: str, table: str) -> str:
    return f"`{schema.replace('`', '``')}`.`{table.replace('`', '``')}`"


def fetch_source_rows(conn, schema: str, limit: int) -> list[SourceRow]:
    sql = f"""
        SELECT
            t.id AS tenant_id,
            t.name AS tenant_name,
            t.code AS tenant_code,
            t.status AS tenant_status,
            t.contact_name AS tenant_contact_name,
            t.contact_mobile AS tenant_contact_mobile,
            t.updated_time AS tenant_updated_at,
            u.id AS user_id,
            u.user_name AS username,
            u.nick_name AS display_name,
            u.mobile AS mobile,
            u.email AS email,
            u.status AS user_status,
            u.updated_time AS user_updated_at,
            r.id AS role_id,
            r.code AS role_code,
            r.name AS role_name,
            r.status AS role_status,
            r.updated_time AS role_updated_at,
            ur.id AS user_role_id,
            ur.updated_time AS user_role_updated_at
        FROM {_q(schema, "system_tenant")} t
        JOIN {_q(schema, "sys_user")} u
          ON u.tenant_id = t.id
         AND u.deleted = b'0'
        JOIN {_q(schema, "sys_user_role")} ur
          ON ur.user_id = u.id
         AND ur.deleted = b'0'
        JOIN {_q(schema, "sys_role")} r
          ON r.id = ur.role_id
         AND r.deleted = b'0'
        WHERE t.deleted = b'0'
        ORDER BY
            CASE WHEN r.code = 'superManager' THEN 0 ELSE 1 END,
            t.id,
            u.id,
            r.id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    return [
        SourceRow(
            tenant_id=str(row["tenant_id"]),
            tenant_name=str(row["tenant_name"]),
            tenant_code=str(row["tenant_code"]) if row.get("tenant_code") is not None else None,
            tenant_status=normalize_tenant_status(row.get("tenant_status")),
            tenant_raw_status=str(row.get("tenant_status")),
            tenant_contact_name=str(row["tenant_contact_name"]) if row.get("tenant_contact_name") is not None else None,
            tenant_contact_mobile_masked=mask_mobile(row.get("tenant_contact_mobile")),
            tenant_updated_at=row.get("tenant_updated_at"),
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]) if row.get("display_name") is not None else None,
            mobile_masked=mask_mobile(row.get("mobile")),
            email_masked=mask_email(row.get("email")),
            user_status=normalize_user_or_role_status(row.get("user_status")),
            user_raw_status=str(row.get("user_status")),
            user_updated_at=row.get("user_updated_at"),
            role_id=str(row["role_id"]),
            role_code=str(row["role_code"]),
            role_name=str(row["role_name"]),
            role_status=normalize_user_or_role_status(row.get("role_status")),
            role_raw_status=str(row.get("role_status")),
            role_updated_at=row.get("role_updated_at"),
            user_role_id=str(row["user_role_id"]) if row.get("user_role_id") is not None else None,
            user_role_status="active",
            user_role_updated_at=row.get("user_role_updated_at"),
        )
        for row in rows
    ]


def _as_utc(value: Any):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def write_snapshot_rows(conn, rows: list[SourceRow]) -> dict[str, int]:
    tenants = {row.tenant_id: row for row in rows}
    users = {row.user_id: row for row in rows}
    roles = {row.role_id: row for row in rows}
    user_roles = {(row.tenant_id, row.user_id, row.role_id): row for row in rows}

    with conn.cursor() as cur:
        for row in tenants.values():
            cur.execute(
                """
                INSERT INTO kb_identity_tenants(
                    tenant_id, tenant_name, tenant_code, tenant_status, raw_status,
                    contact_name, contact_mobile_masked, source_updated_at, synced_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(tenant_id) DO UPDATE SET
                    tenant_name = EXCLUDED.tenant_name,
                    tenant_code = EXCLUDED.tenant_code,
                    tenant_status = EXCLUDED.tenant_status,
                    raw_status = EXCLUDED.raw_status,
                    contact_name = EXCLUDED.contact_name,
                    contact_mobile_masked = EXCLUDED.contact_mobile_masked,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    row.tenant_id,
                    row.tenant_name,
                    row.tenant_code,
                    row.tenant_status,
                    row.tenant_raw_status,
                    row.tenant_contact_name,
                    row.tenant_contact_mobile_masked,
                    _as_utc(row.tenant_updated_at),
                ),
            )
        for row in users.values():
            cur.execute(
                """
                INSERT INTO kb_identity_users(
                    user_id, tenant_id, username, display_name, mobile_masked,
                    email_masked, user_status, raw_status, source_updated_at, synced_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(user_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    username = EXCLUDED.username,
                    display_name = EXCLUDED.display_name,
                    mobile_masked = EXCLUDED.mobile_masked,
                    email_masked = EXCLUDED.email_masked,
                    user_status = EXCLUDED.user_status,
                    raw_status = EXCLUDED.raw_status,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    row.user_id,
                    row.tenant_id,
                    row.username,
                    row.display_name,
                    row.mobile_masked,
                    row.email_masked,
                    row.user_status,
                    row.user_raw_status,
                    _as_utc(row.user_updated_at),
                ),
            )
        for row in roles.values():
            cur.execute(
                """
                INSERT INTO kb_identity_roles(
                    role_id, tenant_id, role_code, role_name, role_status,
                    raw_status, source_updated_at, synced_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(role_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    role_code = EXCLUDED.role_code,
                    role_name = EXCLUDED.role_name,
                    role_status = EXCLUDED.role_status,
                    raw_status = EXCLUDED.raw_status,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    row.role_id,
                    row.tenant_id,
                    row.role_code,
                    row.role_name,
                    row.role_status,
                    row.role_raw_status,
                    _as_utc(row.role_updated_at),
                ),
            )
        for row in user_roles.values():
            cur.execute(
                """
                INSERT INTO kb_identity_user_roles(
                    tenant_id, user_id, role_id, relation_status,
                    source_relation_id, source_updated_at, synced_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(tenant_id, user_id, role_id) DO UPDATE SET
                    relation_status = EXCLUDED.relation_status,
                    source_relation_id = EXCLUDED.source_relation_id,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    row.tenant_id,
                    row.user_id,
                    row.role_id,
                    row.user_role_status,
                    row.user_role_id,
                    _as_utc(row.user_role_updated_at),
                ),
            )
    return {
        "tenants": len(tenants),
        "users": len(users),
        "roles": len(roles),
        "user_roles": len(user_roles),
    }


def record_sync_run(
    conn,
    *,
    source_host: str,
    source_schema: str,
    requested_limit: int,
    counts: dict[str, int],
    status: str,
    error_message: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_identity_sync_runs(
                source_host, source_schema, requested_limit,
                tenants_count, users_count, roles_count, user_roles_count,
                status, error_message, finished_at
            )
            VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                source_host,
                source_schema,
                requested_limit,
                counts.get("tenants", 0),
                counts.get("users", 0),
                counts.get("roles", 0),
                counts.get("user_roles", 0),
                status,
                error_message,
            ),
        )


def sync_identity(args: argparse.Namespace) -> dict[str, Any]:
    mysql_conn = connect_mysql(args)
    pg_conn = get_db_connection()
    try:
        pg_conn.autocommit = False
        ensure_db_schema(pg_conn)
        schema = discover_mysql_schema(mysql_conn, args.mysql_db)
        rows = fetch_source_rows(mysql_conn, schema, args.limit)
        counts = write_snapshot_rows(pg_conn, rows)
        record_sync_run(
            pg_conn,
            source_host=args.mysql_host,
            source_schema=schema,
            requested_limit=args.limit,
            counts=counts,
            status="success",
        )
        pg_conn.commit()
        return {"schema": schema, "counts": counts}
    except Exception as exc:
        pg_conn.rollback()
        try:
            record_sync_run(
                pg_conn,
                source_host=args.mysql_host,
                source_schema=args.mysql_db or "unknown",
                requested_limit=args.limit,
                counts={},
                status="failed",
                error_message=str(exc)[:1000],
            )
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()
        raise
    finally:
        mysql_conn.close()
        pg_conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync 1-5 AI base identity snapshot rows into local PostgreSQL.")
    parser.add_argument("--mysql-host", default=os.environ.get("AI_BASE_MYSQL_HOST", "192.168.2.212"))
    parser.add_argument("--mysql-port", type=int, default=int(os.environ.get("AI_BASE_MYSQL_PORT", "3306")))
    parser.add_argument("--mysql-user", default=os.environ.get("AI_BASE_MYSQL_USER", "root"))
    parser.add_argument("--mysql-password", default=os.environ.get("AI_BASE_MYSQL_PASSWORD", ""))
    parser.add_argument("--mysql-db", default=os.environ.get("AI_BASE_MYSQL_DB", ""))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("AI_BASE_IDENTITY_SYNC_LIMIT", "5")))
    parser.add_argument("--connect-timeout", type=int, default=int(os.environ.get("AI_BASE_MYSQL_CONNECT_TIMEOUT", "8")))
    parser.add_argument("--read-timeout", type=int, default=int(os.environ.get("AI_BASE_MYSQL_READ_TIMEOUT", "20")))
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 5:
        raise SystemExit("--limit must be between 1 and 5 for the quick bootstrap sync")
    if not args.mysql_password:
        raise SystemExit("AI base MySQL password is required via --mysql-password or AI_BASE_MYSQL_PASSWORD")
    args.mysql_db = args.mysql_db or None
    return args


def main(argv: list[str] | None = None) -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args(argv)
    result = sync_identity(args)
    counts = result["counts"]
    print(
        "OK synced AI base identity snapshot "
        f"schema={result['schema']} tenants={counts['tenants']} users={counts['users']} "
        f"roles={counts['roles']} user_roles={counts['user_roles']}"
    )


if __name__ == "__main__":
    main()
