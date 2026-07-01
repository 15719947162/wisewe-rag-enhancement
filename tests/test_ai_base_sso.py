from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.app import app
from core.db.identity import IdentityContext


client = TestClient(app)


def _summary() -> dict:
    return {
        "tenant": {"tenant_id": "t1", "tenant_name": "租户一", "status": "active"},
        "user": {"user_id": "u1", "username": "admin", "display_name": "管理员", "status": "active"},
        "roles": [
            {
                "role_id": "r1",
                "tenant_id": "t1",
                "role_code": "superManager",
                "role_name": "超级管理员",
                "status": "active",
            }
        ],
        "user_roles": [{"tenant_id": "t1", "user_id": "u1", "role_id": "r1", "status": "active"}],
        "snapshot_version": "v1",
    }


def test_ai_base_sso_config_reports_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("AI_BASE_SSO_BASE_URL", raising=False)
    monkeypatch.delenv("AI_BASE_SSO_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AI_BASE_SSO_REDIRECT_URI", raising=False)

    response = client.get("/api/auth/ai-base/config")

    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_ai_base_sso_config_reports_legacy_header_fallback_switch(monkeypatch) -> None:
    monkeypatch.setenv("KB_LEGACY_HEADER_AUTH_ENABLED", "false")

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}):
        response = client.get("/api/auth/ai-base/config")

    assert response.status_code == 200
    assert response.json()["legacyHeaderFallback"] is False


def test_ai_base_sso_uses_configurable_paths(monkeypatch) -> None:
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "rag-client")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_BASE_URL", "https://sso.example.test")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_PATH", "/sso")
    monkeypatch.setenv("AI_BASE_SSO_EXCHANGE_PATH", "/ai/system/internal/sso/exchange")
    monkeypatch.setenv("AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE", "/ai/system/internal/identity/snapshot/users/{userId}")
    monkeypatch.setenv("AI_BASE_SSO_DELTA_PATH", "/ai/system/internal/identity/snapshot/delta")

    from backend.services.ai_base_sso_service import build_delta_url, build_launch_url, build_user_snapshot_url, load_sso_config

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}):
        config = load_sso_config()

    assert config.launch_base_url == "https://sso.example.test"
    assert config.launch_path == "/sso"
    assert config.exchange_path == "/ai/system/internal/sso/exchange"
    assert config.user_snapshot_path_template == "/ai/system/internal/identity/snapshot/users/{userId}"
    assert config.delta_path == "/ai/system/internal/identity/snapshot/delta"
    assert build_launch_url("state-123", config=config) == (
        "https://sso.example.test/sso?"
        "client_id=rag-client&redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fauth%2Fai-base%2Fcallback&state=state-123"
    )
    assert build_user_snapshot_url("u1", "t1", config=config) == (
        "https://ai-base.example.test/ai/system/internal/identity/snapshot/users/u1?tenant_id=t1"
    )
    assert build_delta_url("2026-06-23T00:00:00+08:00", config=config) == (
        "https://ai-base.example.test/ai/system/internal/identity/snapshot/delta?"
        "last_sync_at=2026-06-23+00%3A00%3A00"
    )


def test_ai_base_sso_state_is_random_and_cookie_bound() -> None:
    from backend.services.ai_base_sso_service import AiBaseSsoError, make_state_payload, validate_state

    state_one, payload_one = make_state_payload("/knowledge-bases")
    state_two, payload_two = make_state_payload("/settings")

    assert state_one != state_two
    assert validate_state(payload_one["cookie"], state_one) == "/knowledge-bases"
    assert validate_state(payload_two["cookie"], state_two) == "/settings"

    try:
        validate_state(None, "knowledge-base-home")
    except AiBaseSsoError as exc:
        assert exc.code == "STATE_REQUIRED"
    else:  # pragma: no cover
        raise AssertionError("expected callback without state cookie to be rejected")

    try:
        validate_state(payload_one["cookie"], "knowledge-base-home")
    except AiBaseSsoError as exc:
        assert exc.code == "STATE_MISMATCH"
    else:  # pragma: no cover
        raise AssertionError("expected mismatched state to be rejected")


