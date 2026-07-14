from __future__ import annotations

import json
from unittest.mock import patch

from sdk.python.wisewe_rag_client import WiseWeRagClient


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"requestId": "req-1", "data": {"taskId": "task-1"}}).encode("utf-8")


def test_sdk_ingest_webpage_uses_phase12_endpoint_and_json_body() -> None:
    captured: dict[str, object] = {}
    client = WiseWeRagClient(base_url="http://localhost:8000", api_key="wwkb_test")

    def _fake_request(method: str, path: str, body: bytes) -> dict:
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"ok": True}

    with patch.object(WiseWeRagClient, "_request", side_effect=_fake_request):
        result = client.ingest_webpage(kb_id="kb-1", url="https://example.com/docs", max_pages=3)

    assert result == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/openapi/v1/ingestion/webpage"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["kb_id"] == "kb-1"
    assert body["url"] == "https://example.com/docs"
    assert body["max_pages"] == 3


def test_sdk_get_task_usage_uses_openapi_usage_endpoint() -> None:
    captured: dict[str, object] = {}
    client = WiseWeRagClient(base_url="http://localhost:8000", api_key="wwkb_test")

    def _fake_request(method: str, path: str, body: bytes) -> dict:
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"requestId": "req-1", "data": {"id": "task-1", "overall": {}}}

    with patch.object(WiseWeRagClient, "_request", side_effect=_fake_request):
        result = client.get_task_usage("task-1", limit=50)

    assert result["data"]["id"] == "task-1"
    assert captured["method"] == "GET"
    assert captured["path"] == "/openapi/v1/usage/tasks/task-1?limit=50"
    assert captured["body"] == b""


def test_sdk_upload_document_signs_file_bytes_for_multipart(tmp_path) -> None:
    file_path = tmp_path / "demo.pdf"
    file_bytes = b"%PDF-1.4 demo"
    file_path.write_bytes(file_bytes)
    client = WiseWeRagClient(base_url="http://localhost:8000", api_key="wwkb_test")

    with patch.object(WiseWeRagClient, "signed_headers", return_value={"Authorization": "Bearer wwkb_test"}) as sign_mock, patch(
        "sdk.python.wisewe_rag_client.urllib_request.urlopen",
        return_value=_FakeResponse(),
    ) as urlopen_mock:
        result = client.upload_document(kb_id="kb-1", file_path=str(file_path))

    assert result["data"]["taskId"] == "task-1"
    sign_mock.assert_called_once()
    assert sign_mock.call_args.args == ("POST", "/openapi/v1/ingestion/upload", file_bytes)
    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "http://localhost:8000/openapi/v1/ingestion/upload"
    assert b'name="kb_id"' in request.data
    assert file_bytes in request.data


def test_sdk_upload_backup_csv_uses_backup_endpoint_and_file_hash(tmp_path) -> None:
    file_path = tmp_path / "document-backup.csv"
    file_bytes = b"schemaVersion,documentId\nwisewe-rag-backup-v1,doc-1\n"
    file_path.write_bytes(file_bytes)
    client = WiseWeRagClient(base_url="http://localhost:8000", api_key="wwkb_test")

    with patch.object(WiseWeRagClient, "signed_headers", return_value={"Authorization": "Bearer wwkb_test"}) as sign_mock, patch(
        "sdk.python.wisewe_rag_client.urllib_request.urlopen",
        return_value=_FakeResponse(),
    ) as urlopen_mock:
        result = client.upload_backup_csv(kb_id="kb-1", file_path=str(file_path))

    assert result["data"]["taskId"] == "task-1"
    sign_mock.assert_called_once()
    assert sign_mock.call_args.args == ("POST", "/openapi/v1/ingestion/backup-csv", file_bytes)
    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "http://localhost:8000/openapi/v1/ingestion/backup-csv"
    assert b'document-backup.csv' in request.data
    assert file_bytes in request.data
