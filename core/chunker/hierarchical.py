"""
层次化三层切片策略（Hierarchical Chunking Strategy）

【策略概述】
本策略专为教材类 PDF 文档设计，采用三层结构组织知识内容：
- Layer 1 (parent 层): 章节/标题作为上下文容器，提供宏观导航
- Layer 2 (child 层): 知识点切片，用于向量化嵌入和语义检索
- Layer 3 (enhanced 层): LLM 生成的摘要/问题，用于提升检索效果

【三层结构详解】
1. Parent 层（父级容器）
   - 来源：文档中的标题块（BlockType.TITLE）
   - 作用：作为章节容器，承载 child 切片的上下文信息
   - 用途：在检索时提供完整的章节背景，帮助用户理解知识点的位置

2. Child 层（子级切片）
   - 来源：文本块、表格块、图片块
   - 特点：按语义边界切分，长度可控（默认 600 字符）
   - 用途：向量化嵌入，作为语义检索的基本单元
   - 关联：每个 child 记录其 parent_id，形成层级关系

3. Enhanced 层（增强切片）
   - 来源：由 LLM 对 child 切片进行智能增强
   - 内容：摘要 + 检索问题 + 术语解释
   - 用途：提升检索召回率和准确性

【增强场景】
本策略针对三种特殊内容进行 LLM 增强：
1. 纯图片块 (Image-only blocks):
   - 使用视觉语言模型（VL）描述图片内容和教学意义
   - 若无 VL 模型，则退回文本 LLM 根据图片说明推断内容

2. 纯表格块 (Table-only blocks):
   - LLM 总结表格主题、解释专业术语、提炼数据规律
   - 帮助用户快速理解表格的核心信息

3. 片段内容 (Fragment content):
   - 检测缺少独立上下文的文本片段（如"如上所述"、"见图 X"）
   - LLM 补充背景知识、解释专业术语、生成检索问题

【核心特性】
- 并发增强：支持多线程并行调用 LLM，提升处理速度
- API Key 池：支持多 Key 轮换和限流冷却，提高吞吐量
- 智能调度：按任务类型（text/table/image）分配不同并发数
- 进度回调：支持实时进度通知，适合长时间处理任务

【使用示例】
    strategy = HierarchicalStrategy(
        child_max_chars=600,           # child 切片最大字符数
        enable_enhanced=True,          # 启用 LLM 增强
        enable_image_enhanced=True,    # 启用图片增强
        enable_table_enhanced=True,    # 启用表格增强
        llm_model="qwen-plus",         # LLM 模型名称
    )
    chunks = strategy.chunk(blocks)

【环境变量配置】
- LLM_API_KEY / LLM_BASE_URL: 文本 LLM 配置
- VL_API_KEY / VL_MODEL: 视觉语言模型配置
- LLM_API_KEY_POOL: 多 Key 池（JSON 数组或逗号分隔）
- HIERARCHICAL_ENHANCE_MODE: 增强模式（serial/parallel_ordered）
- HIERARCHICAL_TEXT_ENHANCE_WORKERS: 文本增强并发数
- HIERARCHICAL_TABLE_ENHANCE_WORKERS: 表格增强并发数
- HIERARCHICAL_IMAGE_ENHANCE_WORKERS: 图片增强并发数
"""
from __future__ import annotations

import base64
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from core.chunker.enhanced_parser import parse_enhanced_response
from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_usage import extract_response_usage
from core.models.content_block import BlockType, ContentBlock, Chunk

from .base import ChunkingStrategy, register_strategy

# ============================================================================
# 正则表达式常量：用于识别特殊内容类型
# ============================================================================

# 流程类关键词：用于识别操作步骤、方法论等内容，这些内容需要更大的切片长度
_PROCEDURE_KEYWORDS = re.compile(
    r"步骤|操作|流程|方法|过程|程序|要点|注意事项|procedure|step"
)

# 片段指示词：用于检测缺少独立上下文的文本片段
# 这些词通常出现在引用前文内容的地方，如"如上所述"、"见图 X"等
_FRAGMENT_INDICATORS = re.compile(
    r"如上所述|如前所述|见图|参见|如图所示|上述|前述|以上内容|下面将|如下所示|详见|其中[，,]|该[方法算法模型公式]|此[方法处理操作]"
)

# ============================================================================
# 全局缓存与配置
# ============================================================================

# 线程本地存储：缓存 OpenAI 客户端实例，避免重复创建
_CLIENT_LOCAL = threading.local()

# API Key 池最大容量限制
_MAX_API_KEY_POOL_SIZE = 20


# ============================================================================
# 内容类型检测函数
# ============================================================================

def _is_procedure_block(text: str) -> bool:
    """
    检测文本块是否为流程/步骤类内容。

    这类内容通常包含操作步骤、方法论等，需要更长的切片长度
    以保持步骤的完整性，避免步骤被切断。

    Args:
        text: 文本内容

    Returns:
        bool: 如果前 200 字符内包含流程关键词，返回 True
    """
    return bool(_PROCEDURE_KEYWORDS.search(text[:200]))


def _is_fragment_content(text: str) -> bool:
    """
    检测文本块是否为缺少独立上下文的片段。

    片段内容通常包含引用前文的表述（如"如上所述"、"见图 X"），
    这些内容单独拿出来难以理解，需要 LLM 补充背景知识。

    Args:
        text: 文本内容

    Returns:
        bool: 如果文本过短（< 80 字符）或包含片段指示词，返回 True
    """
    if len(text) < 80:
        return True
    if _FRAGMENT_INDICATORS.search(text[:400]):
        return True
    return False


# ============================================================================
# 文件与文本处理函数
# ============================================================================

def _load_image_base64(image_path: str) -> Optional[str]:
    """
    加载图片文件并返回 Base64 编码字符串。

    用于视觉语言模型的图片输入，VL 模型需要图片以 Base64 编码传入。

    Args:
        image_path: 图片文件路径

    Returns:
        Base64 编码字符串，如果加载失败返回 None
    """
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def _split_text_by_sentences(text: str, max_chars: int) -> list[str]:
    """
    按句子边界切分文本，确保切片长度不超过 max_chars。

    切分策略：
    1. 按优先级查找分隔符：句号 > 分号 > 感叹号 > 问号 > 段落 > 换行
    2. 在 max_chars 范围内查找最后一个分隔符
    3. 如果找不到合适的分隔符，则硬切

    Args:
        text: 待切分的文本
        max_chars: 每个切片的最大字符数

    Returns:
        切片列表，每个切片长度不超过 max_chars
    """
    # 分隔符优先级列表（从高到低）
    separators = ["。", "；", "！", "？", ".\n", "\n\n", "\n"]
    chunks: list[str] = []
    remaining = text

    while len(remaining) > max_chars:
        cut_pos = -1
        # 尝试在 max_chars 范围内找到最佳分隔符
        for sep in separators:
            pos = remaining.rfind(sep, 0, max_chars)
            # 确保分隔符位置合理（至少在文本的前 1/3 处）
            if pos > max_chars // 3:
                cut_pos = pos + len(sep)
                break
        # 找不到合适的分隔符，硬切
        if cut_pos <= 0:
            cut_pos = max_chars
        chunks.append(remaining[:cut_pos].strip())
        remaining = remaining[cut_pos:].strip()

    if remaining.strip():
        chunks.append(remaining.strip())
    return chunks


# ============================================================================
# 环境变量解析工具
# ============================================================================