def test_ai_base_sso_config_does_not_expose_fixed_state(monkeypatch) -> None:
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "rag-client")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_BASE_URL", "https://sso.example.test")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_PATH", "/sso")

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}):
        response = client.get("/api/auth/ai-base/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["launchBaseUrl"] == "https://sso.example.test"
    assert "launchState" not in payload
    assert "externalStateNextMap" not in payload


def test_ai_base_sso_launch_starts_rag_owned_state_transaction(monkeypatch) -> None:
    from backend.services.ai_base_sso_service import STATE_COOKIE_NAME, validate_state

    isolated_client = TestClient(app)
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "rag-client")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8001/api/auth/ai-base/callback")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_BASE_URL", "https://sso.example.test")
    monkeypatch.setenv("AI_BASE_SSO_LAUNCH_PATH", "/sso")

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}):
        response = isolated_client.get("/api/auth/ai-base/launch?next=/knowledge-bases", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlsplit(location)
    params = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://sso.example.test/sso"
    assert params["client_id"] == ["rag-client"]
    assert params["redirect_uri"] == ["http://127.0.0.1:8001/api/auth/ai-base/callback"]
    state = params["state"][0]
    assert state
    assert STATE_COOKIE_NAME in response.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert validate_state(response.cookies[STATE_COOKIE_NAME], state) == "/knowledge-bases"


def test_ai_base_jwt_exchange_creates_kb_session(monkeypatch) -> None:
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "wisewe-kb")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")
    identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        username="admin",
        display_name="管理员",
        tenant_name="租户一",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("superManager",),
    )

    with patch("backend.routes.identity.exchange_ai_base_credential",
        return_value=_summary() | {"_auth_source": "ai_base_sso_jwt", "_credential_fingerprint": "fp1"},
    ), patch(
        "core.db.identity.upsert_identity_snapshot_from_summary",
        return_value=identity,
    ), patch(
        "backend.services.ai_base_sso_service.upsert_identity_snapshot_from_summary",
        return_value=identity,
    ), patch(
        "core.db.identity.create_auth_session",
        return_value={"sessionToken": "session-token", "expiresAt": "2026-06-22T12:00:00+00:00", "identity": {}},
    ), patch(
        "backend.services.ai_base_sso_service.create_auth_session",
        return_value={
            "sessionToken": "session-token",
            "expiresAt": "2026-06-22T12:00:00+00:00",
            "identity": {
                "tenantId": "t1",
                "userId": "u1",
                "username": "admin",
                "displayName": "管理员",
                "tenantName": "租户一",
                "roleCodes": ["superManager"],
                "isTenantAdmin": True,
                "source": "identity_snapshot",
            },
        },
    ), patch("backend.services.ai_base_sso_service.mark_sso_credential_used", return_value=True):
        response = client.post("/api/auth/ai-base/exchange", json={"jwt": "jwt-token"})

    assert response.status_code == 200
    assert response.json()["identity"]["tenantId"] == "t1"
    assert "kb_session=session-token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_auth_session_reads_cookie_identity() -> None:
    isolated_client = TestClient(app)
    identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        username="admin",
        display_name="管理员",
        tenant_name="租户一",
        is_authenticated=True,
        is_tenant_admin=True,
        source="ai_base_sso_jwt",
    )

    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity):
        response = isolated_client.get("/api/auth/session", cookies={"kb_session": "session-token"})

    assert response.status_code == 200
    assert response.json()["identity"]["tenantId"] == "t1"
    assert response.json()["mode"] == "ai_base_sso_jwt"


def test_auth_session_rejects_expired_cookie() -> None:
    isolated_client = TestClient(app)
    with patch("backend.services.identity_service.resolve_auth_session", return_value=None):
        response = isolated_client.get("/api/auth/session", cookies={"kb_session": "expired"})

    assert response.status_code == 401


