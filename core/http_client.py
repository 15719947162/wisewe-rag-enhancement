from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import openai


_DOCKER_PROXY_HOSTS = {"host.docker.internal"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool_env(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _is_running_in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("container", "").strip().lower() == "docker"


def _get_proxy_hosts() -> set[str]:
    return {
        (urlparse(value).hostname or "").lower()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
        for value in [os.environ.get(key, "").strip()]
        if value
    }


def should_trust_env_proxy() -> bool:
    """Ignore Docker-only host proxies unless explicitly enabled."""
    override = _parse_bool_env("HTTP_CLIENT_TRUST_ENV")
    if override is not None:
        return override

    proxy_hosts = _get_proxy_hosts()
    if proxy_hosts and proxy_hosts.issubset(_DOCKER_PROXY_HOSTS):
        return False
    return True


def create_httpx_client(**kwargs: Any) -> httpx.Client:
    kwargs.setdefault("trust_env", should_trust_env_proxy())
    return httpx.Client(**kwargs)


def create_openai_client(
    *,
    api_key: str,
    base_url: str | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> openai.OpenAI:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "http_client": openai.DefaultHttpxClient(
            trust_env=should_trust_env_proxy(),
            timeout=timeout or httpx.Timeout(connect=20.0, read=60.0, write=60.0, pool=20.0),
        ),
    }
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)