def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """
    从环境变量读取整数值。

    Args:
        name: 环境变量名
        default: 默认值
        minimum: 最小值（结果不会小于此值）

    Returns:
        解析后的整数值，如果解析失败返回默认值
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """
    从环境变量读取布尔值。

    支持的 true 值: "1", "true", "yes", "on"
    支持的 false 值: "0", "false", "no", "off"

    Args:
        name: 环境变量名
        default: 默认值

    Returns:
        解析后的布尔值，如果解析失败返回默认值
    """
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_api_key_pool(primary_key: str, pool_value: str) -> list[str]:
    """
    解析 API Key 池配置。

    支持两种格式：
    1. JSON 数组格式: ["key1", "key2", "key3"]
    2. 逗号/空格/分号分隔格式: key1,key2 key3;key4

    Args:
        primary_key: 主 Key（总是第一个加入列表）
        pool_value: Key 池配置字符串

    Returns:
        去重后的 Key 列表（最多 _MAX_API_KEY_POOL_SIZE 个）
    """
    keys: list[str] = []

    def add_key(value: str | None) -> None:
        key = (value or "").strip()
        if key and key not in keys and len(keys) < _MAX_API_KEY_POOL_SIZE:
            keys.append(key)

    # 首先添加主 Key
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

    # 解析分隔符格式（逗号、空格、分号）
    for part in re.split(r"[\s,;]+", raw):
        add_key(part)
    return keys


def _is_throttle_error(error: str) -> bool:
    """
    检测错误信息是否为限流错误。

    限流错误通常需要切换 API Key 或等待冷却后重试。

    Args:
        error: 错误信息字符串

    Returns:
        bool: 如果是限流错误返回 True
    """
    text = (error or "").lower()
    if not text:
        return False
    # 限流相关的关键词（中英文）
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


# ============================================================================
# API Key 池管理
# ============================================================================

@dataclass(frozen=True)
class _ApiKeyLease:
    """
    API Key 租约数据类。

    表示从 Key 池中获取的一个 Key 使用权。

    Attributes:
        key: 实际的 API Key 值
        alias: Key 的别名（用于日志和统计，如 "llm-key-1"）
    """
    key: str
    alias: str


class _ApiKeyPool:
    """
    API Key 池管理器。

    功能：
    1. 多 Key 轮换：避免单 Key 限流，提高并发吞吐量
    2. 限流冷却：检测到限流后自动冷却该 Key，切换到其他 Key
    3. 负载均衡：优先选择并发数低的 Key
    4. 统计追踪：记录每个 Key 的调用次数、成功/失败数、总耗时等

    使用方式：
        pool = _ApiKeyPool("llm-key", ["key1", "key2", "key3"])
        lease = pool.acquire()  # 获取一个 Key
        try:
            result = call_api(lease.key)
        finally:
            pool.release(lease)  # 释放 Key
            pool.record_attempt(lease, elapsed_ms, success=True, throttled=False)
    """

    def __init__(self, name: str, keys: list[str], cooldown_seconds: int = 30):
        """
        初始化 Key 池。

        Args:
            name: 池名称（用于日志）
            keys: API Key 列表
            cooldown_seconds: 限流冷却时间（秒），默认 30 秒
        """
        self.name = name
        self._keys = list(keys)
        # 为每个 Key 生成别名
        self._aliases = {key: f"{name}-{index + 1}" for index, key in enumerate(self._keys)}
        self._index_by_key = {key: index for index, key in enumerate(self._keys)}
        # 并发计数：记录每个 Key 当前正在使用的次数
        self._inflight: Counter[str] = Counter()
        # 冷却时间：记录每个 Key 的冷却结束时间
        self._cooldown_until: dict[str, float] = {}
        # 轮询游标：实现 Round-Robin 选择
        self._cursor = 0
        self._cooldown_seconds = max(0, cooldown_seconds)
        # 统计信息
        self._throttle_count = 0
        self._retry_count = 0
        self._cooldown_count = 0
        self._usage_by_key = {
            key: {
                "calls": 0,        # 调用次数
                "successes": 0,    # 成功次数
                "failures": 0,     # 失败次数
                "throttles": 0,    # 限流次数
                "totalMs": 0,      # 总耗时（毫秒）
            }
            for key in self._keys
        }
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """返回池中 Key 的数量"""
        return len(self._keys)

    @property
    def keys(self) -> list[str]:
        """返回所有 Key 的列表（副本）"""
        return list(self._keys)

    def acquire(self, exclude_keys: set[str] | None = None) -> _ApiKeyLease | None:
        """
        获取一个 API Key 租约。

        选择策略：
        1. 优先选择未在冷却中且并发数最低的 Key
        2. 如果所有 Key 都在冷却中，则忽略冷却状态强制选择
        3. 支持排除指定的 Key（用于重试时避开失败的 Key）

        Args:
            exclude_keys: 要排除的 Key 集合

        Returns:
            _ApiKeyLease 或 None（如果池为空）
        """
        exclude = exclude_keys or set()
        with self._lock:
            if not self._keys:
                return None
            now = time.monotonic()
            # 第一次尝试：尊重冷却状态
            key = self._select_key(now, exclude, respect_cooldown=True)
            if key is None:
                # 所有 Key 都在冷却，强制选择
                key = self._select_key(now, exclude, respect_cooldown=False)
            if key is None:
                return None
            # 增加并发计数
            self._inflight[key] += 1
            # 更新游标（实现 Round-Robin）
            self._cursor = (self._index_by_key[key] + 1) % len(self._keys)
            return _ApiKeyLease(key=key, alias=self._aliases[key])

    def _select_key(self, now: float, exclude: set[str], *, respect_cooldown: bool) -> str | None:
        """
        内部方法：选择最佳 Key。

        评分策略：(并发数, 游标偏移量)
        - 并发数低的优先
        - 游标偏移量小的优先（Round-Robin）

        Args:
            now: 当前时间（用于检查冷却状态）
            exclude: 要排除的 Key 集合
            respect_cooldown: 是否尊重冷却状态

        Returns:
            选中的 Key 或 None
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
            score = (self._inflight[key], offset)
            if best_score is None or score < best_score:
                best_key = key
                best_score = score
        return best_key

    def release(self, lease: _ApiKeyLease | None) -> None:
        """
        释放 API Key 租约。

        必须在完成 API 调用后调用，以减少并发计数。

        Args:
            lease: 要释放的租约
        """
        if lease is None:
            return
        with self._lock:
            if self._inflight[lease.key] > 0:
                self._inflight[lease.key] -= 1

    def record_attempt(
        self,
        lease: _ApiKeyLease | None,
        elapsed_ms: int,
        *,
        success: bool,
        throttled: bool,
    ) -> None:
        """
        记录 API 调用结果。

        Args:
            lease: 使用的租约
            elapsed_ms: 调用耗时（毫秒）
            success: 是否成功
            throttled: 是否被限流
        """
        if lease is None:
            return
        with self._lock:
            usage = self._usage_by_key.setdefault(
                lease.key,
                {
                    "calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "throttles": 0,
                    "totalMs": 0,
                },
            )
            usage["calls"] += 1
            usage["totalMs"] += max(0, int(elapsed_ms))
            if success:
                usage["successes"] += 1
            else:
                usage["failures"] += 1
            if throttled:
                usage["throttles"] += 1

    def mark_throttled(self, lease: _ApiKeyLease | None) -> None:
        """
        标记 Key 为限流状态。

        将该 Key 放入冷却期，期间不会被优先选择。

        Args:
            lease: 被限流的租约
        """
        if lease is None:
            return
        with self._lock:
            self._throttle_count += 1
            self._cooldown_count += 1
            self._cooldown_until[lease.key] = time.monotonic() + self._cooldown_seconds

    def record_retry(self) -> None:
        """记录重试次数"""
        with self._lock:
            self._retry_count += 1

    def stats(self) -> dict[str, Any]:
        """
        获取池统计信息。

        Returns:
            包含池大小、限流次数、重试次数和每个 Key 使用情况的字典
        """
        with self._lock:
            return {
                "size": len(self._keys),
                "throttleCount": self._throttle_count,
                "retryCount": self._retry_count,
                "cooldownCount": self._cooldown_count,
                "usage": {
                    self._aliases.get(key, f"{self.name}-unknown"): dict(self._usage_by_key.get(key, {}))
                    for key in self._keys
                },
            }


# ============================================================================
# OpenAI 客户端缓存
# ============================================================================

def _get_cached_openai_client(api_key: str, base_url: str):
    """
    获取缓存的 OpenAI 客户端实例。

    使用线程本地存储缓存客户端，避免每次调用都创建新实例。
    可通过环境变量 HIERARCHICAL_REUSE_LLM_CLIENTS=false 禁用缓存。

    Args:
        api_key: API Key
        base_url: API 基础 URL

    Returns:
        OpenAI 客户端实例
    """
    if not _env_bool("HIERARCHICAL_REUSE_LLM_CLIENTS", True):
        return create_openai_client(api_key=api_key, base_url=base_url)

    # 从线程本地存储获取缓存
    cache = getattr(_CLIENT_LOCAL, "openai_clients", None)
    if cache is None:
        cache = {}
        setattr(_CLIENT_LOCAL, "openai_clients", cache)

    key = (api_key, base_url or "")
    client = cache.get(key)
    if client is None:
        client = create_openai_client(api_key=api_key, base_url=base_url)
        cache[key] = client
    return client


# ============================================================================
# 数据类：切片计划与增强任务
# ============================================================================

@dataclass
class _PlanEntry:
    """
    切片计划条目。

    在切片过程中，每个条目要么是一个 Chunk，要么是一个增强任务引用。

    Attributes:
        chunk: 已创建的 Chunk 对象（parent/child）
        task_index: 增强任务的索引（用于引用增强结果）
        index_policy: 索引分配策略（"current" 或 "text_group"）
        group_id: 文本组 ID（用于同一组内的连续编号）
        group_offset: 在组内的偏移量
    """
    chunk: Chunk | None = None
    task_index: int | None = None
    index_policy: str = "current"
    group_id: int | None = None
    group_offset: int = 0


