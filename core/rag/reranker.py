from __future__ import annotations

import json
import os
import re
from typing import Any

from core.http_client import create_httpx_client
from core.llm_usage import TokenUsage, extract_response_usage
from core.db.connection import get_db_connection


def _call_reranker_api(query: str, documents: list[str], model: str = "gte-rerank") -> list[float]:
    if not documents:
        return []
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return [0.0] * len(documents)

    payload = {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False},
    }
    try:
        with create_httpx_client(timeout=30) as client:
            response = client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code != 200:
                return [0.0] * len(documents)
            body = response.json()
    except Exception:
        return [0.0] * len(documents)

    try:
        results = body["output"]["results"]
        scores = [float(item.get("relevance_score", 0.0) or 0.0) for item in results]
    except Exception:
        return [0.0] * len(documents)
    if len(scores) != len(documents):
        return [0.0] * len(documents)
    return scores


def _rerank_children(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []

    reranked = [dict(candidate) for candidate in candidates]
    child_indices = [idx for idx, candidate in enumerate(reranked) if candidate.get("layer") == "child"]
    if not child_indices:
        for candidate in reranked:
            candidate["rerank_score"] = float(candidate.get("score", 0.0) or 0.0)
        return sorted(reranked, key=lambda item: item.get("rerank_score", 0.0), reverse=True)

    child_docs = [reranked[idx].get("content", "") for idx in child_indices]
    child_scores = _call_reranker_api(query, child_docs)
    fallback = not child_scores or max(child_scores) <= 0
    child_score_map = {
        child_index: float(reranked[child_index].get("score", 0.0) or 0.0)
        for child_index in child_indices
    }
    if not fallback:
        for child_index, rerank_score in zip(child_indices, child_scores):
            child_score_map[child_index] = float(rerank_score or 0.0)

    for idx, candidate in enumerate(reranked):
        if idx in child_score_map:
            candidate["rerank_score"] = child_score_map[idx]
        else:
            candidate["rerank_score"] = float(candidate.get("score", 0.0) or 0.0)
    return sorted(reranked, key=lambda item: item.get("rerank_score", 0.0), reverse=True)


def _normalize_related_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []


def _aggregate_to_parents(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    if not candidates:
        return []

    children = [candidate for candidate in candidates if candidate.get("layer") == "child" and candidate.get("parent_id")]
    parent_ids = sorted({str(candidate["parent_id"]) for candidate in children if candidate.get("parent_id")})
    if not parent_ids:
        return candidates

    best_child_by_parent: dict[str, dict[str, Any]] = {}
    for child in children:
        parent_id = str(child["parent_id"])
        best_child = best_child_by_parent.get(parent_id)
        if not best_child or float(child.get("rerank_score", 0.0)) > float(best_child.get("rerank_score", 0.0)):
            best_child_by_parent[parent_id] = child

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, source, page, chunk_index, layer, parent_id, related_ids
                FROM chunks
                WHERE id::text = ANY(%s) AND kb_id = %s
                """,
                (parent_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    parents: list[dict[str, Any]] = []
    for row in rows:
        data = dict(zip(columns, row))
        parent_id = str(data.get("id", ""))
        best_child = best_child_by_parent.get(parent_id)
        if not best_child:
            continue
        parent_score = float(best_child.get("rerank_score", 0.0) or 0.0)
        parents.append(
            {
                "id": parent_id,
                "content": data.get("content", "") or "",
                "source": data.get("source", "") or "",
                "page": int(data.get("page", 0) or 0),
                "chunk_index": int(data.get("chunk_index", 0) or 0),
                "layer": "parent",
                "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
                "related_ids": _normalize_related_ids(data.get("related_ids")),
                "score": parent_score,
                "dense_score": float(best_child.get("dense_score", 0.0) or 0.0),
                "rerank_score": parent_score,
                "context_window": data.get("content", "") or "",
                "best_child_id": best_child.get("id", ""),
            }
        )

    merged: list[dict[str, Any]] = parents[:]
    for candidate in candidates:
        if candidate.get("layer") != "child" or not candidate.get("parent_id"):
            merged.append(dict(candidate))
    return sorted(merged, key=lambda item: item.get("rerank_score", 0.0), reverse=True)


def _window_reorder(
    candidates: list[dict[str, Any]],
    kb_id: str,
    window_size: int = 3,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    updated = [dict(candidate) for candidate in candidates]
    conn = get_db_connection()
    try:
        for candidate in updated:
            if candidate.get("layer") != "parent":
                candidate["context_window"] = candidate.get("content", "") or ""
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, chunk_index
                    FROM chunks
                    WHERE parent_id::text = %s AND kb_id = %s
                    ORDER BY chunk_index
                    """,
                    (candidate["id"], kb_id),
                )
                rows = cur.fetchall()
            if not rows:
                candidate["context_window"] = candidate.get("content", "") or ""
                continue

            center_index = 0
            best_child_id = str(candidate.get("best_child_id", ""))
            for idx, row in enumerate(rows):
                if str(row[0]) == best_child_id:
                    center_index = idx
                    break
            half_window = max(window_size // 2, 0)
            start = max(center_index - half_window, 0)
            end = min(start + window_size, len(rows))
            if end - start < window_size:
                start = max(end - window_size, 0)
            window_rows = rows[start:end]
            candidate["context_window"] = "\n\n".join(str(row[1] or "") for row in window_rows) or candidate.get("content", "")
    finally:
        conn.close()
    return updated


def _llm_final_check(query: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not candidates:
        return [], {}
    try:
        from core.rag.generator import _get_rag_llm_client, _get_rag_llm_model

        client = _get_rag_llm_client()
        model = _get_rag_llm_model()
    except Exception:
        return candidates, {}

    filtered: list[dict[str, Any]] = []
    token_usage = TokenUsage()
    try:
        from core.llm_config import resolve_llm_param
        system_prompt = resolve_llm_param("", "system_prompt", [])
        for candidate in candidates:
            prompt = (
                f"以下内容是否与问题相关？问题：{query}\n"
                f"内容：{(candidate.get('context_window') or candidate.get('content') or '')[:300]}\n"
                "只回答 yes 或 no"
            )
            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            token_usage.add_usage(extract_response_usage(response))
            text = (response.choices[0].message.content or "").strip().lower()
            if "no" not in text:
                filtered.append(candidate)
    except Exception:
        return candidates, token_usage.to_metrics("rerankLlmCheck")
    return filtered or candidates, token_usage.to_metrics("rerankLlmCheck")


class ParentChildReranker:
    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        kb_id: str,
        top_k: int = 8,
        use_llm_check: bool = False,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []

        reranked = _rerank_children(query, candidates)
        # Final evidence stays at child granularity. Parent chunks are reserved for
        # structure/navigation rather than replacing child evidence in answer context.
        windowed = _window_reorder(reranked, kb_id)
        top_candidates = sorted(windowed, key=lambda item: item.get("rerank_score", 0.0), reverse=True)[:top_k]
        self.last_metrics: dict[str, int] = {}
        if use_llm_check:
            top_candidates, self.last_metrics = _llm_final_check(query, top_candidates)
        return top_candidates
