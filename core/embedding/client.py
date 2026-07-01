from __future__ import annotations

import os
import json
import re
import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import openai

from core.http_client import create_openai_client
from core.llm_usage import ThreadSafeTokenUsage


_DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"
_DEFAULT_EMBEDDING_DIMENSIONS = 1024
_DEFAULT_BATCH_SIZE = 10
_DEFAULT_MAX_CONCURRENCY = 10
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_KEY_RETRIES = 1
_DEFAULT_KEY_COOLDOWN_SECONDS = 30
_DEFAULT_QUERY_CACHE_TTL_SECONDS = 1800
_DEFAULT_QUERY_CACHE_MAX_SIZE = 512
_MAX_API_KEY_POOL_SIZE = 20


@dataclass
class EmbeddingRun:
    embeddings: list[list[float]]
    metrics: dict[str, int]


class _QueryEmbeddingCache:
    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, ttl_seconds: int) -> list[float] | None:
        if ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            created_at, embedding = item
            if now - created_at > ttl_seconds:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return list(embedding)

    def set(self, key: str, embedding: list[float], max_size: int) -> None:
        if max_size <= 0:
            return
        with self._lock:
            self._items[key] = (time.monotonic(), list(embedding))
            self._items.move_to_end(key)
            while len(self._items) > max_size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_QUERY_EMBEDDING_CACHE = _QueryEmbeddingCache()


def get_embedding_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> openai.OpenAI:
    """Create an OpenAI-compatible embedding client.

    Priority order:
      1. Explicit args (api_key, base_url)
      2. LLM_API_KEY / LLM_BASE_URL env vars  (generic)
      3. DASHSCOPE_API_KEY with DashScope base_url
      4. OPENAI_API_KEY with default OpenAI base_url
    """
    key = (
        api_key
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    url = (
        base_url
        or os.environ.get("LLM_BASE_URL")
        or (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if os.environ.get("DASHSCOPE_API_KEY")
            else None
        )
    )

    if not key:
        raise ValueError(
            "No API key found. Set LLM_API_KEY (or DASHSCOPE_API_KEY / OPENAI_API_KEY) "
            "in your .env file."
        )

    kwargs: dict = {"api_key": key}
    if url:
        kwargs["base_url"] = url

    return create_openai_client(**kwargs)


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _parse_api_key_pool(primary_key: str, pool_value: str) -> list[str]:
    keys: list[str] = []

    def add_key(value: str | None) -> None:
        key = (value or "").strip()
        if key and key not in keys and len(keys) < _MAX_API_KEY_POOL_SIZE:
            keys.append(key)

    add_key(primary_key)
    raw = (pool_value or "").strip()
    if not raw:
        return keys

    if raw.startswith("["):
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                for value in values:
                    add_key(str(value))
                return keys
        except Exception:
            pass

    for part in re.split(r"[\s,;]+", raw):
        add_key(part)
    return keys


def _is_throttle_error(error: str) -> bool:
    text = (error or "").lower()
    if not text:
        return False
    markers = (
        "429",
        "rate limit",
        "ratelimit",
        "too many requests",
        "throttl",
        "quota",
        "qps",
        "限流",
        "请求过多",
        "配额",
    )
    return any(marker in text for marker in markers)


@dataclass(frozen=True)
class _EmbeddingKeyLease:
    key: str
    alias: str


class _EmbeddingKeyPool:
    def __init__(self, keys: list[str], cooldown_seconds: int) -> None:
        self._keys = list(keys)
        self._aliases = {key: f"embedding-key-{index + 1}" for index, key in enumerate(self._keys)}
        self._index_by_key = {key: index for index, key in enumerate(self._keys)}
        self._inflight: Counter[str] = Counter()
        self._cooldown_until: dict[str, float] = {}
        self._usage_by_key = {
            key: {
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "throttles": 0,
                "totalMs": 0,
            }
            for key in self._keys
        }
        self._cursor = 0
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._throttle_count = 0
        self._retry_count = 0
        self._cooldown_count = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self._keys)

    def acquire(self, exclude_keys: set[str] | None = None) -> _EmbeddingKeyLease | None:
        exclude = exclude_keys or set()
        with self._lock:
            if not self._keys:
                return None
            now = time.monotonic()
            key = self._select_key(now, exclude, respect_cooldown=True)
            if key is None:
                key = self._select_key(now, exclude, respect_cooldown=False)
            if key is None:
                return None
            self._inflight[key] += 1
            self._cursor = (self._index_by_key[key] + 1) % len(self._keys)
            return _EmbeddingKeyLease(key=key, alias=self._aliases[key])

    def _select_key(self, now: float, exclude: set[str], *, respect_cooldown: bool) -> str | None:
        best_key: str | None = None
        best_score: tuple[int, int] | None = None
        count = len(self._keys)
        for offset in range(count):
            key = self._keys[(self._cursor + offset) % count]
            if key in exclude:
                continue
            if respect_cooldown and self._cooldown_until.get(key, 0.0) > now:
                continue
            score = (self._inflight[key], offset)
            if best_score is None or score < best_score:
                best_key = key
                best_score = score
        return best_key

    def release(self, lease: _EmbeddingKeyLease, elapsed_ms: int, *, success: bool, throttle: bool = False) -> None:
        with self._lock:
            self._inflight[lease.key] = max(0, self._inflight[lease.key] - 1)
            usage = self._usage_by_key[lease.key]
            usage["calls"] += 1
            usage["totalMs"] += max(0, elapsed_ms)
            if success:
                usage["successes"] += 1
                return
            usage["failures"] += 1
            if throttle:
                usage["throttles"] += 1
                self._throttle_count += 1
                if self._cooldown_seconds > 0:
                    self._cooldown_until[lease.key] = time.monotonic() + self._cooldown_seconds
                    self._cooldown_count += 1

    def record_retry(self) -> None:
        with self._lock:
            self._retry_count += 1

    def metrics(self) -> dict[str, int]:
        with self._lock:
            values: dict[str, int] = {
                "embeddingKeyPoolSize": len(self._keys),
                "embeddingKeyThrottleCount": self._throttle_count,
                "embeddingKeyRetryCount": self._retry_count,
                "embeddingKeyCooldownCount": self._cooldown_count,
            }
            for key in self._keys:
                alias = self._aliases[key]
                usage = self._usage_by_key[key]
                for metric_key, metric_value in usage.items():
                    values[f"embeddingKey.{alias}.{metric_key}"] = int(metric_value)
            return values


