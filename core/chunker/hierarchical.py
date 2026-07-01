"""Hierarchical three-layer chunking strategy for textbook PDFs.

Layer 1 (parent): section/chapter headings as context containers
Layer 2 (child): knowledge-point chunks for embedding
Layer 3 (enhanced): LLM-generated summaries/questions for retrieval boost

Enhanced scenarios:
- Image-only blocks: VL model describes image content and teaching significance
- Table-only blocks: LLM summarizes table and explains domain terms
- Fragment content: LLM supplements missing context and defines jargon
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

_PROCEDURE_KEYWORDS = re.compile(
    r"步骤|操作|流程|方法|过程|程序|要点|注意事项|procedure|step"
)

# Indicators that a text block is a fragment referencing missing context
_FRAGMENT_INDICATORS = re.compile(
    r"如上所述|如前所述|见图|参见|如图所示|上述|前述|以上内容|下面将|如下所示|详见|其中[，,]|该[方法算法模型公式]|此[方法处理操作]"
)

_CLIENT_LOCAL = threading.local()
_MAX_API_KEY_POOL_SIZE = 20


def _is_procedure_block(text: str) -> bool:
    return bool(_PROCEDURE_KEYWORDS.search(text[:200]))


def _is_fragment_content(text: str) -> bool:
    """Detect whether a text block is a fragment lacking standalone context."""
    if len(text) < 80:
        return True
    if _FRAGMENT_INDICATORS.search(text[:400]):
        return True
    return False


def _load_image_base64(image_path: str) -> Optional[str]:
    """Load image file and return base64-encoded string."""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def _split_text_by_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks respecting sentence boundaries."""
    separators = ["。", "；", "！", "？", ".\n", "\n\n", "\n"]
    chunks: list[str] = []
    remaining = text

    while len(remaining) > max_chars:
        cut_pos = -1
        for sep in separators:
            pos = remaining.rfind(sep, 0, max_chars)
            if pos > max_chars // 3:
                cut_pos = pos + len(sep)
                break
        if cut_pos <= 0:
            cut_pos = max_chars
        chunks.append(remaining[:cut_pos].strip())
        remaining = remaining[cut_pos:].strip()

    if remaining.strip():
        chunks.append(remaining.strip())
    return chunks


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


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
class _ApiKeyLease:
    key: str
    alias: str


class _ApiKeyPool:
    def __init__(self, name: str, keys: list[str], cooldown_seconds: int = 30):
        self.name = name
        self._keys = list(keys)
        self._aliases = {key: f"{name}-{index + 1}" for index, key in enumerate(self._keys)}
        self._index_by_key = {key: index for index, key in enumerate(self._keys)}
        self._inflight: Counter[str] = Counter()
        self._cooldown_until: dict[str, float] = {}
        self._cursor = 0
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._throttle_count = 0
        self._retry_count = 0
        self._cooldown_count = 0
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
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def acquire(self, exclude_keys: set[str] | None = None) -> _ApiKeyLease | None:
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
            return _ApiKeyLease(key=key, alias=self._aliases[key])

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

    def release(self, lease: _ApiKeyLease | None) -> None:
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
        if lease is None:
            return
        with self._lock:
            self._throttle_count += 1
            self._cooldown_count += 1
            self._cooldown_until[lease.key] = time.monotonic() + self._cooldown_seconds

    def record_retry(self) -> None:
        with self._lock:
            self._retry_count += 1

    def stats(self) -> dict[str, Any]:
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


def _get_cached_openai_client(api_key: str, base_url: str):
    if not _env_bool("HIERARCHICAL_REUSE_LLM_CLIENTS", True):
        return create_openai_client(api_key=api_key, base_url=base_url)

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


@dataclass
class _PlanEntry:
    chunk: Chunk | None = None
    task_index: int | None = None
    index_policy: str = "current"
    group_id: int | None = None
    group_offset: int = 0


@dataclass
class _EnhancementTask:
    task_index: int
    child: Chunk
    kind: str


@dataclass
class _EnhancementResult:
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


_LlmCallResult = tuple[Optional[str], int, str] | tuple[Optional[str], int, int, int, str]


