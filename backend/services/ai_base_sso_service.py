"""
AI 基座单点登录（SSO）服务模块

本模块负责处理与 AI 基座系统的单点登录集成，主要包括：
1. SSO 配置加载与验证
2. 登录状态（state）的生成与校验
3. 授权码/JWT 凭证交换
4. 用户身份快照获取与刷新
5. 身份增量同步

工作流程：
用户点击登录 → 生成 state → 跳转 AI 基座登录页 → 回调带 code →
交换用户信息 → 创建本地会话 → 返回用户身份

关键概念：
- state: 防 CSRF 攻击的一次性令牌，存储在 cookie 中
- snapshot: AI 基座下发的用户身份快照，包含租户、角色、权限等信息
- delta sync: 增量同步，只拉取上次同步后有变化的用户身份
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from core.db.external_system_configs import get_active_external_system_config
from core.db.identity import (
    DEFAULT_SESSION_TTL_SECONDS,
    IdentityContext,
    create_auth_session,
    fingerprint_credential,
    get_latest_identity_sync_watermark,
    identity_to_payload,
    is_sso_credential_used,
    mark_sso_credential_used,
    record_identity_sync_run,
    upsert_identity_delta_snapshot,
    upsert_identity_snapshot_from_summary,
)
from core.runtime_settings import resolve_runtime_setting


# ========== 常量定义 ==========

# SSO 状态 cookie 名称，用于存储登录状态信息
STATE_COOKIE_NAME = "kb_sso_state"
# 身份增量同步的初始水位线（从 2000 年开始同步所有数据）
IDENTITY_DELTA_INITIAL_WATERMARK = "2000-01-01 00:00:00"
# HTTP 请求超时时间（秒），用于增量同步接口
IDENTITY_DELTA_HTTP_TIMEOUT_SECONDS = 300.0
# 日志记录器
logger = logging.getLogger(__name__)


class AiBaseSsoError(Exception):
    """
    AI 基座 SSO 错误异常

    当 SSO 流程中出现任何错误时抛出此异常，包含：
    - code: 错误代码，如 "STATE_REQUIRED"、"SSO_NOT_CONFIGURED"
    - message: 人类可读的错误信息
    - status_code: HTTP 状态码，用于返回给前端

    示例：
        raise AiBaseSsoError("STATE_EXPIRED", "SSO state is expired", 400)
    """
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class SsoConfig:
    """
    SSO 配置数据类

    存储所有与 AI 基座 SSO 相关的配置信息：
    - base_url: AI 基座 API 基础地址
    - client_id: 客户端 ID，用于标识本系统
    - client_secret: 客户端密钥，用于服务端认证
    - redirect_uri: 登录成功后的回调地址
    - console_base_url: 前端控制台基础地址
    - launch_base_url: 登录页地址（默认使用 base_url）
    - launch_path: 登录页路径
    - exchange_path: 凭证交换接口路径
    - user_snapshot_path_template: 用户快照接口路径模板
    - delta_path: 增量同步接口路径
    - session_ttl_seconds: 会话有效期（秒）
    """
    base_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    console_base_url: str = ""
    launch_base_url: str = ""
    launch_path: str = "/sso"
    exchange_path: str = "/ai/system/internal/sso/exchange"
    user_snapshot_path_template: str = "/ai/system/internal/identity/snapshot/users/{userId}"
    delta_path: str = "/ai/system/internal/identity/snapshot/delta"
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS


def load_sso_config() -> SsoConfig:
    """
    从外部系统配置和环境变量加载 SSO 配置

    ============================================================
    配置加载流程
    ============================================================

    1. 首先加载通用配置：
       - console_base_url: 前端控制台地址（仅从环境变量）
       - session_ttl_seconds: 会话有效期（仅从环境变量）

    2. 然后尝试加载外部系统配置：
       - 调用 _load_active_external_sso_config() 获取数据库中的活动配置
       - 如果存在外部配置，优先使用外部配置构建 SsoConfig

    3. 如果没有外部配置，则回退到环境变量：
       - 从环境变量读取基础配置
       - 从运行时设置读取路径模板配置

    ============================================================
    配置优先级（从高到低）
    ============================================================

    优先级 1: 外部系统配置（数据库）
        - 存储在 external_system_configs 表中
        - 通过管理后台或 API 动态配置
        - 字段：ssoBaseUrl, ssoClientId, ssoClientSecret, ssoRedirectUri 等

    优先级 2: 环境变量
        - AI_BASE_SSO_BASE_URL: AI 基座地址
        - AI_BASE_SSO_CLIENT_ID: 客户端 ID
        - AI_BASE_SSO_CLIENT_SECRET: 客户端密钥
        - AI_BASE_SSO_REDIRECT_URI: 回调地址

    优先级 3: 运行时设置（runtime_settings 表）
        - AI_BASE_SSO_LAUNCH_BASE_URL: 登录页基础地址
        - AI_BASE_SSO_LAUNCH_PATH: 登录页路径
        - AI_BASE_SSO_EXCHANGE_PATH: 凭证交换接口路径
        - AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE: 用户快照路径模板
        - AI_BASE_SSO_DELTA_PATH: 增量同步路径

    优先级 4: 默认值
        - launch_path: "/sso"
        - exchange_path: "/ai/system/internal/sso/exchange"
        - user_snapshot_path_template: "/ai/system/internal/identity/snapshot/users/{userId}"
        - delta_path: "/ai/system/internal/identity/snapshot/delta"

    ============================================================
    使用示例
    ============================================================

    示例 1：仅使用环境变量配置
        # .env 文件
        AI_BASE_SSO_BASE_URL=https://ai.example.com
        AI_BASE_SSO_CLIENT_ID=rag-client
        AI_BASE_SSO_CLIENT_SECRET=your-secret
        AI_BASE_SSO_REDIRECT_URI=https://rag.example.com/sso/callback

        # Python 代码
        config = load_sso_config()
        # config.base_url == "https://ai.example.com"

    示例 2：外部配置覆盖环境变量
        # 数据库中存在活动的外部配置
        # ssoBaseUrl = "https://ai-prod.example.com"
        # ssoClientId = "prod-client"

        # 环境变量设置
        # AI_BASE_SSO_BASE_URL=https://ai-dev.example.com

        config = load_sso_config()
        # config.base_url == "https://ai-prod.example.com"  # 外部配置优先

    示例 3：混合配置（部分外部，部分环境变量）
        # 外部配置仅设置核心字段
        # ssoBaseUrl, ssoClientId, ssoClientSecret, ssoRedirectUri

        # 环境变量设置其他字段
        # KB_CONSOLE_BASE_URL=https://console.example.com
        # KB_SESSION_TTL_SECONDS=86400

        config = load_sso_config()
        # config.base_url 来自外部配置
        # config.console_base_url 来自环境变量

    ============================================================
    错误处理
    ============================================================

    - 外部配置加载失败时，自动回退到环境变量，不影响服务可用性
    - 加载失败会记录 DEBUG 级别日志，便于排查问题
    - 最终通过 is_sso_configured() 验证配置完整性

    返回：
        SsoConfig: 包含所有 SSO 配置的不可变数据对象
    """
    console_base_url = (os.getenv("KB_CONSOLE_BASE_URL") or "").strip().rstrip("/")
    ttl = int(os.getenv("KB_SESSION_TTL_SECONDS") or DEFAULT_SESSION_TTL_SECONDS)
    active_config = _load_active_external_sso_config()
    if active_config:
        return SsoConfig(
            base_url=str(active_config.get("ssoBaseUrl") or "").strip().rstrip("/"),
            client_id=str(active_config.get("ssoClientId") or "").strip(),
            client_secret=str(active_config.get("ssoClientSecret") or "").strip(),
            redirect_uri=str(active_config.get("ssoRedirectUri") or "").strip(),
            console_base_url=console_base_url,
            launch_base_url=str(active_config.get("ssoLaunchBaseUrl") or "").strip().rstrip("/"),
            launch_path=_normalize_sso_path(active_config.get("ssoLaunchPath"), "/sso"),
            exchange_path=_normalize_sso_path(
                active_config.get("ssoExchangePath"),
                "/ai/system/internal/sso/exchange",
            ),
            user_snapshot_path_template=_normalize_sso_path(
                active_config.get("ssoUserSnapshotPathTemplate"),
                "/ai/system/internal/identity/snapshot/users/{userId}",
            ),
            delta_path=_normalize_sso_path(
                active_config.get("ssoDeltaPath"),
                "/ai/system/internal/identity/snapshot/delta",
            ),
            session_ttl_seconds=ttl,
        )

    base_url = (os.getenv("AI_BASE_SSO_BASE_URL") or "").strip().rstrip("/")
    client_id = (os.getenv("AI_BASE_SSO_CLIENT_ID") or "rag-client").strip()
    client_secret = (os.getenv("AI_BASE_SSO_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("AI_BASE_SSO_REDIRECT_URI") or "").strip()
    launch_base_url = str(resolve_runtime_setting("AI_BASE_SSO_LAUNCH_BASE_URL")[0] or "").strip().rstrip("/")
    launch_path = _normalize_sso_path(resolve_runtime_setting("AI_BASE_SSO_LAUNCH_PATH")[0], "/sso")
    exchange_path = _normalize_sso_path(
        resolve_runtime_setting("AI_BASE_SSO_EXCHANGE_PATH")[0],
        "/ai/system/internal/sso/exchange",
    )
    user_snapshot_path_template = _normalize_sso_path(
        resolve_runtime_setting("AI_BASE_SSO_USER_SNAPSHOT_PATH_TEMPLATE")[0],
        "/ai/system/internal/identity/snapshot/users/{userId}",
    )
    delta_path = _normalize_sso_path(
        resolve_runtime_setting("AI_BASE_SSO_DELTA_PATH")[0],
        "/ai/system/internal/identity/snapshot/delta",
    )
    return SsoConfig(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        console_base_url=console_base_url,
        launch_base_url=launch_base_url,
        launch_path=launch_path,
        exchange_path=exchange_path,
        user_snapshot_path_template=user_snapshot_path_template,
        delta_path=delta_path,
        session_ttl_seconds=ttl,
    )


def _load_active_external_sso_config() -> dict[str, Any] | None:
    """
    加载活动的外部系统 SSO 配置

    ============================================================
    功能说明
    ============================================================

    从数据库中查询当前活动的外部系统配置。这是配置加载的第一优先级，
    允许运维人员在不重启服务的情况下动态修改 SSO 配置。

    ============================================================
    加载流程
    ============================================================

    1. 调用 get_active_external_system_config() 查询数据库
    2. 查询条件：system_type = 'ai_base_sso' AND is_active = True
    3. 返回配置字典，包含以下字段（如果存在）：
       - ssoBaseUrl: AI 基座地址
       - ssoClientId: 客户端 ID
       - ssoClientSecret: 客户端密钥
       - ssoRedirectUri: 回调地址
       - ssoLaunchBaseUrl: 登录页基础地址
       - ssoLaunchPath: 登录页路径
       - ssoExchangePath: 凭证交换接口路径
       - ssoUserSnapshotPathTemplate: 用户快照路径模板
       - ssoDeltaPath: 增量同步路径

    ============================================================
    错误处理
    ============================================================

    - 数据库连接失败时返回 None，回退到环境变量
    - 无活动配置时返回 None
    - 记录 DEBUG 级别日志，便于排查

    ============================================================
    使用示例
    ============================================================

    示例 1：正常加载
        config = _load_active_external_sso_config()
        if config:
            base_url = config.get("ssoBaseUrl")  # "https://ai.example.com"
            client_id = config.get("ssoClientId")  # "rag-client"

    示例 2：配置不存在
        config = _load_active_external_sso_config()
        # config is None，load_sso_config() 会使用环境变量

    示例 3：数据库异常
        # 数据库连接失败
        config = _load_active_external_sso_config()
        # config is None，日志记录异常信息

    返回：
        dict[str, Any] | None: 外部配置字典，不存在或加载失败时返回 None
    """
    try:
        return get_active_external_system_config()
    except Exception as exc:
        logger.debug("Failed to load active external SSO config; falling back to runtime settings: %s", exc)
        return None


def _normalize_sso_path(value: Any, default: str) -> str:
    """
    规范化 SSO API 路径

    ============================================================
    功能说明
    ============================================================

    确保路径字符串以 "/" 开头，符合 URL path 规范。
    用于处理外部配置或环境变量中的路径配置。

    ============================================================
    处理规则
    ============================================================

    1. 空值处理：如果输入为空或 None，使用默认值
    2. 格式规范化：确保路径以 "/" 开头
    3. 空白清理：去除首尾空白字符

    ============================================================
    使用示例
    ============================================================

    示例 1：正常路径
        path = _normalize_sso_path("/sso", "/login")
        # 返回: "/sso"

    示例 2：缺少前导斜杠
        path = _normalize_sso_path("sso", "/login")
        # 返回: "/sso"

    示例 3：空值使用默认值
        path = _normalize_sso_path(None, "/login")
        # 返回: "/login"

        path = _normalize_sso_path("", "/login")
        # 返回: "/login"

    示例 4：带空白字符
        path = _normalize_sso_path("  /sso  ", "/login")
        # 返回: "/sso"

    示例 5：在 load_sso_config() 中的应用
        # 外部配置可能返回各种格式的路径
        launch_path = _normalize_sso_path(
            active_config.get("ssoLaunchPath"),  # 可能是 "sso", "/sso", None 等
            "/sso"  # 默认值
        )

    参数：
        value: 原始路径值，可以是任意类型（会转为字符串）
        default: 默认路径，当 value 为空时使用

    返回：
        str: 规范化后的路径，保证以 "/" 开头
    """
    text = str(value or "").strip() or default
    return text if text.startswith("/") else f"/{text}"


def is_sso_configured(config: SsoConfig | None = None) -> bool:
    """
    检查 SSO 是否已正确配置

    只有当以下配置都不为空时才返回 True：
    - base_url: AI 基座地址
    - client_id: 客户端 ID
    - client_secret: 客户端密钥
    - redirect_uri: 回调地址

    参数：
        config: 可选的配置对象，不传则自动加载

    返回：
        bool: SSO 是否已配置完成
    """
    config = config or load_sso_config()
    return bool(config.base_url and config.client_id and config.client_secret and config.redirect_uri)


def make_state_payload(next_path: str, config: SsoConfig | None = None) -> tuple[str, dict[str, Any]]:
    """
    生成 SSO 登录状态（state）

    state 是一个安全令牌，用于防止 CSRF 攻击：
    1. 生成一个随机的 state 字符串
    2. 将 state、跳转路径、创建时间打包成 payload
    3. 将 payload 编码为 base64 存入 cookie

    参数：
        next_path: 登录成功后要跳转的路径
        config: 可选的配置对象

    返回：
        tuple[str, dict]: (state 字符串, 包含 cookie 值的字典)

    示例：
        state, cookie_dict = make_state_payload("/knowledge-bases")
        # state = "abc123..."
        # cookie_dict = {"state": "abc123...", "cookie": "eyJ..."}
    """
    state = secrets.token_urlsafe(24)
    payload = {
        "state": state,
        "next": next_path if _is_safe_local_path(next_path) else "/knowledge-bases",
        "iat": datetime.now(timezone.utc).timestamp(),
    }
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return state, payload | {"cookie": encoded_payload}


def validate_state(cookie_value: str | None, state: str | None) -> str:
    """
    校验 SSO 登录状态（state）

    校验流程：
    1. 检查 state 参数是否存在
    2. 检查 cookie 是否存在
    3. 解码 cookie 中的 payload
    4. 检查是否过期（10 分钟有效期）
    5. 比较 state 是否匹配

    参数：
        cookie_value: cookie 中存储的 state payload
        state: URL 参数中的 state

    返回：
        str: 登录成功后要跳转的路径

    异常：
        AiBaseSsoError: state 无效、过期或不匹配时抛出
    """
    if not state:
        raise AiBaseSsoError("STATE_REQUIRED", "SSO state is required", 400)
    if not cookie_value:
        raise AiBaseSsoError("STATE_REQUIRED", "SSO state cookie is required", 400)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cookie_value.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise AiBaseSsoError("STATE_INVALID", "SSO state cookie is invalid", 400) from exc
    issued_at = float(payload.get("iat") or 0)
    if datetime.now(timezone.utc).timestamp() - issued_at > 10 * 60:
        raise AiBaseSsoError("STATE_EXPIRED", "SSO state is expired", 400)
    if not hmac.compare_digest(str(payload.get("state") or ""), str(state)):
        raise AiBaseSsoError("STATE_MISMATCH", "SSO state did not match", 400)
    next_path = str(payload.get("next") or "/knowledge-bases")
    return next_path if _is_safe_local_path(next_path) else "/knowledge-bases"


def build_launch_url(state: str, config: SsoConfig | None = None) -> str:
    """
    构建 SSO 登录跳转 URL

    生成一个完整的登录页 URL，包含：
    - client_id: 客户端标识
    - redirect_uri: 回调地址
    - state: 安全令牌

    参数：
        state: 安全令牌
        config: 可选的配置对象

    返回：
        str: 完整的登录页 URL

    示例：
        url = build_launch_url("abc123")
        # https://ai.example.com/sso?client_id=rag-client&redirect_uri=...&state=abc123
    """
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    launch_base_url = (config.launch_base_url or config.base_url).rstrip("/")
    query = urlencode({"client_id": config.client_id, "redirect_uri": config.redirect_uri, "state": state})
    return f"{launch_base_url}{config.launch_path}?{query}"


def build_console_redirect_url(next_path: str, config: SsoConfig | None = None) -> str:
    """
    构建登录成功后的控制台跳转 URL

    参数：
        next_path: 目标路径
        config: 可选的配置对象

    返回：
        str: 完整的控制台 URL

    说明：
        如果配置了 console_base_url，则返回完整 URL；
        否则返回相对路径。
    """
    config = config or load_sso_config()
    path = next_path if _is_safe_local_path(next_path) else "/knowledge-bases"
    if config.console_base_url:
        return f"{config.console_base_url}{path}"
    return path


async def exchange_ai_base_credential(
    *,
    code: str | None = None,
    jwt: str | None = None,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    """
    使用授权码或 JWT 交换用户身份信息

    这是 SSO 流程的核心步骤：
    1. 检查是否提供了 code 或 jwt
    2. 检查凭证是否已被使用（防止重放攻击）
    3. 调用 AI 基座的 exchange 接口
    4. 返回用户身份摘要

    参数：
        code: OAuth 授权码（推荐）
        jwt: JWT 令牌（备选）
        config: 可选的配置对象

    返回：
        dict: 用户身份摘要，包含：
            - tenant_id: 租户 ID
            - user_id: 用户 ID
            - roles: 角色列表
            - permissions: 权限列表
            - _auth_source: 认证来源
            - _credential_fingerprint: 凭证指纹

    异常：
        AiBaseSsoError: 交换失败时抛出
    """
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    if not code and not jwt:
        raise AiBaseSsoError("CREDENTIAL_REQUIRED", "SSO code or JWT is required", 400)

    grant_type = "authorization_code" if code else "jwt"
    credential = code or jwt or ""
    fingerprint = fingerprint_credential(credential)
    if is_sso_credential_used(fingerprint):
        raise AiBaseSsoError("CREDENTIAL_REPLAYED", "SSO credential was already used", 409)

    request_payload: dict[str, Any] = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": grant_type,
        "redirect_uri": config.redirect_uri,
    }
    if code:
        request_payload["code"] = code
    if jwt:
        request_payload["jwt"] = jwt

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{config.base_url}{config.exchange_path}", json=request_payload)
    except httpx.HTTPError as exc:
        raise AiBaseSsoError("EXCHANGE_UNAVAILABLE", "AI base SSO exchange is unavailable", 503) from exc

    if response.status_code >= 400:
        raise AiBaseSsoError("EXCHANGE_REJECTED", _safe_exchange_error(response), response.status_code)

    summary = _unwrap_ai_base_result(response, invalid_code="EXCHANGE_INVALID_RESPONSE", invalid_message="AI base SSO returned invalid identity summary")

    return summary | {"_auth_source": f"ai_base_sso_{grant_type}", "_credential_fingerprint": fingerprint}


def create_session_from_identity_summary(summary: dict[str, Any], config: SsoConfig | None = None) -> dict[str, Any]:
    """
    从用户身份摘要创建本地会话

    流程：
    1. 将身份摘要存入数据库（identity_snapshot 表）
    2. 创建会话记录（auth_sessions 表）
    3. 标记凭证已使用（防止再次使用同一 code）

    参数：
        summary: 用户身份摘要（由 exchange_ai_base_credential 返回）
        config: 可选的配置对象

    返回：
        dict: 会话信息，包含：
            - session_token: 会话令牌
            - expires_at: 过期时间
            - identity: 用户身份信息
    """
    config = config or load_sso_config()
    auth_source = str(summary.get("_auth_source") or "ai_base_sso_code")
    fingerprint = summary.get("_credential_fingerprint")
    identity = upsert_identity_snapshot_from_summary(summary)
    session = create_auth_session(
        identity,
        auth_source=auth_source,
        credential_fingerprint=str(fingerprint or ""),
        identity_snapshot_version=str(summary.get("snapshot_version") or ""),
        ttl_seconds=config.session_ttl_seconds,
    )
    if fingerprint:
        mark_sso_credential_used(
            str(fingerprint),
            credential_type=auth_source,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
    return session


def current_identity_payload(identity: IdentityContext) -> dict[str, Any]:
    """
    将身份上下文转换为 API 响应格式

    参数：
        identity: 身份上下文对象

    返回：
        dict: 可用于 API 响应的身份信息
    """
    return identity_to_payload(identity)


async def refresh_current_user_snapshot(
    *,
    tenant_id: str,
    user_id: str,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    """
    刷新当前用户的身份快照

    当用户信息在 AI 基座有更新时，可以调用此接口同步：
    1. 调用 AI 基座的用户快照接口
    2. 更新本地数据库中的身份快照
    3. 返回最新的身份信息

    参数：
        tenant_id: 租户 ID
        user_id: 用户 ID
        config: 可选的配置对象

    返回：
        dict: 包含最新身份信息：
            - identity: 身份信息
            - snapshotVersion: 快照版本
            - generatedAt: 生成时间
    """
    config = config or load_sso_config()
    url = build_user_snapshot_url(user_id, tenant_id, config=config)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=_server_auth_headers(config))
    except httpx.HTTPError as exc:
        raise AiBaseSsoError("USER_SNAPSHOT_UNAVAILABLE", "AI base user snapshot is unavailable", 503) from exc

    if response.status_code >= 400:
        raise AiBaseSsoError("USER_SNAPSHOT_REJECTED", _safe_exchange_error(response), response.status_code)

    summary = _unwrap_ai_base_result(
        response,
        invalid_code="USER_SNAPSHOT_INVALID_RESPONSE",
        invalid_message="AI base returned invalid user snapshot",
    )
    identity = upsert_identity_snapshot_from_summary(summary)
    return {
        "identity": identity_to_payload(identity),
        "snapshotVersion": str(summary.get("snapshot_version") or ""),
        "generatedAt": summary.get("generated_at") or summary.get("issued_at"),
    }


async def sync_identity_delta_from_ai_base(
    *,
    last_sync_at: str | None = None,
    use_latest_watermark: bool = True,
    config: SsoConfig | None = None,
) -> dict[str, Any]:
    """
    从 AI 基座同步身份增量数据

    定时同步机制，用于保持本地身份数据与 AI 基座一致：
    1. 获取上次同步的水位线（watermark）
    2. 调用 AI 基座的增量同步接口
    3. 更新本地的租户、用户、角色、权限数据
    4. 记录同步日志

    参数：
        last_sync_at: 上次同步时间，不传则使用数据库记录的水位线
        use_latest_watermark: 是否使用数据库中的最新水位线
        config: 可选的配置对象

    返回：
        dict: 同步结果：
            - mode: 同步模式（http_delta）
            - lastSyncAt: 本次同步的起始时间
            - maxUpdatedAt: 本次同步的最新时间
            - snapshotVersion: 快照版本
            - counts: 各类数据的更新数量
            - hasMore: 是否还有更多数据
    """
    config = config or load_sso_config()
    requested_last_sync_at = last_sync_at
    if requested_last_sync_at is None and use_latest_watermark:
        requested_last_sync_at = get_latest_identity_sync_watermark()
    if not format_identity_sync_timestamp(requested_last_sync_at):
        requested_last_sync_at = IDENTITY_DELTA_INITIAL_WATERMARK
    requested_last_sync_at = format_identity_sync_timestamp(requested_last_sync_at)
    url = build_delta_url(requested_last_sync_at, config=config)

    try:
        async with httpx.AsyncClient(timeout=IDENTITY_DELTA_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_server_auth_headers(config))
    except httpx.HTTPError as exc:
        record_identity_sync_run(
            sync_mode="http_delta",
            source_host=config.base_url,
            requested_limit=0,
            counts={},
            status="failed",
            last_sync_at=requested_last_sync_at,
            error_message="AI base identity delta is unavailable",
        )
        raise AiBaseSsoError("IDENTITY_DELTA_UNAVAILABLE", "AI base identity delta is unavailable", 503) from exc

    if response.status_code >= 400:
        error_message = _safe_exchange_error(response)
        record_identity_sync_run(
            sync_mode="http_delta",
            source_host=config.base_url,
            requested_limit=0,
            counts={},
            status="failed",
            last_sync_at=requested_last_sync_at,
            error_message=error_message,
        )
        raise AiBaseSsoError("IDENTITY_DELTA_REJECTED", error_message, response.status_code)

    delta = _unwrap_ai_base_result(
        response,
        invalid_code="IDENTITY_DELTA_INVALID_RESPONSE",
        invalid_message="AI base returned invalid identity delta",
    )
    delta_shape = _identity_delta_shape(delta)
    if delta_shape["warnings"]:
        logger.warning(
            "AI base identity delta shape warning: last_sync_at=%s url=%s shape=%s warnings=%s",
            requested_last_sync_at or "",
            f"{config.base_url}{config.delta_path}",
            delta_shape,
            delta_shape["warnings"],
        )
    else:
        logger.info(
            "AI base identity delta shape: last_sync_at=%s url=%s shape=%s",
            requested_last_sync_at or "",
            f"{config.base_url}{config.delta_path}",
            delta_shape,
        )
    counts = upsert_identity_delta_snapshot(delta)
    max_updated_at = format_identity_sync_timestamp(delta.get("max_updated_at") or "")
    snapshot_version = str(delta.get("snapshot_version") or "")
    record_identity_sync_run(
        sync_mode="http_delta",
        source_host=config.base_url,
        requested_limit=0,
        counts=counts,
        status="success",
        source_schema=_identity_delta_source_schema(delta_shape),
        last_sync_at=requested_last_sync_at,
        max_updated_at=max_updated_at or None,
        snapshot_version=snapshot_version or None,
        has_more=bool(delta.get("has_more")),
    )
    return {
        "mode": "http_delta",
        "lastSyncAt": requested_last_sync_at or "",
        "maxUpdatedAt": max_updated_at,
        "snapshotVersion": snapshot_version,
        "generatedAt": delta.get("generated_at"),
        "hasMore": bool(delta.get("has_more")),
        "counts": counts,
        "diagnostics": delta_shape,
    }


def build_user_snapshot_url(user_id: str, tenant_id: str, config: SsoConfig | None = None) -> str:
    """
    构建用户快照接口 URL

    参数：
        user_id: 用户 ID
        tenant_id: 租户 ID
        config: 可选的配置对象

    返回：
        str: 完整的用户快照接口 URL
    """
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    path = config.user_snapshot_path_template.format(userId=user_id, tenantId=tenant_id)
    query = urlencode({"tenant_id": tenant_id})
    return f"{config.base_url}{path}?{query}"


def build_delta_url(last_sync_at: str | None, config: SsoConfig | None = None) -> str:
    """
    构建增量同步接口 URL

    参数：
        last_sync_at: 上次同步时间
        config: 可选的配置对象

    返回：
        str: 完整的增量同步接口 URL
    """
    config = config or load_sso_config()
    if not is_sso_configured(config):
        raise AiBaseSsoError("SSO_NOT_CONFIGURED", "AI base SSO is not configured", 503)
    query = urlencode({"last_sync_at": format_identity_sync_timestamp(last_sync_at)})
    return f"{config.base_url}{config.delta_path}?{query}"


def format_identity_sync_timestamp(value: Any) -> str:
    """
    格式化身份同步时间戳

    将各种格式的时间转换为 AI 基座要求的格式：YYYY-MM-DD HH:mm:ss

    支持的输入格式：
    - datetime 对象
    - ISO 8601 字符串
    - 已格式化的字符串

    参数：
        value: 时间值

    返回：
        str: 格式化后的时间字符串
    """
    """Use the AI base delta contract format: YYYY-MM-DD HH:mm:ss."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip()
    if not text:
        return ""

    iso_candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    match = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return text


