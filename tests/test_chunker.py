"""Unit tests for chunking strategies and chunk relation linking."""
from __future__ import annotations

import time
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chunker import get_strategy, list_strategies
from core.chunker import hierarchical as hierarchical_module
from core.chunker.linker import link_related_chunks
from core.models.content_block import BlockType, Chunk, ContentBlock


def _sample_blocks() -> list[ContentBlock]:
    return [
        ContentBlock(type=BlockType.TITLE, text="Title", page_idx=0, text_level=1, source_file="t.pdf"),
        ContentBlock(type=BlockType.TEXT, text="A" * 600, page_idx=0, source_file="t.pdf"),
        ContentBlock(
            type=BlockType.TABLE,
            text="",
            page_idx=1,
            is_table=True,
            table_html="<table></table>",
            source_file="t.pdf",
        ),
        ContentBlock(type=BlockType.TEXT, text="End.", page_idx=1, source_file="t.pdf"),
    ]


def test_all_strategies_registered():
    names = list_strategies()
    assert "fixed_length" in names
    assert "semantic" in names
    assert "separator" in names
    assert "llm" in names


def test_fixed_length_splits_long_text():
    strategy = get_strategy("fixed_length", chunk_size=200, overlap=20)
    chunks = strategy.chunk(_sample_blocks())
    text_chunks = [chunk for chunk in chunks if not chunk.is_table_chunk]
    assert len(text_chunks) >= 3
    assert all(chunk.char_count <= 220 for chunk in text_chunks)


def test_fixed_length_preserves_tables():
    chunks = get_strategy("fixed_length").chunk(_sample_blocks())
    tables = [chunk for chunk in chunks if chunk.is_table_chunk]
    assert len(tables) == 1
    assert tables[0].content == "<table></table>"


def test_semantic_groups_by_title():
    chunks = get_strategy("semantic").chunk(_sample_blocks())
    non_table = [chunk for chunk in chunks if not chunk.is_table_chunk]
    assert any(chunk.title == "Title" for chunk in non_table)


def test_separator_splits():
    blocks = [
        ContentBlock(type=BlockType.TEXT, text="First sentence. Second sentence. Third.", page_idx=0, source_file="t.pdf"),
    ]
    chunks = get_strategy("separator", separators=[". "]).chunk(blocks)
    assert len(chunks) >= 2


def test_llm_fallback():
    blocks = [
        ContentBlock(type=BlockType.TEXT, text="A" * 1000, page_idx=0, source_file="t.pdf"),
    ]
    with patch("core.chunker.llm_chunker.resolve_llm_param", return_value=""):
        chunks = get_strategy("llm", max_chunk_size=400, api_key="").chunk(blocks)
    assert len(chunks) >= 2


def test_metadata_consistency():
    for name in list_strategies():
        chunks = get_strategy(name).chunk(_sample_blocks())
        for chunk in chunks:
            assert chunk.id
            assert chunk.source == "t.pdf"
            assert chunk.strategy == name
            assert chunk.char_count > 0
            assert chunk.page >= 0


def test_empty_input():
    for name in list_strategies():
        assert get_strategy(name).chunk([]) == []


def _hierarchical_enhancement_blocks() -> list[ContentBlock]:
    return [
        ContentBlock(type=BlockType.TITLE, text="循环系统", page_idx=0, text_level=1, source_file="book.pdf"),
        ContentBlock(
            type=BlockType.TEXT,
            text="first text chunk " + "A" * 120,
            page_idx=0,
            source_file="book.pdf",
        ),
        ContentBlock(
            type=BlockType.TABLE,
            text="",
            page_idx=1,
            is_table=True,
            table_html="<table><tr><td>血液</td><td>运输氧气</td></tr></table>",
            source_file="book.pdf",
        ),
        ContentBlock(
            type=BlockType.IMAGE,
            text="图1-1 血液循环示意图",
            page_idx=2,
            image_path="data/output/circulation.png",
            source_file="book.pdf",
        ),
        ContentBlock(
            type=BlockType.TEXT,
            text="如上所述，second fragment chunk 用于说明血液循环和氧气交换。",
            page_idx=3,
            source_file="book.pdf",
        ),
    ]