def _normalize_llm_result(result: _LlmCallResult) -> tuple[Optional[str], int, int, int, str]:
    if len(result) >= 5:
        text, tokens, prompt_tokens, completion_tokens, err = result[:5]
        return text, int(tokens or 0), int(prompt_tokens or 0), int(completion_tokens or 0), str(err or "")
    text, tokens, err = result
    return text, int(tokens or 0), 0, 0, str(err or "")


def _generate_enhanced_text(content: str, title: str, model: str,
                            base_url: str, api_key: str,
                            system_prompt: str = "") -> tuple[Optional[str], int, int, int, str]:
    """Call LLM to generate retrieval-boosting summary/questions.
    Returns (text, token_count, error_msg)."""
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
    """Shared LLM call helper. Returns (text, tokens, error)."""
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
    """Describe image via VL model; fallback to text LLM with alt_text."""
    b64 = _load_image_base64(image_path) if image_path else None

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
        messages: list[dict] = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt_text},
            ],
        }]
        return _call_llm(messages, vl_model, vl_base_url, vl_api_key, max_tokens=400,
                         system_prompt=system_prompt)

    # Fallback: text LLM with alt_text
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
    """Summarize table content and explain domain-specific terms."""
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
    """Supplement missing context and define jargon for fragment text blocks."""
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


