from __future__ import annotations

import sys
from pathlib import Path
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse, ORJSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.app as backend_app_module

from backend.app import app
from backend.routes.knowledge_bases import _make_kb_id
from backend.services.rag_service import _candidate_to_payload

client = TestClient(app)


def test_default_response_class_falls_back_without_orjson(monkeypatch) -> None:
    monkeypatch.setattr(
        backend_app_module.importlib.util,
        "find_spec",
        lambda name: None if name == "orjson" else object(),
    )
    assert backend_app_module._get_default_response_class() is JSONResponse


def test_default_response_class_prefers_orjson_when_available(monkeypatch) -> None:
    monkeypatch.setattr(
        backend_app_module.importlib.util,
        "find_spec",
        lambda name: object() if name == "orjson" else None,
    )
    assert backend_app_module._get_default_response_class() is ORJSONResponse


def test_create_app_loads_project_env(monkeypatch) -> None:
    called: dict[str, bool] = {}

    def _fake_load_project_env(override: bool = False) -> bool:
        called["override"] = override
        return True

    monkeypatch.setattr(backend_app_module, "load_project_env", _fake_load_project_env)
    backend_app_module.create_app()
    assert called == {"override": False}


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cors_preflight_allows_kb_identity_headers(monkeypatch) -> None:
    monkeypatch.setenv("KB_CORS_ALLOW_ORIGINS", "http://192.168.2.208:3000")
    isolated_client = TestClient(backend_app_module.create_app())

    response = isolated_client.options(
        "/api/knowledge-bases",
        headers={
            "Origin": "http://192.168.2.208:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-kb-user-id,x-kb-tenant-id,content-type",
        },
    )

    assert response.status_code == 200
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-kb-user-id" in allow_headers
    assert "x-kb-tenant-id" in allow_headers
    assert response.headers["access-control-allow-origin"] == "http://192.168.2.208:3000"


def test_identity_snapshot_users_endpoint() -> None:
    from core.db.identity import IdentityContext

    payload = [
        {
            "tenantId": "1",
            "userId": "1",
            "username": "systemAdmin",
            "displayName": "超级管理员",
            "tenantName": "默认租户",
            "roleCodes": ["superManager"],
            "isTenantAdmin": True,
            "source": "identity_snapshot",
        }
    ]
    super_manager = IdentityContext(
        tenant_id="1",
        user_id="1",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("superManager",),
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=super_manager), patch(
        "backend.routes.identity.list_identity_snapshot_users",
        return_value=payload,
    ) as mocked:
        r = client.get(
            "/api/identity/snapshot-users?limit=5",
            headers={"X-KB-Tenant-Id": "1", "X-KB-User-Id": "1"},
        )

    assert r.status_code == 200
    assert r.json()["mode"] == "temporary_sso_deferred"
    assert r.json()["users"] == payload
    assert r.json()["count"] == 1
    mocked.assert_called_once_with(5)


def test_identity_snapshot_users_requires_super_manager() -> None:
    from core.db.identity import IdentityContext

    regular_admin = IdentityContext(
        tenant_id="1",
        user_id="admin",
        is_authenticated=True,
        is_tenant_admin=True,
        role_codes=("tenantAdmin",),
    )
    with patch("backend.services.identity_service.resolve_identity_snapshot", return_value=regular_admin), patch(
        "backend.routes.identity.list_identity_snapshot_users",
        return_value=[],
    ) as mocked:
        r = client.get(
            "/api/identity/snapshot-users?limit=5",
            headers={"X-KB-Tenant-Id": "1", "X-KB-User-Id": "admin"},
        )

    assert r.status_code == 403
    mocked.assert_not_called()


