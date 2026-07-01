from __future__ import annotations

import uuid

from backend.adapters.rag_adapter import run_graph_rag_pipeline, run_rag_pipeline
from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.evaluation_store import append_evaluation
from core.db.identity import IdentityContext
from core.db.knowledge_base import get_knowledge_base
from core.db.query_logs import LlmCallLogRecord, RagQueryLogRecord, append_llm_call_log, append_rag_query_log
from core.runtime_settings import resolve_runtime_setting


def _image_asset_url(image_path: str | None) -> str | None:
    if not image_path:
        return None
    normalized = image_path.replace("\\", "/")
    if normalized.startswith(("http://", "https://", "data:image/")):
        return normalized
    marker = "/data/output/"
    if marker in normalized:
        relative = normalized.split(marker, 1)[1]
    elif normalized.startswith("data/output/"):
        relative = normalized[len("data/output/") :]
    elif "/output/" in normalized:
        relative = normalized.split("/output/", 1)[1]
    else:
        return None
    return f"/api/assets/output/{relative.lstrip('/')}"


def run_rag_query(
    payload: QueryRequest,
    identity: IdentityContext | None = None,
    request_id: str | None = None,
    pipeline_domain: str = "online_rag",
) -> dict:
    _assert_kb_access(payload.kb_id, identity)
    request_id = request_id or str(uuid.uuid4())
    candidates, reranked, answer, scores = run_rag_pipeline(
        query=payload.query,
        kb_id=payload.kb_id,
        top_k=payload.top_k,
        min_score=payload.min_score,
        use_llm_check=payload.use_llm_check,
        use_llm_score=payload.use_llm_score,
    )
    latency_ms = scores.get("_latency_ms", {}) if isinstance(scores, dict) else {}

    recall_channels = [
        {"channel": "dense", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) > 0)},
        {"channel": "sparse", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) == 0)},
        {"channel": "structured", "count": 0},
        {"channel": "rrf", "count": len(candidates)},
        {"channel": "related", "count": sum(1 for c in candidates if c.get("score", 0.0) <= 0.3)},
    ]

    result = {
        "requestId": request_id,
        "query": payload.query,
        "kbId": payload.kb_id,
        "answer": answer.get("answer", ""),
        "cannotAnswer": bool(answer.get("cannot_answer", False)),
        "citations": [_citation_to_payload(c) for c in answer.get("citations", [])],
        "scores": {
            "relevanceScore": float(scores.get("relevance_score", 0.0) or 0.0),
            "faithfulnessScore": float(scores.get("faithfulness_score", 0.0) or 0.0),
            "llmScore": scores.get("llm_score", None),
            "cannotAnswer": bool(answer.get("cannot_answer", False)),
            "interpretation": "结果由检索器、重排器、生成器和评分器共同产生。",
        },
        "timings": _timings_to_payload(latency_ms),
        "recallChannels": recall_channels,
        "candidates": [_candidate_to_payload(c) for c in reranked],
        "contextWindow": [c.get("context_window", c.get("content", "")) for c in reranked],
        "trace": [
            {"key": "retrieval", "label": "混合检索", "status": "success", "detail": "稠密与稀疏候选经粗过滤后完成融合。"},
            {"key": "rerank", "label": "父子重排", "status": "success", "detail": "最佳子块窗口被提升进答案上下文。"},
            {"key": "generate", "label": "答案生成", "status": "success", "detail": "生成器仅允许使用提供的上下文内容。"},
            {
                "key": "score",
                "label": "质量评分",
                "status": "success" if not answer.get("cannot_answer", False) else "degraded",
                "detail": "规则评分结合可选的 LLM 评分。",
            },
        ],
    }
    _attach_trace_timings(result["trace"], latency_ms)

    append_evaluation(
        {
            "id": str(uuid.uuid4()),
            "kbId": payload.kb_id,
            "query": result["query"],
            "answer": result["answer"],
            "relevanceScore": result["scores"]["relevanceScore"],
            "faithfulnessScore": result["scores"]["faithfulnessScore"],
            "llmScore": result["scores"]["llmScore"],
            "cannotAnswer": result["cannotAnswer"],
            "failureReason": None,
        }
    )
    prompt_tokens, completion_tokens, total_tokens = _token_usage_totals(result["timings"].get("llmUsage", {}))
    _record_rag_llm_usage(
        request_id=request_id,
        kb_id=payload.kb_id,
        identity=identity,
        llm_usage=result["timings"].get("llmUsage", {}),
        latency_ms=result["timings"]["latencyMs"],
        pipeline_domain=pipeline_domain,
    )
    append_rag_query_log(
        RagQueryLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            kb_id=payload.kb_id,
            query=payload.query,
            answer=result["answer"],
            identity=identity,
            cannot_answer=result["cannotAnswer"],
            relevance_score=result["scores"]["relevanceScore"],
            faithfulness_score=result["scores"]["faithfulnessScore"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=result["timings"]["latencyMs"]["total"],
        )
    )

    return result


