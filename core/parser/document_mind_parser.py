from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Optional

from core.models.content_block import BlockType, ContentBlock
from core.parser.pdf_sharding import (
    PdfInspection,
    PdfShard,
    PdfShardSaveOptions,
    ShardBlockRecord,
    inspect_pdf,
    merge_shard_records,
    offset_shard_blocks,
    split_pdf_to_shards,
    split_pdf_to_weighted_shards,
)

DEFAULT_ENDPOINT = "docmind-api.cn-hangzhou.aliyuncs.com"
DEFAULT_OUTPUT_FORMATS = ("markdown", "visualLayoutInfo")
SUPPORTED_OUTPUT_FORMATS = {"markdown", "visualLayoutInfo"}
DEFAULT_LAYOUT_STEP_SIZE = 3000
DEFAULT_RESULT_FETCH_RETRIES = 2
DEFAULT_EMPTY_RESULT_RETRIES = 1
DEFAULT_EMPTY_RESULT_RETRY_DELAY = 2.0
EMPTY_RESULT_MESSAGE = (
    "Document Mind result does not contain blocks/pages/layouts/paragraphs or markdown text"
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
DEFAULT_SHARDING = {
    "enabled": True,
    "min_file_mb": 150.0,
    "min_pages": 50,
    "pages_per_shard": 33,
    "max_concurrency": 4,
    "min_pages_per_shard": 20,
    "target_waves": 2,
    "text_sample_pages": 5,
    "weighted_sharding_enabled": False,
    "heavy_shard_first": False,
    "shard_save_garbage": 1,
    "shard_save_deflate": True,
}
DEFAULT_MAX_INFLIGHT_PER_KEY = 1
DEFAULT_KEY_RETRIES = 1
DEFAULT_KEY_COOLDOWN_SECONDS = 60
DEFAULT_KEY_ACQUIRE_WAIT_SECONDS = 1800.0
DEFAULT_KEY_PROBE_CONCURRENCY = 1
DEFAULT_KEY_UNKNOWN_LATENCY_PENALTY_MS = 30_000
DEFAULT_KEY_SLOW_LATENCY_MS = 90_000
DEFAULT_HEDGED_SHARD_ENABLED = False
DEFAULT_HEDGE_AFTER_SECONDS = 90.0
DEFAULT_HEDGE_MAX_EXTRA_ATTEMPTS = 1

_LAST_KEY_POOL_METRICS: dict[str, int] = {}
_LAST_KEY_POOL_METRICS_LOCK = threading.Lock()
_DOCUMENT_MIND_KEY_HISTORY: dict[str, dict[str, int]] = {}
_DOCUMENT_MIND_KEY_HISTORY_LOCK = threading.Lock()


class DocumentMindEmptyResultError(ValueError):
    """Raised when Document Mind reports success but returns no parseable content."""


@dataclass(frozen=True)
class _DocumentMindCredential:
    access_key_id: str
    access_key_secret: str
    alias: str
    fingerprint: str = ""


@dataclass(frozen=True)
class _DocumentMindCredentialLease:
    credential: _DocumentMindCredential

    @property
    def alias(self) -> str:
        return self.credential.alias


@dataclass
class _DocumentMindShardState:
    shard: PdfShard
    primary_started_at: float = 0.0
    hedge_count: int = 0
    aliases: set[str] = field(default_factory=set)
    aliases_lock: threading.Lock = field(default_factory=threading.Lock)
    errors: list[BaseException] = field(default_factory=list)


class _DocumentMindParseTimings:
    def __init__(self) -> None:
        self._values: Counter[str] = Counter()
        self._max_values: dict[str, int] = {}
        self._lock = threading.Lock()

    def observe_ms(self, key: str, start: float, *, max_key: str | None = None) -> int:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        self.add_ms(key, elapsed_ms, max_key=max_key)
        return elapsed_ms

    def add_ms(self, key: str, elapsed_ms: int, *, max_key: str | None = None) -> None:
        elapsed = max(0, int(elapsed_ms))
        with self._lock:
            self._values[key] += elapsed
            if max_key:
                self._max_values[max_key] = max(self._max_values.get(max_key, 0), elapsed)

    def increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._values[key] += int(amount)

    def set_value(self, key: str, value: int) -> None:
        with self._lock:
            self._values[key] = int(value)

    def metrics(self) -> dict[str, int]:
        with self._lock:
            values = {key: int(value) for key, value in self._values.items()}
            values.update(self._max_values)
            return values


class _DocumentMindCredentialPool:
    def __init__(
        self,
        credentials: list[_DocumentMindCredential],
        *,
        max_inflight_per_key: int,
        cooldown_seconds: int,
        unknown_probe_concurrency: int = DEFAULT_KEY_PROBE_CONCURRENCY,
    ) -> None:
        self._credentials = list(credentials)
        self._index_by_alias = {credential.alias: index for index, credential in enumerate(self._credentials)}
        self._inflight: Counter[str] = Counter()
        self._cooldown_until: dict[str, float] = {}
        self._usage = {
            credential.alias: {
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "throttles": 0,
                "totalMs": 0,
                "lastMs": 0,
                "avgMs": 0,
            }
            for credential in self._credentials
        }
        self._cursor = 0
        self._max_inflight_per_key = max(1, max_inflight_per_key)
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._unknown_probe_concurrency = max(1, unknown_probe_concurrency)
        self._active_key_target = 1
        self._throttle_count = 0
        self._retry_count = 0
        self._cooldown_count = 0
        self._lock = threading.Lock()
        self._seed_latency_history()

    @property
    def size(self) -> int:
        return len(self._credentials)

    @property
    def capacity(self) -> int:
        return max(1, self.size * self._max_inflight_per_key)

    def set_active_key_target(self, value: int) -> None:
        with self._lock:
            self._active_key_target = max(1, min(self.size or 1, int(value)))

    def acquire(self, exclude_aliases: set[str] | None = None) -> _DocumentMindCredentialLease | None:
        exclude = exclude_aliases or set()
        with self._lock:
            if not self._credentials:
                return None
            now = time.monotonic()
            credential = self._select_credential(now, exclude, respect_cooldown=True)
            if credential is None:
                credential = self._select_credential(now, exclude, respect_cooldown=False)
            if credential is None:
                return None
            self._inflight[credential.alias] += 1
            self._cursor = (self._index_by_alias[credential.alias] + 1) % len(self._credentials)
            return _DocumentMindCredentialLease(credential=credential)

    def _select_credential(
        self,
        now: float,
        exclude_aliases: set[str],
        *,
        respect_cooldown: bool,
    ) -> _DocumentMindCredential | None:
        best: _DocumentMindCredential | None = None
        best_score: tuple[int, int, int] | None = None
        count = len(self._credentials)
        unknown_inflight = self._unknown_inflight_count()
        known_eligible_count = self._known_eligible_count(now, exclude_aliases, respect_cooldown=respect_cooldown)
        unknown_probe_budget = max(
            self._unknown_probe_concurrency,
            self._active_key_target - known_eligible_count,
        )
        should_probe_unknown = (
            known_eligible_count + unknown_inflight < self._active_key_target
            and unknown_inflight < unknown_probe_budget
        )
        candidates: list[tuple[_DocumentMindCredential, int]] = []
        for offset in range(count):
            credential = self._credentials[(self._cursor + offset) % count]
            alias = credential.alias
            if alias in exclude_aliases:
                continue
            if self._inflight[alias] >= self._max_inflight_per_key:
                continue
            if respect_cooldown and self._cooldown_until.get(alias, 0.0) > now:
                continue
            candidates.append((credential, offset))

        known_candidates = [
            (credential, offset)
            for credential, offset in candidates
            if self._has_latency_history(credential.alias)
        ]
        unknown_candidates = [
            (credential, offset)
            for credential, offset in candidates
            if not self._has_latency_history(credential.alias)
        ]
        if should_probe_unknown and unknown_candidates:
            candidates = unknown_candidates
        elif known_candidates:
            candidates = known_candidates
        elif not should_probe_unknown:
            candidates = []

        for credential, offset in candidates:
            alias = credential.alias
            if not self._has_latency_history(alias) and not should_probe_unknown:
                continue
            score = (self._inflight[alias], self._latency_score(alias), offset)
            if best_score is None or score < best_score:
                best = credential
                best_score = score
        return best

    def _has_latency_history(self, alias: str) -> bool:
        usage = self._usage.get(alias, {})
        return int(usage.get("avgMs", 0) or 0) > 0

    def _unknown_inflight_count(self) -> int:
        return sum(
            int(count)
            for alias, count in self._inflight.items()
            if count > 0 and not self._has_latency_history(alias)
        )

    def _known_eligible_count(
        self,
        now: float,
        exclude_aliases: set[str],
        *,
        respect_cooldown: bool,
    ) -> int:
        total = 0
        for credential in self._credentials:
            alias = credential.alias
            if alias in exclude_aliases:
                continue
            if not self._has_latency_history(alias):
                continue
            if respect_cooldown and self._cooldown_until.get(alias, 0.0) > now:
                continue
            total += 1
        return total

    def _latency_score(self, alias: str) -> int:
        usage = self._usage.get(alias, {})
        avg_ms = int(usage.get("avgMs", 0) or 0)
        if avg_ms > 0:
            return avg_ms
        known_latencies = [
            int(item.get("avgMs", 0) or 0)
            for item in self._usage.values()
            if int(item.get("avgMs", 0) or 0) > 0
        ]
        if not known_latencies:
            return 0
        best_known = min(known_latencies)
        worst_known = max(known_latencies)
        if best_known >= DEFAULT_KEY_SLOW_LATENCY_MS:
            return best_known
        conservative_score = best_known + DEFAULT_KEY_UNKNOWN_LATENCY_PENALTY_MS
        return min(conservative_score, worst_known) if worst_known > best_known else conservative_score

    def release(self, lease: _DocumentMindCredentialLease, elapsed_ms: int, *, success: bool, throttle: bool) -> None:
        alias = lease.alias
        elapsed = max(0, int(elapsed_ms))
        updated_usage: dict[str, int] | None = None
        credential = lease.credential
        with self._lock:
            self._inflight[alias] = max(0, self._inflight[alias] - 1)
            usage = self._usage[alias]
            usage["calls"] += 1
            usage["totalMs"] += elapsed
            usage["lastMs"] = elapsed
            previous_avg = int(usage.get("avgMs", 0) or 0)
            if previous_avg <= 0:
                usage["avgMs"] = elapsed
            else:
                usage["avgMs"] = int(previous_avg * 0.7 + elapsed * 0.3)
            if success:
                usage["successes"] += 1
                updated_usage = dict(usage)
            else:
                usage["failures"] += 1
                if throttle:
                    usage["throttles"] += 1
                    self._throttle_count += 1
                    if self._cooldown_seconds > 0:
                        self._cooldown_until[alias] = time.monotonic() + self._cooldown_seconds
                        self._cooldown_count += 1
        if updated_usage is not None:
            _update_document_mind_key_history(credential, updated_usage)

    def _seed_latency_history(self) -> None:
        with _DOCUMENT_MIND_KEY_HISTORY_LOCK:
            for credential in self._credentials:
                if not credential.fingerprint:
                    continue
                historical = _DOCUMENT_MIND_KEY_HISTORY.get(credential.fingerprint)
                if not historical:
                    continue
                usage = self._usage.get(credential.alias)
                if usage is None:
                    continue
                usage["lastMs"] = int(historical.get("lastMs", 0) or 0)
                usage["avgMs"] = int(historical.get("avgMs", 0) or 0)

    def record_retry(self) -> None:
        with self._lock:
            self._retry_count += 1

    def metrics(self) -> dict[str, int]:
        with self._lock:
            values: dict[str, int] = {
                "parseKeyPoolSize": len(self._credentials),
                "parseKeyMaxInflightPerKey": self._max_inflight_per_key,
                "parseKeyUnknownProbeConcurrency": self._unknown_probe_concurrency,
                "parseKeyActiveTarget": self._active_key_target,
                "parseKeyThrottleCount": self._throttle_count,
                "parseKeyRetryCount": self._retry_count,
                "parseKeyCooldownCount": self._cooldown_count,
            }
            for credential in self._credentials:
                usage = self._usage[credential.alias]
                for key, value in usage.items():
                    values[f"parseKey.{credential.alias}.{key}"] = int(value)
            return values


def get_last_document_mind_key_pool_metrics() -> dict[str, int]:
    with _LAST_KEY_POOL_METRICS_LOCK:
        return dict(_LAST_KEY_POOL_METRICS)


def _set_last_document_mind_key_pool_metrics(metrics: dict[str, int]) -> None:
    with _LAST_KEY_POOL_METRICS_LOCK:
        _LAST_KEY_POOL_METRICS.clear()
        _LAST_KEY_POOL_METRICS.update(metrics)


def _collect_document_mind_metrics(
    pool: _DocumentMindCredentialPool,
    timings: _DocumentMindParseTimings | None = None,
) -> dict[str, int]:
    metrics = pool.metrics()
    if timings is not None:
        metrics.update(timings.metrics())
    return metrics


def _parse_document_mind_credential_pool(
    primary_access_key_id: str,
    primary_access_key_secret: str,
    pool_value: str,
) -> list[_DocumentMindCredential]:
    pairs: list[tuple[str, str]] = []

    def add_pair(access_key_id: str | None, access_key_secret: str | None) -> None:
        ak = (access_key_id or "").strip()
        sk = (access_key_secret or "").strip()
        if not ak or not sk:
            return
        pair = (ak, sk)
        if pair not in pairs:
            pairs.append(pair)

    add_pair(primary_access_key_id, primary_access_key_secret)
    raw = (pool_value or "").strip()
    if raw:
        if raw.startswith("["):
            try:
                values = json.loads(raw)
            except Exception:
                values = None
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        add_pair(
                            item.get("access_key_id") or item.get("ak") or item.get("id"),
                            item.get("access_key_secret") or item.get("sk") or item.get("secret"),
                        )
                    elif isinstance(item, list) and len(item) >= 2:
                        add_pair(str(item[0]), str(item[1]))
                    else:
                        text = str(item)
                        if ":" in text:
                            ak, sk = text.split(":", 1)
                            add_pair(ak, sk)
            else:
                for part in re.split(r"[\s,;]+", raw):
                    if ":" in part:
                        ak, sk = part.split(":", 1)
                        add_pair(ak, sk)
        else:
            for part in re.split(r"[\s,;]+", raw):
                if ":" in part:
                    ak, sk = part.split(":", 1)
                    add_pair(ak, sk)

    return [
        _DocumentMindCredential(
            access_key_id=ak,
            access_key_secret=sk,
            alias=f"dm-key-{index + 1}",
            fingerprint=_document_mind_credential_fingerprint(ak, sk),
        )
        for index, (ak, sk) in enumerate(pairs)
    ]


def _document_mind_credential_fingerprint(access_key_id: str, access_key_secret: str) -> str:
    material = f"{access_key_id}:{access_key_secret}".encode("utf-8", errors="ignore")
    return hashlib.sha256(material).hexdigest()


def _update_document_mind_key_history(
    credential: _DocumentMindCredential,
    usage: dict[str, int],
) -> None:
    if not credential.fingerprint:
        return
    with _DOCUMENT_MIND_KEY_HISTORY_LOCK:
        previous = _DOCUMENT_MIND_KEY_HISTORY.get(credential.fingerprint, {})
        previous_avg = int(previous.get("avgMs", 0) or 0)
        current_avg = int(usage.get("avgMs", 0) or 0)
        if previous_avg > 0 and current_avg > 0:
            avg_ms = int(previous_avg * 0.8 + current_avg * 0.2)
        else:
            avg_ms = current_avg or previous_avg
        _DOCUMENT_MIND_KEY_HISTORY[credential.fingerprint] = {
            "lastMs": int(usage.get("lastMs", 0) or previous.get("lastMs", 0) or 0),
            "avgMs": avg_ms,
        }


def _resolve_document_mind_credential_pool() -> _DocumentMindCredentialPool:
    primary_access_key_id = (
        os.getenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "").strip()
        or os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    )
    primary_access_key_secret = (
        os.getenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "").strip()
        or os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    )
    credentials = _parse_document_mind_credential_pool(
        primary_access_key_id,
        primary_access_key_secret,
        os.getenv("ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL", ""),
    )
    if not credentials:
        raise ValueError(
            "ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID/SECRET or OSS_ACCESS_KEY_ID/SECRET must be configured"
        )
    return _DocumentMindCredentialPool(
        credentials,
        max_inflight_per_key=_env_int(
            "ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY",
            default=DEFAULT_MAX_INFLIGHT_PER_KEY,
            minimum=1,
        ),
        cooldown_seconds=_env_int(
            "ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS",
            default=DEFAULT_KEY_COOLDOWN_SECONDS,
            minimum=0,
        ),
        unknown_probe_concurrency=_env_int(
            "ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY",
            default=DEFAULT_KEY_PROBE_CONCURRENCY,
            minimum=1,
        ),
    )


