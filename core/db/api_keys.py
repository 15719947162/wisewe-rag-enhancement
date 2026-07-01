from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext, anonymous_identity
from core.db.init_db import ensure_db_schema

DEFAULT_CAPABILITIES = ("rag.query", "rag.graph_query")
MAX_BOUND_KB_IDS = 20
ACTIVE_STATUS = "active"
DISABLED_STATUS = "disabled"
DELETED_STATUS = "deleted"
APP_STATUSES = {ACTIVE_STATUS, DISABLED_STATUS}


@dataclass(frozen=True)
class ApiKeyAuthResult:
    identity: IdentityContext
    api_key_id: str
    capabilities: tuple[str, ...]
    kb_ids: tuple[str, ...]
    require_signature: bool = True
    allowed_ips: tuple[str, ...] = ()
    rpm_limit: int = 0
    daily_request_limit: int = 0
    app_id: str | None = None


@dataclass(frozen=True)
class ApiKeySignaturePayload:
    method: str
    path: str
    body: bytes
    timestamp: str | None
    nonce: str | None
    body_sha256: str | None
    signature: str | None


class ApiKeyError(ValueError):
    def __init__(self, code: str, message: str, *, api_key_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.api_key_id = api_key_id


def create_api_key(
    *,
    name: str,
    kb_ids: list[str],
    capabilities: list[str] | None = None,
    note: str = "",
    require_signature: bool = True,
    allowed_ips: list[str] | None = None,
    rpm_limit: int = 0,
    daily_request_limit: int = 0,
    app_id: str | None = None,
    expires_at: datetime | None = None,
    identity: IdentityContext | None = None,
) -> dict[str, Any]:
    identity = identity or anonymous_identity()
    normalized_kb_ids = _normalize_kb_ids(kb_ids)
    normalized_capabilities = _normalize_capabilities(capabilities)
    normalized_allowed_ips = _normalize_allowed_ips(allowed_ips or [])
    key_id = _new_key_id()
    secret = _new_secret()
    plain_key = _format_plain_key(key_id, secret)
    key_hash = _hash_key(plain_key)
    key_prefix = plain_key[:16]
    key_suffix = plain_key[-8:]

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_api_keys(
                    id, app_id, name, tenant_id, created_by, key_hash, key_prefix, key_suffix,
                    status, kb_ids, capabilities, require_signature, allowed_ips,
                    rpm_limit, daily_request_limit, note, expires_at
                )
                VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    'active', %s::jsonb, %s::jsonb, %s, %s::jsonb,
                    %s, %s, %s, %s
                )
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (
                    key_id,
                    (app_id or "").strip() or None,
                    name.strip(),
                    identity.tenant_id if identity.enforce_access else None,
                    identity.user_id if identity.enforce_access else None,
                    key_hash,
                    key_prefix,
                    key_suffix,
                    _json_array(normalized_kb_ids),
                    _json_array(normalized_capabilities),
                    bool(require_signature),
                    _json_array(normalized_allowed_ips),
                    _normalize_non_negative_int(rpm_limit),
                    _normalize_non_negative_int(daily_request_limit),
                    note.strip(),
                    expires_at,
                ),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description]
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()

    return {**_row_to_payload(row, cols), "plainKey": plain_key}


def list_api_keys(identity: IdentityContext | None = None) -> list[dict[str, Any]]:
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                       status, kb_ids, capabilities, require_signature, allowed_ips,
                       rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                       created_at, updated_at, deleted_at
                FROM kb_api_keys
                {where}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [_row_to_payload(row, cols) for row in rows]


