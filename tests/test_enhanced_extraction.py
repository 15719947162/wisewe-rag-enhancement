from core.chunker.hierarchical import HierarchicalStrategy
from core.models.content_block import Chunk


def _make_child(content: str, *, is_image: bool = False, is_table: bool = False) -> Chunk:
    return Chunk(
        content=content,
        source="test",
        page=0,
        chunk_index=0,
        strategy="hierarchical",
        title="测试章节",
        layer="child",
        is_image_chunk=is_image,
        is_table_chunk=is_table,
        image_path=None,
    )


def test_build_enhanced_chunk_parses_entities_and_triples():
    strategy = HierarchicalStrategy(enable_enhanced=True)
    child = _make_child("应急预案是核心制度，编制流程包括评估与审批。")
    raw = '{"summary":"应急预案编制要点","questions":["应急预案如何编制？"],"entities":[{"name":"应急预案","type":"Concept","aliases":[]}],"triples":[{"s":"应急预案","p":"包括","o":"编制流程","confidence":0.9}]}'
    enhanced = strategy._build_enhanced_chunk(child, 1, raw, 42, "", "[LLM增强]")
    assert enhanced is not None
    assert enhanced.enhanced_text == "应急预案编制要点"
    assert len(enhanced.extracted_entities) == 1
    assert len(enhanced.extracted_triples) == 1
    assert enhanced.token_cost == 42


def test_build_enhanced_chunk_fallback_keeps_plain_text_summary():
    strategy = HierarchicalStrategy(enable_enhanced=True)
    child = _make_child("片段内容")
    enhanced = strategy._build_enhanced_chunk(child, 1, "not json", 10, "", "[片段增强]")
    assert enhanced is not None
    assert enhanced.enhanced_text == "not json"
    assert enhanced.extracted_entities == []
    assert enhanced.extracted_triples == []


def test_build_enhanced_chunk_handles_image_prompt_output():
    strategy = HierarchicalStrategy(enable_enhanced=True)
    child = _make_child("图片说明", is_image=True)
    raw = '{"summary":"图片展示流程结构","questions":["图中展示了什么流程？"],"entities":[{"name":"流程结构","type":"Procedure","aliases":[]}],"triples":[{"s":"图片","p":"展示","o":"流程结构","confidence":0.8}]}'
    enhanced = strategy._build_enhanced_chunk(child, 1, raw, 18, "", "[图片描述]")
    assert enhanced is not None
    assert enhanced.content.startswith("[图片描述]")
    assert enhanced.extracted_entities[0].name == "流程结构"


def test_build_enhanced_chunk_handles_table_prompt_output():
    strategy = HierarchicalStrategy(enable_enhanced=True)
    child = _make_child("表格内容", is_table=True)
    raw = '{"summary":"表格总结了关键指标","questions":["表格中的关键指标是什么？"],"entities":[{"name":"关键指标","type":"Quantity","aliases":[]}],"triples":[{"s":"表格","p":"总结","o":"关键指标","confidence":0.85}]}'
    enhanced = strategy._build_enhanced_chunk(child, 1, raw, 20, "", "[表格摘要]")
    assert enhanced is not None
    assert enhanced.extracted_triples[0].o == "关键指标"
