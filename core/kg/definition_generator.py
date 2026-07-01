from __future__ import annotations

from core.models.content_block import Chunk
from core.models.entity import Entity


def generate_definition(entity: Entity, chunk_map: dict[str, Chunk]) -> str:
    for chunk_id in entity.source_chunks:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        snippet = _extract_definition_snippet(entity.name, chunk.content)
        if snippet:
            return snippet
    return entity.name


def _extract_definition_snippet(name: str, content: str) -> str:
    text = " ".join(content.strip().split())
    if not text:
        return ""

    sentence = text.split("。", 1)[0].split("；", 1)[0].split("\n", 1)[0].strip()
    if not sentence:
        return ""

    if name in sentence:
        return sentence[:120]

    return f"{name}：{sentence[:100]}"
