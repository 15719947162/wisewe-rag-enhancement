from __future__ import annotations

import os
import re
from typing import Any

import openai

from core.http_client import create_openai_client
from core.llm_usage import extract_response_usage

RAG_SYSTEM_PROMPT = """你是一个严格基于文档的问答助手。
规则：
1. 只能从提供的上下文中提取答案，不得编造任何信息。
2. 引用来源时使用 [数字] 格式，如 [1][2]。
3. 如果上下文中没有足够信息回答问题，回答：“根据现有文档无法回答该问题”。
4. 答案末尾必须列出参考来源，格式：[N] 文档名，位置。"""

_CANNOT_ANSWER_TEXT = "根据现有文档无法回答该问题"


def _get_rag_system_prompt() -> str:
    """Return system prompt: global config takes precedence over module default."""
    from core.llm_config import resolve_llm_param

    return (
        resolve_llm_param("", "rag_system_prompt", ["RAG_SYSTEM_PROMPT"])
        or resolve_llm_param("", "system_prompt", [])
        or RAG_SYSTEM_PROMPT
    )


def _get_rag_llm_client() -> openai.OpenAI:
    rag_key = os.environ.get("RAG_LLM_API_KEY", "").strip()
    rag_url = os.environ.get("RAG_LLM_BASE_URL", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()
    llm_url = os.environ.get("LLM_BASE_URL", "").strip()
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    key = rag_key or llm_key or dashscope_key or openai_key
    if not key:
        raise ValueError("No RAG LLM API key configured.")

    base_url = ""
    if rag_key:
        base_url = rag_url
    elif llm_key:
        base_url = llm_url
    elif dashscope_key:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    kwargs: dict[str, Any] = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return create_openai_client(**kwargs)


def _get_rag_llm_model() -> str:
    return (
        os.environ.get("RAG_LLM_MODEL", "").strip()
        or os.environ.get("LLM_CLEANER_MODEL", "").strip()
        or "qwen-max"
    )


def _format_location(context: dict) -> str:
    page = int(context.get("page", 0) or 0)
    chunk_index = context.get("chunk_index", None)
    if chunk_index is None:
        return f"P.{page}"
    return f"P.{page} · #{int(chunk_index) + 1}"


def _build_rag_prompt(query: str, contexts: list[dict]) -> tuple[str, str]:
    context_blocks: list[str] = []
    for index, context in enumerate(contexts, start=1):
        text = (context.get("context_window") or context.get("content") or "")[:500]
        source = context.get("document_name") or context.get("source", "")
        context_blocks.append(f"[{index}] 来源：{source}，位置：{_format_location(context)}\n{text}")
    joined_contexts = "\n\n".join(context_blocks)
    user_prompt = (
        f"问题：{query}\n\n"
        "上下文：\n"
        f"{joined_contexts}\n\n"
        "请基于以上上下文回答问题，并在答案中标注引用编号 [1][2]。"
    )
    return _get_rag_system_prompt(), user_prompt


def _parse_answer_with_citations(raw_answer: str, contexts: list[dict]) -> dict:
    seen: set[int] = set()
    citations = []
    for match in re.findall(r"\[(\d+)\]", raw_answer):
        index = int(match)
        if index in seen or index - 1 >= len(contexts):
            continue
        seen.add(index)
        context = contexts[index - 1]
        snippet_source = context.get("context_window") or context.get("content", "")
        source = context.get("document_name") or context.get("source", "")
        citations.append(
            {
                "index": index,
                "source": source,
                "document_name": source,
                "document_id": context.get("document_id", ""),
                "page": context.get("page", 0),
                "chunk_index": context.get("chunk_index", None),
                "location": _format_location(context),
                "snippet": snippet_source[:100],
                "chunk_id": context.get("id", ""),
            }
        )
    return {
        "answer": raw_answer,
        "citations": citations,
        "cannot_answer": _CANNOT_ANSWER_TEXT in raw_answer,
    }


def _context_score(context: dict) -> float:
    return float(context.get("rerank_score") or context.get("score") or context.get("dense_score") or 0.0)


def _build_extract_answer_from_contexts(query: str, contexts: list[dict], min_score: float = 0.65) -> dict | None:
    """Fallback when high-confidence evidence exists but the LLM refuses to answer."""
    strong_contexts = [
        (index, context)
        for index, context in enumerate(contexts, start=1)
        if _context_score(context) >= min_score
    ]
    if not strong_contexts:
        return None

    query_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", query))

    def sentence_score(sentence: str, context: dict) -> tuple[int, float, int]:
        hit_count = sum(1 for term in query_terms if term and term in sentence)
        return hit_count, _context_score(context), len(sentence)

    evidence: list[tuple[int, str]] = []
    for index, context in strong_contexts[:4]:
        text = (context.get("context_window") or context.get("content") or "").strip()
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])", text) if item.strip()]
        if not sentences and text:
            sentences = [text[:220]]
        if not sentences:
            continue
        best = max(sentences, key=lambda sentence: sentence_score(sentence, context))
        evidence.append((index, best[:220]))

    if not evidence:
        return None

    answer_lines = [f"根据当前召回证据，{query}可归纳为："]
    for index, sentence in evidence[:3]:
        answer_lines.append(f"- {sentence} [{index}]")
    parsed = _parse_answer_with_citations("\n".join(answer_lines), contexts)
    if not parsed["citations"]:
        return None
    parsed["cannot_answer"] = False
    parsed["fallback"] = "extractive_high_confidence"
    return parsed


class RAGGenerator:
    def generate(
        self,
        query: str,
        contexts: list[dict],
        temperature: float = 0.1,
    ) -> dict:
        if not contexts:
            return {
                "answer": _CANNOT_ANSWER_TEXT,
                "citations": [],
                "cannot_answer": True,
            }

        try:
            system_prompt, user_prompt = _build_rag_prompt(query, contexts)
            client = _get_rag_llm_client()
            model = _get_rag_llm_model()
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            usage = extract_response_usage(response)
            raw_answer = response.choices[0].message.content or ""
            parsed = _parse_answer_with_citations(raw_answer, contexts)
            parsed["llm_usage"] = {
                "generateRequests": 1,
                "generatePromptTokens": usage["prompt_tokens"],
                "generateCompletionTokens": usage["completion_tokens"],
                "generateTotalTokens": usage["total_tokens"],
            }
            if parsed["cannot_answer"]:
                fallback = _build_extract_answer_from_contexts(query, contexts)
                if fallback:
                    fallback["llm_usage"] = parsed["llm_usage"]
                    return fallback
            if not parsed["citations"]:
                fallback = _build_extract_answer_from_contexts(query, contexts)
                if fallback:
                    fallback["llm_usage"] = parsed["llm_usage"]
                    return fallback
            return parsed
        except Exception as exc:
            fallback = _build_extract_answer_from_contexts(query, contexts)
            if fallback:
                fallback["error"] = str(exc)
                return fallback
            return {
                "answer": _CANNOT_ANSWER_TEXT,
                "citations": [],
                "cannot_answer": True,
                "error": str(exc),
            }
