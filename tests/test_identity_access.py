from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from core.db.identity import (
    IdentityContext,
    latest_identity_snapshot_synced_at,
    list_identity_snapshot_users,
    resolve_identity_snapshot,
    upsert_identity_delta_snapshot,
)
from core.db.knowledge_base import delete_knowledge_base, list_knowledge_bases, transfer_knowledge_base_owner
from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.identity_service import assert_fresh_identity_snapshot, get_current_identity, identity_snapshot_freshness
from backend.services.rag_service import _token_usage_totals, run_graph_rag_query, run_rag_query


class _Cursor:
    def __init__(self, *, rows=None, row=None, rowcount: int = 0) -> None:
        self.rows = rows or []
        self.row = row
        self.rowcount = rowcount
        self.executed: list[tuple[str, tuple]] = []
        self.description = [
            ("id",),
            ("name",),
            ("description",),
            ("default_strategy",),
            ("tenant_id",),
            ("created_by",),
            ("owner_user_id",),
            ("status",),
            ("deleted_at",),
            ("created_at",),
            ("doc_count",),
            ("chunk_count",),
            ("last_updated",),
        ]

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_obj = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_resolve_identity_snapshot_marks_super_manager() -> None:
    cursor = _Cursor(row=("u1", "t1", "zhangsan", "张三", "第一租户", True))
    conn = _Conn(cursor)

    with patch("core.db.identity.get_db_connection", return_value=conn):
        identity = resolve_identity_snapshot("t1", "u1")

    assert identity is not None
    assert identity.tenant_id == "t1"
    assert identity.user_id == "u1"
    assert identity.is_tenant_admin is True
    assert identity.is_authenticated is True
    assert cursor.executed[0][1] == ("superManager", "t1", "u1")
    assert conn.closed is True


def test_list_identity_snapshot_users_returns_sanitized_login_options() -> None:
    cursor = _Cursor(
        rows=[
            ("t1", "u1", "admin", "管理员", "租户一", ["superManager"], ["超级管理员"], True, None),
            ("t1", "u2", "teacher", "教师", "租户一", [], [], False, None),
        ]
    )
    conn = _Conn(cursor)

    with patch("core.db.identity.get_db_connection", return_value=conn):
        users = list_identity_snapshot_users(limit=5)

    assert users == [
        {
            "tenantId": "t1",
            "userId": "u1",
            "username": "admin",
            "displayName": "管理员",
            "tenantName": "租户一",
            "roleCodes": ["superManager"],
            "roleNames": ["超级管理员"],
            "ragRole": "租户管理员",
            "isTenantAdmin": True,
            "source": "identity_snapshot",
            "syncedAt": "",
        },
        {
            "tenantId": "t1",
            "userId": "u2",
            "username": "teacher",
            "displayName": "教师",
            "tenantName": "租户一",
            "roleCodes": [],
            "roleNames": [],
            "ragRole": "普通用户",
            "isTenantAdmin": False,
            "source": "identity_snapshot",
            "syncedAt": "",
        },
    ]
    assert "sys_user.password" not in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("superManager", 5)
    assert conn.closed is True


def test_upsert_identity_delta_snapshot_accepts_camel_case_payload() -> None:
    cursor = _Cursor()
    conn = _Conn(cursor)
    payload = {
        "tenantList": [
            {
                "tenantId": "t1",
                "tenantName": "租户一",
                "tenantCode": "tenant-one",
                "status": "active",
                "updatedAt": "2026-06-25 10:00:00",
            }
        ],
        "userList": [
            {
                "userId": "u1",
                "tenantId": "t1",
                "userName": "admin",
                "displayName": "管理员",
                "status": "active",
                "updatedAt": "2026-06-25 10:01:00",
            }
        ],
        "roleList": [
            {
                "roleId": "r1",
                "tenantId": "t1",
                "roleCode": "superManager",
                "roleName": "超级管理员",
                "status": "active",
                "updatedAt": "2026-06-25 10:02:00",
            }
        ],
        "userRoles": [
            {
                "relationId": "ur1",
                "tenantId": "t1",
                "userId": "u1",
                "roleId": "r1",
                "relationStatus": "active",
                "updatedAt": "2026-06-25 10:03:00",
            }
        ],
        "deleted": {"userIds": ["u9"], "userRoleRelationIds": ["ur9"]},
    }

    with patch("core.db.identity.get_db_connection", return_value=conn):
        counts = upsert_identity_delta_snapshot(payload)

    assert counts == {"tenants": 1, "users": 1, "roles": 1, "user_roles": 1, "deleted": 2}
    assert len(cursor.executed) == 7
    assert conn.committed is True
    assert conn.closed is True