@dataclass
class _EnhancementTask:
    """
    LLM 增强任务。

    表示一个需要 LLM 处理的增强任务。

    Attributes:
        task_index: 任务索引（用于关联结果）
        child: 待增强的 child 切片
        kind: 任务类型（text/table/image/fragment）
    """
    task_index: int
    child: Chunk
    kind: str


@dataclass
class _EnhancementResult:
    """
    LLM 增强结果。

    Attributes:
        task_index: 关联的任务索引
        child: 原始的 child 切片
        kind: 任务类型
        text: LLM 生成的增强文本
        tokens: 总 token 数
        err: 错误信息（如果有）
        prefix: 输出前缀（如 "[LLM增强]"）
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        elapsed_ms: 耗时（毫秒）
    """
    task_index: int
    child: Chunk
    kind: str
    text: Optional[str]
    tokens: int
    err: str
    prefix: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: int = 0


# LLM 调用结果的类型别名
_LlmCallResult = tuple[Optional[str], int, str] | tuple[Optional[str], int, int, int, str]


def _normalize_llm_result(result: _LlmCallResult) -> tuple[Optional[str], int, int, int, str]:
    """
    规范化 LLM 调用结果。

    LLM 函数可能返回 3 元组或 5 元组，此函数统一转换为 5 元组格式。

    Args:
        result: LLM 调用结果（3 元组或 5 元组）

    Returns:
        5 元组：(text, tokens, prompt_tokens, completion_tokens, error)
    """
    if len(result) >= 5:
        text, tokens, prompt_tokens, completion_tokens, err = result[:5]
        return text, int(tokens or 0), int(prompt_tokens or 0), int(completion_tokens or 0), str(err or "")
    text, tokens, err = result
    return text, int(tokens or 0), 0, 0, str(err or "")


# ============================================================================
# LLM 增强内容生成函数
# ============================================================================

def _generate_enhanced_text(content: str, title: str, model: str,
                            base_url: str, api_key: str,
                            system_prompt: str = "") -> tuple[Optional[str], int, int, int, str]:
    """
    生成检索增强文本（摘要 + 检索问题）。

    针对普通文本切片，生成：
    1. 一句话摘要：概括切片的核心内容
    2. 3 个检索问题：模拟用户可能的查询方式

    这些增强内容可以提升检索的召回率和准确性。

    Args:
        content: 切片内容
        title: 所属章节标题
        model: LLM 模型名称
        base_url: API 基础 URL
        api_key: API Key
        system_prompt: 系统提示词（可选）

    Returns:
        5 元组：(增强文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
    """
    if not api_key:
        return None, 0, 0, 0, "未配置 API Key"
    try:
        client = _get_cached_openai_client(api_key=api_key, base_url=base_url)
        prompt = (
            f"你是一个教材知识点摘要助手。请为以下内容生成一段增强检索文本，"
            f"包含：1) 一句话摘要；2) 3个可能的检索问题。\n\n"
            f"所属章节：{title}\n"
            f"原文内容：{content[:800]}\n\n"
            f"请直接输出增强文本（不要标题、不要编号前缀）："
        )
        messages: list[Any] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=300,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content
        text = raw.strip() if raw else ""
        usage = extract_response_usage(resp)
        return text, usage["total_tokens"], usage["prompt_tokens"], usage["completion_tokens"], ""
    except Exception as e:
        return None, 0, 0, 0, str(e)


def _call_llm(
    messages: list[Any],
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int = 400,
    system_prompt: str = "",
) -> tuple[Optional[str], int, int, int, str]:
    """
    通用的 LLM 调用辅助函数。

    封装了 OpenAI API 调用的通用逻辑，包括客户端获取、消息构建、错误处理等。

    Args:
        messages: 消息列表
        model: 模型名称
        base_url: API 基础 URL
        api_key: API Key
        max_tokens: 最大输出 token 数
        system_prompt: 系统提示词（可选）

    Returns:
        5 元组：(输出文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
    """
    if not api_key:
        return None, 0, 0, 0, "未配置 API Key"
    try:
        client = _get_cached_openai_client(api_key=api_key, base_url=base_url)
        final_messages: list[Any] = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        final_messages.extend(messages)
        resp = client.chat.completions.create(
            model=model,
            messages=final_messages,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content
        text = raw.strip() if raw else ""
        usage = extract_response_usage(resp)
        return text, usage["total_tokens"], usage["prompt_tokens"], usage["completion_tokens"], ""
    except Exception as e:
        return None, 0, 0, 0, str(e)


# ============================================================================
# 特殊内容增强函数
# ============================================================================

def _generate_image_description(
    image_path: Optional[str],
    alt_text: str,
    title: str,
    vl_model: str,
    vl_base_url: str,
    vl_api_key: str,
    text_model: str,
    text_base_url: str,
    text_api_key: str,
    system_prompt: str = "",
) -> tuple[Optional[str], int, int, int, str]:
    """
    生成图片描述增强文本。

    针对图片切片的增强策略：
    1. 如果配置了视觉语言模型（VL）且图片可加载：
       - 使用 VL 模型直接分析图片内容
       - 描述图片主要内容、教学意义、关键标注

    2. 否则使用文本 LLM 作为降级方案：
       - 根据图片的说明文字（alt_text）推断内容
       - 生成推断描述、教学作用、检索问题

    Args:
        image_path: 图片文件路径
        alt_text: 图片的说明文字
        title: 所属章节标题
        vl_model: 视觉语言模型名称
        vl_base_url: VL API 基础 URL
        vl_api_key: VL API Key
        text_model: 文本 LLM 模型名称（降级方案）
        text_base_url: 文本 LLM API 基础 URL
        text_api_key: 文本 LLM API Key
        system_prompt: 系统提示词（可选）

    Returns:
        5 元组：(描述文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
    """
    # 尝试加载图片为 Base64
    b64 = _load_image_base64(image_path) if image_path else None

    # 如果有 VL 模型且图片可加载，使用 VL 模型
    if b64 and vl_model and vl_api_key:
        ext = (image_path or "").rsplit(".", 1)[-1].lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
        prompt_text = (
            f"这是教材《{title}》中的一张图片。请完成以下任务：\n"
            f"1) 用2-3句话描述图片的主要内容；\n"
            f"2) 说明该图片在教学中的作用或意义；\n"
            f"3) 列出图片中出现的关键概念或标注（如有）。\n"
            f"请直接输出描述，不要加标题或编号前缀。"
        )
        # 构建多模态消息：图片 + 文本
        messages: list[dict] = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ],
        }]
        return _call_llm(messages, vl_model, vl_base_url, vl_api_key, max_tokens=400,
                         system_prompt=system_prompt)

    # 降级方案：使用文本 LLM 根据 alt_text 推断
    fallback_prompt = (
        f"你是一个教材内容助手。以下是教材《{title}》中一张图片的说明文字：\n"
        f"图片说明：{alt_text or '（无说明）'}\n\n"
        f"请根据上下文推断该图片可能展示的内容，并生成：\n"
        f"1) 图片内容的推断描述（2-3句）；\n"
        f"2) 该图片在教学中的可能作用；\n"
        f"3) 2个与该图片相关的检索问题。\n"
        f"请直接输出，不要加标题或编号前缀。"
    )
    return _call_llm(
        [{"role": "user", "content": fallback_prompt}],
        text_model, text_base_url, text_api_key, max_tokens=350,
        system_prompt=system_prompt,
    )


def _generate_table_summary(
    table_content: str,
    title: str,
    model: str,
    base_url: str,
    api_key: str,
    system_prompt: str = "",
) -> tuple[Optional[str], int, int, int, str]:
    """
    生成表格摘要增强文本。

    针对表格切片的增强策略：
    1. 概括表格主题和核心内容
    2. 解释表格中的专业术语或缩写
    3. 提炼最重要的数据规律或结论

    Args:
        table_content: 表格内容（HTML 或纯文本）
        title: 所属章节标题
        model: LLM 模型名称
        base_url: API 基础 URL
        api_key: API Key
        system_prompt: 系统提示词（可选）

    Returns:
        5 元组：(摘要文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
    """
    prompt = (
        f"你是一个教材内容分析助手。以下是教材《{title}》中的一个表格：\n\n"
        f"{table_content[:1200]}\n\n"
        f"请完成以下任务：\n"
        f"1) 用1-2句话概括表格的主题和核心内容；\n"
        f"2) 解释表格中出现的专业术语或缩写（如有，列出3个以内）；\n"
        f"3) 提炼表格中最重要的1-2条数据规律或结论。\n"
        f"请直接输出分析内容，不要加标题或编号前缀。"
    )
    return _call_llm(
        [{"role": "user", "content": prompt}],
        model, base_url, api_key, max_tokens=400,
        system_prompt=system_prompt,
    )