def _timings_to_payload(latency_ms: dict) -> dict:
    return {
        "latencyMs": {
            "retrieval": int(latency_ms.get("retrieval", 0) or 0),
            "rerank": int(latency_ms.get("rerank", 0) or 0),
            "generate": int(latency_ms.get("generate", 0) or 0),
            "score": int(latency_ms.get("score", 0) or 0),
            "build": int(latency_ms.get("build", 0) or 0),
            "total": int(latency_ms.get("total", 0) or 0),
        },
        "retrievalBreakdownMs": _retrieval_breakdown_to_payload(
            latency_ms.get("retrieval_breakdown", {})
        ),
        "llmUsage": _llm_usage_to_payload(latency_ms.get("llm_usage", {})),
        "shortCircuit": bool(latency_ms.get("short_circuit", False)),
    }


def _retrieval_breakdown_to_payload(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, int | bool] = {}
    for key, raw in value.items():
        if key == "short_circuit":
            payload["shortCircuit"] = bool(raw)
        else:
            payload[_snake_to_camel(str(key))] = int(raw or 0)
    return payload


def _llm_usage_to_payload(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        _snake_to_camel(str(key)): int(raw or 0)
        for key, raw in value.items()
        if isinstance(raw, (int, float)) and not isinstance(raw, bool)
    }


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _attach_trace_timings(trace: list[dict], latency_ms: dict) -> None:
    for item in trace:
        key = item.get("key", "")
        item["latencyMs"] = int(latency_ms.get(key, 0) or 0)


def run_graph_rag_query(
    payload: GraphQueryRequest,
    identity: IdentityContext | None = None,
    request_id: str | None = None,
    pipeline_domain: str = "graph_rag",
) -> dict:
    _assert_kb_access(payload.kb_id, identity)
    request_id = request_id or str(uuid.uuid4())
    result = run_graph_rag_pipeline(
        query=payload.query,
        kb_id=payload.kb_id,
        top_k=payload.top_k,
        min_score=payload.min_score,
        explain=payload.explain,
        intent=payload.intent,
    )
    response = {
        "requestId": request_id,
        "query": payload.query,
        "kbId": payload.kb_id,
        "intent": result["intent"],
        "intentSource": result["intent_source"],
        "results": result["results"],
        "stats": result["stats"],
    }
    append_rag_query_log(
        RagQueryLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            kb_id=payload.kb_id,
            query=payload.query,
            identity=identity,
            total_tokens=0,
            latency_ms=0,
        )
    )
    return response


def _record_rag_llm_usage(
    *,
    request_id: str,
    kb_id: str,
    identity: IdentityContext | None,
    llm_usage: object,
    latency_ms: dict,
    pipeline_domain: str,
) -> None:
    if not isinstance(llm_usage, dict):
        return
    for item in (
        ("rerankLlmCheck", pipeline_domain, "rerank", "重排 LLM 检查", "rerank"),
        ("generate", pipeline_domain, "generation", "问答生成 LLM", "generate"),
        ("score", "evaluation", "evaluation", "评测打分 LLM", "score"),
    ):
        prefix, domain, stage, feature_name, latency_key = item
        _record_rag_llm_usage_prefix(
            request_id=request_id,
            kb_id=kb_id,
            identity=identity,
            llm_usage=llm_usage,
            latency_ms=latency_ms,
            token_prefix=prefix,
            pipeline_domain=domain,
            pipeline_stage=stage,
            feature_name=feature_name,
            latency_key=latency_key,
        )