def _is_document_mind_throttle_error(error: object) -> bool:
    text = str(error or "").lower()
    markers = (
        "429",
        "rate limit",
        "ratelimit",
        "too many requests",
        "throttl",
        "quota",
        "qps",
        "limitexceeded",
        "限流",
        "请求过多",
        "配额",
    )
    return any(marker in text for marker in markers)


def _acquire_document_mind_lease(
    pool: _DocumentMindCredentialPool,
    excluded_aliases: set[str],
    *,
    wait_seconds: float = DEFAULT_KEY_ACQUIRE_WAIT_SECONDS,
    allow_excluded_fallback: bool = True,
) -> _DocumentMindCredentialLease | None:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        lease = pool.acquire(excluded_aliases)
        if lease is not None:
            return lease
        if time.monotonic() >= deadline:
            if excluded_aliases and allow_excluded_fallback:
                return pool.acquire(set())
            return None
        time.sleep(0.1)


def parse_pdf(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """Parse a PDF through Alibaba Document Mind OpenAPI."""

    def _log(message: str) -> None:
        if log_fn:
            log_fn(message)

    source_path = Path(pdf_path)
    if not source_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    source_name = original_name or source_path.name
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    _set_last_document_mind_key_pool_metrics({})
    _parse_output_formats(os.getenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT"))
    credential_pool = _resolve_document_mind_credential_pool()
    sharding_cfg = _get_document_mind_sharding_config()
    timings = _DocumentMindParseTimings()
    timings.set_value("parseServiceInputBytes", source_path.stat().st_size)
    timings.set_value("parseProviderManagedLlmEnabled", 1 if _env_bool("ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT", default=False) else 0)
    timings.set_value("parseProviderLlmTokensAvailable", 0)
    timings.set_value("parseProviderLlmPromptTokens", 0)
    timings.set_value("parseProviderLlmCompletionTokens", 0)
    timings.set_value("parseProviderLlmTotalTokens", 0)

    if sharding_cfg["enabled"]:
        try:
            inspect_start = time.monotonic()
            inspection = inspect_pdf(
                str(source_path),
                text_sample_pages=int(sharding_cfg["text_sample_pages"]),
                profile_pages=bool(sharding_cfg.get("weighted_sharding_enabled", False)),
            )
        except Exception as exc:
            timings.observe_ms("inspectMs", inspect_start)
            _log(f"Document Mind PDF 体检失败，回退单任务解析：{type(exc).__name__}: {exc}")
        else:
            timings.observe_ms("inspectMs", inspect_start)
            timings.set_value("parseServiceInputPages", inspection.page_count)
            _log_document_mind_inspection(inspection, _log)
            if _should_parse_document_mind_with_shards(inspection, sharding_cfg):
                return parse_pdf_sharded(
                    str(source_path),
                    output_dir=str(output_path),
                    log_fn=log_fn,
                    original_name=source_name,
                    inspection=inspection,
                    sharding_config=sharding_cfg,
                    credential_pool=credential_pool,
                    timings=timings,
                )
            _log("未命中 Document Mind 分片阈值，使用单任务解析。")

    if "parseServiceInputPages" not in timings.metrics():
        timings.set_value("parseServiceInputPages", 0)

    try:
        return _parse_pdf_single(
            str(source_path),
            str(output_path),
            _log,
            source_name,
            credential_pool=credential_pool,
            timings=timings,
        )
    finally:
        _set_last_document_mind_key_pool_metrics(
            _collect_document_mind_metrics(credential_pool, timings)
        )


def _parse_pdf_single(
    pdf_path: str,
    output_dir: str,
    log_fn: Callable[[str], None],
    source_name: str,
    *,
    credential_pool: _DocumentMindCredentialPool | None = None,
    timings: _DocumentMindParseTimings | None = None,
    acquire_wait_seconds: float = DEFAULT_KEY_ACQUIRE_WAIT_SECONDS,
    excluded_aliases: set[str] | None = None,
    used_aliases: set[str] | None = None,
    used_aliases_lock: threading.Lock | None = None,
    allow_excluded_fallback: bool = True,
    count_hedge_submission: bool = False,
) -> list[ContentBlock]:
    pool = credential_pool or _resolve_document_mind_credential_pool()
    key_retries = _env_int(
        "ALIYUN_DOCUMENT_MIND_KEY_RETRIES",
        default=DEFAULT_KEY_RETRIES,
        minimum=0,
    )
    excluded_aliases = set(excluded_aliases or set())
    attempt = 0

    while True:
        lease = _acquire_document_mind_lease(
            pool,
            excluded_aliases,
            wait_seconds=acquire_wait_seconds,
            allow_excluded_fallback=allow_excluded_fallback,
        )
        if lease is None:
            raise RuntimeError("No available Document Mind credential lease")

        start = time.monotonic()
        try:
            log_fn(f"Document Mind 使用凭证 {lease.alias}")
            if used_aliases is not None:
                if used_aliases_lock is not None:
                    with used_aliases_lock:
                        used_aliases.add(lease.alias)
                else:
                    used_aliases.add(lease.alias)
            if count_hedge_submission and timings is not None:
                timings.increment("parseHedgeExtraSubmissions")
            client_start = time.monotonic()
            client = _create_document_mind_client(lease.credential)
            if timings is not None:
                timings.observe_ms("clientCreateMs", client_start, max_key="clientCreateMsMax")
            blocks = _parse_pdf_single_with_client(
                client,
                pdf_path,
                output_dir,
                log_fn,
                source_name,
                timings=timings,
            )
            pool.release(lease, int((time.monotonic() - start) * 1000), success=True, throttle=False)
            if credential_pool is None:
                _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
            return blocks
        except Exception as exc:
            throttle = _is_document_mind_throttle_error(exc)
            pool.release(lease, int((time.monotonic() - start) * 1000), success=False, throttle=throttle)
            if throttle and attempt < key_retries:
                attempt += 1
                excluded_aliases.add(lease.alias)
                pool.record_retry()
                log_fn(
                    f"Document Mind 凭证 {lease.alias} 触发限流，"
                    f"准备切换凭证重试 {attempt}/{key_retries}"
                )
                continue
            if credential_pool is None:
                _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
            raise


def _parse_pdf_single_with_client(
    client: object,
    pdf_path: str,
    output_dir: str,
    log_fn: Callable[[str], None],
    source_name: str,
    *,
    timings: _DocumentMindParseTimings | None = None,
) -> list[ContentBlock]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    attempts = 1 + _env_int(
        "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES",
        default=DEFAULT_EMPTY_RESULT_RETRIES,
        minimum=0,
    )
    retry_delay = _env_float(
        "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY",
        default=DEFAULT_EMPTY_RESULT_RETRY_DELAY,
        minimum=0.0,
    )
    last_empty_error: DocumentMindEmptyResultError | None = None

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            log_fn(f"Document Mind 空结果重试：重新提交任务 {attempt}/{attempts}")

        log_fn("步骤 1/3：提交阿里 Document Mind 文档解析任务...")
        if timings is not None:
            timings.add_ms("parseServiceRequests", 1)
        submit_start = time.monotonic()
        try:
            job_id = _submit_document_mind_job(client, pdf_path, source_name, log_fn)
        finally:
            if timings is not None:
                timings.observe_ms("submitWallMs", submit_start, max_key="submitWallMsMax")
        log_fn(f"任务已提交，job_id={job_id}")
        log_fn("步骤 2/3：轮询 Document Mind 任务状态...")
        poll_start = time.monotonic()
        try:
            status_payload = _poll_document_mind_job(client, job_id, log_fn)
        finally:
            if timings is not None:
                timings.observe_ms("pollWallMs", poll_start, max_key="pollWallMsMax")
        log_fn("步骤 3/3：获取并转换 Document Mind 解析结果...")

        try:
            blocks = _get_and_convert_document_mind_result(
                client,
                job_id,
                status_payload,
                source_name,
                output_path,
                log_fn,
                timings=timings,
            )
        except DocumentMindEmptyResultError as exc:
            last_empty_error = exc
            if attempt >= attempts:
                raise
            log_fn(
                "Document Mind 已成功但结果为空，准备重新提交该分片；"
                f"attempt={attempt}/{attempts}"
            )
            _sleep_document_mind_retry(retry_delay)
            continue

        log_fn(f"Document Mind 转换完成，共 {len(blocks)} 个内容块")
        return blocks

    raise last_empty_error or DocumentMindEmptyResultError(EMPTY_RESULT_MESSAGE)


def _get_and_convert_document_mind_result(
    client: object,
    job_id: str,
    status_payload: object,
    source_name: str,
    output_path: Path,
    log_fn: Callable[[str], None],
    *,
    timings: _DocumentMindParseTimings | None = None,
) -> list[ContentBlock]:
    status_blocks = _try_convert_document_mind_status_result(
        status_payload,
        source_name,
        output_path,
        log_fn,
        timings=timings,
    )
    if status_blocks is not None:
        return status_blocks

    attempts = 1 + _env_int(
        "ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES",
        default=DEFAULT_RESULT_FETCH_RETRIES,
        minimum=0,
    )
    retry_delay = _env_float(
        "ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRY_DELAY",
        default=DEFAULT_EMPTY_RESULT_RETRY_DELAY,
        minimum=0.0,
    )
    last_empty_error: DocumentMindEmptyResultError | None = None

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            log_fn(f"Document Mind 结果为空，重新获取解析结果 {attempt}/{attempts}")
        fetch_start = time.monotonic()
        try:
            payload = _get_document_mind_result(client, job_id)
        finally:
            if timings is not None:
                timings.observe_ms("resultFetchMs", fetch_start, max_key="resultFetchMsMax")
        convert_start = time.monotonic()
        payload = _merge_document_mind_payload(payload, status_payload)
        try:
            blocks = convert_document_mind_result(payload, source_name, output_path)
            if timings is not None:
                timings.observe_ms("convertMs", convert_start, max_key="convertMsMax")
            return blocks
        except DocumentMindEmptyResultError as exc:
            last_empty_error = exc
            if timings is not None:
                timings.observe_ms("convertMs", convert_start, max_key="convertMsMax")
            log_fn(
                "Document Mind 返回空结果："
                f"{_summarize_document_mind_payload(payload)}"
            )
            if attempt >= attempts:
                raise
            _sleep_document_mind_retry(retry_delay)

    raise last_empty_error or DocumentMindEmptyResultError(EMPTY_RESULT_MESSAGE)


def _try_convert_document_mind_status_result(
    status_payload: object,
    source_name: str,
    output_path: Path,
    log_fn: Callable[[str], None],
    *,
    timings: _DocumentMindParseTimings | None = None,
) -> list[ContentBlock] | None:
    convert_start = time.monotonic()
    try:
        blocks = convert_document_mind_result(status_payload, source_name, output_path)
    except DocumentMindEmptyResultError:
        if timings is not None:
            timings.observe_ms("statusConvertMs", convert_start, max_key="statusConvertMsMax")
        return None
    if timings is not None:
        timings.observe_ms("statusConvertMs", convert_start, max_key="statusConvertMsMax")

    if not _has_primary_document_mind_blocks(blocks):
        return None

    if timings is not None:
        timings.increment("resultFetchSkippedByStatus")
    log_fn("Document Mind 状态响应已包含可解析结果，跳过单独结果获取。")
    return blocks


def _has_primary_document_mind_blocks(blocks: list[ContentBlock]) -> bool:
    return any(block.type != BlockType.IMAGE or bool((block.text or "").strip()) for block in blocks)


def parse_pdf_sharded(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
    *,
    inspection: PdfInspection | None = None,
    sharding_config: dict[str, int | float | bool] | None = None,
    credential_pool: _DocumentMindCredentialPool | None = None,
    timings: _DocumentMindParseTimings | None = None,
) -> list[ContentBlock]:
    pool = credential_pool or _resolve_document_mind_credential_pool()
    sharding_cfg = sharding_config or _get_document_mind_sharding_config()
    timings = timings or _DocumentMindParseTimings()
    source_name = original_name or Path(pdf_path).name
    if inspection is None:
        inspect_start = time.monotonic()
        try:
            inspection = inspect_pdf(
                pdf_path,
                text_sample_pages=int(sharding_cfg["text_sample_pages"]),
                profile_pages=bool(sharding_cfg.get("weighted_sharding_enabled", False)),
            )
        finally:
            timings.observe_ms("inspectMs", inspect_start)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        if log_fn:
            log_fn(message)

    hedge_cfg = _get_document_mind_hedge_config()
    hedge_enabled = bool(hedge_cfg["enabled"]) and int(hedge_cfg["max_extra_attempts"]) > 0

    with _document_mind_shard_dir(output_path, persistent=hedge_enabled) as shard_tmp_dir:
        scheduling_capacity = min(int(sharding_cfg["max_concurrency"]), pool.capacity)
        pages_per_shard = _get_effective_document_mind_pages_per_shard(
            inspection,
            sharding_cfg,
            pool_capacity=scheduling_capacity,
        )
        timings.set_value("configuredPagesPerShard", int(sharding_cfg["pages_per_shard"]))
        timings.set_value("effectivePagesPerShard", pages_per_shard)
        timings.set_value("parseSchedulingCapacity", scheduling_capacity)
        timings.set_value("parseServiceInputPages", inspection.page_count)
        split_start = time.monotonic()
        weighted_enabled = bool(sharding_cfg.get("weighted_sharding_enabled", False))
        save_options = _get_document_mind_shard_save_options(sharding_cfg)
        timings.set_value("parseShardSaveGarbage", save_options.garbage)
        timings.set_value("parseShardSaveDeflate", 1 if save_options.deflate else 0)
        if weighted_enabled and inspection.page_profiles:
            shards = split_pdf_to_weighted_shards(
                pdf_path,
                shard_tmp_dir,
                page_profiles=inspection.page_profiles,
                max_pages_per_shard=pages_per_shard,
                save_options=save_options,
            )
        else:
            shards = split_pdf_to_shards(
                pdf_path,
                shard_tmp_dir,
                pages_per_shard=pages_per_shard,
                save_options=save_options,
            )
        timings.observe_ms("splitMs", split_start)
        _record_document_mind_shard_weight_metrics(shards, timings, weighted_enabled=weighted_enabled)
        timings.set_value("parseServiceShardBytes", sum(int(shard.path.stat().st_size) for shard in shards))
        if not shards:
            return []

        max_workers = min(int(sharding_cfg["max_concurrency"]), pool.capacity, len(shards))
        pool.set_active_key_target(max_workers)
        timings.set_value("parseShardCount", len(shards))
        timings.set_value("parseWorkerCount", max_workers)
        _log(
            "启用 Document Mind 分片解析："
            f"{inspection.page_count} 页，{inspection.file_size_mb:.1f} MB，"
            f"{len(shards)} 个 shard，每片最多 {pages_per_shard} 页，"
            f"并发 {max_workers}"
        )
        heavy_first = bool(sharding_cfg.get("heavy_shard_first", False))
        scheduled_shards = _order_document_mind_shards_for_submission(
            shards,
            heavy_first=heavy_first,
        )
        timings.set_value("parseHeavyShardFirstEnabled", 1 if heavy_first else 0)
        if heavy_first and scheduled_shards:
            _log(
                "Document Mind 分片提交顺序：重片优先，"
                f"首个 shard #{scheduled_shards[0].index} weight={scheduled_shards[0].weight}"
            )

        timings.set_value("parseHedgeEnabled", 1 if hedge_cfg["enabled"] else 0)
        timings.set_value("parseHedgeAfterMs", int(float(hedge_cfg["after_seconds"]) * 1000))
        timings.set_value("parseHedgeMaxExtraAttempts", int(hedge_cfg["max_extra_attempts"]))

        if hedge_cfg["enabled"] and int(hedge_cfg["max_extra_attempts"]) > 0 and max_workers > 1:
            records = _parse_document_mind_shards_with_hedging(
                scheduled_shards,
                output_path,
                source_name,
                max_workers,
                _log,
                pool,
                timings,
                hedge_after_seconds=float(hedge_cfg["after_seconds"]),
                hedge_max_extra_attempts=int(hedge_cfg["max_extra_attempts"]),
            )
            timings.set_value(
                "parseServiceRequests",
                max(int(timings.metrics().get("parseServiceRequests", 0) or 0), len(scheduled_shards)),
            )
            _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
            merge_start = time.monotonic()
            blocks = merge_shard_records(records)
            timings.observe_ms("mergeShardMs", merge_start, max_key="mergeShardMsMax")
            _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
            if blocks:
                min_page = min(block.page_idx for block in blocks) + 1
                max_page = max(block.page_idx for block in blocks) + 1
                page_range = f"P{min_page}-P{max_page}"
            else:
                page_range = "empty"
            _log(
                "Document Mind hedged sharding merged: "
                f"{len(shards)} shards, {len(blocks)} blocks, pages {page_range}"
            )
            return blocks

        records: list[ShardBlockRecord] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _parse_document_mind_shard,
                    shard,
                    output_path,
                    source_name,
                    len(shards),
                    _log,
                    pool,
                    timings,
                ): shard
                for shard in scheduled_shards
            }
            for future in as_completed(futures):
                shard = futures[future]
                try:
                    shard_records = future.result()
                except Exception as exc:
                    _set_last_document_mind_key_pool_metrics(
                        _collect_document_mind_metrics(pool, timings)
                    )
                    raise RuntimeError(
                        "Document Mind 分片解析失败："
                        f"shard #{shard.index} {shard.display_range}"
                    ) from exc
                records.extend(shard_records)
                _log(
                    f"[shard {shard.index:03d}/{len(shards):03d}] "
                    f"合并完成，输出 {len(shard_records)} 个内容块"
                )

        timings.set_value(
            "parseServiceRequests",
            max(int(timings.metrics().get("parseServiceRequests", 0) or 0), len(shards)),
        )
        _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
        merge_start = time.monotonic()
        blocks = merge_shard_records(records)
        timings.observe_ms("mergeShardMs", merge_start, max_key="mergeShardMsMax")
        _set_last_document_mind_key_pool_metrics(_collect_document_mind_metrics(pool, timings))
        if blocks:
            min_page = min(block.page_idx for block in blocks) + 1
            max_page = max(block.page_idx for block in blocks) + 1
            page_range = f"P{min_page}-P{max_page}"
        else:
            page_range = "empty"
        _log(
            "Document Mind 分片解析合并完成："
            f"{len(shards)} 个 shard，{len(blocks)} 个内容块，全局页码范围 {page_range}"
        )
        return blocks


