from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from typing import Any

from core.db.connection import get_db_connection
from core.embedding.client import embed_query_cached, embed_texts
from core.rag.retrieval_snapshot import (
    expand_related_snapshot,
    fetch_retrieval_snapshot,
    fold_enhanced_snapshot,
)

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    class BM25Okapi:  # pragma: no cover - fallback only used when dependency is absent
        def __init__(self, corpus_tokens: list[list[str]]):
            self.corpus_tokens = corpus_tokens
            self.doc_freqs = [Counter(doc) for doc in corpus_tokens]
            self.doc_lengths = [len(doc) for doc in corpus_tokens]
            self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
            self.df: Counter[str] = Counter()
            for doc in corpus_tokens:
                for token in set(doc):
                    self.df[token] += 1
            self.corpus_size = len(corpus_tokens)
            self.k1 = 1.5
            self.b = 0.75

        def get_scores(self, query_tokens: list[str]) -> list[float]:
            scores: list[float] = []
            for index, freqs in enumerate(self.doc_freqs):
                doc_len = self.doc_lengths[index] or 1
                score = 0.0
                for token in query_tokens:
                    tf = freqs.get(token, 0)
                    if not tf:
                        continue
                    df = self.df.get(token, 0)
                    idf = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)
                    denom = tf + self.k1 * (1 - self.b + self.b * doc_len / (self.avgdl or 1.0))
                    score += idf * (tf * (self.k1 + 1)) / denom
                scores.append(score)
            return scores


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_REF_NUMBER_PATTERN = r"([0-9０-９]+(?:\s*[-－–—]\s*[0-9０-９]+){0,6})"
_MEDIA_REF_QUERY_PATTERN = re.compile(rf"(图|表)\s*{_REF_NUMBER_PATTERN}")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_bm25_cache: dict[str, tuple[BM25Okapi | None, list[dict[str, Any]]]] = {}
_DEFAULT_RETRIEVAL_LAYERS = ("child", "enhanced")


