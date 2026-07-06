"""
Embedding 向量化客户端模块
===========================

本模块提供了文本向量化（Embedding）功能，是 RAG（检索增强生成）系统的核心组件之一。

## 什么是 Embedding（向量化）？

Embedding 是将文本转换为高维向量的过程。向量是一个浮点数数组（如 [0.1, -0.3, 0.5, ...]），
它在向量空间中表示文本的语义信息。语义相近的文本，其向量在空间中的距离也更近。

例如：
- "猫是一种宠物" 和 "狗是常见的宠物动物" → 向量距离近（语义相似）
- "猫是一种宠物" 和 "量子力学研究微观粒子" → 向量距离远（语义不同）

## Embedding 在 RAG 中的作用

1. **文档向量化**：将知识库中的文档切片转换为向量并存储
2. **查询向量化**：将用户问题转换为向量
3. **相似度检索**：通过向量距离计算，找到与问题最相关的文档切片

向量相似度计算通常使用余弦相似度或点积，距离越近表示语义越相似。

## 批处理机制

为了提高效率，本模块支持批处理：
- 将大量文本分成小批次（默认每批 10 条）
- 支持并发处理多个批次（默认最大并发 10）
- 失败自动重试（默认最多重试 2 次）

## 多 API 密钥池

当使用多个 API 密钥时，模块会：
- 轮询选择可用的密钥
- 自动跳过被限流的密钥
- 冷却期后自动恢复密钥使用

## 支持的 Embedding API

本模块使用 OpenAI 兼容接口，支持多种 Embedding 服务：
- **DashScope（阿里云灵积）**：通过 DASHSCOPE_API_KEY 环境变量配置
- **OpenAI**：通过 OPENAI_API_KEY 环境变量配置
- **自定义服务**：通过 LLM_API_KEY 和 LLM_BASE_URL 配置

默认模型为 text-embedding-v3，向量维度 1024。

## 环境变量配置

- LLM_API_KEY / LLM_BASE_URL：通用配置
- DASHSCOPE_API_KEY：阿里云灵积 API
- OPENAI_API_KEY：OpenAI API
- LLM_EMBEDDING_MODEL：指定 Embedding 模型
- LLM_EMBEDDING_BATCH_SIZE：批处理大小
- LLM_EMBEDDING_MAX_CONCURRENCY：最大并发数
- LLM_EMBEDDING_API_KEY_POOL：多密钥池配置
"""

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


# =============================================================================
# 默认配置常量
# =============================================================================

# 默认 Embedding 模型名称（DashScope 的 text-embedding-v3）
_DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"

# 默认向量维度（1024 维，适合大多数语义检索场景）
_DEFAULT_EMBEDDING_DIMENSIONS = 1024

# 默认批处理大小（每次 API 调用处理的文本条数）
_DEFAULT_BATCH_SIZE = 10

# 默认最大并发数（同时进行的 API 请求数）
_DEFAULT_MAX_CONCURRENCY = 10

# 默认最大重试次数（单个批次失败后的重试次数）
_DEFAULT_MAX_RETRIES = 2

# 默认密钥切换重试次数（遇到限流时尝试切换其他密钥的次数）
_DEFAULT_KEY_RETRIES = 1

# 默认密钥冷却时间（秒）- 被限流的密钥在此时间后才能重新使用
_DEFAULT_KEY_COOLDOWN_SECONDS = 30

# 默认查询缓存 TTL（秒）- 查询向量的缓存有效期
_DEFAULT_QUERY_CACHE_TTL_SECONDS = 1800

# 默认查询缓存最大条目数
_DEFAULT_QUERY_CACHE_MAX_SIZE = 512

# API 密钥池最大容量
_MAX_API_KEY_POOL_SIZE = 20


@dataclass
class EmbeddingRun:
    """Embedding 运行结果。

    Attributes:
        embeddings: 向量列表，每个向量是一个浮点数数组
        metrics: 运行指标，包括批次数、重试次数、耗时等
    """
    embeddings: list[list[float]]
    metrics: dict[str, int]


