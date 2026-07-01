from __future__ import annotations

import json
from typing import Any

from core.db.connection import get_db_connection

_RRF_K = 60


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _normalize_related_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        if parsed:
            return [str(parsed)]
    return []


def snapshot_row_to_candidate(row: dict[str, Any] | tuple[Any, ...], columns: list[str] | None = None) -> dict[str, Any]:
    data = dict(zip(columns or [], row)) if not isinstance(row, dict) else row
    dense_rank = data.get("dense_rank")
    sparse_rank = data.get("sparse_rank")
    dense_score = float(data.get("dense_score", 0.0) or 0.0)
    sparse_score = float(data.get("sparse_score", 0.0) or 0.0)
    sources = [item for item in (data.get("sources") or []) if item]
    if not sources:
        if dense_rank is not None:
            sources.append("embedding")
        if sparse_rank is not None:
            sources.append("bm25")

    rrf_score = 0.0
    if dense_rank is not None:
        rrf_score += 1.0 / (_RRF_K + int(dense_rank))
    if sparse_rank is not None:
        rrf_score += 1.0 / (_RRF_K + int(sparse_rank))
    # The non-snapshot path keeps the original dense/BM25 relevance as the
    # candidate score and uses RRF only as a tiny ordering bonus. Snapshot mode
    # must keep that scale, otherwise the default min_score=0.3 filters out real
    # dense hits (~0.7) and leaves only related expansion rows scored at 0.3.
    relevance_score = max(dense_score, min(max(sparse_score, 0.0), 1.0))
    score = min(relevance_score + rrf_score, 1.0) if relevance_score > 0 else rrf_score

    candidate = {
        "id": str(data.get("id", "")),
        "content": data.get("content", "") or "",
        "source": data.get("source", "") or "",
        "document_id": str(data["document_id"]) if data.get("document_id") else "",
        "document_name": data.get("document_name", "") or data.get("filename", "") or data.get("source", "") or "",
        "page": int(data.get("page", 0) or 0),
        "chunk_index": int(data.get("chunk_index", 0) or 0),
        "layer": data.get("layer", "") or "",
        "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
        "related_ids": _normalize_related_ids(data.get("related_ids")),
        "title": data.get("title") or "",
        "is_table_chunk": bool(data.get("is_table_chunk", False)),
        "is_image_chunk": bool(data.get("is_image_chunk", False)),
        "image_path": data.get("image_path", "") or None,
        "score": score,
        "rrf_score": rrf_score,
        "dense_score": dense_score,
        "sparse_score": sparse_score,
        "sources": sources,
        "matched_by": [],
        "_snapshot_role": data.get("snapshot_role", "base") or "base",
        "_dense_rank": int(dense_rank) if dense_rank is not None else None,
        "_sparse_rank": int(sparse_rank) if sparse_rank is not None else None,
    }
    return candidate