def _normalized_chunks(chunks: list[Chunk]) -> list[dict]:
    id_to_pos = {chunk.id: idx for idx, chunk in enumerate(chunks)}
    return [
        {
            "content": chunk.content,
            "source": chunk.source,
            "page": chunk.page,
            "chunk_index": chunk.chunk_index,
            "strategy": chunk.strategy,
            "title": chunk.title,
            "layer": chunk.layer,
            "is_table_chunk": chunk.is_table_chunk,
            "is_image_chunk": chunk.is_image_chunk,
            "image_path": chunk.image_path,
            "parent_pos": id_to_pos.get(chunk.parent_id),
            "enhanced_text": chunk.enhanced_text,
            "token_cost": chunk.token_cost,
        }
        for chunk in chunks
    ]

def test_hierarchical_can_skip_enhanced_layer_for_basic_ready_mode():
    strategy = get_strategy("hierarchical", child_max_chars=1000, enable_enhanced=False)

    chunks = strategy.chunk(_hierarchical_enhancement_blocks())

    assert chunks
    assert all(chunk.layer != "enhanced" for chunk in chunks)
    assert any(chunk.layer == "child" for chunk in chunks)
    assert any(chunk.is_table_chunk for chunk in chunks)
    assert any(chunk.is_image_chunk for chunk in chunks)
    assert strategy.last_timings["enhanceTasks"] == 0
    assert strategy.last_timings["enhanceRequests"] == 0


def _patch_fake_enhancers():
    def fake_text(content: str, *_args, **_kwargs):
        return (f'{{"summary":"text:{content[:20]}","questions":[]}}', 11, "")

    def fake_fragment(content: str, *_args, **_kwargs):
        return (f'{{"summary":"fragment:{content[:20]}","questions":[]}}', 13, "")

    def fake_table(table_content: str, *_args, **_kwargs):
        return (f'{{"summary":"table:{table_content[:20]}","questions":[]}}', 17, "")

    def fake_image(image_path: str | None, alt_text: str, *_args, **_kwargs):
        return (f'{{"summary":"image:{alt_text[:20]}","questions":[]}}', 19, "")

    return (
        patch("core.chunker.hierarchical._generate_enhanced_text", side_effect=fake_text),
        patch("core.chunker.hierarchical._generate_fragment_enhancement", side_effect=fake_fragment),
        patch("core.chunker.hierarchical._generate_table_summary", side_effect=fake_table),
        patch("core.chunker.hierarchical._generate_image_description", side_effect=fake_image),
    )


def test_hierarchical_parallel_ordered_matches_serial_output(monkeypatch):
    blocks = _hierarchical_enhancement_blocks()
    patches = _patch_fake_enhancers()
    with patches[0], patches[1], patches[2], patches[3]:
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "serial")
        serial_strategy = get_strategy("hierarchical", child_max_chars=1000)
        serial_chunks = serial_strategy.chunk(blocks)

        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered")
        monkeypatch.setenv("HIERARCHICAL_TEXT_ENHANCE_WORKERS", "3")
        monkeypatch.setenv("HIERARCHICAL_TABLE_ENHANCE_WORKERS", "2")
        monkeypatch.setenv("HIERARCHICAL_IMAGE_ENHANCE_WORKERS", "1")
        parallel_strategy = get_strategy("hierarchical", child_max_chars=1000)
        parallel_chunks = parallel_strategy.chunk(blocks)

    assert _normalized_chunks(parallel_chunks) == _normalized_chunks(serial_chunks)
    assert parallel_strategy.total_tokens == serial_strategy.total_tokens
    assert parallel_strategy.last_timings["enhanceWallMs"] >= 0