class _QueryEmbeddingCache:
    """查询向量缓存（LRU + TTL）。

    用于缓存用户查询的向量结果，避免重复计算相同查询的向量。
    使用场景：在对话式 RAG 中，用户可能会问相似的问题，
    缓存可以显著减少 API 调用次数和响应时间。

    特性：
    - LRU 淘汰：当缓存满时，移除最久未使用的条目
    - TTL 过期：条目在指定时间后自动失效
    - 线程安全：使用锁保护并发访问

    Attributes:
        _items: 有序字典，存储 {查询键: (创建时间, 向量)}
        _lock: 线程锁，保证并发安全
    """

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, ttl_seconds: int) -> list[float] | None:
        """从缓存获取向量。

        Args:
            key: 缓存键（由查询文本、模型、维度等生成）
            ttl_seconds: 缓存有效期（秒），0 表示禁用缓存

        Returns:
            向量列表，如果未命中或已过期则返回 None
        """
        if ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            created_at, embedding = item
            # 检查是否过期
            if now - created_at > ttl_seconds:
                self._items.pop(key, None)
                return None
            # 命中缓存，移动到链表尾部（LRU）
            self._items.move_to_end(key)
            return list(embedding)

    def set(self, key: str, embedding: list[float], max_size: int) -> None:
        """将向量存入缓存。

        Args:
            key: 缓存键
            embedding: 向量列表
            max_size: 缓存最大容量，0 表示禁用缓存
        """
        if max_size <= 0:
            return
        with self._lock:
            self._items[key] = (time.monotonic(), list(embedding))
            self._items.move_to_end(key)
            # 超过容量时移除最旧的条目（链表头部）
            while len(self._items) > max_size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        """清空缓存。"""
        with self._lock:
            self._items.clear()


# 全局查询缓存实例（单例模式）
_QUERY_EMBEDDING_CACHE = _QueryEmbeddingCache()


