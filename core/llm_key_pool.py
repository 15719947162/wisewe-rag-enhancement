from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

MAX_API_KEY_POOL_SIZE = 20


def parse_api_key_pool(primary_key: str | None, pool_value: str | None) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add_key(value: str | None) -> None:
        key = (value or "").strip()
        if not key or key in seen or len(keys) >= MAX_API_KEY_POOL_SIZE:
            return
        seen.add(key)
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


def is_throttle_error(error: object) -> bool:
    text = str(error or "").lower()
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
        "\u9650\u6d41",
        "\u8bf7\u6c42\u8fc7\u591a",
        "\u914d\u989d",
    )
    return any(marker in text for marker in markers)


@dataclass(frozen=True)
class ApiKeyLease:
    key: str
    alias: str


class ApiKeyPool:
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

    def acquire(self, exclude_keys: set[str] | None = None) -> ApiKeyLease | None:
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
            return ApiKeyLease(key=key, alias=self._aliases[key])

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

    def release(self, lease: ApiKeyLease | None) -> None:
        if lease is None:
            return
        with self._lock:
            if self._inflight[lease.key] > 0:
                self._inflight[lease.key] -= 1

    def record_attempt(
        self,
        lease: ApiKeyLease | None,
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

    def mark_throttled(self, lease: ApiKeyLease | None) -> None:
        if lease is None:
            return
        with self._lock:
            self._throttle_count += 1
            if self._cooldown_seconds > 0:
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