def test_hierarchical_parallel_ordered_keeps_slot_order_when_tasks_finish_out_of_order(monkeypatch):
    blocks = _hierarchical_enhancement_blocks()

    def slow_text(content: str, *_args, **_kwargs):
        if content.startswith("first"):
            time.sleep(0.05)
        return (f'{{"summary":"text:{content[:20]}","questions":[]}}', 11, "")

    patches = _patch_fake_enhancers()
    with patch("core.chunker.hierarchical._generate_enhanced_text", side_effect=slow_text), patches[1], patches[2], patches[3]:
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered")
        monkeypatch.setenv("HIERARCHICAL_TEXT_ENHANCE_WORKERS", "2")
        chunks = get_strategy("hierarchical", child_max_chars=1000).chunk(blocks)

    layers_and_prefixes = [
        (chunk.layer, chunk.content.split(" ", 1)[0])
        for chunk in chunks
    ]
    assert layers_and_prefixes == [
        ("parent", "循环系统"),
        ("child", "first"),
        ("enhanced", "[LLM增强]"),
        ("child", "<table><tr><td>血液</td><td>运输氧气</td></tr></table>"),
        ("enhanced", "[表格摘要]"),
        ("child", "图1-1"),
        ("enhanced", "[图片描述]"),
        ("child", "如上所述，second"),
        ("enhanced", "[片段增强]"),
    ]


def test_hierarchical_dynamic_scheduler_borrows_idle_capacity(monkeypatch):
    blocks = [
        ContentBlock(type=BlockType.TITLE, text="Title", page_idx=0, text_level=1, source_file="book.pdf"),
        ContentBlock(type=BlockType.TEXT, text=("A" * 100 + ". ") * 8, page_idx=0, source_file="book.pdf"),
    ]

    def fake_text(content: str, *_args, **_kwargs):
        time.sleep(0.02)
        return (f'{{"summary":"text:{content[:20]}","questions":[]}}', 11, "")

    with patch("core.chunker.hierarchical._generate_enhanced_text", side_effect=fake_text):
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered")
        monkeypatch.setenv("HIERARCHICAL_TEXT_ENHANCE_WORKERS", "1")
        monkeypatch.setenv("HIERARCHICAL_TABLE_ENHANCE_WORKERS", "1")
        monkeypatch.setenv("HIERARCHICAL_IMAGE_ENHANCE_WORKERS", "1")
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MAX_CONCURRENCY", "4")
        strategy = get_strategy("hierarchical", child_max_chars=120)
        chunks = strategy.chunk(blocks)

    assert strategy.last_timings["enhanceScheduler"] == 1
    assert strategy.last_timings["enhanceMaxConcurrency"] == 4
    assert strategy.last_timings["enhancePeakConcurrency"] == 4
    assert len([chunk for chunk in chunks if chunk.layer == "enhanced"]) >= 4


def test_hierarchical_enhancement_uses_llm_key_pool(monkeypatch):
    blocks = [
        ContentBlock(type=BlockType.TITLE, text="Title", page_idx=0, text_level=1, source_file="book.pdf"),
        ContentBlock(type=BlockType.TEXT, text=("A" * 100 + "\n") * 5, page_idx=0, source_file="book.pdf"),
    ]
    used_keys: list[str] = []

    def fake_text(content: str, _title: str, _model: str, _base_url: str, api_key: str, **_kwargs):
        used_keys.append(api_key)
        return (f'{{"summary":"text:{content[:10]}","questions":[]}}', 11, "")

    with patch("core.chunker.hierarchical._generate_enhanced_text", side_effect=fake_text):
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "serial")
        monkeypatch.setenv("LLM_API_KEY", "key-primary")
        monkeypatch.setenv("LLM_API_KEY_POOL", "key-a,key-b")
        strategy = get_strategy("hierarchical", child_max_chars=120)
        strategy.chunk(blocks)

    assert used_keys[:3] == ["key-primary", "key-a", "key-b"]
    assert strategy.last_timings["enhanceLlmKeyPoolSize"] == 3
    assert strategy.last_timings["enhanceKeyPoolSize"] >= 3
    assert strategy.last_timings["enhanceLlmKey.llm-key-1.calls"] >= 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-2.calls"] >= 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-3.calls"] >= 1
    assert "key-primary" not in repr(strategy.last_timings)
    assert "key-a" not in repr(strategy.last_timings)
    assert "key-b" not in repr(strategy.last_timings)


def test_hierarchical_key_pool_is_limited_to_20_entries() -> None:
    pool = ",".join(f"key-{index}" for index in range(25))

    keys = hierarchical_module._parse_api_key_pool("primary", pool)

    assert len(keys) == 20
    assert keys[0] == "primary"
    assert keys[-1] == "key-18"


