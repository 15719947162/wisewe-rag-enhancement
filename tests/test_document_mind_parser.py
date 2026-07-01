from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

from core.models.content_block import BlockType, ContentBlock
from core.parser import document_mind_parser as document_mind_parser_module
from core.parser.document_mind_parser import (
    _DocumentMindCredentialPool,
    _DocumentMindCredential,
    _DocumentMindParseTimings,
    _get_effective_document_mind_pages_per_shard,
    _get_document_mind_hedge_config,
    _get_document_mind_sharding_config,
    _parse_document_mind_credential_pool,
    _parse_pdf_single,
    _should_parse_document_mind_with_shards,
    get_last_document_mind_key_pool_metrics,
    convert_document_mind_result,
    parse_pdf,
    parse_pdf_sharded,
)
from core.parser.pdf_sharding import PdfInspection, PdfPageProfile


@pytest.fixture(autouse=True)
def _clear_document_mind_key_history() -> None:
    with document_mind_parser_module._DOCUMENT_MIND_KEY_HISTORY_LOCK:
        document_mind_parser_module._DOCUMENT_MIND_KEY_HISTORY.clear()
    yield
    with document_mind_parser_module._DOCUMENT_MIND_KEY_HISTORY_LOCK:
        document_mind_parser_module._DOCUMENT_MIND_KEY_HISTORY.clear()


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


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def to_map(self) -> object:
        return self.payload