def _resolve_key_pool(api_key: Optional[str]) -> _EmbeddingKeyPool | None:
    if api_key:
        keys = [api_key]
    else:
        primary = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        pool_value = os.environ.get("LLM_EMBEDDING_API_KEY_POOL") or os.environ.get("LLM_API_KEY_POOL") or ""
        keys = _parse_api_key_pool(primary, pool_value)
    if not keys:
        return None
    cooldown_seconds = _int_env(
        "LLM_EMBEDDING_KEY_COOLDOWN_SECONDS",
        _DEFAULT_KEY_COOLDOWN_SECONDS,
        minimum=0,
    )
    return _EmbeddingKeyPool(keys, cooldown_seconds)


def _resolve_model(model: Optional[str]) -> str:
    return model or os.environ.get("LLM_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL


def _resolve_batch_size(batch_size: int) -> int:
    resolved = batch_size or _int_env("LLM_EMBEDDING_BATCH_SIZE", _DEFAULT_BATCH_SIZE, minimum=1)
    return max(resolved, 1)


def _resolve_max_concurrency(max_concurrency: int | None) -> int:
    if max_concurrency is not None and max_concurrency > 0:
        return max_concurrency
    return _int_env("LLM_EMBEDDING_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY, minimum=1)


def _resolve_max_retries(max_retries: int | None) -> int:
    if max_retries is not None and max_retries >= 0:
        return max_retries
    return _int_env("LLM_EMBEDDING_MAX_RETRIES", _DEFAULT_MAX_RETRIES, minimum=0)


def _resolve_key_retries() -> int:
    return _int_env("LLM_EMBEDDING_KEY_RETRIES", _DEFAULT_KEY_RETRIES, minimum=0)


def _embed_batch(
    client: openai.OpenAI,
    batch: list[str],
    model: str,
    dimensions: int,
    token_usage: ThreadSafeTokenUsage | None = None,
) -> list[list[float]]:
    response = client.embeddings.create(
        model=model,
        input=batch,
        dimensions=dimensions,
    )
    if token_usage is not None:
        token_usage.add_response(response)
    embeddings = [item.embedding for item in response.data]
    if len(embeddings) != len(batch):
        raise RuntimeError(
            f"Embedding API returned {len(embeddings)} vectors for batch of {len(batch)} texts"
        )
    return embeddings


def _embed_batch_with_retry(
    client: openai.OpenAI,
    batch_index: int,
    offset: int,
    batch: list[str],
    model: str,
    dimensions: int,
    max_retries: int,
    token_usage: ThreadSafeTokenUsage | None = None,
) -> tuple[int, list[list[float]], int]:
    attempts = 0
    while True:
        try:
            return offset, _embed_batch(client, batch, model, dimensions, token_usage), attempts
        except Exception as exc:
            if attempts >= max_retries:
                end = offset + len(batch) - 1
                raise RuntimeError(
                    f"Embedding batch {batch_index} failed after {attempts + 1} attempts "
                    f"(offset {offset}-{end}): {exc}"
                ) from exc
            attempts += 1
            time.sleep(min(0.25 * attempts, 1.0))


def _embed_batch_with_key_pool(
    pool: _EmbeddingKeyPool,
    clients_by_key: dict[str, openai.OpenAI],
    client_lock: threading.Lock,
    batch_index: int,
    offset: int,
    batch: list[str],
    model: str,
    dimensions: int,
    max_retries: int,
    key_retries: int,
    base_url: Optional[str],
    token_usage: ThreadSafeTokenUsage | None = None,
) -> tuple[int, list[list[float]], int, int]:
    attempts = 0
    total_key_retries = 0
    excluded_keys: set[str] = set()
    while True:
        lease = pool.acquire(excluded_keys)
        if lease is None:
            raise RuntimeError("Embedding key pool has no available key")
        with client_lock:
            client = clients_by_key.get(lease.key)
            if client is None:
                client = get_embedding_client(api_key=lease.key, base_url=base_url)
                clients_by_key[lease.key] = client
        call_started = time.perf_counter()
        try:
            embeddings = _embed_batch(client, batch, model, dimensions, token_usage)
            pool.release(
                lease,
                int((time.perf_counter() - call_started) * 1000),
                success=True,
            )
            return offset, embeddings, attempts, total_key_retries
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - call_started) * 1000)
            throttle = _is_throttle_error(str(exc))
            pool.release(lease, elapsed_ms, success=False, throttle=throttle)
            if throttle and total_key_retries < key_retries:
                total_key_retries += 1
                pool.record_retry()
                excluded_keys.add(lease.key)
                continue
            if attempts >= max_retries:
                end = offset + len(batch) - 1
                raise RuntimeError(
                    f"Embedding batch {batch_index} failed after {attempts + 1} attempts "
                    f"(offset {offset}-{end}): {exc}"
                ) from exc
            attempts += 1
            excluded_keys.clear()
            time.sleep(min(0.25 * attempts, 1.0))