def create_openapi_app(
    *,
    name: str,
    note: str = "",
    identity: IdentityContext | None = None,
) -> dict[str, Any]:
    identity = identity or anonymous_identity()
    app_id = _new_app_id()
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_openapi_apps(id, name, tenant_id, owner_user_id, status, note)
                VALUES(%s, %s, %s, %s, 'active', %s)
                RETURNING id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                """,
                (
                    app_id,
                    name.strip(),
                    identity.tenant_id if identity.enforce_access else None,
                    identity.user_id if identity.enforce_access else None,
                    note.strip(),
                ),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description]
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return _app_row_to_payload(row, cols)


def list_openapi_apps(identity: IdentityContext | None = None) -> list[dict[str, Any]]:
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity)
    app_where = where.replace("deleted_at IS NULL", "deleted_at IS NULL")
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                FROM kb_openapi_apps
                {app_where}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [_app_row_to_payload(row, cols) for row in rows]


def update_openapi_app(
    app_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    note: str | None = None,
    identity: IdentityContext | None = None,
) -> dict[str, Any] | None:
    identity = identity or anonymous_identity()
    assignments: list[str] = []
    values: list[Any] = []
    if name is not None:
        assignments.append("name = %s")
        values.append(name.strip())
    if status is not None:
        normalized_status = status.strip().lower()
        if normalized_status not in APP_STATUSES:
            raise ValueError("status must be active or disabled")
        assignments.append("status = %s")
        values.append(normalized_status)
    if note is not None:
        assignments.append("note = %s")
        values.append(note.strip())
    if not assignments:
        matches = [item for item in list_openapi_apps(identity) if item["id"] == app_id]
        return matches[0] if matches else None

    assignments.append("updated_at = NOW()")
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_openapi_apps
                SET {", ".join(assignments)}
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                """,
                (*values, app_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return _app_row_to_payload(row, cols) if row else None


def delete_openapi_app(app_id: str, identity: IdentityContext | None = None) -> bool:
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_openapi_apps
                SET status = 'deleted',
                    deleted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                """,
                (app_id, *params),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return deleted


def update_api_key(
    key_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    kb_ids: list[str] | None = None,
    capabilities: list[str] | None = None,
    require_signature: bool | None = None,
    allowed_ips: list[str] | None = None,
    rpm_limit: int | None = None,
    daily_request_limit: int | None = None,
    app_id: str | None = None,
    app_id_provided: bool = False,
    note: str | None = None,
    expires_at: datetime | None = None,
    expires_at_provided: bool = False,
    identity: IdentityContext | None = None,
) -> dict[str, Any] | None:
    identity = identity or anonymous_identity()
    assignments: list[str] = []
    values: list[Any] = []

    if name is not None:
        assignments.append("name = %s")
        values.append(name.strip())
    if status is not None:
        normalized_status = status.strip().lower()
        if normalized_status not in {ACTIVE_STATUS, DISABLED_STATUS}:
            raise ValueError("status must be active or disabled")
        assignments.append("status = %s")
        values.append(normalized_status)
    if kb_ids is not None:
        assignments.append("kb_ids = %s::jsonb")
        values.append(_json_array(_normalize_kb_ids(kb_ids)))
    if capabilities is not None:
        assignments.append("capabilities = %s::jsonb")
        values.append(_json_array(_normalize_capabilities(capabilities)))
    if require_signature is not None:
        assignments.append("require_signature = %s")
        values.append(bool(require_signature))
    if allowed_ips is not None:
        assignments.append("allowed_ips = %s::jsonb")
        values.append(_json_array(_normalize_allowed_ips(allowed_ips)))
    if rpm_limit is not None:
        assignments.append("rpm_limit = %s")
        values.append(_normalize_non_negative_int(rpm_limit))
    if daily_request_limit is not None:
        assignments.append("daily_request_limit = %s")
        values.append(_normalize_non_negative_int(daily_request_limit))
    if app_id_provided:
        assignments.append("app_id = %s")
        values.append((app_id or "").strip() or None)
    if note is not None:
        assignments.append("note = %s")
        values.append(note.strip())
    if expires_at_provided:
        assignments.append("expires_at = %s")
        values.append(expires_at)

    if not assignments:
        return get_api_key(key_id, identity)

    assignments.append("updated_at = NOW()")
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_api_keys
                SET {", ".join(assignments)}
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (*values, key_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_payload(row, cols)


def rotate_api_key(key_id: str, identity: IdentityContext | None = None) -> dict[str, Any] | None:
    identity = identity or anonymous_identity()
    secret = _new_secret()
    plain_key = _format_plain_key(key_id, secret)
    key_hash = _hash_key(plain_key)
    key_prefix = plain_key[:16]
    key_suffix = plain_key[-8:]
    where, params = _scope_filter(identity, include_where=False)

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_api_keys
                SET key_hash = %s,
                    key_prefix = %s,
                    key_suffix = %s,
                    status = 'active',
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (key_hash, key_prefix, key_suffix, key_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    if not row:
        return None
    return {**_row_to_payload(row, cols), "plainKey": plain_key}


def delete_api_key(key_id: str, identity: IdentityContext | None = None) -> bool:
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_api_keys
                SET status = 'deleted',
                    deleted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                """,
                (key_id, *params),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return deleted


def get_api_key(key_id: str, identity: IdentityContext | None = None) -> dict[str, Any] | None:
    matches = [item for item in list_api_keys(identity) if item["id"] == key_id]
    return matches[0] if matches else None


def authenticate_api_key(
    plain_key: str,
    *,
    kb_id: str,
    capability: str,
    signature: ApiKeySignaturePayload | None = None,
    client_ip: str | None = None,
    force_signature: bool = False,
) -> ApiKeyAuthResult:
    normalized_key = (plain_key or "").strip()
    if not normalized_key:
        raise ApiKeyError("API_KEY_REQUIRED", "API Key is required")
    if not kb_id.strip():
        raise ApiKeyError("KB_ID_REQUIRED", "kb_id is required for OpenAPI calls")

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, status, kb_ids, capabilities, expires_at,
                       require_signature, allowed_ips, rpm_limit, daily_request_limit, app_id
                FROM kb_api_keys
                WHERE key_hash = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (_hash_key(normalized_key),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise ApiKeyError("INVALID_API_KEY", "API Key is invalid")

    key_id = str(row[0])
    tenant_id, status, kb_ids, capabilities, expires_at, require_signature, allowed_ips = row[1:8]
    rpm_limit = _int_at(row, 8)
    daily_request_limit = _int_at(row, 9)
    app_id = _str_at(row, 10)
    if status == DISABLED_STATUS:
        raise ApiKeyError("API_KEY_DISABLED", "API Key is disabled", api_key_id=key_id)
    if status != ACTIVE_STATUS:
        raise ApiKeyError("INVALID_API_KEY", "API Key is not active", api_key_id=key_id)
    if expires_at and _ensure_aware(expires_at) <= datetime.now(timezone.utc):
        raise ApiKeyError("API_KEY_EXPIRED", "API Key is expired", api_key_id=key_id)

    kb_scope = tuple(str(item) for item in _list_from_json(kb_ids))
    capability_scope = tuple(str(item) for item in _list_from_json(capabilities))
    if kb_id != "*" and kb_id not in kb_scope:
        raise ApiKeyError("KB_BINDING_DENIED", "API Key is not bound to this knowledge base", api_key_id=key_id)
    if capability not in capability_scope:
        raise ApiKeyError("CAPABILITY_DENIED", "API Key lacks the required capability", api_key_id=key_id)

    allowed_ip_scope = tuple(str(item) for item in _list_from_json(allowed_ips))
    _verify_client_ip(client_ip, allowed_ip_scope, api_key_id=key_id)
    if bool(require_signature) or force_signature:
        _verify_signature(
            key_id=key_id,
            plain_key=normalized_key,
            signature=signature,
        )

    _enforce_api_key_quota(
        key_id=key_id,
        rpm_limit=rpm_limit,
        daily_request_limit=daily_request_limit,
    )
    _mark_api_key_used(key_id)
    return ApiKeyAuthResult(
        identity=IdentityContext(
            tenant_id=str(tenant_id) if tenant_id else None,
            user_id=f"api_key:{key_id}",
            username=key_id,
            display_name=f"API Key {key_id}",
            is_tenant_admin=True,
            is_authenticated=True,
            source="api_key",
        ),
        api_key_id=key_id,
        capabilities=capability_scope,
        kb_ids=kb_scope,
        require_signature=bool(require_signature),
        allowed_ips=allowed_ip_scope,
        rpm_limit=rpm_limit,
        daily_request_limit=daily_request_limit,
        app_id=app_id,
    )


def _mark_api_key_used(key_id: str) -> None:
    try:
        conn = get_db_connection()
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kb_api_keys SET last_used_at = NOW() WHERE id = %s", (key_id,))
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
    finally:
        conn.close()


def _verify_client_ip(client_ip: str | None, allowed_ips: tuple[str, ...], *, api_key_id: str | None = None) -> None:
    if not allowed_ips:
        return
    value = (client_ip or "").split(",", 1)[0].strip()
    if not value:
        raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id)
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id) from exc
    for item in allowed_ips:
        try:
            if "/" in item and address in ipaddress.ip_network(item, strict=False):
                return
            if "/" not in item and address == ipaddress.ip_address(item):
                return
        except ValueError:
            continue
    raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id)


