from __future__ import annotations

from core.chunker.relation_utils import add_relation
from core.models.content_block import Chunk

_CAUSAL_MARKERS = ("因为", "由于", "鉴于", "导致", "使得", "引起", "造成", "所以", "因此")


def link_causal(chunks: list[Chunk]) -> int:
    added = 0
    child_chunks = [chunk for chunk in chunks if chunk.layer == "child"]
    for idx, current in enumerate(child_chunks):
        if not any(marker in current.content[:200] for marker in _CAUSAL_MARKERS):
            continue
        if idx == 0:
            continue
        previous = child_chunks[idx - 1]
        if current.parent_id and previous.parent_id and current.parent_id != previous.parent_id:
            continue
        before = len(previous.relations)
        add_relation(previous, current.id, "cause_of", weight=0.7, source="rule", evidence=current.content[:20])
        add_relation(current, previous.id, "effect_of", weight=0.7, source="rule", evidence=previous.content[:20])
        if len(previous.relations) > before:
            added += 1
    return added
