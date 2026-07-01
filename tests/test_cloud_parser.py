"""Tests for the 302.ai cloud parser implementation."""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.content_block import BlockType
from core.parser.mineru_parser import (
    _convert_content_list,
    _convert_to_blocks,
    _extract_and_map,
    _map_category,
    _map_type,
    _poll_task,
    _should_parse_with_shards,
    _split_pdf_to_shards,
    _submit_task,
    inspect_pdf,
    parse_pdf,
    parse_pdf_sharded,
    parse_pdf_from_url,
)
from core.parser.oss_uploader import _normalize_endpoint, _safe_object_name, upload_to_oss


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_zip(content_list: list[dict], images: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc_content_list.json", json.dumps(content_list))
        for name, data in (images or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


_BASE_CONFIG = {
    "parser": {
        "cloud": {
            "parse_method": "auto",
            "version": "2.5",
            "timeout": 10,
            "poll_interval": 0.1,
            "sharding": {
                "enabled": True,
                "min_pages": 4,
                "min_file_mb": 1,
                "pages_per_shard": 3,
                "max_concurrency": 2,
                "text_sample_pages": 2,
            },
        },
        "oss": {"prefix": "test-uploads", "url_expiry": 3600},
    }
}


# ── 配置加载 ──────────────────────────────────────────────────────────────────

def test_config_cloud_section():
    cloud = _BASE_CONFIG["parser"]["cloud"]
    assert cloud["version"] == "2.5"
    assert cloud["parse_method"] == "auto"
    assert cloud["timeout"] == 10


def test_config_oss_section():
    oss = _BASE_CONFIG["parser"]["oss"]
    assert oss["prefix"] == "test-uploads"
    assert oss["url_expiry"] == 3600


def test_normalize_oss_endpoint():
    assert _normalize_endpoint("https://oss-cn-hangzhou.aliyuncs.com") == "oss-cn-hangzhou.aliyuncs.com"
    assert _normalize_endpoint("http://oss-cn-hangzhou.aliyuncs.com/") == "oss-cn-hangzhou.aliyuncs.com"
    assert _normalize_endpoint("oss-cn-hangzhou.aliyuncs.com") == "oss-cn-hangzhou.aliyuncs.com"


def test_safe_object_name_handles_chinese_filename():
    safe_name = _safe_object_name("14病理学 第10版（第三章）.PDF")
    assert safe_name.endswith(".pdf")
    assert "/" not in safe_name
    assert "\\" not in safe_name
    assert " " not in safe_name


# ── content_list 映射 ─────────────────────────────────────────────────────────

def test_map_category_all_types():
    assert _map_category("text") == BlockType.TEXT
    assert _map_category("header") == BlockType.TITLE
    assert _map_category("title") == BlockType.TITLE
    assert _map_category("table") == BlockType.TABLE
    assert _map_category("image") == BlockType.IMAGE
    assert _map_category("interline_equation") == BlockType.TEXT
    assert _map_category("unknown") == BlockType.TEXT


def test_convert_content_list_basic():
    items = [
        {"type": "header", "text": "Chapter 1", "page_idx": 0, "text_level": 1},
        {"type": "text", "text": "Body text.", "page_idx": 0},
        {"type": "table", "text": "", "page_idx": 1,
         "table_body": "<table><tr><td>X</td></tr></table>",
         "bbox": [10.0, 20.0, 100.0, 50.0]},
    ]
    blocks = _convert_content_list(items, "doc.pdf", Path("data/output"))
    assert len(blocks) == 3
    assert blocks[0].type == BlockType.TITLE
    assert blocks[0].text_level == 1
    assert blocks[1].type == BlockType.TEXT
    assert blocks[2].is_table is True
    assert blocks[2].table_html == "<table><tr><td>X</td></tr></table>"
    assert blocks[2].bbox == [10.0, 20.0, 100.0, 50.0]
    assert all(b.source_file == "doc.pdf" for b in blocks)


def test_convert_content_list_image():
    items = [{"type": "image", "text": "", "page_idx": 0, "img_path": "images/fig1.png"}]
    blocks = _convert_content_list(items, "doc.pdf", Path("out"))
    assert blocks[0].type == BlockType.IMAGE
    assert blocks[0].image_path == str(Path("out") / "images/fig1.png")


def test_convert_content_list_skips_page_number():
    items = [
        {"type": "page_number", "text": "1", "page_idx": 0},
        {"type": "text", "text": "Real content", "page_idx": 0},
    ]
    blocks = _convert_content_list(items, "doc.pdf", Path("out"))
    assert len(blocks) == 1
    assert blocks[0].text == "Real content"


def test_convert_content_list_empty():
    assert _convert_content_list([], "doc.pdf", Path("out")) == []


# ── Mock API — 提交→轮询→下载 ─────────────────────────────────────────────────

def test_submit_task_success(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"task_id": "task-abc"}}
    mock_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = mock_resp
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client):
        task_id = _submit_task("https://oss.example.com/doc.pdf", _BASE_CONFIG)

    assert task_id == "task-abc"
    payload = fake_client.post.call_args.kwargs["json"]
    assert payload["pdf_url"] == "https://oss.example.com/doc.pdf"
    assert payload["parse_method"] == "auto"
    assert payload["version"] == "2.5"
    assert payload["enable_formula"] is True
    assert payload["enable_table_html"] is True
    assert payload["language"] == "ch"
    assert payload["is_ocr"] is False
    assert payload["model_version"] == "v2"


