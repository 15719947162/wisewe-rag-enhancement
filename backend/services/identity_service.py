from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Header, HTTPException

from core.db.identity import (
    SESSION_COOKIE_NAME,
    IdentityContext,
    anonymous_identity,
    latest_identity_snapshot_synced_at,
    resolve_auth_session,
    resolve_identity_snapshot,
)
from core.db.query_logs import AuditLogRecord, append_audit_log
from core.runtime_settings import resolve_runtime_setting


def is_legacy_header_auth_enabled() -> bool:
    """Keep local bootstrap compatibility unless production explicitly disables it."""
    try:
        return bool(resolve_runtime_setting("KB_LEGACY_HEADER_AUTH_ENABLED")[0])
    except Exception:
        return True


def get_current_identity(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    tenant_id: str | None = Header(default=None, alias="X-KB-Tenant-Id"),
    user_id: str | None = Header(default=None, alias="X-KB-User-Id"),
) -> IdentityContext:
    """Resolve request identity from the KB session or the legacy bootstrap headers."""
    if session_token:
        identity = resolve_auth_session(session_token)
        if identity is None:
            raise HTTPException(status_code=401, detail="KB session is invalid or expired")
        return identity

    tenant = (tenant_id or "").strip()
    user = (user_id or "").strip()
    if not tenant and not user:
        return anonymous_identity()
    if not is_legacy_header_auth_enabled():
        raise HTTPException(status_code=401, detail="Legacy X-KB-* header authentication is disabled")
    if not tenant or not user:
        raise HTTPException(status_code=401, detail="Both X-KB-Tenant-Id and X-KB-User-Id are required")

    identity = resolve_identity_snapshot(tenant, user)
    if identity is None:
        raise HTTPException(status_code=403, detail="AI base identity snapshot did not allow this user")
    return identity


def identity_snapshot_freshness(identity: IdentityContext) -> dict:
    """Return freshness metadata for formal AI Base SSO identities."""
    max_age_seconds = _identity_snapshot_max_age_seconds()
    if not _requires_fresh_snapshot(identity):
        return {
            "enforced": False,
            "fresh": True,
            "reasonCode": "",
            "syncedAt": "",
            "ageSeconds": 0,
            "maxAgeSeconds": max_age_seconds,
        }

    synced_at = latest_identity_snapshot_synced_at(str(identity.tenant_id), str(identity.user_id))
    if synced_at is None:
        return {
            "enforced": True,
            "fresh": False,
            "reasonCode": "IDENTITY_SNAPSHOT_MISSING",
            "syncedAt": "",
            "ageSeconds": None,
            "maxAgeSeconds": max_age_seconds,
        }

    age_seconds = max(0, int((datetime.now(timezone.utc) - synced_at).total_seconds()))
    return {
        "enforced": True,
        "fresh": age_seconds <= max_age_seconds,
        "reasonCode": "" if age_seconds <= max_age_seconds else "IDENTITY_SNAPSHOT_STALE",
        "syncedAt": synced_at.isoformat(),
        "ageSeconds": age_seconds,
        "maxAgeSeconds": max_age_seconds,
    }


def assert_fresh_identity_snapshot(
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    kb_id: str | None = None,
) -> None:
    freshness = identity_snapshot_freshness(identity)
    if freshness["fresh"]:
        return

    reason_code = str(freshness["reasonCode"] or "IDENTITY_SNAPSHOT_STALE")
    metadata = {
        "reasonCode": reason_code,
        "identitySnapshotStale": True,
        "syncedAt": freshness["syncedAt"],
        "ageSeconds": freshness["ageSeconds"],
        "maxAgeSeconds": freshness["maxAgeSeconds"],
        "action": action,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "kbId": kb_id,
    }
    append_audit_log(
        AuditLogRecord(
            action="access.denied",
            resource_type=resource_type,
            resource_id=resource_id,
            kb_id=kb_id,
            identity=identity,
            outcome="denied",
            risk_level="high",
            summary=f"Rejected {action} because AI Base identity snapshot is stale",
            metadata=metadata,
        )
    )
    raise HTTPException(
        status_code=403,
        detail={
            "code": reason_code,
            "message": "AI base identity snapshot is stale; please run identity delta sync or refresh current user",
            "details": metadata,
        },
    )


def _requires_fresh_snapshot(identity: IdentityContext) -> bool:
    return identity.enforce_access and str(identity.source or "").startswith("ai_base_sso_")


def _identity_snapshot_max_age_seconds() -> int:
    try:
        value = int(resolve_runtime_setting("KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS")[0])
    except Exception:
        value = 600
    return max(60, value)
