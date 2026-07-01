from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db.connection import get_db_connection


TENANT_ADMIN_ROLE_CODE = "superManager"
SESSION_COOKIE_NAME = "kb_session"
DEFAULT_SESSION_TTL_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class IdentityContext:
    tenant_id: str | None = None
    user_id: str | None = None
    username: str = ""
    display_name: str = ""
    tenant_name: str = ""
    is_tenant_admin: bool = False
    is_platform_admin: bool = False
    is_authenticated: bool = False
    source: str = "anonymous"
    role_codes: tuple[str, ...] = ()

    @property
    def enforce_access(self) -> bool:
        return self.is_authenticated and bool(self.tenant_id and self.user_id)


def anonymous_identity() -> IdentityContext:
    return IdentityContext()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint_credential(value: str) -> str:
    return hash_secret(value)[:32]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_status(value: Any) -> str:
    status = str(value or "active").strip().lower()
    if status in {"active", "enabled", "normal", "1", "true"}:
        return "active"
    if status in {"disabled", "disable", "inactive", "deleted", "removed", "resigned", "frozen", "0", "false"}:
        return status
    return status or "inactive"


def _flag_is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _has_flag(item: dict[str, Any], *keys: str) -> bool:
    return any(key in item and item.get(key) is not None for key in keys)


def _normalize_user_status(user: dict[str, Any]) -> str:
    if _flag_is_true(user.get("deleted")) or _flag_is_true(user.get("is_deleted")) or _flag_is_true(user.get("isDeleted")):
        return "deleted"
    if _flag_is_true(user.get("disabled")) or _flag_is_true(user.get("is_disabled")) or _flag_is_true(user.get("isDisabled")):
        return "disabled"
    status_value = user.get("status") or user.get("user_status")
    if status_value in (None, "") and (
        _has_flag(user, "deleted", "is_deleted", "isDeleted") or _has_flag(user, "disabled", "is_disabled", "isDisabled")
    ):
        return "active"
    return _normalize_status(status_value)


def _raw_user_status(user: dict[str, Any]) -> str:
    status_value = user.get("status") or user.get("user_status")
    if status_value not in (None, ""):
        return str(status_value)
    if _flag_is_true(user.get("deleted")) or _flag_is_true(user.get("is_deleted")) or _flag_is_true(user.get("isDeleted")):
        return "deleted"
    if _flag_is_true(user.get("disabled")) or _flag_is_true(user.get("is_disabled")) or _flag_is_true(user.get("isDisabled")):
        return "disabled"
    if _has_flag(user, "deleted", "is_deleted", "isDeleted") or _has_flag(user, "disabled", "is_disabled", "isDisabled"):
        return "active"
    return "active"


def _mark_knowledge_bases_pending_transfer(cur, tenant_id: str, user_id: str, reason: str) -> None:
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"deleted", "disabled"}:
        normalized_reason = "disabled"
    if not user:
        return
    cur.execute(
        """
        UPDATE knowledge_bases
        SET owner_status = 'pending_transfer',
            owner_invalid_reason = %s
        WHERE deleted_at IS NULL
          AND status <> 'deleted'
          AND (%s = '' OR tenant_id = %s)
          AND (
              owner_user_id = %s
              OR (owner_user_id IS NULL AND created_by = %s)
          )
        """,
        (normalized_reason, tenant, tenant, user, user),
    )


def _source_timestamp(item: dict[str, Any]) -> Any:
    return (
        item.get("updated_at")
        or item.get("changed_at")
        or item.get("source_updated_at")
        or item.get("updateTime")
        or item.get("updatedTime")
        or item.get("updatedAt")
        or item.get("changedAt")
        or item.get("sourceUpdatedAt")
    )


def _as_dict_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    return []


def _first_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        items = _as_dict_list(payload.get(key))
        if items:
            return items
    return []