@register_strategy
class HierarchicalStrategy(ChunkingStrategy):
    """Three-layer hierarchical chunking for textbook content."""

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
        self.child_max_chars = child_max_chars
        self.enable_enhanced = enable_enhanced
        self.enable_image_enhanced = enable_image_enhanced
        self.enable_table_enhanced = enable_table_enhanced
        self.enable_fragment_enhanced = enable_fragment_enhanced
        self.enhanced_system_prompt = (
            resolve_llm_param(enhanced_system_prompt, "enhance_system_prompt", ["LLM_ENHANCE_SYSTEM_PROMPT"])
            or ""
        )

        self.llm_model = llm_model or os.getenv("LLM_CLEANER_MODEL", "qwen-plus")
        self.llm_base_url = llm_base_url or os.getenv(
            "LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.llm_api_key = llm_api_key or os.getenv(
            "LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")
        )
        # VL model for image recognition; falls back to text LLM if not configured
        self.vl_model = vl_model or os.getenv("VL_MODEL", "")
        self.vl_base_url = vl_base_url or os.getenv(
            "VL_BASE_URL",
            os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        explicit_vl_key = bool(vl_api_key or os.getenv("VL_API_KEY", "").strip())
        self.vl_api_key = vl_api_key or os.getenv(
            "VL_API_KEY", os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
        )
        llm_pool_value = os.getenv("LLM_API_KEY_POOL", "")
        vl_pool_value = os.getenv("VL_API_KEY_POOL", "")
        if not vl_pool_value and not explicit_vl_key:
            vl_pool_value = llm_pool_value
        key_cooldown_seconds = _env_int("HIERARCHICAL_KEY_COOLDOWN_SECONDS", 30, minimum=0)
        self.enhance_key_retries = _env_int("HIERARCHICAL_ENHANCE_KEY_RETRIES", 1, minimum=0)
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

        self.total_tokens = 0
        self.llm_errors: list[str] = []
        self.progress_callback = progress_callback
        self.enhance_mode = os.getenv("HIERARCHICAL_ENHANCE_MODE", "parallel_ordered").strip().lower()
        if self.enhance_mode not in {"serial", "parallel_ordered"}:
            self.enhance_mode = "parallel_ordered"
        self.text_enhance_workers = _env_int("HIERARCHICAL_TEXT_ENHANCE_WORKERS", 16)
        self.table_enhance_workers = _env_int("HIERARCHICAL_TABLE_ENHANCE_WORKERS", 3)
        self.image_enhance_workers = _env_int("HIERARCHICAL_IMAGE_ENHANCE_WORKERS", 4)
        default_total_workers = self.text_enhance_workers + self.table_enhance_workers + self.image_enhance_workers
        default_max_workers = (
            22
            if (self.text_enhance_workers, self.table_enhance_workers, self.image_enhance_workers) == (16, 3, 4)
            else default_total_workers
        )
        self.max_enhance_workers = _env_int("HIERARCHICAL_ENHANCE_MAX_CONCURRENCY", default_max_workers)
        self.last_timings: dict[str, int] = {}

    def _progress(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        self.total_tokens = 0
        self.last_timings = {}
        plan_start = time.monotonic()
        entries: list[_PlanEntry] = []
        tasks: list[_EnhancementTask] = []
        current_title = ""
        current_parent: Optional[Chunk] = None
        buffer: list[ContentBlock] = []
        source = blocks[0].source_file if blocks else ""
        total_blocks = len(blocks)
        group_counter = 0

        self._progress(
            f"切片开始：输入 {total_blocks} 个内容块，增强={self.enable_enhanced}，"
            f"模式={self.enhance_mode}"
        )

        def add_enhancement_task(child: Chunk) -> None:
            kind = self._get_enhancement_kind(child)
            task_index = len(tasks)
            tasks.append(_EnhancementTask(task_index=task_index, child=child, kind=kind))
            entries.append(_PlanEntry(task_index=task_index))

        def flush_buffer():
            nonlocal buffer, group_counter
            if not buffer:
                return
            first_page = buffer[0].page_idx + 1
            last_page = buffer[-1].page_idx + 1
            self._progress(f"正在切分文本缓冲：{len(buffer)} 个块，页码 P.{first_page}-P.{last_page}")
            child_chunks = self._make_children(
                buffer, current_parent, source, 0, current_title
            )
            self._progress(f"文本缓冲生成 {len(child_chunks)} 个 child 切片")
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

        last_reported_percent = -1
        for block_index, block in enumerate(blocks, start=1):
            percent = int(block_index * 100 / total_blocks) if total_blocks else 100
            if percent >= last_reported_percent + 10 or block_index == 1 or block_index == total_blocks:
                self._progress(
                    f"正在扫描内容块 {block_index}/{total_blocks} ({percent}%)，"
                    f"类型={block.type.value}，页码 P.{block.page_idx + 1}"
                )
                last_reported_percent = percent
            if block.type == BlockType.TITLE:
                flush_buffer()
                current_title = block.text.strip()
                self._progress(f"发现标题：P.{block.page_idx + 1} {current_title[:60]}")
                parent = Chunk(
                    content=current_title,
                    source=source,
                    page=block.page_idx,
                    chunk_index=0,
                    strategy=self.name,
                    title=current_title,
                    layer="parent",
                )
                entries.append(_PlanEntry(chunk=parent))
                current_parent = parent

            elif block.type == BlockType.TABLE:
                flush_buffer()
                table_content = block.table_html or block.text
                self._progress(f"正在处理表格块：页码 P.{block.page_idx + 1}，字符数 {len(table_content)}")
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
                if self.enable_enhanced and self.enable_table_enhanced:
                    add_enhancement_task(child)

            elif block.type == BlockType.IMAGE:
                flush_buffer()
                img_content = block.text or f"[图片 第{block.page_idx+1}页]"
                self._progress(f"正在处理图片块：页码 P.{block.page_idx + 1}，image_path={block.image_path or '无'}")
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
                if self.enable_enhanced and self.enable_image_enhanced:
                    add_enhancement_task(child)

            else:
                buffer.append(block)

        flush_buffer()
        base_ms = int((time.monotonic() - plan_start) * 1000)
        self.last_timings["chunkBaseMs"] = base_ms
        self._progress(
            f"基础切片完成：{sum(1 for entry in entries if entry.chunk)} 个基础条目，"
            f"{len(tasks)} 个增强任务"
        )

        enhance_start = time.monotonic()
        results = self._execute_enhancement_tasks(tasks)
        enhance_wall_ms = int((time.monotonic() - enhance_start) * 1000)
        self._record_enhancement_timings(results, enhance_wall_ms)

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
        if pool.size <= 0:
            return _normalize_llm_result(caller(fallback_key))

        attempted: set[str] = set()
        max_attempts = max(1, min(pool.size, self.enhance_key_retries + 1))
        last_result: tuple[Optional[str], int, int, int, str] = (None, 0, 0, 0, "")
        for attempt in range(max_attempts):
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
            pool.record_attempt(
                lease,
                int((time.monotonic() - attempt_started) * 1000),
                success=bool(text) or not err,
                throttled=throttled,
            )
            last_result = result
            if throttled:
                pool.mark_throttled(lease)
                attempted.add(lease.key)
                if attempt < max_attempts - 1:
                    pool.record_retry()
                    continue
            return result
        return last_result

    def _can_use_vl_for_image(self, child: Chunk) -> bool:
        if not (child.image_path and self.vl_model and self.vl_key_pool.size > 0):
            return False
        return bool(_load_image_base64(child.image_path))

    def _run_enhancement_task(self, task: _EnhancementTask) -> _EnhancementResult:
        started = time.monotonic()
        child = task.child
        if task.kind == "image":
            if self._can_use_vl_for_image(child):
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
                text, tokens, prompt_tokens, completion_tokens, err = self._call_with_key_pool(
                    self.llm_key_pool,
                    self.llm_api_key,
                    lambda api_key: _generate_image_description(
                        image_path=child.image_path,
                        alt_text=child.content,
                        title=child.title or "",
                        vl_model=self.vl_model,
                        vl_base_url=self.vl_base_url,
                        vl_api_key="",
                        text_model=self.llm_model,
                        text_base_url=self.llm_base_url,
                        text_api_key=api_key,
                        system_prompt=self.enhanced_system_prompt,
                    ),
                )
            prefix = "[图片描述]"
        elif task.kind == "table":
            if len(child.content) < 20:
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
        else:
            if len(child.content) < 50:
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
        if not tasks:
            self._progress("增强阶段跳过：没有需要增强的切片")
            return {}

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

        if self.enhance_mode == "serial" or len(tasks) == 1:
            for completed, task in enumerate(tasks, start=1):
                result = self._run_enhancement_task(task)
                results[task.task_index] = result
                completed_counts[result.kind] += 1
                if result.err and not result.text:
                    failures += 1
                self._report_enhancement_progress(completed, len(tasks), completed_counts, failures)
            return results

        task_queues: dict[str, deque[_EnhancementTask]] = {
            "text": deque(task for task in tasks if task.kind in {"text", "fragment"}),
            "table": deque(task for task in tasks if task.kind == "table"),
            "image": deque(task for task in tasks if task.kind == "image"),
        }
        soft_caps = {
            "text": self.text_enhance_workers,
            "table": self.table_enhance_workers,
            "image": self.image_enhance_workers,
        }
        preferred_order = ("text", "table", "image")
        worker_count = min(self.max_enhance_workers, len(tasks))
        active_by_pool: Counter[str] = Counter()
        futures: dict[Future[_EnhancementResult], tuple[_EnhancementTask, str]] = {}
        peak_concurrency = 0

        def pop_next_task(allow_borrow: bool) -> tuple[_EnhancementTask, str] | None:
            for pool in preferred_order:
                if task_queues[pool] and (allow_borrow or active_by_pool[pool] < soft_caps[pool]):
                    return task_queues[pool].popleft(), pool
            return None

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal peak_concurrency
            while len(futures) < worker_count:
                next_task = pop_next_task(allow_borrow=False)
                if next_task is None:
                    next_task = pop_next_task(allow_borrow=True)
                if next_task is None:
                    break
                task, pool = next_task
                active_by_pool[pool] += 1
                futures[executor.submit(self._run_enhancement_task, task)] = (task, pool)
                peak_concurrency = max(peak_concurrency, len(futures))

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="hierarchical-enhance") as executor:
            submit_available(executor)
            completed = 0
            while futures:
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
                submit_available(executor)

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
        self.last_timings["enhanceWallMs"] = wall_ms
        by_kind: Counter[str] = Counter()
        failures = 0
        for result in results.values():
            by_kind[result.kind] += result.elapsed_ms
            if result.err and not result.text:
                failures += 1
        self.last_timings["enhanceTasks"] = len(results)
        self.last_timings["enhanceTextTasks"] = int(sum(1 for result in results.values() if result.kind == "text"))
        self.last_timings["enhanceFragmentTasks"] = int(sum(1 for result in results.values() if result.kind == "fragment"))
        self.last_timings["enhanceTableTasks"] = int(sum(1 for result in results.values() if result.kind == "table"))
        self.last_timings["enhanceImageTasks"] = int(sum(1 for result in results.values() if result.kind == "image"))
        self.last_timings["enhanceFailures"] = failures
        self.last_timings["enhanceClientReuse"] = 1 if _env_bool("HIERARCHICAL_REUSE_LLM_CLIENTS", True) else 0
        self.last_timings["enhanceTextMs"] = int(by_kind.get("text", 0))
        self.last_timings["enhanceFragmentMs"] = int(by_kind.get("fragment", 0))
        self.last_timings["enhanceTableMs"] = int(by_kind.get("table", 0))
        self.last_timings["enhanceImageMs"] = int(by_kind.get("image", 0))
        self.last_timings["enhanceRequests"] = int(sum(1 for result in results.values() if result.tokens > 0))
        self.last_timings["enhancePromptTokens"] = int(sum(result.prompt_tokens for result in results.values()))
        self.last_timings["enhanceCompletionTokens"] = int(sum(result.completion_tokens for result in results.values()))
        self.last_timings["enhanceTotalTokens"] = int(sum(result.tokens for result in results.values()))
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
        self._record_key_usage_timings("enhanceLlmKey", llm_key_stats["usage"])
        self._record_key_usage_timings("enhanceVlKey", vl_key_stats["usage"])

    def _record_key_usage_timings(self, prefix: str, usage: dict[str, dict[str, int]]) -> None:
        for alias, values in usage.items():
            for metric in ("calls", "successes", "failures", "throttles", "totalMs"):
                self.last_timings[f"{prefix}.{alias}.{metric}"] = int(values.get(metric, 0))

    def _render_plan(
        self,
        entries: list[_PlanEntry],
        results: dict[int, _EnhancementResult],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0
        text_group_starts: dict[int, int] = {}

        for entry in entries:
            if entry.chunk is not None:
                if entry.index_policy == "text_group" and entry.group_id is not None:
                    group_start = text_group_starts.setdefault(entry.group_id, idx)
                    entry.chunk.chunk_index = group_start + entry.group_offset
                else:
                    entry.chunk.chunk_index = idx
                chunks.append(entry.chunk)
                idx += 1
                continue

            if entry.task_index is None:
                continue
            result = results.get(entry.task_index)
            if result is None:
                continue
            enhanced = self._build_enhanced_from_result(result, idx)
            if enhanced:
                chunks.append(enhanced)
                idx += 1

        return chunks

    def _build_enhanced_from_result(self, result: _EnhancementResult, idx: int) -> Optional[Chunk]:
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
        """Split buffered text blocks into child chunks."""
        merged_text = "\n".join(b.text.strip() for b in buffer if b.text.strip())
        if not merged_text:
            return []

        max_chars = self.child_max_chars
        if _is_procedure_block(merged_text):
            max_chars = min(1000, max_chars * 2)

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
                layer="child",
                parent_id=parent.id if parent else None,
            )
            children.append(child)
        return children

    def _make_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """Dispatch to the appropriate enhancement based on chunk type."""
        task = _EnhancementTask(task_index=0, child=child, kind=self._get_enhancement_kind(child))
        result = self._run_enhancement_task(task)
        return self._build_enhanced_from_result(result, idx)

    def _make_text_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """Standard retrieval enhancement for plain text chunks."""
        if len(child.content) < 50:
            return None
        text, tokens, prompt_tokens, completion_tokens, err = _normalize_llm_result(_generate_enhanced_text(
            child.content, child.title or "",
            self.llm_model, self.llm_base_url, self.llm_api_key,
            system_prompt=self.enhanced_system_prompt,
        ))
        return self._build_enhanced_chunk(child, idx, text, tokens, err, "[LLM增强]")

    def _make_image_enhanced(self, child: Chunk, idx: int) -> Optional[Chunk]:
        """Generate image description via VL model or text LLM fallback."""
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
        """Generate table summary and term explanations."""
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
        """Supplement context and define jargon for fragment text blocks."""
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
        """Build an enhanced Chunk from LLM output."""
        self.total_tokens += tokens
        if err and not text:
            if not self.llm_errors or self.llm_errors[-1] != err:
                self.llm_errors.append(err)
        if not text:
            return None
        parsed = parse_enhanced_response(text, child.id, fallback_text=text)
        return Chunk(
            content=f"{prefix} {parsed.summary or text}",
            source=child.source,
            page=child.page,
            chunk_index=idx,
            strategy=self.name,
            title=child.title,
            layer="enhanced",
            parent_id=child.id,
            enhanced_text=parsed.summary or text,
            extracted_entities=parsed.entities,
            extracted_triples=parsed.triples,
            token_cost=tokens,
        )
