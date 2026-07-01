from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import app
from core.db.identity import IdentityContext


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_preview_endpoint_returns_error_without_pdf_path() -> None:
    response = client.post("/api/parse/preview", json={})
    assert response.status_code == 400
    assert "pdf_path" in response.json()["detail"]


def test_parse_preview_endpoint_returns_shape() -> None:
    payload = [
        {
            "id": "block-001",
            "type": "text",
            "text": "解析成功",
            "page": 1,
            "level": 1,
            "sourceFile": "demo.pdf",
            "tableHtml": None,
            "imagePath": None,
        }
    ]
    with patch("backend.routes.parse.get_parse_preview", return_value=payload):
        response = client.post("/api/parse/preview", json={"pdf_path": "demo.pdf"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data
    assert {"id", "type", "text", "page", "sourceFile"} <= set(data[0].keys())


def test_rag_query_endpoint_returns_traceable_shape() -> None:
    payload = {
        "query": "How does the system keep answer evidence traceable?",
        "kbId": "default",
        "answer": "By returning citations.",
        "cannotAnswer": False,
        "citations": [],
        "scores": {
            "relevanceScore": 0.9,
            "faithfulnessScore": 0.8,
            "llmScore": None,
            "cannotAnswer": False,
            "interpretation": "ok",
        },
        "recallChannels": [],
        "candidates": [],
        "contextWindow": [],
        "trace": [],
    }
    with patch("backend.routes.rag.run_rag_query", return_value=payload):
        response = client.post(
            "/api/rag/query",
            json={
                "query": "How does the system keep answer evidence traceable?",
                "kb_id": "default",
                "top_k": 6,
                "min_score": 0.3,
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert {"answer", "citations", "scores", "candidates", "trace"} <= set(data.keys())
    assert isinstance(data["citations"], list)
    assert isinstance(data["trace"], list)


def test_settings_endpoint_returns_groups() -> None:
    response = client.get("/api/console/settings")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data
    group_ids = [group["id"] for group in data]
    assert group_ids[:7] == [
        "models_common",
        "models_embedding",
        "models_cleaner",
        "models_chunker",
        "models_quality",
        "models_enhance",
        "models_rag",
    ]
    assert {"parser", "chunking", "vector_db", "storage", "about"} <= set(group_ids)
    assert {"id", "title", "description", "values"} <= set(data[0].keys())
    assert {"label", "value", "category", "editable", "source"} <= set(data[0]["values"][0].keys())
    assert {"configScope", "effectiveMode", "governance"} <= set(data[0]["values"][0].keys())
    assert data[0]["values"][0]["configScope"] == "global"
    assert data[0]["values"][0]["governance"]["editableBy"] == "platform_admin"
    sso = next(group for group in data if group["id"] == "identity_sso")
    sso_labels = {entry["label"] for entry in sso["values"]}
    assert "AI_BASE_SSO_BASE_URL" in sso_labels
    assert "AI_BASE_SSO_CLIENT_ID" in sso_labels
    assert "AI_BASE_SSO_CLIENT_SECRET" in sso_labels
    assert "AI_BASE_SSO_REDIRECT_URI" in sso_labels
    assert "AI_BASE_SSO_LAUNCH_BASE_URL" in sso_labels
    assert "AI_BASE_SSO_LAUNCH_PATH" in sso_labels
    assert "AI_BASE_SSO_EXCHANGE_PATH" in sso_labels
    assert "AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE" in sso_labels
    assert "AI_BASE_SSO_DELTA_PATH" in sso_labels
    assert "KB_LEGACY_HEADER_AUTH_ENABLED" in sso_labels
    assert "AI_BASE_IDENTITY_SYNC_ENABLED" in sso_labels
    assert "AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS" in sso_labels
    assert "AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP" in sso_labels
    assert "KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS" in sso_labels
    cleaner = next(group for group in data if group["id"] == "models_cleaner")
    cleaner_labels = {entry["label"] for entry in cleaner["values"]}
    assert "LLM_CLEANER_ENABLED" in cleaner_labels
    assert "LLM_CLEANER_MODEL" in cleaner_labels
    assert "LLM_CLEANER_BASE_URL" in cleaner_labels
    assert "LLM_CLEANER_API_KEY" in cleaner_labels
    prompt_entry = next(entry for entry in cleaner["values"] if entry["label"] == "LLM_CLEANER_SYSTEM_PROMPT")
    assert prompt_entry["editable"] is True
    quality = next(group for group in data if group["id"] == "models_quality")
    quality_labels = {entry["label"] for entry in quality["values"]}
    assert {
        "LLM_QUALITY_GATE_ENABLED",
        "LLM_QUALITY_GATE_MODEL",
        "LLM_QUALITY_GATE_BASE_URL",
        "LLM_QUALITY_GATE_API_KEY",
        "LLM_QUALITY_GATE_MIN_SCORE",
        "LLM_QUALITY_GATE_SYSTEM_PROMPT",
    } <= quality_labels
    embedding = next(group for group in data if group["id"] == "models_embedding")
    embedding_labels = {entry["label"] for entry in embedding["values"]}
    assert "LLM_EMBEDDING_BATCH_SIZE" in embedding_labels
    assert "LLM_EMBEDDING_MAX_CONCURRENCY" in embedding_labels
    assert "LLM_EMBEDDING_MAX_RETRIES" in embedding_labels
    assert "LLM_EMBEDDING_API_KEY_POOL" in embedding_labels
    assert "LLM_EMBEDDING_KEY_RETRIES" in embedding_labels
    assert "LLM_EMBEDDING_KEY_COOLDOWN_SECONDS" in embedding_labels
    embedding_concurrency_entry = next(
        entry for entry in embedding["values"] if entry["label"] == "LLM_EMBEDDING_MAX_CONCURRENCY"
    )
    assert embedding_concurrency_entry["editable"] is True
    assert embedding_concurrency_entry["category"] == "advanced"
    embedding_pool_entry = next(entry for entry in embedding["values"] if entry["label"] == "LLM_EMBEDDING_API_KEY_POOL")
    assert embedding_pool_entry["sensitive"] is True
    parser = next(group for group in data if group["id"] == "parser")
    parser_labels = {entry["label"] for entry in parser["values"]}
    assert "MINERU_OFFICIAL_API_BASE" in parser_labels
    assert "MINERU_OFFICIAL_API_TOKEN" in parser_labels
    assert "MINERU_OFFICIAL_MODEL_VERSION" in parser_labels
    assert "MINERU_OFFICIAL_SHARDING_ENABLED" in parser_labels
    assert "MINERU_OFFICIAL_SHARDING_MIN_PAGES" in parser_labels
    assert "MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD" in parser_labels
    assert "MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_KEY_RETRIES" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS" in parser_labels
    assert "ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY" in parser_labels
    dm_pool_entry = next(entry for entry in parser["values"] if entry["label"] == "ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL")
    assert dm_pool_entry["sensitive"] is True
    official_token_entry = next(entry for entry in parser["values"] if entry["label"] == "MINERU_OFFICIAL_API_TOKEN")
    assert official_token_entry["sensitive"] is True
    chunking = next(group for group in data if group["id"] == "chunking")
    chunking_labels = {entry["label"] for entry in chunking["values"]}
    assert "INGESTION_READY_MODE" in chunking_labels
    assert "HIERARCHICAL_ENHANCE_MODE" in chunking_labels
    assert "HIERARCHICAL_TEXT_ENHANCE_WORKERS" in chunking_labels
    assert "HIERARCHICAL_TABLE_ENHANCE_WORKERS" in chunking_labels
    assert "HIERARCHICAL_IMAGE_ENHANCE_WORKERS" in chunking_labels
    assert "HIERARCHICAL_ENHANCE_MAX_CONCURRENCY" in chunking_labels
    assert "HIERARCHICAL_REUSE_LLM_CLIENTS" in chunking_labels
    assert "LLM_API_KEY_POOL" in chunking_labels
    assert "VL_API_KEY_POOL" in chunking_labels
    assert "HIERARCHICAL_ENHANCE_KEY_RETRIES" in chunking_labels
    assert "HIERARCHICAL_KEY_COOLDOWN_SECONDS" in chunking_labels
    key_pool_entry = next(entry for entry in chunking["values"] if entry["label"] == "LLM_API_KEY_POOL")
    assert key_pool_entry["sensitive"] is True


def test_update_console_settings_only_updates_allowed_keys(monkeypatch) -> None:
    monkeypatch.delenv("302AI_API_BASE", raising=False)
    monkeypatch.delenv("PARSER_MODE", raising=False)

    with patch("backend.routes.console.persist_console_settings", return_value={"updated": ["302AI_API_BASE"], "count": 1}) as mocked:
        response = client.put(
            "/api/console/settings",
            json={
                "302AI_API_BASE": "https://api.example.com",
                "PARSER_MODE": "local",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["updated"] == ["302AI_API_BASE"]
    assert data["count"] == 1
    mocked.assert_called_once_with({"302AI_API_BASE": "https://api.example.com"}, identity=None, updated_by="console")


def test_update_console_settings_drops_masked_sensitive_values() -> None:
    with patch("backend.routes.console.persist_console_settings", return_value={"updated": [], "count": 0}) as mocked:
        response = client.put(
            "/api/console/settings",
            json={
                "LLM_API_KEY": "****6337",
                "302AI_API_BASE": "https://api.example.com",
            },
        )

    assert response.status_code == 200
    mocked.assert_called_once_with({"302AI_API_BASE": "https://api.example.com"}, identity=None, updated_by="console")


def test_update_console_settings_returns_503_when_persistence_fails() -> None:
    with patch("backend.routes.console.persist_console_settings", side_effect=RuntimeError("db down")):
        response = client.put("/api/console/settings", json={"302AI_API_BASE": "https://api.example.com"})

    assert response.status_code == 503
    assert response.json()["detail"] == "db down"


def test_console_evaluations_supports_kb_filter() -> None:
    kb_id = "7cbc7f0b2d7449188ae71b48"
    payload = [
        {
            "id": "eval-1",
            "kbId": kb_id,
            "query": "q1",
            "answer": "a1",
            "relevanceScore": 0.8,
            "faithfulnessScore": 0.7,
            "llmScore": None,
            "cannotAnswer": False,
        }
    ]
    with patch("backend.routes.console.get_console_evaluations", return_value=payload) as mocked:
        response = client.get(f"/api/console/evaluations?kb_id={kb_id}")
    assert response.status_code == 200
    mocked.assert_called_once_with(kb_id=kb_id, identity=None)
    assert response.json()[0]["kbId"] == kb_id


def test_console_query_logs_supports_filters() -> None:
    payload = [
        {
            "requestId": "req-1",
            "pipelineDomain": "online_rag",
            "pipelineStage": "query",
            "tenantId": "tenant-a",
            "actorId": "user-a",
            "kbId": "kb-a",
            "apiKeyId": "key-a",
            "queryHash": "hash-a",
            "querySummary": "q",
            "answerSummary": "a",
            "cannotAnswer": False,
            "relevanceScore": 0.8,
            "faithfulnessScore": 0.7,
            "promptTokens": 12,
            "completionTokens": 8,
            "totalTokens": 20,
            "latencyMs": 345,
            "status": "success",
            "errorCode": None,
            "createdAt": "2026-06-19T08:30:00+00:00",
        }
    ]
    with patch("backend.routes.console.get_console_query_logs", return_value=payload) as mocked:
        response = client.get(
            "/api/console/query-logs"
            "?kb_id=kb-a&request_id=req-1&actor_id=user-a&api_key_id=key-a&pipeline_domain=online_rag"
            "&start_at=2026-06-21T08:00:00Z&end_at=2026-06-21T09:00:00Z&limit=20"
        )

    assert response.status_code == 200
    mocked.assert_called_once_with(
        tenant_id=None,
        kb_id="kb-a",
        request_id="req-1",
        actor_id="user-a",
        api_key_id="key-a",
        pipeline_domain="online_rag",
        start_at=datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc),
        limit=20,
        identity=None,
    )
    assert response.json()[0]["requestId"] == "req-1"


def test_console_ingestion_tasks_supports_search_and_pagination() -> None:
    payload = {
        "items": [
            {
                "id": "task-1",
                "kbId": "kb-a",
                "kbName": "医学教材库",
                "documentName": "demo.pdf",
                "status": "success",
                "strategy": "hierarchical",
                "createdAt": "2026-06-23T01:00:00+00:00",
                "updatedAt": "2026-06-23T01:05:00+00:00",
                "actorId": "u1",
                "actorName": "u1",
                "totalLatencyMs": 1234,
                "stages": [],
            }
        ],
        "total": 1,
        "page": 1,
        "pageSize": 20,
        "pageCount": 1,
    }
    with patch("backend.routes.console.get_console_ingestion_tasks", return_value=payload) as mocked:
        response = client.get(
            "/api/console/ingestion-tasks?keyword=demo&status=success&strategy=hierarchical&page=1&page_size=20"
        )

    assert response.status_code == 200
    mocked.assert_called_once_with(
        keyword="demo",
        status="success",
        strategy="hierarchical",
        page=1,
        page_size=20,
        identity=None,
    )
    assert response.json()["items"][0]["kbName"] == "医学教材库"


def test_console_latest_ingestion_log_returns_latest_task_lines() -> None:
    payload = {
        "task": {"id": "task-1", "documentName": "demo.pdf", "kbId": "kb-a"},
        "lines": ["line-1", "line-2"],
        "lineCount": 2,
        "truncated": False,
    }
    with patch("backend.routes.console.get_latest_ingestion_log", return_value=payload) as mocked:
        response = client.get("/api/console/ingestion-logs/latest?kb_id=kb-a&max_lines=200")

    assert response.status_code == 200
    mocked.assert_called_once_with(kb_id="kb-a", max_lines=200, identity=None)
    assert response.json()["lines"] == ["line-1", "line-2"]


def test_console_backfill_ingestion_llm_usage_route() -> None:
    payload = {"taskId": "task-1", "backfilled": True, "totalTokens": 632738}
    with patch("backend.routes.console.backfill_ingestion_llm_usage", return_value=payload) as mocked:
        response = client.post("/api/console/ingestion-tasks/task-1/backfill-llm-usage")

    assert response.status_code == 200
    mocked.assert_called_once_with("task-1")
    assert response.json()["totalTokens"] == 632738


def test_console_audit_logs_filters_and_scopes_identity() -> None:
    payload = [
        {
            "id": 1,
            "action": "settings.update",
            "resourceType": "settings",
            "resourceId": "runtime",
            "tenantId": "tenant-a",
            "actorId": "user-a",
            "outcome": "success",
            "riskLevel": "medium",
            "summary": "Updated settings",
            "metadata": {"updatedCount": 1},
            "createdAt": "2026-06-23T08:30:00+00:00",
        }
    ]
    with patch("backend.routes.console.get_console_audit_logs", return_value=payload) as mocked:
        response = client.get(
            "/api/console/audit-logs"
            "?actor_id=user-a&action=settings.update&resource_type=settings&resource_id=runtime"
            "&request_id=req-1&kb_id=kb-a&outcome=success"
            "&start_at=2026-06-23T08:00:00Z&end_at=2026-06-23T09:00:00Z&limit=20"
        )

    assert response.status_code == 200
    mocked.assert_called_once_with(
        tenant_id=None,
        actor_id="user-a",
        action="settings.update",
        resource_type="settings",
        resource_id="runtime",
        request_id="req-1",
        kb_id="kb-a",
        outcome="success",
        start_at=datetime(2026, 6, 23, 8, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc),
        limit=20,
        identity=None,
    )
    assert response.json()[0]["action"] == "settings.update"


def test_console_identity_sync_logs_requires_super_manager_identity() -> None:
    from fastapi.testclient import TestClient

    from core.db.identity import IdentityContext

    isolated_client = TestClient(app)

    response = isolated_client.get("/api/console/identity-sync-logs")
    assert response.status_code == 401

    regular_identity = IdentityContext(
        tenant_id="t1",
        user_id="u1",
        is_authenticated=True,
        is_tenant_admin=False,
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=regular_identity), patch(
        "backend.routes.console.get_console_identity_sync_logs",
        return_value=[],
    ) as mocked:
        response = isolated_client.get(
            "/api/console/identity-sync-logs?limit=20",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "u1"},
        )

    assert response.status_code == 403
    mocked.assert_not_called()

    admin_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("tenantAdmin",),
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=admin_identity), patch(
        "backend.routes.console.get_console_identity_sync_logs",
        return_value=[],
    ) as mocked:
        response = isolated_client.get(
            "/api/console/identity-sync-logs?limit=20",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 403
    mocked.assert_not_called()

    super_manager_identity = IdentityContext(
        tenant_id="t1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("superManager",),
    )
    payload = [
        {
            "id": 1,
            "syncMode": "http_delta",
            "sourceHost": "https://ai-base.example.test",
            "sourceSchema": "",
            "requestedLimit": 0,
            "tenantsCount": 1,
            "usersCount": 2,
            "rolesCount": 1,
            "userRolesCount": 2,
            "deletedCount": 0,
            "lastSyncAt": "",
            "maxUpdatedAt": "2026-06-24 10:30:00",
            "snapshotVersion": "v2",
            "hasMore": False,
            "status": "success",
            "errorMessage": "",
            "startedAt": "2026-06-24T02:30:00+00:00",
            "finishedAt": "2026-06-24T02:30:02+00:00",
        }
    ]
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=super_manager_identity), patch(
        "backend.routes.console.get_console_identity_sync_logs",
        return_value=payload,
    ) as mocked:
        response = isolated_client.get(
            "/api/console/identity-sync-logs?limit=20",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 200
    assert response.json()[0]["syncMode"] == "http_delta"
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["limit"] == 20
    assert mocked.call_args.kwargs["identity"] == super_manager_identity

    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=super_manager_identity), patch(
        "backend.routes.console.get_console_identity_sync_logs",
        return_value=payload,
    ) as mocked:
        response = isolated_client.get(
            "/api/console/identity-sync-logs",
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "admin"},
        )

    assert response.status_code == 200
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["limit"] == 100
    assert mocked.call_args.kwargs["identity"] == super_manager_identity


def test_export_console_query_logs_returns_csv_download() -> None:
    content = "\ufeffrequestId,queryHash\nreq-1,hash-a\n".encode("utf-8")
    with patch("backend.routes.console.export_console_query_logs", return_value=("rag-query-logs.csv", content)) as mocked:
        response = client.get(
            "/api/console/query-logs/export.csv"
            "?kb_id=kb-a&request_id=req-1&actor_id=user-a&api_key_id=key-a&pipeline_domain=online_rag"
            "&start_at=2026-06-21T08:00:00Z&end_at=2026-06-21T09:00:00Z&limit=1000"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content == content
    mocked.assert_called_once_with(
        tenant_id=None,
        kb_id="kb-a",
        request_id="req-1",
        actor_id="user-a",
        api_key_id="key-a",
        pipeline_domain="online_rag",
        start_at=datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc),
        limit=1000,
        identity=None,
    )


def test_export_console_query_logs_requires_kb_for_regular_identity() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True, is_tenant_admin=False)
    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity), patch(
        "backend.routes.console.export_console_query_logs"
    ) as export_logs:
        response = client.get("/api/console/query-logs/export.csv", cookies={"kb_session": "token"})

    assert response.status_code == 403
    export_logs.assert_not_called()


