from __future__ import annotations

import threading
import time

from core.embedding.client import (
    clear_query_embedding_cache,
    embed_query_cached,
    embed_texts,
    embed_texts_with_metrics,
    _parse_api_key_pool,
)


class _EmbeddingItem:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _EmbeddingResponse:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.data = [_EmbeddingItem(embedding) for embedding in embeddings]
        self.usage = type(
            "_Usage",
            (),
            {
                "prompt_tokens": len(embeddings) * 2,
                "completion_tokens": 0,
                "total_tokens": len(embeddings) * 2,
            },
        )()


class _FakeEmbeddings:
    def __init__(self, fail_first_for: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_first_for = fail_first_for
        self.failed: set[str] = set()
        self.lock = threading.Lock()

    def create(self, *, model: str, input: list[str], dimensions: int):
        del model, dimensions
        if input and input[0] == "slow":
            time.sleep(0.03)
        with self.lock:
            self.calls.append(list(input))
            if self.fail_first_for in input and self.fail_first_for not in self.failed:
                self.failed.add(self.fail_first_for or "")
                raise RuntimeError("temporary failure")
        return _EmbeddingResponse([[0.0 if text == "slow" else float(text.split("-")[-1])] for text in input])


class _FakeClient:
    def __init__(self, fail_first_for: str | None = None) -> None:
        self.embeddings = _FakeEmbeddings(fail_first_for=fail_first_for)


def test_embed_texts_uses_default_batch_size_and_preserves_order(monkeypatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr("core.embedding.client.get_embedding_client", lambda **_kwargs: client)
    monkeypatch.delenv("LLM_EMBEDDING_BATCH_SIZE", raising=False)
    monkeypatch.setenv("LLM_EMBEDDING_MAX_CONCURRENCY", "4")

    texts = [f"text-{index}" for index in range(23)]
    embeddings = embed_texts(texts)

    assert embeddings == [[float(index)] for index in range(23)]
    assert [len(call) for call in client.embeddings.calls] == [10, 10, 3]


def test_embed_texts_concurrent_batches_fill_original_slots(monkeypatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr("core.embedding.client.get_embedding_client", lambda **_kwargs: client)

    run = embed_texts_with_metrics(
        ["slow", "text-1", "text-2", "text-3"],
        batch_size=1,
        max_concurrency=4,
    )

    assert run.embeddings == [[float(index)] for index in range(4)]
    assert run.metrics["batchSize"] == 1
    assert run.metrics["batchCount"] == 4
    assert run.metrics["maxConcurrency"] == 4
    assert run.metrics["embeddingRequests"] == 4
    assert run.metrics["embeddingPromptTokens"] == 8
    assert run.metrics["embeddingTotalTokens"] == 8


def test_embed_texts_retries_failed_batch(monkeypatch) -> None:
    client = _FakeClient(fail_first_for="text-1")
    monkeypatch.setattr("core.embedding.client.get_embedding_client", lambda **_kwargs: client)

    run = embed_texts_with_metrics(
        ["text-0", "text-1", "text-2"],
        batch_size=1,
        max_concurrency=1,
        max_retries=1,
    )

    assert run.embeddings == [[0.0], [1.0], [2.0]]
    assert run.metrics["retryCount"] == 1


def test_embedding_key_pool_falls_back_to_llm_key_pool(monkeypatch) -> None:
    clients: dict[str, _FakeClient] = {}

    def _client_factory(**kwargs):
        key = kwargs.get("api_key")
        clients.setdefault(key, _FakeClient())
        return clients[key]

    monkeypatch.setattr("core.embedding.client.get_embedding_client", _client_factory)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_EMBEDDING_API_KEY_POOL", raising=False)
    monkeypatch.setenv("LLM_API_KEY_POOL", "key-a,key-b")

    run = embed_texts_with_metrics(
        ["text-0", "text-1", "text-2", "text-3"],
        batch_size=1,
        max_concurrency=2,
    )

    assert run.embeddings == [[0.0], [1.0], [2.0], [3.0]]
    assert set(clients) == {"key-a", "key-b"}
    assert run.metrics["embeddingKeyPoolSize"] == 2
    assert run.metrics["embeddingKey.embedding-key-1.calls"] > 0
    assert run.metrics["embeddingKey.embedding-key-2.calls"] > 0


def test_embedding_key_pool_prefers_embedding_specific_pool(monkeypatch) -> None:
    seen_keys: list[str] = []

    def _client_factory(**kwargs):
        seen_keys.append(kwargs.get("api_key"))
        return _FakeClient()

    monkeypatch.setattr("core.embedding.client.get_embedding_client", _client_factory)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY_POOL", "shared-a,shared-b")
    monkeypatch.setenv("LLM_EMBEDDING_API_KEY_POOL", "embed-a")

    run = embed_texts_with_metrics(["text-0"], batch_size=1, max_concurrency=1)

    assert run.embeddings == [[0.0]]
    assert seen_keys == ["embed-a"]
    assert run.metrics["embeddingKeyPoolSize"] == 1


def test_embedding_key_pool_is_limited_to_20_entries() -> None:
    pool = ",".join(f"key-{index}" for index in range(25))

    keys = _parse_api_key_pool("primary", pool)

    assert len(keys) == 20
    assert keys[0] == "primary"
    assert keys[-1] == "key-18"


def test_embedding_key_pool_retries_throttled_key(monkeypatch) -> None:
    clients: dict[str, _FakeClient] = {}

    def _client_factory(**kwargs):
        key = kwargs.get("api_key")
        fail_first_for = "text-0" if key == "key-a" else None
        clients.setdefault(key, _FakeClient(fail_first_for=fail_first_for))
        return clients[key]

    monkeypatch.setattr("core.embedding.client.get_embedding_client", _client_factory)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_EMBEDDING_API_KEY_POOL", "key-a,key-b")
    monkeypatch.setenv("LLM_EMBEDDING_KEY_RETRIES", "1")
    monkeypatch.setenv("LLM_EMBEDDING_KEY_COOLDOWN_SECONDS", "30")

    original_create = _FakeEmbeddings.create

    def _create_with_throttle(self, *, model: str, input: list[str], dimensions: int):
        try:
            return original_create(self, model=model, input=input, dimensions=dimensions)
        except RuntimeError as exc:
            raise RuntimeError("429 rate limit") from exc

    monkeypatch.setattr(_FakeEmbeddings, "create", _create_with_throttle)

    run = embed_texts_with_metrics(["text-0"], batch_size=1, max_concurrency=1)

    assert run.embeddings == [[0.0]]
    assert set(clients) == {"key-a", "key-b"}
    assert run.metrics["embeddingKeyThrottleCount"] == 1
    assert run.metrics["embeddingKeyRetryCount"] == 1
    assert run.metrics["embeddingKeyCooldownCount"] == 1
    assert run.metrics["embeddingKey.embedding-key-1.throttles"] == 1


def test_query_embedding_cache_avoids_second_api_call(monkeypatch) -> None:
    client = _FakeClient()
    clear_query_embedding_cache()
    monkeypatch.setattr("core.embedding.client.get_embedding_client", lambda **_kwargs: client)
    monkeypatch.setenv("RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE", "16")

    first, first_hit = embed_query_cached("text-1")
    second, second_hit = embed_query_cached(" text-1 ")

    assert first == [1.0]
    assert second == [1.0]
    assert first_hit is False
    assert second_hit is True
    assert len(client.embeddings.calls) == 1


def test_query_embedding_cache_can_be_disabled(monkeypatch) -> None:
    client = _FakeClient()
    clear_query_embedding_cache()
    monkeypatch.setattr("core.embedding.client.get_embedding_client", lambda **_kwargs: client)
    monkeypatch.setenv("RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS", "0")

    embed_query_cached("text-1")
    _vector, cache_hit = embed_query_cached("text-1")

    assert cache_hit is False
    assert len(client.embeddings.calls) == 2