def _safe_exchange_error(response: httpx.Response) -> str:
    """
    从 HTTP 响应中提取安全的错误信息

    尝试解析响应体中的错误信息，如果解析失败则返回通用错误。

    参数：
        response: HTTP 响应对象

    返回：
        str: 人类可读的错误信息
    """
    try:
        payload = response.json()
    except ValueError:
        return f"AI base SSO rejected exchange with HTTP {response.status_code}"
    if isinstance(payload, dict):
        code = payload.get("code") or payload.get("error") or payload.get("error_code")
        message = payload.get("message") or payload.get("detail") or payload.get("error_description")
        if code and message:
            return f"{code}: {message}"
        if message:
            return str(message)
    return f"AI base SSO rejected exchange with HTTP {response.status_code}"


def _unwrap_ai_base_result(response: httpx.Response, *, invalid_code: str, invalid_message: str) -> dict[str, Any]:
    """
    解析 AI 基座的响应结果

    AI 基座的响应格式：
    {
        "success": true/false,
        "code": "错误代码",
        "msg": "错误信息",
        "data": { ... 实际数据 }
    }

    参数：
        response: HTTP 响应对象
        invalid_code: 响应无效时的错误代码
        invalid_message: 响应无效时的错误信息

    返回：
        dict: data 字段中的实际数据

    异常：
        AiBaseSsoError: 响应无效或 success=false 时抛出
    """
    try:
        payload = response.json()
    except ValueError as exc:
        raise AiBaseSsoError(invalid_code, "AI base returned invalid JSON", 502) from exc
    if not isinstance(payload, dict):
        raise AiBaseSsoError(invalid_code, invalid_message, 502)
    if payload.get("success") is False:
        raise AiBaseSsoError(
            str(payload.get("code") or "AI_BASE_REJECTED"),
            str(payload.get("msg") or payload.get("message") or "AI base rejected request"),
            502,
        )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise AiBaseSsoError(invalid_code, invalid_message, 502)
    return dict(data)


