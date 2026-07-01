from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.services.ai_base_sso_service import (
    STATE_COOKIE_NAME,
    AiBaseSsoError,
    build_console_redirect_url,
    build_launch_url,
    create_session_from_identity_summary,
    current_identity_payload,
    exchange_ai_base_credential,
    is_sso_configured,
    load_sso_config,
    make_state_payload,
    refresh_current_user_snapshot,
    sync_identity_delta_from_ai_base,
    validate_state,
)
from backend.services.identity_service import get_current_identity, is_legacy_header_auth_enabled
from backend.services.identity_sync_scheduler import get_identity_sync_status
from core.db.identity import (
    SESSION_COOKIE_NAME,
    TENANT_ADMIN_ROLE_CODE,
    IdentityContext,
    clear_identity_snapshot_data,
    list_identity_snapshot_users,
    revoke_auth_session,
    revoke_auth_sessions_for_identity,
)
from core.db.query_logs import AuditLogRecord, append_audit_log

router = APIRouter()


class AiBaseExchangeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str | None = Field(default=None, min_length=1)
    jwt: str | None = Field(default=None, min_length=1)
    state: str | None = Field(default=None, min_length=1)


class AiBaseLogoutCallbackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = Field(..., alias="tenantId", min_length=1)
    user_id: str | None = Field(default=None, alias="userId")
    reason: str = Field(default="", max_length=200)


