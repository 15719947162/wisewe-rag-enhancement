"""
HTTP 客户端工具模块

本模块提供统一的 HTTP 客户端创建和管理功能，支持 httpx 和 OpenAI SDK。

主要功能：
1. **智能代理配置**：自动检测 Docker 环境，避免在容器内使用宿主机专用代理
2. **环境变量解析**：支持灵活的布尔值环境变量配置
3. **统一客户端创建**：提供工厂函数创建预配置的 HTTP 客户端

设计理念：
- 在 Docker 容器中运行时，自动忽略 `host.docker.internal` 这类宿主机专用代理
- 可通过 `HTTP_CLIENT_TRUST_ENV` 环境变量强制覆盖代理信任行为
- OpenAI 客户端默认配置合理的超时时间，避免长时间阻塞

使用示例：
    # 创建 httpx 客户端
    >>> client = create_httpx_client(timeout=30.0)
    >>> response = client.get("https://api.example.com/data")
    >>> print(response.json())

    # 创建 OpenAI 客户端
    >>> openai_client = create_openai_client(
    ...     api_key="sk-xxx",
    ...     base_url="https://api.openai.com/v1"
    ... )
    >>> completion = openai_client.chat.completions.create(
    ...     model="gpt-4",
    ...     messages=[{"role": "user", "content": "Hello"}]
    ... )

    # 检查是否应该信任环境代理
    >>> if should_trust_env_proxy():
    ...     print("将使用系统代理配置")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import openai


# Docker 容器专用的代理主机名集合
# host.docker.internal 是 Docker 提供的特殊 DNS 名称，用于从容器访问宿主机
_DOCKER_PROXY_HOSTS = {"host.docker.internal"}

# 布尔值环境变量的真值集合（支持多种常见格式）
_TRUE_VALUES = {"1", "true", "yes", "on"}

# 布尔值环境变量的假值集合
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool_env(name: str) -> bool | None:
    """
    解析布尔类型的环境变量。

    将环境变量值标准化为布尔值，支持多种常见格式。
    这使得配置更灵活，用户可以使用 "yes", "1", "true", "on" 等多种形式。

    参数:
        name: 环境变量名称

    返回:
        bool | None:
            - True: 如果环境变量值为 "1", "true", "yes", "on"（不区分大小写）
            - False: 如果环境变量值为 "0", "false", "no", "off"（不区分大小写）
            - None: 如果环境变量未设置或值无法识别

    示例:
        >>> import os
        >>> os.environ["DEBUG"] = "yes"
        >>> _parse_bool_env("DEBUG")
        True
        >>> os.environ["DEBUG"] = "0"
        >>> _parse_bool_env("DEBUG")
        False
        >>> _parse_bool_env("NOT_EXISTS")
        None
    """
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
    """
    检测当前进程是否运行在 Docker 容器中。

    通过两种方式检测：
    1. 检查 /.dockerenv 文件是否存在（Docker 自动创建）
    2. 检查环境变量 container 是否为 "docker"

    返回:
        bool: True 表示运行在容器中，False 表示运行在宿主机上

    示例:
        >>> if _is_running_in_container():
        ...     print("运行在 Docker 容器中")
        ... else:
        ...     print("运行在宿主机上")
    """
    return Path("/.dockerenv").exists() or os.environ.get("container", "").strip().lower() == "docker"


def _get_proxy_hosts() -> set[str]:
    """
    从环境变量中提取所有代理主机名。

    扫描常见的代理环境变量（HTTP_PROXY, HTTPS_PROXY 及其小写形式），
    提取其中的主机名部分。这用于检测是否配置了代理以及代理的目标主机。

    支持的环境变量:
        - HTTP_PROXY / http_proxy
        - HTTPS_PROXY / https_proxy

    返回:
        set[str]: 代理主机名集合（已转换为小写）

    示例:
        >>> import os
        >>> os.environ["HTTP_PROXY"] = "http://host.docker.internal:8080"
        >>> os.environ["HTTPS_PROXY"] = "http://proxy.example.com:3128"
        >>> hosts = _get_proxy_hosts()
        >>> print(hosts)
        {'host.docker.internal', 'proxy.example.com'}
    """
    return {
        (urlparse(value).hostname or "").lower()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
        for value in [os.environ.get(key, "").strip()]
        if value
    }


def should_trust_env_proxy() -> bool:
    """
    判断是否应该信任环境变量中的代理配置。

    这是核心的代理决策逻辑，用于智能处理 Docker 容器环境中的代理配置问题。

    决策逻辑（按优先级）：
    1. **显式覆盖**：如果设置了 `HTTP_CLIENT_TRUST_ENV` 环境变量，直接使用其值
    2. **Docker 环境检测**：如果配置的代理仅包含 Docker 专用主机名
       （如 `host.docker.internal`），则忽略这些代理配置
    3. **默认信任**：其他情况下信任环境变量中的代理配置

    为什么需要这个逻辑？
    在 Docker 容器中，经常会在宿主机上设置指向 `host.docker.internal` 的代理。
    这些代理在容器内无法使用，应该被自动忽略。但如果用户显式设置了
    `HTTP_CLIENT_TRUST_ENV=true`，则强制信任所有代理配置。

    返回:
        bool: True 表示应该使用环境代理，False 表示忽略

    环境变量:
        HTTP_CLIENT_TRUST_ENV: 可选，值为 "true"/"false"/"1"/"0" 等

    示例:
        # 在 Docker 容器中，自动忽略宿主机代理
        >>> import os
        >>> os.environ["HTTP_PROXY"] = "http://host.docker.internal:8080"
        >>> should_trust_env_proxy()
        False  # 自动忽略 Docker 专用代理

        # 强制信任代理
        >>> os.environ["HTTP_CLIENT_TRUST_ENV"] = "true"
        >>> should_trust_env_proxy()
        True

        # 配置了外部代理
        >>> os.environ["HTTP_PROXY"] = "http://proxy.company.com:3128"
        >>> should_trust_env_proxy()
        True
    """
    override = _parse_bool_env("HTTP_CLIENT_TRUST_ENV")
    if override is not None:
        return override

    proxy_hosts = _get_proxy_hosts()
    if proxy_hosts and proxy_hosts.issubset(_DOCKER_PROXY_HOSTS):
        return False
    return True


def create_httpx_client(**kwargs: Any) -> httpx.Client:
    """
    创建预配置的 httpx HTTP 客户端。

    这是创建 httpx 客户端的推荐方式，自动处理代理配置问题。
    默认行为会根据 `should_trust_env_proxy()` 的结果决定是否信任环境代理。

    参数:
        **kwargs: 传递给 httpx.Client 的参数，包括但不限于：
            - timeout: 超时时间（秒），可以是 float 或 httpx.Timeout 对象
            - base_url: 基础 URL，所有相对请求都会基于此 URL
            - headers: 默认请求头
            - auth: 认证信息
            - cookies: Cookie
            - verify: SSL 证书验证（默认 True）
            - follow_redirects: 是否跟随重定向（默认 False）

    返回:
        httpx.Client: 配置好的 HTTP 客户端实例

    注意:
        - 如果 kwargs 中未指定 `trust_env`，会自动根据环境设置
        - 客户端使用完毕后应调用 `.close()` 或使用上下文管理器

    示例:
        # 基本使用
        >>> client = create_httpx_client()
        >>> try:
        ...     response = client.get("https://api.example.com/data")
        ...     print(response.status_code)
        ... finally:
        ...     client.close()

        # 使用上下文管理器（推荐）
        >>> with create_httpx_client(timeout=30.0) as client:
        ...     response = client.post("https://api.example.com/submit", json={"key": "value"})
        ...     print(response.json())

        # 配置基础 URL 和自定义超时
        >>> with create_httpx_client(
        ...     base_url="https://api.example.com",
        ...     timeout=httpx.Timeout(10.0, read=30.0)
        ... ) as client:
        ...     response = client.get("/users")  # 实际请求 https://api.example.com/users
        ...     print(response.json())

        # 禁用 SSL 验证（仅用于测试环境）
        >>> with create_httpx_client(verify=False) as client:
        ...     response = client.get("https://self-signed.example.com")
    """
    kwargs.setdefault("trust_env", should_trust_env_proxy())
    return httpx.Client(**kwargs)


def create_openai_client(
    *,
    api_key: str,
    base_url: str | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> openai.OpenAI:
    """
    创建预配置的 OpenAI SDK 客户端。

    这是创建 OpenAI 客户端的推荐方式，自动配置合理的超时和代理设置。
    适用于 OpenAI API 及兼容的第三方 API（如 Azure OpenAI、国内代理等）。

    参数:
        api_key: API 密钥（必需）
            - OpenAI 官方：以 "sk-" 开头
            - 第三方服务：根据服务商要求填写
        base_url: API 基础 URL（可选）
            - None: 使用 OpenAI 官方 API（https://api.openai.com/v1）
            - 自定义 URL: 使用第三方服务或私有部署
        timeout: 超时配置（可选）
            - None: 使用默认超时（连接 20 秒，读/写 60 秒，连接池 20 秒）
            - float: 所有操作使用相同超时时间
            - httpx.Timeout: 细粒度控制各阶段超时

    返回:
        openai.OpenAI: 配置好的 OpenAI 客户端实例

    超时配置说明:
        默认超时配置为：
        - connect_timeout: 20 秒（建立 TCP 连接）
        - read_timeout: 60 秒（等待服务器响应）
        - write_timeout: 60 秒（发送请求数据）
        - pool_timeout: 20 秒（从连接池获取连接）

        这些值适合大多数场景，但对于流式响应或大量数据传输，
        可能需要调整 `read_timeout`。

    代理处理:
        客户端自动应用 `should_trust_env_proxy()` 的结果，
        正确处理 Docker 容器环境中的代理配置问题。

    示例:
        # 使用 OpenAI 官方 API
        >>> client = create_openai_client(api_key="sk-xxx")
        >>> completion = client.chat.completions.create(
        ...     model="gpt-4",
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
        >>> print(completion.choices[0].message.content)

        # 使用第三方服务（如国内代理）
        >>> client = create_openai_client(
        ...     api_key="your-api-key",
        ...     base_url="https://api.example.com/v1"
        ... )

        # 自定义超时（适用于慢速响应）
        >>> import httpx
        >>> client = create_openai_client(
        ...     api_key="sk-xxx",
        ...     timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)
        ... )

        # 使用环境变量中的 API Key
        >>> import os
        >>> client = create_openai_client(
        ...     api_key=os.environ.get("OPENAI_API_KEY"),
        ...     base_url=os.environ.get("OPENAI_BASE_URL")
        ... )

        # 流式响应（注意：超时仍适用于初始连接）
        >>> client = create_openai_client(api_key="sk-xxx")
        >>> stream = client.chat.completions.create(
        ...     model="gpt-4",
        ...     messages=[{"role": "user", "content": "写一首诗"}],
        ...     stream=True
        ... )
        >>> for chunk in stream:
        ...     print(chunk.choices[0].delta.content, end="")
    """
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