def _identity_delta_shape(delta: dict[str, Any]) -> dict[str, Any]:
    """
    分析身份增量数据的结构

    用于诊断和日志记录，检查返回的数据结构是否符合预期：
    - 各列表的长度
    - 列表中元素的键
    - 是否有警告（如租户列表非空但用户/角色列表为空）

    参数：
        delta: 增量数据字典

    返回：
        dict: 结构分析结果
    """
    list_aliases = {
        "tenants": ("tenants", "tenantList", "tenant_list"),
        "users": ("users", "userList", "user_list"),
        "roles": ("roles", "roleList", "role_list"),
        "user_roles": ("user_roles", "userRoles", "userRoleList", "user_role_list"),
        "deleted": ("deleted", "deletedList", "deleted_list"),
    }
    list_lengths = {key: len(_first_delta_list(delta, *aliases)) for key, aliases in list_aliases.items()}
    sample_keys: dict[str, list[str]] = {}
    for key, aliases in list_aliases.items():
        value = _first_delta_list(delta, *aliases)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sample_keys[key] = sorted(str(item_key) for item_key in value[0].keys())
    warnings: list[str] = []
    if (
        list_lengths["tenants"] > 0
        and list_lengths["users"] == 0
        and list_lengths["roles"] == 0
        and list_lengths["user_roles"] == 0
    ):
        warnings.append("tenants_non_empty_but_identity_edges_empty")
    return {
        "keys": sorted(str(key) for key in delta.keys()),
        "listLengths": list_lengths,
        "sampleKeys": sample_keys,
        "warnings": warnings,
    }


