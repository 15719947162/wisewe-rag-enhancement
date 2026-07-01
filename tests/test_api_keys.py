from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import app
from core.db.api_keys import ApiKeyAuthResult, ApiKeyError
from core.db.identity import IdentityContext


client = TestClient(app)


def _api_key_payload() -> dict:
    return {
        "id": "ak_test",
        "appId": None,
        "name": "demo",
        "tenantId": "t1",
        "createdBy": "u1",
        "keyPrefix": "wwkb_ak_test_abc",
        "keySuffix": "12345678",
        "status": "active",
        "kbIds": ["kb-1"],
        "capabilities": ["rag.query"],
        "requireSignature": True,
        "allowedIps": [],
        "rpmLimit": 0,
        "dailyRequestLimit": 0,
        "note": "",
        "expiresAt": None,
        "lastUsedAt": None,
        "createdAt": "2026-06-20T00:00:00+00:00",
        "updatedAt": "2026-06-20T00:00:00+00:00",
        "deletedAt": None,
    }


def test_console_api_key_lifecycle_routes() -> None:
    payload = {**_api_key_payload(), "plainKey": "wwkb_ak_test_secret"}

    with patch("backend.routes.console.get_console_api_keys", return_value=[_api_key_payload()]) as list_mock:
        response = client.get("/api/console/api-keys")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "ak_test"
    list_mock.assert_called_once_with(None)

    with patch("backend.routes.console.create_console_api_key", return_value=payload) as create_mock:
        response = client.post(
            "/api/console/api-keys",
            json={
                "name": "demo",
                "kbIds": ["kb-1"],
                "capabilities": ["rag.query"],
                "note": "",
            },
        )

    assert response.status_code == 201
    assert response.json()["plainKey"] == "wwkb_ak_test_secret"
    create_mock.assert_called_once()

    with patch("backend.routes.console.update_console_api_key", return_value={**_api_key_payload(), "status": "disabled"}):
        response = client.patch("/api/console/api-keys/ak_test", json={"status": "disabled"})

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"

    with patch("backend.routes.console.rotate_console_api_key", return_value=payload):
        response = client.post("/api/console/api-keys/ak_test/rotate")

    assert response.status_code == 200
    assert response.json()["plainKey"] == "wwkb_ak_test_secret"

    with patch("backend.routes.console.delete_console_api_key", return_value=True):
        response = client.delete("/api/console/api-keys/ak_test")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "id": "ak_test"}


def test_console_api_key_management_requires_admin_when_identity_present() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True, is_tenant_admin=False)

    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=identity):
        response = client.post(
            "/api/console/api-keys",
            json={"name": "demo", "kbIds": ["kb-1"], "capabilities": ["rag.query"]},
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "u1"},
        )

    assert response.status_code == 403


def test_console_openapi_app_lifecycle_routes() -> None:
    payload = {
        "id": "app_test",
        "name": "AI 基座用户端",
        "tenantId": "t1",
        "ownerUserId": "u1",
        "status": "active",
        "note": "demo",
        "createdAt": "2026-06-26T00:00:00+00:00",
        "updatedAt": "2026-06-26T00:00:00+00:00",
        "deletedAt": None,
    }

    with patch("backend.routes.console.get_console_openapi_apps", return_value=[payload]) as list_mock:
        response = client.get("/api/console/openapi-apps")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "app_test"
    list_mock.assert_called_once_with(None)

    with patch("backend.routes.console.create_console_openapi_app", return_value=payload) as create_mock:
        response = client.post("/api/console/openapi-apps", json={"name": "AI 基座用户端", "note": "demo"})

    assert response.status_code == 201
    assert response.json()["name"] == "AI 基座用户端"
    create_mock.assert_called_once()

    with patch("backend.routes.console.update_console_openapi_app", return_value={**payload, "status": "disabled"}):
        response = client.patch("/api/console/openapi-apps/app_test", json={"status": "disabled"})

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"

    with patch("backend.routes.console.delete_console_openapi_app", return_value=True):
        response = client.delete("/api/console/openapi-apps/app_test")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "id": "app_test"}


def test_openapi_accepts_bearer_api_key() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("rag.query",),
        kb_ids=("kb-1",),
        require_signature=False,
    )

    with patch("backend.routes.openapi_v1.authenticate_api_key", return_value=auth) as auth_mock, patch(
        "backend.routes.openapi_v1.run_rag_query",
        return_value={"kbId": "kb-1", "answer": "ok"},
    ) as run_query:
        response = client.post(
            "/openapi/v1/rag/query",
            json={"query": "test", "kb_id": "kb-1"},
            headers={"Authorization": "Bearer wwkb_ak_test_secret"},
        )

    assert response.status_code == 200
    assert auth_mock.call_args.args[0] == "wwkb_ak_test_secret"
    assert auth_mock.call_args.kwargs["kb_id"] == "kb-1"
    assert auth_mock.call_args.kwargs["capability"] == "rag.query"
    assert auth_mock.call_args.kwargs["signature"] is not None
    assert auth_mock.call_args.kwargs["client_ip"]
    assert run_query.call_args.args[1].source == "api_key"


