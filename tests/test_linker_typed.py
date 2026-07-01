from core.chunker.linker import link_related_chunks
from core.models.content_block import Chunk


def _make_chunk(content: str, chunk_index: int, layer: str = "child", is_image_chunk: bool = False, is_table_chunk: bool = False, parent_id: str | None = None) -> Chunk:
    return Chunk(
        content=content,
        source="test",
        page=0,
        chunk_index=chunk_index,
        strategy="hierarchical",
        layer=layer,
        is_image_chunk=is_image_chunk,
        is_table_chunk=is_table_chunk,
        parent_id=parent_id,
    )


def test_refers_to_relation_type():
    text_chunk = _make_chunk("如图1-2所示", 0)
    img_chunk = _make_chunk("图1-2 示意图", 1, is_image_chunk=True)
    result = link_related_chunks([text_chunk, img_chunk])
    relation = result[0].relations[0]
    assert relation.rel_type == "refers_to"
    assert relation.target_id == img_chunk.id


def test_adjacent_relation_type_for_nearest_text():
    text_chunk = _make_chunk("正文内容", 0)
    img_chunk = _make_chunk("插图说明", 1, is_image_chunk=True)
    result = link_related_chunks([text_chunk, img_chunk])
    assert any(relation.rel_type == "adjacent" for relation in result[1].relations)


def test_sibling_relation_type_for_same_parent():
    text_chunk = _make_chunk("正文内容", 0, parent_id="parent-1")
    img_chunk = _make_chunk("图1-2 示意图", 1, is_image_chunk=True, parent_id="parent-1")
    result = link_related_chunks([text_chunk, img_chunk])
    assert any(relation.rel_type == "sibling" for relation in result[0].relations + result[1].relations)


def test_enhanced_chunk_inherits_parent_relations():
    text_chunk = _make_chunk("如图1-2所示", 0, parent_id="parent-1")
    img_chunk = _make_chunk("图1-2 示意图", 1, is_image_chunk=True, parent_id="parent-1")
    enhanced_chunk = _make_chunk("增强摘要", 2, layer="enhanced", parent_id=text_chunk.id)
    result = link_related_chunks([text_chunk, img_chunk, enhanced_chunk])
    assert result[2].related_ids
    assert img_chunk.id in result[2].related_ids
