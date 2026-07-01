from __future__ import annotations

import math
import os

from core.chunker.relation_utils import add_bidirectional_relation
from core.models.content_block import Chunk

DEFAULT_THRESHOLD = 0.85
DEFAULT_TOPK = 10
DEFAULT_DUP_THRESHOLD = 0.95
DEFAULT_SKIP_SAME_PARENT = True
DEFAULT_ENABLED = True
DEFAULT_BLOCK_SIZE = 256


def _cosine(a: list[float], b: list[float]) -> float:
    denom_a = math.sqrt(sum(x * x for x in a)) or 1.0
    denom_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (denom_a * denom_b)


def link_semantic(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    topk: int = DEFAULT_TOPK,
    dup_threshold: float = DEFAULT_DUP_THRESHOLD,
    skip_same_parent: bool = DEFAULT_SKIP_SAME_PARENT,
    enabled: bool | None = None,
) -> int:
    if enabled is None:
        enabled = os.getenv("LINKER_SEMANTIC_ENABLED", "true").lower() not in {"0", "false", "off"}
    if not enabled:
        return 0
    if len(embeddings) != len(chunks):
        raise ValueError("embeddings length must match chunks length")
    if topk <= 0:
        return 0

    child_pairs = [(idx, chunk) for idx, chunk in enumerate(chunks) if chunk.layer == "child"]
    if len(child_pairs) < 2:
        return 0

    numpy_added = _link_semantic_numpy(
        child_pairs,
        embeddings,
        threshold=threshold,
        topk=topk,
        dup_threshold=dup_threshold,
        skip_same_parent=skip_same_parent,
    )
    if numpy_added is not None:
        return numpy_added

    return _link_semantic_python(
        child_pairs,
        embeddings,
        threshold=threshold,
        topk=topk,
        dup_threshold=dup_threshold,
        skip_same_parent=skip_same_parent,
    )


def _link_semantic_python(
    child_pairs: list[tuple[int, Chunk]],
    embeddings: list[list[float]],
    *,
    threshold: float,
    topk: int,
    dup_threshold: float,
    skip_same_parent: bool,
) -> int:
    norms = {
        global_idx: math.sqrt(sum(value * value for value in embeddings[global_idx])) or 1.0
        for global_idx, _chunk in child_pairs
    }
    candidates: list[list[tuple[float, int]]] = [[] for _global_idx, _chunk in child_pairs]

    for pos_i, (global_i, chunk_i) in enumerate(child_pairs[:-1]):
        emb_i = embeddings[global_i]
        norm_i = norms[global_i]
        for pos_j in range(pos_i + 1, len(child_pairs)):
            global_j, chunk_j = child_pairs[pos_j]
            if skip_same_parent and chunk_i.parent_id and chunk_i.parent_id == chunk_j.parent_id:
                continue
            score = sum(x * y for x, y in zip(emb_i, embeddings[global_j])) / (norm_i * norms[global_j])
            if score >= threshold:
                candidates[pos_i].append((score, pos_j))
                candidates[pos_j].append((score, pos_i))

    return _add_top_semantic_relations(
        child_pairs,
        candidates,
        topk=topk,
        dup_threshold=dup_threshold,
    )


def _link_semantic_numpy(
    child_pairs: list[tuple[int, Chunk]],
    embeddings: list[list[float]],
    *,
    threshold: float,
    topk: int,
    dup_threshold: float,
    skip_same_parent: bool,
) -> int | None:
    if os.getenv("LINKER_SEMANTIC_NUMPY_ENABLED", "true").lower() in {"0", "false", "off"}:
        return None
    try:
        import numpy as np
    except Exception:
        return None

    try:
        matrix = np.asarray([embeddings[global_idx] for global_idx, _chunk in child_pairs], dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if matrix.ndim != 2:
        return None

    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    matrix = matrix / norms[:, None]

    parent_positions: dict[str, list[int]] = {}
    if skip_same_parent:
        for pos, (_global_idx, chunk) in enumerate(child_pairs):
            if chunk.parent_id:
                parent_positions.setdefault(chunk.parent_id, []).append(pos)

    block_size = _semantic_block_size()
    added = 0
    for start in range(0, len(child_pairs), block_size):
        stop = min(start + block_size, len(child_pairs))
        similarities = matrix[start:stop] @ matrix.T
        for offset, scores in enumerate(similarities):
            pos_i = start + offset
            chunk_i = child_pairs[pos_i][1]
            mask = scores >= threshold
            mask[pos_i] = False
            if skip_same_parent and chunk_i.parent_id:
                mask[parent_positions.get(chunk_i.parent_id, [])] = False
            candidate_positions = np.flatnonzero(mask)
            if candidate_positions.size == 0:
                continue
            candidate_scores = scores[candidate_positions]
            order = np.argsort(-candidate_scores, kind="stable")[:topk]
            added += _add_semantic_relations_for_positions(
                child_pairs,
                pos_i,
                [(float(candidate_scores[item]), int(candidate_positions[item])) for item in order],
                dup_threshold=dup_threshold,
            )
    return added


def _add_top_semantic_relations(
    child_pairs: list[tuple[int, Chunk]],
    candidates: list[list[tuple[float, int]]],
    *,
    topk: int,
    dup_threshold: float,
) -> int:
    added = 0
    for pos_i, scored in enumerate(candidates):
        scored.sort(key=lambda item: item[0], reverse=True)
        added += _add_semantic_relations_for_positions(
            child_pairs,
            pos_i,
            scored[:topk],
            dup_threshold=dup_threshold,
        )
    return added


def _add_semantic_relations_for_positions(
    child_pairs: list[tuple[int, Chunk]],
    pos_i: int,
    scored: list[tuple[float, int]],
    *,
    dup_threshold: float,
) -> int:
    chunk_i = child_pairs[pos_i][1]
    added = 0
    for score, pos_j in scored:
        chunk_j = child_pairs[pos_j][1]
        rel_type = "duplicate_of" if score >= dup_threshold else "semantic_similar"
        before = len(chunk_i.relations)
        add_bidirectional_relation(
            chunk_i,
            chunk_j,
            rel_type=rel_type,
            weight=score,
            source="embedding",
            evidence=f"cos={score:.3f}",
        )
        if len(chunk_i.relations) > before:
            added += 1
    return added


def _semantic_block_size() -> int:
    raw = os.getenv("LINKER_SEMANTIC_BLOCK_SIZE", str(DEFAULT_BLOCK_SIZE))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BLOCK_SIZE
    return max(1, value)