def test_openapi_rejects_api_key_capability_denied() -> None:
    with patch(
        "backend.routes.openapi_v1.authenticate_api_key",
        side_effect=ApiKeyError("CAPABILITY_DENIED", "API Key lacks the required capability"),
    ):
        response = client.post(
            "/openapi/v1/rag/graph-query",
            json={"query": "test", "kb_id": "kb-1"},
            headers={"X-API-Key": "wwkb_ak_test_secret"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CAPABILITY_DENIED"


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.description = [
            ("id",),
            ("app_id",),
            ("name",),
            ("tenant_id",),
            ("created_by",),
            ("key_prefix",),
            ("key_suffix",),
            ("status",),
            ("kb_ids",),
            ("capabilities",),
            ("require_signature",),
            ("allowed_ips",),
            ("rpm_limit",),
            ("daily_request_limit",),
            ("note",),
            ("expires_at",),
            ("last_used_at",),
            ("created_at",),
            ("updated_at",),
            ("deleted_at",),
        ]
        self.row = (
            "ak_test",
            None,
            "demo",
            "t1",
            "u1",
            "wwkb_ak_test_abc",
            "12345678",
            "active",
            ["kb-1"],
            ["rag.query"],
            True,
            [],
            0,
            0,
            "",
            None,
            None,
            datetime(2026, 6, 20, tzinfo=timezone.utc),
            datetime(2026, 6, 20, tzinfo=timezone.utc),
            None,
        )

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self) -> None:
        self.cursor_obj = _Cursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_create_api_key_hashes_plain_key_before_storage() -> None:
    from core.db.api_keys import create_api_key

    conn = _Conn()
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True, is_tenant_admin=True)

    with patch("core.db.api_keys.get_db_connection", return_value=conn), patch(
        "core.db.api_keys.ensure_db_schema"
    ), patch("core.db.api_keys._new_key_id", return_value="ak_test"), patch(
        "core.db.api_keys._new_secret", return_value="secretsecretsecretsecretsecretsecretsecret"
    ):
        created = create_api_key(name="demo", kb_ids=["kb-1"], capabilities=["rag.query"], identity=identity)

    plain = created["plainKey"]
    sql, params = conn.cursor_obj.executed[0]
    assert "INSERT INTO kb_api_keys" in sql
    assert plain.startswith("wwkb_ak_test_")
    assert plain not in params
    assert len(params[5]) == 64
    assert params[6] == plain[:16]
    assert params[7] == plain[-8:]
    assert conn.committed is True


class _AuthCursor:
    def __init__(self, row) -> None:
        self.row = row
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _AuthConn:
    def __init__(self, row) -> None:
        self.cursor_obj = _AuthCursor(row)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_authenticate_api_key_marks_usage_only_after_success() -> None:
    from core.db.api_keys import _hash_key, authenticate_api_key

    plain_key = "wwkb_ak_test_secret"
    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, False, [])
    read_conn = _AuthConn(row)
    mark_conn = _AuthConn(None)

    with patch("core.db.api_keys.get_db_connection", side_effect=[read_conn, mark_conn]), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        result = authenticate_api_key(plain_key, kb_id="kb-1", capability="rag.query")

    assert result.api_key_id == "ak_test"
    assert read_conn.cursor_obj.executed[0][1] == (_hash_key(plain_key),)
    assert "last_used_at" in mark_conn.cursor_obj.executed[0][0]


def test_authenticate_api_key_does_not_mark_usage_when_capability_denied() -> None:
    from core.db.api_keys import authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, False, [])
    read_conn = _AuthConn(row)

    with patch("core.db.api_keys.get_db_connection", return_value=read_conn), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        try:
            authenticate_api_key("wwkb_ak_test_secret", kb_id="kb-1", capability="rag.graph_query")
        except ApiKeyError as exc:
            assert exc.code == "CAPABILITY_DENIED"
        else:
            raise AssertionError("Expected ApiKeyError")

    assert len(read_conn.cursor_obj.executed) == 1