def _parse_document_mind_shard(
    shard: PdfShard,
    output_path: Path,
    source_name: str,
    total_shards: int,
    log_fn: Callable[[str], None],
    credential_pool: _DocumentMindCredentialPool,
    timings: _DocumentMindParseTimings | None = None,
) -> list[ShardBlockRecord]:
    return _parse_document_mind_shard_once(
        shard,
        output_path,
        source_name,
        total_shards,
        log_fn,
        credential_pool,
        timings,
        attempt_index=0,
        is_hedge=False,
    )


def _record_document_mind_shard_weight_metrics(
    shards: list[PdfShard],
    timings: _DocumentMindParseTimings,
    *,
    weighted_enabled: bool,
) -> None:
    timings.set_value("parseWeightedShardingEnabled", 1 if weighted_enabled else 0)
    if not shards:
        return
    weights = [max(0, int(shard.weight)) for shard in shards]
    nonzero_weights = [weight for weight in weights if weight > 0]
    if not nonzero_weights:
        return
    timings.set_value("parseShardWeightTotal", sum(nonzero_weights))
    timings.set_value("parseShardWeightMax", max(nonzero_weights))
    timings.set_value("parseShardWeightMin", min(nonzero_weights))
    timings.set_value("parseShardWeightAvg", int(sum(nonzero_weights) / len(nonzero_weights)))
    heaviest = max(shards, key=lambda shard: int(shard.weight))
    timings.set_value("parseHeaviestShardIndex", heaviest.index)
    timings.set_value("parseHeaviestShardPages", heaviest.page_count)