def fetch_retrieval_snapshot(
    query: str,
    query_vec: list[float],
    kb_id: str,
    dense_limit: int = 50,
    sparse_limit: int = 50,
    related_limit: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Fetch dense, sparse, fold, and related candidates with one database query."""
    vector_literal = _vector_literal(query_vec)
    like_query = f"%{query}%"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH dense AS (
                    SELECT c.id::text AS id,
                           1 - (c.embedding <=> %s::vector) AS dense_score,
                           row_number() OVER (ORDER BY c.embedding <=> %s::vector) AS dense_rank
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND c.layer = ANY(%s)
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                ),
                sparse_source AS (
                    SELECT c.id::text AS id,
                           GREATEST(
                               COALESCE(ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)), 0),
                               CASE
                                   WHEN COALESCE(c.search_text, c.title, c.content, '') ILIKE %s THEN 0.5
                                   ELSE 0
                               END
                           ) AS sparse_score
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND c.layer = ANY(%s)
                      AND (
                          c.search_vector @@ plainto_tsquery('simple', %s)
                          OR COALESCE(c.search_text, c.title, c.content, '') ILIKE %s
                      )
                ),
                sparse AS (
                    SELECT id,
                           sparse_score,
                           row_number() OVER (ORDER BY sparse_score DESC, id) AS sparse_rank
                    FROM sparse_source
                    WHERE sparse_score > 0
                    ORDER BY sparse_score DESC, id
                    LIMIT %s
                ),
                base AS (
                    SELECT COALESCE(d.id, s.id) AS id,
                           d.dense_score,
                           d.dense_rank,
                           s.sparse_score,
                           s.sparse_rank
                    FROM dense d
                    FULL OUTER JOIN sparse s ON s.id = d.id
                ),
                base_chunks AS (
                    SELECT c.*
                    FROM chunks c
                    JOIN base b ON b.id = c.id::text
                    WHERE c.kb_id = %s
                ),
                fold_ids AS (
                    SELECT DISTINCT parent_id::text AS id
                    FROM base_chunks
                    WHERE layer = 'enhanced' AND parent_id IS NOT NULL
                ),
                fold_base_chunks AS (
                    SELECT c.*
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND (
                          c.id::text IN (SELECT id FROM base)
                          OR c.id::text IN (SELECT id FROM fold_ids)
                      )
                ),
                related_ids AS (
                    SELECT DISTINCT value AS id
                    FROM fold_base_chunks c
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        CASE
                            WHEN c.related_ids IS NULL OR btrim(c.related_ids) = '' THEN '[]'::jsonb
                            ELSE c.related_ids::jsonb
                        END
                    ) value
                    LIMIT %s
                ),
                needed_ids AS (
                    SELECT id, 'base' AS snapshot_role FROM base
                    UNION
                    SELECT id, 'fold' AS snapshot_role FROM fold_ids
                    UNION
                    SELECT id, 'related' AS snapshot_role FROM related_ids
                )
                SELECT c.id::text AS id,
                       c.content,
                       c.source,
                       c.document_id::text AS document_id,
                       d.filename AS document_name,
                       c.page,
                       c.layer,
                       c.parent_id::text AS parent_id,
                       c.related_ids,
                       c.chunk_index,
                       c.title,
                       c.is_table_chunk,
                       c.is_image_chunk,
                       c.image_path,
                       b.dense_score,
                       b.dense_rank,
                       b.sparse_score,
                       b.sparse_rank,
                       ARRAY_REMOVE(ARRAY[
                           CASE WHEN b.dense_rank IS NOT NULL THEN 'embedding' END,
                           CASE WHEN b.sparse_rank IS NOT NULL THEN 'bm25' END
                       ], NULL) AS sources,
                       n.snapshot_role
                FROM needed_ids n
                JOIN chunks c ON c.id::text = n.id AND c.kb_id = %s
                LEFT JOIN documents d ON d.id = c.document_id
                LEFT JOIN base b ON b.id = c.id::text
                """,
                (
                    vector_literal,
                    vector_literal,
                    kb_id,
                    ["child", "enhanced"],
                    vector_literal,
                    dense_limit,
                    query,
                    like_query,
                    kb_id,
                    ["child", "enhanced"],
                    query,
                    like_query,
                    sparse_limit,
                    kb_id,
                    kb_id,
                    related_limit,
                    kb_id,
                ),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    snapshot_by_id: dict[str, dict[str, Any]] = {}
    base_candidates: list[dict[str, Any]] = []
    for row in rows:
        candidate = snapshot_row_to_candidate(row, columns)
        snapshot_by_id[candidate["id"]] = candidate
        if candidate.get("_snapshot_role") == "base":
            base_candidates.append(candidate)

    base_candidates.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return base_candidates, snapshot_by_id


def _score_for_folded_child(candidate: dict[str, Any]) -> float:
    score = float(candidate.get("score", 0.0) or 0.0)
    if candidate.get("layer") != "enhanced":
        return score
    content = candidate.get("content", "") or ""
    if "[图像描述]" in content or "[表格摘要]" in content:
        return score * 0.95
    return score * 0.85


def fold_enhanced_snapshot(candidates: list[dict[str, Any]], snapshot_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    folded: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        current = dict(candidate)
        folded_score = _score_for_folded_child(candidate)
        matched_by = list(current.get("matched_by", []))

        if candidate.get("layer") == "enhanced" and candidate.get("parent_id"):
            child = snapshot_by_id.get(str(candidate["parent_id"]))
            if not child:
                continue
            current = dict(child)
            current["score"] = folded_score
            current["dense_score"] = float(candidate.get("dense_score", 0.0) or 0.0)
            current["sparse_score"] = float(candidate.get("sparse_score", 0.0) or 0.0)
            current["sources"] = list(candidate.get("sources", []))
            current["matched_enhanced_id"] = candidate.get("id", "")
            current["matched_enhanced_text"] = candidate.get("content", "")
            matched_by.append("enhanced")
        else:
            current["score"] = folded_score
            matched_by.append(str(candidate.get("layer") or "chunk"))

        current["matched_by"] = sorted(set(item for item in matched_by if item))
        existing = folded.get(current["id"])
        if not existing or float(current.get("score", 0.0)) > float(existing.get("score", 0.0)):
            folded[current["id"]] = current
            continue
        existing["score"] = max(float(existing.get("score", 0.0)), float(current.get("score", 0.0)))
        existing["dense_score"] = max(float(existing.get("dense_score", 0.0)), float(current.get("dense_score", 0.0)))
        existing["sparse_score"] = max(float(existing.get("sparse_score", 0.0)), float(current.get("sparse_score", 0.0)))
        existing["sources"] = sorted(set(existing.get("sources", []) + current.get("sources", [])))
        existing["matched_by"] = sorted(set(existing.get("matched_by", []) + current.get("matched_by", [])))

    return sorted(folded.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def expand_related_snapshot(
    candidates: list[dict[str, Any]],
    snapshot_by_id: dict[str, dict[str, Any]],
    related_score: float = 0.24,
) -> list[dict[str, Any]]:
    expanded = list(candidates)
    existing_ids = {candidate["id"] for candidate in candidates}
    related_image_pages: set[tuple[str, int]] = set()
    for candidate in candidates:
        for related_id in candidate.get("related_ids", []):
            related = snapshot_by_id.get(str(related_id))
            if not related or related["id"] in existing_ids:
                continue
            current = dict(related)
            is_related_image = bool(current.get("is_image_chunk", False))
            if is_related_image:
                image_page_key = (str(current.get("document_id", "") or current.get("source", "")), int(current.get("page", 0) or 0))
                if image_page_key in related_image_pages:
                    continue
                related_image_pages.add(image_page_key)
            inherited_factor = 0.45 if is_related_image else 0.85
            inherited_score = float(candidate.get("score", 0.0) or 0.0) * inherited_factor
            current["score"] = max(float(current.get("score", 0.0) or 0.0), related_score, inherited_score)
            current["dense_score"] = float(current.get("dense_score", 0.0) or 0.0)
            current["matched_by"] = sorted(set(current.get("matched_by", []) + ["related"]))
            current["sources"] = sorted(set(current.get("sources", []) + ["related"]))
            expanded.append(current)
            existing_ids.add(current["id"])
    return expanded
