from __future__ import annotations

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext, anonymous_identity


ACTIVE_OWNER_STATUS = "active"
PENDING_TRANSFER_OWNER_STATUS = "pending_transfer"


def create_knowledge_base(
    kb_id: str,
    name: str,
    description: str = "",
    default_strategy: str = "hierarchical",
    identity: IdentityContext | None = None,
) -> dict:
    """Create a knowledge base, or no-op if it already exists."""
    identity = identity or anonymous_identity()
    tenant_id = identity.tenant_id if identity.enforce_access else None
    actor_id = identity.user_id if identity.enforce_access else None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge_bases(
                    id, name, description, default_strategy,
                    tenant_id, created_by, owner_user_id, owner_status, status
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, 'active', 'active')
                ON CONFLICT(id) DO NOTHING
                """,
                (kb_id, name, description, default_strategy, tenant_id, actor_id, actor_id),
            )
            created = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return {
        "id": kb_id,
        "name": name,
        "description": description,
        "default_strategy": default_strategy,
        "tenant_id": tenant_id,
        "created_by": actor_id,
        "owner_user_id": actor_id,
        "owner_status": ACTIVE_OWNER_STATUS,
        "owner_invalid_reason": "",
        "status": "active",
        "created": created,
    }


def list_knowledge_bases(identity: IdentityContext | None = None) -> list[dict]:
    """Return visible knowledge bases with document counts."""
    identity = identity or anonymous_identity()
    where_sql, params = _access_filter_sql(identity, "kb")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT kb.id,
                       kb.name,
                       kb.description,
                       kb.default_strategy,
                       kb.tenant_id,
                       kb.created_by,
                       kb.owner_user_id,
                       kb.owner_status,
                       kb.owner_invalid_reason,
                       kb.status,
                       kb.deleted_at,
                       kb.created_at,
                       COUNT(d.id) AS doc_count,
                       COALESCE(SUM(d.chunk_count), 0) AS chunk_count,
                       COALESCE(MAX(d.updated_at), kb.created_at) AS last_updated
                FROM knowledge_bases kb
                LEFT JOIN documents d ON d.kb_id = kb.id
                {where_sql}
                GROUP BY kb.id
                ORDER BY kb.created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_knowledge_base(kb_id: str, identity: IdentityContext | None = None) -> dict | None:
    """Return a visible knowledge base by ID, or None if not found."""
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, name, description, default_strategy,
                       tenant_id, created_by, owner_user_id,
                       owner_status, owner_invalid_reason,
                       status, deleted_at, created_at
                FROM knowledge_bases
                WHERE id = %s {_where_to_and(access_sql)}
                """,
                (kb_id, *params),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return dict(zip(cols, row))


def update_knowledge_base(
    kb_id: str,
    name: str,
    description: str = "",
    default_strategy: str = "hierarchical",
    identity: IdentityContext | None = None,
) -> dict | None:
    """Update editable knowledge base metadata, or return None when missing."""
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE knowledge_bases
                SET name = %s,
                    description = %s,
                    default_strategy = %s
                WHERE id = %s {_where_to_and(access_sql)}
                RETURNING id, name, description, default_strategy,
                          tenant_id, created_by, owner_user_id,
                          owner_status, owner_invalid_reason,
                          status, deleted_at, created_at
                """,
                (name, description, default_strategy, kb_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(cols, row))


def delete_knowledge_base(kb_id: str, identity: IdentityContext | None = None) -> int:
    """Soft-delete a knowledge base. Returns rows marked deleted."""
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE knowledge_bases
                SET status = 'deleted',
                    deleted_at = NOW()
                WHERE id = %s {_where_to_and(access_sql)}
                """,
                (kb_id, *params),
            )
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def mark_knowledge_bases_pending_transfer_for_user(tenant_id: str, user_id: str, reason: str) -> int:
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"deleted", "disabled"}:
        normalized_reason = "disabled"
    if not tenant or not user:
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE knowledge_bases
                SET owner_status = %s,
                    owner_invalid_reason = %s
                WHERE tenant_id = %s
                  AND owner_user_id = %s
                  AND deleted_at IS NULL
                  AND status <> 'deleted'
                """,
                (PENDING_TRANSFER_OWNER_STATUS, normalized_reason, tenant, user),
            )
            updated = cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()


def transfer_knowledge_base_owner(
    kb_id: str,
    new_owner_user_id: str,
    identity: IdentityContext | None = None,
) -> dict | None:
    identity = identity or anonymous_identity()
    if not (identity.is_tenant_admin or identity.is_platform_admin):
        raise PermissionError("Only tenant or platform administrators can transfer knowledge base ownership")

    new_owner = str(new_owner_user_id or "").strip()
    if not new_owner:
        raise ValueError("new_owner_user_id is required")

    access_sql, params = _access_filter_sql(identity, "kb")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT kb.id, kb.tenant_id
                FROM knowledge_bases kb
                {access_sql}
                  AND kb.id = %s
                LIMIT 1
                """,
                (*params, kb_id),
            )
            kb_row = cur.fetchone()
            if not kb_row:
                return None
            kb_tenant_id = str(kb_row[1] or "")

            if kb_tenant_id:
                cur.execute(
                    """
                    SELECT 1
                    FROM kb_identity_users
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND user_status = 'active'
                    LIMIT 1
                    """,
                    (kb_tenant_id, new_owner),
                )
                if cur.fetchone() is None:
                    raise ValueError("new owner must be an active user in the same tenant")

            cur.execute(
                """
                UPDATE knowledge_bases
                SET owner_user_id = %s,
                    owner_transferred_at = NOW(),
                    owner_transferred_by = %s,
                    owner_status = %s,
                    owner_invalid_reason = NULL
                WHERE id = %s
                RETURNING id, name, description, default_strategy,
                          tenant_id, created_by, owner_user_id,
                          owner_status, owner_invalid_reason,
                          status, deleted_at, created_at
                """,
                (new_owner, identity.user_id if identity.enforce_access else None, ACTIVE_OWNER_STATUS, kb_id),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    finally:
        conn.close()

    if row is None:
        return None
    return dict(zip(cols, row))


def ensure_default_kb() -> None:
    """Ensure the default knowledge base exists."""
    create_knowledge_base("default", "Default knowledge base", "Automatically created default knowledge base")


def _access_filter_sql(identity: IdentityContext, table_alias: str) -> tuple[str, tuple[str, ...]]:
    qualifier = f"{table_alias}." if table_alias else ""
    clauses = [f"{qualifier}deleted_at IS NULL"]
    params: list[str] = []
    if identity.enforce_access and not identity.is_platform_admin:
        if identity.is_tenant_admin:
            # Legacy knowledge bases created before identity governance have no tenant owner.
            # Tenant admins may see them so the migration path does not hide existing content.
            clauses.append(f"({qualifier}tenant_id = %s OR {qualifier}tenant_id IS NULL)")
            params.append(identity.tenant_id or "")
        else:
            clauses.append(f"{qualifier}tenant_id = %s")
            params.append(identity.tenant_id or "")
            clauses.append(f"{qualifier}owner_user_id = %s")
            params.append(identity.user_id or "")
    return "WHERE " + " AND ".join(clauses), tuple(params)


def _where_to_and(where_sql: str) -> str:
    return where_sql.replace("WHERE", "AND", 1)
