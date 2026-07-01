from __future__ import annotations

from unittest.mock import patch

from core.db.identity import IdentityContext
from datetime import datetime, timezone

from core.db.query_logs import (
    AuditLogRecord,
    LlmCallLogRecord,
    RagQueryLogRecord,
    append_audit_log,
    append_llm_call_log,
    append_rag_query_log,
    export_rag_query_logs_csv,
    fetch_audit_logs,
    fetch_rag_query_logs,
    fetch_token_usage_summary,
    refresh_token_usage_hourly,
)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

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


class _ReadCursor:
    def __init__(self, fetchone_rows=None, fetchall_rows=None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def fetchone(self):
        return self.fetchone_rows.pop(0)

    def fetchall(self):
        return self.fetchall_rows.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ReadConn:
    def __init__(self, cursor: _ReadCursor) -> None:
        self.cursor_obj = cursor
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_append_rag_query_log_writes_sanitized_metadata_only() -> None:
    conn = _Conn()
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        ok = append_rag_query_log(
            RagQueryLogRecord(
                request_id="req-1",
                pipeline_domain="online_rag",
                kb_id="kb-1",
                query="What is a very secret full query?",
                answer="This is the full generated answer body.",
                identity=identity,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_ms=123,
            )
        )

    assert ok is True
    sql, params = conn.cursor_obj.executed[0]
    assert "kb_rag_query_logs" in sql
    assert params[0] == "req-1"
    assert params[1] == "online_rag"
    assert params[2] == "t1"
    assert params[3] == "u1"
    assert params[4] == "kb-1"
    assert len(params[6]) == 64
    assert params[9:12] == (False, None, None)
    assert params[12:15] == (10, 5, 15)
    assert conn.committed is True
    assert conn.closed is True


def test_append_rag_query_log_is_best_effort_when_db_unavailable() -> None:
    with patch("core.db.query_logs.get_db_connection", side_effect=RuntimeError("db down")):
        ok = append_rag_query_log(
            RagQueryLogRecord(
                request_id="req-1",
                pipeline_domain="online_rag",
                kb_id="kb-1",
                query="q",
            )
        )

    assert ok is False


def test_append_llm_call_log_writes_model_usage() -> None:
    conn = _Conn()
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        ok = append_llm_call_log(
            LlmCallLogRecord(
                request_id="task-1",
                pipeline_domain="ingestion",
                pipeline_stage="chunk",
                feature_name="三层切片增强",
                provider="dashscope",
                model_name="qwen-plus",
                kb_id="kb-1",
                identity=identity,
                prompt_tokens=100,
                completion_tokens=40,
                total_tokens=140,
                latency_ms=1200,
            )
        )

    assert ok is True
    sql, params = conn.cursor_obj.executed[0]
    assert "kb_llm_call_logs" in sql
    assert params[:11] == (
        "task-1",
        "t1",
        "u1",
        "kb-1",
        None,
        "ingestion",
        "chunk",
        "三层切片增强",
        "dashscope",
        "qwen-plus",
        "",
    )
    assert params[11:15] == (100, 40, 140, 1200)
    rollup_sql, rollup_params = conn.cursor_obj.executed[1]
    assert "kb_token_usage_hourly" in rollup_sql
    assert rollup_params[:6] == (
        "t1",
        "kb-1",
        "",
        "ingestion",
        "chunk",
        "三层切片增强",
    )
    assert rollup_params[6:11] == (100, 40, 140, 1200, 0)
    assert conn.committed is True


def test_refresh_token_usage_hourly_rebuilds_rollup_window() -> None:
    conn = _Conn()
    conn.cursor_obj.rowcount = 2
    start_at = datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        result = refresh_token_usage_hourly(start_at=start_at, end_at=end_at)

    assert result["refreshed"] is True
    assert "DELETE FROM kb_token_usage_hourly" in conn.cursor_obj.executed[0][0]
    assert conn.cursor_obj.executed[0][1] == (start_at, end_at)
    insert_sql, insert_params = conn.cursor_obj.executed[1]
    assert "INSERT INTO kb_token_usage_hourly" in insert_sql
    assert "SUM(prompt_tokens)" in insert_sql
    assert insert_params[-2:] == (start_at, end_at)
    assert conn.committed is True


def test_append_audit_log_writes_sanitized_governance_event() -> None:
    conn = _Conn()
    identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        username="alice",
        display_name="Alice",
        source="session",
        is_authenticated=True,
    )

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        ok = append_audit_log(
            AuditLogRecord(
                action="api_key.create",
                resource_type="api_key",
                resource_id="key-1",
                api_key_id="key-1",
                identity=identity,
                risk_level="high",
                summary="Created API Key",
                metadata={"name": "test", "plainKey": "secret-value", "capabilities": ["rag.query"]},
            )
        )

    assert ok is True
    sql, params = conn.cursor_obj.executed[0]
    assert "kb_audit_logs" in sql
    assert params[:10] == (
        None,
        "t1",
        "u1",
        "Alice",
        "session",
        "api_key.create",
        "api_key",
        "key-1",
        None,
        "key-1",
    )
    assert params[10:13] == ("success", "high", "Created API Key")
    assert '"plainKey": "***"' in params[13]
    assert "secret-value" not in params[13]
    assert conn.committed is True


