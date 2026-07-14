from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_key_pool import ApiKeyPool, is_throttle_error, parse_api_key_pool
from core.llm_usage import TokenUsage
from core.models.content_block import Chunk
from core.runtime_settings import QUALITY_GATE_SYSTEM_PROMPT_DEFAULT, normalize_quality_gate_system_prompt


QUALITY_GATE_OUTPUT_CONTRACT = (
    "输出格式必须是严格 JSON 数组，不要 Markdown、不要解释、不要额外文本。"
    "每一项只需要 index 和 score，例如："
    '[{"index":0,"score":5}]。'
    "reason 仅在 score<=2 时可选，且不超过 12 个字；不要为高分项输出 reason。"
    "score 只能是 1 到 5 的整数：5=内容完整且知识密度高，4=有明确知识点，"
    "3=信息有限但可检索，2=碎片化或价值较低，1=乱码、空内容或明显解析噪声。"
)


@dataclass
class DiscardedChunk:
    chunk_index: int
    reason: str
    preview: str
    score: int = 0


@dataclass
class QualityGateResult:
    chunks: list[Chunk]
    discarded_count: int = 0
    low_quality_count: int = 0
    details: list[str] = field(default_factory=list)
    discarded_chunks: list[DiscardedChunk] = field(default_factory=list)
    scores: dict[int, int] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)


@dataclass
class _BatchScoreResult:
    batch_number: int
    offset: int
    input_count: int
    scores: dict[int, int]
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    error: str = ""


