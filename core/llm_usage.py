from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


def _usage_value(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        value = usage.get(name, 0)
    else:
        value = getattr(usage, name, 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def extract_response_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


@dataclass
class TokenUsage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add_response(self, response: Any) -> None:
        self.add_usage(extract_response_usage(response))

    def add_usage(self, usage: dict[str, int]) -> None:
        self.requests += 1
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)

    def add(self, other: "TokenUsage") -> None:
        self.requests += other.requests
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens

    def to_metrics(self, prefix: str) -> dict[str, int]:
        return {
            f"{prefix}Requests": self.requests,
            f"{prefix}PromptTokens": self.prompt_tokens,
            f"{prefix}CompletionTokens": self.completion_tokens,
            f"{prefix}TotalTokens": self.total_tokens,
        }


class ThreadSafeTokenUsage:
    def __init__(self) -> None:
        self._usage = TokenUsage()
        self._lock = threading.Lock()

    def add_response(self, response: Any) -> None:
        usage = extract_response_usage(response)
        self.add_usage(usage)

    def add_usage(self, usage: dict[str, int]) -> None:
        with self._lock:
            self._usage.add_usage(usage)

    def snapshot(self) -> TokenUsage:
        with self._lock:
            return TokenUsage(
                requests=self._usage.requests,
                prompt_tokens=self._usage.prompt_tokens,
                completion_tokens=self._usage.completion_tokens,
                total_tokens=self._usage.total_tokens,
            )

    def to_metrics(self, prefix: str) -> dict[str, int]:
        return self.snapshot().to_metrics(prefix)