def _order_document_mind_shards_for_submission(
    shards: list[PdfShard],
    *,
    heavy_first: bool,
) -> list[PdfShard]:
    if not heavy_first:
        return list(shards)
    return sorted(shards, key=lambda shard: (-int(shard.weight), shard.index))


def _parse_document_mind_shard_once(
    shard: PdfShard,
    output_path: Path,
    source_name: str,
    total_shards: int,
    log_fn: Callable[[str], None],
    credential_pool: _DocumentMindCredentialPool,
    timings: _DocumentMindParseTimings | None = None,
    *,
    attempt_index: int = 0,
    is_hedge: bool = False,
    excluded_aliases: set[str] | None = None,
    used_aliases: set[str] | None = None,
    used_aliases_lock: threading.Lock | None = None,
    allow_excluded_fallback: bool = True,
    count_hedge_submission: bool = False,
) -> list[ShardBlockRecord]:
    prefix = f"[shard {shard.index:03d}/{total_shards:03d} {shard.display_range}]"
    if is_hedge:
        prefix = f"{prefix} [hedge {attempt_index}]"

    def shard_log(message: str) -> None:
        log_fn(f"{prefix} {message}")

    shard_start = time.monotonic()
    shard_log(f"开始 Document Mind 解析：{shard.path.name}，{shard.page_count} 页")
    shard_output_dir = output_path / "document_mind_shards" / f"shard_{shard.index:03d}"
    try:
        blocks = _parse_pdf_single(
            str(shard.path),
            str(shard_output_dir),
            shard_log,
            source_name,
            credential_pool=credential_pool,
            timings=timings,
            acquire_wait_seconds=0.0 if is_hedge else DEFAULT_KEY_ACQUIRE_WAIT_SECONDS,
            excluded_aliases=excluded_aliases,
            used_aliases=used_aliases,
            used_aliases_lock=used_aliases_lock,
            allow_excluded_fallback=allow_excluded_fallback,
            count_hedge_submission=count_hedge_submission,
        )

        merge_start = time.monotonic()
        records = offset_shard_blocks(blocks, shard, source_name)
        if timings is not None:
            timings.observe_ms("mergeShardMs", merge_start, max_key="mergeShardMsMax")
        shard_log(f"解析完成，局部内容块 {len(blocks)} 个，已应用页码 offset={shard.start_page}")
        return records
    finally:
        if timings is not None:
            timings.observe_ms("shardWallMs", shard_start, max_key="shardWallMsMax")