def test_upsert_identity_delta_user_deleted_disabled_flags_keep_zero_active() -> None:
    cursor = _Cursor()
    conn = _Conn(cursor)
    payload = {
        "users": [
            {
                "user_id": "u1",
                "tenant_id": "t1",
                "username": "teacher",
                "deleted": 0,
                "disabled": 0,
                "updated_at": "2026-06-26 10:00:00",
            }
        ]
    }

    with patch("core.db.identity.get_db_connection", return_value=conn):
        counts = upsert_identity_delta_snapshot(payload)

    assert counts["users"] == 1
    user_sql, user_params = cursor.executed[0]
    assert "INSERT INTO kb_identity_users" in user_sql
    assert user_params[6] == "active"
    assert user_params[7] == "active"
    assert all("UPDATE knowledge_bases" not in sql for sql, _ in cursor.executed)


def test_upsert_identity_delta_disabled_user_marks_owned_kb_pending_transfer() -> None:
    cursor = _Cursor()
    conn = _Conn(cursor)
    payload = {
        "userList": [
            {
                "userId": "u1",
                "tenantId": "t1",
                "userName": "teacher",
                "deleted": 0,
                "disabled": 1,
                "updatedAt": "2026-06-26 10:00:00",
            }
        ]
    }

    with patch("core.db.identity.get_db_connection", return_value=conn):
        counts = upsert_identity_delta_snapshot(payload)

    assert counts["users"] == 1
    user_sql, user_params = cursor.executed[0]
    assert "INSERT INTO kb_identity_users" in user_sql
    assert user_params[6] == "disabled"
    assert user_params[7] == "disabled"
    transfer_sql, transfer_params = cursor.executed[1]
    assert "UPDATE knowledge_bases" in transfer_sql
    assert "owner_status = 'pending_transfer'" in transfer_sql
    assert transfer_params == ("disabled", "t1", "t1", "u1", "u1")


def test_current_identity_keeps_legacy_header_bootstrap_enabled_by_default() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    with patch("backend.services.identity_service.resolve_runtime_setting", return_value=(True, "code")), patch(
        "backend.services.identity_service.resolve_identity_snapshot",
        return_value=identity,
    ) as resolve_snapshot:
        result = get_current_identity(session_token=None, tenant_id="t1", user_id="u1")

    assert result == identity
    resolve_snapshot.assert_called_once_with("t1", "u1")


def test_current_identity_rejects_legacy_headers_when_disabled() -> None:
    with patch("backend.services.identity_service.resolve_runtime_setting", return_value=(False, "env")), patch(
        "backend.services.identity_service.resolve_identity_snapshot",
    ) as resolve_snapshot:
        try:
            get_current_identity(session_token=None, tenant_id="t1", user_id="u1")
        except HTTPException as exc:
            assert exc.status_code == 401
            assert "Legacy X-KB-* header authentication is disabled" in str(exc.detail)
        else:  # pragma: no cover
            raise AssertionError("expected legacy header auth to be rejected")

    resolve_snapshot.assert_not_called()


def test_latest_identity_snapshot_synced_at_returns_utc_timestamp() -> None:
    synced_at = datetime(2026, 6, 25, 10, 30, 0)
    cursor = _Cursor(row=(synced_at,))
    conn = _Conn(cursor)

    with patch("core.db.identity.get_db_connection", return_value=conn):
        result = latest_identity_snapshot_synced_at("t1", "u1")

    assert result == synced_at.replace(tzinfo=timezone.utc)
    assert cursor.executed[0][1] == ("t1", "u1")
    assert conn.closed is True


def test_identity_snapshot_freshness_only_enforces_formal_sso() -> None:
    legacy_identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        is_authenticated=True,
        source="identity_snapshot",
    )

    with patch("backend.services.identity_service.latest_identity_snapshot_synced_at") as latest_mock:
        freshness = identity_snapshot_freshness(legacy_identity)

    assert freshness["enforced"] is False
    assert freshness["fresh"] is True
    latest_mock.assert_not_called()


def test_assert_fresh_identity_snapshot_rejects_stale_sso_and_audits() -> None:
    identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        is_authenticated=True,
        source="ai_base_sso_authorization_code",
    )
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=1200)

    with patch("backend.services.identity_service.resolve_runtime_setting", return_value=(600, "code")), patch(
        "backend.services.identity_service.latest_identity_snapshot_synced_at",
        return_value=stale_at,
    ), patch("backend.services.identity_service.append_audit_log", return_value=True) as audit_mock:
        try:
            assert_fresh_identity_snapshot(
                identity,
                action="settings.update",
                resource_type="settings",
                resource_id="runtime",
            )
        except HTTPException as exc:
            assert exc.status_code == 403
            assert exc.detail["code"] == "IDENTITY_SNAPSHOT_STALE"
        else:  # pragma: no cover
            raise AssertionError("expected stale SSO identity snapshot to be rejected")

    audit_mock.assert_called_once()
    audit_record = audit_mock.call_args.args[0]
    assert audit_record.action == "access.denied"
    assert audit_record.outcome == "denied"
    assert audit_record.metadata["reasonCode"] == "IDENTITY_SNAPSHOT_STALE"


def test_list_knowledge_bases_filters_to_owner_for_regular_user() -> None:
    cursor = _Cursor(rows=[])
    conn = _Conn(cursor)
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    with patch("core.db.knowledge_base.get_db_connection", return_value=conn):
        result = list_knowledge_bases(identity)

    sql, params = cursor.executed[0]
    assert result == []
    assert "kb.deleted_at IS NULL" in sql
    assert "kb.tenant_id = %s" in sql
    assert "kb.owner_user_id = %s" in sql
    assert params == ("t1", "u1")