def _install_fake_document_mind_modules(
    monkeypatch: pytest.MonkeyPatch,
    client_cls: type,
) -> None:
    class FakeConfig:
        def __init__(self, access_key_id: str, access_key_secret: str) -> None:
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.endpoint = ""

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711.client",
        types.SimpleNamespace(Client=client_cls),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi",
        types.SimpleNamespace(models=types.SimpleNamespace(Config=FakeConfig)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(
            models=types.SimpleNamespace(
                SubmitDocParserJobAdvanceRequest=SimpleRequest,
                QueryDocParserStatusRequest=SimpleRequest,
                GetDocParserResultRequest=SimpleRequest,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )


def test_document_mind_credential_pool_parses_primary_and_pool() -> None:
    credentials = _parse_document_mind_credential_pool(
        "primary-ak",
        "primary-sk",
        "pool-ak-1:pool-sk-1; pool-ak-2:pool-sk-2 primary-ak:primary-sk",
    )

    assert [credential.alias for credential in credentials] == ["dm-key-1", "dm-key-2", "dm-key-3"]
    assert [(credential.access_key_id, credential.access_key_secret) for credential in credentials] == [
        ("primary-ak", "primary-sk"),
        ("pool-ak-1", "pool-sk-1"),
        ("pool-ak-2", "pool-sk-2"),
    ]


def test_document_mind_credential_pool_prefers_lower_latency_key() -> None:
    slow = _DocumentMindCredential("slow-ak", "slow-sk", "dm-key-1")
    fast = _DocumentMindCredential("fast-ak", "fast-sk", "dm-key-2")
    pool = _DocumentMindCredentialPool(
        [slow, fast],
        max_inflight_per_key=1,
        cooldown_seconds=60,
    )
    pool.set_active_key_target(2)

    first = pool.acquire()
    assert first is not None
    assert first.alias == "dm-key-1"
    pool.release(first, 300_000, success=True, throttle=False)

    second = pool.acquire()
    assert second is not None
    assert second.alias == "dm-key-2"
    pool.release(second, 30_000, success=True, throttle=False)

    third = pool.acquire()
    assert third is not None
    assert third.alias == "dm-key-2"

    metrics = pool.metrics()
    assert metrics["parseKey.dm-key-1.lastMs"] == 300_000
    assert metrics["parseKey.dm-key-1.avgMs"] == 300_000
    assert metrics["parseKey.dm-key-2.lastMs"] == 30_000
    assert metrics["parseKey.dm-key-2.avgMs"] == 30_000
    assert "slow-ak" not in str(metrics)
    assert "fast-ak" not in str(metrics)


def test_document_mind_credential_pool_uses_process_latency_history() -> None:
    credentials = _parse_document_mind_credential_pool(
        "slow-ak",
        "slow-sk",
        "fast-ak:fast-sk",
    )
    pool = _DocumentMindCredentialPool(
        credentials,
        max_inflight_per_key=1,
        cooldown_seconds=60,
    )
    pool.set_active_key_target(2)

    first = pool.acquire()
    assert first is not None
    assert first.alias == "dm-key-1"
    pool.release(first, 300_000, success=True, throttle=False)

    second = pool.acquire()
    assert second is not None
    assert second.alias == "dm-key-2"
    pool.release(second, 30_000, success=True, throttle=False)

    fresh_credentials = _parse_document_mind_credential_pool(
        "slow-ak",
        "slow-sk",
        "fast-ak:fast-sk",
    )
    fresh_pool = _DocumentMindCredentialPool(
        fresh_credentials,
        max_inflight_per_key=1,
        cooldown_seconds=60,
    )

    lease = fresh_pool.acquire()
    assert lease is not None
    assert lease.alias == "dm-key-2"

    metrics = fresh_pool.metrics()
    assert metrics["parseKey.dm-key-1.avgMs"] > metrics["parseKey.dm-key-2.avgMs"]
    assert "slow-ak" not in str(metrics)
    assert "fast-ak" not in str(metrics)


def test_document_mind_credential_pool_limits_unknown_key_probes() -> None:
    credentials = _parse_document_mind_credential_pool(
        "ak-1",
        "sk-1",
        "ak-2:sk-2,ak-3:sk-3,ak-4:sk-4",
    )
    pool = _DocumentMindCredentialPool(
        credentials,
        max_inflight_per_key=1,
        cooldown_seconds=60,
        unknown_probe_concurrency=2,
    )
    pool.set_active_key_target(2)

    first = pool.acquire()
    second = pool.acquire()

    assert first is not None
    assert second is not None
    assert {first.alias, second.alias} == {"dm-key-1", "dm-key-2"}
    assert pool.acquire() is None

    pool.release(first, 30_000, success=True, throttle=False)
    third = pool.acquire()

    assert third is not None
    assert third.alias == "dm-key-1"
    assert pool.metrics()["parseKeyUnknownProbeConcurrency"] == 2


def test_document_mind_credential_pool_does_not_probe_unknown_when_known_key_available() -> None:
    credentials = _parse_document_mind_credential_pool(
        "ak-1",
        "sk-1",
        "ak-2:sk-2,ak-3:sk-3",
    )
    pool = _DocumentMindCredentialPool(
        credentials,
        max_inflight_per_key=1,
        cooldown_seconds=60,
        unknown_probe_concurrency=1,
    )

    first = pool.acquire()
    assert first is not None
    assert first.alias == "dm-key-1"
    pool.release(first, 25_000, success=True, throttle=False)

    second = pool.acquire()
    assert second is not None
    assert second.alias == "dm-key-1"


def test_document_mind_credential_pool_stops_unknown_probes_when_active_target_is_met() -> None:
    credentials = _parse_document_mind_credential_pool(
        "ak-1",
        "sk-1",
        "ak-2:sk-2,ak-3:sk-3,ak-4:sk-4,ak-5:sk-5,ak-6:sk-6",
    )
    pool = _DocumentMindCredentialPool(
        credentials,
        max_inflight_per_key=1,
        cooldown_seconds=60,
        unknown_probe_concurrency=1,
    )
    pool.set_active_key_target(4)

    for index, elapsed_ms in enumerate([25_000, 30_000, 35_000, 40_000], start=1):
        lease = pool.acquire()
        assert lease is not None
        assert lease.alias == f"dm-key-{index}"
        pool.release(lease, elapsed_ms, success=True, throttle=False)

    leases = [pool.acquire() for _ in range(4)]

    assert [lease.alias for lease in leases if lease is not None] == [
        "dm-key-1",
        "dm-key-2",
        "dm-key-3",
        "dm-key-4",
    ]
    assert pool.acquire() is None


def test_parse_pdf_single_tracks_used_aliases_without_exposing_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    credentials = _parse_document_mind_credential_pool("ak-1", "sk-1", "ak-2:sk-2")
    pool = _DocumentMindCredentialPool(credentials, max_inflight_per_key=1, cooldown_seconds=60)
    used_aliases: set[str] = set()
    used_aliases_lock = threading.Lock()

    def fake_create_client(credential: object | None = None) -> str:
        return getattr(credential, "alias")

    def fake_parse_with_client(
        client: str,
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[ContentBlock]:
        del client, pdf_path, output_dir, log_fn
        return [ContentBlock(type=BlockType.TEXT, text="ok", page_idx=0, source_file=source_name)]

    monkeypatch.setattr("core.parser.document_mind_parser._create_document_mind_client", fake_create_client)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single_with_client", fake_parse_with_client)

    blocks = _parse_pdf_single(
        str(pdf_path),
        str(tmp_path / "out"),
        lambda _msg: None,
        "book.pdf",
        credential_pool=pool,
        used_aliases=used_aliases,
        used_aliases_lock=used_aliases_lock,
    )

    assert [block.text for block in blocks] == ["ok"]
    assert used_aliases == {"dm-key-1"}
    assert "ak-1" not in str(pool.metrics())
    assert "sk-1" not in str(pool.metrics())


def test_parse_pdf_single_retries_throttled_document_mind_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", "ak-2:sk-2")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_KEY_RETRIES", "1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS", "60")

    used_aliases: list[str] = []

    def fake_create_client(credential: object | None = None) -> str:
        alias = getattr(credential, "alias")
        used_aliases.append(alias)
        return alias

    def fake_parse_with_client(
        client: str,
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[ContentBlock]:
        del pdf_path, output_dir, log_fn
        if client == "dm-key-1":
            raise RuntimeError("429 rate limit")
        return [ContentBlock(type=BlockType.TEXT, text="ok", page_idx=0, source_file=source_name)]

    monkeypatch.setattr("core.parser.document_mind_parser._create_document_mind_client", fake_create_client)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single_with_client", fake_parse_with_client)

    blocks = _parse_pdf_single(str(pdf_path), str(tmp_path / "out"), lambda _msg: None, "book.pdf")

    assert [block.text for block in blocks] == ["ok"]
    assert used_aliases == ["dm-key-1", "dm-key-2"]
    metrics = get_last_document_mind_key_pool_metrics()
    assert metrics["parseKeyPoolSize"] == 2
    assert metrics["parseKeyThrottleCount"] == 1
    assert metrics["parseKeyRetryCount"] == 1
    assert metrics["parseKeyCooldownCount"] == 1
    assert metrics["parseKey.dm-key-1.throttles"] == 1
    assert metrics["parseKey.dm-key-2.successes"] == 1


def test_parse_document_mind_shards_hedge_attempt_can_win(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core.parser.document_mind_parser import _parse_document_mind_shards_with_hedging
    from core.parser.pdf_sharding import PdfShard

    shard_path = tmp_path / "shard_001.pdf"
    shard_path.write_bytes(b"%PDF-1.4\n")
    shard = PdfShard(index=1, start_page=0, end_page=1, path=shard_path)
    credentials = _parse_document_mind_credential_pool("ak-1", "sk-1", "ak-2:sk-2")
    pool = _DocumentMindCredentialPool(credentials, max_inflight_per_key=1, cooldown_seconds=60)
    timings = _DocumentMindParseTimings()
    calls: list[bool] = []

    def fake_single(
        shard: PdfShard,
        output_path: Path,
        source_name: str,
        total_shards: int,
        log_fn: object,
        credential_pool: object,
        timings: object = None,
        *,
        is_hedge: bool = False,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, ContentBlock]]:
        del output_path, total_shards, log_fn, credential_pool, timings, _kwargs
        calls.append(is_hedge)
        if not is_hedge:
            time.sleep(0.2)
            return [
                (
                    0,
                    shard.index,
                    0,
                    ContentBlock(type=BlockType.TEXT, text="primary", page_idx=0, source_file=source_name),
                )
            ]
        return [
            (
                0,
                shard.index,
                0,
                ContentBlock(type=BlockType.TEXT, text="hedge", page_idx=0, source_file=source_name),
            )
        ]

    monkeypatch.setattr("core.parser.document_mind_parser._parse_document_mind_shard_once", fake_single)

    records = _parse_document_mind_shards_with_hedging(
        [shard],
        tmp_path / "out",
        "book.pdf",
        max_workers=1,
        log_fn=lambda _msg: None,
        credential_pool=pool,
        timings=timings,
        hedge_after_seconds=0.01,
        hedge_max_extra_attempts=1,
    )

    assert [record[3].text for record in records] == ["hedge"]
    assert calls.count(False) == 1
    assert calls.count(True) == 1
    metrics = timings.metrics()
    assert metrics["parseHedgeAttempts"] == 1
    assert metrics["parseHedgeWins"] == 1


def test_markdown_result_converts_to_content_blocks() -> None:
    blocks = convert_document_mind_result(
        "# Chapter 1\n\nBody text.\n\n| A | B |\n| - | - |\n| 1 | 2 |",
        source_file="book.pdf",
    )

    assert [block.type for block in blocks] == [BlockType.TITLE, BlockType.TEXT, BlockType.TABLE]
    assert blocks[0].text == "Chapter 1"
    assert blocks[0].text_level == 1
    assert blocks[1].text == "Body text."
    assert blocks[2].is_table is True
    assert blocks[2].table_html == "| A | B |\n| - | - |\n| 1 | 2 |"
    assert all(block.source_file == "book.pdf" for block in blocks)


def test_markdown_result_preserves_images_as_image_blocks(tmp_path: Path) -> None:
    blocks = convert_document_mind_result(
        "# Chapter 1\n\n![图1 解剖示意图](images/fig1.png)\n\nText.\n\n![远程图](https://example.com/fig2.png)",
        source_file="book.pdf",
        output_path=tmp_path,
    )

    assert [block.type for block in blocks] == [
        BlockType.TITLE,
        BlockType.IMAGE,
        BlockType.TEXT,
        BlockType.IMAGE,
    ]
    assert blocks[1].text == "图1 解剖示意图"
    assert blocks[1].image_path == str(tmp_path / "images/fig1.png")
    assert blocks[3].text == "远程图"
    assert blocks[3].image_path == "https://example.com/fig2.png"


def test_markdown_inline_image_is_split_into_image_block(tmp_path: Path) -> None:
    blocks = convert_document_mind_result(
        "正文引用图片 ![图2 病理切片](images/fig2.png) 后继续解释。",
        source_file="book.pdf",
        output_path=tmp_path,
    )

    assert [block.type for block in blocks] == [BlockType.IMAGE, BlockType.TEXT]
    assert blocks[0].text == "图2 病理切片"
    assert blocks[0].image_path == str(tmp_path / "images/fig2.png")
    assert blocks[1].text == "正文引用图片  后继续解释。"


def test_markdown_and_visual_layout_images_are_merged(tmp_path: Path) -> None:
    payload = {
        "markdown": "# Chapter\n\nBody text.",
        "visualLayoutInfo": [
            {"layoutType": "image", "imageUrl": "https://example.com/page-1.png", "page": 2}
        ],
    }

    blocks = convert_document_mind_result(payload, source_file="book.pdf", output_path=tmp_path)

    assert [block.type for block in blocks] == [BlockType.TITLE, BlockType.TEXT, BlockType.IMAGE]
    assert blocks[2].page_idx == 1
    assert blocks[2].image_path == "https://example.com/page-1.png"


def test_block_payload_converts_common_fields(tmp_path: Path) -> None:
    payload = {
        "result": {
            "blocks": [
                {"type": "heading", "text": "Section", "page": 2, "level": 2},
                {
                    "type": "table",
                    "html": "<table><tr><td>A</td></tr></table>",
                    "page_idx": 3,
                    "bbox": [1, 2, 3, 4],
                },
                {"type": "figure", "imagePath": "images/fig1.png", "pageNo": 4},
                {"type": "image", "imageUrl": "https://example.com/fig2.png", "pageNo": 5},
            ]
        }
    }

    blocks = convert_document_mind_result(payload, source_file="book.pdf", output_path=tmp_path)

    assert [block.type for block in blocks] == [BlockType.TITLE, BlockType.TABLE, BlockType.IMAGE, BlockType.IMAGE]
    assert blocks[0].page_idx == 1
    assert blocks[0].text_level == 2
    assert blocks[1].page_idx == 3
    assert blocks[1].table_html == "<table><tr><td>A</td></tr></table>"
    assert blocks[1].bbox == [1.0, 2.0, 3.0, 4.0]
    assert blocks[2].page_idx == 3
    assert blocks[2].image_path == str(tmp_path / "images/fig1.png")
    assert blocks[3].page_idx == 4
    assert blocks[3].image_path == "https://example.com/fig2.png"


def test_nested_pages_payload_is_flattened() -> None:
    payload = {
        "pages": [
            {"pageNo": 1, "blocks": [{"type": "text", "text": "A", "page": 1}]},
            {"pageNo": 2, "paragraphs": [{"type": "text", "text": "B", "page": 2}]},
        ]
    }

    blocks = convert_document_mind_result(payload, source_file="book.pdf")

    assert [block.text for block in blocks] == ["A", "B"]
    assert [block.page_idx for block in blocks] == [0, 1]


def test_parse_pdf_requires_document_mind_sdk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")

    def raise_missing_sdk(_credential: object | None = None) -> object:
        raise ImportError("Alibaba Document Mind SDK is not installed. Document Mind SDK")

    monkeypatch.setattr("core.parser.document_mind_parser._create_document_mind_client", raise_missing_sdk)

    with pytest.raises(ImportError, match="Document Mind SDK"):
        parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"))


def test_parse_pdf_uses_official_sdk_and_oss_credentials_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", raising=False)
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "oss-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "oss-secret")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ENDPOINT", "docmind-api.cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT", "markdown")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")

    calls: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, access_key_id: str, access_key_secret: str) -> None:
            calls["access_key_id"] = access_key_id
            calls["access_key_secret"] = access_key_secret
            self.endpoint = ""

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def to_map(self) -> object:
            return self.payload

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            calls["endpoint"] = config.endpoint

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> FakeResponse:
            calls["submit_request"] = request
            calls["runtime"] = runtime
            return FakeResponse({"body": {"data": {"id": "job-1"}}})

        def query_doc_parser_status(self, request: object) -> FakeResponse:
            calls["status_request"] = request
            return FakeResponse({"body": {"data": {"status": "success"}}})

        def get_doc_parser_result(self, request: object) -> FakeResponse:
            calls["result_request"] = request
            return FakeResponse({"body": {"data": {"markdown": "# Title\nText"}}})

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711.client",
        types.SimpleNamespace(Client=FakeClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi",
        types.SimpleNamespace(models=types.SimpleNamespace(Config=FakeConfig)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(
            models=types.SimpleNamespace(
                SubmitDocParserJobAdvanceRequest=SimpleRequest,
                QueryDocParserStatusRequest=SimpleRequest,
                GetDocParserResultRequest=SimpleRequest,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert [block.text for block in blocks] == ["Title", "Text"]
    assert calls["access_key_id"] == "oss-key"
    assert calls["access_key_secret"] == "oss-secret"
    assert calls["endpoint"] == "docmind-api.cn-hangzhou.aliyuncs.com"
    submit_request = calls["submit_request"]
    assert getattr(submit_request, "file_name") == "book.pdf"
    assert getattr(submit_request, "file_name_extension") == "pdf"
    assert getattr(submit_request, "output_format") == ["markdown"]
    assert getattr(submit_request, "llm_enhancement") is False
    assert getattr(submit_request, "enhancement_mode") == "VLM"


def test_parse_pdf_accepts_document_mind_capitalized_response_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")

    calls: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, access_key_id: str, access_key_secret: str) -> None:
            self.endpoint = ""

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def to_map(self) -> object:
            return self.payload

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> FakeResponse:
            return FakeResponse({"body": {"Data": {"Id": "docmind-job-1"}, "RequestId": "req-1"}})

        def query_doc_parser_status(self, request: object) -> FakeResponse:
            calls["status_id"] = getattr(request, "id")
            return FakeResponse({"body": {"Data": {"Status": "success"}}})

        def get_doc_parser_result(self, request: object) -> FakeResponse:
            calls["result_id"] = getattr(request, "id")
            return FakeResponse({"body": {"Data": {"Markdown": "# Title\nText"}}})

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711.client",
        types.SimpleNamespace(Client=FakeClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi",
        types.SimpleNamespace(models=types.SimpleNamespace(Config=FakeConfig)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(
            models=types.SimpleNamespace(
                SubmitDocParserJobAdvanceRequest=SimpleRequest,
                QueryDocParserStatusRequest=SimpleRequest,
                GetDocParserResultRequest=SimpleRequest,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert calls["status_id"] == "docmind-job-1"
    assert calls["result_id"] == "docmind-job-1"
    assert [block.text for block in blocks] == ["Title", "Text"]


def test_parse_pdf_skips_result_fetch_when_status_contains_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")
    monkeypatch.setattr(
        "core.parser.document_mind_parser.inspect_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("skip")),
    )

    calls = {"result": 0}

    class FakeClient:
        def __init__(self, config: object) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> _FakeResponse:
            return _FakeResponse({"body": {"Data": {"Id": "docmind-job-1"}}})

        def query_doc_parser_status(self, request: object) -> _FakeResponse:
            return _FakeResponse(
                {
                    "body": {
                        "Data": {
                            "Status": "success",
                            "Markdown": "# From status\nOK",
                        }
                    }
                }
            )

        def get_doc_parser_result(self, request: object) -> _FakeResponse:
            calls["result"] += 1
            return _FakeResponse({"body": {"Data": {"Markdown": "# From result\nSlow"}}})

    _install_fake_document_mind_modules(monkeypatch, FakeClient)

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert calls["result"] == 0
    assert [block.text for block in blocks] == ["From status", "OK"]
    metrics = get_last_document_mind_key_pool_metrics()
    assert metrics["resultFetchSkippedByStatus"] == 1
    assert metrics["statusConvertMs"] >= 0
    assert "resultFetchMs" not in metrics


def test_parse_pdf_retries_empty_document_mind_result_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES", "1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES", "0")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY", "0")
    monkeypatch.setattr("core.parser.document_mind_parser.inspect_pdf", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("skip")))

    calls = {"result": 0}

    class FakeClient:
        def __init__(self, config: object) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> _FakeResponse:
            return _FakeResponse({"body": {"Data": {"Id": "docmind-job-1"}}})

        def query_doc_parser_status(self, request: object) -> _FakeResponse:
            return _FakeResponse({"body": {"Data": {"Status": "success"}}})

        def get_doc_parser_result(self, request: object) -> _FakeResponse:
            calls["result"] += 1
            if calls["result"] == 1:
                return _FakeResponse({"body": {"Data": {}}})
            return _FakeResponse({"body": {"Data": {"markdown": "# Retry\nOK"}}})

    _install_fake_document_mind_modules(monkeypatch, FakeClient)

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert calls["result"] == 2
    assert [block.text for block in blocks] == ["Retry", "OK"]


def test_parse_pdf_resubmits_job_after_persistent_empty_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES", "0")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES", "1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY", "0")
    monkeypatch.setattr("core.parser.document_mind_parser.inspect_pdf", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("skip")))

    calls = {"submit": 0}

    class FakeClient:
        def __init__(self, config: object) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> _FakeResponse:
            calls["submit"] += 1
            return _FakeResponse({"body": {"Data": {"Id": f"docmind-job-{calls['submit']}"}}})

        def query_doc_parser_status(self, request: object) -> _FakeResponse:
            return _FakeResponse({"body": {"Data": {"Status": "success"}}})

        def get_doc_parser_result(self, request: object) -> _FakeResponse:
            if getattr(request, "id") == "docmind-job-1":
                return _FakeResponse({"body": {"Data": {}}})
            return _FakeResponse({"body": {"Data": {"markdown": "# Resubmitted\nOK"}}})

    _install_fake_document_mind_modules(monkeypatch, FakeClient)

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert calls["submit"] == 2
    assert [block.text for block in blocks] == ["Resubmitted", "OK"]


def test_parse_pdf_merges_status_output_format_result_images(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT", "markdown,visualLayoutInfo")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "0")

    class FakeConfig:
        def __init__(self, access_key_id: str, access_key_secret: str) -> None:
            self.endpoint = ""

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def to_map(self) -> object:
            return self.payload

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> FakeResponse:
            return FakeResponse({"body": {"Data": {"Id": "docmind-job-1"}}})

        def query_doc_parser_status(self, request: object) -> FakeResponse:
            return FakeResponse(
                {
                    "body": {
                        "Data": {
                            "Status": "success",
                            "OutputFormatResult": {
                                "Pages": [
                                    {
                                        "PageIdCurDoc": 2,
                                        "ImageUrl": "https://example.com/page-2.png",
                                    }
                                ]
                            },
                        }
                    }
                }
            )

        def get_doc_parser_result(self, request: object) -> FakeResponse:
            return FakeResponse({"body": {"Data": {"Markdown": "# Title\nBody"}}})

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711.client",
        types.SimpleNamespace(Client=FakeClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi",
        types.SimpleNamespace(models=types.SimpleNamespace(Config=FakeConfig)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(
            models=types.SimpleNamespace(
                SubmitDocParserJobAdvanceRequest=SimpleRequest,
                QueryDocParserStatusRequest=SimpleRequest,
                GetDocParserResultRequest=SimpleRequest,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert [block.type for block in blocks] == [BlockType.TITLE, BlockType.TEXT, BlockType.IMAGE]
    assert blocks[2].page_idx == 1
    assert blocks[2].image_path == "https://example.com/page-2.png"
    metrics = get_last_document_mind_key_pool_metrics()
    assert metrics.get("resultFetchSkippedByStatus", 0) == 0
    assert metrics["resultFetchMs"] >= 0


def test_parse_pdf_raises_document_mind_poll_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")

    class FakeConfig:
        def __init__(self, access_key_id: str, access_key_secret: str) -> None:
            self.endpoint = ""

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def to_map(self) -> object:
            return self.payload

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            pass

        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> FakeResponse:
            return FakeResponse({"body": {"Data": {"Id": "docmind-job-1"}}})

        def query_doc_parser_status(self, request: object) -> FakeResponse:
            return FakeResponse(
                {
                    "body": {
                        "Code": "DocSizeLimitError",
                        "Message": "The document you provided is beyond the size limitation.",
                        "RequestId": "req-1",
                    }
                }
            )

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711.client",
        types.SimpleNamespace(Client=FakeClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi",
        types.SimpleNamespace(models=types.SimpleNamespace(Config=FakeConfig)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(
            models=types.SimpleNamespace(
                SubmitDocParserJobAdvanceRequest=SimpleRequest,
                QueryDocParserStatusRequest=SimpleRequest,
                GetDocParserResultRequest=SimpleRequest,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )

    with pytest.raises(RuntimeError, match="DocSizeLimitError"):
        parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")


def test_parse_pdf_rejects_unsupported_document_mind_output_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT", "html")

    with pytest.raises(ValueError, match="ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT"):
        parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"))


def test_document_mind_default_output_formats_include_layout_info() -> None:
    from core.parser.document_mind_parser import _parse_output_formats

    assert _parse_output_formats(None) == ["markdown", "visualLayoutInfo"]


def test_document_mind_managed_llm_enhancement_defaults_to_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core.parser.document_mind_parser import _submit_document_mind_job

    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT", raising=False)
    captured: dict[str, object] = {}

    class FakeResponse:
        def to_map(self) -> object:
            return {"body": {"data": {"id": "docmind-job-1"}}}

    class SimpleRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class FakeClient:
        def submit_doc_parser_job_advance(self, request: object, runtime: object) -> FakeResponse:
            captured["request"] = request
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_docmind_api20220711",
        types.SimpleNamespace(models=types.SimpleNamespace(SubmitDocParserJobAdvanceRequest=SimpleRequest)),
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_util",
        types.SimpleNamespace(models=types.SimpleNamespace(RuntimeOptions=object)),
    )

    job_id = _submit_document_mind_job(FakeClient(), str(pdf_path), "book.pdf", lambda message: None)

    request = captured["request"]
    assert job_id == "docmind-job-1"
    assert getattr(request, "output_format") == ["markdown", "visualLayoutInfo"]
    assert getattr(request, "llm_enhancement") is False


def test_document_mind_uses_shared_pdf_sharding_module() -> None:
    source = Path("core/parser/document_mind_parser.py").read_text(encoding="utf-8")

    assert "core.parser.pdf_sharding" in source
    assert "core.parser.mineru_parser" not in source


def test_document_mind_sharding_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED", "true")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB", "150")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES", "50")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD", "25")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD", "20")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES", "4")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES", "2")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED", "false")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST", "false")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE", "1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE", "true")

    cfg = _get_document_mind_sharding_config()

    assert cfg == {
        "enabled": True,
        "min_file_mb": 150.0,
        "min_pages": 50,
        "pages_per_shard": 25,
        "max_concurrency": 4,
        "min_pages_per_shard": 20,
        "target_waves": 4,
        "text_sample_pages": 2,
        "weighted_sharding_enabled": False,
        "heavy_shard_first": False,
        "shard_save_garbage": 1,
        "shard_save_deflate": True,
    }


def test_document_mind_hedge_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED", "true")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS", "12.5")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS", "2")

    cfg = _get_document_mind_hedge_config()

    assert cfg == {
        "enabled": True,
        "after_seconds": 12.5,
        "max_extra_attempts": 2,
    }


def test_document_mind_sharding_config_does_not_default_concurrency_to_credential_pool_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY", raising=False)

    cfg = _get_document_mind_sharding_config()

    assert cfg["max_concurrency"] == 4


def test_parse_pdf_sharded_defaults_worker_limit_to_independent_parse_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, 6)
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", "ak-2:sk-2,ak-3:sk-3,ak-4:sk-4,ak-5:sk-5,ak-6:sk-6")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES", "1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD", "1")
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY", raising=False)
    worker_counts: list[int] = []

    class RecordingExecutor:
        def __init__(self, max_workers: int) -> None:
            worker_counts.append(max_workers)

        def __enter__(self) -> "RecordingExecutor":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def submit(self, fn: object, *args: object, **kwargs: object) -> object:
            class ImmediateFuture:
                def __init__(self, result: object) -> None:
                    self._result = result

                def result(self) -> object:
                    return self._result

            return ImmediateFuture(fn(*args, **kwargs))

    def immediate_as_completed(futures: object) -> object:
        return list(futures)

    def fake_single(
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[ContentBlock]:
        del output_dir, log_fn
        return [ContentBlock(type=BlockType.TEXT, text=Path(pdf_path).stem, page_idx=0, source_file=source_name)]

    monkeypatch.setattr("core.parser.document_mind_parser.ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr("core.parser.document_mind_parser.as_completed", immediate_as_completed)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single", fake_single)

    parse_pdf_sharded(
        str(pdf_path),
        output_dir=str(tmp_path / "out"),
        original_name="book.pdf",
        inspection=PdfInspection(6, 1, 0, 0, False),
        sharding_config=None,
    )

    assert worker_counts == [4]


def test_document_mind_sharding_threshold_uses_pages_or_size() -> None:
    cfg = {
        "enabled": True,
        "min_file_mb": 150.0,
        "min_pages": 50,
        "pages_per_shard": 40,
        "max_concurrency": 3,
        "min_pages_per_shard": 20,
        "target_waves": 4,
        "text_sample_pages": 5,
    }

    large_by_pages = PdfInspection(51, 10 * 1024 * 1024, 100, 1, False)
    large_by_size = PdfInspection(41, 151 * 1024 * 1024, 100, 1, False)
    large_by_size_few_pages = PdfInspection(20, 151 * 1024 * 1024, 100, 1, False)
    small = PdfInspection(40, 10 * 1024 * 1024, 100, 1, False)

    assert _should_parse_document_mind_with_shards(large_by_pages, cfg) is True
    assert _should_parse_document_mind_with_shards(large_by_size, cfg) is True
    assert _should_parse_document_mind_with_shards(large_by_size_few_pages, cfg) is True
    assert _should_parse_document_mind_with_shards(small, cfg) is False


def test_document_mind_pages_per_shard_is_reduced_for_large_files() -> None:
    cfg = {
        "enabled": True,
        "min_file_mb": 150.0,
        "min_pages": 50,
        "pages_per_shard": 40,
        "max_concurrency": 3,
        "text_sample_pages": 5,
    }
    inspection = PdfInspection(20, 200 * 1024 * 1024, 100, 1, False)

    assert _get_effective_document_mind_pages_per_shard(inspection, cfg) == 15


def test_document_mind_pages_per_shard_balances_worker_waves() -> None:
    cfg = {
        "enabled": True,
        "min_file_mb": 150.0,
        "min_pages": 50,
        "pages_per_shard": 40,
        "max_concurrency": 4,
        "min_pages_per_shard": 20,
        "target_waves": 4,
        "text_sample_pages": 5,
    }
    inspection = PdfInspection(396, 45 * 1024 * 1024, 100, 5, False)

    assert _get_effective_document_mind_pages_per_shard(inspection, cfg, pool_capacity=4) == 25


def test_parse_pdf_routes_large_document_to_sharded_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    called: dict[str, object] = {}

    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES", "50")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD", "40")
    monkeypatch.setattr(
        "core.parser.document_mind_parser.inspect_pdf",
        lambda path, text_sample_pages=5, **_kwargs: PdfInspection(51, 10 * 1024 * 1024, 100, 1, False),
    )

    def fake_sharded(*args: object, **kwargs: object) -> list[object]:
        called["args"] = args
        called["kwargs"] = kwargs
        return []

    monkeypatch.setattr("core.parser.document_mind_parser.parse_pdf_sharded", fake_sharded)

    blocks = parse_pdf(str(pdf_path), output_dir=str(tmp_path / "out"), original_name="book.pdf")

    assert blocks == []
    assert called["args"][0] == str(pdf_path)
    assert called["kwargs"]["original_name"] == "book.pdf"


def test_parse_pdf_sharded_offsets_pages_and_preserves_source_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, 5)
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", "ak-2:sk-2,ak-3:sk-3")
    worker_counts: list[int] = []

    class RecordingExecutor:
        def __init__(self, max_workers: int) -> None:
            worker_counts.append(max_workers)
            self.max_workers = max_workers

        def __enter__(self) -> "RecordingExecutor":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def submit(self, fn: object, *args: object, **kwargs: object) -> object:
            class ImmediateFuture:
                def __init__(self, result: object) -> None:
                    self._result = result

                def result(self) -> object:
                    return self._result

            return ImmediateFuture(fn(*args, **kwargs))

    def immediate_as_completed(futures: object) -> object:
        return list(futures)

    def fake_single(
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[object]:
        timings = _kwargs.get("timings")
        if timings is not None:
            timings.add_ms("submitWallMs", 3, max_key="submitWallMsMax")
            timings.add_ms("pollWallMs", 7, max_key="pollWallMsMax")
            timings.add_ms("resultFetchMs", 2, max_key="resultFetchMsMax")
            timings.add_ms("convertMs", 1, max_key="convertMsMax")
        return [
            ContentBlock(type=BlockType.TEXT, text=f"{Path(pdf_path).stem}-A", page_idx=0, source_file="wrong.pdf"),
            ContentBlock(type=BlockType.TEXT, text=f"{Path(pdf_path).stem}-B", page_idx=1, source_file="wrong.pdf"),
        ]

    monkeypatch.setattr("core.parser.document_mind_parser.ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr("core.parser.document_mind_parser.as_completed", immediate_as_completed)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single", fake_single)

    blocks = parse_pdf_sharded(
        str(pdf_path),
        output_dir=str(tmp_path / "out"),
        original_name="book.pdf",
        inspection=PdfInspection(5, 1, 0, 0, False),
        sharding_config={
            "enabled": True,
            "min_file_mb": 150.0,
            "min_pages": 1,
            "pages_per_shard": 2,
            "max_concurrency": 4,
            "text_sample_pages": 0,
        },
    )

    assert worker_counts == [3]
    assert [block.page_idx for block in blocks] == [0, 1, 2, 3, 4, 5]
    assert all(block.source_file == "book.pdf" for block in blocks)
    metrics = get_last_document_mind_key_pool_metrics()
    assert metrics["configuredPagesPerShard"] == 2
    assert metrics["effectivePagesPerShard"] == 2
    assert metrics["parseShardCount"] == 3
    assert metrics["parseWorkerCount"] == 3
    assert metrics["parseServiceInputPages"] == 5
    assert metrics["parseServiceRequests"] == 3
    assert metrics["parseServiceShardBytes"] > 0
    assert metrics["parseShardSaveGarbage"] == 1
    assert metrics["parseShardSaveDeflate"] == 1
    assert metrics["submitWallMs"] == 9
    assert metrics["submitWallMsMax"] == 3
    assert metrics["pollWallMs"] == 21
    assert metrics["resultFetchMs"] == 6
    assert metrics["convertMs"] == 3
    assert "splitMs" in metrics
    assert "mergeShardMs" in metrics
    assert "shardWallMsMax" in metrics


def test_parse_pdf_sharded_uses_weighted_shards_and_heavy_first_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, 7)
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", "ak-2:sk-2,ak-3:sk-3")
    submitted_shards: list[str] = []

    class RecordingExecutor:
        def __init__(self, max_workers: int) -> None:
            self.max_workers = max_workers

        def __enter__(self) -> "RecordingExecutor":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def submit(self, fn: object, *args: object, **kwargs: object) -> object:
            class ImmediateFuture:
                def __init__(self, result: object) -> None:
                    self._result = result

                def result(self) -> object:
                    return self._result

            return ImmediateFuture(fn(*args, **kwargs))

    def immediate_as_completed(futures: object) -> object:
        return list(futures)

    def fake_single(
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[ContentBlock]:
        del output_dir, log_fn, _kwargs
        shard_name = Path(pdf_path).stem
        submitted_shards.append(shard_name)
        return [ContentBlock(type=BlockType.TEXT, text=shard_name, page_idx=0, source_file=source_name)]

    page_profiles = tuple(
        PdfPageProfile(
            index,
            text_chars=0,
            text_blocks=0,
            image_count=0,
            drawing_count=0,
            likely_scanned=False,
            weight=weight,
        )
        for index, weight in enumerate([1, 1, 20, 1, 1, 1, 1])
    )

    monkeypatch.setattr("core.parser.document_mind_parser.ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr("core.parser.document_mind_parser.as_completed", immediate_as_completed)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single", fake_single)

    blocks = parse_pdf_sharded(
        str(pdf_path),
        output_dir=str(tmp_path / "out"),
        original_name="book.pdf",
        inspection=PdfInspection(7, 1, 0, 0, False, page_profiles=page_profiles),
        sharding_config={
            "enabled": True,
            "min_file_mb": 150.0,
            "min_pages": 1,
            "pages_per_shard": 3,
            "max_concurrency": 3,
            "min_pages_per_shard": 1,
            "target_waves": 1,
            "text_sample_pages": 0,
            "weighted_sharding_enabled": True,
            "heavy_shard_first": True,
        },
    )

    assert [name[:9] for name in submitted_shards] == ["shard_002", "shard_003", "shard_001"]
    assert [block.text[:9] for block in blocks] == ["shard_001", "shard_002", "shard_003"]
    assert [block.page_idx for block in blocks] == [0, 2, 4]
    metrics = get_last_document_mind_key_pool_metrics()
    assert metrics["parseWeightedShardingEnabled"] == 1
    assert metrics["parseHeavyShardFirstEnabled"] == 1
    assert metrics["parseShardWeightTotal"] == 26
    assert metrics["parseShardWeightMax"] == 21
    assert metrics["parseShardWeightMin"] == 2
    assert metrics["parseShardWeightAvg"] == 8
    assert metrics["parseHeaviestShardIndex"] == 2
    assert metrics["parseHeaviestShardPages"] == 2


def test_parse_pdf_sharded_limits_workers_by_credential_pool_capacity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, 6)
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk-1")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", "ak-2:sk-2")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY", "1")
    worker_counts: list[int] = []

    class RecordingExecutor:
        def __init__(self, max_workers: int) -> None:
            worker_counts.append(max_workers)

        def __enter__(self) -> "RecordingExecutor":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def submit(self, fn: object, *args: object, **kwargs: object) -> object:
            class ImmediateFuture:
                def __init__(self, result: object) -> None:
                    self._result = result

                def result(self) -> object:
                    return self._result

            return ImmediateFuture(fn(*args, **kwargs))

    def immediate_as_completed(futures: object) -> object:
        return list(futures)

    def fake_single(
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[ContentBlock]:
        del output_dir, log_fn
        return [ContentBlock(type=BlockType.TEXT, text=Path(pdf_path).stem, page_idx=0, source_file=source_name)]

    monkeypatch.setattr("core.parser.document_mind_parser.ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr("core.parser.document_mind_parser.as_completed", immediate_as_completed)
    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single", fake_single)

    parse_pdf_sharded(
        str(pdf_path),
        output_dir=str(tmp_path / "out"),
        original_name="book.pdf",
        inspection=PdfInspection(6, 1, 0, 0, False),
        sharding_config={
            "enabled": True,
            "min_file_mb": 150.0,
            "min_pages": 1,
            "pages_per_shard": 1,
            "max_concurrency": 4,
            "text_sample_pages": 0,
        },
    )

    assert worker_counts == [2]


def test_parse_pdf_sharded_reports_failed_shard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "book.pdf"
    _make_pdf(pdf_path, 3)
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "sk")

    def fake_single(
        pdf_path: str,
        output_dir: str,
        log_fn: object,
        source_name: str,
        **_kwargs: object,
    ) -> list[object]:
        if "shard_002" in Path(pdf_path).name:
            raise RuntimeError("boom")
        return [ContentBlock(type=BlockType.TEXT, text="ok", page_idx=0, source_file=source_name)]

    monkeypatch.setattr("core.parser.document_mind_parser._parse_pdf_single", fake_single)

    with pytest.raises(RuntimeError, match=r"shard #2 P2-2"):
        parse_pdf_sharded(
            str(pdf_path),
            output_dir=str(tmp_path / "out"),
            original_name="book.pdf",
            inspection=PdfInspection(3, 1, 0, 0, False),
            sharding_config={
                "enabled": True,
                "min_file_mb": 150.0,
                "min_pages": 1,
                "pages_per_shard": 1,
                "max_concurrency": 1,
                "text_sample_pages": 0,
            },
        )