def _first_delta_list(delta: dict[str, Any], *keys: str) -> list[Any]:
    """
    从增量数据中获取第一个匹配的列表

    增量数据中的列表可能有多种字段名（别名），此函数按优先级查找。

    参数：
        delta: 增量数据字典
        keys: 可能的字段名列表

    返回：
        list: 找到的列表，如果都没找到则返回空列表
    """
    for key in keys:
        value = delta.get(key)
        if isinstance(value, list):
            return value
    return []


def _identity_delta_source_schema(shape: dict[str, Any]) -> str:
    """
    将增量数据结构分析结果转换为可存储的字符串

    用于记录在同步日志中，便于排查问题。

    参数：
        shape: 结构分析结果

    返回：
        str: 简化的结构描述字符串
    """
    list_lengths = shape.get("listLengths") if isinstance(shape.get("listLengths"), dict) else {}
    lengths = ",".join(
        f"{key}:{int(list_lengths.get(key) or 0)}"
        for key in ("tenants", "users", "roles", "user_roles", "deleted")
    )
    warnings = shape.get("warnings") if isinstance(shape.get("warnings"), list) else []
    warning_text = ",".join(str(item) for item in warnings)
    text = f"delta_shape {lengths}"
    if warning_text:
        text = f"{text};warnings:{warning_text}"
    return text[:255]


def _server_auth_headers(config: SsoConfig) -> dict[str, str]:
    """
    构建 AI 基座服务端认证请求头

    参数：
        config: SSO 配置对象

    返回：
        dict: 包含 client_id 和 client_secret 的请求头
    """
    return {
        "X-Client-Id": config.client_id,
        "X-Client-Secret": config.client_secret,
    }


def _is_safe_local_path(path: str) -> bool:
    """
    检查路径是否为安全的本地路径

    防止开放重定向攻击：
    - 必须以 / 开头
    - 不能以 // 开头（防止协议相对 URL）

    参数：
        path: 待检查的路径

    返回：
        bool: 是否为安全路径
    """
    return path.startswith("/") and not path.startswith("//")


def jwt_fingerprint(jwt: str) -> str:
    """
    计算 JWT 的指纹

    用于唯一标识一个 JWT，防止重放攻击。

    参数：
        jwt: JWT 字符串

    返回：
        str: JWT 的 SHA256 哈希值前 32 位
    """
    return hashlib.sha256(jwt.encode("utf-8")).hexdigest()[:32]