def test_list_knowledge_bases_tenant_admin_skips_owner_filter() -> None:
    cursor = _Cursor(rows=[])
    conn = _Conn(cursor)
    identity = IdentityContext(tenant_id="t1", user_id="admin", is_authenticated=True, is_tenant_admin=True)

    with patch("core.db.knowledge_base.get_db_connection", return_value=conn):
        list_knowledge_bases(identity)

    sql, params = cursor.executed[0]
    assert "(kb.tenant_id = %s OR kb.tenant_id IS NULL)" in sql
    assert "kb.owner_user_id = %s" not in sql
    assert params == ("t1",)


def test_delete_knowledge_base_uses_soft_delete_and_access_filter() -> None:
    cursor = _Cursor(rowcount=1)
    conn = _Conn(cursor)
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    with patch("core.db.knowledge_base.get_db_connection", return_value=conn):
        deleted = delete_knowledge_base("kb-1", identity)

    sql, params = cursor.executed[0]
    assert deleted == 1
    assert "UPDATE knowledge_bases" in sql
    assert "deleted_at = NOW()" in sql
    assert "DELETE FROM knowledge_bases" not in sql
    assert params == ("kb-1", "t1", "u1")
    assert conn.committed is True


def test_transfer_knowledge_base_owner_requires_admin() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    try:
        transfer_knowledge_base_owner("kb-1", "u2", identity)
    except PermissionError as exc:
        assert "administrators" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected regular users to be rejected")


def test_transfer_knowledge_base_owner_sets_active_owner_status() -> None:
    cursor = _Cursor(
        rows=[("kb-1", "t1")],
        row=("kb-1", "t1"),
    )

    def _fetchone():
        if len(cursor.executed) == 1:
            return ("kb-1", "t1")
        if len(cursor.executed) == 2:
            return (1,)
        return (
            "kb-1",
            "教材库",
            "",
            "hierarchical",
            "t1",
            "u1",
            "u2",
            "active",
            None,
            "active",
            None,
            None,
        )

    cursor.fetchone = _fetchone
    cursor.description = [
        ("id",),
        ("name",),
        ("description",),
        ("default_strategy",),
        ("tenant_id",),
        ("created_by",),
        ("owner_user_id",),
        ("owner_status",),
        ("owner_invalid_reason",),
        ("status",),
        ("deleted_at",),
        ("created_at",),
    ]
    conn = _Conn(cursor)
    identity = IdentityContext(tenant_id="t1", user_id="admin", is_authenticated=True, is_tenant_admin=True)

    with patch("core.db.knowledge_base.get_db_connection", return_value=conn):
        kb = transfer_knowledge_base_owner("kb-1", "u2", identity)

    assert kb is not None
    assert kb["owner_user_id"] == "u2"
    assert kb["owner_status"] == "active"
    assert kb["owner_invalid_reason"] is None
    assert "kb.id = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("t1", "kb-1")
    assert cursor.executed[1][1] == ("t1", "u2")
    assert cursor.executed[2][1] == ("u2", "admin", "active", "kb-1")
    assert conn.committed is True


def test_rag_query_rejects_inaccessible_kb_before_pipeline() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)
    payload = QueryRequest(query="test", kb_id="kb-private")

    with patch("backend.services.rag_service.get_knowledge_base", return_value=None), patch(
        "backend.services.rag_service.run_rag_pipeline"
    ) as pipeline:
        try:
            run_rag_query(payload, identity)
        except ValueError as exc:
            assert "not accessible" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected inaccessible KB to be rejected")

    pipeline.assert_not_called()


def test_graph_rag_query_checks_kb_access_before_pipeline() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="admin", is_authenticated=True, is_tenant_admin=True)
    payload = GraphQueryRequest(query="test", kb_id="kb-1")
    graph_payload = {
        "intent": "fact",
        "intent_source": "explicit",
        "results": [],
        "stats": {},
    }

    with patch("backend.services.rag_service.get_knowledge_base", return_value={"id": "kb-1"}), patch(
        "backend.services.rag_service.run_graph_rag_pipeline",
        return_value=graph_payload,
    ) as pipeline, patch("backend.services.rag_service.append_rag_query_log") as append_log:
        result = run_graph_rag_query(payload, identity, request_id="req-graph")

    assert result["kbId"] == "kb-1"
    assert result["requestId"] == "req-graph"
    pipeline.assert_called_once()
    append_log.assert_called_once()


def test_token_usage_totals_sums_pipeline_stages() -> None:
    prompt, completion, total = _token_usage_totals(
        {
            "generatePromptTokens": 10,
            "generateCompletionTokens": 4,
            "generateTotalTokens": 14,
            "scorePromptTokens": 3,
            "scoreCompletionTokens": 2,
            "scoreTotalTokens": 5,
        }
    )

    assert (prompt, completion, total) == (13, 6, 19)
