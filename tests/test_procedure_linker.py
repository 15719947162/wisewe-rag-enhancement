from core.chunker.procedure_linker import detect_procedure_chunks, link_procedure
from core.models.content_block import Chunk


def _chunk(content: str, idx: int, parent_id: str) -> Chunk:
    return Chunk(content=content, source="s", page=0, chunk_index=idx, strategy="hierarchical", layer="child", parent_id=parent_id)


def test_link_procedure_orders_chain():
    chunks = [
        _chunk("第一步 准备", 0, "p1"),
        _chunk("第二步 执行", 1, "p1"),
        _chunk("第三步 完成", 2, "p1"),
    ]
    detect_procedure_chunks(chunks)
    added = link_procedure(chunks)
    assert added == 2
    assert any(rel.rel_type == "next_step" for rel in chunks[0].relations)
    assert any(rel.rel_type == "prev_step" and rel.target_id == chunks[0].id for rel in chunks[1].relations)