def test_output_assets_are_served(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "data" / "output" / "images"
    output_dir.mkdir(parents=True)
    (output_dir / "demo.txt").write_text("asset-ok", encoding="utf-8")

    isolated_app = backend_app_module.create_app()
    isolated_client = TestClient(isolated_app)

    r = isolated_client.get("/api/assets/output/images/demo.txt")
    assert r.status_code == 200
    assert r.text == "asset-ok"


def test_parse_preview_requires_real_pdf_path() -> None:
    r = client.post("/api/parse/preview", json={})
    assert r.status_code == 400
    assert "pdf_path" in r.json()["detail"]


def test_parse_preview_shape() -> None:
    payload = [
        {
            "id": "block-001",
            "type": "text",
            "text": "真实解析块",
            "page": 1,
            "level": None,
            "sourceFile": "sample.pdf",
            "tableHtml": None,
            "imagePath": None,
        }
    ]
    with patch("backend.routes.parse.get_parse_preview", return_value=payload):
        r = client.post("/api/parse/preview", json={"pdf_path": "data/input/sample.pdf"})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and data
    assert {"id", "type", "text", "page", "sourceFile"} <= set(data[0].keys())


def test_rag_query_shape() -> None:
    payload = {
        "query": "test query",
        "kbId": "default",
        "answer": "真实答案",
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
        r = client.post(
            "/api/rag/query",
            json={"query": "test query", "kb_id": "default", "top_k": 4, "min_score": 0.3},
        )
    assert r.status_code == 200
    data = r.json()
    assert {"answer", "citations", "scores", "candidates", "trace"} <= set(data.keys())


def test_rag_query_persists_evaluation_record() -> None:
    with patch(
        "backend.services.rag_service.run_rag_pipeline",
        return_value=([], [], {"answer": "真实答案", "cannot_answer": False, "citations": []}, {"relevance_score": 0.9, "faithfulness_score": 0.8, "llm_score": None}),
    ), patch("backend.services.rag_service.append_evaluation") as mocked_append:
        r = client.post(
            "/api/rag/query",
            json={"query": "test query", "kb_id": "kb-1", "top_k": 4, "min_score": 0.3},
        )
    assert r.status_code == 200
    mocked_append.assert_called_once()
    saved = mocked_append.call_args.args[0]
    assert saved["kbId"] == "kb-1"
    assert saved["query"] == "test query"
    assert saved["relevanceScore"] == 0.9


def test_rag_query_exposes_stage_timings() -> None:
    with patch(
        "backend.services.rag_service.run_rag_pipeline",
        return_value=(
            [],
            [],
            {"answer": "ok", "cannot_answer": False, "citations": []},
            {
                "relevance_score": 0.9,
                "faithfulness_score": 0.8,
                "llm_score": None,
                "_latency_ms": {
                    "retrieval": 11,
                    "rerank": 12,
                    "generate": 13,
                    "score": 14,
                    "total": 50,
                    "llm_usage": {
                        "generatePromptTokens": 10,
                        "generateCompletionTokens": 5,
                        "generateTotalTokens": 15,
                        "scorePromptTokens": 4,
                        "scoreCompletionTokens": 2,
                        "scoreTotalTokens": 6,
                    },
                    "short_circuit": False,
                    "retrieval_breakdown": {
                        "embedding": 3,
                        "dense": 4,
                        "sparse": 5,
                        "structured": 0,
                        "fusion": 1,
                        "fold": 2,
                        "related": 0,
                        "filter": 1,
                        "total": 16,
                        "short_circuit": False,
                    },
                },
            },
        ),
    ), patch("backend.services.rag_service.append_evaluation"), patch(
        "backend.services.rag_service.append_llm_call_log"
    ) as append_llm:
        r = client.post(
            "/api/rag/query",
            json={"query": "test query", "kb_id": "kb-1", "top_k": 4, "min_score": 0.3},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["timings"]["latencyMs"]["retrieval"] == 11
    assert data["timings"]["latencyMs"]["total"] == 50
    assert data["timings"]["retrievalBreakdownMs"]["embedding"] == 3
    assert data["timings"]["retrievalBreakdownMs"]["sparse"] == 5
    assert data["timings"]["retrievalBreakdownMs"]["shortCircuit"] is False
    assert data["timings"]["shortCircuit"] is False
    assert {item["key"]: item["latencyMs"] for item in data["trace"]} == {
        "retrieval": 11,
        "rerank": 12,
        "generate": 13,
        "score": 14,
    }
    recorded = [call.args[0] for call in append_llm.call_args_list]
    assert [(item.pipeline_domain, item.pipeline_stage, item.total_tokens) for item in recorded] == [
        ("online_rag", "generation", 15),
        ("evaluation", "evaluation", 6),
    ]


def test_rag_candidate_payload_includes_image_asset_url() -> None:
    payload = _candidate_to_payload(
        {
            "id": "chunk-image",
            "content": "[图片 第2页]",
            "source": "demo.pdf",
            "page": 1,
            "chunk_index": 15,
            "layer": "child",
            "score": 0.63,
            "is_image_chunk": True,
            "image_path": "data/output/images/page-2.png",
        }
    )

    assert payload["isImageChunk"] is True
    assert payload["imagePath"] == "data/output/images/page-2.png"
    assert payload["imageUrl"] == "/api/assets/output/images/page-2.png"


def test_rag_candidate_payload_keeps_remote_image_url() -> None:
    payload = _candidate_to_payload(
        {
            "id": "chunk-image",
            "content": "远程图片",
            "source": "demo.pdf",
            "page": 1,
            "chunk_index": 15,
            "layer": "child",
            "score": 0.63,
            "is_image_chunk": True,
            "image_path": "https://example.com/page-2.png",
        }
    )

    assert payload["isImageChunk"] is True
    assert payload["imagePath"] == "https://example.com/page-2.png"
    assert payload["imageUrl"] == "https://example.com/page-2.png"


def test_graph_rag_query_shape() -> None:
    payload = {
        "query": "test graph query",
        "kbId": "default",
        "intent": "fact",
        "intentSource": "classifier",
        "results": [
            {
                "id": "chunk-1",
                "score": 0.92,
                "channel": "graph_expand",
                "path": ["chunk-0", "chunk-1"],
            }
        ],
        "stats": {
            "recall_counts": {"embedding": 5, "bm25": 3, "structured": 1},
            "after_fusion": 6,
            "after_expand": 8,
            "after_dedupe": 5,
        },
    }
    with patch("backend.routes.rag.run_graph_rag_query", return_value=payload):
        r = client.post(
            "/api/rag/graph-query",
            json={"query": "test graph query", "kb_id": "default", "top_k": 5, "min_score": 0.3, "explain": True},
        )
    assert r.status_code == 200
    data = r.json()
    assert {"intent", "intentSource", "results", "stats"} <= set(data.keys())
    assert isinstance(data["results"], list)
    assert isinstance(data["stats"], dict)


def test_eval_reports_shape() -> None:
    payload = {
        "records": 2,
        "strategies": ["baseline_vector", "graph_full"],
        "summary": [
            {"strategy": "baseline_vector", "recallAt5": 0.5, "mrr": 0.5, "ndcgAt5": 0.5},
            {"strategy": "graph_full", "recallAt5": 1.0, "mrr": 1.0, "ndcgAt5": 1.0},
        ],
    }
    with patch("backend.routes.eval.run_eval", return_value=payload):
        r = client.get("/api/eval/reports")
    assert r.status_code == 200
    data = r.json()
    assert {"records", "strategies", "summary"} <= set(data.keys())
    assert isinstance(data["summary"], list)


def test_console_settings() -> None:
    r = client.get("/api/console/settings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and data
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
    assert {"id", "title", "values"} <= set(data[0].keys())


def test_chunk_preview_endpoint() -> None:
    with patch("backend.routes.ingestion.get_task", return_value={"id": "task-1", "status": "awaiting_confirmation"}), patch(
        "backend.routes.ingestion.list_chunk_drafts",
        return_value=[{"id": "draft-1", "content": "chunk", "isDeleted": False}],
    ):
        r = client.get("/api/ingestion/chunks/preview/task-1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["taskId"] == "task-1"
    assert payload["count"] == 1


def test_chunk_confirm_endpoint() -> None:
    task_payload = {
        "id": "task-1",
        "kbId": "default",
        "documentName": "demo.pdf",
        "status": "success",
        "strategy": "hierarchical",
        "createdAt": "",
        "updatedAt": "",
        "parseMethod": "mineru",
        "chunkCount": 2,
        "stages": [],
        "blocks": [],
        "chunks": [],
        "removedReasons": [],
        "qualityBreakdown": [],
    }
    with patch("backend.routes.ingestion.get_task", return_value={"id": "task-1"}), patch(
        "backend.routes.ingestion.confirm_pipeline",
        return_value=task_payload,
    ):
        r = client.post("/api/ingestion/chunks/confirm/task-1")
    assert r.status_code == 200
    assert r.json()["status"] == "success"


def test_delete_ingestion_task_endpoint_success() -> None:
    payload = {
        "deleted": True,
        "task_id": "task-1",
        "removed": {"sourceFile": True, "logFile": True, "chunkDrafts": 2, "taskRecord": True},
    }
    with patch("backend.routes.ingestion.delete_ingestion_task", return_value=payload):
        r = client.delete("/api/ingestion/tasks/task-1")

    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert r.json()["removed"]["sourceFile"] is True


def test_delete_ingestion_task_endpoint_not_found() -> None:
    with patch("backend.routes.ingestion.delete_ingestion_task", return_value=None):
        r = client.delete("/api/ingestion/tasks/missing-task")

    assert r.status_code == 404


def test_delete_ingestion_task_endpoint_rejects_running_task() -> None:
    with patch("backend.routes.ingestion.delete_ingestion_task", side_effect=RuntimeError("task is running")):
        r = client.delete("/api/ingestion/tasks/task-running")

    assert r.status_code == 409
    assert "running" in r.json()["detail"]


def test_knowledge_bases() -> None:
    with patch("backend.routes.knowledge_bases.get_knowledge_bases_payload", return_value=[]):
        r = client.get("/api/knowledge-bases")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_make_kb_id_uses_opaque_hex_not_name_slug() -> None:
    kb_id = _make_kb_id("中医教材库")

    assert len(kb_id) == 24
    assert all(ch in "0123456789abcdef" for ch in kb_id)
    assert "zhong" not in kb_id
    assert "yi" not in kb_id


def test_create_knowledge_base_uses_opaque_hex_id() -> None:
    def _fake_create(kb_id, name, description, strategy):
        return {
            "id": kb_id,
            "name": name,
            "description": description,
            "strategy": strategy,
            "createdAt": "",
            "docCount": 0,
            "chunkCount": 0,
            "lastUpdated": "",
            "duplicatePolicy": "hash-skip",
        }

    with patch("backend.routes.knowledge_bases.create_knowledge_base_payload", side_effect=_fake_create) as mocked:
        r = client.post(
            "/api/knowledge-bases",
            json={"name": "中医教材库", "description": "医学教材", "strategy": "hierarchical"},
        )

    assert r.status_code == 201
    kb_id = r.json()["id"]
    assert len(kb_id) == 24
    assert all(ch in "0123456789abcdef" for ch in kb_id)
    assert "zhong" not in kb_id
    mocked.assert_called_once_with(kb_id, "中医教材库", "医学教材", "hierarchical")


def test_update_knowledge_base() -> None:
    payload = {
        "id": "kb-1",
        "name": "教材库",
        "description": "医学教材",
        "strategy": "semantic",
        "createdAt": "",
        "docCount": 0,
        "chunkCount": 0,
        "lastUpdated": "",
        "duplicatePolicy": "hash-skip",
    }
    with patch("backend.routes.knowledge_bases.update_knowledge_base_payload", return_value=payload) as mocked:
        r = client.put(
            "/api/knowledge-bases/kb-1",
            json={"name": "教材库", "description": "医学教材", "strategy": "semantic"},
        )
    assert r.status_code == 200
    assert r.json()["strategy"] == "semantic"
    mocked.assert_called_once_with("kb-1", "教材库", "医学教材", "semantic")


def test_update_knowledge_base_not_found() -> None:
    with patch(
        "backend.routes.knowledge_bases.update_knowledge_base_payload",
        side_effect=ValueError("Knowledge base 'missing' not found"),
    ):
        r = client.put(
            "/api/knowledge-bases/missing",
            json={"name": "missing", "description": "", "strategy": "hierarchical"},
        )
    assert r.status_code == 404


def test_documents() -> None:
    with patch("backend.routes.knowledge_bases.get_documents_payload", return_value=[]):
        r = client.get("/api/documents")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_documents_supports_opaque_hex_kb_filter() -> None:
    kb_id = "7cbc7f0b2d7449188ae71b48"
    with patch("backend.routes.knowledge_bases.get_documents_payload", return_value=[]) as mocked:
        r = client.get(f"/api/documents?kb_id={kb_id}")
    assert r.status_code == 200
    mocked.assert_called_once_with(kb_id, None)


def test_document_detail_rejects_inaccessible_document_before_payload() -> None:
    from core.db.identity import IdentityContext

    identity = IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity), patch(
        "backend.services.access_control.get_document_kb_id",
        return_value="kb-private",
    ), patch("backend.services.access_control.get_knowledge_base", return_value=None), patch(
        "backend.services.access_control.append_audit_log"
    ), patch("backend.routes.knowledge_bases.get_document_detail_payload") as payload:
        r = client.get("/api/documents/doc-private", cookies={"kb_session": "token"})

    assert r.status_code == 404
    payload.assert_not_called()


def test_document_export_rejects_inaccessible_document_before_export() -> None:
    from core.db.identity import IdentityContext

    identity = IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity), patch(
        "backend.services.access_control.get_document_kb_id",
        return_value="kb-private",
    ), patch("backend.services.access_control.get_knowledge_base", return_value=None), patch(
        "backend.services.access_control.append_audit_log"
    ), patch("backend.routes.knowledge_bases.export_document_csv_payload") as export_payload:
        r = client.get("/api/documents/doc-private/export.csv", cookies={"kb_session": "token"})

    assert r.status_code == 404
    export_payload.assert_not_called()


def test_ingestion_tasks_supports_opaque_hex_kb_filter() -> None:
    kb_id = "7cbc7f0b2d7449188ae71b48"
    tasks = [
        {"id": "task-hex", "kb_id": kb_id},
        {"id": "task-other", "kb_id": "zhong-yi-xue"},
    ]

    def _payload(task: dict) -> dict:
        return {"id": task["id"], "kbId": task["kb_id"]}

    with patch("backend.routes.parse.get_all_tasks", return_value=tasks), patch(
        "backend.routes.parse._task_to_payload",
        side_effect=_payload,
    ) as mocked_payload:
        r = client.get(f"/api/ingestion/tasks?kb_id={kb_id}")

    assert r.status_code == 200
    assert r.json() == [{"id": "task-hex", "kbId": kb_id}]
    mocked_payload.assert_called_once_with(tasks[0])


def test_ingestion_task_rejects_inaccessible_task() -> None:
    from core.db.identity import IdentityContext

    identity = IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    task = {
        "id": "task-private",
        "kb_id": "kb-private",
        "filename": "demo.pdf",
        "status": "success",
        "strategy": "hierarchical",
        "created_at": "",
        "updated_at": "",
        "chunk_count": 0,
        "stages": {key: {"status": "success", "input_count": 0, "output_count": 0, "latency_ms": 0, "message": ""} for key in ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]},
        "blocks_preview": [],
        "chunks_preview": [],
        "removed_reasons": [],
        "quality_breakdown": [],
    }

    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity), patch(
        "backend.routes.ingestion.get_task",
        return_value=task,
    ), patch("backend.services.access_control.get_knowledge_base", return_value=None), patch(
        "backend.services.access_control.append_audit_log"
    ), patch("backend.routes.ingestion._task_to_payload") as payload:
        r = client.get("/api/ingestion/tasks/task-private", cookies={"kb_session": "token"})

    assert r.status_code == 404
    payload.assert_not_called()


def test_chunk_draft_update_rejects_inaccessible_draft() -> None:
    from core.db.identity import IdentityContext

    identity = IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    with patch("backend.services.identity_service.resolve_auth_session", return_value=identity), patch(
        "backend.services.access_control.get_chunk_draft_scope",
        return_value={"task_id": "task-private", "kb_id": "kb-private"},
    ), patch("backend.services.access_control.get_knowledge_base", return_value=None), patch(
        "backend.services.access_control.append_audit_log"
    ), patch("backend.routes.ingestion.update_chunk_draft") as update_draft:
        r = client.put(
            "/api/ingestion/chunks/draft-private",
            json={"content": "updated"},
            cookies={"kb_session": "token"},
        )

    assert r.status_code == 404
    update_draft.assert_not_called()


def test_document_graph() -> None:
    payload = {
        "documentId": "doc-1",
        "nodes": [{"id": "chunk:c1", "type": "chunk", "label": "P.1 · #1", "meta": {}}],
        "edges": [],
        "stats": {"nodeCount": 1, "edgeCount": 0, "chunkCount": 1, "entityCount": 0, "tripleCount": 0, "truncated": False},
    }
    with patch("backend.routes.knowledge_bases.get_document_graph_payload", return_value=payload):
        r = client.get("/api/documents/doc-1/graph")
    assert r.status_code == 200
    data = r.json()
    assert {"documentId", "nodes", "edges", "stats"} <= set(data.keys())
    assert data["nodes"][0]["id"] == "chunk:c1"


def test_knowledge_base_graph() -> None:
    payload = {
        "documentId": "kb:kb-1",
        "kbId": "kb-1",
        "scope": "knowledge_base",
        "nodes": [{"id": "chunk:c1", "type": "chunk", "label": "P.1 路 #1", "meta": {}}],
        "edges": [],
        "stats": {
            "nodeCount": 1,
            "edgeCount": 0,
            "chunkCount": 1,
            "entityCount": 0,
            "tripleCount": 0,
            "truncated": False,
            "documentCount": 1,
            "totalChunkCount": 1,
            "selectedChunkCount": 1,
        },
    }
    with patch("backend.routes.knowledge_bases.get_knowledge_base_graph_payload", return_value=payload):
        r = client.get("/api/knowledge-bases/kb-1/graph")
    assert r.status_code == 200
    data = r.json()
    assert data["kbId"] == "kb-1"
    assert data["scope"] == "knowledge_base"
    assert data["stats"]["documentCount"] == 1


def test_export_document_csv() -> None:
    with patch(
        "backend.routes.knowledge_bases.export_document_csv_payload",
        return_value=("demo-chunks.csv", b"\xef\xbb\xbfchunkId,content\r\n1,test\r\n"),
    ):
        r = client.get("/api/documents/doc-1/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment;" in r.headers["content-disposition"]
    assert b"chunkId,content" in r.content


def test_download_document_source_local(tmp_path: Path) -> None:
    source = tmp_path / "demo.pdf"
    source.write_bytes(b"%PDF-1.4 source")
    with patch(
        "backend.routes.knowledge_bases.get_document_source_download_payload",
        return_value={"kind": "local", "path": str(source), "filename": "demo.pdf"},
    ):
        r = client.get("/api/documents/doc-1/source")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment;" in r.headers["content-disposition"]
    assert r.content == b"%PDF-1.4 source"


def test_download_document_source_oss_redirect() -> None:
    with patch(
        "backend.routes.knowledge_bases.get_document_source_download_payload",
        return_value={"kind": "oss", "url": "https://signed.example.com/demo.pdf", "filename": "demo.pdf"},
    ):
        r = client.get("/api/documents/doc-1/source", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "https://signed.example.com/demo.pdf"


def test_ingestion_upload_persists_source_file(tmp_path: Path) -> None:
    task_id = "task-upload-001"
    persisted = tmp_path / f"{task_id}.pdf"

    def _fake_create_task(*_args, **kwargs):
        persisted.write_bytes(kwargs["file_bytes"])
        return task_id

    with patch("backend.routes.ingestion.create_task", side_effect=_fake_create_task), patch(
        "backend.routes.ingestion.run_pipeline_real"
    ), patch("backend.routes.ingestion.get_task", return_value={"id": task_id, "kb_id": "default", "filename": "demo.pdf", "status": "pending", "strategy": "hierarchical", "created_at": "", "updated_at": "", "chunk_count": 0, "stages": {key: {"status": "pending", "input_count": 0, "output_count": 0, "latency_ms": 0, "message": ""} for key in ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]}, "blocks_preview": [], "chunks_preview": [], "removed_reasons": [], "quality_breakdown": []}), patch("backend.routes.ingestion._task_to_payload", return_value={"documentName": "demo.pdf"}):
        response = client.post(
            "/api/ingestion/upload?kb_id=default&strategy=hierarchical",
            files={"file": ("demo.pdf", b"%PDF-1.4 test", "application/pdf")},
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "pending"

        task_response = client.get(f"/api/ingestion/tasks/{payload['task_id']}")
        assert task_response.status_code == 200
        task = task_response.json()
        assert task["documentName"] == "demo.pdf"
        assert persisted.exists()


def test_ingestion_upload_passes_current_identity(tmp_path: Path) -> None:
    from core.db.identity import IdentityContext

    task_id = "task-upload-identity"
    captured = {}

    def _fake_create_task(*_args, **kwargs):
        captured.update(kwargs)
        return task_id

    with patch("backend.services.identity_service.resolve_auth_session") as resolve_session, patch(
        "backend.routes.ingestion.create_task",
        side_effect=_fake_create_task,
    ), patch("backend.services.access_control.get_knowledge_base", return_value={"id": "kb-a"}), patch(
        "backend.routes.ingestion.run_pipeline_real"
    ):
        resolve_session.return_value = IdentityContext(
            tenant_id="tenant-a",
            user_id="user-a",
            username="alice",
            display_name="Alice",
            is_authenticated=True,
            source="session",
        )
        response = client.post(
            "/api/ingestion/upload?kb_id=kb-a&strategy=hierarchical",
            files={"file": ("demo.pdf", b"%PDF-1.4 test", "application/pdf")},
            cookies={"kb_session": "token"},
        )

    assert response.status_code == 202
    assert captured["identity"].tenant_id == "tenant-a"
    assert captured["identity"].user_id == "user-a"
    assert captured["file_bytes"] == b"%PDF-1.4 test"


def test_ingestion_task_payload_exposes_stage_progress() -> None:
    from backend.services.ingestion_service import STAGE_KEYS, _new_stage_state, _task_to_payload

    stages = {key: _new_stage_state() for key in STAGE_KEYS}
    stages["chunk"].update(
        {
            "status": "running",
            "progress": 65,
            "message": "增强进度：100/200 (50%)",
            "input_count": 1000,
            "output_count": 0,
        }
    )
    payload = _task_to_payload(
        {
            "id": "task-progress",
            "kb_id": "default",
            "filename": "demo.pdf",
            "status": "running",
            "strategy": "hierarchical",
            "created_at": "",
            "updated_at": "",
            "chunk_count": 0,
            "stages": stages,
            "blocks_preview": [],
            "chunks_preview": [],
            "removed_reasons": [],
            "quality_breakdown": [],
        }
    )

    chunk_stage = next(stage for stage in payload["stages"] if stage["key"] == "chunk")
    assert chunk_stage["progress"] == 65
    assert chunk_stage["reason"] == "增强进度：100/200 (50%)"


def test_create_task_stores_identity_metadata(tmp_path: Path) -> None:
    from backend.services import ingestion_service
    from core.db.identity import IdentityContext

    with patch.object(ingestion_service, "UPLOAD_DIR", str(tmp_path)), patch(
        "backend.services.ingestion_service.save_task"
    ) as save_task:
        task_id = ingestion_service.create_task(
            "kb-a",
            "demo.pdf",
            "hierarchical",
            file_bytes=b"%PDF-1.4 test",
            identity=IdentityContext(
                tenant_id="tenant-a",
                user_id="user-a",
                username="alice",
                display_name="Alice",
                is_authenticated=True,
                source="session",
            ),
        )

    task = save_task.call_args.args[0]
    assert task["id"] == task_id
    assert task["tenant_id"] == "tenant-a"
    assert task["actor_id"] == "user-a"
    assert task["actor_name"] == "Alice"
    assert task["created_by"] == "user-a"


def test_record_ingestion_llm_usage_passes_task_identity() -> None:
    from backend.services.ingestion_service import _record_ingestion_llm_usage

    with patch("backend.services.ingestion_service.append_llm_call_log") as append_log:
        _record_ingestion_llm_usage(
            task_id="task-1",
            task={"kb_id": "kb-a", "tenant_id": "tenant-a", "actor_id": "user-a"},
            stage="chunk",
            feature_name="三层切片增强",
            metrics={"enhanceTotalTokens": 632738, "enhanceWallMs": 64417},
            token_prefix="enhance",
            latency_ms=64417,
            provider="provider",
            model_name="model",
        )

    record = append_log.call_args.args[0]
    assert record.total_tokens == 632738
    assert record.identity.tenant_id == "tenant-a"
    assert record.identity.user_id == "user-a"


def test_ingestion_cleaner_runtime_options_follow_llm_switch() -> None:
    from backend.services.ingestion_service import _cleaner_runtime_options

    overrides = {
        "LLM_CLEANER_ENABLED": True,
        "LLM_CLEANER_MODEL": "clean-model",
        "LLM_CLEANER_BASE_URL": "https://clean.example.test/v1",
        "LLM_CLEANER_API_KEY": "clean-key",
        "LLM_CLEANER_SYSTEM_PROMPT": "清洗提示词",
    }
    with patch("backend.services.ingestion_service.resolve_runtime_setting") as resolve_setting:
        resolve_setting.side_effect = lambda key: (overrides.get(key, ""), "db")

        options = _cleaner_runtime_options()

    assert options == {
        "use_llm": True,
        "llm_model": "clean-model",
        "llm_base_url": "https://clean.example.test/v1",
        "llm_api_key": "clean-key",
        "llm_system_prompt": "清洗提示词",
    }


def test_ingestion_quality_runtime_options_disable_llm_by_default() -> None:
    from backend.services.ingestion_service import _quality_runtime_options

    overrides = {
        "LLM_QUALITY_GATE_ENABLED": False,
        "LLM_QUALITY_GATE_MODEL": "quality-model",
        "LLM_QUALITY_GATE_BASE_URL": "https://quality.example.test/v1",
        "LLM_QUALITY_GATE_API_KEY": "quality-key",
        "LLM_QUALITY_GATE_MIN_SCORE": 4,
        "LLM_QUALITY_GATE_SYSTEM_PROMPT": "质检提示词",
    }
    with patch("backend.services.ingestion_service.resolve_runtime_setting") as resolve_setting:
        resolve_setting.side_effect = lambda key: (overrides.get(key, ""), "db")

        options = _quality_runtime_options()

    assert options["min_score"] == 0
    assert options["llm_model"] == "quality-model"
    assert options["llm_base_url"] == "https://quality.example.test/v1"
    assert options["llm_api_key"] == "quality-key"
    assert options["llm_system_prompt"] == "质检提示词"


def test_ingestion_quality_runtime_options_enable_llm_with_min_score() -> None:
    from backend.services.ingestion_service import _quality_runtime_options

    overrides = {
        "LLM_QUALITY_GATE_ENABLED": True,
        "LLM_QUALITY_GATE_MODEL": "quality-model",
        "LLM_QUALITY_GATE_BASE_URL": "https://quality.example.test/v1",
        "LLM_QUALITY_GATE_API_KEY": "quality-key",
        "LLM_QUALITY_GATE_MIN_SCORE": 4,
        "LLM_QUALITY_GATE_SYSTEM_PROMPT": "质检提示词",
    }
    with patch("backend.services.ingestion_service.resolve_runtime_setting") as resolve_setting:
        resolve_setting.side_effect = lambda key: (overrides.get(key, ""), "db")

        options = _quality_runtime_options()

    assert options == {
        "min_score": 4,
        "llm_model": "quality-model",
        "llm_base_url": "https://quality.example.test/v1",
        "llm_api_key": "quality-key",
        "llm_system_prompt": "质检提示词",
    }


def test_backfill_ingestion_llm_usage_parses_chunk_log(tmp_path: Path) -> None:
    from backend.services import ingestion_service

    task_id = "task-backfill"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / f"{task_id}.log").write_text(
        "2026-06-23T08:47:11 [INFO] task.task-backfill - [chunk] 切片完成：输出 2649 个切片，LLM tokens=632738，增强墙钟耗时=64417ms\n",
        encoding="utf-8",
    )
    task = {"id": task_id, "kb_id": "kb-a", "tenant_id": "tenant-a", "actor_id": "user-a", "stages": {"chunk": {"metrics": {}}}}

    with patch.object(ingestion_service, "LOG_DIR", str(log_dir)), patch(
        "backend.services.ingestion_service.get_task",
        return_value=task,
    ), patch("backend.services.ingestion_service.has_llm_call_log", return_value=False), patch(
        "backend.services.ingestion_service.append_llm_call_log"
    ) as append_log:
        result = ingestion_service.backfill_ingestion_llm_usage(task_id)

    assert result["backfilled"] is True
    assert result["totalTokens"] == 632738
    record = append_log.call_args.args[0]
    assert record.total_tokens == 632738
    assert record.latency_ms == 64417
    assert record.identity.tenant_id == "tenant-a"


def test_parse_stage_tracker_derives_progress_and_metrics_from_parser_logs() -> None:
    from backend.services.ingestion_service import _ParseStageTracker

    tracker = _ParseStageTracker("task-parse", "document_mind")

    tracker._observe("PDF inspection: 100 pages")
    assert tracker._progress >= 5

    tracker._observe("enabled parse sharding: 4 shards")
    assert tracker.metrics["shardCount"] == 4

    tracker._observe("submit parse job task_id=abc")
    assert tracker._progress >= 28

    tracker._observe("poll parse job status=running")
    tracker._observe("poll parse job status=running")
    assert tracker.metrics["pollCount"] == 2

    tracker._observe("[shard 001/004 P1-20] output 10 content blocks")
    tracker._observe("[shard 001/004 P1-20] merge complete")
    tracker._observe("[shard 002/004 P21-40] output 11 blocks")
    assert tracker.metrics["completedShards"] == 2
    assert tracker._progress >= 56

    tracker._observe("result_url=https://example.test/result.zip")
    tracker._observe("convert result into content blocks")
    assert tracker._progress >= 88

    metrics = tracker.finish_metrics(129513, 751)
    assert metrics["provider"] == "document_mind"
    assert metrics["parseWallMs"] == 129513
    assert metrics["outputBlocks"] == 751


def test_parse_stage_tracker_ignores_page_count_when_reading_shard_count() -> None:
    from backend.services.ingestion_service import _ParseStageTracker

    tracker = _ParseStageTracker("task-parse", "ali_document_mind")

    tracker._observe("enabled Document Mind sharding: 396 pages, 44.9 MB, 10 shards, 40 pages per shard")
    assert tracker.metrics["shardCount"] == 10

    tracker._observe("[shard 001/010 P1-40] output 667 content blocks")
    tracker._observe("[shard 001/010] merge complete")
    tracker._observe("[shard 002/010 P41-80] output 574 content blocks")

    assert tracker.metrics["completedShards"] == 2


def test_chunk_progress_is_derived_from_hierarchical_messages() -> None:
    from backend.services.ingestion_service import _chunk_progress_from_message

    assert _chunk_progress_from_message("正在扫描内容块 10/100 (10%)，类型=text，页码 P.1") == 7
    assert _chunk_progress_from_message("基础切片完成：100 个基础条目，80 个增强任务") == 35
    assert _chunk_progress_from_message("增强阶段开始：mode=parallel_ordered，text=80，fragment=0，table=0，image=0") == 40
    assert _chunk_progress_from_message("增强进度：40/80 (50%)，text=40，fragment=0，table=0，image=0，失败=0") == 65