def test_identity_delta_sync_requires_sso_super_admin() -> None:
    isolated_client = TestClient(app)

    response = isolated_client.post("/api/identity/sync-delta")

    assert response.status_code == 401

    regular_identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        is_authenticated=True,
        is_tenant_admin=False,
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=regular_identity), patch(
        "backend.routes.identity.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        response = isolated_client.post(
            "/api/identity/sync-delta",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "u1"},
        )

    assert response.status_code == 403
    sync_mock.assert_not_called()

    header_admin_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        source="identity_snapshot",
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=header_admin_identity), patch(
        "backend.routes.identity.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        response = isolated_client.post(
            "/api/identity/sync-delta",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 403
    sync_mock.assert_not_called()

    sso_admin_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("superManager",),
        source="ai_base_sso_authorization_code",
    )
    with patch("backend.services.identity_service.resolve_auth_session", return_value=sso_admin_identity), patch(
        "backend.routes.identity.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        sync_mock.return_value = {"mode": "http_delta", "counts": {}}
        response = isolated_client.post(
            "/api/identity/sync-delta?last_sync_at=2026-06-24T00:00:00%2B08:00",
            cookies={"kb_session": "session-token"},
        )

    assert response.status_code == 200
    sync_mock.assert_awaited_once_with(last_sync_at="2026-06-24T00:00:00+08:00")


def test_identity_delta_sync_rejects_non_sso_platform_admin() -> None:
    isolated_client = TestClient(app)
    platform_identity = IdentityContext(
        tenant_id="t1",
        user_id="platform-admin",
        is_authenticated=True,
        is_platform_admin=True,
        source="identity_snapshot",
    )

    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=platform_identity), patch(
        "backend.routes.identity.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        response = isolated_client.post(
            "/api/identity/sync-delta",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "platform-admin"},
        )

    assert response.status_code == 403
    sync_mock.assert_not_called()


def test_identity_delta_sync_rejects_sso_platform_admin_without_super_manager() -> None:
    isolated_client = TestClient(app)
    platform_identity = IdentityContext(
        tenant_id="t1",
        user_id="platform-admin",
        is_authenticated=True,
        is_platform_admin=True,
        role_codes=("platformAdmin",),
        source="ai_base_sso_authorization_code",
    )

    with patch("backend.services.identity_service.resolve_auth_session", return_value=platform_identity), patch(
        "backend.routes.identity.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        response = isolated_client.post(
            "/api/identity/sync-delta",
            cookies={"kb_session": "session-token"},
        )

    assert response.status_code == 403
    sync_mock.assert_not_called()


def test_identity_sync_status_requires_authenticated_super_manager() -> None:
    isolated_client = TestClient(app)

    response = isolated_client.get("/api/identity/sync-status")

    assert response.status_code == 401

    regular_identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        is_authenticated=True,
        is_tenant_admin=False,
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=regular_identity), patch(
        "backend.routes.identity.get_identity_sync_status",
        return_value={"enabled": True},
    ) as status_mock:
        response = isolated_client.get(
            "/api/identity/sync-status",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "u1"},
        )

    assert response.status_code == 403
    status_mock.assert_not_called()

    admin_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=admin_identity), patch(
        "backend.routes.identity.get_identity_sync_status",
        return_value={"enabled": True},
    ) as status_mock:
        response = isolated_client.get(
            "/api/identity/sync-status",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 403
    status_mock.assert_not_called()

    super_manager_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("superManager",),
    )
    status_payload = {
        "enabled": True,
        "running": True,
        "intervalSeconds": 300,
        "latestWatermark": "2026-06-24T10:30:00+08:00",
        "lastRun": {"status": "success"},
    }
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=super_manager_identity), patch(
        "backend.routes.identity.get_identity_sync_status",
        return_value=status_payload,
    ) as status_mock:
        response = isolated_client.get(
            "/api/identity/sync-status",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 200
    assert response.json()["latestWatermark"] == "2026-06-24T10:30:00+08:00"
    status_mock.assert_called_once_with()


def test_ai_base_logout_callback_requires_server_credentials(monkeypatch) -> None:
    isolated_client = TestClient(app)
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "wisewe-kb")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}), patch(
        "backend.routes.identity.revoke_auth_sessions_for_identity",
    ) as revoke_mock:
        response = isolated_client.post(
            "/api/auth/ai-base/logout-callback",
            json={"tenantId": "t1", "userId": "u1", "reason": "single_logout"},
            headers={"X-Client-Id": "wisewe-kb", "X-Client-Secret": "wrong"},
        )

    assert response.status_code == 403
    revoke_mock.assert_not_called()