def get_embedding_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> openai.OpenAI:
    """创建 OpenAI 兼容的 Embedding 客户端。

    本函数根据配置自动选择合适的 Embedding 服务提供商，
    所有提供商都使用 OpenAI 兼容的 API 接口。

    API 密钥优先级（从高到低）：
        1. 函数参数显式传入（api_key）
        2. LLM_API_KEY 环境变量（通用配置）
        3. DASHSCOPE_API_KEY 环境变量（阿里云灵积，自动设置 base_url）
        4. OPENAI_API_KEY 环境变量（OpenAI 官方服务）

    Args:
        api_key: API 密钥，如果未提供则从环境变量读取
        base_url: API 基础 URL，如果未提供则根据密钥类型自动推断

    Returns:
        OpenAI 客户端实例

    Raises:
        ValueError: 未找到任何可用的 API 密钥

    Example:
        # 使用 DashScope
        client = get_embedding_client()  # 自动读取 DASHSCOPE_API_KEY

        # 使用自定义服务
        client = get_embedding_client(
            api_key="your-key",
            base_url="https://your-api.com/v1"
        )
    """
    # 按优先级获取 API 密钥
    key = (
        api_key
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )

    # 按优先级获取 Base URL
    url = (
        base_url
        or os.environ.get("LLM_BASE_URL")
        or (
            # 如果使用 DashScope 密钥，自动设置其 base URL
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
    """读取整数类型环境变量。

    Args:
        name: 环境变量名
        default: 默认值
        minimum: 最小值约束

    Returns:
        解析后的整数值，如果解析失败或小于最小值则返回默认值
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _parse_api_key_pool(primary_key: str, pool_value: str) -> list[str]:
    """解析 API 密钥池配置。

    支持两种配置格式：
    1. JSON 数组格式：'["key1", "key2", "key3"]'
    2. 分隔符格式：'key1,key2,key3' 或 'key1;key2;key3'

    Args:
        primary_key: 主密钥（始终作为第一个元素）
        pool_value: 密钥池配置字符串

    Returns:
        去重后的密钥列表（最多 20 个）
    """
    keys: list[str] = []

    def add_key(value: str | None) -> None:
        key = (value or "").strip()
        if key and key not in keys and len(keys) < _MAX_API_KEY_POOL_SIZE:
            keys.append(key)

    # 首先添加主密钥
    add_key(primary_key)
    raw = (pool_value or "").strip()
    if not raw:
        return keys

    # 尝试解析 JSON 数组格式
    if raw.startswith("["):
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                for value in values:
                    add_key(str(value))
                return keys
        except Exception:
            pass

    # 使用分隔符格式（空格、逗号、分号）
    for part in re.split(r"[\s,;]+", raw):
        add_key(part)
    return keys


def _is_throttle_error(error: str) -> bool:
    """检测是否为限流错误。

    Args:
        error: 错误消息字符串

    Returns:
        是否为限流类错误
    """
    text = (error or "").lower()
    if not text:
        return False
    # 限流错误的关键词特征
    markers = (
        "429",                    # HTTP 状态码
        "rate limit",             # 速率限制
        "ratelimit",              # 无空格版本
        "too many requests",       # 请求过多
        "throttl",                # 节流（throttle/throttled）
        "quota",                  # 配额
        "qps",                    # 每秒查询数
        "限流",                    # 中文
        "请求过多",                 # 中文
        "配额",                    # 中文
    )
    return any(marker in text for marker in markers)


@dataclass(frozen=True)
class _EmbeddingKeyLease:
    """API 密钥租约。

    表示从密钥池中租用的单个密钥，用于追踪当前正在使用的密钥。

    Attributes:
        key: API 密钥值
        alias: 密钥别名（用于日志和指标，如 "embedding-key-1"）
    """
    key: str
    alias: str


class _EmbeddingKeyPool:
    """API 密钥池管理器。

    当配置了多个 Embedding API 密钥时，本类负责：
    1. 轮询分配可用的密钥
    2. 跟踪每个密钥的使用状态（在途请求数）
    3. 管理限流冷却期（被限流的密钥暂时不可用）
    4. 记录每个密钥的使用指标

    工作原理：
    - acquire(): 获取一个可用的密钥（优先选择负载最低的）
    - release(): 释放密钥，记录调用结果（成功/失败/限流）
    - 被限流的密钥会进入冷却期，冷却结束后自动恢复

    这种设计可以有效提高吞吐量，避免单个密钥被限流导致整个服务不可用。
    """

    def __init__(self, keys: list[str], cooldown_seconds: int) -> None:
        """初始化密钥池。

        Args:
            keys: API 密钥列表
            cooldown_seconds: 限流冷却时间（秒）
        """
        self._keys = list(keys)
        # 为每个密钥分配易读的别名
        self._aliases = {key: f"embedding-key-{index + 1}" for index, key in enumerate(self._keys)}
        self._index_by_key = {key: index for index, key in enumerate(self._keys)}
        # 在途请求计数（当前正在使用的请求数）
        self._inflight: Counter[str] = Counter()
        # 冷却结束时间（被限流的密钥在此时间后才可用）
        self._cooldown_until: dict[str, float] = {}
        # 每个密钥的使用统计
        self._usage_by_key = {
            key: {
                "calls": 0,       # 总调用次数
                "successes": 0,   # 成功次数
                "failures": 0,    # 失败次数
                "throttles": 0,   # 被限流次数
                "totalMs": 0,     # 总耗时（毫秒）
            }
            for key in self._keys
        }
        self._cursor = 0  # 轮询游标
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._throttle_count = 0   # 总限流次数
        self._retry_count = 0      # 总重试次数
        self._cooldown_count = 0   # 总冷却次数
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """密钥池大小。"""
        return len(self._keys)

    def acquire(self, exclude_keys: set[str] | None = None) -> _EmbeddingKeyLease | None:
        """获取一个可用的密钥租约。

        选择策略：
        1. 优先选择在途请求数最少的密钥（负载均衡）
        2. 跳过冷却中的密钥
        3. 如果所有密钥都在冷却，则忽略冷却限制选择一个

        Args:
            exclude_keys: 要排除的密钥集合（通常是被限流后暂时排除）

        Returns:
            密钥租约，如果无可用密钥则返回 None
        """
        exclude = exclude_keys or set()
        with self._lock:
            if not self._keys:
                return None
            now = time.monotonic()
            # 首先尝试选择不在冷却期的密钥
            key = self._select_key(now, exclude, respect_cooldown=True)
            if key is None:
                # 所有密钥都在冷却，忽略冷却限制
                key = self._select_key(now, exclude, respect_cooldown=False)
            if key is None:
                return None
            # 增加在途计数
            self._inflight[key] += 1
            # 移动游标到下一个位置（轮询）
            self._cursor = (self._index_by_key[key] + 1) % len(self._keys)
            return _EmbeddingKeyLease(key=key, alias=self._aliases[key])

    def _select_key(self, now: float, exclude: set[str], *, respect_cooldown: bool) -> str | None:
        """选择最佳密钥。

        选择标准：
        1. 不在排除列表中
        2. （可选）不在冷却期
        3. 在途请求数最少
        4. 位置最靠前（当在途数相同时）

        Args:
            now: 当前时间（monotonic）
            exclude: 要排除的密钥集合
            respect_cooldown: 是否尊重冷却期

        Returns:
            最佳密钥，如果无满足条件的密钥则返回 None
        """
        best_key: str | None = None
        best_score: tuple[int, int] | None = None
        count = len(self._keys)
        for offset in range(count):
            key = self._keys[(self._cursor + offset) % count]
            if key in exclude:
                continue
            if respect_cooldown and self._cooldown_until.get(key, 0.0) > now:
                continue
            # 评分：(在途请求数, 位置偏移) - 越小越好
            score = (self._inflight[key], offset)
            if best_score is None or score < best_score:
                best_key = key
                best_score = score
        return best_key

    def release(self, lease: _EmbeddingKeyLease, elapsed_ms: int, *, success: bool, throttle: bool = False) -> None:
        """释放密钥租约并记录结果。

        Args:
            lease: 密钥租约
            elapsed_ms: 调用耗时（毫秒）
            success: 是否成功
            throttle: 是否被限流
        """
        with self._lock:
            # 减少在途计数
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
                # 如果配置了冷却时间，将被限流的密钥加入冷却
                if self._cooldown_seconds > 0:
                    self._cooldown_until[lease.key] = time.monotonic() + self._cooldown_seconds
                    self._cooldown_count += 1

    def record_retry(self) -> None:
        """记录一次重试。"""
        with self._lock:
            self._retry_count += 1

    def metrics(self) -> dict[str, int]:
        """获取密钥池使用指标。

        Returns:
            包含各种统计指标的字典
        """
        with self._lock:
            values: dict[str, int] = {
                "embeddingKeyPoolSize": len(self._keys),
                "embeddingKeyThrottleCount": self._throttle_count,
                "embeddingKeyRetryCount": self._retry_count,
                "embeddingKeyCooldownCount": self._cooldown_count,
            }
            # 包含每个密钥的详细指标
            for key in self._keys:
                alias = self._aliases[key]
                usage = self._usage_by_key[key]
                for metric_key, metric_value in usage.items():
                    values[f"embeddingKey.{alias}.{metric_key}"] = int(metric_value)
            return values


def _resolve_key_pool(api_key: Optional[str]) -> _EmbeddingKeyPool | None:
    """解析并创建 API 密钥池。

    从函数参数和环境变量中读取密钥配置，创建密钥池实例。

    密钥来源优先级：
    1. 函数参数 api_key（如果提供，则只使用这一个密钥）
    2. 环境变量 LLM_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY（主密钥）
    3. 环境变量 LLM_EMBEDDING_API_KEY_POOL 或 LLM_API_KEY_POOL（密钥池）

    Args:
        api_key: 可选的 API 密钥

    Returns:
        密钥池实例，如果没有任何密钥则返回 None
    """
    if api_key:
        # 如果显式提供了密钥，只使用这一个
        keys = [api_key]
    else:
        # 从环境变量读取主密钥
        primary = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        # 从环境变量读取密钥池配置
        pool_value = os.environ.get("LLM_EMBEDDING_API_KEY_POOL") or os.environ.get("LLM_API_KEY_POOL") or ""
        keys = _parse_api_key_pool(primary, pool_value)
    if not keys:
        return None
    # 读取冷却时间配置
    cooldown_seconds = _int_env(
        "LLM_EMBEDDING_KEY_COOLDOWN_SECONDS",
        _DEFAULT_KEY_COOLDOWN_SECONDS,
        minimum=0,
    )
    return _EmbeddingKeyPool(keys, cooldown_seconds)


def _resolve_model(model: Optional[str]) -> str:
    """解析 Embedding 模型名称。

    Args:
        model: 可选的模型名称

    Returns:
        模型名称，优先使用参数值，否则从环境变量或默认值
    """
    return model or os.environ.get("LLM_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL


def _resolve_batch_size(batch_size: int) -> int:
    """解析批处理大小。

    Args:
        batch_size: 批处理大小（0 表示使用默认值）

    Returns:
        批处理大小，最小为 1
    """
    resolved = batch_size or _int_env("LLM_EMBEDDING_BATCH_SIZE", _DEFAULT_BATCH_SIZE, minimum=1)
    return max(resolved, 1)


def _resolve_max_concurrency(max_concurrency: int | None) -> int:
    """解析最大并发数。

    Args:
        max_concurrency: 最大并发数（None 或 0 表示使用默认值）

    Returns:
        最大并发数，最小为 1
    """
    if max_concurrency is not None and max_concurrency > 0:
        return max_concurrency
    return _int_env("LLM_EMBEDDING_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY, minimum=1)


def _resolve_max_retries(max_retries: int | None) -> int:
    """解析最大重试次数。

    Args:
        max_retries: 最大重试次数（None 表示使用默认值）

    Returns:
        最大重试次数，最小为 0
    """
    if max_retries is not None and max_retries >= 0:
        return max_retries
    return _int_env("LLM_EMBEDDING_MAX_RETRIES", _DEFAULT_MAX_RETRIES, minimum=0)


def _resolve_key_retries() -> int:
    """解析密钥切换重试次数。

    Returns:
        密钥切换重试次数（当被限流时尝试其他密钥的次数）
    """
    return _int_env("LLM_EMBEDDING_KEY_RETRIES", _DEFAULT_KEY_RETRIES, minimum=0)


def _embed_batch(
    client: openai.OpenAI,
    batch: list[str],
    model: str,
    dimensions: int,
    token_usage: ThreadSafeTokenUsage | None = None,
) -> list[list[float]]:
    """调用 Embedding API 处理单个批次。

    这是最底层的 API 调用函数，不包含重试逻辑。

    Args:
        client: OpenAI 客户端
        batch: 文本列表（一批）
        model: 模型名称
        dimensions: 向量维度
        token_usage: Token 使用量统计器（可选）

    Returns:
        向量列表，与输入文本一一对应

    Raises:
        RuntimeError: API 返回的向量数量与输入不匹配
    """
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
    """带重试机制的批次向量化。

    当 API 调用失败时，会进行指数退避重试。

    Args:
        client: OpenAI 客户端
        batch_index: 批次索引（用于错误消息）
        offset: 批次在原始列表中的起始位置
        batch: 文本列表
        model: 模型名称
        dimensions: 向量维度
        max_retries: 最大重试次数
        token_usage: Token 使用量统计器

    Returns:
        元组 (offset, embeddings, attempts)：
        - offset: 起始位置
        - embeddings: 向量列表
        - attempts: 重试次数
    """
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
            # 指数退避：0.25s, 0.5s, 0.75s, ... 最大 1s
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
    """使用密钥池的批次向量化。

    当配置了多个 API 密钥时，使用本函数进行批次处理。
    它会：
    1. 从密钥池获取一个可用的密钥
    2. 使用该密钥创建客户端（或复用已有客户端）
    3. 调用 Embedding API
    4. 如果被限流，尝试切换到其他密钥
    5. 释放密钥并记录结果

    这种设计可以提高整体吞吐量，避免单个密钥被限流导致服务中断。

    Args:
        pool: API 密钥池
        clients_by_key: 密钥到客户端的映射（用于复用客户端）
        client_lock: 客户端访问锁（线程安全）
        batch_index: 批次索引
        offset: 批次起始位置
        batch: 文本列表
        model: 模型名称
        dimensions: 向量维度
        max_retries: 单个密钥的最大重试次数
        key_retries: 密钥切换的最大次数
        base_url: API 基础 URL
        token_usage: Token 使用量统计器

    Returns:
        元组 (offset, embeddings, attempts, key_retry_count)：
        - offset: 起始位置
        - embeddings: 向量列表
        - attempts: 单密钥重试次数
        - key_retry_count: 密钥切换次数
    """
    attempts = 0
    total_key_retries = 0
    excluded_keys: set[str] = set()  # 暂时排除的密钥（通常是被限流后）

    while True:
        # 从密钥池获取一个可用的密钥
        lease = pool.acquire(excluded_keys)
        if lease is None:
            raise RuntimeError("Embedding key pool has no available key")

        # 获取或创建对应的客户端（线程安全）
        with client_lock:
            client = clients_by_key.get(lease.key)
            if client is None:
                client = get_embedding_client(api_key=lease.key, base_url=base_url)
                clients_by_key[lease.key] = client

        call_started = time.perf_counter()
        try:
            embeddings = _embed_batch(client, batch, model, dimensions, token_usage)
            # 成功：释放密钥
            pool.release(
                lease,
                int((time.perf_counter() - call_started) * 1000),
                success=True,
            )
            return offset, embeddings, attempts, total_key_retries
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - call_started) * 1000)
            throttle = _is_throttle_error(str(exc))
            # 释放密钥并记录结果
            pool.release(lease, elapsed_ms, success=False, throttle=throttle)

            # 如果是限流错误且还有密钥切换机会，尝试其他密钥
            if throttle and total_key_retries < key_retries:
                total_key_retries += 1
                pool.record_retry()
                excluded_keys.add(lease.key)  # 暂时排除这个密钥
                continue

            # 检查是否超过重试次数
            if attempts >= max_retries:
                end = offset + len(batch) - 1
                raise RuntimeError(
                    f"Embedding batch {batch_index} failed after {attempts + 1} attempts "
                    f"(offset {offset}-{end}): {exc}"
                ) from exc
            attempts += 1
            excluded_keys.clear()  # 普通重试时清空排除列表
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
    """批量向量化文本并返回详细指标。

    这是本模块的核心函数，实现了高效的批处理和并发向量化。

    ## 工作流程

    1. **参数解析**：从参数或环境变量读取配置
    2. **分批**：将文本列表分成多个批次
    3. **并发处理**：
       - 如果只有 1 个批次或并发数为 1，顺序处理
       - 否则使用线程池并发处理多个批次
    4. **结果组装**：按原始顺序组装各批次的向量结果
    5. **指标收集**：收集耗时、重试次数、Token 使用量等指标

    ## 批处理机制

    批处理是提高 Embedding 效率的关键：
    - 每次 API 调用可以处理多条文本（默认 10 条）
    - 减少 HTTP 请求开销
    - 充分利用 API 的批处理优化

    示例：100 条文本，批大小 10，并发 5
    - 分成 10 个批次
    - 每批 10 条文本
    - 同时处理 5 个批次（并发）
    - 2 轮完成（10 批 / 5 并发）

    ## 多密钥支持

    如果配置了多个 API 密钥（通过 LLM_EMBEDDING_API_KEY_POOL 环境变量）：
    - 自动轮询使用不同密钥
    - 被限流的密钥会进入冷却期
    - 可以切换到其他可用密钥继续处理

    Args:
        texts: 待向量化的文本列表
        model: Embedding 模型名称（默认从环境变量或 text-embedding-v3）
        api_key: API 密钥（可选，优先级最高）
        base_url: API 基础 URL（可选）
        batch_size: 批处理大小（0 表示使用默认值 10）
        dimensions: 向量维度（默认 1024）
        max_concurrency: 最大并发数（None 表示使用默认值 10）
        max_retries: 最大重试次数（None 表示使用默认值 2）

    Returns:
        EmbeddingRun 对象，包含：
        - embeddings: 向量列表，与输入文本一一对应
        - metrics: 运行指标字典

    Raises:
        ValueError: 未找到 API 密钥
        RuntimeError: 向量化失败

    Example:
        >>> texts = ["你好世界", "机器学习很有趣"]
        >>> result = embed_texts_with_metrics(texts)
        >>> print(len(result.embeddings))  # 2
        >>> print(len(result.embeddings[0]))  # 1024 (向量维度)
        >>> print(result.metrics["embeddingWallMs"])  # 耗时（毫秒）
    """
    started_at = time.perf_counter()
    # 解析配置
    resolved_model = _resolve_model(model)
    resolved_batch = _resolve_batch_size(batch_size)
    resolved_concurrency = _resolve_max_concurrency(max_concurrency)
    resolved_retries = _resolve_max_retries(max_retries)
    batch_count = (len(texts) + resolved_batch - 1) // resolved_batch if texts else 0

    # 初始化指标
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

    # 初始化密钥池（如果配置了多个密钥）
    key_pool = _resolve_key_pool(api_key)
    client = None if key_pool is not None else get_embedding_client(api_key=api_key, base_url=base_url)
    clients_by_key: dict[str, openai.OpenAI] = {}
    client_lock = threading.Lock()
    key_retries = _resolve_key_retries()

    # 分批
    batches = [
        (batch_index, offset, texts[offset:offset + resolved_batch])
        for batch_index, offset in enumerate(range(0, len(texts), resolved_batch))
    ]
    results: list[list[float] | None] = [None] * len(texts)
    total_retries = 0
    total_key_retries = 0
    token_usage = ThreadSafeTokenUsage()

    # 并发处理
    if resolved_concurrency <= 1 or len(batches) <= 1:
        # 单线程顺序处理
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
        # 多线程并发处理
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
            # 收集结果
            for future in as_completed(futures):
                result = future.result()
                if key_pool is not None:
                    offset, embeddings, retries, key_retry_count = result
                    total_key_retries += key_retry_count
                else:
                    offset, embeddings, retries = result
                total_retries += retries
                results[offset:offset + len(embeddings)] = embeddings

    # 验证结果完整性
    if any(item is None for item in results):
        raise RuntimeError("Embedding result alignment failed: at least one vector slot is empty")

    # 收集指标
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
    """批量向量化文本（简化版接口）。

    这是 embed_texts_with_metrics 的简化包装，只返回向量列表，
    不包含运行指标。适用于不需要监控指标的简单场景。

    Args:
        texts: 待向量化的文本列表
        model: Embedding 模型名称（可选）
        api_key: API 密钥（可选）
        base_url: API 基础 URL（可选）
        batch_size: 批处理大小（0 表示使用默认值）
        dimensions: 向量维度（默认 1024）

    Returns:
        向量列表，每个向量是一个浮点数数组，与输入文本一一对应

    Example:
        >>> texts = ["这是第一段文本", "这是第二段文本"]
        >>> vectors = embed_texts(texts)
        >>> print(len(vectors))  # 2
        >>> print(len(vectors[0]))  # 1024
    """
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
    """生成查询缓存键。

    缓存键由以下部分组成：
    - Provider 标识（从 base_url 或环境变量推断）
    - 模型名称
    - 向量维度
    - 归一化的查询文本（小写、去多余空格）

    这样可以确保相同的查询在不同参数配置下不会混淆。

    Args:
        query: 查询文本
        model: 模型名称
        base_url: API 基础 URL
        dimensions: 向量维度

    Returns:
        缓存键字符串
    """
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
    """向量化查询（带缓存）。

    专门用于处理用户查询的向量化，内置 LRU + TTL 缓存机制。

    ## 为什么需要查询缓存？

    在对话式 RAG 场景中：
    1. 用户可能会重复或相似的问题
    2. 相同的查询文本应该返回相同的向量（确定性）
    3. 缓存可以显著减少 API 调用和响应延迟

    ## 缓存配置

    通过环境变量配置：
    - RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS: 缓存有效期（默认 1800 秒 = 30 分钟）
    - RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE: 最大缓存条目数（默认 512）

    Args:
        query: 查询文本
        model: Embedding 模型名称（可选）
        api_key: API 密钥（可选）
        base_url: API 基础 URL（可选）
        dimensions: 向量维度（默认 1024）

    Returns:
        元组 (vector, cache_hit)：
        - vector: 向量（浮点数数组）
        - cache_hit: 是否命中缓存

    Example:
        >>> vector, hit = embed_query_cached("什么是机器学习？")
        >>> print(hit)  # False (首次查询)
        >>> vector2, hit2 = embed_query_cached("什么是机器学习？")
        >>> print(hit2)  # True (命中缓存)
    """
    resolved_model = _resolve_model(model)
    ttl_seconds = _int_env("RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS", _DEFAULT_QUERY_CACHE_TTL_SECONDS, minimum=0)
    max_size = _int_env("RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE", _DEFAULT_QUERY_CACHE_MAX_SIZE, minimum=0)
    key = _query_cache_key(query, resolved_model, base_url, dimensions)

    # 尝试从缓存获取
    cached = _QUERY_EMBEDDING_CACHE.get(key, ttl_seconds)
    if cached is not None:
        return cached, True

    # 缓存未命中，调用 API
    embedding = embed_texts(
        [query],
        model=resolved_model,
        api_key=api_key,
        base_url=base_url,
        batch_size=1,
        dimensions=dimensions,
    )[0]

    # 存入缓存
    _QUERY_EMBEDDING_CACHE.set(key, embedding, max_size)
    return embedding, False


def clear_query_embedding_cache() -> None:
    """清空查询向量缓存。

    在以下场景可能需要调用此函数：
    - 切换了 Embedding 模型
    - 切换了 API 服务商
    - 测试或调试时需要清除缓存
    """
    _QUERY_EMBEDDING_CACHE.clear()