def _parse_document_mind_shards_with_hedging(
    shards: list[PdfShard],
    output_path: Path,
    source_name: str,
    max_workers: int,
    log_fn: Callable[[str], None],
    credential_pool: _DocumentMindCredentialPool,
    timings: _DocumentMindParseTimings,
    *,
    hedge_after_seconds: float,
    hedge_max_extra_attempts: int,
) -> list[ShardBlockRecord]:
    records: list[ShardBlockRecord] = []
    shard_states = {shard.index: _DocumentMindShardState(shard=shard) for shard in shards}
    pending_shards = deque(shards)
    future_to_state: dict[Future[list[ShardBlockRecord]], _DocumentMindShardState] = {}
    future_is_hedge: dict[Future[list[ShardBlockRecord]], bool] = {}
    completed_shards: set[int] = set()
    hedge_after = max(0.0, float(hedge_after_seconds))
    max_extra = max(0, int(hedge_max_extra_attempts))
    hedge_worker_limit = min(len(shards) + max_extra, max_workers + max_extra, credential_pool.capacity)
    timings.set_value("parseHedgeWorkerLimit", hedge_worker_limit)

    def run_attempt(
        state: _DocumentMindShardState,
        *,
        attempt_index: int,
        is_hedge: bool,
        excluded_aliases: set[str],
    ) -> list[ShardBlockRecord]:
        if not is_hedge and state.primary_started_at <= 0:
            state.primary_started_at = time.monotonic()
        return _parse_document_mind_shard_once(
            state.shard,
            output_path,
            source_name,
            len(shards),
            log_fn,
            credential_pool,
            timings,
            attempt_index=attempt_index,
            is_hedge=is_hedge,
            excluded_aliases=excluded_aliases,
            used_aliases=state.aliases,
            used_aliases_lock=state.aliases_lock,
            allow_excluded_fallback=not is_hedge,
            count_hedge_submission=is_hedge,
        )

    def submit_attempt(
        executor: ThreadPoolExecutor,
        state: _DocumentMindShardState,
        *,
        is_hedge: bool,
    ) -> None:
        with state.aliases_lock:
            excluded = set(state.aliases) if is_hedge else set()
        if is_hedge and timings is not None:
            timings.increment("parseHedgeAttempts")
        future = executor.submit(
            run_attempt,
            state,
            attempt_index=state.hedge_count if is_hedge else 0,
            is_hedge=is_hedge,
            excluded_aliases=excluded,
        )
        if is_hedge:
            state.hedge_count += 1
        future_to_state[future] = state
        future_is_hedge[future] = is_hedge

    executor = ThreadPoolExecutor(max_workers=hedge_worker_limit)
    try:
        while pending_shards and len(future_to_state) < max_workers:
            shard = pending_shards.popleft()
            submit_attempt(executor, shard_states[shard.index], is_hedge=False)

        while future_to_state:
            wait_timeout = 0.2
            if not pending_shards:
                now = time.monotonic()
                hedge_deadlines = [
                    state.primary_started_at + hedge_after
                    for state in shard_states.values()
                    if state.shard.index not in completed_shards
                    and state.hedge_count < max_extra
                    and state.primary_started_at > 0
                ]
                if hedge_deadlines:
                    wait_timeout = max(0.0, min(wait_timeout, min(hedge_deadlines) - now))
            done, _ = wait(
                set(future_to_state),
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )

            if not done:
                if not pending_shards:
                    now = time.monotonic()
                    for state in shard_states.values():
                        if state.shard.index in completed_shards:
                            continue
                        if state.hedge_count >= max_extra:
                            continue
                        if len(future_to_state) >= hedge_worker_limit:
                            break
                        if state.primary_started_at > 0 and now - state.primary_started_at >= hedge_after:
                            submit_attempt(executor, state, is_hedge=True)
                            log_fn(
                                f"[shard {state.shard.index:03d}/{len(shards):03d} {state.shard.display_range}] "
                                f"hedge submitted after {int(hedge_after * 1000)}ms"
                            )
                continue

            for future in done:
                state = future_to_state.pop(future)
                is_hedge = future_is_hedge.pop(future, False)
                if state.shard.index in completed_shards:
                    continue
                try:
                    shard_records = future.result()
                except Exception as exc:
                    state.errors.append(exc)
                    active_for_shard = any(
                        other_state.shard.index == state.shard.index
                        for other_state in future_to_state.values()
                    )
                    if active_for_shard:
                        continue
                    _set_last_document_mind_key_pool_metrics(
                        _collect_document_mind_metrics(credential_pool, timings)
                    )
                    raise RuntimeError(
                        "Document Mind hedged shard parse failed: "
                        f"shard #{state.shard.index} {state.shard.display_range}"
                    ) from exc

                completed_shards.add(state.shard.index)
                records.extend(shard_records)
                if is_hedge:
                    timings.increment("parseHedgeWins")
                else:
                    timings.increment("parseHedgePrimaryWins")
                log_fn(
                    f"[shard {state.shard.index:03d}/{len(shards):03d}] "
                    f"hedged merge complete, blocks={len(shard_records)}"
                )

                for other_future, other_state in list(future_to_state.items()):
                    if other_state.shard.index == state.shard.index:
                        other_future.cancel()
                        future_to_state.pop(other_future, None)
                        future_is_hedge.pop(other_future, None)

                while pending_shards and len(future_to_state) < max_workers:
                    shard = pending_shards.popleft()
                    submit_attempt(executor, shard_states[shard.index], is_hedge=False)

                if len(completed_shards) >= len(shards):
                    for remaining in future_to_state:
                        remaining.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    return records

        executor.shutdown(wait=False, cancel_futures=True)
        return records
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise


def convert_document_mind_result(
    payload: object,
    source_file: str,
    output_path: Path | str = Path("data/output"),
) -> list[ContentBlock]:
    output = Path(output_path)
    if isinstance(payload, str):
        blocks = _markdown_to_blocks(payload, source_file, output_path)
        if blocks:
            return blocks
        raise DocumentMindEmptyResultError(EMPTY_RESULT_MESSAGE)
    if not isinstance(payload, dict):
        raise ValueError("Document Mind result must be a dict or markdown string")

    payload_data = _extract_document_mind_data(payload)
    if not isinstance(payload_data, dict):
        raise ValueError("Document Mind result must be a dict or markdown string")

    maybe_result = _first_present(payload_data, ("result",))
    result = maybe_result if isinstance(maybe_result, dict) else payload_data
    record_sources = _collect_document_mind_record_sources(result)
    page_image_map = _collect_document_mind_page_images(result)
    markdown = (
        _first_present(result, ("markdown",))
        or _first_present(result, ("md",))
        or _first_present(result, ("contentMarkdown",))
        or _first_present(result, ("text",))
    )

    blocks: list[ContentBlock] = []
    if isinstance(markdown, str):
        blocks.extend(_markdown_to_blocks(markdown, source_file, output))
    for record_source in record_sources:
        decoded_source = _decode_json_value(record_source)
        if isinstance(decoded_source, list):
            record_blocks = _records_to_blocks(decoded_source, source_file, output, page_image_map)
        elif isinstance(decoded_source, dict):
            record_blocks = _records_to_blocks([decoded_source], source_file, output, page_image_map)
        else:
            continue
        blocks.extend(_dedupe_blocks(record_blocks, blocks))

    if blocks:
        return blocks

    raise DocumentMindEmptyResultError(EMPTY_RESULT_MESSAGE)


