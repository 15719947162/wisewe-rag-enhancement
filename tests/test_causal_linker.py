from core.chunker.causal_linker import link_causal
from core.models.content_block import Chunk


def test_link_causal_prev_to_current():
    chunks = [
        Chunk(content="前置条件", source="s", page=0, chunk_index=0, strategy="hierarchical", layer="child", parent_id="p"),
        Chunk(content="因为温度变化导致压力上升", source="s", page=0, chunk_index=1, strategy="hierarchical", layer="child", parent_id="p"),
    ]
    added = link_causal(chunks)
    assert added == 1
    assert any(rel.rel_type == "cause_of" for rel in chunks[0].relations)
    assert any(rel.rel_type == "effect_of" and rel.target_id == chunks[0].id for rel in chunks[1].relations)