def apply_quality_gate(
    chunks: list[Chunk],
    max_punct_ratio: float = 0.9,
    min_score: int = 0,
    score_only: bool = False,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_api_key_pool: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
    llm_key_retries: int = 1,
    llm_key_cooldown_seconds: int = 30,
    llm_batch_size: int = 10,
    llm_max_concurrency: int = 4,
    llm_progress_callback: Callable[[int, int], None] | None = None,
) -> QualityGateResult:
    """Post-chunking quality filter."""
    kept: list[Chunk] = []
    discarded: list[DiscardedChunk] = []
    details: list[str] = []

    text_chunks: list[Chunk] = []
    for chunk in chunks:
        if chunk.is_table_chunk or chunk.is_image_chunk:
            kept.append(chunk)
            continue

        text = chunk.content.strip()
        punct_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
        if len(text) > 0 and punct_count / len(text) > max_punct_ratio:
            discarded.append(
                DiscardedChunk(
                    chunk_index=chunk.chunk_index,
                    reason=f"punctuation_ratio>{max_punct_ratio:.0%}",
                    preview=text[:60],
                )
            )
            continue

        text_chunks.append(chunk)

    llm_scores: dict[int, int] = {}
    metrics: dict[str, int] = {}
    if min_score > 0 and text_chunks:
        scored, llm_metrics = _llm_score_chunks(
            text_chunks,
            min_score,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_api_key_pool=llm_api_key_pool,
            llm_model=llm_model,
            llm_system_prompt=llm_system_prompt,
            llm_key_retries=llm_key_retries,
            llm_key_cooldown_seconds=llm_key_cooldown_seconds,
            llm_batch_size=llm_batch_size,
            llm_max_concurrency=llm_max_concurrency,
            progress_callback=llm_progress_callback,
        )
        metrics.update(llm_metrics)
        for chunk, score in scored:
            llm_scores[chunk.chunk_index] = score
            if score_only:
                kept.append(chunk)
            elif score < min_score:
                discarded.append(
                    DiscardedChunk(
                        chunk_index=chunk.chunk_index,
                        reason=f"llm_score<{min_score}",
                        preview=chunk.content[:60],
                        score=score,
                    )
                )
            else:
                kept.append(chunk)
    else:
        kept.extend(text_chunks)

    discarded_count = len(discarded)
    if discarded_count:
        details.append(f"quality_gate_discarded={discarded_count}")

    return QualityGateResult(
        chunks=kept,
        discarded_count=discarded_count,
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
    llm_api_key_pool: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
    llm_key_retries: int = 1,
    llm_key_cooldown_seconds: int = 30,
    llm_batch_size: int = 10,
    llm_max_concurrency: int = 4,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[list[tuple[Chunk, int]], dict[str, int]]:
    """Score chunks with LLM (1-5). Returns (chunk, score) pairs."""
    del min_score
    api_key = resolve_llm_param(
        llm_api_key,
        "api_key",
        ["LLM_QUALITY_GATE_API_KEY", "LLM_API_KEY", "DASHSCOPE_API_KEY"],
    )
    pool_value = resolve_llm_param(
        llm_api_key_pool,
        "api_key_pool",
        ["LLM_QUALITY_GATE_API_KEY_POOL", "LLM_API_KEY_POOL"],
        "",
    )
    keys = parse_api_key_pool(api_key, pool_value)
    if not keys:
        return [(chunk, 5) for chunk in chunks], {}

    base_url = resolve_llm_param(
        llm_base_url,
        "base_url",
        ["LLM_QUALITY_GATE_BASE_URL", "LLM_BASE_URL"],
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    model = resolve_llm_param(
        llm_model,
        "model",
        ["LLM_QUALITY_GATE_MODEL", "LLM_CLEANER_MODEL"],
        "qwen-plus",
    )
    system_prompt = llm_system_prompt or resolve_llm_param(
        "",
        "quality_gate_system_prompt",
        ["LLM_QUALITY_GATE_SYSTEM_PROMPT"],
        "",
    )
    system_prompt = normalize_quality_gate_system_prompt(system_prompt) or QUALITY_GATE_SYSTEM_PROMPT_DEFAULT
    if "娴ｇ姵妲" in system_prompt or "閵" in system_prompt:
        system_prompt = QUALITY_GATE_SYSTEM_PROMPT_DEFAULT
    system_prompt = _with_output_contract(system_prompt)

    key_pool = ApiKeyPool(
        "quality",
        keys,
        cooldown_seconds=max(0, int(llm_key_cooldown_seconds or 0)),
    )
    batch_size = max(1, int(llm_batch_size or 20))
    max_workers = max(1, int(llm_max_concurrency or 1))
    max_workers = min(max_workers, max(1, (len(chunks) + batch_size - 1) // batch_size))

    batches = [
        (batch_number, offset, chunks[offset: offset + batch_size])
        for batch_number, offset in enumerate(range(0, len(chunks), batch_size), start=1)
    ]
    batch_results: list[_BatchScoreResult] = []
    completed_batches = 0
    total_batches = len(batches)
    if max_workers <= 1 or len(batches) <= 1:
        for batch_number, offset, batch in batches:
            batch_results.append(
                _score_batch_with_key_pool(
                    batch_number=batch_number,
                    offset=offset,
                    batch=batch,
                    key_pool=key_pool,
                    fallback_key=api_key or keys[0],
                    base_url=base_url,
                    model=model,
                    system_prompt=system_prompt,
                    key_retries=llm_key_retries,
                )
            )
            completed_batches += 1
            _notify_progress(progress_callback, completed_batches, total_batches)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _score_batch_with_key_pool,
                    batch_number=batch_number,
                    offset=offset,
                    batch=batch,
                    key_pool=key_pool,
                    fallback_key=api_key or keys[0],
                    base_url=base_url,
                    model=model,
                    system_prompt=system_prompt,
                    key_retries=llm_key_retries,
                )
                for batch_number, offset, batch in batches
            ]
            for future in as_completed(futures):
                batch_results.append(future.result())
                completed_batches += 1
                _notify_progress(progress_callback, completed_batches, total_batches)

    scores: dict[int, int] = {}
    token_usage = TokenUsage()
    for batch_result in sorted(batch_results, key=lambda result: result.batch_number):
        scores.update(batch_result.scores)
        token_usage.add(batch_result.token_usage)

    metrics = _quality_metrics(token_usage, batch_results, key_pool, batch_size, max_workers)
    return [(chunk, scores.get(index, 3)) for index, chunk in enumerate(chunks)], metrics


def _notify_progress(
    progress_callback: Callable[[int, int], None] | None,
    completed_batches: int,
    total_batches: int,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(completed_batches, total_batches)
    except Exception:
        return


def _score_batch_with_key_pool(
    *,
    batch_number: int,
    offset: int,
    batch: list[Chunk],
    key_pool: ApiKeyPool,
    fallback_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    key_retries: int,
) -> _BatchScoreResult:
    if key_pool.size <= 0:
        return _score_batch(
            batch_number=batch_number,
            offset=offset,
            batch=batch,
            api_key=fallback_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
        )

    attempted: set[str] = set()
    max_attempts = max(1, min(key_pool.size, int(key_retries or 0) + 1))
    last_result: _BatchScoreResult | None = None
    for attempt in range(max_attempts):
        lease = key_pool.acquire(exclude_keys=attempted)
        if lease is None:
            break
        started = time.monotonic()
        try:
            result = _score_batch(
                batch_number=batch_number,
                offset=offset,
                batch=batch,
                api_key=lease.key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
            )
        finally:
            key_pool.release(lease)
        throttled = bool(result.error and is_throttle_error(result.error))
        key_pool.record_attempt(
            lease,
            result.latency_ms or int((time.monotonic() - started) * 1000),
            success=not result.error,
            throttled=throttled,
        )
        last_result = result
        if throttled:
            key_pool.mark_throttled(lease)
            attempted.add(lease.key)
            if attempt < max_attempts - 1:
                key_pool.record_retry()
                continue
        return result
    return last_result or _default_batch_result(batch_number, offset, batch, "no_available_quality_key")


def _score_batch(
    *,
    batch_number: int,
    offset: int,
    batch: list[Chunk],
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
) -> _BatchScoreResult:
    started = time.monotonic()
    token_usage = TokenUsage()
    items = [{"index": index, "text": chunk.content[:200]} for index, chunk in enumerate(batch)]
    try:
        client = create_openai_client(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=max(96, min(512, len(batch) * 18 + 32)),
        )
        token_usage.add_response(response)
        content = response.choices[0].message.content or "[]"
        scores = _parse_score_content(content, offset, len(batch))
        if not scores:
            raise ValueError("no_parseable_quality_scores")
        for local_index in range(len(batch)):
            scores.setdefault(offset + local_index, 3)
        return _BatchScoreResult(
            batch_number=batch_number,
            offset=offset,
            input_count=len(batch),
            scores=scores,
            token_usage=token_usage,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except ImportError:
        return _default_batch_result(
            batch_number,
            offset,
            batch,
            "client_unavailable",
            default_score=5,
            started=started,
        )
    except Exception as exc:
        result = _default_batch_result(
            batch_number,
            offset,
            batch,
            str(exc),
            default_score=3,
            started=started,
        )
        result.token_usage.add(token_usage)
        return result


def _with_output_contract(system_prompt: str) -> str:
    prompt = (system_prompt or "").strip()
    if "只需要 index 和 score" in prompt and "reason 仅在" in prompt:
        return prompt
    return f"{prompt}\n\n{QUALITY_GATE_OUTPUT_CONTRACT}" if prompt else QUALITY_GATE_OUTPUT_CONTRACT


def _strip_json_fence(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _load_json_payload(content: str) -> object:
    text = _strip_json_fence(content)
    try:
        return json.loads(text)
    except Exception:
        pass

    candidates: list[str] = []
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(text[array_start: array_end + 1])
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(text[object_start: object_end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError("response_is_not_json")


def _coerce_score_item(item: dict, offset: int, batch_len: int) -> tuple[int, int] | None:
    raw_index = item.get("index", item.get("idx", item.get("chunk_index", item.get("chunkIndex"))))
    if raw_index is None:
        return None
    try:
        local_index = int(raw_index)
    except Exception:
        return None
    if not 0 <= local_index < batch_len:
        return None

    raw_score = item.get("score", item.get("quality_score", item.get("qualityScore")))
    if raw_score is None:
        decision = str(item.get("decision", item.get("action", ""))).lower()
        raw_score = 1 if decision in {"discard", "drop", "filter", "reject"} else 5
    try:
        score = int(float(raw_score))
    except Exception:
        score = 3
    return offset + local_index, min(5, max(1, score))


def _scores_from_json_payload(payload: object, offset: int, batch_len: int) -> dict[int, int]:
    if isinstance(payload, dict):
        for key in ("scores", "results", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            keyed_scores: dict[int, int] = {}
            for key, value in payload.items():
                if str(key).isdigit():
                    coerced = _coerce_score_item({"index": key, "score": value}, offset, batch_len)
                    if coerced:
                        keyed_scores[coerced[0]] = coerced[1]
            return keyed_scores

    if not isinstance(payload, list):
        return {}

    scores: dict[int, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        coerced = _coerce_score_item(item, offset, batch_len)
        if coerced:
            scores[coerced[0]] = coerced[1]
    return scores


def _scores_from_natural_language(content: str, offset: int, batch_len: int) -> dict[int, int]:
    text = content or ""
    scores: dict[int, int] = {}
    keep_words = ("保留", "有效", "通过", "可用于问答", "有价值", "keep", "pass", "valid")
    drop_words = ("过滤", "丢弃", "剔除", "无意义", "乱码", "噪声", "空内容", "discard", "drop", "reject", "invalid")
    for local_index in range(batch_len):
        marker = re.compile(
            rf"(?:切片|片段|chunk)\s*{local_index}\b|(?:index|索引)\s*[:：]?\s*{local_index}\b",
            re.IGNORECASE,
        )
        match = marker.search(text)
        if not match:
            continue
        next_match = None
        for next_index in range(local_index + 1, batch_len):
            next_marker = re.compile(
                rf"(?:切片|片段|chunk)\s*{next_index}\b|(?:index|索引)\s*[:：]?\s*{next_index}\b",
                re.IGNORECASE,
            )
            next_match = next_marker.search(text, match.end())
            if next_match:
                break
        segment = text[match.start(): next_match.start() if next_match else len(text)]
        lowered = segment.lower()
        if any(word in lowered for word in drop_words):
            scores[offset + local_index] = 1
        elif any(word in lowered for word in keep_words):
            scores[offset + local_index] = 5
    return scores


def _parse_score_content(content: str, offset: int, batch_len: int) -> dict[int, int]:
    try:
        payload = _load_json_payload(content)
        scores = _scores_from_json_payload(payload, offset, batch_len)
        if scores:
            return scores
    except Exception:
        pass
    return _scores_from_natural_language(content, offset, batch_len)


def _default_batch_result(
    batch_number: int,
    offset: int,
    batch: list[Chunk],
    error: str,
    *,
    default_score: int = 3,
    started: float | None = None,
) -> _BatchScoreResult:
    started_at = started if started is not None else time.monotonic()
    return _BatchScoreResult(
        batch_number=batch_number,
        offset=offset,
        input_count=len(batch),
        scores={offset + local_index: default_score for local_index in range(len(batch))},
        latency_ms=int((time.monotonic() - started_at) * 1000),
        error=error,
    )


def _quality_metrics(
    token_usage: TokenUsage,
    batch_results: list[_BatchScoreResult],
    key_pool: ApiKeyPool,
    batch_size: int,
    max_workers: int,
) -> dict[str, int]:
    metrics = token_usage.to_metrics("qualityLlm")
    metrics["qualityLlmBatchSize"] = batch_size
    metrics["qualityLlmBatchCount"] = len(batch_results)
    metrics["qualityLlmMaxConcurrency"] = max_workers
    metrics["qualityLlmBatchFailures"] = sum(1 for result in batch_results if result.error)
    key_stats = key_pool.stats()
    metrics["qualityLlmKeyPoolSize"] = key_stats["size"]
    metrics["qualityLlmKeyThrottleCount"] = key_stats["throttleCount"]
    metrics["qualityLlmKeyRetryCount"] = key_stats["retryCount"]
    metrics["qualityLlmKeyCooldownCount"] = key_stats["cooldownCount"]
    for alias, values in key_stats["usage"].items():
        for metric in ("calls", "successes", "failures", "throttles", "totalMs"):
            metrics[f"qualityLlmKey.{alias}.{metric}"] = int(values.get(metric, 0))
    for result in batch_results:
        prefix = f"qualityLlmBatch.{result.batch_number}"
        metrics[f"{prefix}.requests"] = result.token_usage.requests
        metrics[f"{prefix}.promptTokens"] = result.token_usage.prompt_tokens
        metrics[f"{prefix}.completionTokens"] = result.token_usage.completion_tokens
        metrics[f"{prefix}.totalTokens"] = result.token_usage.total_tokens
        metrics[f"{prefix}.latencyMs"] = result.latency_ms
        metrics[f"{prefix}.inputCount"] = result.input_count
        metrics[f"{prefix}.error"] = 1 if result.error else 0
    return metrics