def test_console_token_usage_returns_summary() -> None:
    payload = {
        "source": "kb_llm_call_logs",
        "fallbackSource": "kb_rag_query_logs",
        "scope": "unscoped",
        "detailAvailable": False,
        "overall": {
            "requestCount": 2,
            "promptTokens": 20,
            "completionTokens": 10,
            "totalTokens": 30,
            "avgLatencyMs": 120,
        },
        "byPipeline": [],
        "byKnowledgeBase": [],
        "byApiKey": [],
        "pipelineStages": [],
        "llmCalls": [],
        "chartReady": False,
    }
    with patch("backend.routes.console.get_console_token_usage", return_value=payload) as mocked:
        response = client.get("/api/console/token-usage?limit=12&pipeline_domain=ingestion")

    assert response.status_code == 200
    mocked.assert_called_once()
    assert mocked.call_args.kwargs["limit"] == 12
    assert mocked.call_args.kwargs["identity"] is None
    assert mocked.call_args.kwargs["pipeline_domain"] == "ingestion"
    assert response.json()["source"] == "kb_llm_call_logs"


def test_console_token_usage_passes_current_identity_for_scope() -> None:
    from core.db.identity import IdentityContext

    payload = {
        "source": "kb_llm_call_logs",
        "fallbackSource": "kb_rag_query_logs",
        "scope": "all_tenants",
        "detailAvailable": False,
        "overall": {
            "requestCount": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "avgLatencyMs": 0,
        },
        "byPipeline": [],
        "byKnowledgeBase": [],
        "byApiKey": [],
        "pipelineStages": [],
        "llmCalls": [],
        "chartReady": False,
    }
    with patch("backend.services.identity_service.resolve_auth_session") as resolve_session, patch(
        "backend.routes.console.get_console_token_usage",
        return_value=payload,
    ) as mocked:
        resolve_session.return_value = IdentityContext(
            tenant_id="t1",
            user_id="platform-admin",
            is_authenticated=True,
            is_platform_admin=True,
        )
        response = client.get("/api/console/token-usage?limit=20", cookies={"kb_session": "token"})

    assert response.status_code == 200
    passed_identity = mocked.call_args.kwargs["identity"]
    assert passed_identity.tenant_id == "t1"
    assert passed_identity.is_platform_admin is True