def _generate_fragment_enhancement(
    content: str,
    title: str,
    model: str,
    base_url: str,
    api_key: str,
    system_prompt: str = "",
) -> tuple[Optional[str], int, int, int, str]:
    """
    生成片段内容增强文本。

    针对缺少独立上下文的文本片段的增强策略：
    1. 补充该片段所需的背景知识（帮助读者理解上下文）
    2. 识别并解释片段中的专业术语
    3. 生成有助于检索该知识点的问题

    片段内容通常包含引用前文的表述，如"如上所述"、"见图 X"等。

    Args:
        content: 片段内容
        title: 所属章节标题
        model: LLM 模型名称
        base_url: API 基础 URL
        api_key: API Key
        system_prompt: 系统提示词（可选）

    Returns:
        5 元组：(增强文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
    """
    prompt = (
        f"你是一个教材知识点补充助手。以下内容来自教材《{title}》，"
        f"可能是一个片段，缺少完整的上下文背景：\n\n"
        f"片段内容：{content[:800]}\n\n"
        f"请完成以下任务：\n"
        f"1) 补充该片段所需的背景知识（1-2句，帮助读者理解上下文）；\n"
        f"2) 识别并解释片段中的专业术语（如有，列出2-3个）；\n"
        f"3) 生成2个有助于检索该知识点的问题。\n"
        f"请直接输出增强内容，不要加标题或编号前缀。"
    )
    return _call_llm(
        [{"role": "user", "content": prompt}],
        model, base_url, api_key, max_tokens=400,
        system_prompt=system_prompt,
    )


# ============================================================================
# 层次化切片策略主类
# ============================================================================