def test_authenticate_api_key_rejects_body_hash_mismatch_when_signature_required() -> None:
    from core.db.api_keys import ApiKeySignaturePayload, authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, True, [])
    read_conn = _AuthConn(row)
    signature = ApiKeySignaturePayload(
        method="POST",
        path="/openapi/v1/rag/query",
        body=b'{"kb_id":"kb-1","query":"test"}',
        timestamp=datetime.now(timezone.utc).isoformat(),
        nonce="nonce-1",
        body_sha256="bad",
        signature="bad",
    )

    with patch("core.db.api_keys.get_db_connection", return_value=read_conn), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        try:
            authenticate_api_key(
                "wwkb_ak_test_secret",
                kb_id="kb-1",
                capability="rag.query",
                signature=signature,
            )
        except ApiKeyError as exc:
            assert exc.code == "BODY_HASH_MISMATCH"
        else:
            raise AssertionError("Expected ApiKeyError")


def test_authenticate_api_key_accepts_valid_hmac_signature_and_ip_scope() -> None:
    from core.db.api_keys import ApiKeySignaturePayload, authenticate_api_key

    plain_key = "wwkb_ak_test_secret"
    body = b'{"kb_id":"kb-1","query":"test"}'
    body_hash = hashlib.sha256(body).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat()
    nonce = "nonce-2"
    canonical = "\n".join(["POST", "/openapi/v1/rag/query", timestamp, nonce, body_hash])
    request_signature = hmac.new(plain_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    signature = ApiKeySignaturePayload(
        method="POST",
        path="/openapi/v1/rag/query",
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_hash,
        signature=request_signature,
    )
    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, True, ["127.0.0.1/32"])
    read_conn = _AuthConn(row)
    mark_conn = _AuthConn(None)

    with patch("core.db.api_keys.get_db_connection", side_effect=[read_conn, mark_conn]), patch(
        "core.db.api_keys.ensure_db_schema"
    ), patch("core.db.api_keys._record_nonce") as nonce_mock:
        result = authenticate_api_key(
            plain_key,
            kb_id="kb-1",
            capability="rag.query",
            signature=signature,
            client_ip="127.0.0.1",
        )

    assert result.api_key_id == "ak_test"
    nonce_mock.assert_called_once()


def test_authenticate_api_key_rejects_when_minute_quota_exceeded() -> None:
    from core.db.api_keys import authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, False, [], 1, 0, None)
    read_conn = _AuthConn(row)
    quota_conn = _AuthConn(None)
    quota_conn.cursor_obj.row = None

    with patch("core.db.api_keys.get_db_connection", side_effect=[read_conn, quota_conn]), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        try:
            authenticate_api_key("wwkb_ak_test_secret", kb_id="kb-1", capability="rag.query")
        except ApiKeyError as exc:
            assert exc.code == "RATE_LIMITED"
            assert exc.api_key_id == "ak_test"
        else:
            raise AssertionError("Expected ApiKeyError")

    assert "kb_api_key_usage_windows" in quota_conn.cursor_obj.executed[0][0]


def test_authenticate_api_key_rejects_disallowed_ip() -> None:
    from core.db.api_keys import authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["rag.query"], None, False, ["10.0.0.0/8"])
    read_conn = _AuthConn(row)

    with patch("core.db.api_keys.get_db_connection", return_value=read_conn), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        try:
            authenticate_api_key(
                "wwkb_ak_test_secret",
                kb_id="kb-1",
                capability="rag.query",
                client_ip="127.0.0.1",
            )
        except ApiKeyError as exc:
            assert exc.code == "IP_NOT_ALLOWED"
        else:
            raise AssertionError("Expected ApiKeyError")


def test_authenticate_api_key_accepts_wildcard_kb_for_capability_checks() -> None:
    from core.db.api_keys import authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["kb.list"], None, False, [])
    read_conn = _AuthConn(row)
    mark_conn = _AuthConn(None)

    with patch("core.db.api_keys.get_db_connection", side_effect=[read_conn, mark_conn]), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        result = authenticate_api_key("wwkb_ak_test_secret", kb_id="*", capability="kb.list")

    assert result.api_key_id == "ak_test"
    assert result.kb_ids == ("kb-1",)


def test_authenticate_api_key_force_signature_requires_signature_even_when_key_optional() -> None:
    from core.db.api_keys import authenticate_api_key

    row = ("ak_test", "t1", "active", ["kb-1"], ["ingestion.upload"], None, False, [])
    read_conn = _AuthConn(row)

    with patch("core.db.api_keys.get_db_connection", return_value=read_conn), patch(
        "core.db.api_keys.ensure_db_schema"
    ):
        try:
            authenticate_api_key(
                "wwkb_ak_test_secret",
                kb_id="kb-1",
                capability="ingestion.upload",
                force_signature=True,
            )
        except ApiKeyError as exc:
            assert exc.code == "SIGNATURE_REQUIRED"
        else:
            raise AssertionError("Expected ApiKeyError")