def test_ai_base_logout_callback_revokes_local_sessions(monkeypatch) -> None:
    isolated_client = TestClient(app)
    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "wisewe-kb")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")

    with patch("core.runtime_settings._load_overrides_from_db", return_value={}), patch(
        "backend.routes.identity.revoke_auth_sessions_for_identity",
        return_value=2,
    ) as revoke_mock, patch("backend.routes.identity.append_audit_log", return_value=True) as audit_mock:
        response = isolated_client.post(
            "/api/auth/ai-base/logout-callback",
            json={"tenantId": "t1", "userId": "u1", "reason": "single_logout"},
            headers={"X-Client-Id": "wisewe-kb", "X-Client-Secret": "secret"},
        )

    assert response.status_code == 200
    assert response.json()["revoked"] == 2
    revoke_mock.assert_called_once_with("t1", "u1")
    audit_record = audit_mock.call_args.args[0]
    assert audit_record.action == "sso.logout_callback"
    assert audit_record.metadata["tenantId"] == "t1"
    assert audit_record.metadata["userId"] == "u1"


def test_identity_sync_scheduler_run_once_skips_without_sso_super_admin() -> None:
    from backend.services import identity_sync_scheduler

    identity_sync_scheduler._STATE.update(  # noqa: SLF001
        {
            "running": False,
            "startedAt": "",
            "stoppedAt": "",
            "runCount": 0,
            "failureCount": 0,
            "lastRun": None,
        }
    )

    with patch(
        "backend.services.ai_base_sso_service.sync_identity_delta_from_ai_base",
        new_callable=AsyncMock,
    ) as sync_mock:
        result = asyncio.run(identity_sync_scheduler.run_identity_sync_once(last_sync_at="2026-06-24T10:30:00+08:00"))

    assert result["mode"] == "http_delta"
    assert result["status"] == "skipped"
    assert result["reasonCode"] == "SSO_SUPER_ADMIN_REQUIRED"
    assert result["lastSyncAt"] == "2026-06-24 10:30:00"
    sync_mock.assert_not_called()
    assert identity_sync_scheduler._STATE["runCount"] == 0  # noqa: SLF001
    assert identity_sync_scheduler._STATE["lastRun"]["status"] == "skipped"  # noqa: SLF001


def test_exchange_rejects_replayed_credential(monkeypatch) -> None:
    from backend.services.ai_base_sso_service import exchange_ai_base_credential

    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "wisewe-kb")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")

    with patch("backend.services.ai_base_sso_service.is_sso_credential_used", return_value=True):
        try:
            asyncio.run(exchange_ai_base_credential(code="used-code"))
        except Exception as exc:
            assert getattr(exc, "code") == "CREDENTIAL_REPLAYED"
        else:  # pragma: no cover
            raise AssertionError("expected replayed credential to be rejected")


def test_exchange_unwraps_ai_base_result_payload(monkeypatch) -> None:
    from backend.services.ai_base_sso_service import exchange_ai_base_credential

    monkeypatch.setenv("AI_BASE_SSO_BASE_URL", "https://ai-base.example.test")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_ID", "wisewe-kb")
    monkeypatch.setenv("AI_BASE_SSO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("AI_BASE_SSO_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/ai-base/callback")

    async def fake_post(self, url, json):
        assert url == "https://ai-base.example.test/ai/system/internal/sso/exchange"
        assert json["grant_type"] == "authorization_code"
        assert json["client_id"] == "wisewe-kb"
        return httpx.Response(200, json={"success": True, "code": 200, "msg": "ok", "data": _summary()})

    with patch("backend.services.ai_base_sso_service.is_sso_credential_used", return_value=False), patch(
        "httpx.AsyncClient.post",
        new=fake_post,
    ):
        summary = asyncio.run(exchange_ai_base_credential(code="one-time-code"))

    assert summary["tenant"]["tenant_id"] == "t1"
    assert summary["_auth_source"] == "ai_base_sso_authorization_code"


def test_refresh_current_user_snapshot_uses_server_headers() -> None:
    from backend.services.ai_base_sso_service import SsoConfig, refresh_current_user_snapshot

    identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        username="admin",
        display_name="Admin",
        tenant_name="Tenant",
        is_authenticated=True,
        is_tenant_admin=True,
    )
    config = SsoConfig(
        base_url="https://ai-base.example.test",
        client_id="rag-client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/auth/ai-base/callback",
    )

    async def fake_get(self, url, headers):
        assert url == "https://ai-base.example.test/ai/system/internal/identity/snapshot/users/u1?tenant_id=t1"
        assert headers == {"X-Client-Id": "rag-client", "X-Client-Secret": "secret"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "code": 200,
                "msg": "ok",
                "data": _summary() | {"generated_at": "2026-06-24T10:00:00+08:00"},
            },
        )

    with patch("httpx.AsyncClient.get", new=fake_get), patch(
        "backend.services.ai_base_sso_service.upsert_identity_snapshot_from_summary",
        return_value=identity,
    ):
        result = asyncio.run(refresh_current_user_snapshot(tenant_id="t1", user_id="u1", config=config))

    assert result["identity"]["tenantId"] == "t1"
    assert result["snapshotVersion"] == "v1"
    assert result["generatedAt"] == "2026-06-24T10:00:00+08:00"


