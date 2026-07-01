from __future__ import annotations

import time

from core.rag.generator import RAGGenerator
from core.rag.graph_retriever import GraphRetriever
from core.rag.reranker import ParentChildReranker
from core.rag.retriever import HybridRetriever
from core.rag.scorer import RAGScorer


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _merge_usage(*values: dict | None) -> dict[str, int]:
    merged: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, raw in value.items():
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                merged[str(key)] = int(merged.get(str(key), 0) or 0) + int(raw or 0)
    return merged


def run_rag_pipeline(
    query: str,
    kb_id: str,
    top_k: int,
    min_score: float,
    use_llm_check: bool,
    use_llm_score: bool,
) -> tuple[list[dict], list[dict], dict, dict]:
    """Run full RAG pipeline. Returns (candidates, reranked, answer, scores)."""
    total_started_at = time.perf_counter()
    retriever = HybridRetriever()
    reranker = ParentChildReranker()
    generator = RAGGenerator()
    scorer = RAGScorer()

    retrieval_started_at = time.perf_counter()
    candidates = retriever.retrieve(
        query=query,
        kb_id=kb_id,
        top_k=max(top_k * 2, top_k),
        min_score=min_score,
    )
    retrieval_ms = _elapsed_ms(retrieval_started_at)
    retrieval_breakdown_ms = getattr(retriever, "last_timings", {})
    if candidates and candidates[0].get("retrieval_mode") == "media_ref":
        build_started_at = time.perf_counter()
        direct = sorted(candidates, key=lambda item: item.get("score", 0.0), reverse=True)[:top_k]
        for item in direct:
            item["rerank_score"] = float(item.get("score", 0.0) or 0.0)
            item["context_window"] = item.get("content", "") or ""
        answer = _build_media_ref_answer(query, direct)
        build_ms = _elapsed_ms(build_started_at)
        scores = {
            "relevance_score": 1.0 if direct else 0.0,
            "faithfulness_score": 1.0 if direct else 0.0,
            "llm_score": None,
            "_latency_ms": {
                "retrieval": retrieval_ms,
                "rerank": 0,
                "generate": 0,
                "score": 0,
                "build": build_ms,
                "total": _elapsed_ms(total_started_at),
                "short_circuit": True,
                "retrieval_breakdown": retrieval_breakdown_ms,
                "llm_usage": {},
            },
        }
        return candidates, direct, answer, scores

    rerank_started_at = time.perf_counter()
    reranked = reranker.rerank(
        query=query,
        candidates=candidates,
        kb_id=kb_id,
        top_k=top_k,
        use_llm_check=use_llm_check,
    )
    rerank_ms = _elapsed_ms(rerank_started_at)
    generate_started_at = time.perf_counter()
    answer = generator.generate(query, reranked)
    generate_ms = _elapsed_ms(generate_started_at)
    score_started_at = time.perf_counter()
    scores = scorer.score(
        query=query,
        contexts=reranked,
        answer_dict=answer,
        use_llm_score=use_llm_score,
    )
    llm_usage = _merge_usage(
        getattr(reranker, "last_metrics", {}),
        answer.get("llm_usage", {}),
        scores.get("llm_usage", {}),
    )
    scores["_latency_ms"] = {
        "retrieval": retrieval_ms,
        "rerank": rerank_ms,
        "generate": generate_ms,
        "score": _elapsed_ms(score_started_at),
        "total": _elapsed_ms(total_started_at),
        "short_circuit": False,
        "retrieval_breakdown": retrieval_breakdown_ms,
        "llm_usage": llm_usage,
    }
    return candidates, reranked, answer, scores


def _build_media_ref_answer(query: str, contexts: list[dict]) -> dict:
    if not contexts:
        return {
            "answer": "根据现有文档无法回答该问题",
            "citations": [],
            "cannot_answer": True,
        }

    first = contexts[0]
    source = first.get("document_name") or first.get("source", "")
    page = int(first.get("page", 0) or 0)
    chunk_index = first.get("chunk_index", None)
    location = f"P.{page}" if chunk_index is None else f"P.{page} · #{int(chunk_index) + 1}"
    label = first.get("matched_media_ref") or query
    answer = f"已定位到 {label}，来源：{source}，位置：{location}。[1]"
    citation = {
        "index": 1,
        "source": source,
        "document_name": source,
        "document_id": first.get("document_id", ""),
        "page": page,
        "chunk_index": chunk_index,
        "location": location,
        "snippet": (first.get("context_window") or first.get("content") or "")[:100],
        "chunk_id": first.get("id", ""),
    }
    return {
        "answer": answer,
        "citations": [citation],
        "cannot_answer": False,
    }


def run_graph_rag_pipeline(
    query: str,
    kb_id: str,
    top_k: int,
    min_score: float,
    explain: bool,
    intent: str | None,
) -> dict:
    retriever = GraphRetriever()
    return retriever.retrieve(
        query=query,
        kb_id=kb_id,
        top_k=top_k,
        min_score=min_score,
        explain=explain,
        intent=intent,
    )
