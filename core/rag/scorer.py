from __future__ import annotations

import re

from core.llm_usage import extract_response_usage


def _rule_score(contexts: list[dict], answer_dict: dict) -> dict:
    # prefer rerank_score when available (BM25-only hits have dense_score=0)
    relevance_scores = [
        float(context.get("rerank_score") or context.get("dense_score") or 0.0)
        for context in contexts
    ]
    relevance_score = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
    citations = answer_dict.get("citations", []) or []
    cited_count = len(citations)
    total_count = len(contexts)
    if total_count <= 0 or answer_dict.get("cannot_answer", False):
        faithfulness_score = 0.0
        valid_cited_count = 0
    else:
        context_ids = {str(context.get("id", "")) for context in contexts if context.get("id")}
        valid_indices = set(range(1, total_count + 1))
        valid_cited_count = 0
        for citation in citations:
            chunk_id = str(citation.get("chunk_id", "") or "")
            raw_index = citation.get("index", None)
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = 0
            if (chunk_id and chunk_id in context_ids) or index in valid_indices:
                valid_cited_count += 1
        faithfulness_score = valid_cited_count / cited_count if cited_count > 0 else 0.0
    return {
        "relevance_score": relevance_score,
        "faithfulness_score": faithfulness_score,
        "llm_score": None,
        "details": {
            "avg_dense_score": relevance_score,
            "cited_chunks": cited_count,
            "valid_cited_chunks": valid_cited_count,
            "total_chunks": total_count,
        },
    }


def _llm_score(query: str, answer: str) -> tuple[float | None, dict[str, int]]:
    try:
        from core.rag.generator import _get_rag_llm_client, _get_rag_llm_model

        client = _get_rag_llm_client()
        model = _get_rag_llm_model()
    except Exception:
        return None, {}

    prompt = (
        "请对以下问答质量打分（1-5分整数）：\n"
        f"问题：{query}\n"
        f"答案：{answer[:500]}\n"
        "只返回数字 1-5，不要其他内容"
    )
    try:
        from core.llm_config import resolve_llm_param
        system_prompt = resolve_llm_param("", "system_prompt", [])
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        usage = extract_response_usage(response)
        text = response.choices[0].message.content or ""
    except Exception:
        return None, {}
    match = re.search(r"[1-5]", text)
    if not match:
        return None, {}
    return float(match.group()) / 5.0, {
        "scoreRequests": 1,
        "scorePromptTokens": usage["prompt_tokens"],
        "scoreCompletionTokens": usage["completion_tokens"],
        "scoreTotalTokens": usage["total_tokens"],
    }


class RAGScorer:
    def score(
        self,
        query: str,
        contexts: list[dict],
        answer_dict: dict,
        use_llm_score: bool = False,
    ) -> dict:
        result = _rule_score(contexts, answer_dict)
        if use_llm_score:
            try:
                llm_result = _llm_score(query, answer_dict.get("answer", ""))
                if isinstance(llm_result, tuple):
                    score, usage = llm_result
                else:
                    score, usage = llm_result, {}
                result["llm_score"] = score
                result["llm_usage"] = usage
            except Exception:
                result["llm_score"] = None
                result["llm_usage"] = {}
        return result
