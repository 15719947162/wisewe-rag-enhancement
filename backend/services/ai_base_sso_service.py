from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from core.db.identity import (
    DEFAULT_SESSION_TTL_SECONDS,
    IdentityContext,
    create_auth_session,
    fingerprint_credential,
    get_latest_identity_sync_watermark,
    identity_to_payload,
    is_sso_credential_used,
    mark_sso_credential_used,
    record_identity_sync_run,
    upsert_identity_delta_snapshot,
    upsert_identity_snapshot_from_summary,
)
from core.runtime_settings import resolve_runtime_setting


STATE_COOKIE_NAME = "kb_sso_state"
IDENTITY_DELTA_INITIAL_WATERMARK = "2000-01-01 00:00:00"
IDENTITY_DELTA_HTTP_TIMEOUT_SECONDS = 300.0
logger = logging.getLogger(__name__)


class AiBaseSsoError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class SsoConfig:
    base_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    console_base_url: str = ""
    launch_base_url: str = ""
    launch_path: str = "/sso"
    exchange_path: str = "/ai/system/internal/sso/exchange"
    user_snapshot_path_template: str = "/ai/system/internal/identity/snapshot/users/{userId}"
    delta_path: str = "/ai/system/internal/identity/snapshot/delta"
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS


def load_sso_config() -> SsoConfig:
    base_url = (os.getenv("AI_BASE_SSO_BASE_URL") or "").strip().rstrip("/")
    client_id = (os.getenv("AI_BASE_SSO_CLIENT_ID") or "rag-client").strip()
    client_secret = (os.getenv("AI_BASE_SSO_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("AI_BASE_SSO_REDIRECT_URI") or "").strip()
    console_base_url = (os.getenv("KB_CONSOLE_BASE_URL") or "").strip().rstrip("/")
    launch_base_url = str(resolve_runtime_setting("AI_BASE_SSO_LAUNCH_BASE_URL")[0] or "").strip().rstrip("/")
    launch_path = str(resolve_runtime_setting("AI_BASE_SSO_LAUNCH_PATH")[0] or "/sso").strip()
    exchange_path = str(resolve_runtime_setting("AI_BASE_SSO_EXCHANGE_PATH")[0] or "/ai/system/internal/sso/exchange").strip()
    user_snapshot_path_template = str(
        resolve_runtime_setting("AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE")[0]
        or "/ai/system/internal/identity/snapshot/users/{userId}"
    ).strip()
    delta_path = str(resolve_runtime_setting("AI_BASE_SSO_DELTA_PATH")[0] or "/ai/system/internal/identity/snapshot/delta").strip()
    ttl = int(os.getenv("KB_SESSION_TTL_SECONDS") or DEFAULT_SESSION_TTL_SECONDS)
    return SsoConfig(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        console_base_url=console_base_url,
        launch_base_url=launch_base_url,
        launch_path=launch_path,
        exchange_path=exchange_path,
        user_snapshot_path_template=user_snapshot_path_template,
        delta_path=delta_path,
        session_ttl_seconds=ttl,
    )


def is_sso_configured(config: SsoConfig | None = None) -> bool:
    config = config or load_sso_config()
    return bool(config.base_url and config.client_id and config.client_secret and config.redirect_uri)


def make_state_payload(next_path: str, config: SsoConfig | None = None) -> tuple[str, dict[str, Any]]:
    state = secrets.token_urlsafe(24)
    payload = {
        "state": state,
        "next": next_path if _is_safe_local_path(next_path) else "/knowledge-bases",
        "iat": datetime.now(timezone.utc).timestamp(),
    }
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return state, payload | {"cookie": encoded_payload}


def validate_state(cookie_value: str | None, state: str | None) -> str:
    if not state:
        raise AiBaseSsoError("STATE_REQUIRED", "SSO state is required", 400)
    if not cookie_value:
        raise AiBaseSsoError("STATE_REQUIRED", "SSO state cookie is required", 400)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cookie_value.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise AiBaseSsoError("STATE_INVALID", "SSO state cookie is invalid", 400) from exc
    issued_at = float(payload.get("iat") or 0)
    if datetime.now(timezone.utc).timestamp() - issued_at > 10 * 60:
        raise AiBaseSsoError("STATE_EXPIRED", "SSO state is expired", 400)
    if not hmac.compare_digest(str(payload.get("state") or ""), str(state)):
        raise AiBaseSsoError("STATE_MISMATCH", "SSO state did not match", 400)
    next_path = str(payload.get("next") or "/knowledge-bases")
    return next_path if _is_safe_local_path(next_path) else "/knowledge-bases"


def build_launch_url(state: str, config: SsoConfig | None = None) -> str:
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    launch_base_url = (config.launch_base_url or config.base_url).rstrip("/")
    query = urlencode({"client_id": config.client_id, "redirect_uri": config.redirect_uri, "state": state})
    return f"{launch_base_url}{config.launch_path}?{query}"


def build_console_redirect_url(next_path: str, config: SsoConfig | None = None) -> str:
    config = config or load_sso_config()
    path = next_path if _is_safe_local_path(next_path) else "/knowledge-bases"
    if config.console_base_url:
        return f"{config.console_base_url}{path}"
    return path


async def exchange_ai_base_credential(
    *,
    code: str | None = None,
    jwt: str | None = None,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    if not code and not jwt:
        raise AiBaseSsoError("CREDENTIAL_REQUIRED", "SSO code or JWT is required", 400)

    grant_type = "authorization_code" if code else "jwt"
    credential = code or jwt or ""
    fingerprint = fingerprint_credential(credential)
    if is_sso_credential_used(fingerprint):
        raise AiBaseSsoError("CREDENTIAL_REPLAYED", "SSO credential was already used", 409)

    request_payload: dict[str, Any] = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": grant_type,
        "redirect_uri": config.redirect_uri,
    }
    if code:
        request_payload["code"] = code
    if jwt:
        request_payload["jwt"] = jwt

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{config.base_url}{config.exchange_path}", json=request_payload)
    except httpx.HTTPError as exc:
        raise AiBaseSsoError("EXCHANGE_UNAVAILABLE", "AI base SSO exchange is unavailable", 503) from exc

    if response.status_code >= 400:
        raise AiBaseSsoError("EXCHANGE_REJECTED", _safe_exchange_error(response), response.status_code)

    summary = _unwrap_ai_base_result(response, invalid_code="EXCHANGE_INVALID_RESPONSE", invalid_message="AI base SSO returned invalid identity summary")

    return summary | {"_auth_source": f"ai_base_sso_{grant_type}", "_credential_fingerprint": fingerprint}


def create_session_from_identity_summary(summary: dict[str, Any], config: SsoConfig | None = None) -> dict[str, Any]:
    config = config or load_sso_config()
    auth_source = str(summary.get("_auth_source") or "ai_base_sso_code")
    fingerprint = summary.get("_credential_fingerprint")
    identity = upsert_identity_snapshot_from_summary(summary)
    session = create_auth_session(
        identity,
        auth_source=auth_source,
        credential_fingerprint=str(fingerprint or ""),
        identity_snapshot_version=str(summary.get("snapshot_version") or ""),
        ttl_seconds=config.session_ttl_seconds,
    )
    if fingerprint:
        mark_sso_credential_used(
            str(fingerprint),
            credential_type=auth_source,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
    return session


def current_identity_payload(identity: IdentityContext) -> dict[str, Any]:
    return identity_to_payload(identity)


async def refresh_current_user_snapshot(
    *,
    tenant_id: str,
    user_id: str,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    config = config or load_sso_config()
    url = build_user_snapshot_url(user_id, tenant_id, config=config)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=_server_auth_headers(config))
    except httpx.HTTPError as exc:
        raise AiBaseSsoError("USER_SNAPSHOT_UNAVAILABLE", "AI base user snapshot is unavailable", 503) from exc

    if response.status_code >= 400:
        raise AiBaseSsoError("USER_SNAPSHOT_REJECTED", _safe_exchange_error(response), response.status_code)

    summary = _unwrap_ai_base_result(
        response,
        invalid_code="USER_SNAPSHOT_INVALID_RESPONSE",
        invalid_message="AI base returned invalid user snapshot",
    )
    identity = upsert_identity_snapshot_from_summary(summary)
    return {
        "identity": identity_to_payload(identity),
        "snapshotVersion": str(summary.get("snapshot_version") or ""),
        "generatedAt": summary.get("generated_at") or summary.get("issued_at"),
    }


async def sync_identity_delta_from_ai_base(
    *,
    last_sync_at: str | None = None,
    use_latest_watermark: bool = True,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    config = config or load_sso_config()
    requested_last_sync_at = last_sync_at
    if requested_last_sync_at is None and use_latest_watermark:
        requested_last_sync_at = get_latest_identity_sync_watermark()
    if not format_identity_sync_timestamp(requested_last_sync_at):
        requested_last_sync_at = IDENTITY_DELTA_INITIAL_WATERMARK
    requested_last_sync_at = format_identity_sync_timestamp(requested_last_sync_at)
    url = build_delta_url(requested_last_sync_at, config=config)

    try:
        async with httpx.AsyncClient(timeout=IDENTITY_DELTA_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_server_auth_headers(config))
    except httpx.HTTPError as exc:
        record_identity_sync_run(
            sync_mode="http_delta",
            source_host=config.base_url,
            requested_limit=0,
            counts={},
            status="failed",
            last_sync_at=requested_last_sync_at,
            error_message="AI base identity delta is unavailable",
        )
        raise AiBaseSsoError("IDENTITY_DELTA_UNAVAILABLE", "AI base identity delta is unavailable", 503) from exc

    if response.status_code >= 400:
        error_message = _safe_exchange_error(response)
        record_identity_sync_run(
            sync_mode="http_delta",
            source_host=config.base_url,
            requested_limit=0,
            counts={},
            status="failed",
            last_sync_at=requested_last_sync_at,
            error_message=error_message,
        )
        raise AiBaseSsoError("IDENTITY_DELTA_REJECTED", error_message, response.status_code)

    delta = _unwrap_ai_base_result(
        response,
        invalid_code="IDENTITY_DELTA_INVALID_RESPONSE",
        invalid_message="AI base returned invalid identity delta",
    )
    delta_shape = _identity_delta_shape(delta)
    if delta_shape["warnings"]:
        logger.warning(
            "AI base identity delta shape warning: last_sync_at=%s url=%s shape=%s warnings=%s",
            requested_last_sync_at or "",
            f"{config.base_url}{config.delta_path}",
            delta_shape,
            delta_shape["warnings"],
        )
    else:
        logger.info(
            "AI base identity delta shape: last_sync_at=%s url=%s shape=%s",
            requested_last_sync_at or "",
            f"{config.base_url}{config.delta_path}",
            delta_shape,
        )
    counts = upsert_identity_delta_snapshot(delta)
    max_updated_at = format_identity_sync_timestamp(delta.get("max_updated_at") or "")
    snapshot_version = str(delta.get("snapshot_version") or "")
    record_identity_sync_run(
        sync_mode="http_delta",
        source_host=config.base_url,
        requested_limit=0,
        counts=counts,
        status="success",
        source_schema=_identity_delta_source_schema(delta_shape),
        last_sync_at=requested_last_sync_at,
        max_updated_at=max_updated_at or None,
        snapshot_version=snapshot_version or None,
        has_more=bool(delta.get("has_more")),
    )
    return {
        "mode": "http_delta",
        "lastSyncAt": requested_last_sync_at or "",
        "maxUpdatedAt": max_updated_at,
        "snapshotVersion": snapshot_version,
        "generatedAt": delta.get("generated_at"),
        "hasMore": bool(delta.get("has_more")),
        "counts": counts,
        "diagnostics": delta_shape,
    }


def build_user_snapshot_url(user_id: str, tenant_id: str, config: SsoConfig | None = None) -> str:
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    path = config.user_snapshot_path_template.format(userId=user_id, tenantId=tenant_id)
    query = urlencode({"tenant_id": tenant_id})
    return f"{config.base_url}{path}?{query}"


def build_delta_url(last_sync_at: str | None, config: SsoConfig | None = None) -> str:
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    query = urlencode({"last_sync_at": format_identity_sync_timestamp(last_sync_at)})
    return f"{config.base_url}{config.delta_path}?{query}"


def format_identity_sync_timestamp(value: Any) -> str:
    """Use the AI base delta contract format: YYYY-MM-DD HH:mm:ss."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip()
    if not text:
        return ""

    iso_candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    match = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return text


def _safe_exchange_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"AI base SSO rejected exchange with HTTP {response.status_code}"
    if isinstance(payload, dict):
        code = payload.get("code") or payload.get("error") or payload.get("error_code")
        message = payload.get("message") or payload.get("detail") or payload.get("error_description")
        if code and message:
            return f"{code}: {message}"
        if message:
            return str(message)
    return f"AI base SSO rejected exchange with HTTP {response.status_code}"


def _unwrap_ai_base_result(response: httpx.Response, *, invalid_code: str, invalid_message: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AiBaseSsoError(invalid_code, "AI base returned invalid JSON", 502) from exc
    if not isinstance(payload, dict):
        raise AiBaseSsoError(invalid_code, invalid_message, 502)
    if payload.get("success") is False:
        raise AiBaseSsoError(
            str(payload.get("code") or "AI_BASE_REJECTED"),
            str(payload.get("msg") or payload.get("message") or "AI base rejected request"),
            502,
        )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise AiBaseSsoError(invalid_code, invalid_message, 502)
    return dict(data)


def _identity_delta_shape(delta: dict[str, Any]) -> dict[str, Any]:
    list_aliases = {
        "tenants": ("tenants", "tenantList", "tenant_list"),
        "users": ("users", "userList", "user_list"),
        "roles": ("roles", "roleList", "role_list"),
        "user_roles": ("user_roles", "userRoles", "userRoleList", "user_role_list"),
        "deleted": ("deleted", "deletedList", "deleted_list"),
    }
    list_lengths = {key: len(_first_delta_list(delta, *aliases)) for key, aliases in list_aliases.items()}
    sample_keys: dict[str, list[str]] = {}
    for key, aliases in list_aliases.items():
        value = _first_delta_list(delta, *aliases)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sample_keys[key] = sorted(str(item_key) for item_key in value[0].keys())
    warnings: list[str] = []
    if (
        list_lengths["tenants"] > 0
        and list_lengths["users"] == 0
        and list_lengths["roles"] == 0
        and list_lengths["user_roles"] == 0
    ):
        warnings.append("tenants_non_empty_but_identity_edges_empty")
    return {
        "keys": sorted(str(key) for key in delta.keys()),
        "listLengths": list_lengths,
        "sampleKeys": sample_keys,
        "warnings": warnings,
    }


def _first_delta_list(delta: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = delta.get(key)
        if isinstance(value, list):
            return value
    return []


def _identity_delta_source_schema(shape: dict[str, Any]) -> str:
    list_lengths = shape.get("listLengths") if isinstance(shape.get("listLengths"), dict) else {}
    lengths = ",".join(
        f"{key}:{int(list_lengths.get(key) or 0)}"
        for key in ("tenants", "users", "roles", "user_roles", "deleted")
    )
    warnings = shape.get("warnings") if isinstance(shape.get("warnings"), list) else []
    warning_text = ",".join(str(item) for item in warnings)
    text = f"delta_shape {lengths}"
    if warning_text:
        text = f"{text};warnings:{warning_text}"
    return text[:255]


def _server_auth_headers(config: SsoConfig) -> dict[str, str]:
    return {
        "X-Client-Id": config.client_id,
        "X-Client-Secret": config.client_secret,
    }


def _is_safe_local_path(path: str) -> bool:
    return path.startswith("/") and not path.startswith("//")


def jwt_fingerprint(jwt: str) -> str:
    return hashlib.sha256(jwt.encode("utf-8")).hexdigest()[:32]
