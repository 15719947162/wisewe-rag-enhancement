from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.parser.mineru_official_parser import (
    _effective_pages_per_shard,
    _poll_task,
    _should_parse_with_shards,
    _submit_task,
    parse_pdf_from_url,
)
from core.parser.pdf_sharding import PdfInspection


def _make_zip(content_list: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc_content_list.json", json.dumps(content_list))
    return buf.getvalue()


def test_submit_task_uses_official_extract_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_OFFICIAL_API_TOKEN", "token-test")
    monkeypatch.setenv("MINERU_OFFICIAL_API_BASE", "https://mineru.example")
    monkeypatch.setenv("MINERU_OFFICIAL_MODEL_VERSION", "vlm")
    monkeypatch.setenv("MINERU_OFFICIAL_NO_CACHE", "true")
    monkeypatch.setenv("MINERU_OFFICIAL_EXTRA_FORMATS", "docx,html")

    response = MagicMock()
    response.json.return_value = {"code": 0, "msg": "ok", "data": {"task_id": "task-123"}}
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response

    with patch("core.parser.mineru_official_parser.create_httpx_client", return_value=client):
        task_id = _submit_task("https://oss.example.com/doc.pdf")

    assert task_id == "task-123"
    assert client.post.call_args.args[0] == "https://mineru.example/api/v4/extract/task"
    payload = client.post.call_args.kwargs["json"]
    assert payload["url"] == "https://oss.example.com/doc.pdf"
    assert payload["model_version"] == "vlm"
    assert payload["enable_table"] is True
    assert payload["no_cache"] is True
    assert payload["extra_formats"] == ["docx", "html"]
    assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer token-test"


def test_submit_task_requires_official_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINERU_OFFICIAL_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="MINERU_OFFICIAL_API_TOKEN"):
        _submit_task("https://oss.example.com/doc.pdf")


def test_poll_task_returns_full_zip_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_OFFICIAL_API_TOKEN", "token-test")
    monkeypatch.setenv("MINERU_OFFICIAL_API_BASE", "https://mineru.example")
    monkeypatch.setenv("MINERU_OFFICIAL_POLL_INTERVAL", "0.01")

    responses = [
        {"code": 0, "data": {"state": "running", "extract_progress": {"extracted_pages": 1, "total_pages": 2}}},
        {"code": 0, "data": {"state": "done", "full_zip_url": "https://cdn.example.com/result.zip"}},
    ]
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        response = MagicMock()
        response.json.return_value = responses[calls["n"]]
        response.raise_for_status = MagicMock()
        calls["n"] += 1
        return response

    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = fake_get

    with patch("core.parser.mineru_official_parser.create_httpx_client", return_value=client), patch(
        "core.parser.mineru_official_parser.time.sleep"
    ):
        result_url = _poll_task("task-123")

    assert result_url == "https://cdn.example.com/result.zip"
    assert client.get.call_args.args[0] == "https://mineru.example/api/v4/extract/task/task-123"


def test_poll_task_fails_on_official_failed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_OFFICIAL_API_TOKEN", "token-test")
    response = MagicMock()
    response.json.return_value = {"code": 0, "data": {"state": "failed", "err_msg": "bad pdf"}}
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response

    with patch("core.parser.mineru_official_parser.create_httpx_client", return_value=client):
        with pytest.raises(RuntimeError, match="bad pdf"):
            _poll_task("task-failed")


def test_parse_pdf_from_url_maps_content_list(tmp_path: Path) -> None:
    zip_bytes = _make_zip([{"type": "text", "text": "正文", "page_idx": 0}])

    with patch("core.parser.mineru_official_parser._submit_task", return_value="task-1"), patch(
        "core.parser.mineru_official_parser._poll_task", return_value="https://cdn.example.com/result.zip"
    ), patch("core.parser.mineru_official_parser._download_zip", return_value=zip_bytes):
        blocks = parse_pdf_from_url(
            "https://oss.example.com/doc.pdf",
            pdf_path=str(tmp_path / "tmp.pdf"),
            output_dir=str(tmp_path / "out"),
            original_name="origin.pdf",
        )

    assert len(blocks) == 1
    assert blocks[0].text == "正文"
    assert blocks[0].source_file == "origin.pdf"


def test_official_sharding_threshold_uses_official_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINERU_OFFICIAL_SHARDING_MIN_PAGES", raising=False)
    monkeypatch.delenv("MINERU_OFFICIAL_SHARDING_MIN_FILE_MB", raising=False)
    inspection = PdfInspection(
        page_count=201,
        file_size_bytes=10 * 1024 * 1024,
        sampled_pages=1,
        sampled_text_chars=1,
        likely_scanned=False,
    )

    assert _should_parse_with_shards(inspection) is True


def test_effective_pages_per_shard_respects_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD", "180")
    monkeypatch.setenv("MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD", "90")
    inspection = PdfInspection(
        page_count=200,
        file_size_bytes=200 * 1024 * 1024,
        sampled_pages=1,
        sampled_text_chars=1,
        likely_scanned=False,
    )

    assert _effective_pages_per_shard(inspection) == 90


def test_poll_retries_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_OFFICIAL_API_TOKEN", "token-test")
    success = MagicMock()
    success.json.return_value = {"code": 0, "data": {"state": "done", "full_zip_url": "https://cdn/result.zip"}}
    success.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = [httpx.RemoteProtocolError("disconnect"), success]

    with patch("core.parser.mineru_official_parser.create_httpx_client", return_value=client), patch(
        "core.parser.mineru_official_parser.time.sleep"
    ):
        assert _poll_task("task-retry") == "https://cdn/result.zip"