def _records_to_blocks(
    records: list[object],
    source_file: str,
    output_path: Path,
    page_image_map: dict[int, str] | None = None,
    page_context: int | None = None,
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        page_idx = _extract_page_idx(record, default=page_context)
        nested = _first_present(
            record,
            ("blocks",),
            ("pages",),
            ("layouts",),
            ("paragraphs",),
            ("layoutInfo",),
            ("visualLayoutInfo",),
            ("outputFormatResult",),
            ("outputImageUrls",),
        )
        if isinstance(nested, list):
            blocks.extend(_records_to_blocks(nested, source_file, output_path, page_image_map, page_idx))
            continue
        if isinstance(nested, dict):
            blocks.extend(_records_to_blocks([nested], source_file, output_path, page_image_map, page_idx))
            continue

        raw_type_value = _first_present(
            record,
            ("type",),
            ("category",),
            ("blockType",),
            ("layoutType",),
            ("class",),
        )
        raw_type = str(raw_type_value or ("image" if _extract_image_path(record) else "text")).lower()
        block_type = _map_document_mind_type(raw_type)
        text_value = _first_present(
            record,
            ("text",),
            ("content",),
            ("markdown",),
            ("html",),
            ("textContent",),
            ("contentText",),
        )
        text = str(text_value or "").strip()
        table_html = None
        if block_type == BlockType.TABLE:
            table_html = str(
                _first_present(record, ("html",), ("tableHtml",), ("table_html",))
                or text
            )
        image_path = _extract_image_path(record)
        if not image_path and block_type == BlockType.IMAGE and page_image_map:
            image_path = page_image_map.get(page_idx)
        if image_path:
            image_path = _resolve_image_path(str(image_path), output_path)

        bbox = _normalize_bbox(_first_present(record, ("bbox",), ("position",)))
        text_level = _first_present(record, ("level",), ("text_level",))

        if not text and block_type != BlockType.IMAGE:
            continue
        blocks.append(
            ContentBlock(
                type=block_type,
                text=text,
                page_idx=page_idx,
                text_level=int(text_level) if str(text_level or "").isdigit() else None,
                is_table=block_type == BlockType.TABLE,
                table_html=table_html,
                source_file=source_file,
                image_path=image_path,
                bbox=bbox,
            )
        )
    return blocks


def _markdown_to_blocks(
    markdown: str,
    source_file: str,
    output_path: Path | str = Path("data/output"),
) -> list[ContentBlock]:
    output = Path(output_path)
    blocks: list[ContentBlock] = []
    table_buffer: list[str] = []

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        table_text = "\n".join(table_buffer).strip()
        blocks.append(
            ContentBlock(
                type=BlockType.TABLE,
                text=table_text,
                page_idx=0,
                is_table=True,
                table_html=table_text,
                source_file=source_file,
            )
        )
        table_buffer = []

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_table()
            continue
        image_blocks, remaining_text = _extract_markdown_image_blocks(stripped, source_file, output)
        if image_blocks:
            flush_table()
            blocks.extend(image_blocks)
            stripped = remaining_text.strip()
            if not stripped:
                continue
        if stripped:
            stripped = MARKDOWN_IMAGE_RE.sub(lambda match: match.group(1).strip(), stripped).strip()
        if not stripped:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buffer.append(stripped)
            continue
        flush_table()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            block_type = BlockType.TITLE
            text_level = level
        else:
            text = stripped
            block_type = BlockType.TEXT
            text_level = None
        if text:
            blocks.append(
                ContentBlock(
                    type=block_type,
                    text=text,
                    page_idx=0,
                    text_level=text_level,
                    source_file=source_file,
                )
            )
    flush_table()
    return blocks


def _extract_markdown_image_blocks(line: str, source_file: str, output_path: Path) -> tuple[list[ContentBlock], str]:
    blocks: list[ContentBlock] = []
    for match in MARKDOWN_IMAGE_RE.finditer(line):
        alt_text = match.group(1).strip()
        image_path = _resolve_image_path(match.group(2).strip(), output_path)
        blocks.append(
            ContentBlock(
                type=BlockType.IMAGE,
                text=alt_text,
                page_idx=0,
                source_file=source_file,
                image_path=image_path,
            )
        )
    return blocks, MARKDOWN_IMAGE_RE.sub("", line)


def _merge_document_mind_payload(result_payload: object, status_payload: object) -> object:
    result_data = _extract_document_mind_data(result_payload)
    status_data = _extract_document_mind_data(status_payload)
    if not isinstance(result_data, dict) or not isinstance(status_data, dict):
        return result_payload

    status_records = _flatten_record_sources(_collect_document_mind_record_sources(status_data))
    status_images = _collect_document_mind_page_images(status_data)
    if not status_records and status_images:
        status_records = [
            {"type": "image", "page_idx": page_idx, "imageUrl": image_path}
            for page_idx, image_path in sorted(status_images.items())
        ]
    if not status_records and not status_images:
        return result_data

    merged = dict(result_data)
    if status_records:
        existing = _first_present(merged, ("visualLayoutInfo",), ("layoutInfo",), ("pages",))
        if existing is None:
            merged["visualLayoutInfo"] = status_records
        else:
            merged.setdefault("documentMindStatusRecords", status_records)
    if status_images:
        merged.setdefault("documentMindStatusPageImages", status_images)
    return merged


def _summarize_document_mind_payload(payload: object) -> str:
    data = _extract_document_mind_data(payload)
    parts = [f"type={type(data).__name__}"]
    if isinstance(data, str):
        parts.append(f"chars={len(data.strip())}")
        return " ".join(parts)
    if not isinstance(data, dict):
        return " ".join(parts)

    keys = sorted(str(key) for key in data.keys())[:12]
    parts.append(f"keys={','.join(keys) if keys else '-'}")
    maybe_result = _first_present(data, ("result",))
    result = maybe_result if isinstance(maybe_result, dict) else data
    markdown = (
        _first_present(result, ("markdown",))
        or _first_present(result, ("md",))
        or _first_present(result, ("contentMarkdown",))
        or _first_present(result, ("text",))
    )
    if isinstance(markdown, str):
        parts.append(f"markdownChars={len(markdown.strip())}")
    parts.append(f"recordSources={len(_collect_document_mind_record_sources(result))}")
    parts.append(f"pageImages={len(_collect_document_mind_page_images(result))}")
    return " ".join(parts)


def _sleep_document_mind_retry(delay_seconds: float) -> None:
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def _extract_document_mind_data(payload: object) -> object:
    decoded = _decode_json_value(payload)
    if not isinstance(decoded, dict):
        return decoded
    data = _first_present(
        decoded,
        ("body", "data"),
        ("data",),
        ("result",),
    )
    if data is None:
        return decoded
    return _decode_json_value(data)


def _decode_json_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _flatten_record_sources(sources: list[object]) -> list[object]:
    flattened: list[object] = []

    def visit(value: object) -> None:
        decoded = _decode_json_value(value)
        if isinstance(decoded, list):
            for item in decoded:
                visit(item)
            return
        if isinstance(decoded, dict):
            flattened.append(decoded)

    for source in sources:
        visit(source)
    return flattened


def _collect_document_mind_record_sources(payload: object) -> list[object]:
    sources: list[object] = []

    def add_source(value: object) -> None:
        decoded = _decode_json_value(value)
        if isinstance(decoded, list):
            sources.append(decoded)
        elif isinstance(decoded, dict):
            visit(decoded)

    def visit(value: object) -> None:
        decoded = _decode_json_value(value)
        if isinstance(decoded, list):
            if any(isinstance(_decode_json_value(item), dict) for item in decoded):
                sources.append(decoded)
            return
        if not isinstance(decoded, dict):
            return

        for key, child in decoded.items():
            if not isinstance(key, str):
                continue
            normalized = key.lower()
            if normalized in {
                "blocks",
                "pages",
                "layouts",
                "layoutinfo",
                "visuallayoutinfo",
                "paragraphs",
                "content",
                "outputformatresult",
                "outputimageurls",
                "documentmindstatusrecords",
            }:
                add_source(child)
            elif normalized in {"data", "result", "value", "output", "outputresult"}:
                visit(child)

    visit(payload)
    return sources


def _collect_document_mind_page_images(payload: object) -> dict[int, str]:
    images: dict[int, str] = {}

    def remember(page_idx: int, image_path: object) -> None:
        if isinstance(image_path, str) and image_path.strip():
            images.setdefault(max(page_idx, 0), image_path.strip())

    def visit(value: object, page_context: int | None = None) -> None:
        decoded = _decode_json_value(value)
        if isinstance(decoded, list):
            for index, item in enumerate(decoded):
                if isinstance(item, str):
                    remember(index if page_context is None else page_context, item)
                else:
                    visit(item, page_context)
            return
        if not isinstance(decoded, dict):
            return

        page_idx = _extract_page_idx(decoded, default=page_context)
        remember(page_idx, _extract_image_path(decoded))

        image_list = _first_present(
            decoded,
            ("imageUrls",),
            ("image_urls",),
            ("pageImageUrls",),
            ("page_image_urls",),
            ("outputImageUrls",),
            ("output_image_urls",),
        )
        if isinstance(_decode_json_value(image_list), list):
            for index, item in enumerate(_decode_json_value(image_list)):
                if isinstance(item, str):
                    remember(index, item)
                else:
                    visit(item, page_idx)

        for child in decoded.values():
            visit(child, page_idx)

    visit(payload)
    return images


def _map_document_mind_type(raw_type: str) -> BlockType:
    if raw_type in {"title", "header", "heading"}:
        return BlockType.TITLE
    if raw_type in {"table", "tablebody", "table_html"}:
        return BlockType.TABLE
    if raw_type in {"image", "figure", "pic", "picture", "figurecaption", "figure_caption"}:
        return BlockType.IMAGE
    return BlockType.TEXT


def _extract_image_path(record: dict[str, object]) -> object:
    return _first_present(
        record,
        ("imagePath",),
        ("image_path",),
        ("imgPath",),
        ("img_path",),
        ("imageUrl",),
        ("image_url",),
        ("pageImageUrl",),
        ("page_image_url",),
        ("originImageUrl",),
        ("origin_image_url",),
        ("url",),
        ("src",),
    )


def _resolve_image_path(value: str, output_path: Path) -> str:
    if _is_url(value) or _is_data_url(value) or os.path.isabs(value):
        return value
    return str(output_path / value)


def _is_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _is_data_url(value: str) -> bool:
    return value.lower().startswith("data:image/")


def _dedupe_blocks(candidates: list[ContentBlock], existing: list[ContentBlock]) -> list[ContentBlock]:
    seen = {_block_dedupe_key(block) for block in existing}
    deduped: list[ContentBlock] = []
    for block in candidates:
        key = _block_dedupe_key(block)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def _block_dedupe_key(block: ContentBlock) -> tuple[str, int, str, str]:
    return (
        block.type.value,
        block.page_idx,
        block.image_path or "",
        block.text.strip(),
    )


def _normalize_page_idx(value: object) -> int:
    if isinstance(value, list) and value:
        value = value[0]
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 0
    return max(page - 1, 0)


def _extract_page_idx(record: dict[str, object], default: int | None = None) -> int:
    page_idx = _first_present(record, ("page_idx",))
    if page_idx is not None:
        try:
            return max(int(page_idx), 0)
        except (TypeError, ValueError):
            return default or 0
    page = _first_present(
        record,
        ("page",),
        ("pageNo",),
        ("pageNumber",),
        ("pageNum",),
        ("pageIdCurDoc",),
        ("page_id_cur_doc",),
    )
    if page is None:
        return default or 0
    return _normalize_page_idx(page)


def _normalize_bbox(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _create_document_mind_client(credential: _DocumentMindCredential | None = None) -> object:
    access_key_id = credential.access_key_id if credential else (
        os.getenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", "").strip()
        or os.getenv("OSS_ACCESS_KEY_ID", "").strip()
    )
    access_key_secret = credential.access_key_secret if credential else (
        os.getenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", "").strip()
        or os.getenv("OSS_ACCESS_KEY_SECRET", "").strip()
    )
    endpoint = os.getenv("ALIYUN_DOCUMENT_MIND_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT

    if not access_key_id or not access_key_secret:
        raise ValueError(
            "ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID/SECRET or OSS_ACCESS_KEY_ID/SECRET must be configured"
        )

    try:
        from alibabacloud_docmind_api20220711.client import Client as DocMindClient
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise ImportError(
            "Alibaba Document Mind SDK is not installed. "
            "Install dependencies from requirements.txt, including "
            "alibabacloud-docmind-api20220711."
        ) from exc

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
    )
    config.endpoint = endpoint
    return DocMindClient(config)


def _submit_document_mind_job(
    client: object,
    pdf_path: str,
    source_name: str,
    log_fn: Callable[[str], None],
) -> str:
    output_format = _parse_output_formats(os.getenv("ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT"))
    llm_enhancement = _env_bool("ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT", default=False)
    enhancement_mode = os.getenv("ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE", "VLM").strip() or "VLM"

    try:
        from alibabacloud_docmind_api20220711 import models as docmind_models
        from alibabacloud_tea_util import models as util_models
    except ImportError as exc:
        raise ImportError(
            "Alibaba Document Mind SDK is not installed. "
            "Install dependencies from requirements.txt, including "
            "alibabacloud-docmind-api20220711."
        ) from exc

    with open(pdf_path, "rb") as file:
        request = docmind_models.SubmitDocParserJobAdvanceRequest(
            file_url_object=file,
            file_name=source_name,
            file_name_extension=_file_extension(source_name),
            output_format=output_format,
            llm_enhancement=llm_enhancement,
            enhancement_mode=enhancement_mode,
        )
        response = client.submit_doc_parser_job_advance(request, util_models.RuntimeOptions())
    payload = _to_plain_data(response)
    _raise_document_mind_error(payload, "submit")
    job_id = _first_present(
        payload,
        ("body", "data", "id"),
        ("body", "data", "Id"),
        ("body", "data", "taskId"),
        ("body", "data", "TaskId"),
        ("data", "id"),
        ("data", "Id"),
        ("data", "taskId"),
        ("data", "TaskId"),
        ("job_id",),
        ("jobId",),
        ("Id",),
        ("taskId",),
    )
    if not job_id:
        raise RuntimeError(f"Document Mind submit response did not contain job_id: {payload}")
    log_fn(f"Document Mind submit output_format={output_format} llm_enhancement={llm_enhancement}")
    return str(job_id)


def _poll_document_mind_job(client: object, job_id: str, log_fn: Callable[[str], None]) -> object:
    timeout = int(os.getenv("ALIYUN_DOCUMENT_MIND_TIMEOUT", "1800"))
    poll_interval = float(os.getenv("ALIYUN_DOCUMENT_MIND_POLL_INTERVAL", "3"))

    try:
        from alibabacloud_docmind_api20220711 import models as docmind_models
    except ImportError as exc:
        raise ImportError(
            "Alibaba Document Mind SDK is not installed. "
            "Install dependencies from requirements.txt, including "
            "alibabacloud-docmind-api20220711."
        ) from exc

    deadline = time.monotonic() + timeout
    poll_count = 0
    while True:
        response = client.query_doc_parser_status(docmind_models.QueryDocParserStatusRequest(id=job_id))
        payload = _to_plain_data(response)
        _raise_document_mind_error(payload, "poll")
        status = str(_first_present(payload, ("body", "data", "status"), ("body", "data", "Status"), ("data", "status"), ("data", "Status")) or "").lower()
        poll_count += 1
        log_fn(f"  Document Mind 轮询 #{poll_count} 状态：{status or 'unknown'}")

        if status in {"success", "succeeded", "finished", "completed"}:
            return payload
        if status in {"fail", "failed", "error"}:
            raise RuntimeError(f"Document Mind job {job_id} failed: {payload}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Document Mind polling timed out after {timeout}s for job_id={job_id}")
        time.sleep(poll_interval)


def _get_document_mind_result(client: object, job_id: str) -> object:
    try:
        from alibabacloud_docmind_api20220711 import models as docmind_models
    except ImportError as exc:
        raise ImportError(
            "Alibaba Document Mind SDK is not installed. "
            "Install dependencies from requirements.txt, including "
            "alibabacloud-docmind-api20220711."
        ) from exc

    step_size = int(os.getenv("ALIYUN_DOCUMENT_MIND_LAYOUT_STEP_SIZE", str(DEFAULT_LAYOUT_STEP_SIZE)))
    request = docmind_models.GetDocParserResultRequest(
        id=job_id,
        layout_num=0,
        layout_step_size=step_size,
    )
    response = client.get_doc_parser_result(request)
    payload = _to_plain_data(response)
    _raise_document_mind_error(payload, "result")
    data = _first_present(payload, ("body", "data"), ("data",))
    if data is None:
        raise RuntimeError(f"Document Mind result response did not contain data: {payload}")
    if isinstance(data, str):
        try:
            import json

            return json.loads(data)
        except json.JSONDecodeError:
            return data
    return data


def _file_extension(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or None


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_output_formats(raw: str | None) -> list[str]:
    if raw is None or raw.strip() == "":
        return list(DEFAULT_OUTPUT_FORMATS)

    formats = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    invalid = [item for item in formats if item not in SUPPORTED_OUTPUT_FORMATS]
    if invalid:
        raise ValueError(
            "ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT only supports "
            f"{sorted(SUPPORTED_OUTPUT_FORMATS)}, got {invalid}"
        )
    return formats or list(DEFAULT_OUTPUT_FORMATS)


def _get_document_mind_sharding_config() -> dict[str, int | float | bool]:
    return {
        "enabled": _env_bool(
            "ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED",
            default=bool(DEFAULT_SHARDING["enabled"]),
        ),
        "min_file_mb": _env_float(
            "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB",
            default=float(DEFAULT_SHARDING["min_file_mb"]),
            minimum=1.0,
        ),
        "min_pages": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES",
            default=int(DEFAULT_SHARDING["min_pages"]),
            minimum=1,
        ),
        "pages_per_shard": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD",
            default=int(DEFAULT_SHARDING["pages_per_shard"]),
            minimum=1,
        ),
        "max_concurrency": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY",
            default=int(DEFAULT_SHARDING["max_concurrency"]),
            minimum=1,
        ),
        "min_pages_per_shard": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD",
            default=int(DEFAULT_SHARDING["min_pages_per_shard"]),
            minimum=1,
        ),
        "target_waves": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES",
            default=int(DEFAULT_SHARDING["target_waves"]),
            minimum=1,
        ),
        "text_sample_pages": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES",
            default=int(DEFAULT_SHARDING["text_sample_pages"]),
            minimum=0,
        ),
        "weighted_sharding_enabled": _env_bool(
            "ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED",
            default=bool(DEFAULT_SHARDING["weighted_sharding_enabled"]),
        ),
        "heavy_shard_first": _env_bool(
            "ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST",
            default=bool(DEFAULT_SHARDING["heavy_shard_first"]),
        ),
        "shard_save_garbage": _env_int(
            "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE",
            default=int(DEFAULT_SHARDING["shard_save_garbage"]),
            minimum=0,
        ),
        "shard_save_deflate": _env_bool(
            "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE",
            default=bool(DEFAULT_SHARDING["shard_save_deflate"]),
        ),
    }