def test_overview_metrics_still_returns_when_task_store_is_degraded() -> None:
    with patch("backend.routes.console.get_console_metrics", return_value=[{"label": "知识库", "value": "0"}]):
        response = client.get("/api/console/overview-metrics")

    assert response.status_code == 200
    assert response.json()[0]["label"] == "知识库"


def test_overview_metrics_uses_current_identity() -> None:
    from core.db.identity import IdentityContext

    with patch("backend.services.identity_service.resolve_auth_session") as resolve_session, patch(
        "backend.routes.console.get_console_metrics",
        return_value=[{"label": "知识库", "value": "12"}],
    ) as mocked:
        resolve_session.return_value = IdentityContext(
            tenant_id="t1",
            user_id="admin",
            username="admin",
            display_name="管理员",
            tenant_name="租户一",
            is_tenant_admin=True,
            is_authenticated=True,
            role_codes=("superManager",),
        )
        response = client.get("/api/console/overview-metrics", cookies={"kb_session": "token"})

    assert response.status_code == 200
    passed_identity = mocked.call_args.args[0]
    assert passed_identity.tenant_id == "t1"
    assert passed_identity.user_id == "admin"
    assert passed_identity.is_tenant_admin is True


def test_retry_ingestion_task_resets_existing_task() -> None:
    task = {
        "id": "task-001",
        "kb_id": "default",
        "filename": "demo.pdf",
        "strategy": "hierarchical",
        "subject_type": "general",
        "layout_type": "single_column",
        "source_path": "data/uploads/task-001.pdf",
        "status": "failed",
        "current_stage": "upload",
        "stages": {
            key: {
                "status": "failed",
                "progress": 100,
                "message": "错误",
                "latency_ms": 1,
                "input_count": 0,
                "output_count": 0,
            }
            for key in ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]
        },
        "done": True,
        "error": "bad",
        "chunk_count": 0,
        "blocks_preview": [],
        "chunks_preview": [],
        "removed_reasons": [],
        "quality_breakdown": [],
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
    }

    with patch("backend.routes.ingestion.reset_task_for_retry", return_value=task), patch(
        "backend.routes.ingestion.run_pipeline_real"
    ):
        response = client.post("/api/ingestion/tasks/task-001/retry")

    assert response.status_code == 202
    assert response.json()["retried"] is True
