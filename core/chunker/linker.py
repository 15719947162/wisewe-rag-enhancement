"""Post-processing: link related chunks for co-retrieval.

Rules are intentionally local and auditable:
1. Reference matching: text mentioning numbered figures/tables links to the matching media chunk.
2. Adjacency: image/table chunks link to the nearest preceding text chunk.
3. Same-parent: under the same parent_id, all image/table chunks link to all text chunks.
4. Enhanced inheritance: enhanced chunks inherit relations from their parent child chunk.
"""
from __future__ import annotations

import re

from core.models.content_block import Chunk
from core.models.relation import RelSource, RelType, Relation

_REF_NUMBER = r"([0-9０-９]+(?:\s*[-－–—]\s*[0-9０-９]+){0,6})"
_FIG_REF = re.compile(rf"(?:如\s*)?图\s*{_REF_NUMBER}")
_TABLE_REF = re.compile(rf"(?:如\s*)?表\s*{_REF_NUMBER}")
_FIG_LABEL = re.compile(rf"图\s*{_REF_NUMBER}")
_TABLE_LABEL = re.compile(rf"表\s*{_REF_NUMBER}")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_ref(ref: str) -> str:
    normalized = ref.translate(_FULLWIDTH_DIGITS)
    return re.sub(r"[\s\-－–—]", "", normalized)


def _extract_refs(pattern: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for match in pattern.finditer(text or ""):
        refs.append((_normalize_ref(match.group(1)), match.group(0)))
    return refs


def link_related_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Build typed bidirectional relations between text, image, and table chunks."""
    if not chunks:
        return chunks

    img_chunks = [(i, c) for i, c in enumerate(chunks) if c.is_image_chunk]
    table_chunks = [(i, c) for i, c in enumerate(chunks) if c.is_table_chunk]
    text_chunks = [
        (i, c)
        for i, c in enumerate(chunks)
        if not c.is_image_chunk and not c.is_table_chunk and c.layer not in ("parent", "enhanced")
    ]

    relation_keys: dict[str, set[tuple[str, str]]] = {
        chunk.id: {(relation.target_id, relation.rel_type) for relation in chunk.relations}
        for chunk in chunks
    }

    def _add_relation(
        chunk: Chunk,
        target_id: str,
        rel_type: RelType,
        weight: float = 1.0,
        source: RelSource = "rule",
        evidence: str = "",
    ) -> bool:
        key = (target_id, rel_type)
        keys = relation_keys.setdefault(chunk.id, set())
        if key in keys:
            return False
        chunk.relations.append(
            Relation(
                target_id=target_id,
                rel_type=rel_type,
                weight=weight,
                source=source,
                evidence=evidence[:20],
            )
        )
        keys.add(key)
        return True

    def _link(a_idx: int, b_idx: int, rel_type: RelType, evidence: str) -> None:
        _add_relation(chunks[a_idx], chunks[b_idx].id, rel_type=rel_type, source="rule", evidence=evidence)
        _add_relation(chunks[b_idx], chunks[a_idx].id, rel_type=rel_type, source="rule", evidence=evidence)

    # Rule 1: explicit references such as 图1-3-3-6 / 表1-3-3-1.
    fig_label_map: dict[str, int] = {}
    table_label_map: dict[str, int] = {}

    for idx, chunk in img_chunks:
        for key, _label in _extract_refs(_FIG_LABEL, chunk.content[:300]):
            fig_label_map.setdefault(key, idx)

    for idx, chunk in table_chunks:
        for key, _label in _extract_refs(_TABLE_LABEL, chunk.content[:300]):
            table_label_map.setdefault(key, idx)

    for idx, chunk in text_chunks:
        for key, evidence in _extract_refs(_FIG_REF, chunk.content):
            if key in fig_label_map:
                _link(idx, fig_label_map[key], "refers_to", evidence)
        for key, evidence in _extract_refs(_TABLE_REF, chunk.content):
            if key in table_label_map:
                _link(idx, table_label_map[key], "refers_to", evidence)

    # Rule 2: image/table links to nearest preceding text chunk when no explicit relation exists.
    for idx, chunk in img_chunks + table_chunks:
        if chunk.related_ids:
            continue
        for back in range(idx - 1, max(idx - 5, -1), -1):
            prev = chunks[back]
            if not prev.is_image_chunk and not prev.is_table_chunk and prev.layer not in ("parent", "enhanced"):
                _link(idx, back, "adjacent", "邻近5块内")
                break

    # Rule 3: under same parent_id, link media to text for local co-retrieval.
    if any(chunk.parent_id for chunk in chunks):
        parent_groups: dict[str, list[int]] = {}
        for idx, chunk in enumerate(chunks):
            if chunk.parent_id and chunk.layer == "child":
                parent_groups.setdefault(chunk.parent_id, []).append(idx)

        for group in parent_groups.values():
            media_in_group = [idx for idx in group if chunks[idx].is_image_chunk or chunks[idx].is_table_chunk]
            text_in_group = [idx for idx in group if not chunks[idx].is_image_chunk and not chunks[idx].is_table_chunk]
            for media_idx in media_in_group:
                for text_idx in text_in_group:
                    _link(media_idx, text_idx, "sibling", "同parent_id")

    # Rule 4: enhanced chunks inherit relations from their parent child chunk.
    child_id_to_idx = {chunk.id: idx for idx, chunk in enumerate(chunks) if chunk.layer == "child"}
    for chunk in chunks:
        if chunk.layer != "enhanced" or not chunk.parent_id:
            continue
        parent_idx = child_id_to_idx.get(chunk.parent_id)
        if parent_idx is None:
            continue
        for relation in chunks[parent_idx].relations:
            _add_relation(
                chunk,
                relation.target_id,
                relation.rel_type,
                weight=relation.weight,
                source=relation.source,
                evidence=relation.evidence,
            )

    return chunks
