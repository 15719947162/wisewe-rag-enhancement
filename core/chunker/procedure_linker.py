from __future__ import annotations

import re

from core.chunker.relation_utils import add_relation
from core.models.content_block import Chunk

_ORDER_RE = re.compile(r"^(?:第)?([0-9一二三四五六七八九十]+)(?:步|\.|\))")
_TEMPORAL_HEAD = ("首先", "接着", "然后", "随后", "最后", "紧接着", "之后", "最终", "step")

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_order(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    return None


def detect_procedure_chunks(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        if chunk.layer != "child" or chunk.is_table_chunk or chunk.is_image_chunk:
            continue
        text = chunk.content[:80].strip().lower()
        match = _ORDER_RE.match(text)
        if match:
            chunk.is_procedure_chunk = True
            chunk.procedure_order = _parse_order(match.group(1))
            continue
        if any(text.startswith(head.lower()) for head in _TEMPORAL_HEAD):
            chunk.is_procedure_chunk = True


def link_procedure(chunks: list[Chunk]) -> int:
    added = 0
    groups: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        if chunk.layer == "child" and chunk.parent_id:
            groups.setdefault(chunk.parent_id, []).append(chunk)

    for group in groups.values():
        proc = [chunk for chunk in group if chunk.is_procedure_chunk]
        proc.sort(key=lambda item: (item.procedure_order or 999, item.chunk_index))
        if len(proc) < 2:
            continue
        for prev, curr in zip(proc, proc[1:]):
            before = len(prev.relations)
            add_relation(prev, curr.id, "next_step", source="rule", evidence=curr.content[:20])
            add_relation(curr, prev.id, "prev_step", source="rule", evidence=prev.content[:20])
            if len(prev.relations) > before:
                added += 1
    return added