def _normalize_identity_delta_record(item: dict[str, Any], mapping: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    normalized = dict(item)
    for canonical_key, aliases in mapping.items():
        if normalized.get(canonical_key) not in (None, ""):
            continue
        for alias in aliases:
            if normalized.get(alias) not in (None, ""):
                normalized[canonical_key] = normalized[alias]
                break
    return normalized


def _normalize_deleted_events(raw_deleted: Any) -> list[dict[str, Any]]:
    if not raw_deleted:
        return []
    if isinstance(raw_deleted, list):
        return [dict(item) for item in raw_deleted if isinstance(item, dict)]
    if not isinstance(raw_deleted, dict):
        return []

    events: list[dict[str, Any]] = []
    grouped_keys = {
        "tenant_ids": "tenant",
        "tenantIds": "tenant",
        "user_ids": "user",
        "userIds": "user",
        "role_ids": "role",
        "roleIds": "role",
        "user_role_relation_ids": "user_role",
        "userRoleRelationIds": "user_role",
        "userRoleIds": "user_role",
    }
    for key, entity_type in grouped_keys.items():
        for entity_id in raw_deleted.get(key) or []:
            events.append({"entity_type": entity_type, "entity_id": entity_id})
    return events


def _apply_deleted_events(cur, deleted_events: list[dict[str, Any]]) -> None:
    for event in deleted_events:
        entity_type = str(event.get("entity_type") or event.get("entityType") or event.get("type") or "").strip()
        entity_id = str(event.get("entity_id") or event.get("entityId") or event.get("id") or "").strip()
        tenant_id = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
        user_id = str(event.get("user_id") or event.get("userId") or "").strip()
        role_id = str(event.get("role_id") or event.get("roleId") or "").strip()

        if entity_type == "tenant" and entity_id:
            cur.execute(
                """
                UPDATE kb_identity_tenants
                SET tenant_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE tenant_id = %s
                """,
                (_source_timestamp(event), entity_id),
            )
        elif entity_type == "user" and entity_id:
            cur.execute(
                """
                UPDATE kb_identity_users
                SET user_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE user_id = %s
                  AND (%s = '' OR tenant_id = %s)
                """,
                (_source_timestamp(event), entity_id, tenant_id, tenant_id),
            )
        elif entity_type == "role" and entity_id:
            cur.execute(
                """
                UPDATE kb_identity_roles
                SET role_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE role_id = %s
                  AND (%s = '' OR tenant_id = %s OR tenant_id IS NULL)
                """,
                (_source_timestamp(event), entity_id, tenant_id, tenant_id),
            )
        elif entity_type == "user_role":
            if tenant_id and user_id and role_id:
                cur.execute(
                    """
                    UPDATE kb_identity_user_roles
                    SET relation_status = 'deleted',
                        source_updated_at = COALESCE(%s, source_updated_at),
                        synced_at = NOW()
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND role_id = %s
                    """,
                    (_source_timestamp(event), tenant_id, user_id, role_id),
                )
            elif entity_id:
                cur.execute(
                    """
                    UPDATE kb_identity_user_roles
                    SET relation_status = 'deleted',
                        source_updated_at = COALESCE(%s, source_updated_at),
                        synced_at = NOW()
                    WHERE source_relation_id = %s
                    """,
                    (_source_timestamp(event), entity_id),
                )


def _extract_identity_summary(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    tenant = dict(summary.get("tenant") or {})
    user = dict(summary.get("user") or {})
    roles = list(summary.get("roles") or [])
    user_roles = list(summary.get("user_roles") or [])
    if not tenant.get("tenant_id") or not user.get("user_id"):
        raise ValueError("SSO identity summary requires tenant.tenant_id and user.user_id")
    user.setdefault("tenant_id", tenant.get("tenant_id"))
    return tenant, user, roles, user_roles


def upsert_identity_snapshot_from_summary(summary: dict[str, Any]) -> IdentityContext:
    tenant, user, roles, user_roles = _extract_identity_summary(summary)
    tenant_id = str(tenant["tenant_id"])
    user_id = str(user["user_id"])
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_identity_tenants (
                    tenant_id, tenant_name, tenant_code, tenant_status, raw_status,
                    contact_name, contact_mobile_masked, source_updated_at, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL, NULL, NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                    tenant_name = EXCLUDED.tenant_name,
                    tenant_code = EXCLUDED.tenant_code,
                    tenant_status = EXCLUDED.tenant_status,
                    raw_status = EXCLUDED.raw_status,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    tenant_id,
                    str(tenant.get("tenant_name") or tenant_id),
                    tenant.get("tenant_code"),
                    _normalize_status(tenant.get("status") or tenant.get("tenant_status")),
                    str(tenant.get("status") or tenant.get("tenant_status") or "active"),
                ),
            )
            cur.execute(
                """
                INSERT INTO kb_identity_users (
                    user_id, tenant_id, username, display_name, mobile_masked, email_masked,
                    user_status, raw_status, source_updated_at, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET
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
                    user_id,
                    tenant_id,
                    str(user.get("username") or user_id),
                    user.get("display_name") or user.get("displayName") or user.get("username") or user_id,
                    user.get("mobile_masked") or user.get("mobileMasked"),
                    user.get("email_masked") or user.get("emailMasked"),
                    _normalize_user_status(user),
                    _raw_user_status(user),
                ),
            )
            user_status = _normalize_user_status(user)
            if user_status in {"deleted", "disabled"}:
                _mark_knowledge_bases_pending_transfer(cur, tenant_id, user_id, user_status)

            active_role_ids: set[str] = set()
            for role in roles:
                role_id = str(role.get("role_id") or role.get("id") or role.get("role_code") or "")
                role_code = str(role.get("role_code") or role.get("code") or "")
                if not role_id or not role_code:
                    continue
                active_role_ids.add(role_id)
                cur.execute(
                    """
                    INSERT INTO kb_identity_roles (
                        role_id, tenant_id, role_code, role_name, role_status, raw_status,
                        source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (role_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        role_code = EXCLUDED.role_code,
                        role_name = EXCLUDED.role_name,
                        role_status = EXCLUDED.role_status,
                        raw_status = EXCLUDED.raw_status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        role_id,
                        role.get("tenant_id") or tenant_id,
                        role_code,
                        str(role.get("role_name") or role.get("name") or role_code),
                        _normalize_status(role.get("status") or role.get("role_status")),
                        str(role.get("status") or role.get("role_status") or "active"),
                    ),
                )

            if user_roles:
                for relation in user_roles:
                    rel_user_id = str(relation.get("user_id") or user_id)
                    role_id = str(relation.get("role_id") or "")
                    rel_tenant_id = str(relation.get("tenant_id") or tenant_id)
                    if rel_user_id != user_id or rel_tenant_id != tenant_id or not role_id:
                        continue
                    active_role_ids.add(role_id)
                    cur.execute(
                        """
                        INSERT INTO kb_identity_user_roles (
                            tenant_id, user_id, role_id, relation_status,
                            source_relation_id, source_updated_at, synced_at
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                            relation_status = EXCLUDED.relation_status,
                            source_relation_id = EXCLUDED.source_relation_id,
                            source_updated_at = EXCLUDED.source_updated_at,
                            synced_at = NOW()
                        """,
                        (
                            tenant_id,
                            user_id,
                            role_id,
                            _normalize_status(relation.get("status") or relation.get("relation_status")),
                            relation.get("source_relation_id") or relation.get("id"),
                        ),
                    )
            else:
                for role_id in active_role_ids:
                    cur.execute(
                        """
                        INSERT INTO kb_identity_user_roles (
                            tenant_id, user_id, role_id, relation_status,
                            source_relation_id, source_updated_at, synced_at
                        )
                        VALUES (%s, %s, %s, 'active', NULL, NOW(), NOW())
                        ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                            relation_status = 'active',
                            source_updated_at = EXCLUDED.source_updated_at,
                            synced_at = NOW()
                        """,
                        (tenant_id, user_id, role_id),
                    )
            conn.commit()
    finally:
        conn.close()

    identity = resolve_identity_snapshot(tenant_id, user_id)
    if identity is None:
        raise ValueError("SSO identity summary did not resolve to an active snapshot")
    return identity


def upsert_identity_delta_snapshot(delta: dict[str, Any]) -> dict[str, int]:
    tenants = [
        _normalize_identity_delta_record(
            item,
            {
                "tenant_id": ("tenantId", "id"),
                "tenant_name": ("tenantName", "name"),
                "tenant_code": ("tenantCode", "code"),
                "tenant_status": ("tenantStatus",),
                "contact_name": ("contactName",),
                "contact_mobile_masked": ("contactMobileMasked",),
            },
        )
        for item in _first_list(delta, "tenants", "tenantList", "tenant_list")
    ]
    users = [
        _normalize_identity_delta_record(
            item,
            {
                "user_id": ("userId", "id"),
                "tenant_id": ("tenantId",),
                "user_name": ("userName",),
                "display_name": ("displayName", "nickName"),
                "mobile_masked": ("mobileMasked",),
                "email_masked": ("emailMasked",),
                "user_status": ("userStatus",),
                "is_deleted": ("isDeleted",),
                "is_disabled": ("isDisabled",),
            },
        )
        for item in _first_list(delta, "users", "userList", "user_list")
    ]
    roles = [
        _normalize_identity_delta_record(
            item,
            {
                "role_id": ("roleId", "id"),
                "tenant_id": ("tenantId",),
                "role_code": ("roleCode", "code"),
                "role_name": ("roleName", "name"),
                "role_status": ("roleStatus",),
            },
        )
        for item in _first_list(delta, "roles", "roleList", "role_list")
    ]
    user_roles = [
        _normalize_identity_delta_record(
            item,
            {
                "relation_id": ("relationId", "id"),
                "tenant_id": ("tenantId",),
                "user_id": ("userId",),
                "role_id": ("roleId",),
                "relation_status": ("relationStatus",),
                "source_relation_id": ("sourceRelationId",),
            },
        )
        for item in _first_list(delta, "user_roles", "userRoles", "userRoleList", "user_role_list")
    ]
    deleted = _normalize_deleted_events(delta.get("deleted") or delta.get("deletedList") or delta.get("deleted_list"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for tenant in tenants:
                tenant_id = str(tenant.get("tenant_id") or "")
                if not tenant_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO kb_identity_tenants (
                        tenant_id, tenant_name, tenant_code, tenant_status, raw_status,
                        contact_name, contact_mobile_masked, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tenant_id) DO UPDATE SET
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
                        tenant_id,
                        str(tenant.get("tenant_name") or tenant.get("name") or tenant_id),
                        tenant.get("tenant_code") or tenant.get("code"),
                        _normalize_status(tenant.get("status") or tenant.get("tenant_status")),
                        str(tenant.get("status") or tenant.get("tenant_status") or "active"),
                        tenant.get("contact_name"),
                        tenant.get("contact_mobile_masked"),
                        _source_timestamp(tenant),
                    ),
                )

            for user in users:
                user_id = str(user.get("user_id") or "")
                tenant_id = str(user.get("tenant_id") or "")
                if not user_id or not tenant_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO kb_identity_users (
                        user_id, tenant_id, username, display_name, mobile_masked, email_masked,
                        user_status, raw_status, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
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
                        user_id,
                        tenant_id,
                        str(user.get("username") or user.get("user_name") or user_id),
                        user.get("display_name") or user.get("displayName") or user.get("nick_name") or user.get("username") or user_id,
                        user.get("mobile_masked") or user.get("mobileMasked"),
                        user.get("email_masked") or user.get("emailMasked"),
                        _normalize_user_status(user),
                        _raw_user_status(user),
                        _source_timestamp(user),
                    ),
                )
                user_status = _normalize_user_status(user)
                if user_status in {"deleted", "disabled"}:
                    _mark_knowledge_bases_pending_transfer(cur, tenant_id, user_id, user_status)

            for role in roles:
                role_id = str(role.get("role_id") or role.get("id") or "")
                role_code = str(role.get("role_code") or role.get("code") or "")
                if not role_id or not role_code:
                    continue
                tenant_id = role.get("tenant_id")
                cur.execute(
                    """
                    INSERT INTO kb_identity_roles (
                        role_id, tenant_id, role_code, role_name, role_status, raw_status,
                        source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (role_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        role_code = EXCLUDED.role_code,
                        role_name = EXCLUDED.role_name,
                        role_status = EXCLUDED.role_status,
                        raw_status = EXCLUDED.raw_status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        role_id,
                        str(tenant_id) if tenant_id is not None else None,
                        role_code,
                        str(role.get("role_name") or role.get("name") or role_code),
                        _normalize_status(role.get("status") or role.get("role_status")),
                        str(role.get("status") or role.get("role_status") or "active"),
                        _source_timestamp(role),
                    ),
                )

            for relation in user_roles:
                tenant_id = str(relation.get("tenant_id") or "")
                user_id = str(relation.get("user_id") or "")
                role_id = str(relation.get("role_id") or "")
                if not tenant_id or not user_id or not role_id:
                    continue
                status = _normalize_status(relation.get("status") or relation.get("relation_status"))
                cur.execute(
                    """
                    INSERT INTO kb_identity_user_roles (
                        tenant_id, user_id, role_id, relation_status,
                        source_relation_id, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                        relation_status = EXCLUDED.relation_status,
                        source_relation_id = EXCLUDED.source_relation_id,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        tenant_id,
                        user_id,
                        role_id,
                        status,
                        relation.get("source_relation_id") or relation.get("relation_id") or relation.get("id"),
                        _source_timestamp(relation),
                    ),
                )

            _apply_deleted_events(cur, deleted)
            for event in deleted:
                entity_type = str(event.get("entity_type") or event.get("entityType") or event.get("type") or "").strip()
                entity_id = str(event.get("entity_id") or event.get("entityId") or event.get("id") or "").strip()
                tenant_id = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
                if entity_type == "user" and entity_id:
                    _mark_knowledge_bases_pending_transfer(cur, tenant_id, entity_id, "deleted")
        conn.commit()
    finally:
        conn.close()

    return {
        "tenants": len(tenants),
        "users": len(users),
        "roles": len(roles),
        "user_roles": len(user_roles),
        "deleted": len(deleted),
    }


def _identity_from_session_row(row) -> IdentityContext:
    role_codes = tuple(str(item) for item in (row[6] or []))
    return IdentityContext(
        tenant_id=str(row[0]),
        user_id=str(row[1]),
        username=row[2] or "",
        display_name=row[3] or "",
        tenant_name=row[4] or "",
        is_tenant_admin=bool(row[5]),
        role_codes=role_codes,
        is_authenticated=True,
        source=str(row[7] or "kb_session"),
    )


def create_auth_session(
    identity: IdentityContext,
    *,
    auth_source: str,
    credential_fingerprint: str | None = None,
    identity_snapshot_version: str | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> dict[str, Any]:
    if not identity.enforce_access:
        raise ValueError("Cannot create a session for anonymous identity")

    session_token = secrets.token_urlsafe(32)
    session_hash = hash_secret(session_token)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=max(60, int(ttl_seconds)))
    role_codes = list(identity.role_codes)
    if identity.is_tenant_admin and TENANT_ADMIN_ROLE_CODE not in role_codes:
        role_codes.append(TENANT_ADMIN_ROLE_CODE)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_auth_sessions (
                    session_hash, tenant_id, user_id, username, display_name, tenant_name,
                    role_codes, is_tenant_admin, auth_source, credential_fingerprint,
                    identity_snapshot_version, issued_at, expires_at, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_hash,
                    identity.tenant_id,
                    identity.user_id,
                    identity.username,
                    identity.display_name,
                    identity.tenant_name,
                    json.dumps(role_codes, ensure_ascii=False),
                    identity.is_tenant_admin,
                    auth_source,
                    credential_fingerprint,
                    identity_snapshot_version,
                    issued_at,
                    expires_at,
                    issued_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "sessionToken": session_token,
        "expiresAt": expires_at.isoformat(),
        "identity": identity_to_payload(identity),
    }


def resolve_auth_session(session_token: str) -> IdentityContext | None:
    token = (session_token or "").strip()
    if not token:
        return None
    session_hash = hash_secret(token)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tenant_id, user_id, username, display_name, tenant_name,
                       is_tenant_admin, role_codes, auth_source
                FROM kb_auth_sessions
                WHERE session_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                LIMIT 1
                """,
                (session_hash,),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE kb_auth_sessions SET last_seen_at = NOW() WHERE session_hash = %s",
                    (session_hash,),
                )
                conn.commit()
    finally:
        conn.close()
    if not row:
        return None
    return _identity_from_session_row(row)


def revoke_auth_session(session_token: str) -> bool:
    token = (session_token or "").strip()
    if not token:
        return False
    session_hash = hash_secret(token)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_auth_sessions
                SET revoked_at = NOW()
                WHERE session_hash = %s
                  AND revoked_at IS NULL
                """,
                (session_hash,),
            )
            revoked = cur.rowcount > 0
        conn.commit()
        return revoked
    finally:
        conn.close()


