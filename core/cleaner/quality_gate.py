from __future__ import annotations

from dataclasses import dataclass, field

from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_usage import TokenUsage
from core.models.content_block import Chunk


@dataclass
class DiscardedChunk:
    chunk_index: int
    reason: str
    preview: str
    score: int = 0  # LLM score if used


@dataclass
class QualityGateResult:
    chunks: list[Chunk]
    discarded_count: int = 0
    low_quality_count: int = 0
    details: list[str] = field(default_factory=list)
    discarded_chunks: list[DiscardedChunk] = field(default_factory=list)
    scores: dict[int, int] = field(default_factory=dict)  # chunk_index → LLM score
    metrics: dict[str, int] = field(default_factory=dict)


def apply_quality_gate(
    chunks: list[Chunk],
    max_punct_ratio: float = 0.9,
    min_score: int = 0,
    score_only: bool = False,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
) -> QualityGateResult:
    """Post-chunking quality filter.

    Rules (applied in order, exempt: table/image chunks):
    1. max_punct_ratio — discard if >max_punct_ratio of chars are punctuation/whitespace
    2. min_score       — if > 0, call LLM to score each chunk (1-5)
                         score_only=True: record scores in details but keep all chunks
    """
    kept: list[Chunk] = []
    discarded: list[DiscardedChunk] = []
    details: list[str] = []

    text_chunks = []
    for chunk in chunks:
        if chunk.is_table_chunk or chunk.is_image_chunk:
            kept.append(chunk)
            continue

        text = chunk.content.strip()

        punct_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
        if len(text) > 0 and punct_count / len(text) > max_punct_ratio:
            discarded.append(DiscardedChunk(
                chunk_index=chunk.chunk_index,
                reason=f"纯标点/符号（占比 {punct_count/len(text):.0%}）",
                preview=text[:60],
            ))
            continue

        text_chunks.append(chunk)

    # LLM scoring (optional, only when min_score > 0)
    llm_scores: dict[int, int] = {}
    metrics: dict[str, int] = {}
    if min_score > 0 and text_chunks:
        scored, llm_metrics = _llm_score_chunks(
            text_chunks, min_score,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_system_prompt=llm_system_prompt,
        )
        metrics.update(llm_metrics)
        for chunk, score in scored:
            llm_scores[chunk.chunk_index] = score
            if score_only:
                kept.append(chunk)
            elif score < min_score:
                discarded.append(DiscardedChunk(
                    chunk_index=chunk.chunk_index,
                    reason=f"LLM 质量评分不足（{score}/5 < {min_score}/5）",
                    preview=chunk.content[:60],
                    score=score,
                ))
            else:
                kept.append(chunk)
    else:
        kept.extend(text_chunks)

    n_discarded = len(discarded)
    if n_discarded:
        details.append(f"质量门控丢弃: {n_discarded} 个低质量切片")

    return QualityGateResult(
        chunks=kept,
        discarded_count=n_discarded,
        details=details,
        discarded_chunks=discarded,
        scores=llm_scores,
        metrics=metrics,
    )


def _llm_score_chunks(
    chunks: list[Chunk],
    min_score: int,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
) -> tuple[list[tuple[Chunk, int]], dict[str, int]]:
    """Score chunks with LLM (1-5). Returns (chunk, score) pairs."""
    import json

    api_key = resolve_llm_param(
        llm_api_key, "api_key",
        ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
    )
    if not api_key:
        return [(c, 5) for c in chunks], {}

    base_url = resolve_llm_param(
        llm_base_url, "base_url",
        ["LLM_BASE_URL"],
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    model = resolve_llm_param(
        llm_model, "model",
        ["LLM_CLEANER_MODEL"],
        "qwen-plus",
    )
    system_prompt = llm_system_prompt or resolve_llm_param(
        "", "quality_gate_system_prompt", ["LLM_QUALITY_GATE_SYSTEM_PROMPT"],
        "",
    ) or resolve_llm_param(
        "", "system_prompt", [],
        (
            "你是知识库质量评估助手。对每个文本片段评估其作为检索知识库条目的价值，打分 1-5：\n"
            "5分：内容完整、信息丰富、有明确知识点\n"
            "4分：有一定价值，信息基本完整\n"
            "3分：内容有限，但有一定参考价值\n"
            "2分：信息碎片化，价值较低\n"
            "1分：无实质内容（列表符号、空洞描述、无意义片段）\n\n"
            "返回 JSON 数组，每项：{\"index\": 序号, \"score\": 分数, \"reason\": \"一句话理由\"}"
        ),
    )

    try:
        client = create_openai_client(api_key=api_key, base_url=base_url)
    except ImportError:
        return [(c, 5) for c in chunks], {}

    batch_size = 10
    results: dict[int, int] = {}
    token_usage = TokenUsage()

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        items = [{"index": j, "text": c.content[:300]} for j, c in enumerate(batch)]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
                ],
                temperature=0,
            )
            token_usage.add_response(response)
            content = response.choices[0].message.content or "[]"
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            scored = json.loads(content)
            for item in scored:
                results[i + item["index"]] = item.get("score", 3)
        except Exception:
            for j in range(len(batch)):
                results[i + j] = 3

    return [(chunk, results.get(idx, 3)) for idx, chunk in enumerate(chunks)], token_usage.to_metrics("qualityLlm")