def test_hierarchical_key_pool_retries_throttled_key(monkeypatch):
    blocks = [
        ContentBlock(type=BlockType.TITLE, text="Title", page_idx=0, text_level=1, source_file="book.pdf"),
        ContentBlock(type=BlockType.TEXT, text="A" * 160, page_idx=0, source_file="book.pdf"),
    ]
    used_keys: list[str] = []

    def fake_text(content: str, _title: str, _model: str, _base_url: str, api_key: str, **_kwargs):
        used_keys.append(api_key)
        if api_key == "key-limited":
            return (None, 0, "429 rate limit")
        return (f'{{"summary":"text:{content[:10]}","questions":[]}}', 11, "")

    with patch("core.chunker.hierarchical._generate_enhanced_text", side_effect=fake_text):
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "serial")
        monkeypatch.setenv("HIERARCHICAL_ENHANCE_KEY_RETRIES", "1")
        monkeypatch.setenv("LLM_API_KEY", "key-limited")
        monkeypatch.setenv("LLM_API_KEY_POOL", "key-ok")
        strategy = get_strategy("hierarchical", child_max_chars=1000)
        chunks = strategy.chunk(blocks)

    assert used_keys == ["key-limited", "key-ok"]
    assert [chunk.layer for chunk in chunks] == ["parent", "child", "enhanced"]
    assert strategy.last_timings["enhanceFailures"] == 0
    assert strategy.last_timings["enhanceKeyThrottleCount"] == 1
    assert strategy.last_timings["enhanceKeyRetryCount"] == 1
    assert strategy.last_timings["enhanceKeyCooldownCount"] == 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-1.calls"] == 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-1.failures"] == 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-1.throttles"] == 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-2.calls"] == 1
    assert strategy.last_timings["enhanceLlmKey.llm-key-2.successes"] == 1