def test_sync_identity_delta_uses_watermark_and_records_run() -> None:
    from backend.services.ai_base_sso_service import SsoConfig, sync_identity_delta_from_ai_base

    config = SsoConfig(
        base_url="https://ai-base.example.test",
        client_id="rag-client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/auth/ai-base/callback",
    )
    delta_payload = {
        "max_updated_at": "2026-06-24T10:30:00+08:00",
        "snapshot_version": "identity-v2",
        "generated_at": "2026-06-24T10:31:00+08:00",
        "has_more": True,
        "tenants": [{"tenant_id": "t1", "tenant_name": "Tenant", "status": "active"}],
        "deleted": {"user_ids": ["u9"]},
    }
    counts = {"tenants": 1, "users": 0, "roles": 0, "user_roles": 0, "deleted": 1}

    async def fake_get(self, url, headers):
        assert url == "https://ai-base.example.test/ai/system/internal/identity/snapshot/delta?last_sync_at=2026-06-24+10%3A30%3A00"
        assert headers == {"X-Client-Id": "rag-client", "X-Client-Secret": "secret"}
        return httpx.Response(200, json={"success": True, "data": delta_payload})

    with patch("httpx.AsyncClient.get", new=fake_get), patch(
        "backend.services.ai_base_sso_service.get_latest_identity_sync_watermark",
        return_value="2026-06-24T10:30:00+08:00",
    ), patch(
        "backend.services.ai_base_sso_service.upsert_identity_delta_snapshot",
        return_value=counts,
    ) as upsert_mock, patch(
        "backend.services.ai_base_sso_service.record_identity_sync_run",
    ) as record_mock:
        result = asyncio.run(sync_identity_delta_from_ai_base(config=config))

    upsert_mock.assert_called_once_with(delta_payload)
    record_mock.assert_called_once()
    record_kwargs = record_mock.call_args.kwargs
    assert record_kwargs["sync_mode"] == "http_delta"
    assert record_kwargs["last_sync_at"] == "2026-06-24 10:30:00"
    assert record_kwargs["max_updated_at"] == "2026-06-24 10:30:00"
    assert record_kwargs["snapshot_version"] == "identity-v2"
    assert record_kwargs["has_more"] is True
    assert result["counts"] == counts
    assert result["lastSyncAt"] == "2026-06-24 10:30:00"
    assert result["maxUpdatedAt"] == "2026-06-24 10:30:00"


def test_sync_identity_delta_first_run_sends_initial_watermark() -> None:
    from backend.services.ai_base_sso_service import SsoConfig, sync_identity_delta_from_ai_base

    config = SsoConfig(
        base_url="https://ai-base.example.test",
        client_id="rag-client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/auth/ai-base/callback",
    )
    counts = {"tenants": 0, "users": 0, "roles": 0, "user_roles": 0, "deleted": 0}

    async def fake_get(self, url, headers):
        assert url == "https://ai-base.example.test/ai/system/internal/identity/snapshot/delta?last_sync_at=2000-01-01+00%3A00%3A00"
        assert headers == {"X-Client-Id": "rag-client", "X-Client-Secret": "secret"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "max_updated_at": "2026-06-24 10:31:00",
                    "snapshot_version": "identity-v3",
                    "tenants": [],
                    "users": [],
                    "roles": [],
                    "user_roles": [],
                    "deleted": [],
                },
            },
        )

    with patch("httpx.AsyncClient.get", new=fake_get), patch(
        "backend.services.ai_base_sso_service.get_latest_identity_sync_watermark",
        return_value=None,
    ), patch(
        "backend.services.ai_base_sso_service.upsert_identity_delta_snapshot",
        return_value=counts,
    ), patch(
        "backend.services.ai_base_sso_service.record_identity_sync_run",
    ) as record_mock:
        result = asyncio.run(sync_identity_delta_from_ai_base(config=config))

    record_kwargs = record_mock.call_args.kwargs
    assert record_kwargs["last_sync_at"] == "2000-01-01 00:00:00"
    assert record_kwargs["max_updated_at"] == "2026-06-24 10:31:00"
    assert result["lastSyncAt"] == "2000-01-01 00:00:00"
    assert result["maxUpdatedAt"] == "2026-06-24 10:31:00"


