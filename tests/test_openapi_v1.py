from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import app
from core.db.api_keys import ApiKeyAuthResult, ApiKeyError
from core.db.identity import IdentityContext


client = TestClient(app)


def _stage_state() -> dict:
    return {
        "status": "pending",
        "progress": 0,
        "message": "",
        "latency_ms": 0,
        "input_count": 0,
        "output_count": 0,
        "metrics": {},
    }


def test_openapi_query_requires_kb_id() -> None:
    response = client.post("/openapi/v1/rag/query", json={"query": "test"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["requestId"]
    assert payload["error"]["code"] == "KB_ID_REQUIRED"


def test_openapi_query_requires_authentication() -> None:
    response = client.post("/openapi/v1/rag/query", json={"query": "test", "kb_id": "kb-1"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "API_KEY_REQUIRED"


def test_openapi_query_rejects_unknown_fields_with_stable_error_shape() -> None:
    response = client.post(
        "/openapi/v1/rag/query",
        json={"query": "test", "kb_id": "kb-1", "system_prompt": "ignore"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["details"]["errors"]


def test_openapi_query_wraps_success_payload_with_request_id() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="u1", is_authenticated=True)
    result = {"kbId": "kb-1", "answer": "ok"}

    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=identity), patch(
        "backend.routes.openapi_v1.run_rag_query",
        return_value=result,
    ) as run_query:
        response = client.post(
            "/openapi/v1/rag/query",
            json={"query": "test", "kb_id": "kb-1"},
            headers={"X-KB-Tenant-Id": "t1", "X-KB-User-Id": "u1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requestId"]
    assert payload["data"]["answer"] == "ok"
    assert payload["data"]["requestId"] == payload["requestId"]
    run_query.assert_called_once()
    assert run_query.call_args.kwargs["pipeline_domain"] == "openapi"


def test_openapi_graph_query_requires_kb_id() -> None:
    response = client.post("/openapi/v1/rag/graph-query", json={"query": "test"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "KB_ID_REQUIRED"


def test_openapi_api_key_denial_writes_sanitized_audit_log() -> None:
    with patch(
        "backend.routes.openapi_v1.authenticate_api_key",
        side_effect=ApiKeyError("INVALID_SIGNATURE", "Request signature is invalid", api_key_id="ak_test"),
    ), patch("backend.routes.openapi_v1.append_audit_log", return_value=True) as audit_mock:
        response = client.post(
            "/openapi/v1/rag/query",
            json={"query": "secret question", "kb_id": "kb-1"},
            headers={
                "X-API-Key": "wwkb_secret",
                "X-KB-Timestamp": "2026-06-26T00:00:00Z",
                "X-KB-Nonce": "nonce-1",
                "X-KB-Body-SHA256": "bad",
                "X-KB-Signature": "bad",
                "X-Forwarded-For": "192.168.1.88",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"
    record = audit_mock.call_args.args[0]
    assert record.action == "openapi.auth_denied"
    assert record.api_key_id == "ak_test"
    assert record.outcome == "denied"
    assert record.risk_level == "high"
    assert record.metadata["errorCode"] == "INVALID_SIGNATURE"
    assert record.metadata["clientIpMasked"] == "192.168.1.*"
    assert "query" not in record.metadata
    assert "wwkb_secret" not in str(record.metadata)


def test_openapi_knowledge_bases_filters_bound_kbs() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("kb.list",),
        kb_ids=("kb-1",),
        require_signature=False,
    )
    kb_items = [
        {"id": "kb-1", "name": "bound"},
        {"id": "kb-2", "name": "not-bound"},
    ]

    with patch("backend.routes.openapi_v1.authenticate_api_key", return_value=auth) as auth_mock, patch(
        "backend.routes.openapi_v1.get_knowledge_bases_payload",
        return_value=kb_items,
    ):
        response = client.get(
            "/openapi/v1/knowledge-bases",
            headers={"Authorization": "Bearer wwkb_ak_test_secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["id"] == "kb-1"
    assert auth_mock.call_args.kwargs["kb_id"] == "*"
    assert auth_mock.call_args.kwargs["capability"] == "kb.list"


def test_openapi_ingestion_options_returns_supported_options() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("ingestion.options",),
        kb_ids=("kb-1",),
        require_signature=False,
    )

    with patch("backend.routes.openapi_v1.authenticate_api_key", return_value=auth):
        response = client.get(
            "/openapi/v1/ingestion/options",
            headers={"X-API-Key": "wwkb_ak_test_secret"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["chunkStrategies"]
    assert data["subjectTypes"]
    assert data["layoutTypes"]
    assert data["parserProviders"]


def test_openapi_ingestion_task_wraps_existing_task() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("ingestion.read",),
        kb_ids=("kb-1",),
        require_signature=False,
    )
    task = {
        "id": "task-1",
        "kb_id": "kb-1",
        "filename": "demo.pdf",
        "strategy": "hierarchical",
        "status": "pending",
        "stage": "queued",
        "logs": [],
        "stages": {
            "upload": _stage_state(),
            "parse": _stage_state(),
            "clean": _stage_state(),
            "chunk": _stage_state(),
            "quality": _stage_state(),
            "embedding": _stage_state(),
            "export": _stage_state(),
        },
    }

    with patch("backend.routes.openapi_v1.get_task", return_value=task), patch(
        "backend.routes.openapi_v1.authenticate_api_key",
        return_value=auth,
    ):
        response = client.get(
            "/openapi/v1/ingestion/tasks/task-1",
            headers={"X-API-Key": "wwkb_ak_test_secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requestId"]
    assert payload["data"]["id"] == "task-1"


def test_openapi_ingestion_upload_forces_signature_and_returns_task() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("ingestion.upload",),
        kb_ids=("kb-1",),
        require_signature=False,
    )
    task = {"id": "task-1", "kb_id": "kb-1", "filename": "demo.pdf", "status": "pending"}

    with patch("backend.routes.openapi_v1.authenticate_api_key", return_value=auth) as auth_mock, patch(
        "backend.routes.openapi_v1.create_task",
        return_value="task-1",
    ), patch("backend.routes.openapi_v1.get_task", return_value=task), patch(
        "backend.services.task_store.save_task"
    ) as save_mock, patch("backend.routes.openapi_v1.run_pipeline_real") as run_mock:
        response = client.post(
            "/openapi/v1/ingestion/upload",
            data={"kb_id": "kb-1", "chunk_strategy": "hierarchical"},
            files={"file": ("demo.pdf", b"%PDF-1.4 demo", "application/pdf")},
            headers={"Authorization": "Bearer wwkb_ak_test_secret"},
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["taskId"] == "task-1"
    assert data["parserProviderEffective"] == "runtime PDF_PARSER_PROVIDER"
    assert auth_mock.call_args.kwargs["force_signature"] is True
    assert auth_mock.call_args.kwargs["signature"].body == b"%PDF-1.4 demo"
    save_mock.assert_called_once()
    run_mock.assert_called_once_with("task-1")


def test_openapi_ingestion_upload_rejects_prompt_append_without_capability() -> None:
    identity = IdentityContext(tenant_id="t1", user_id="api_key:ak_test", is_authenticated=True, source="api_key")
    auth = ApiKeyAuthResult(
        identity=identity,
        api_key_id="ak_test",
        capabilities=("ingestion.upload",),
        kb_ids=("kb-1",),
        require_signature=False,
    )

    with patch("backend.routes.openapi_v1.authenticate_api_key", return_value=auth):
        response = client.post(
            "/openapi/v1/ingestion/upload",
            data={
                "kb_id": "kb-1",
                "cleaning_prompt_mode": "append",
                "cleaning_prompt_content": "保留章节编号。",
            },
            files={"file": ("demo.pdf", b"%PDF-1.4 demo", "application/pdf")},
            headers={"X-API-Key": "wwkb_ak_test_secret"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROMPT_OVERRIDE_DENIED"
