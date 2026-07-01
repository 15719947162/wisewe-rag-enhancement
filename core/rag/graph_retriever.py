from __future__ import annotations

import time
from typing import Any

from core.rag.graph_expander import graph_expand
from core.rag.intent_router import classify_intent
from core.rag.retriever import (
    _entity_retrieve,
    _fetch_chunks_by_ids,
    _coarse_filter,
    _dense_retrieve,
    _expand_related,
    _rrf_merge,
    _sparse_retrieve,
)
from core.embedding.client import embed_query_cached, embed_texts


class GraphRetriever:
    def retrieve(
        self,
        query: str,
        kb_id: str,
        top_k: int = 5,
        min_score: float = 0.3,
        explain: bool = False,
        intent: str | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        resolved_intent, source = classify_intent(query) if not intent else (intent, "override")
        recall_started = time.perf_counter()
        query_vec, query_embedding_cache_hit = embed_query_cached(query)
        dense = _dense_retrieve(query_vec, kb_id, 20)
        sparse = _sparse_retrieve(query, kb_id, 20)
        entity_hits = _entity_retrieve(query, kb_id, query_vec if resolved_intent != "data" else None, 20)
        merged = _rrf_merge(dense, sparse, entity_hits)
        expanded_related = _expand_related(merged, kb_id)
        seed_top = _coarse_filter(expanded_related, min_score=min_score, top_n=max(top_k * 2, 10))
        recall_elapsed = int((time.perf_counter() - recall_started) * 1000)

        expand_started = time.perf_counter()
        expanded_graph = graph_expand([item["id"] for item in seed_top], kb_id, resolved_intent, max_hops=2, max_neighbors=50)
        expand_elapsed = int((time.perf_counter() - expand_started) * 1000)

        by_id = {item["id"]: dict(item) for item in seed_top}
        for item in expanded_graph:
            entry = by_id.get(item["id"], {"id": item["id"], "score": 0.0})
            entry["score"] = max(float(entry.get("score", 0.0)), float(item["score"]))
            if explain:
                entry["path"] = item["path"]
            entry["channel"] = item.get("channel", "graph_expand")
            sources = entry.setdefault("sources", [])
            if "graph_expand" not in sources:
                sources.append("graph_expand")
            by_id[item["id"]] = entry

        ranked = sorted(by_id.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)[:top_k]
        build_started = time.perf_counter()
        ranked = self._hydrate_results(ranked, kb_id, explain)
        context = self._build_context(ranked, explain)
        build_elapsed = int((time.perf_counter() - build_started) * 1000)
        total_elapsed = int((time.perf_counter() - started_at) * 1000)
        return {
            "intent": resolved_intent,
            "intent_source": source,
            "results": ranked,
            "context": context,
            "stats": {
                "recall_counts": {"embedding": len(dense), "bm25": len(sparse), "entity": len(entity_hits)},
                "after_fusion": len(merged),
                "after_expand": len(expanded_graph),
                "after_dedupe": len(ranked),
                "latency_ms": {
                    "recall": recall_elapsed,
                    "expand": expand_elapsed,
                    "build": build_elapsed,
                    "total": total_elapsed,
                },
                "query_embedding_cache_hit": query_embedding_cache_hit,
            },
        }

    def _hydrate_results(self, ranked: list[dict[str, Any]], kb_id: str, explain: bool) -> list[dict[str, Any]]:
        chunk_ids = [item["id"] for item in ranked]
        chunks = _fetch_chunks_by_ids(chunk_ids, kb_id)
        chunk_map = {chunk["id"]: chunk for chunk in chunks}
        entity_map = self._load_entities_for_chunks(chunk_ids, kb_id)

        hydrated: list[dict[str, Any]] = []
        for item in ranked:
            base = chunk_map.get(item["id"])
            if base is None:
                continue
            result = dict(base)
            result["score"] = float(item.get("score", 0.0) or 0.0)
            result["rank"] = len(hydrated) + 1
            result["source"] = item.get("sources", item.get("channel", "graph_expand"))
            result["entities"] = entity_map.get(base["id"], [])
            if explain and item.get("path"):
                result["path"] = item["path"]
            hydrated.append(result)
        return hydrated

    def _load_entities_for_chunks(self, chunk_ids: list[str], kb_id: str) -> dict[str, list[dict[str, Any]]]:
        if not chunk_ids:
            return {}

        from core.db.connection import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT em.chunk_id::text, e.name, e.type, e.definition
                    FROM entity_mentions em
                    JOIN entities e ON e.id = em.entity_id
                    WHERE em.kb_id = %s AND em.chunk_id::text = ANY(%s)
                    ORDER BY e.name
                    """,
                    (kb_id, chunk_ids),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        by_chunk: dict[str, list[dict[str, Any]]] = {}
        for chunk_id, name, entity_type, definition in rows:
            by_chunk.setdefault(chunk_id, []).append(
                {
                    "name": name,
                    "type": entity_type,
                    "definition": definition or name,
                }
            )
        return by_chunk

    def _build_context(self, hits: list[dict[str, Any]], explain: bool) -> str:
        blocks: list[str] = []
        for hit in hits:
            entity_lines = "\n".join(
                f"- {entity['name']}：{entity['definition']}" for entity in hit.get("entities", [])
            )
            block = f"## {hit.get('source', '')} 第 {hit.get('page', 0)} 页\n{hit.get('content', '')}"
            if entity_lines:
                block += f"\n\n相关实体：\n{entity_lines}"
            if explain and hit.get("path"):
                path = " → ".join(step["rel_type"] for step in hit["path"])
                block += f"\n\n路径：{path}"
            blocks.append(block)
        return "\n\n---\n\n".join(blocks)