def test_sync_identity_delta_ignores_legacy_next_cursor_watermark() -> None:
    from backend.services.ai_base_sso_service import SsoConfig, sync_identity_delta_from_ai_base

    config = SsoConfig(
        base_url="https://ai-base.example.test",
        client_id="rag-client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/auth/ai-base/callback",
    )
    counts = {"tenants": 0, "users": 0, "roles": 0, "user_roles": 0, "deleted": 0}

    async def fake_get(self, url, headers):
        assert url == "https://ai-base.example.test/ai/system/internal/identity/snapshot/delta?last_sync_at=2000-01-01+00%3A00%3A00"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "next_cursor": "2026-06-24T10:31:00+08:00",
                    "snapshot_version": "identity-v3",
                    "tenants": [],
                    "users": [],
                    "roles": [],
                    "user_roles": [],
                    "deleted": [],
                },
            },
        )

    with patch("httpx.AsyncClient.get", new=fake_get), patch(
        "backend.services.ai_base_sso_service.get_latest_identity_sync_watermark",
        return_value=None,
    ), patch(
        "backend.services.ai_base_sso_service.upsert_identity_delta_snapshot",
        return_value=counts,
    ), patch(
        "backend.services.ai_base_sso_service.record_identity_sync_run",
    ) as record_mock:
        result = asyncio.run(sync_identity_delta_from_ai_base(config=config))

    record_kwargs = record_mock.call_args.kwargs
    assert record_kwargs["max_updated_at"] is None
    assert result["maxUpdatedAt"] == ""


def test_identity_delta_shape_uses_supported_aliases_and_warns_on_empty_edges() -> None:
    from backend.services.ai_base_sso_service import _identity_delta_shape, _identity_delta_source_schema

    shape = _identity_delta_shape(
        {
            "tenantList": [{"tenantId": "t1", "tenantName": "Tenant"}],
            "userList": [],
            "roleList": [],
            "userRoles": [],
            "deletedList": [{"entityType": "user", "entityId": "u9"}],
            "max_updated_at": "2026-06-24 10:31:00",
        }
    )

    assert shape["listLengths"] == {
        "tenants": 1,
        "users": 0,
        "roles": 0,
        "user_roles": 0,
        "deleted": 1,
    }
    assert shape["sampleKeys"]["tenants"] == ["tenantId", "tenantName"]
    assert shape["sampleKeys"]["deleted"] == ["entityId", "entityType"]
    assert shape["warnings"] == ["tenants_non_empty_but_identity_edges_empty"]
    assert _identity_delta_source_schema(shape) == (
        "delta_shape tenants:1,users:0,roles:0,user_roles:0,deleted:1;"
        "warnings:tenants_non_empty_but_identity_edges_empty"
    )


def test_format_identity_sync_timestamp_contract() -> None:
    from datetime import datetime

    from backend.services.ai_base_sso_service import format_identity_sync_timestamp

    assert format_identity_sync_timestamp(None) == ""
    assert format_identity_sync_timestamp("") == ""
    assert format_identity_sync_timestamp("2026-06-24T10:30:00+08:00") == "2026-06-24 10:30:00"
    assert format_identity_sync_timestamp("2026-06-24 10:30:00") == "2026-06-24 10:30:00"
    assert format_identity_sync_timestamp(datetime(2026, 6, 24, 10, 30, 0)) == "2026-06-24 10:30:00"