def embed_texts_with_metrics(
    texts: list[str],
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    batch_size: int = 0,
    dimensions: int = _DEFAULT_EMBEDDING_DIMENSIONS,
    max_concurrency: int | None = None,
    max_retries: int | None = None,
) -> EmbeddingRun:
    """Embed texts with ordered concurrent batching and lightweight metrics."""
    started_at = time.perf_counter()
    resolved_model = _resolve_model(model)
    resolved_batch = _resolve_batch_size(batch_size)
    resolved_concurrency = _resolve_max_concurrency(max_concurrency)
    resolved_retries = _resolve_max_retries(max_retries)
    batch_count = (len(texts) + resolved_batch - 1) // resolved_batch if texts else 0
    metrics = {
        "batchSize": resolved_batch,
        "batchCount": batch_count,
        "maxConcurrency": resolved_concurrency,
        "retryCount": 0,
        "embeddingWallMs": 0,
        "embeddingKeyPoolSize": 0,
        "embeddingKeyThrottleCount": 0,
        "embeddingKeyRetryCount": 0,
        "embeddingKeyCooldownCount": 0,
    }

    if not texts:
        return EmbeddingRun([], metrics)

    key_pool = _resolve_key_pool(api_key)
    client = None if key_pool is not None else get_embedding_client(api_key=api_key, base_url=base_url)
    clients_by_key: dict[str, openai.OpenAI] = {}
    client_lock = threading.Lock()
    key_retries = _resolve_key_retries()
    batches = [
        (batch_index, offset, texts[offset:offset + resolved_batch])
        for batch_index, offset in enumerate(range(0, len(texts), resolved_batch))
    ]
    results: list[list[float] | None] = [None] * len(texts)
    total_retries = 0
    total_key_retries = 0
    token_usage = ThreadSafeTokenUsage()

    if resolved_concurrency <= 1 or len(batches) <= 1:
        for batch_index, offset, batch in batches:
            if key_pool is not None:
                _offset, embeddings, retries, key_retry_count = _embed_batch_with_key_pool(
                    key_pool,
                    clients_by_key,
                    client_lock,
                    batch_index,
                    offset,
                    batch,
                    resolved_model,
                    dimensions,
                    resolved_retries,
                    key_retries,
                    base_url,
                    token_usage,
                )
                total_key_retries += key_retry_count
            else:
                if client is None:
                    raise RuntimeError("Embedding client initialization failed")
                _offset, embeddings, retries = _embed_batch_with_retry(
                    client,
                    batch_index,
                    offset,
                    batch,
                    resolved_model,
                    dimensions,
                    resolved_retries,
                    token_usage,
                )
            total_retries += retries
            results[_offset:_offset + len(embeddings)] = embeddings
    else:
        worker_count = min(resolved_concurrency, len(batches))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            if key_pool is not None:
                futures = [
                    executor.submit(
                        _embed_batch_with_key_pool,
                        key_pool,
                        clients_by_key,
                        client_lock,
                        batch_index,
                        offset,
                        batch,
                        resolved_model,
                        dimensions,
                        resolved_retries,
                        key_retries,
                        base_url,
                        token_usage,
                    )
                    for batch_index, offset, batch in batches
                ]
            else:
                if client is None:
                    raise RuntimeError("Embedding client initialization failed")
                futures = [
                    executor.submit(
                        _embed_batch_with_retry,
                        client,
                        batch_index,
                        offset,
                        batch,
                        resolved_model,
                        dimensions,
                        resolved_retries,
                        token_usage,
                    )
                    for batch_index, offset, batch in batches
                ]
            for future in as_completed(futures):
                result = future.result()
                if key_pool is not None:
                    offset, embeddings, retries, key_retry_count = result
                    total_key_retries += key_retry_count
                else:
                    offset, embeddings, retries = result
                total_retries += retries
                results[offset:offset + len(embeddings)] = embeddings

    if any(item is None for item in results):
        raise RuntimeError("Embedding result alignment failed: at least one vector slot is empty")

    metrics["retryCount"] = total_retries
    metrics["embeddingWallMs"] = int((time.perf_counter() - started_at) * 1000)
    metrics.update(token_usage.to_metrics("embedding"))
    if key_pool is not None:
        metrics.update(key_pool.metrics())
        metrics["embeddingKeyRetryCount"] = max(metrics["embeddingKeyRetryCount"], total_key_retries)
    return EmbeddingRun([item for item in results if item is not None], metrics)