def test_submit_task_missing_api_key(monkeypatch):
    monkeypatch.delenv("302AI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="302AI_API_KEY"):
        _submit_task("https://example.com/doc.pdf", _BASE_CONFIG)


def test_submit_task_retries_remote_protocol_error(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"task_id": "task-retry"}}
    mock_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.side_effect = [httpx.RemoteProtocolError("server disconnected"), mock_resp]

    logs: list[str] = []
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client), patch("core.parser.mineru_parser.time.sleep"):
        task_id = _submit_task("https://oss.example.com/doc.pdf", _BASE_CONFIG, log_fn=logs.append)

    assert task_id == "task-retry"
    assert fake_client.post.call_count == 2
    assert any("重试 #2/3" in item for item in logs)


def test_poll_task_success(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    responses = [
        {"data": {"state": "running"}},
        {"data": {"state": "SUCCESS", "result_url": "https://storage.example.com/result.zip"}},
    ]
    call_count = {"n": 0}

    def fake_get(*args, **kwargs):
        resp = MagicMock()
        resp.json.return_value = responses[call_count["n"]]
        resp.raise_for_status = MagicMock()
        call_count["n"] += 1
        return resp

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.side_effect = fake_get
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client):
        result_url = _poll_task("task-abc", _BASE_CONFIG)

    assert result_url == "https://storage.example.com/result.zip"


def test_poll_task_retries_remote_protocol_error(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    success_resp = MagicMock()
    success_resp.json.return_value = {"data": {"state": "SUCCESS", "result_url": "https://storage.example.com/result.zip"}}
    success_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.side_effect = [httpx.RemoteProtocolError("server disconnected"), success_resp]

    logs: list[str] = []
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client), patch("core.parser.mineru_parser.time.sleep"):
        result_url = _poll_task("task-abc", _BASE_CONFIG, log_fn=logs.append)

    assert result_url == "https://storage.example.com/result.zip"
    assert fake_client.get.call_count == 2
    assert any("继续重试 1/5" in item for item in logs)


def test_poll_task_failure(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"state": "FAILED", "err_msg": "server error"}}
    mock_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.return_value = mock_resp
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="server error"):
            _poll_task("task-fail", _BASE_CONFIG)


def test_poll_task_timeout(monkeypatch):
    monkeypatch.setenv("302AI_API_KEY", "sk-test")
    monkeypatch.setenv("302AI_API_BASE", "https://api.302ai.cn")

    cfg = {"parser": {"cloud": {"timeout": 0, "poll_interval": 0.01}}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"state": "running"}}
    mock_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.get.return_value = mock_resp
    with patch("core.parser.mineru_parser.create_httpx_client", return_value=fake_client):
        with pytest.raises(TimeoutError, match="task-timeout"):
            _poll_task("task-timeout", cfg)