def _record_rag_llm_usage_prefix(
    *,
    request_id: str,
    kb_id: str,
    identity: IdentityContext | None,
    llm_usage: dict,
    latency_ms: dict,
    token_prefix: str,
    pipeline_domain: str,
    pipeline_stage: str,
    feature_name: str,
    latency_key: str,
) -> None:
    prompt_tokens = int(llm_usage.get(f"{token_prefix}PromptTokens", 0) or 0)
    completion_tokens = int(llm_usage.get(f"{token_prefix}CompletionTokens", 0) or 0)
    total_tokens = int(llm_usage.get(f"{token_prefix}TotalTokens", 0) or 0)
    if total_tokens <= 0 and prompt_tokens + completion_tokens <= 0:
        return
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    append_llm_call_log(
        LlmCallLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            pipeline_stage=pipeline_stage,
            feature_name=feature_name,
            provider=_runtime_str("RAG_LLM_BASE_URL", "openai-compatible"),
            model_name=_runtime_str("RAG_LLM_MODEL", "qwen-max"),
            kb_id=kb_id,
            identity=identity,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int(latency_ms.get(latency_key, 0) or 0),
        )
    )


def _runtime_str(key: str, default: str) -> str:
    try:
        value, _source = resolve_runtime_setting(key)
    except Exception:
        value = default
    return str(value or default)


def _assert_kb_access(kb_id: str, identity: IdentityContext | None = None) -> None:
    if identity is None or not identity.enforce_access:
        return
    if get_knowledge_base(kb_id, identity) is None:
        raise ValueError(f"Knowledge base '{kb_id}' not found or not accessible")


def _token_usage_totals(llm_usage: object) -> tuple[int, int, int]:
    if not isinstance(llm_usage, dict):
        return 0, 0, 0
    prompt = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("PromptTokens"))
    completion = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("CompletionTokens"))
    total = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("TotalTokens"))
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total


def _citation_to_payload(citation: dict) -> dict:
    chunk_index = citation.get("chunk_index", None)
    return {
        "index": int(citation.get("index", 0) or 0),
        "source": citation.get("source", ""),
        "documentName": citation.get("document_name", citation.get("source", "")),
        "documentId": citation.get("document_id", ""),
        "page": int(citation.get("page", 0) or 0),
        "chunkIndex": int(chunk_index) if chunk_index is not None else None,
        "location": citation.get("location", ""),
        "snippet": citation.get("snippet", ""),
        "chunkId": citation.get("chunk_id", ""),
    }


def _candidate_to_payload(item: dict) -> dict:
    image_path = item.get("image_path", "") or None
    return {
        "id": item.get("id", ""),
        "source": item.get("source", ""),
        "documentName": item.get("document_name", item.get("source", "")),
        "documentId": item.get("document_id", ""),
        "page": int(item.get("page", 0) or 0),
        "chunkIndex": int(item.get("chunk_index", 0) or 0),
        "location": item.get("location") or _format_candidate_location(item),
        "layer": item.get("layer", "child"),
        "score": float(item.get("score", 0.0) or 0.0),
        "denseScore": float(item.get("dense_score", 0.0) or 0.0),
        "rerankScore": float(item.get("rerank_score", item.get("score", 0.0)) or 0.0),
        "channel": "rrf",
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "contextWindow": item.get("context_window", item.get("content", "")),
        "isImageChunk": bool(item.get("is_image_chunk", False)),
        "imagePath": image_path,
        "imageUrl": _image_asset_url(image_path),
        "relatedIds": item.get("related_ids", []),
        "bestChildId": item.get("best_child_id", ""),
        "matchedBy": item.get("matched_by", []),
        "matchedEnhancedId": item.get("matched_enhanced_id", ""),
    }


def _format_candidate_location(item: dict) -> str:
    return f"P.{int(item.get('page', 0) or 0)} · #{int(item.get('chunk_index', 0) or 0) + 1}"