def test_fetch_rag_query_logs_filters_and_maps_sanitized_payload() -> None:
    created_at = datetime(2026, 6, 19, 8, 30, tzinfo=timezone.utc)
    cursor = _ReadCursor(
        fetchall_rows=[
            [
                (
                    "req-1",
                    "online_rag",
                    "query",
                    "tenant-a",
                    "user-a",
                    "kb-a",
                    "key-a",
                    "hash-a",
                    "query summary",
                    "answer summary",
                    False,
                    0.8,
                    0.7,
                    12,
                    8,
                    20,
                    345,
                    "success",
                    None,
                    created_at,
                )
            ]
        ]
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        logs = fetch_rag_query_logs(kb_id="kb-a", actor_id="user-a", pipeline_domain="online_rag", limit=500)

    sql, params = cursor.executed[0]
    assert "WHERE kb_id = %s AND actor_id = %s AND pipeline_domain = %s" in sql
    assert params == ("kb-a", "user-a", "online_rag", 200)
    assert logs == [
        {
            "requestId": "req-1",
            "pipelineDomain": "online_rag",
            "pipelineStage": "query",
            "tenantId": "tenant-a",
            "actorId": "user-a",
            "kbId": "kb-a",
            "apiKeyId": "key-a",
            "queryHash": "hash-a",
            "querySummary": "query summary",
            "answerSummary": "answer summary",
            "cannotAnswer": False,
            "relevanceScore": 0.8,
            "faithfulnessScore": 0.7,
            "promptTokens": 12,
            "completionTokens": 8,
            "totalTokens": 20,
            "latencyMs": 345,
            "status": "success",
            "errorCode": None,
            "createdAt": created_at.isoformat(),
        }
    ]
    assert conn.closed is True


def test_fetch_rag_query_logs_supports_time_range_and_tenant_filter() -> None:
    cursor = _ReadCursor(fetchall_rows=[[]])
    conn = _ReadConn(cursor)
    start_at = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        logs = fetch_rag_query_logs(tenant_id="tenant-a", start_at=start_at, end_at=end_at, limit=20)

    sql, params = cursor.executed[0]
    assert "WHERE tenant_id = %s AND created_at >= %s AND created_at <= %s" in sql
    assert params == ("tenant-a", start_at, end_at, 20)
    assert logs == []


def test_fetch_audit_logs_filters_and_maps_payload() -> None:
    created_at = datetime(2026, 6, 23, 8, 30, tzinfo=timezone.utc)
    cursor = _ReadCursor(
        fetchall_rows=[
            [
                (
                    1,
                    "req-1",
                    "tenant-a",
                    "user-a",
                    "Alice",
                    "session",
                    "settings.update",
                    "settings",
                    "runtime",
                    None,
                    None,
                    "success",
                    "medium",
                    "Updated settings",
                    {"updatedKeys": ["RAG_TOP_K"]},
                    created_at,
                )
            ]
        ]
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        logs = fetch_audit_logs(
            tenant_id="tenant-a",
            action="settings.update",
            resource_type="settings",
            start_at=created_at,
            limit=500,
        )

    sql, params = cursor.executed[0]
    assert "WHERE tenant_id = %s AND action = %s AND resource_type = %s AND created_at >= %s" in sql
    assert params == ("tenant-a", "settings.update", "settings", created_at, 200)
    assert logs[0]["action"] == "settings.update"
    assert logs[0]["metadata"] == {"updatedKeys": ["RAG_TOP_K"]}
    assert logs[0]["createdAt"] == created_at.isoformat()


def test_export_rag_query_logs_csv_uses_sanitized_columns_only() -> None:
    created_at = datetime(2026, 6, 21, 8, 30, tzinfo=timezone.utc)
    cursor = _ReadCursor(
        fetchall_rows=[
            [
                (
                    "req-1",
                    "online_rag",
                    "query",
                    "tenant-a",
                    "user-a",
                    "kb-a",
                    "key-a",
                    "hash-a",
                    "query summary",
                    "answer summary",
                    False,
                    0.8,
                    0.7,
                    12,
                    8,
                    20,
                    345,
                    "success",
                    None,
                    created_at,
                )
            ]
        ]
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        filename, content = export_rag_query_logs_csv(tenant_id="tenant-a", limit=50000)

    csv_text = content.decode("utf-8-sig")
    header = csv_text.splitlines()[0].split(",")
    assert filename.startswith("rag-query-logs-")
    assert "requestId" in header
    assert "queryHash" in header
    assert "query" not in header
    assert "answer" not in header
    assert "prompt" not in header
    assert "apiKey" not in header
    assert "query summary" in csv_text
    assert "answer summary" in csv_text
    assert cursor.executed[0][1] == ("tenant-a", 50000)


def test_fetch_token_usage_summary_aggregates_supported_dimensions() -> None:
    cursor = _ReadCursor(
        fetchone_rows=[(3, 30, 15, 45, 120.5)],
        fetchall_rows=[
            [("online_rag", 2, 20, 10, 30, 100.0), ("graph_rag", 1, 10, 5, 15, 161.0)],
            [("kb-a", 2, 22, 11, 33, 90.0)],
            [("key-a", 1, 12, 6, 18, 80.0), ("console", 2, 18, 9, 27, 140.0)],
            [("chunk", "切片 LLM", 2, 100, 50, 150, 800.0, datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc))],
            [
                (
                    1,
                    "req-1",
                    "ingestion",
                    "chunk",
                    "切片 LLM",
                    "302AI",
                    "qwen-plus",
                    "2026-06",
                    80,
                    40,
                    120,
                    700,
                    "success",
                    None,
                    "kb-a",
                    "console",
                    datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
                )
            ],
            [
                (
                    datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
                    "ingestion",
                    "chunk",
                    "鍒囩墖 LLM",
                    2,
                    100,
                    50,
                    150,
                    1600,
                    0,
                    0,
                )
            ],
        ],
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        summary = fetch_token_usage_summary(limit=99)

    assert summary["source"] == "kb_llm_call_logs"
    assert summary["fallbackSource"] == "kb_rag_query_logs"
    assert summary["scope"] == "unscoped"
    assert summary["detailAvailable"] is True
    assert summary["chartReady"] is True
    assert summary["overall"] == {
        "requestCount": 3,
        "promptTokens": 30,
        "completionTokens": 15,
        "totalTokens": 45,
        "avgLatencyMs": 120.5,
    }
    assert summary["byPipeline"][0]["pipelineDomain"] == "online_rag"
    assert summary["byKnowledgeBase"][0]["kbId"] == "kb-a"
    assert summary["byApiKey"][0]["apiKeyId"] == "key-a"
    assert summary["pipelineStages"][0]["pipelineStage"] == "chunk"
    assert summary["pipelineStages"][0]["featureName"] == "切片 LLM"
    assert summary["pipelineStages"][0]["detailStatus"] == "recorded"
    assert summary["llmCalls"][0]["provider"] == "302AI"
    assert summary["llmCalls"][0]["modelName"] == "qwen-plus"
    assert summary["hourlyUsage"][0]["pipelineDomain"] == "ingestion"
    assert summary["hourlyUsage"][0]["totalTokens"] == 150
    assert summary["costSummary"]["configured"] is False
    assert summary["quotaAlerts"] == []
    assert cursor.executed[2][1] == (50,)
    assert cursor.executed[3][1] == (50,)
    assert cursor.executed[5][1] == (50,)
    assert cursor.executed[6][1] == (50,)
    assert conn.closed is True


def test_fetch_token_usage_summary_limits_stage_placeholders_by_pipeline_domain() -> None:
    from core.db.query_logs import fetch_token_usage_summary_for_identity

    cursor = _ReadCursor(
        fetchone_rows=[(1, 10, 5, 15, 120.0)],
        fetchall_rows=[
            [("online_rag", 1, 10, 5, 15, 120.0)],
            [],
            [],
            [],
            [],
            [],
        ],
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        summary = fetch_token_usage_summary_for_identity(limit=20, pipeline_domain="online_rag")

    stage_keys = [stage["pipelineStage"] for stage in summary["pipelineStages"]]
    assert stage_keys == ["rerank", "generation", "query"]
    assert "chunk" not in stage_keys
    assert "embedding" not in stage_keys
    assert cursor.executed[0][1] == ("online_rag",)
    assert cursor.executed[4][1] == ("online_rag",)
    assert cursor.executed[5][1] == ("online_rag", 20)
    assert cursor.executed[6][1] == ("online_rag", 24)


def test_fetch_token_usage_summary_applies_tenant_filter_for_non_platform_identity() -> None:
    cursor = _ReadCursor(
        fetchone_rows=[(0, 0, 0, 0, 0)],
        fetchall_rows=[[], [], [], [], [], []],
    )
    conn = _ReadConn(cursor)

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        summary = fetch_token_usage_summary(limit=20)

    assert summary["scope"] == "unscoped"

    cursor = _ReadCursor(
        fetchone_rows=[(0, 0, 0, 0, 0)],
        fetchall_rows=[[], [], [], [], [], []],
    )
    conn = _ReadConn(cursor)
    from core.db.query_logs import fetch_token_usage_summary_for_identity

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        summary = fetch_token_usage_summary_for_identity(limit=20, tenant_id="tenant-a")

    assert summary["scope"] == "tenant"
    assert "WHERE tenant_id = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("tenant-a",)
    assert cursor.executed[4][1] == ("tenant-a",)
    assert cursor.executed[5][1] == ("tenant-a", 20)
    assert cursor.executed[6][1] == ("tenant-a", 24)


def test_fetch_token_usage_summary_does_not_filter_platform_admin_scope() -> None:
    cursor = _ReadCursor(
        fetchone_rows=[(0, 0, 0, 0, 0)],
        fetchall_rows=[[], [], [], [], [], []],
    )
    conn = _ReadConn(cursor)
    from core.db.query_logs import fetch_token_usage_summary_for_identity

    with patch("core.db.query_logs.get_db_connection", return_value=conn), patch(
        "core.db.query_logs.ensure_db_schema"
    ):
        summary = fetch_token_usage_summary_for_identity(
            limit=20,
            tenant_id="tenant-a",
            include_all_tenants=True,
        )

    assert summary["scope"] == "all_tenants"
    assert "WHERE tenant_id = %s" not in cursor.executed[0][0]
    assert cursor.executed[0][1] == ()
    assert cursor.executed[6][1] == (24,)