# ── 错误处理 ──────────────────────────────────────────────────────────────────

def test_extract_and_map_missing_content_list():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("result.md", "# markdown only")
    with pytest.raises(RuntimeError, match="content_list.json"):
        _extract_and_map(buf.getvalue(), "doc.pdf", Path("out"))


def test_extract_and_map_saves_images(tmp_path):
    content_list = [{"type": "image", "text": "", "page_idx": 0, "img_path": "images/fig1.png"}]
    zip_bytes = _make_zip(content_list, {"images/fig1.png": b"\x89PNG\r\n"})
    blocks = _extract_and_map(zip_bytes, "doc.pdf", tmp_path)
    assert (tmp_path / "images" / "fig1.png").exists()
    assert blocks[0].image_path == str(tmp_path / "images" / "fig1.png")


def test_parse_pdf_from_url_prefers_original_name_for_source_file(tmp_path):
    zip_bytes = _make_zip([{"type": "text", "text": "正文", "page_idx": 0}])

    with patch("core.parser.mineru_parser.load_config", return_value=_BASE_CONFIG), patch(
        "core.parser.mineru_parser._submit_task",
        return_value="task-1",
    ), patch(
        "core.parser.mineru_parser._poll_task",
        return_value="https://storage.example.com/result.zip",
    ), patch(
        "core.parser.mineru_parser._download_zip",
        return_value=zip_bytes,
    ):
        blocks = parse_pdf_from_url(
            "https://oss.example.com/tmp.pdf",
            pdf_path=str(tmp_path / "12345678-temp.pdf"),
            output_dir=str(tmp_path),
            original_name="教材原文.pdf",
        )

    assert len(blocks) == 1
    assert blocks[0].source_file == "教材原文.pdf"


def test_upload_to_oss_uses_normalized_endpoint_and_safe_name(tmp_path, monkeypatch):
    pdf_path = tmp_path / "14病理学 第10版（第三章）.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com/")
    monkeypatch.setenv("OSS_BUCKET", "wisewe")

    bucket_instance = MagicMock()
    bucket_instance.sign_url.return_value = "https://signed.example.com/file.pdf"
    fake_oss2 = SimpleNamespace(Auth=MagicMock(return_value=MagicMock()), Bucket=MagicMock(return_value=bucket_instance))

    with patch.dict(sys.modules, {"oss2": fake_oss2}):
        signed_url = upload_to_oss(str(pdf_path), _BASE_CONFIG, original_name=pdf_path.name)

    assert signed_url == "https://signed.example.com/file.pdf"
    fake_oss2.Auth.assert_called_once_with("ak", "sk")
    fake_oss2.Bucket.assert_called_once()
    assert fake_oss2.Bucket.call_args.args[1] == "https://oss-cn-hangzhou.aliyuncs.com"
    object_key = bucket_instance.put_object_from_file.call_args.args[0]
    assert object_key.startswith("test-uploads/")
    assert object_key.endswith(".pdf")


# ── 分片解析 ──────────────────────────────────────────────────────────────────

def _make_pdf(path: Path, page_count: int) -> None:
    import fitz

    doc = fitz.open()
    try:
        for index in range(page_count):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {index + 1}")
        doc.save(str(path))
    finally:
        doc.close()


def test_inspect_pdf_reads_page_count_and_text_sample(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 4)

    inspection = inspect_pdf(str(pdf_path), text_sample_pages=2)

    assert inspection.page_count == 4
    assert inspection.file_size_bytes > 0
    assert inspection.sampled_pages == 2
    assert inspection.sampled_text_chars > 0
    assert inspection.likely_scanned is False


def test_split_pdf_to_shards_creates_expected_page_ranges(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 7)

    shards = _split_pdf_to_shards(str(pdf_path), tmp_path / "shards", pages_per_shard=3)

    assert [(s.start_page, s.end_page) for s in shards] == [(0, 3), (3, 6), (6, 7)]
    assert all(s.path.exists() for s in shards)

    import fitz

    page_counts = []
    for shard in shards:
        doc = fitz.open(str(shard.path))
        try:
            page_counts.append(doc.page_count)
        finally:
            doc.close()
    assert page_counts == [3, 3, 1]