def _get_document_mind_shard_save_options(
    sharding_cfg: dict[str, int | float | bool],
) -> PdfShardSaveOptions:
    return PdfShardSaveOptions(
        garbage=min(4, max(0, int(sharding_cfg.get("shard_save_garbage", DEFAULT_SHARDING["shard_save_garbage"])))),
        deflate=bool(sharding_cfg.get("shard_save_deflate", DEFAULT_SHARDING["shard_save_deflate"])),
    )


def _get_document_mind_hedge_config() -> dict[str, int | float | bool]:
    return {
        "enabled": _env_bool(
            "ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED",
            default=DEFAULT_HEDGED_SHARD_ENABLED,
        ),
        "after_seconds": _env_float(
            "ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS",
            default=DEFAULT_HEDGE_AFTER_SECONDS,
            minimum=0.0,
        ),
        "max_extra_attempts": _env_int(
            "ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS",
            default=DEFAULT_HEDGE_MAX_EXTRA_ATTEMPTS,
            minimum=0,
        ),
    }


def _should_parse_document_mind_with_shards(
    inspection: PdfInspection,
    sharding_cfg: dict[str, int | float | bool],
) -> bool:
    if not sharding_cfg["enabled"]:
        return False
    return (
        inspection.page_count > int(sharding_cfg["min_pages"])
        or inspection.file_size_mb > float(sharding_cfg["min_file_mb"])
    )


def _get_effective_document_mind_pages_per_shard(
    inspection: PdfInspection,
    sharding_cfg: dict[str, int | float | bool],
    *,
    pool_capacity: int = 1,
) -> int:
    configured_pages = max(1, int(sharding_cfg["pages_per_shard"]))
    max_file_mb = max(1.0, float(sharding_cfg["min_file_mb"]))
    if inspection.page_count <= 0:
        return configured_pages

    effective_pages = configured_pages
    if inspection.file_size_mb > max_file_mb:
        avg_page_mb = inspection.file_size_mb / inspection.page_count
        if avg_page_mb > 0:
            size_limited_pages = max(1, int(max_file_mb / avg_page_mb))
            effective_pages = min(effective_pages, size_limited_pages)

    capacity = max(1, int(pool_capacity))
    target_waves = max(1, int(sharding_cfg.get("target_waves", 4)))
    min_pages_per_shard = max(1, int(sharding_cfg.get("min_pages_per_shard", 20)))
    target_pages = max(
        min_pages_per_shard,
        int((inspection.page_count + capacity * target_waves - 1) / (capacity * target_waves)),
    )
    return max(1, min(effective_pages, target_pages))


@contextmanager
def _document_mind_shard_dir(output_path: Path, *, persistent: bool = False):
    if persistent:
        shard_dir = output_path / "document_mind_shards" / f"_pdf_shards_{int(time.time() * 1000)}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        yield shard_dir
        return
    with TemporaryDirectory(prefix="document_mind_shards_") as tmp_dir:
        yield Path(tmp_dir)


def _log_document_mind_inspection(
    inspection: PdfInspection,
    log_fn: Callable[[str], None],
) -> None:
    scanned_text = "是" if inspection.likely_scanned else "否"
    log_fn(
        "Document Mind PDF 体检："
        f"{inspection.page_count} 页，{inspection.file_size_mb:.1f} MB，"
        f"采样 {inspection.sampled_pages} 页文本 {inspection.sampled_text_chars} 字符，"
        f"疑似扫描型={scanned_text}"
    )


def _env_int(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw.strip()))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, *, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return max(minimum, float(default))
    try:
        return max(minimum, float(raw.strip()))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _raise_document_mind_error(payload: object, action: str) -> None:
    if not isinstance(payload, dict):
        return
    body = _first_present(payload, ("body",), ("Body",))
    body = body if isinstance(body, dict) else payload
    code = _first_present(body, ("Code",), ("code",))
    if not code or str(code).lower() in {"success", "ok"}:
        return
    message = _first_present(body, ("Message",), ("message",)) or ""
    request_id = (
        _first_present(body, ("RequestId",), ("requestId",))
        or _first_present(payload, ("headers", "x-acs-request-id"))
    )
    raise RuntimeError(
        f"Document Mind {action} failed: code={code} message={message} request_id={request_id}"
    )


def _to_plain_data(value: object) -> object:
    if hasattr(value, "to_map"):
        return value.to_map()
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _to_plain_data(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def _first_present(payload: object, *paths: tuple[str, ...]) -> object:
    for path in paths:
        current = payload
        for key in path:
            if isinstance(current, dict):
                actual_key = _find_dict_key(current, key)
                if actual_key is None:
                    current = None
                    break
                current = current[actual_key]
                continue
            current = None
            break
        if current is not None:
            return current
    return None


def _find_dict_key(payload: dict[object, object], key: str) -> object | None:
    if key in payload:
        return key
    lowered = key.lower()
    for candidate in payload:
        if isinstance(candidate, str) and candidate.lower() == lowered:
            return candidate
    return None