def test_hierarchical_missing_enhanced_slot_matches_legacy_gap(monkeypatch):
    blocks = [
        ContentBlock(type=BlockType.TITLE, text="短文本", page_idx=0, text_level=1, source_file="book.pdf"),
        ContentBlock(type=BlockType.TEXT, text="太短", page_idx=0, source_file="book.pdf"),
    ]

    monkeypatch.setenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered")
    chunks = get_strategy("hierarchical", child_max_chars=1000).chunk(blocks)

    assert [chunk.layer for chunk in chunks] == ["parent", "child"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


class _FakeChatMessage:
    content = '{"summary":"cached client response","questions":[]}'


class _FakeChatChoice:
    message = _FakeChatMessage()


class _FakeChatUsage:
    total_tokens = 9


class _FakeChatResponse:
    choices = [_FakeChatChoice()]
    usage = _FakeChatUsage()


class _FakeCompletions:
    def create(self, **_kwargs):
        return _FakeChatResponse()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = _FakeChat()


def _clear_hierarchical_client_cache() -> None:
    if hasattr(hierarchical_module._CLIENT_LOCAL, "openai_clients"):
        delattr(hierarchical_module._CLIENT_LOCAL, "openai_clients")


def test_hierarchical_reuses_openai_client_per_thread(monkeypatch):
    _clear_hierarchical_client_cache()
    monkeypatch.setenv("HIERARCHICAL_REUSE_LLM_CLIENTS", "true")
    create_calls = []

    def fake_create_openai_client(**kwargs):
        create_calls.append(kwargs)
        return _FakeOpenAIClient()

    with patch("core.chunker.hierarchical.create_openai_client", side_effect=fake_create_openai_client):
        first = hierarchical_module._generate_enhanced_text(
            "A" * 80,
            "Title",
            "model-a",
            "https://reuse.test/v1",
            "key-reuse-test",
        )
        second = hierarchical_module._generate_enhanced_text(
            "B" * 80,
            "Title",
            "model-a",
            "https://reuse.test/v1",
            "key-reuse-test",
        )

    assert first[0] == second[0] == '{"summary":"cached client response","questions":[]}'
    assert len(create_calls) == 1


def test_hierarchical_can_disable_openai_client_reuse(monkeypatch):
    _clear_hierarchical_client_cache()
    monkeypatch.setenv("HIERARCHICAL_REUSE_LLM_CLIENTS", "false")
    create_calls = []

    def fake_create_openai_client(**kwargs):
        create_calls.append(kwargs)
        return _FakeOpenAIClient()

    with patch("core.chunker.hierarchical.create_openai_client", side_effect=fake_create_openai_client):
        hierarchical_module._generate_enhanced_text(
            "A" * 80,
            "Title",
            "model-a",
            "https://no-reuse.test/v1",
            "key-no-reuse-test",
        )
        hierarchical_module._generate_enhanced_text(
            "B" * 80,
            "Title",
            "model-a",
            "https://no-reuse.test/v1",
            "key-no-reuse-test",
        )

    assert len(create_calls) == 2


def _make_chunk(
    content: str,
    chunk_index: int,
    layer: str = "child",
    is_image_chunk: bool = False,
    is_table_chunk: bool = False,
    parent_id: str | None = None,
) -> Chunk:
    return Chunk(
        content=content,
        source="test.pdf",
        page=0,
        chunk_index=chunk_index,
        strategy="hierarchical",
        layer=layer,
        is_image_chunk=is_image_chunk,
        is_table_chunk=is_table_chunk,
        parent_id=parent_id,
    )


def test_linker_links_numbered_figure_reference_to_image_chunk():
    text_chunk = _make_chunk("这些都直接关系检验结果的正确性(如图1-3-3-6)。", 0)
    img_chunk = _make_chunk("图1-3-3-6 血液检查结果判读示意图", 1, is_image_chunk=True)

    result = link_related_chunks([text_chunk, img_chunk])

    relation = result[0].relations[0]
    assert relation.rel_type == "refers_to"
    assert relation.target_id == img_chunk.id
    assert relation.evidence == "如图1-3-3-6"


def test_linker_links_numbered_table_reference_to_table_chunk():
    text_chunk = _make_chunk("表1-3-3-1列出了常用血液检查项目。", 0)
    table_chunk = _make_chunk("表1-3-3-1 常用血液检查项目汇总表格", 1, is_table_chunk=True)

    result = link_related_chunks([text_chunk, table_chunk])

    assert table_chunk.id in result[0].related_ids
    assert result[0].relations[0].rel_type == "refers_to"
    assert result[0].relations[0].evidence == "表1-3-3-1"


def test_linker_adjacent_relation_for_unreferenced_media():
    text_chunk = _make_chunk("这是一段普通说明文字。", 0)
    img_chunk = _make_chunk("插图说明", 1, is_image_chunk=True)

    result = link_related_chunks([text_chunk, img_chunk])

    assert text_chunk.id in result[1].related_ids
    assert any(relation.rel_type == "adjacent" for relation in result[1].relations)


def test_linker_same_parent_relation():
    text_chunk = _make_chunk("正文内容", 0, parent_id="parent-1")
    table_chunk = _make_chunk("表1-1 数据表", 1, is_table_chunk=True, parent_id="parent-1")

    result = link_related_chunks([text_chunk, table_chunk])

    assert any(relation.rel_type == "sibling" for relation in result[0].relations + result[1].relations)


def test_linker_large_same_parent_keeps_relation_count_stable():
    parent_id = "parent-large"
    text_chunks = [
        _make_chunk(f"正文内容 {idx}", idx, parent_id=parent_id)
        for idx in range(20)
    ]
    media_chunks = [
        _make_chunk(f"插图说明 {idx}", 20 + idx, is_image_chunk=True, parent_id=parent_id)
        for idx in range(3)
    ]

    result = link_related_chunks(text_chunks + media_chunks)

    for media in result[20:]:
        sibling_targets = [relation.target_id for relation in media.relations if relation.rel_type == "sibling"]
        assert len(sibling_targets) == len(text_chunks)
        assert len(sibling_targets) == len(set(sibling_targets))


def test_linker_enhanced_inherits_child_relations():
    child_chunk = _make_chunk("如图1-1所示，该方法有效。", 0, parent_id="parent-1")
    img_chunk = _make_chunk("图1-1 方法示意图", 1, is_image_chunk=True, parent_id="parent-1")
    enhanced_chunk = _make_chunk("该方法的增强摘要。", 2, layer="enhanced", parent_id=child_chunk.id)

    result = link_related_chunks([child_chunk, img_chunk, enhanced_chunk])

    assert img_chunk.id in result[0].related_ids
    assert img_chunk.id in result[2].related_ids