def test_should_parse_with_shards_respects_thresholds(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 7)
    inspection = inspect_pdf(str(pdf_path), text_sample_pages=1)

    assert _should_parse_with_shards(inspection, _BASE_CONFIG) is True

    disabled = {
        "parser": {
            "cloud": {
                "sharding": {
                    "enabled": False,
                    "min_pages": 4,
                    "min_file_mb": 1,
                    "pages_per_shard": 3,
                }
            }
        }
    }
    assert _should_parse_with_shards(inspection, disabled) is False


def test_parse_pdf_sharded_offsets_pages_and_preserves_source_file(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 5)

    def fake_upload(path, log_fn=None, original_name=None):
        return f"https://oss.example.com/{Path(path).name}"

    def fake_parse_from_url(pdf_url, pdf_path="", output_dir="data/output", log_fn=None, original_name=None):
        name = Path(pdf_path).name
        if "shard_001" in name:
            return [
                _convert_content_list(
                    [{"type": "text", "text": "第一页", "page_idx": 0}],
                    source_file=original_name or name,
                    output_path=Path(output_dir),
                )[0],
                _convert_content_list(
                    [{"type": "text", "text": "第三页", "page_idx": 2}],
                    source_file=original_name or name,
                    output_path=Path(output_dir),
                )[0],
            ]
        return _convert_content_list(
            [{"type": "text", "text": "第四页", "page_idx": 0}],
            source_file=original_name or name,
            output_path=Path(output_dir),
        )

    cfg = {
        "parser": {
            "cloud": {
                "sharding": {
                    "enabled": True,
                    "min_pages": 1,
                    "min_file_mb": 1,
                    "pages_per_shard": 3,
                    "max_concurrency": 1,
                    "text_sample_pages": 1,
                }
            }
        }
    }

    with patch("core.parser.mineru_parser.upload_pdf_to_oss", side_effect=fake_upload), patch(
        "core.parser.mineru_parser.parse_pdf_from_url",
        side_effect=fake_parse_from_url,
    ):
        blocks = parse_pdf_sharded(
            str(pdf_path),
            output_dir=str(tmp_path / "out"),
            original_name="原教材.pdf",
            config=cfg,
        )

    assert [block.text for block in blocks] == ["第一页", "第三页", "第四页"]
    assert [block.page_idx for block in blocks] == [0, 2, 3]
    assert {block.source_file for block in blocks} == {"原教材.pdf"}


def test_parse_pdf_sharded_uses_unique_oss_names_for_shards(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 5)
    upload_names: list[str | None] = []

    def fake_upload(path, log_fn=None, original_name=None):
        upload_names.append(original_name)
        return f"https://oss.example.com/{Path(path).name}"

    def fake_parse_from_url(pdf_url, pdf_path="", output_dir="data/output", log_fn=None, original_name=None):
        return _convert_content_list(
            [{"type": "text", "text": Path(pdf_path).name, "page_idx": 0}],
            source_file=original_name or Path(pdf_path).name,
            output_path=Path(output_dir),
        )

    cfg = {
        "parser": {
            "cloud": {
                "sharding": {
                    "enabled": True,
                    "min_pages": 1,
                    "min_file_mb": 1,
                    "pages_per_shard": 3,
                    "max_concurrency": 1,
                    "text_sample_pages": 1,
                }
            }
        }
    }

    with patch("core.parser.mineru_parser.upload_pdf_to_oss", side_effect=fake_upload), patch(
        "core.parser.mineru_parser.parse_pdf_from_url",
        side_effect=fake_parse_from_url,
    ):
        blocks = parse_pdf_sharded(
            str(pdf_path),
            output_dir=str(tmp_path / "out"),
            original_name="原教材.pdf",
            config=cfg,
        )

    assert upload_names == ["原教材-shard-001.pdf", "原教材-shard-002.pdf"]
    assert {block.source_file for block in blocks} == {"原教材.pdf"}