def _snapshot_enabled() -> bool:
    value = os.environ.get("RAG_RETRIEVAL_SNAPSHOT", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall((text or "").lower())


def _normalize_media_ref(kind: str, number: str) -> str:
    normalized_number = number.translate(_FULLWIDTH_DIGITS)
    normalized_number = re.sub(r"[\s\-－–—]", "", normalized_number)
    return f"{kind}{normalized_number}"


def _extract_media_ref_query(query: str) -> tuple[str, str] | None:
    match = _MEDIA_REF_QUERY_PATTERN.search(query or "")
    if not match:
        return None
    kind = match.group(1)
    normalized = _normalize_media_ref(kind, match.group(2))
    if len(normalized) <= 1:
        return None
    return kind, normalized


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


def _row_to_candidate(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    data = dict(zip(columns, row))
    candidate = {
        "id": str(data.get("id", "")),
        "content": data.get("content", "") or "",
        "source": data.get("source", "") or "",
        "document_id": str(data["document_id"]) if data.get("document_id") else "",
        "document_name": data.get("document_name", "") or data.get("filename", "") or data.get("source", "") or "",
        "page": int(data.get("page", 0) or 0),
        "layer": data.get("layer", "") or "",
        "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
        "related_ids": _normalize_related_ids(data.get("related_ids")),
        "score": float(data.get("score", 0.0) or 0.0),
        "dense_score": float(data.get("dense_score", data.get("score", 0.0)) or 0.0),
        "image_path": data.get("image_path", "") or None,
    }
    if "chunk_index" in data:
        candidate["chunk_index"] = int(data.get("chunk_index", 0) or 0)
    if "title" in data:
        candidate["title"] = data.get("title") or ""
    if "is_table_chunk" in data:
        candidate["is_table_chunk"] = bool(data.get("is_table_chunk", False))
    if "is_image_chunk" in data:
        candidate["is_image_chunk"] = bool(data.get("is_image_chunk", False))
    return candidate


def _dense_retrieve(query_vec: list[float], kb_id: str, top_n: int = 50) -> list[dict[str, Any]]:
    conn = get_db_connection()
    vector_literal = _vector_literal(query_vec)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s AND c.layer = ANY(%s)
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (vector_literal, kb_id, list(_DEFAULT_RETRIEVAL_LAYERS), vector_literal, top_n),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    candidates = [_row_to_candidate(columns, row) for row in rows]
    for candidate in candidates:
        candidate["dense_score"] = candidate["score"]
    return candidates


def _build_bm25_index(kb_id: str) -> tuple[BM25Okapi | None, list[dict[str, Any]]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s AND c.layer = ANY(%s)
                ORDER BY c.chunk_index
                """,
                (kb_id, list(_DEFAULT_RETRIEVAL_LAYERS)),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    corpus_docs = [_row_to_candidate(columns, row) for row in rows]
    if not corpus_docs:
        return None, []

    corpus_tokens = [_tokenize(doc["content"]) for doc in corpus_docs]
    return BM25Okapi(corpus_tokens), corpus_docs


def _sparse_retrieve(query: str, kb_id: str, top_n: int = 50) -> list[dict[str, Any]]:
    index, corpus_docs = _bm25_cache.get(kb_id) or _build_bm25_index(kb_id)
    _bm25_cache[kb_id] = (index, corpus_docs)
    if not corpus_docs or index is None:
        return []

    scores = [float(score) for score in index.get_scores(_tokenize(query))]
    if not scores:
        return []
    max_score = max(scores)
    if max_score <= 0:
        return []

    ranked_indices = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:top_n]
    results: list[dict[str, Any]] = []
    for idx in ranked_indices:
        score = scores[idx] / max_score
        if score <= 0:
            continue
        candidate = dict(corpus_docs[idx])
        candidate["score"] = float(score)
        candidate["dense_score"] = 0.0
        results.append(candidate)
    return results


def _structured_retrieve(
    query: str,
    kb_id: str,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    del query
    if not filters:
        return []

    where_clauses = ["c.kb_id = %s"]
    params: list[Any] = [kb_id]
    if filters.get("source"):
        where_clauses.append("(c.source = %s OR d.filename = %s)")
        params.append(filters["source"])
        params.append(filters["source"])
    if filters.get("title_like"):
        where_clauses.append("c.title LIKE %s")
        params.append(f"%{filters['title_like']}%")
    if filters.get("is_table"):
        where_clauses.append("c.is_table_chunk = TRUE")
    if filters.get("page_range") and len(filters["page_range"]) == 2:
        start_page, end_page = filters["page_range"]
        where_clauses.append("c.page BETWEEN %s AND %s")
        params.extend([start_page, end_page])

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE {" AND ".join(where_clauses)}
                  AND c.layer = ANY(%s)
                ORDER BY c.page, c.chunk_index
                """,
                tuple([*params, list(_DEFAULT_RETRIEVAL_LAYERS)]),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    results = [_row_to_candidate(columns, row) for row in rows]
    for candidate in results:
        candidate["score"] = 1.0
        candidate["dense_score"] = 0.0
    return results


def _media_ref_retrieve(query: str, kb_id: str, top_n: int = 20) -> list[dict[str, Any]]:
    media_ref = _extract_media_ref_query(query)
    if media_ref is None:
        return []

    kind, normalized_ref = media_ref
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s
                  AND c.layer = 'child'
                  AND (
                    (%s = '图' AND c.is_image_chunk = TRUE)
                    OR (%s = '表' AND c.is_table_chunk = TRUE)
                  )
                  AND regexp_replace(
                    COALESCE(c.title, '') || ' ' || COALESCE(c.content, ''),
                    '[[:space:]\\-－–—]',
                    '',
                    'g'
                  ) ILIKE %s
                ORDER BY
                  CASE
                    WHEN regexp_replace(COALESCE(c.title, ''), '[[:space:]\\-－–—]', '', 'g') ILIKE %s THEN 0
                    ELSE 1
                  END,
                  c.page,
                  c.chunk_index
                LIMIT %s
                """,
                (kb_id, kind, kind, f"%{normalized_ref}%", f"%{normalized_ref}%", top_n),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    results = [_row_to_candidate(columns, row) for row in rows]
    for index, candidate in enumerate(results):
        candidate["score"] = max(1.0 - index * 0.02, 0.7)
        candidate["dense_score"] = 0.0
        candidate["rerank_score"] = candidate["score"]
        candidate["sources"] = ["media_ref"]
        candidate["matched_by"] = ["media_ref"]
        candidate["retrieval_mode"] = "media_ref"
        candidate["matched_media_ref"] = normalized_ref
    return results


def _fetch_chunks_by_ids(chunk_ids: list[str], kb_id: str) -> list[dict[str, Any]]:
    if not chunk_ids:
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.id::text = ANY(%s) AND c.kb_id = %s
                """,
                (chunk_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    return [_row_to_candidate(columns, row) for row in rows]


def _entity_match_score(query: str, name: str, aliases: list[str], emb_score: float) -> float:
    normalized_query = query.strip().lower()
    query_tokens = set(_tokenize(query))
    best = 0.0

    for candidate in [name, *aliases]:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        if normalized == normalized_query:
            best = max(best, 1.0)
            continue
        if normalized in normalized_query or normalized_query in normalized:
            best = max(best, 0.92)
            continue

        candidate_tokens = set(_tokenize(candidate))
        if query_tokens and candidate_tokens:
            overlap = len(query_tokens & candidate_tokens) / max(len(candidate_tokens), 1)
            if overlap > 0:
                best = max(best, 0.4 + 0.4 * overlap)

    return max(best, emb_score * 0.85)


def _entity_retrieve(
    query: str,
    kb_id: str,
    query_vec: list[float] | None = None,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if query_vec:
                vector_literal = _vector_literal(query_vec)
                cur.execute(
                    """
                    SELECT id::text, name, aliases, type, definition,
                           1 - (embedding <=> %s::vector) AS emb_score
                    FROM entities
                    WHERE kb_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector_literal, kb_id, vector_literal, max(top_n * 2, 20)),
                )
            else:
                cur.execute(
                    """
                    SELECT id::text, name, aliases, type, definition, 0.0 AS emb_score
                    FROM entities
                    WHERE kb_id = %s
                    LIMIT %s
                    """,
                    (kb_id, max(top_n * 2, 20)),
                )
            entity_rows = cur.fetchall()

            ranked_entities: list[tuple[str, float, dict[str, Any]]] = []
            for entity_id, name, aliases, entity_type, definition, emb_score in entity_rows:
                alias_list = aliases if isinstance(aliases, list) else []
                score = _entity_match_score(query, name, alias_list, float(emb_score or 0.0))
                if score < 0.35:
                    continue
                ranked_entities.append(
                    (
                        entity_id,
                        score,
                        {
                            "id": entity_id,
                            "name": name,
                            "aliases": alias_list,
                            "type": entity_type,
                            "definition": definition or name,
                        },
                    )
                )

            ranked_entities.sort(key=lambda item: item[1], reverse=True)
            selected = ranked_entities[:top_n]
            if not selected:
                return []

            entity_ids = [entity_id for entity_id, _score, _entity in selected]
            cur.execute(
                """
                SELECT entity_id::text, chunk_id::text
                FROM entity_mentions
                WHERE kb_id = %s AND entity_id::text = ANY(%s)
                """,
                (kb_id, entity_ids),
            )
            mention_rows = cur.fetchall()
    finally:
        conn.close()

    entity_meta = {entity_id: entity for entity_id, _score, entity in selected}
    entity_scores = {entity_id: score for entity_id, score, _entity in selected}
    chunk_to_entity: dict[str, tuple[float, dict[str, Any]]] = {}
    for entity_id, chunk_id in mention_rows:
        score = entity_scores.get(entity_id, 0.0)
        entity = entity_meta.get(entity_id)
        if not entity:
            continue
        current = chunk_to_entity.get(chunk_id)
        if current is None or score > current[0]:
            chunk_to_entity[chunk_id] = (score, entity)

    chunks = _fetch_chunks_by_ids(list(chunk_to_entity.keys()), kb_id)
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        score, entity = chunk_to_entity.get(chunk["id"], (0.0, None))
        if entity is None:
            continue
        candidate = dict(chunk)
        candidate["score"] = score
        candidate["dense_score"] = 0.0
        candidate["entity"] = entity
        results.append(candidate)
    return sorted(results, key=lambda item: item.get("score", 0.0), reverse=True)[:top_n]


def _rrf_merge(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    structured: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    channels = (
        ("embedding", dense),
        ("bm25", sparse),
        ("entity", structured),
    )
    for channel, candidates in channels:
        for rank, candidate in enumerate(candidates, start=1):
            entry = merged.setdefault(candidate["id"], dict(candidate))
            entry["score"] = float(entry.get("score", 0.0)) + 1.0 / (k + rank)
            entry["dense_score"] = max(
                float(entry.get("dense_score", 0.0) or 0.0),
                float(candidate.get("dense_score", 0.0) or 0.0),
            )
            sources = entry.setdefault("sources", [])
            if channel not in sources:
                sources.append(channel)
    return sorted(merged.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def _score_for_folded_child(candidate: dict[str, Any]) -> float:
    score = float(candidate.get("score", 0.0) or 0.0)
    if candidate.get("layer") != "enhanced":
        return score
    content = candidate.get("content", "") or ""
    if "[图片描述]" in content or "[表格摘要]" in content:
        return score * 0.95
    return score * 0.85


def _fold_enhanced_to_children(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    """Use enhanced chunks for matching, but return their source child as evidence."""
    if not candidates:
        return []

    child_ids = [
        str(candidate["parent_id"])
        for candidate in candidates
        if candidate.get("layer") == "enhanced" and candidate.get("parent_id")
    ]
    child_by_id = {child["id"]: child for child in _fetch_chunks_by_ids(sorted(set(child_ids)), kb_id)}

    folded: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        current = dict(candidate)
        folded_score = _score_for_folded_child(candidate)
        matched_by = list(current.get("matched_by", []))

        if candidate.get("layer") == "enhanced" and candidate.get("parent_id"):
            child = child_by_id.get(str(candidate["parent_id"]))
            if not child:
                continue
            current = dict(child)
            current["score"] = folded_score
            current["dense_score"] = float(candidate.get("dense_score", 0.0) or 0.0)
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
        existing["matched_by"] = sorted(set(existing.get("matched_by", []) + current.get("matched_by", [])))

    return sorted(folded.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def _expand_related(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    if not candidates:
        return []

    existing_ids = {candidate["id"] for candidate in candidates}
    related_ids: list[str] = []
    for candidate in candidates:
        for related_id in candidate.get("related_ids", []):
            if related_id and related_id not in existing_ids and related_id not in related_ids:
                related_ids.append(related_id)
    if not related_ids:
        return candidates

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.id::text = ANY(%s) AND c.kb_id = %s
                """,
                (related_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    expanded = list(candidates)
    for row in rows:
        candidate = _row_to_candidate(columns, row)
        if candidate["id"] in existing_ids:
            continue
        candidate["score"] = 0.3
        candidate["dense_score"] = 0.0
        expanded.append(candidate)
        existing_ids.add(candidate["id"])
    return expanded


def _coarse_filter(
    candidates: list[dict[str, Any]],
    min_score: float = 0.3,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    filtered = [candidate for candidate in candidates if float(candidate.get("score", 0.0)) >= min_score]
    return sorted(filtered, key=lambda item: item.get("score", 0.0), reverse=True)[:top_n]


class HybridRetriever:
    def __init__(self) -> None:
        self.last_timings: dict[str, int | bool] = {}

    def retrieve(
        self,
        query: str,
        kb_id: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        timings: dict[str, int | bool] = {"short_circuit": False}
        started_at = time.perf_counter()

        media_ref_started_at = time.perf_counter()
        media_ref_hits = _media_ref_retrieve(query, kb_id, top_n=top_k)
        timings["media_ref"] = _elapsed_ms(media_ref_started_at)
        if media_ref_hits:
            related_started_at = time.perf_counter()
            expanded = _expand_related(media_ref_hits, kb_id)
            timings["related"] = _elapsed_ms(related_started_at)
            filter_started_at = time.perf_counter()
            result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
            timings["filter"] = _elapsed_ms(filter_started_at)
            timings["total"] = _elapsed_ms(started_at)
            timings["short_circuit"] = True
            self.last_timings = timings
            return result

        embedding_started_at = time.perf_counter()
        query_vec, query_embedding_cache_hit = embed_query_cached(query)
        timings["embedding"] = _elapsed_ms(embedding_started_at)
        timings["query_embedding_cache_hit"] = query_embedding_cache_hit

        if _snapshot_enabled() and not filters:
            try:
                snapshot_started_at = time.perf_counter()
                snapshot_candidates, snapshot_by_id = fetch_retrieval_snapshot(
                    query=query,
                    query_vec=query_vec,
                    kb_id=kb_id,
                    dense_limit=50,
                    sparse_limit=50,
                    related_limit=200,
                )
                timings["snapshot"] = _elapsed_ms(snapshot_started_at)
                timings["dense"] = 0
                timings["sparse"] = 0
                timings["structured"] = 0
                timings["fusion"] = 0
                fold_started_at = time.perf_counter()
                folded = fold_enhanced_snapshot(snapshot_candidates, snapshot_by_id)
                timings["fold"] = _elapsed_ms(fold_started_at)
                related_started_at = time.perf_counter()
                expanded = expand_related_snapshot(folded, snapshot_by_id)
                timings["related"] = _elapsed_ms(related_started_at)
                filter_started_at = time.perf_counter()
                result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
                timings["filter"] = _elapsed_ms(filter_started_at)
                timings["total"] = _elapsed_ms(started_at)
                self.last_timings = timings
                return result
            except Exception as exc:
                timings["snapshot_error"] = True
                timings["snapshot"] = int(timings.get("snapshot", 0) or 0)
                timings["snapshot_fallback"] = True

        dense_started_at = time.perf_counter()
        dense = _dense_retrieve(query_vec, kb_id, 50)
        timings["dense"] = _elapsed_ms(dense_started_at)
        sparse_started_at = time.perf_counter()
        sparse = _sparse_retrieve(query, kb_id, 50)
        timings["sparse"] = _elapsed_ms(sparse_started_at)
        structured_started_at = time.perf_counter()
        structured = _structured_retrieve(query, kb_id, filters)
        timings["structured"] = _elapsed_ms(structured_started_at)
        fusion_started_at = time.perf_counter()
        merged = _rrf_merge(dense, sparse, structured)
        timings["fusion"] = _elapsed_ms(fusion_started_at)
        fold_started_at = time.perf_counter()
        folded = _fold_enhanced_to_children(merged, kb_id)
        timings["fold"] = _elapsed_ms(fold_started_at)
        related_started_at = time.perf_counter()
        expanded = _expand_related(folded, kb_id)
        timings["related"] = _elapsed_ms(related_started_at)
        filter_started_at = time.perf_counter()
        result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
        timings["filter"] = _elapsed_ms(filter_started_at)
        timings["total"] = _elapsed_ms(started_at)
        self.last_timings = timings
        return result