def _verify_signature(
    *,
    key_id: str,
    plain_key: str,
    signature: ApiKeySignaturePayload | None,
) -> None:
    if signature is None:
        raise ApiKeyError("SIGNATURE_REQUIRED", "Signed OpenAPI headers are required for this API Key", api_key_id=key_id)
    timestamp = (signature.timestamp or "").strip()
    nonce = (signature.nonce or "").strip()
    body_sha256 = (signature.body_sha256 or "").strip().lower()
    provided_signature = (signature.signature or "").strip().lower()
    if not timestamp or not nonce or not body_sha256 or not provided_signature:
        raise ApiKeyError("SIGNATURE_REQUIRED", "Signed OpenAPI headers are required for this API Key", api_key_id=key_id)
    if len(nonce) > 128:
        raise ApiKeyError("INVALID_NONCE", "Nonce is too long", api_key_id=key_id)

    expected_body_sha256 = hashlib.sha256(signature.body).hexdigest()
    if not hmac.compare_digest(body_sha256, expected_body_sha256):
        raise ApiKeyError("BODY_HASH_MISMATCH", "Request body hash does not match", api_key_id=key_id)

    signed_at = _parse_timestamp(timestamp, api_key_id=key_id)
    now = datetime.now(timezone.utc)
    if abs((now - signed_at).total_seconds()) > 300:
        raise ApiKeyError("TIMESTAMP_EXPIRED", "Request timestamp is outside the allowed 5 minute window", api_key_id=key_id)

    canonical = "\n".join(
        [
            signature.method.upper(),
            signature.path,
            timestamp,
            nonce,
            body_sha256,
        ]
    )
    expected_signature = hmac.new(plain_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ApiKeyError("INVALID_SIGNATURE", "Request signature is invalid", api_key_id=key_id)

    _record_nonce(
        key_id=key_id,
        nonce=nonce,
        request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(minutes=10),
    )


def _parse_timestamp(value: str, *, api_key_id: str | None = None) -> datetime:
    try:
        if value.isdigit():
            parsed = datetime.fromtimestamp(int(value), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiKeyError("INVALID_TIMESTAMP", "Request timestamp is invalid", api_key_id=api_key_id) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_nonce(*, key_id: str, nonce: str, request_hash: str, expires_at: datetime) -> None:
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kb_api_key_nonces WHERE expires_at <= NOW()")
            cur.execute(
                """
                INSERT INTO kb_api_key_nonces(api_key_id, nonce, request_hash, expires_at)
                VALUES(%s, %s, %s, %s)
                ON CONFLICT(api_key_id, nonce) DO NOTHING
                """,
                (key_id, nonce, request_hash, expires_at),
            )
            inserted = cur.rowcount > 0
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    if not inserted:
        raise ApiKeyError("NONCE_REPLAYED", "Request nonce has already been used", api_key_id=key_id)


def _enforce_api_key_quota(*, key_id: str, rpm_limit: int, daily_request_limit: int) -> None:
    if rpm_limit <= 0 and daily_request_limit <= 0:
        return
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            if rpm_limit > 0:
                _consume_quota_window(cur, key_id=key_id, window_type="minute", limit=rpm_limit)
            if daily_request_limit > 0:
                _consume_quota_window(cur, key_id=key_id, window_type="day", limit=daily_request_limit)
        conn.commit()
    except ApiKeyError:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()


def _consume_quota_window(cur: Any, *, key_id: str, window_type: str, limit: int) -> None:
    if window_type == "minute":
        bucket_sql = "date_trunc('minute', NOW())"
        code = "RATE_LIMITED"
        message = "API Key minute request limit exceeded"
    else:
        bucket_sql = "date_trunc('day', NOW())"
        code = "QUOTA_EXCEEDED"
        message = "API Key daily request quota exceeded"
    cur.execute(
        f"""
        INSERT INTO kb_api_key_usage_windows(api_key_id, window_type, window_start, request_count, updated_at)
        VALUES(%s, %s, {bucket_sql}, 1, NOW())
        ON CONFLICT(api_key_id, window_type, window_start)
        DO UPDATE SET
            request_count = kb_api_key_usage_windows.request_count + 1,
            updated_at = NOW()
        WHERE kb_api_key_usage_windows.request_count < %s
        RETURNING request_count
        """,
        (key_id, window_type, limit),
    )
    if cur.fetchone() is None:
        raise ApiKeyError(code, message, api_key_id=key_id)


def _normalize_kb_ids(kb_ids: list[str]) -> list[str]:
    result = []
    for item in kb_ids:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    if not result:
        raise ValueError("At least one knowledge base must be bound")
    if len(result) > MAX_BOUND_KB_IDS:
        raise ValueError(f"An API Key can bind at most {MAX_BOUND_KB_IDS} knowledge bases")
    return result


def _normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    raw = capabilities or list(DEFAULT_CAPABILITIES)
    result = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    if not result:
        raise ValueError("At least one capability is required")
    return result


def _normalize_allowed_ips(allowed_ips: list[str]) -> list[str]:
    result: list[str] = []
    for item in allowed_ips:
        value = str(item or "").strip()
        if not value or value in result:
            continue
        try:
            if "/" in value:
                ipaddress.ip_network(value, strict=False)
            else:
                ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"Invalid allowed IP or CIDR: {value}") from exc
        result.append(value)
    return result


def _normalize_non_negative_int(value: int | str | None) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit fields must be non-negative integers") from exc
    if parsed < 0:
        raise ValueError("limit fields must be non-negative integers")
    return parsed


def _scope_filter(identity: IdentityContext, *, include_where: bool = True) -> tuple[str, tuple[str, ...]]:
    if identity.enforce_access and not identity.is_platform_admin:
        clause = "tenant_id = %s"
        prefix = "WHERE" if include_where else "AND"
        return f"{prefix} deleted_at IS NULL AND {clause}" if include_where else f"AND {clause}", (identity.tenant_id or "",)
    return ("WHERE deleted_at IS NULL" if include_where else ""), ()


def _new_key_id() -> str:
    return f"ak_{secrets.token_hex(12)}"


def _new_app_id() -> str:
    return f"app_{secrets.token_hex(8)}"


def _new_secret() -> str:
    return secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:40]