@register_strategy
class HierarchicalStrategy(ChunkingStrategy):
    """
    层次化三层切片策略。

    这是针对教材类 PDF 文档设计的高级切片策略，通过三层结构组织知识内容：
    - Parent 层：章节标题作为上下文容器
    - Child 层：知识点切片用于向量化嵌入
    - Enhanced 层：LLM 生成的增强文本用于提升检索效果

    核心特性：
    1. 层级关联：每个 child 切片记录 parent_id，形成层级导航
    2. LLM 增强：对 child 切片进行智能增强（摘要、问题、术语解释）
    3. 多模态支持：支持图片（VL 模型）、表格、文本的增强
    4. 并发处理：多线程并行调用 LLM，提升处理速度
    5. API Key 池：多 Key 轮换和限流冷却，提高吞吐量

    切片流程：
    1. 扫描内容块，识别标题、表格、图片、文本
    2. 为每个标题创建 parent 切片
    3. 将文本块缓冲后按语义边界切分为 child 切片
    4. 表格和图片各自生成独立的 child 切片
    5. 并发调用 LLM 为 child 切片生成增强内容
    6. 合并所有切片（parent + child + enhanced）

    Attributes:
        name: 策略名称，固定为 "hierarchical"
        child_max_chars: child 切片的最大字符数
        enable_enhanced: 是否启用 LLM 增强
        enable_image_enhanced: 是否启用图片增强
        enable_table_enhanced: 是否启用表格增强
        enable_fragment_enhanced: 是否启用片段增强
        llm_model: 文本 LLM 模型名称
        vl_model: 视觉语言模型名称
        total_tokens: 累计使用的 token 数
        llm_errors: LLM 调用错误列表
        progress_callback: 进度回调函数
        last_timings: 最近一次处理的时间统计
    """

    name = "hierarchical"

    def __init__(
        self,
        child_max_chars: int = 600,
        enable_enhanced: bool = True,
        enable_image_enhanced: bool = True,
        enable_table_enhanced: bool = True,
        enable_fragment_enhanced: bool = True,
        llm_model: str = "",
        llm_base_url: str = "",
        llm_api_key: str = "",
        vl_model: str = "",
        vl_base_url: str = "",
        vl_api_key: str = "",
        enhanced_system_prompt: str = "",
        progress_callback: Callable[[str], None] | None = None,
    ):
        """
        初始化层次化切片策略。

        Args:
            child_max_chars: child 切片的最大字符数，默认 600
                - 流程类内容会自动加倍
                - 太小会导致语义不完整，太大会影响检索精度

            enable_enhanced: 是否启用 LLM 增强，默认 True
                - 开启后会为每个 child 切片生成增强内容
                - 需要消耗 LLM API 调用

            enable_image_enhanced: 是否启用图片增强，默认 True
                - 使用 VL 模型描述图片内容
                - 若无 VL 模型则降级为文本 LLM

            enable_table_enhanced: 是否启用表格增强，默认 True
                - 生成表格摘要、术语解释、数据规律

            enable_fragment_enhanced: 是否启用片段增强，默认 True
                - 为缺少上下文的片段补充背景知识

            llm_model: 文本 LLM 模型名称，默认从环境变量读取
            llm_base_url: 文本 LLM API 基础 URL
            llm_api_key: 文本 LLM API Key

            vl_model: 视觉语言模型名称（用于图片理解）
            vl_base_url: VL API 基础 URL
            vl_api_key: VL API Key

            enhanced_system_prompt: 增强任务的系统提示词
            progress_callback: 进度回调函数，用于实时报告处理进度
        """
        self.child_max_chars = child_max_chars
        self.enable_enhanced = enable_enhanced
        self.enable_image_enhanced = enable_image_enhanced
        self.enable_table_enhanced = enable_table_enhanced
        self.enable_fragment_enhanced = enable_fragment_enhanced
        self.enhanced_system_prompt = (
            resolve_llm_param(enhanced_system_prompt, "enhance_system_prompt", ["LLM_ENHANCE_SYSTEM_PROMPT"])
            or ""
        )

        # 文本 LLM 配置（优先使用参数，其次环境变量）
        self.llm_model = llm_model or os.getenv("LLM_CLEANER_MODEL", "qwen-plus")
        self.llm_base_url = llm_base_url or os.getenv(
            "LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")
        )

        # 视觉语言模型配置（用于图片理解，若无配置则降级为文本 LLM）
        self.vl_model = vl_model or os.getenv("VL_MODEL", "")
        self.vl_base_url = vl_base_url or os.getenv(
            "VL_BASE_URL",
            os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        explicit_vl_key = bool(vl_api_key or os.getenv("VL_API_KEY", "").strip())
        self.vl_api_key = vl_api_key or os.getenv(
            "VL_API_KEY", os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
        )

        # 解析 API Key 池配置（支持多 Key 轮换）
        llm_pool_value = os.getenv("LLM_API_KEY_POOL", "")
        vl_pool_value = os.getenv("VL_API_KEY_POOL", "")
        if not vl_pool_value and not explicit_vl_key:
            vl_pool_value = llm_pool_value

        # Key 池配置
        key_cooldown_seconds = _env_int("HIERARCHICAL_KEY_COOLDOWN_SECONDS", 30, minimum=0)
        self.enhance_key_retries = _env_int("HIERARCHICAL_ENHANCE_KEY_RETRIES", 1, minimum=0)

        # 创建 Key 池
        self.llm_key_pool = _ApiKeyPool(
            "llm-key",
            _parse_api_key_pool(self.llm_api_key, llm_pool_value),
            cooldown_seconds=key_cooldown_seconds,
        )
        self.vl_key_pool = _ApiKeyPool(
            "vl-key",
            _parse_api_key_pool(self.vl_api_key, vl_pool_value) if self.vl_model else [],
            cooldown_seconds=key_cooldown_seconds,
        )

        # 统计信息
        self.total_tokens = 0
        self.llm_errors: list[str] = []
        self.progress_callback = progress_callback

        # 并发配置
        self.enhance_mode = os.getenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered").strip().lower()
        if self.enhance_mode not in {"serial", "parallel_ordered"}:
            self.enhance_mode = "parallel_ordered"

        # 各类任务的并发数配置
        self.text_enhance_workers = _env_int("HIERARCHICAL_TEXT_ENHANCE_WORKERS", 16)
        self.table_enhance_workers = _env_int("HIERARCHICAL_TABLE_ENHANCE_WORKERS", 3)
        self.image_enhance_workers = _env_int("HIERARCHICAL_IMAGE_ENHANCE_WORKERS", 4)

        # 计算默认最大并发数
        default_total_workers = self.text_enhance_workers + self.table_enhance_workers + self.image_enhance_workers
        default_max_workers = (
            22
            if (self.text_enhance_workers, self.table_enhance_workers, self.image_enhance_workers) == (16, 3, 4)
            else default_total_workers
        )
        self.max_enhance_workers = _env_int("HIERARCHICAL_ENHANCE_MAX_CONCURRENCY", default_max_workers)

        # 时间统计
        self.last_timings: dict[str, int] = {}

    def _progress(self, message: str) -> None:
        """发送进度通知"""
        if self.progress_callback:
            self.progress_callback(message)

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """
        执行层次化切片。

        这是策略的核心方法，将内容块列表转换为三层切片结构。

        处理流程：
        ┌─────────────────────────────────────────────────────────────┐
        │ 1. 扫描阶段：遍历内容块，识别类型                            │
        │    - 标题块 → 创建 parent 切片                              │
        │    - 表格块 → 创建 child 切片 + 表格增强任务                 │
        │    - 图片块 → 创建 child 切片 + 图片增强任务                 │
        │    - 文本块 → 暂存到缓冲区                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ 2. 切分阶段：将文本缓冲区按语义边界切分为 child 切片          │
        │    - 按句号、分号等分隔符切分                               │
        │    - 控制每个切片长度不超过 child_max_chars                 │
        │    - 为每个 child 创建文本增强任务                          │
        ├─────────────────────────────────────────────────────────────┤
        │ 3. 增强阶段：并发调用 LLM 为 child 切片生成增强内容          │
        │    - 按任务类型（text/table/image）分配不同并发数           │
        │    - 支持 API Key 轮换和限流冷却                            │
        ├─────────────────────────────────────────────────────────────┤
        │ 4. 合并阶段：生成最终切片列表                                │
        │    - parent 切片（章节容器）                                │
        │    - child 切片（知识点）                                   │
        │    - enhanced 切片（LLM 增强内容）                          │
        └─────────────────────────────────────────────────────────────┘

        Args:
            blocks: 内容块列表（来自 PDF 解析器）

        Returns:
            切片列表，包含 parent/child/enhanced 三层切片
        """
        # 重置统计信息
        self.total_tokens = 0
        self.last_timings = {}
        plan_start = time.monotonic()

        # 切片计划：记录所有待创建的切片和增强任务
        entries: list[_PlanEntry] = []
        tasks: list[_EnhancementTask] = []

        # 当前上下文
        current_title = ""              # 当前章节标题
        current_parent: Optional[Chunk] = None  # 当前父级切片

        # 文本缓冲区：累积文本块，等待切分
        buffer: list[ContentBlock] = []
        source = blocks[0].source_file if blocks else ""
        total_blocks = len(blocks)
        group_counter = 0  # 文本组计数器

        self._progress(
            f"切片开始：输入 {total_blocks} 个内容块，增强={self.enable_enhanced}，"
            f"模式={self.enhance_mode}"
        )

        # ----------------------------------------------------------------
        # 内部函数：添加增强任务
        # ----------------------------------------------------------------
        def add_enhancement_task(child: Chunk) -> None:
            """为 child 切片创建增强任务"""
            kind = self._get_enhancement_kind(child)
            task_index = len(tasks)
            tasks.append(_EnhancementTask(task_index=task_index, child=child, kind=kind))
            entries.append(_PlanEntry(task_index=task_index))

        # ----------------------------------------------------------------
        # 内部函数：刷新文本缓冲区
        # ----------------------------------------------------------------
        def flush_buffer():
            """
            将文本缓冲区切分为 child 切片。

            当遇到标题、表格、图片或扫描结束时调用。
            """
            nonlocal buffer, group_counter
            if not buffer:
                return

            first_page = buffer[0].page_idx + 1
            last_page = buffer[-1].page_idx + 1
            self._progress(f"正在切分文本缓冲：{len(buffer)} 个块，页码 P.{first_page}-P.{last_page}")

            # 切分文本缓冲区
            child_chunks = self._make_children(
                buffer, current_parent, source, 0, current_title
            )
            self._progress(f"文本缓冲生成 {len(child_chunks)} 个 child 切片")

            # 为同一批文本切片分配连续编号
            group_id = group_counter
            group_counter += 1
            for offset, c in enumerate(child_chunks):
                entries.append(
                    _PlanEntry(
                        chunk=c,
                        index_policy="text_group",
                        group_id=group_id,
                        group_offset=offset,
                    )
                )
                if self.enable_enhanced:
                    add_enhancement_task(c)
            buffer = []

        # ----------------------------------------------------------------
        # 主循环：扫描内容块
        # ----------------------------------------------------------------
        last_reported_percent = -1
        for block_index, block in enumerate(blocks, start=1):
            # 进度报告（每 10% 报告一次）
            percent = int(block_index * 100 / total_blocks) if total_blocks else 100
            if percent >= last_reported_percent + 10 or block_index == 1 or block_index == total_blocks:
                self._progress(
                    f"正在扫描内容块 {block_index}/{total_blocks} ({percent}%)，"
                    f"类型={block.type.value}，页码 P.{block.page_idx + 1}"
                )
                last_reported_percent = percent

            # ----------------------------------------------------------------
            # 处理标题块：创建 parent 切片
            # ----------------------------------------------------------------
            if block.type == BlockType.TITLE:
                flush_buffer()  # 先刷新之前的文本缓冲
                current_title = block.text.strip()
                self._progress(f"发现标题：P.{block.page_idx + 1} {current_title[:60]}")

                # 创建 parent 切片
                parent = Chunk(
                    content=current_title,
                    source=source,
                    page=block.page_idx,
                    chunk_index=0,
                    strategy=self.name,
                    title=current_title,
                    layer="parent",  # 标记为父级
                )
                entries.append(_PlanEntry(chunk=parent))
                current_parent = parent

            # ----------------------------------------------------------------
            # 处理表格块：创建 child 切片
            # ----------------------------------------------------------------
            elif block.type == BlockType.TABLE:
                flush_buffer()  # 先刷新文本缓冲
                table_content = block.table_html or block.text
                self._progress(f"正在处理表格块：页码 P.{block.page_idx + 1}，字符数 {len(table_content)}")

                # 创建 child 切片
                child = Chunk(
                    content=table_content,
                    source=source,
                    page=block.page_idx,
                    chunk_index=0,
                    strategy=self.name,
                    title=current_title,
                    is_table_chunk=True,
                    layer="child",
                    parent_id=current_parent.id if current_parent else None,
                )
                entries.append(_PlanEntry(chunk=child))

                # 添加表格增强任务
                if self.enable_enhanced and self.enable_table_enhanced:
                    add_enhancement_task(child)

            # ----------------------------------------------------------------
            # 处理图片块：创建 child 切片
            # ----------------------------------------------------------------
            elif block.type == BlockType.IMAGE:
                flush_buffer()  # 先刷新文本缓冲
                img_content = block.text or f"[图片 第{block.page_idx+1}页]"
                self._progress(f"正在处理图片块：页码 P.{block.page_idx + 1}，image_path={block.image_path or '无'}")

                # 创建 child 切片
                child = Chunk(
                    content=img_content,
                    source=source,
                    page=block.page_idx,
                    chunk_index=0,
                    strategy=self.name,
                    title=current_title,
                    is_image_chunk=True,
                    image_path=block.image_path,
                    layer="child",
                    parent_id=current_parent.id if current_parent else None,
                )
                entries.append(_PlanEntry(chunk=child))

                # 添加图片增强任务
                if self.enable_enhanced and self.enable_image_enhanced:
                    add_enhancement_task(child)

            # ----------------------------------------------------------------
            # 处理文本块：暂存到缓冲区
            # ----------------------------------------------------------------
            else:
                buffer.append(block)

        # 刷新最后的文本缓冲
        flush_buffer()

        # 记录基础切片耗时
        base_ms = int((time.monotonic() - plan_start) * 1000)
        self.last_timings["chunkBaseMs"] = base_ms
        self._progress(
            f"基础切片完成：{sum(1 for entry in entries if entry.chunk)} 个基础条目，"
            f"{len(tasks)} 个增强任务"
        )

        # ----------------------------------------------------------------
        # 增强阶段：并发调用 LLM
        # ----------------------------------------------------------------
        enhance_start = time.monotonic()
        results = self._execute_enhancement_tasks(tasks)
        enhance_wall_ms = int((time.monotonic() - enhance_start) * 1000)
        self._record_enhancement_timings(results, enhance_wall_ms)

        # ----------------------------------------------------------------
        # 合并阶段：生成最终切片列表
        # ----------------------------------------------------------------
        render_start = time.monotonic()
        chunks = self._render_plan(entries, results)
        self.last_timings["mergeChunksMs"] = int((time.monotonic() - render_start) * 1000)
        self.last_timings["chunkTotalMs"] = int((time.monotonic() - plan_start) * 1000)

        self._progress(
            f"切片完成：输出 {len(chunks)} 个切片，LLM tokens={self.total_tokens}，"
            f"增强墙钟耗时={enhance_wall_ms}ms"
        )
        return chunks

    def _get_enhancement_kind(self, child: Chunk) -> str:
        """
        判断切片的增强类型。

        Args:
            child: 待增强的 child 切片

        Returns:
            增强类型字符串：
            - "image": 图片切片
            - "table": 表格切片
            - "fragment": 缺少上下文的片段
            - "text": 普通文本切片
        """
        if child.is_image_chunk:
            return "image"
        if child.is_table_chunk:
            return "table"
        if self.enable_fragment_enhanced and _is_fragment_content(child.content):
            return "fragment"
        return "text"

    def _call_with_key_pool(
        self,
        pool: _ApiKeyPool,
        fallback_key: str,
        caller: Callable[[str], _LlmCallResult],
    ) -> tuple[Optional[str], int, int, int, str]:
        """
        通过 API Key 池调用 LLM。

        功能：
        1. 从 Key 池获取可用 Key
        2. 执行 LLM 调用
        3. 检测限流并触发冷却
        4. 支持失败后重试其他 Key

        Args:
            pool: API Key 池
            fallback_key: 降级 Key（当池为空时使用）
            caller: LLM 调用函数，接收 api_key 参数

        Returns:
            5 元组：(输出文本, 总 token 数, 输入 token 数, 输出 token 数, 错误信息)
        """
        if pool.size <= 0:
            return _normalize_llm_result(caller(fallback_key))

        attempted: set[str] = set()
        max_attempts = max(1, min(pool.size, self.enhance_key_retries + 1))
        last_result: tuple[Optional[str], int, int, int, str] = (None, 0, 0, 0, "")

        for attempt in range(max_attempts):
            # 从池中获取 Key（排除已尝试的）
            lease = pool.acquire(exclude_keys=attempted)
            if lease is None:
                break

            attempt_started = time.monotonic()
            try:
                result = _normalize_llm_result(caller(lease.key))
            finally:
                pool.release(lease)

            text, _tokens, _prompt_tokens, _completion_tokens, err = result
            throttled = bool(err and not text and _is_throttle_error(err))

            # 记录调用结果
            pool.record_attempt(
                lease,
                int((time.monotonic() - attempt_started) * 1000),
                success=bool(text) or not err,
                throttled=throttled,
            )
            last_result = result

            # 如果被限流，标记该 Key 并尝试其他 Key
            if throttled:
                pool.mark_throttled(lease)
                attempted.add(lease.key)
                if attempt < max_attempts - 1:
                    pool.record_retry()
                    continue

            return result

        return last_result

    def _can_use_vl_for_image(self, child: Chunk) -> bool:
        """
        检查是否可以使用 VL 模型处理图片。

        条件：
        1. 切片有图片路径
        2. 配置了 VL 模型
        3. VL Key 池有可用的 Key
        4. 图片可以成功加载为 Base64

        Args:
            child: 图片切片

        Returns:
            bool: 是否可以使用 VL 模型
        """
        if not (child.image_path and self.vl_model and self.vl_key_pool.size > 0):
            return False
        return bool(_load_image_base64(child.image_path))

    def _run_enhancement_task(self, task: _EnhancementTask) -> _EnhancementResult:
        """
        执行单个增强任务。

        根据任务类型调用相应的 LLM 函数：
        - image: 图片描述生成
        - table: 表格摘要生成
        - fragment: 片段内容增强
        - text: 普通文本增强

        Args:
            task: 增强任务

        Returns:
            增强结果对象
        """
        started = time.monotonic()
        child = task.child

        # ------------------------------------------------------------
        # 图片增强任务
        # ------------------------------------------------------------
        if task.kind == "image":
            if self._can_use_vl_for_image(child):
                # 使用 VL 模型
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.vl_key_pool,
                    self.vl_api_key,
                    lambda api_key: _generate_image_description(
                        image_path=child.image_path,
                        alt_text=child.content,
                        title=child.title or "",
                        vl_model=self.vl_model,
                        vl_base_url=self.vl_base_url,
                        vl_api_key=api_key,
                        text_model=self.llm_model,
                        text_base_url=self.llm_base_url,
                        text_api_key=self.llm_api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            else:
                # 降级为文本 LLM
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.llm_key_pool,
                    self.llm_api_key,
                    lambda api_key: _generate_image_description(
                        image_path=child.image_path,
                        alt_text=child.content,
                        title=child.title or "",
                        vl_model=self.vl_model,
                        vl_base_url=self.vl_base_url,
                        vl_api_key="",  # 强制使用文本 LLM
                        text_model=self.llm_model,
                        text_base_url=self.llm_base_url,
                        text_api_key=api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            prefix = "[图片描述]"

        # ------------------------------------------------------------
        # 表格增强任务
        # ------------------------------------------------------------
        elif task.kind == "table":
            if len(child.content) < 20:
                # 内容过短，跳过增强
                text, tokens, prompt_tokens, completion_tokens, err = None, 0, 0, 0, ""
            else:
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.llm_key_pool,
                    self.llm_api_key,
                    lambda api_key: _generate_table_summary(
                        table_content=child.content,
                        title=child.title or "",
                        model=self.llm_model,
                        base_url=self.llm_base_url,
                        api_key=api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            prefix = "[表格摘要]"

        # ------------------------------------------------------------
        # 片段增强任务
        # ------------------------------------------------------------
        elif task.kind == "fragment":
            if len(child.content) < 20:
                text, tokens, prompt_tokens, completion_tokens, err = None, 0, 0, 0, ""
            else:
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.llm_key_pool,
                    self.llm_api_key,
                    lambda api_key: _generate_fragment_enhancement(
                        content=child.content,
                        title=child.title or "",
                        model=self.llm_model,
                        base_url=self.llm_base_url,
                        api_key=api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            prefix = "[片段增强]"

        # ------------------------------------------------------------
        # 普通文本增强任务
        # ------------------------------------------------------------
        else:
            if len(child.content) < 50:
                # 内容过短，跳过增强
                text, tokens, prompt_tokens, completion_tokens, err = None, 0, 0, 0, ""
            else:
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.llm_key_pool,
                    self.llm_api_key,
                    lambda api_key: _generate_enhanced_text(
                        child.content,
                        child.title or "",
                        self.llm_model,
                        self.llm_base_url,
                        api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            prefix = "[LLM增强]"

        return _EnhancementResult(
            task_index=task.task_index,
            child=child,
            kind=task.kind,
            text=text,
            tokens=tokens,
            err=err,
            prefix=prefix,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def _report_enhancement_progress(
        self,
        completed: int,
        total: int,
        counts: Counter[str],
        failures: int,
        force: bool = False,
    ) -> None:
        """
        报告增强进度。

        每 10% 或首尾时报告一次。

        Args:
            completed: 已完成的任务数
            total: 总任务数
            counts: 各类型任务完成数统计
            failures: 失败任务数
            force: 是否强制报告（忽略进度检查）
        """
        if not total:
            return
        step = max(1, total // 10)
        if not force and completed not in {1, total} and completed % step != 0:
            return
        percent = int(completed * 100 / total)
        self._progress(
            "增强进度："
            f"{completed}/{total} ({percent}%)，"
            f"text={counts.get('text', 0)}，fragment={counts.get('fragment', 0)}，"
            f"table={counts.get('table', 0)}，image={counts.get('image', 0)}，失败={failures}"
        )

    def _execute_enhancement_tasks(self, tasks: list[_EnhancementTask]) -> dict[int, _EnhancementResult]:
        """
        执行所有增强任务。

        支持两种执行模式：
        1. serial: 串行执行，逐个处理任务
        2. parallel_ordered: 并行执行，按任务类型分配不同并发数

        并行执行策略：
        - 文本任务（text/fragment）: 高并发（默认 16）
        - 表格任务（table）: 低并发（默认 3），因为表格通常较长
        - 图片任务（image）: 中等并发（默认 4），VL 模型可能较慢

        调度算法：
        1. 为每种任务类型维护一个队列
        2. 优先使用该类型的并发槽位
        3. 如果某类型槽位已满，可以"借用"其他类型的槽位
        4. 动态调整，确保总并发数不超过限制

        Args:
            tasks: 增强任务列表

        Returns:
            任务索引到结果的映射字典
        """
        if not tasks:
            self._progress("增强阶段跳过：没有需要增强的切片")
            return {}

        # 统计任务类型分布
        task_counts = Counter(task.kind for task in tasks)
        self._progress(
            "增强阶段开始："
            f"mode={self.enhance_mode}，text={task_counts.get('text', 0)}，"
            f"fragment={task_counts.get('fragment', 0)}，table={task_counts.get('table', 0)}，"
            f"image={task_counts.get('image', 0)}"
        )

        results: dict[int, _EnhancementResult] = {}
        completed_counts: Counter[str] = Counter()
        failures = 0

        # ----------------------------------------------------------------
        # 串行模式：逐个执行
        # ----------------------------------------------------------------
        if self.enhance_mode == "serial" or len(tasks) == 1:
            for completed, task in enumerate(tasks, start=1):
                result = self._run_enhancement_task(task)
                results[task.task_index] = result
                completed_counts[result.kind] += 1
                if result.err and not result.text:
                    failures += 1
                self._report_enhancement_progress(completed, len(tasks), completed_counts, failures)
            return results

        # ----------------------------------------------------------------
        # 并行模式：使用线程池
        # ----------------------------------------------------------------
        # 按类型分组任务
        task_queues: dict[str, deque[_EnhancementTask]] = {
            "text": deque(task for task in tasks if task.kind in {"text", "fragment"}),
            "table": deque(task for task in tasks if task.kind == "table"),
            "image": deque(task for task in tasks if task.kind == "image"),
        }

        # 各类型的软上限（优先使用本类型的槽位）
        soft_caps = {
            "text": self.text_enhance_workers,
            "table": self.table_enhance_workers,
            "image": self.image_enhance_workers,
        }

        # 优先级顺序（优先处理文本，其次是表格，最后是图片）
        preferred_order = ("text", "table", "image")

        worker_count = min(self.max_enhance_workers, len(tasks))
        active_by_pool: Counter[str] = Counter()  # 各类型当前活跃任务数
        futures: dict[Future[_EnhancementResult], tuple[_EnhancementTask, str]] = {}
        peak_concurrency = 0

        # ----------------------------------------------------------------
        # 内部函数：从队列弹出下一个任务
        # ----------------------------------------------------------------
        def pop_next_task(allow_borrow: bool) -> tuple[_EnhancementTask, str] | None:
            """
            从任务队列中弹出下一个任务。

            Args:
                allow_borrow: 是否允许借用其他类型的槽位

            Returns:
                (任务, 任务类型) 或 None
            """
            for pool in preferred_order:
                if task_queues[pool] and (allow_borrow or active_by_pool[pool] < soft_caps[pool]):
                    return task_queues[pool].popleft(), pool
            return None

        # ----------------------------------------------------------------
        # 内部函数：提交可用的任务
        # ----------------------------------------------------------------
        def submit_available(executor: ThreadPoolExecutor) -> None:
            """提交所有可执行的任务"""
            nonlocal peak_concurrency
            while len(futures) < worker_count:
                # 先尝试使用本类型的槽位
                next_task = pop_next_task(allow_borrow=False)
                if next_task is None:
                    # 本类型槽位已满，尝试借用
                    next_task = pop_next_task(allow_borrow=True)
                if next_task is None:
                    break
                task, pool = next_task
                active_by_pool[pool] += 1
                futures[executor.submit(self._run_enhancement_task, task)] = (task, pool)
                peak_concurrency = max(peak_concurrency, len(futures))

        # ----------------------------------------------------------------
        # 主循环：执行任务并收集结果
        # ----------------------------------------------------------------
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hierarchical-enhance") as executor:
            submit_available(executor)
            completed = 0
            while futures:
                # 等待任意一个任务完成
                done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task, pool = futures.pop(future)
                    active_by_pool[pool] -= 1
                    result = future.result()
                    completed += 1
                    results[result.task_index] = result
                    completed_counts[result.kind] += 1
                    if result.err and not result.text:
                        failures += 1
                    self._report_enhancement_progress(completed, len(tasks), completed_counts, failures)
                # 提交新任务
                submit_available(executor)

        # 记录调度统计信息
        self.last_timings["enhanceScheduler"] = 1
        self.last_timings["enhanceMaxConcurrency"] = worker_count
        self.last_timings["enhancePeakConcurrency"] = peak_concurrency
        self.last_timings["enhanceTextWorkers"] = self.text_enhance_workers
        self.last_timings["enhanceTableWorkers"] = self.table_enhance_workers
        self.last_timings["enhanceImageWorkers"] = self.image_enhance_workers
        return results

    def _record_enhancement_timings(
        self,
        results: dict[int, _EnhancementResult],
        wall_ms: int,
    ) -> None:
        """
        记录增强阶段的详细时间统计。

        统计内容包括：
        - 总耗时、任务数、失败数
        - 各类型任务的耗时和数量
        - Token 使用量（输入/输出/总计）
        - API Key 池的使用情况和限流统计

        Args:
            results: 增强结果字典
            wall_ms: 墙钟耗时（毫秒）
        """
        self.last_timings["enhanceWallMs"] = wall_ms

        # 按类型统计耗时
        by_kind: Counter[str] = Counter()
        failures = 0
        for result in results.values():
            by_kind[result.kind] += result.elapsed_ms
            if result.err and not result.text:
                failures += 1

        # 任务统计
        self.last_timings["enhanceTasks"] = len(results)
        self.last_timings["enhanceTextTasks"] = int(sum(1 for result in results.values() if result.kind == "text"))
        self.last_timings["enhanceFragmentTasks"] = int(sum(1 for result in results.values() if result.kind == "fragment"))
        self.last_timings["enhanceTableTasks"] = int(sum(1 for result in results.values() if result.kind == "table"))
        self.last_timings["enhanceImageTasks"] = int(sum(1 for result in results.values() if result.kind == "image"))
        self.last_timings["enhanceFailures"] = failures

        # 客户端复用统计
        self.last_timings["enhanceClientReuse"] = 1 if _env_bool("HIERARCHICAL_REUSE_LLM_CLIENTS", True) else 0

        # 各类型耗时
        self.last_timings["enhanceTextMs"] = int(by_kind.get("text", 0))
        self.last_timings["enhanceFragmentMs"] = int(by_kind.get("fragment", 0))
        self.last_timings["enhanceTableMs"] = int(by_kind.get("table", 0))
        self.last_timings["enhanceImageMs"] = int(by_kind.get("image", 0))

        # 请求统计
        self.last_timings["enhanceRequests"] = int(sum(1 for result in results.values() if result.tokens > 0))
        self.last_timings["enhancePromptTokens"] = int(sum(result.prompt_tokens for result in results.values()))
        self.last_timings["enhanceCompletionTokens"] = int(sum(result.completion_tokens for result in results.values()))
        self.last_timings["enhanceTotalTokens"] = int(sum(result.tokens for result in results.values()))

        # API Key 池统计
        llm_key_stats = self.llm_key_pool.stats()
        vl_key_stats = self.vl_key_pool.stats()
        self.last_timings["enhanceLlmKeyPoolSize"] = llm_key_stats["size"]
        self.last_timings["enhanceVlKeyPoolSize"] = vl_key_stats["size"]
        self.last_timings["enhanceKeyPoolSize"] = len(set(self.llm_key_pool.keys + self.vl_key_pool.keys))
        self.last_timings["enhanceKeyThrottleCount"] = (
            llm_key_stats["throttleCount"] + vl_key_stats["throttleCount"]
        )
        self.last_timings["enhanceKeyRetryCount"] = (
            llm_key_stats["retryCount"] + vl_key_stats["retryCount"]
        )
        self.last_timings["enhanceKeyCooldownCount"] = (
            llm_key_stats["cooldownCount"] + vl_key_stats["cooldownCount"]
        )

        # 各 Key 的详细使用情况
        self._record_key_usage_timings("enhanceLlmKey", llm_key_stats["usage"])
        self._record_key_usage_timings("enhanceVlKey", vl_key_stats["usage"])

    def _record_key_usage_timings(self, prefix: str, usage: dict[str, dict[str, int]]) -> None:
        """
        记录单个 Key 的使用统计。

        Args:
            prefix: 统计键前缀（如 "enhanceLlmKey"）
            usage: 使用情况字典，键为 Key 别名，值为统计指标
        """
        for alias, values in usage.items():
            for metric in ("calls", "successes", "failures", "throttles", "totalMs"):
                self.last_timings[f"{prefix}.{alias}.{metric}"] = int(values.get(metric, 0))

    def _render_plan(
        self,
        entries: list[_PlanEntry],
        results: dict[int, _EnhancementResult],
    ) -> list[Chunk]:
        """
        根据切片计划生成最终切片列表。

        处理流程：
        1. 遍历计划条目
        2. 对于 chunk 条目：直接添加，并分配索引
        3. 对于 task_index 条目：查找增强结果，创建 enhanced 切片

        索引分配策略：
        - 普通切片：使用当前索引
        - 文本组切片：使用组起始索引 + 组内偏移

        Args:
            entries: 切片计划条目列表
            results: 增强结果字典

        Returns:
            最终切片列表
        """
        chunks: list[Chunk] = []
        idx = 0  # 当前索引
        text_group_starts: dict[int, int] = {}  # 记录每个文本组的起始索引

        for entry in entries:
            # ------------------------------------------------------------
            # 处理已创建的切片
            # ------------------------------------------------------------
            if entry.chunk is not None:
                # 为切片分配索引
                if entry.index_policy == "text_group" and entry.group_id is not None:
                    # 文本组：使用组起始索引 + 偏移
                    group_start = text_group_starts.setdefault(entry.group_id, idx)
                    entry.chunk.chunk_index = group_start + entry.group_offset
                else:
                    # 普通切片：使用当前索引
                    entry.chunk.chunk_index = idx
                chunks.append(entry.chunk)
                idx += 1
                continue

            # ------------------------------------------------------------
            # 处理增强任务结果
            # ------------------------------------------------------------
            if entry.task_index is None:
                continue
            result = results.get(entry.task_index)
            if result is None:
                continue

            # 创建 enhanced 切片
            enhanced = self._build_enhanced_from_result(result, idx)
            if enhanced:
                chunks.append(enhanced)
                idx += 1

        return chunks

    def _build_enhanced_from_result(self, result: _EnhancementResult, idx: int) -> Optional[Chunk]:
        """
        从增强结果构建 enhanced 切片。

        Args:
            result: 增强结果
            idx: 切片索引

        Returns:
            enhanced 切片或 None（如果增强失败）
        """
        return self._build_enhanced_chunk(
            result.child,
            idx,
            result.text,
            result.tokens,
            result.err,
            result.prefix,
        )

    def _make_children(
        self,
        buffer: list[ContentBlock],
        parent: Optional[Chunk],
        source: str,
        start_idx: int,
        title: str,
    ) -> list[Chunk]:
        """
        将文本缓冲区切分为 child 切片。

        切分策略：
        1. 合并所有文本块
        2. 检测是否为流程类内容（若是，则加倍切片长度）
        3. 按句子边界切分
        4. 为每个切片创建 Chunk 对象

        Args:
            buffer: 文本块缓冲区
            parent: 父级切片（用于关联）
            source: 源文件路径
            start_idx: 起始索引
            title: 章节标题

        Returns:
            child 切片列表
        """
        # 合并所有文本块
        merged_text = "\n".join(b.text.strip() for b in buffer if b.text.strip())
        if not merged_text:
            return []

        # 确定切片长度
        max_chars = self.child_max_chars
        if _is_procedure_block(merged_text):
            # 流程类内容需要更长的切片以保持步骤完整性
            max_chars = min(1000, max_chars * 2)

        # 按句子边界切分
        segments = _split_text_by_sentences(merged_text, max_chars)
        page = buffer[0].page_idx
        children: list[Chunk] = []

        for seg in segments:
            child = Chunk(
                content=seg,
                source=source,
                page=page,
                chunk_index=start_idx + len(children),
                strategy=self.name,
                title=title,
                layer="child",  # 标记为子级
                parent_id=parent.id if parent else None,  # 关联父级
            )
            children.append(child)

        return children

    # ========================================================================
    # 增强切片构建方法（公开接口，供外部调用）
    # ========================================================================

    def _make_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """
        为单个 child 切片生成增强内容。

        这是一个便捷方法，用于单独处理某个切片的增强。

        Args:
            child: 待增强的 child 切片
            idx: 切片索引

        Returns:
            enhanced 切片或 None（如果增强失败）
        """
        task = _EnhancementTask(task_index=0, child=child, kind=self._get_enhancement_kind(child))
        result = self._run_enhancement_task(task)
        return self._build_enhanced_from_result(result, idx)

    def _make_text_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """
        为普通文本切片生成增强内容。

        生成内容：
        - 一句话摘要
        - 3 个检索问题

        Args:
            child: 文本切片
            idx: 切片索引

        Returns:
            enhanced 切片或 None
        """
        if len(child.content) < 50:
            return None
        text, tokens, prompt_tokens, completion_tokens, err = _normalize_llm_result(_generate_enhanced_text(
            child.content, child.title or "",
            self.llm_model, self.llm_base_url, self.llm_api_key,
            system_prompt=self.enhanced_system_prompt,
        ))
        return self._build_enhanced_chunk(child, idx, text, tokens, err, "[LLM增强]")

    def _make_image_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """
        为图片切片生成增强描述。

        策略：
        1. 如果配置了 VL 模型且图片可加载，使用 VL 模型
        2. 否则使用文本 LLM 根据图片说明推断内容

        Args:
            child: 图片切片
            idx: 切片索引

        Returns:
            enhanced 切片或 None
        """
        text, tokens, prompt_tokens, completion_tokens, err = _normalize_llm_result(_generate_image_description(
            image_path=child.image_path,
            alt_text=child.content,
            title=child.title or "",
            vl_model=self.vl_model,
            vl_base_url=self.vl_base_url,
            vl_api_key=self.vl_api_key,
            text_model=self.llm_model,
            text_base_url=self.llm_base_url,
            text_api_key=self.llm_api_key,
            system_prompt=self.enhanced_system_prompt,
        ))
        return self._build_enhanced_chunk(child, idx, text, tokens, err, "[图片描述]")

    def _make_table_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """
        为表格切片生成增强摘要。

        生成内容：
        - 表格主题概括
        - 专业术语解释
        - 数据规律提炼

        Args:
            child: 表格切片
            idx: 切片索引

        Returns:
            enhanced 切片或 None
        """
        if len(child.content) < 20:
            return None
        text, tokens, prompt_tokens, completion_tokens, err = _normalize_llm_result(_generate_table_summary(
            table_content=child.content,
            title=child.title or "",
            model=self.llm_model,
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            system_prompt=self.enhanced_system_prompt,
        ))
        return self._build_enhanced_chunk(child, idx, text, tokens, err, "[表格摘要]")

    def _make_fragment_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """
        为片段内容生成增强补充。

        生成内容：
        - 背景知识补充
        - 专业术语解释
        - 检索问题生成

        Args:
            child: 片段切片
            idx: 切片索引

        Returns:
            enhanced 切片或 None
        """
        if len(child.content) < 20:
            return None
        text, tokens, prompt_tokens, completion_tokens, err = _normalize_llm_result(_generate_fragment_enhancement(
            content=child.content,
            title=child.title or "",
            model=self.llm_model,
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            system_prompt=self.enhanced_system_prompt,
        ))
        return self._build_enhanced_chunk(child, idx, text, tokens, err, "[片段增强]")

    def _build_enhanced_chunk(
        self,
        child: Chunk,
        idx: int,
        text: Optional[str],
        tokens: int,
        err: str,
        prefix: str,
    ) -> Optional[Chunk]:
        """
        构建 enhanced 切片。

        这是最终的切片构建方法，负责：
        1. 累计 token 使用量
        2. 记录错误信息
        3. 解析 LLM 输出（摘要、实体、三元组）
        4. 创建 enhanced 切片对象

        Args:
            child: 原始的 child 切片
            idx: 切片索引
            text: LLM 生成的增强文本
            tokens: token 使用量
            err: 错误信息
            prefix: 输出前缀

        Returns:
            enhanced 切片或 None（如果没有生成内容）
        """
        # 累计 token
        self.total_tokens += tokens

        # 记录错误（去重）
        if err and not text:
            if not self.llm_errors or self.llm_errors[-1] != err:
                self.llm_errors.append(err)

        # 如果没有生成内容，返回 None
        if not text:
            return None

        # 解析增强文本（提取摘要、实体、三元组）
        parsed = parse_enhanced_response(text, child.id, fallback_text=text)

        # 创建 enhanced 切片
        return Chunk(
            content=f"{prefix} {parsed.summary or text}",  # 添加前缀方便识别
            source=child.source,
            page=child.page,
            chunk_index=idx,
            strategy=self.name,
            title=child.title,
            layer="enhanced",  # 标记为增强层
            parent_id=child.id,  # 关联原始 child 切片
            enhanced_text=parsed.summary or text,  # 增强文本
            extracted_entities=parsed.entities,     # 提取的实体
            extracted_triples=parsed.triples,       # 提取的三元组
            token_cost=tokens,                      # token 成本
        )
