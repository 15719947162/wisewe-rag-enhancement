from __future__ import annotations

from core.models.content_block import Chunk
from core.models.relation import Relation, RelSource, RelType

_WEIGHT_EPSILON = 1e-9


def has_relation(chunk: Chunk, target_id: str, rel_type: RelType | None = None) -> bool:
    return any(
        relation.target_id == target_id and (rel_type is None or relation.rel_type == rel_type)
        for relation in chunk.relations
    )


def add_relation(
    chunk: Chunk,
    target_id: str,
    rel_type: RelType,
    weight: float = 1.0,
    source: RelSource = "rule",
    evidence: str = "",
) -> None:
    if has_relation(chunk, target_id, rel_type):
        return
    chunk.relations.append(
        Relation(
            target_id=target_id,
            rel_type=rel_type,
            weight=_normalize_weight(weight),
            source=source,
            evidence=evidence[:20],
        )
    )


def add_bidirectional_relation(
    src: Chunk,
    dst: Chunk,
    rel_type: RelType,
    weight: float = 1.0,
    source: RelSource = "rule",
    evidence: str = "",
) -> None:
    add_relation(src, dst.id, rel_type, weight=weight, source=source, evidence=evidence)
    add_relation(dst, src.id, rel_type, weight=weight, source=source, evidence=evidence)


def filter_by_type(relations: list[Relation], types: set[RelType]) -> list[Relation]:
    return [relation for relation in relations if relation.rel_type in types]


def _normalize_weight(weight: float) -> float:
    if 1.0 < weight <= 1.0 + _WEIGHT_EPSILON:
        return 1.0
    if -_WEIGHT_EPSILON <= weight < 0.0:
        return 0.0
    return weight