@router.get("/api/identity/snapshot-users")
def identity_snapshot_users(
    limit: int = 10,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    _assert_super_manager(identity, "view identity snapshot users")
    try:
        users = list_identity_snapshot_users(limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "mode": "temporary_sso_deferred",
        "users": users,
        "count": len(users),
    }


@router.get("/api/auth/ai-base/config")
def ai_base_sso_config() -> dict:
    config = load_sso_config()
    return {
        "configured": is_sso_configured(config),
        "mode": "ai_base_sso",
        "legacyHeaderFallback": is_legacy_header_auth_enabled(),
        "baseUrl": config.base_url,
        "launchBaseUrl": config.launch_base_url or config.base_url,
        "clientId": config.client_id,
        "redirectUri": config.redirect_uri,
        "launchPath": config.launch_path,
        "ragLaunchPath": "/api/auth/ai-base/launch",
        "ragLaunchUrl": _rag_public_url("/api/auth/ai-base/launch", config.redirect_uri),
        "ragCallbackPath": "/api/auth/ai-base/callback",
        "ragCallbackUrl": config.redirect_uri,
        "aiBaseBrowserSsoBaseUrl": config.launch_base_url or config.base_url,
        "aiBaseBrowserSsoPath": config.launch_path,
        "aiBaseBrowserSsoUrl": f"{(config.launch_base_url or config.base_url).rstrip('/')}{config.launch_path}",
        "stateOwner": "rag_launch_cookie",
        "exchangePath": config.exchange_path,
        "userSnapshotPathTemplate": config.user_snapshot_path_template,
        "deltaPath": config.delta_path,
    }


@router.get("/api/auth/ai-base/launch")
def ai_base_sso_launch(next: str = Query(default="/knowledge-bases")):
    try:
        config = load_sso_config()
        state, payload = make_state_payload(next, config=config)
        url = build_launch_url(state, config=config)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        STATE_COOKIE_NAME,
        str(payload["cookie"]),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/api/auth/ai-base/callback")
async def ai_base_sso_callback(
    code: str | None = None,
    state: str | None = None,
    sso_state: str | None = Cookie(default=None, alias=STATE_COOKIE_NAME),
):
    try:
        next_path = validate_state(sso_state, state)
        summary = await exchange_ai_base_credential(code=code)
        session = create_session_from_identity_summary(summary)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response = RedirectResponse(url=build_console_redirect_url(next_path), status_code=302)
    _attach_session_cookie(response, session["sessionToken"])
    response.delete_cookie(STATE_COOKIE_NAME, path="/")
    return response


@router.post("/api/auth/ai-base/exchange")
async def ai_base_sso_exchange(payload: AiBaseExchangeRequest, response: Response) -> dict:
    try:
        summary = await exchange_ai_base_credential(code=payload.code, jwt=payload.jwt)
        session = create_session_from_identity_summary(summary)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _attach_session_cookie(response, session["sessionToken"])
    return {
        "identity": session["identity"],
        "expiresAt": session["expiresAt"],
        "mode": "ai_base_sso_session",
    }


@router.get("/api/auth/session")
def auth_session(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "identity": current_identity_payload(identity),
        "mode": identity.source,
    }


@router.post("/api/auth/ai-base/refresh-current-user")
async def refresh_current_user(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await refresh_current_user_snapshot(
            tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id),
        )
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/identity/sync-delta")
async def identity_sync_delta(
    last_sync_at: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _can_run_identity_delta_sync(identity):
        raise HTTPException(
            status_code=403,
            detail="Only SSO-authenticated super administrators can sync identity delta",
        )
    try:
        return await sync_identity_delta_from_ai_base(last_sync_at=last_sync_at)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/identity/snapshot-data")
def remove_identity_snapshot_data(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    _assert_super_manager(identity, "remove identity snapshot data")
    try:
        deleted = clear_identity_snapshot_data()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    append_audit_log(
        AuditLogRecord(
            action="identity_snapshot.clear",
            resource_type="identity_snapshot",
            resource_id=str(identity.tenant_id or ""),
            identity=identity,
            outcome="success",
            risk_level="high",
            summary="Cleared local AI Base identity snapshot data",
            metadata=deleted,
        )
    )
    return {"deleted": deleted}


@router.get("/api/identity/sync-status")
def identity_sync_status(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    _assert_super_manager(identity, "view identity sync status")
    return get_identity_sync_status()


@router.post("/api/auth/logout")
def auth_logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    revoked = revoke_auth_session(session_token or "")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "revoked": revoked}


@router.post("/api/auth/ai-base/logout-callback")
def ai_base_logout_callback(
    payload: AiBaseLogoutCallbackRequest,
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    x_client_secret: str | None = Header(default=None, alias="X-Client-Secret"),
) -> dict:
    _assert_ai_base_server_credentials(x_client_id, x_client_secret)
    revoked = revoke_auth_sessions_for_identity(payload.tenant_id, payload.user_id)
    append_audit_log(
        AuditLogRecord(
            action="sso.logout_callback",
            resource_type="auth_session",
            resource_id=payload.user_id or payload.tenant_id,
            outcome="success",
            risk_level="medium",
            summary="AI Base logout callback revoked KB sessions",
            metadata={
                "tenantId": payload.tenant_id,
                "userId": payload.user_id or "",
                "reason": payload.reason,
                "revoked": revoked,
            },
        )
    )
    return {
        "ok": True,
        "tenantId": payload.tenant_id,
        "userId": payload.user_id or "",
        "revoked": revoked,
    }


def _attach_session_cookie(response: Response, session_token: str) -> None:
    config = load_sso_config()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=config.session_ttl_seconds,
        path="/",
    )


def _cookie_secure() -> bool:
    return str(load_sso_config().redirect_uri).lower().startswith("https://")


def _can_run_identity_delta_sync(identity: IdentityContext) -> bool:
    return (
        identity.enforce_access
        and str(identity.source or "").startswith("ai_base_sso_")
        and identity.is_tenant_admin
        and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}
    )


def _is_super_manager(identity: IdentityContext) -> bool:
    return identity.enforce_access and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}


def _assert_super_manager(identity: IdentityContext, action: str) -> None:
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _is_super_manager(identity):
        raise HTTPException(status_code=403, detail=f"Only super administrators can {action}")


def _assert_ai_base_server_credentials(client_id: str | None, client_secret: str | None) -> None:
    if not client_id or not client_secret:
        raise HTTPException(status_code=401, detail="AI Base client credentials are required")
    config = load_sso_config()
    if client_id != config.client_id or client_secret != config.client_secret:
        raise HTTPException(status_code=403, detail="AI Base client credentials are invalid")


def _rag_public_url(path: str, redirect_uri: str) -> str:
    parsed = urlsplit(redirect_uri or "")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return path