def revoke_auth_sessions_for_identity(tenant_id: str, user_id: str | None = None) -> int:
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    if not tenant:
        return 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if user:
                cur.execute(
                    """
                    UPDATE kb_auth_sessions
                    SET revoked_at = NOW()
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    """,
                    (tenant, user),
                )
            else:
                cur.execute(
                    """
                    UPDATE kb_auth_sessions
                    SET revoked_at = NOW()
                    WHERE tenant_id = %s
                      AND revoked_at IS NULL
                    """,
                    (tenant,),
                )
            revoked = int(cur.rowcount or 0)
        conn.commit()
        return revoked
    finally:
        conn.close()


def latest_identity_snapshot_synced_at(tenant_id: str, user_id: str) -> datetime | None:
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    if not tenant or not user:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(
                           GREATEST(
                               COALESCE(u.synced_at, 'epoch'::timestamptz),
                               COALESCE(t.synced_at, 'epoch'::timestamptz),
                               COALESCE(r.synced_at, 'epoch'::timestamptz),
                               COALESCE(ur.synced_at, 'epoch'::timestamptz)
                           )
                       ) AS synced_at
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                LEFT JOIN kb_identity_user_roles ur
                  ON ur.tenant_id = u.tenant_id
                 AND ur.user_id = u.user_id
                 AND ur.relation_status = 'active'
                LEFT JOIN kb_identity_roles r
                  ON r.role_id = ur.role_id
                 AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                 AND r.role_status = 'active'
                WHERE u.tenant_id = %s
                  AND u.user_id = %s
                  AND u.user_status = 'active'
                  AND t.tenant_status = 'active'
                """,
                (tenant, user),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return _as_utc(row[0])


def get_latest_identity_sync_watermark() -> str | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max_updated_at
                FROM kb_identity_sync_runs
                WHERE sync_mode = 'http_delta'
                  AND status = 'success'
                  AND COALESCE(max_updated_at, '') <> ''
                ORDER BY finished_at DESC NULLS LAST, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def list_identity_sync_runs(limit: int = 50) -> list[dict[str, Any]]:
    capped_limit = max(1, min(int(limit or 50), 200))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       sync_mode,
                       source_host,
                       source_schema,
                       requested_limit,
                       tenants_count,
                       users_count,
                       roles_count,
                       user_roles_count,
                       deleted_count,
                       last_sync_at,
                       max_updated_at,
                       snapshot_version,
                       has_more,
                       status,
                       error_message,
                       started_at,
                       finished_at
                FROM kb_identity_sync_runs
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, id DESC
                LIMIT %s
                """,
                (capped_limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": int(row[0]),
            "syncMode": str(row[1] or ""),
            "sourceHost": row[2] or "",
            "sourceSchema": row[3] or "",
            "requestedLimit": int(row[4] or 0),
            "tenantsCount": int(row[5] or 0),
            "usersCount": int(row[6] or 0),
            "rolesCount": int(row[7] or 0),
            "userRolesCount": int(row[8] or 0),
            "deletedCount": int(row[9] or 0),
            "lastSyncAt": row[10] or "",
            "maxUpdatedAt": row[11] or "",
            "snapshotVersion": row[12] or "",
            "hasMore": bool(row[13]),
            "status": str(row[14] or ""),
            "errorMessage": row[15] or "",
            "startedAt": row[16].isoformat() if hasattr(row[16], "isoformat") else str(row[16] or ""),
            "finishedAt": row[17].isoformat() if hasattr(row[17], "isoformat") else str(row[17] or ""),
        }
        for row in rows
    ]