def _format_plain_key(key_id: str, secret: str) -> str:
    return f"wwkb_{key_id}_{secret}"


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_array(values: list[str]) -> str:
    import json

    return json.dumps(values, ensure_ascii=False)


def _list_from_json(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _int_at(row: tuple[Any, ...], index: int, default: int = 0) -> int:
    if len(row) <= index:
        return default
    try:
        return max(0, int(row[index] or 0))
    except (TypeError, ValueError):
        return default


def _str_at(row: tuple[Any, ...], index: int) -> str | None:
    if len(row) <= index or row[index] is None:
        return None
    value = str(row[index]).strip()
    return value or None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_payload(row: tuple[Any, ...], cols: list[str]) -> dict[str, Any]:
    data = dict(zip(cols, row))
    return {
        "id": data["id"],
        "appId": data.get("app_id"),
        "name": data["name"],
        "tenantId": data.get("tenant_id"),
        "createdBy": data.get("created_by"),
        "keyPrefix": data.get("key_prefix"),
        "keySuffix": data.get("key_suffix"),
        "status": data.get("status"),
        "kbIds": [str(item) for item in _list_from_json(data.get("kb_ids"))],
        "capabilities": [str(item) for item in _list_from_json(data.get("capabilities"))],
        "requireSignature": bool(data.get("require_signature")),
        "allowedIps": [str(item) for item in _list_from_json(data.get("allowed_ips"))],
        "rpmLimit": int(data.get("rpm_limit") or 0),
        "dailyRequestLimit": int(data.get("daily_request_limit") or 0),
        "note": data.get("note") or "",
        "expiresAt": _iso(data.get("expires_at")),
        "lastUsedAt": _iso(data.get("last_used_at")),
        "createdAt": _iso(data.get("created_at")),
        "updatedAt": _iso(data.get("updated_at")),
        "deletedAt": _iso(data.get("deleted_at")),
    }


def _app_row_to_payload(row: tuple[Any, ...], cols: list[str]) -> dict[str, Any]:
    data = dict(zip(cols, row))
    return {
        "id": data["id"],
        "name": data["name"],
        "tenantId": data.get("tenant_id"),
        "ownerUserId": data.get("owner_user_id"),
        "status": data.get("status"),
        "note": data.get("note") or "",
        "createdAt": _iso(data.get("created_at")),
        "updatedAt": _iso(data.get("updated_at")),
        "deletedAt": _iso(data.get("deleted_at")),
    }