def test_parse_pdf_auto_uses_single_task_when_below_threshold(tmp_path):
    pdf_path = tmp_path / "small.pdf"
    _make_pdf(pdf_path, 2)

    cfg = {
        "parser": {
            "cloud": {
                "sharding": {
                    "enabled": True,
                    "min_pages": 120,
                    "min_file_mb": 80,
                    "pages_per_shard": 40,
                    "max_concurrency": 2,
                    "text_sample_pages": 1,
                }
            }
        }
    }

    with patch("core.parser.mineru_parser.load_config", return_value=cfg), patch(
        "core.parser.mineru_parser.upload_pdf_to_oss",
        return_value="https://oss.example.com/small.pdf",
    ) as upload_mock, patch(
        "core.parser.mineru_parser.parse_pdf_from_url",
        return_value=[],
    ) as parse_url_mock, patch(
        "core.parser.mineru_parser.parse_pdf_sharded",
        return_value=[],
    ) as sharded_mock:
        blocks = parse_pdf(str(pdf_path), original_name="small.pdf")

    assert blocks == []
    upload_mock.assert_called_once()
    parse_url_mock.assert_called_once()
    sharded_mock.assert_not_called()


def test_parse_pdf_auto_uses_shards_when_threshold_matches(tmp_path):
    pdf_path = tmp_path / "large.pdf"
    _make_pdf(pdf_path, 5)

    with patch("core.parser.mineru_parser.load_config", return_value=_BASE_CONFIG), patch(
        "core.parser.mineru_parser.parse_pdf_sharded",
        return_value=[],
    ) as sharded_mock, patch(
        "core.parser.mineru_parser.upload_pdf_to_oss",
    ) as upload_mock:
        blocks = parse_pdf(str(pdf_path), original_name="large.pdf")

    assert blocks == []
    sharded_mock.assert_called_once()
    upload_mock.assert_not_called()


def test_parse_pdf_auto_falls_back_to_single_task_when_inspection_fails(tmp_path):
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"not a real pdf")

    cfg = {
        "parser": {
            "cloud": {
                "sharding": {
                    "enabled": True,
                    "min_pages": 1,
                    "min_file_mb": 1,
                    "pages_per_shard": 1,
                    "max_concurrency": 1,
                    "text_sample_pages": 1,
                }
            }
        }
    }

    with patch("core.parser.mineru_parser.load_config", return_value=cfg), patch(
        "core.parser.mineru_parser.upload_pdf_to_oss",
        return_value="https://oss.example.com/broken.pdf",
    ) as upload_mock, patch(
        "core.parser.mineru_parser.parse_pdf_from_url",
        return_value=[],
    ) as parse_url_mock, patch(
        "core.parser.mineru_parser.parse_pdf_sharded",
        return_value=[],
    ) as sharded_mock:
        blocks = parse_pdf(str(pdf_path), original_name="broken.pdf")

    assert blocks == []
    upload_mock.assert_called_once()
    parse_url_mock.assert_called_once()
    sharded_mock.assert_not_called()


# ── 类型过滤 ──────────────────────────────────────────────────────────────────

def test_page_number_filtered():
    items = [
        {"type": "page_number", "text": "42", "page_idx": 3},
        {"type": "text", "text": "Content", "page_idx": 3},
    ]
    blocks = _convert_content_list(items, "doc.pdf", Path("out"))
    assert len(blocks) == 1
    assert blocks[0].type == BlockType.TEXT


# ── 向后兼容 ──────────────────────────────────────────────────────────────────

def test_legacy_convert_to_blocks():
    raw = [
        {"type": "title", "text": "Title", "page_idx": 0, "text_level": 1},
        {"type": "text", "text": "Para", "page_idx": 0},
        {"type": "table", "text": "", "page_idx": 1, "table_body": "<table></table>"},
    ]
    blocks = _convert_to_blocks(raw, source_file="doc.pdf")
    assert len(blocks) == 3
    assert blocks[0].type == BlockType.TITLE
    assert blocks[2].is_table is True


def test_legacy_map_type():
    assert _map_type("text") == BlockType.TEXT
    assert _map_type("table") == BlockType.TABLE
    assert _map_type("image") == BlockType.IMAGE
    assert _map_type("title") == BlockType.TITLE
    assert _map_type("unknown") == BlockType.TEXT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