def record_identity_sync_run(
    *,
    sync_mode: str,
    source_host: str,
    requested_limit: int,
    counts: dict[str, int],
    status: str,
    source_schema: str | None = None,
    error_message: str | None = None,
    last_sync_at: str | None = None,
    max_updated_at: str | None = None,
    snapshot_version: str | None = None,
    has_more: bool = False,
) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_identity_sync_runs(
                    sync_mode, source_host, source_schema, requested_limit,
                    tenants_count, users_count, roles_count, user_roles_count, deleted_count,
                    last_sync_at, max_updated_at, snapshot_version, has_more,
                    status, error_message, finished_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    sync_mode,
                    source_host,
                    source_schema,
                    int(requested_limit),
                    int(counts.get("tenants", 0)),
                    int(counts.get("users", 0)),
                    int(counts.get("roles", 0)),
                    int(counts.get("user_roles", 0)),
                    int(counts.get("deleted", 0)),
                    last_sync_at,
                    max_updated_at,
                    snapshot_version,
                    bool(has_more),
                    status,
                    error_message[:1000] if error_message else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def mark_sso_credential_used(
    credential_fingerprint: str,
    *,
    credential_type: str,
    tenant_id: str | None,
    user_id: str | None,
    expires_at: datetime | None = None,
) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_sso_used_credentials (
                    credential_fingerprint, credential_type, tenant_id, user_id, expires_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (credential_fingerprint) DO NOTHING
                """,
                (credential_fingerprint, credential_type, tenant_id, user_id, _as_utc(expires_at)),
            )
            inserted = cur.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def is_sso_credential_used(credential_fingerprint: str) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM kb_sso_used_credentials
                WHERE credential_fingerprint = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                (credential_fingerprint,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def identity_to_payload(identity: IdentityContext) -> dict[str, Any]:
    role_codes = list(identity.role_codes)
    if identity.is_tenant_admin and TENANT_ADMIN_ROLE_CODE not in role_codes:
        role_codes.append(TENANT_ADMIN_ROLE_CODE)
    return {
        "tenantId": identity.tenant_id,
        "userId": identity.user_id,
        "username": identity.username,
        "displayName": identity.display_name or identity.username or identity.user_id,
        "tenantName": identity.tenant_name,
        "roleCodes": role_codes,
        "isTenantAdmin": identity.is_tenant_admin,
        "source": identity.source,
    }


def resolve_identity_snapshot(tenant_id: str, user_id: str) -> IdentityContext | None:
    """Resolve a user from the read-only AI base identity snapshot."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id,
                       u.tenant_id,
                       u.username,
                       u.display_name,
                       t.tenant_name,
                       EXISTS (
                           SELECT 1
                           FROM kb_identity_user_roles ur
                           JOIN kb_identity_roles r
                             ON r.role_id = ur.role_id
                            AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                           WHERE ur.tenant_id = u.tenant_id
                             AND ur.user_id = u.user_id
                             AND ur.relation_status = 'active'
                             AND r.role_status = 'active'
                             AND r.role_code = %s
                       ) AS is_tenant_admin
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                WHERE u.tenant_id = %s
                  AND u.user_id = %s
                  AND u.user_status = 'active'
                  AND t.tenant_status = 'active'
                LIMIT 1
                """,
                (TENANT_ADMIN_ROLE_CODE, tenant_id, user_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    role_codes = [TENANT_ADMIN_ROLE_CODE] if bool(row[5]) else []
    return IdentityContext(
        user_id=str(row[0]),
        tenant_id=str(row[1]),
        username=row[2] or "",
        display_name=row[3] or "",
        tenant_name=row[4] or "",
        is_tenant_admin=bool(row[5]),
        is_platform_admin=False,
        is_authenticated=True,
        source="identity_snapshot",
        role_codes=tuple(role_codes),
    )


def list_identity_snapshot_users(limit: int = 10) -> list[dict]:
    """Return sanitized users from the local AI base identity snapshot."""
    capped_limit = max(1, min(int(limit), 1_000_000))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.tenant_id,
                       u.user_id,
                       u.username,
                       u.display_name,
                       t.tenant_name,
                       COALESCE(
                           ARRAY_AGG(DISTINCT r.role_code)
                             FILTER (
                               WHERE r.role_code IS NOT NULL
                                 AND r.role_status = 'active'
                                 AND ur.relation_status = 'active'
                             ),
                           ARRAY[]::text[]
                       ) AS role_codes,
                       COALESCE(
                           ARRAY_AGG(DISTINCT r.role_name)
                             FILTER (
                               WHERE r.role_name IS NOT NULL
                                 AND r.role_status = 'active'
                                 AND ur.relation_status = 'active'
                             ),
                           ARRAY[]::text[]
                       ) AS role_names,
                       COALESCE(
                           BOOL_OR(
                               r.role_code = %s
                               AND r.role_status = 'active'
                               AND ur.relation_status = 'active'
                           ),
                           FALSE
                       ) AS is_tenant_admin,
                       MAX(
                           GREATEST(
                               COALESCE(u.synced_at, 'epoch'::timestamptz),
                               COALESCE(t.synced_at, 'epoch'::timestamptz),
                               COALESCE(r.synced_at, 'epoch'::timestamptz),
                               COALESCE(ur.synced_at, 'epoch'::timestamptz)
                           )
                       ) AS synced_at
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                LEFT JOIN kb_identity_user_roles ur
                  ON ur.tenant_id = u.tenant_id
                 AND ur.user_id = u.user_id
                LEFT JOIN kb_identity_roles r
                  ON r.role_id = ur.role_id
                 AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                WHERE u.user_status = 'active'
                  AND t.tenant_status = 'active'
                GROUP BY u.tenant_id, u.user_id, u.username, u.display_name, t.tenant_name
                ORDER BY u.tenant_id, u.user_id
                LIMIT %s
                """,
                (TENANT_ADMIN_ROLE_CODE, capped_limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "tenantId": str(row[0]),
            "userId": str(row[1]),
            "username": row[2] or "",
            "displayName": row[3] or row[2] or str(row[1]),
            "tenantName": row[4] or "",
            "roleCodes": list(row[5] or []),
            "roleNames": list(row[6] or []),
            "ragRole": "租户管理员" if bool(row[7]) else "普通用户",
            "isTenantAdmin": bool(row[7]),
            "source": "identity_snapshot",
            "syncedAt": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8] or ""),
        }
        for row in rows
    ]


def clear_identity_snapshot_data() -> dict[str, int]:
    """Remove local AI base identity snapshot tables and sync run records."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            deleted: dict[str, int] = {}
            for table, key in (
                ("kb_identity_user_roles", "userRoles"),
                ("kb_identity_users", "users"),
                ("kb_identity_roles", "roles"),
                ("kb_identity_tenants", "tenants"),
                ("kb_identity_sync_runs", "syncRuns"),
            ):
                cur.execute(f"DELETE FROM {table}")
                deleted[key] = int(cur.rowcount or 0)
        conn.commit()
        return deleted
    finally:
        conn.close()