def embed_texts(
    texts: list[str],
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    batch_size: int = 0,
    dimensions: int = _DEFAULT_EMBEDDING_DIMENSIONS,
) -> list[list[float]]:
    """Embed a list of texts with automatic batching."""
    return embed_texts_with_metrics(
        texts,
        model=model,
        api_key=api_key,
        base_url=base_url,
        batch_size=batch_size,
        dimensions=dimensions,
    ).embeddings


def _query_cache_key(
    query: str,
    model: str,
    base_url: str | None,
    dimensions: int,
) -> str:
    normalized_query = " ".join((query or "").strip().split()).lower()
    provider_marker = base_url or os.environ.get("LLM_BASE_URL") or ("dashscope" if os.environ.get("DASHSCOPE_API_KEY") else "default")
    return f"{provider_marker}|{model}|{dimensions}|{normalized_query}"


def embed_query_cached(
    query: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    dimensions: int = _DEFAULT_EMBEDDING_DIMENSIONS,
) -> tuple[list[float], bool]:
    """Embed a query using an in-process TTL cache. Returns (vector, cache_hit)."""
    resolved_model = _resolve_model(model)
    ttl_seconds = _int_env("RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS", _DEFAULT_QUERY_CACHE_TTL_SECONDS, minimum=0)
    max_size = _int_env("RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE", _DEFAULT_QUERY_CACHE_MAX_SIZE, minimum=0)
    key = _query_cache_key(query, resolved_model, base_url, dimensions)

    cached = _QUERY_EMBEDDING_CACHE.get(key, ttl_seconds)
    if cached is not None:
        return cached, True

    embedding = embed_texts(
        [query],
        model=resolved_model,
        api_key=api_key,
        base_url=base_url,
        batch_size=1,
        dimensions=dimensions,
    )[0]
    _QUERY_EMBEDDING_CACHE.set(key, embedding, max_size)
    return embedding, False


def clear_query_embedding_cache() -> None:
    _QUERY_EMBEDDING_CACHE.clear()
