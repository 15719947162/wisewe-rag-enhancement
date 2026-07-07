"""
身份认证与用户管理路由模块

这个模块提供了完整的身份认证流程,包括:
- 单点登录(SSO)启动和回调
- 会话管理(登录、登出、刷新)
- 身份快照同步
- 权限验证

支持与 AI Base 平台集成的单点登录功能。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.services.ai_base_sso_service import (
    STATE_COOKIE_NAME,
    AiBaseSsoError,
    build_console_redirect_url,
    build_launch_url,
    create_session_from_identity_summary,
    current_identity_payload,
    exchange_ai_base_credential,
    is_sso_configured,
    load_sso_config,
    make_state_payload,
    refresh_current_user_snapshot,
    sync_identity_delta_from_ai_base,
    validate_state,
)
from backend.services.identity_service import audit_access_denied, get_current_identity
from backend.services.identity_service import is_legacy_header_auth_enabled
from backend.services.identity_sync_scheduler import get_identity_sync_status
from core.db.identity import (
    SESSION_COOKIE_NAME,
    TENANT_ADMIN_ROLE_CODE,
    IdentityContext,
    clear_identity_snapshot_data,
    list_identity_snapshot_users,
    revoke_auth_session,
    revoke_auth_sessions_for_identity,
)
from core.db.query_logs import AuditLogRecord, append_audit_log

router = APIRouter()


class AiBaseExchangeRequest(BaseModel):
    """AI Base 凭证交换请求"""
    model_config = ConfigDict(populate_by_name=True)

    code: str | None = Field(default=None, min_length=1)  # 授权码
    jwt: str | None = Field(default=None, min_length=1)   # JWT token
    state: str | None = Field(default=None, min_length=1)  # 状态参数


class AiBaseLogoutCallbackRequest(BaseModel):
    """AI Base 登出回调请求"""
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = Field(..., alias="tenantId", min_length=1)  # 租户ID
    user_id: str | None = Field(default=None, alias="userId")    # 用户ID
    reason: str = Field(default="", max_length=200)              # 登出原因


@router.get("/api/identity/snapshot-users")
def identity_snapshot_users(
    limit: int = 10,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    获取身份快照用户列表

    查询本地存储的用户身份快照数据,用于调试和管理。

    参数:
        limit: 返回数量限制,默认 10 条

    返回值:
        dict: 用户列表
            - mode: 模式标识
            - users: 用户快照列表
            - count: 用户数量

    使用场景:
        - 调试身份同步问题
        - 查看本地缓存的用户数据

    权限要求:
        - 超级管理员权限
    """
    _assert_super_manager(identity, "view identity snapshot users")
    try:
        users = list_identity_snapshot_users(limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "mode": "temporary_sso_deferred",
        "users": users,
        "count": len(users),
    }


@router.get("/api/auth/ai-base/config")
def ai_base_sso_config() -> dict:
    """
    获取 AI Base SSO 配置信息

    返回单点登录的配置参数,包括客户端ID、回调地址等。
    前端需要这些信息来构建登录跳转链接。

    返回值:
        dict: SSO 配置信息
            - configured: 是否已配置
            - mode: 认证模式
            - baseUrl: AI Base 服务地址
            - clientId: 客户端ID
            - redirectUri: 回调地址
            - 各种路径配置

    使用场景:
        - 前端初始化登录组件
        - 判断是否启用 SSO
        - 获取登录跳转参数
    """
    config = load_sso_config()
    return {
        "configured": is_sso_configured(config),
        "mode": "ai_base_sso",
        "legacyHeaderFallback": is_legacy_header_auth_enabled(),
        "baseUrl": config.base_url,
        "launchBaseUrl": config.launch_base_url or config.base_url,
        "clientId": config.client_id,
        "redirectUri": config.redirect_uri,
        "launchPath": config.launch_path,
        "ragLaunchPath": "/api/auth/ai-base/launch",
        "ragLaunchUrl": _rag_public_url("/api/auth/ai-base/launch", config.redirect_uri),
        "ragCallbackPath": "/api/auth/ai-base/callback",
        "ragCallbackUrl": config.redirect_uri,
        "aiBaseBrowserSsoBaseUrl": config.launch_base_url or config.base_url,
        "aiBaseBrowserSsoPath": config.launch_path,
        "aiBaseBrowserSsoUrl": f"{(config.launch_base_url or config.base_url).rstrip('/')}{config.launch_path}",
        "stateOwner": "rag_launch_cookie",
        "exchangePath": config.exchange_path,
        "userSnapshotPathTemplate": config.user_snapshot_path_template,
        "deltaPath": config.delta_path,
    }


@router.get("/api/auth/ai-base/launch")
def ai_base_sso_launch(next: str = Query(default="/knowledge-bases")):
    """
    启动 AI Base SSO 登录流程

    用户点击登录后,会跳转到这个接口。
    系统生成状态参数,然后重定向到 AI Base 的登录页面。

    参数:
        next: 登录成功后的跳转目标路径,默认 "/knowledge-bases"

    返回值:
        RedirectResponse: 重定向到 AI Base 登录页

    使用场景:
        - 用户点击登录按钮
        - 强制重新登录
        - 切换账号
    """
    try:
        config = load_sso_config()
        state, payload = make_state_payload(next, config=config)
        url = build_launch_url(state, config=config)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        STATE_COOKIE_NAME,
        str(payload["cookie"]),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/api/auth/ai-base/callback")
async def ai_base_sso_callback(
    code: str | None = None,
    state: str | None = None,
    sso_state: str | None = Cookie(default=None, alias=STATE_COOKIE_NAME),
):
    """
    AI Base SSO 登录回调

    用户在 AI Base 登录成功后,会跳转到这个回调接口。
    系统验证状态参数,用授权码换取用户信息,创建本地会话。

    参数:
        code: AI Base 返回的授权码
        state: 状态参数,用于防止 CSRF 攻击
        sso_state: Cookie 中的状态参数

    返回值:
        RedirectResponse: 重定向到目标页面,并设置会话 Cookie

    使用场景:
        - SSO 登录流程的第二步
        - 自动处理登录回调

    错误情况:
        - 状态验证失败
        - 授权码无效
        - 用户信息获取失败
    """
    try:
        next_path = validate_state(sso_state, state)
        summary = await exchange_ai_base_credential(code=code)
        session = create_session_from_identity_summary(summary)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response = RedirectResponse(url=build_console_redirect_url(next_path), status_code=302)
    _attach_session_cookie(response, session["sessionToken"])
    response.delete_cookie(STATE_COOKIE_NAME, path="/")
    return response


@router.post("/api/auth/ai-base/exchange")
async def ai_base_sso_exchange(payload: AiBaseExchangeRequest, response: Response) -> dict:
    """
    AI Base 凭证交换接口（非浏览器方式）

    对于非浏览器的客户端（如移动端、桌面应用、CLI 工具），可以直接使用授权码或 JWT
    换取会话令牌，无需经过浏览器重定向流程。此接口不会返回重定向，而是直接返回会话信息。

    与浏览器 SSO 流程的区别：
    - 浏览器 SSO：/launch → 跳转登录 → /callback → 设置 Cookie → 重定向
    - 非浏览器：直接调用本接口 → 返回会话信息（同时设置 Cookie）

    参数:
        payload: 交换请求体
            - code: AI Base 授权码（可选，与 jwt 二选一）
            - jwt: AI Base JWT 令牌（可选，与 code 二选一）
            - state: 状态参数（可选，用于防止重放攻击）

    返回值:
        dict: 会话信息
            - identity: 用户身份信息
                - tenantId: 租户 ID
                - userId: 用户 ID
                - userName: 用户名
                - roleCodes: 角色代码列表
                - isTenantAdmin: 是否租户管理员
            - expiresAt: 会话过期时间（ISO 8601 格式）
            - mode: 会话模式（固定为 "ai_base_sso_session"）

    使用场景:
        - 移动端应用登录
        - 桌面客户端认证
        - CLI 工具认证
        - 服务端到服务端认证
        - API 客户端获取会话

    请求示例（使用授权码）:
        ```bash
        POST /api/auth/ai-base/exchange
        Content-Type: application/json

        {
          "code": "auth_code_from_ai_base",
          "state": "optional_state_value"
        }
        ```

    请求示例（使用 JWT）:
        ```bash
        POST /api/auth/ai-base/exchange
        Content-Type: application/json

        {
          "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
        }
        ```

    响应示例:
        ```json
        {
          "identity": {
            "tenantId": "tenant_001",
            "userId": "user_123",
            "userName": "张三",
            "roleCodes": ["TENANT_ADMIN", "USER"],
            "isTenantAdmin": true
          },
          "expiresAt": "2026-07-07T10:00:00Z",
          "mode": "ai_base_sso_session"
        }
        ```

    错误情况:
        - 400: 请求参数无效（code 和 jwt 都为空）
        - 401: 凭证无效或已过期
        - 403: 凭证验证失败或用户无权限
        - 503: AI Base 服务不可用

    注意事项:
        - 成功调用后会在响应中设置 httponly 会话 Cookie
        - 会话有效期由 SSO 配置的 session_ttl_seconds 决定
        - 建议使用 HTTPS 以保护传输安全
    """
    try:
    AI Base 凭证交换接口(非浏览器方式)

    对于非浏览器的客户端(如移动端),可以直接用授权码或 JWT 换取会话。
    不会跳转页面,直接返回会话信息。

    参数:
        payload: 交换请求,包含授权码或 JWT

    返回值:
        dict: 会话信息
            - identity: 用户身份信息
            - expiresAt: 会话过期时间
            - mode: 会话模式

    使用场景:
        - 移动端登录
        - API 客户端登录
        - 服务端到服务端的认证

    错误情况:
        - 凭证无效
        - 凭证过期
    """
    try:
        summary = await exchange_ai_base_credential(code=payload.code, jwt=payload.jwt)
        session = create_session_from_identity_summary(summary)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _attach_session_cookie(response, session["sessionToken"])
    return {
        "identity": session["identity"],
        "expiresAt": session["expiresAt"],
        "mode": "ai_base_sso_session",
    }


@router.get("/api/auth/session")
def auth_session(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取当前会话信息

    查询当前登录用户的身份信息和会话状态。
    前端可以用这个接口判断用户是否登录。

    返回值:
        dict: 会话信息
            - identity: 用户身份信息(租户ID、用户ID、角色等)
            - mode: 认证模式(会话来源)

    使用场景:
        - 前端检查登录状态
        - 获取用户权限信息
        - 显示用户信息

    错误情况:
        - 401: 未登录
    """
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "identity": current_identity_payload(identity),
        "mode": identity.source,
    }


@router.post("/api/auth/ai-base/refresh-current-user")
async def refresh_current_user(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    刷新当前用户的身份快照

    从 AI Base 重新获取当前用户的最新身份信息并更新本地快照。
    用于同步最新的权限和角色变更。

    返回值:
        dict: 更新后的用户信息

    使用场景:
        - 权限变更后刷新
        - 角色调整后同步
        - 强制更新用户信息

    错误情况:
        - 401: 未登录
        - 获取用户信息失败
    """
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await refresh_current_user_snapshot(
            tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id),
        )
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/identity/sync-delta")
async def identity_sync_delta(
    last_sync_at: str | None = Query(default=None),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    同步身份增量更新

    从 AI Base 同步自上次同步后的身份变更(新增用户、权限变更等)。
    用于保持本地身份数据与 AI Base 一致。

    参数:
        last_sync_at: 上次同步时间,可选

    返回值:
        dict: 同步结果
            - 新增用户数量
            - 更新用户数量
            - 删除用户数量

    使用场景:
        - 定期同步身份数据
        - 手动触发同步
        - 批量用户管理

    权限要求:
        - SSO 认证的超级管理员
    """
    if not identity.enforce_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _can_run_identity_delta_sync(identity):
        raise HTTPException(
            status_code=403,
            detail="Only SSO-authenticated super administrators can sync identity delta",
        )
    try:
        return await sync_identity_delta_from_ai_base(last_sync_at=last_sync_at)
    except AiBaseSsoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/identity/snapshot-data")
def remove_identity_snapshot_data(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    清除本地身份快照数据

    删除本地存储的所有用户身份快照数据,通常用于重置或清理。

    返回值:
        dict: 删除结果
            - deleted: 删除的记录统计

    使用场景:
        - 清理本地缓存
        - 重置身份数据
        - 调试问题

    权限要求:
        - 超级管理员权限
    """
    _assert_super_manager(identity, "remove identity snapshot data")
    try:
        deleted = clear_identity_snapshot_data()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    append_audit_log(
        AuditLogRecord(
            action="identity_snapshot.clear",
            resource_type="identity_snapshot",
            resource_id=str(identity.tenant_id or ""),
            identity=identity,
            outcome="success",
            risk_level="high",
            summary="Cleared local AI Base identity snapshot data",
            metadata=deleted,
        )
    )
    return {"deleted": deleted}


@router.get("/api/identity/sync-status")
def identity_sync_status(identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取身份同步状态

    查询自动同步调度器的运行状态,包括上次同步时间、下次同步时间等。

    返回值:
        dict: 同步状态信息
            - lastSyncAt: 上次同步时间
            - nextSyncAt: 下次同步时间
            - status: 同步器状态

    使用场景:
        - 监控同步任务
        - 查看同步进度

    权限要求:
        - 超级管理员权限
    """
    _assert_super_manager(identity, "view identity sync status")
    return get_identity_sync_status()


@router.post("/api/auth/logout")
def auth_logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict:
    """
    用户登出

    注销当前会话,清除登录状态。用户需要重新登录才能继续访问。

    返回值:
        dict: 登出结果
            - ok: 操作成功
            - revoked: 是否撤销了会话

    使用场景:
        - 用户主动登出
        - 切换账号
        - 安全退出
    """
    revoked = revoke_auth_session(session_token or "")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True, "revoked": revoked}


@router.post("/api/auth/ai-base/logout-callback")
def ai_base_logout_callback(
    payload: AiBaseLogoutCallbackRequest,
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
    x_client_secret: str | None = Header(default=None, alias="X-Client-Secret"),
) -> dict:
    """
    AI Base 登出回调

    当用户在 AI Base 登出时,会调用这个接口通知 RAG 系统也登出该用户。
    撤销该用户在本系统的所有会话。

    参数:
        payload: 登出通知,包含租户ID、用户ID、原因
        x_client_id: AI Base 客户端ID(用于验证调用方)
        x_client_secret: AI Base 客户端密钥

    返回值:
        dict: 处理结果
            - ok: 操作成功
            - tenantId: 租户ID
            - userId: 用户ID
            - revoked: 撤销的会话数量

    使用场景:
        - AI Base 统一登出
        - 多系统同步登出

    权限验证:
        - 需要验证 AI Base 客户端凭证
    """
    _assert_ai_base_server_credentials(x_client_id, x_client_secret)
    revoked = revoke_auth_sessions_for_identity(payload.tenant_id, payload.user_id)
    append_audit_log(
        AuditLogRecord(
            action="sso.logout_callback",
            resource_type="auth_session",
            resource_id=payload.user_id or payload.tenant_id,
            outcome="success",
            risk_level="medium",
            summary="AI Base logout callback revoked KB sessions",
            metadata={
                "tenantId": payload.tenant_id,
                "userId": payload.user_id or "",
                "reason": payload.reason,
                "revoked": revoked,
            },
        )
    )
    return {
        "ok": True,
        "tenantId": payload.tenant_id,
        "userId": payload.user_id or "",
        "revoked": revoked,
    }


def _attach_session_cookie(response: Response, session_token: str) -> None:
    config = load_sso_config()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=config.session_ttl_seconds,
        path="/",
    )


def _cookie_secure() -> bool:
    return str(load_sso_config().redirect_uri).lower().startswith("https://")


def _can_run_identity_delta_sync(identity: IdentityContext) -> bool:
    return (
        identity.enforce_access
        and str(identity.source or "").startswith("ai_base_sso_")
        and identity.is_tenant_admin
        and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}
    )


def _is_super_manager(identity: IdentityContext) -> bool:
    return identity.enforce_access and TENANT_ADMIN_ROLE_CODE in {str(role) for role in identity.role_codes}


def _assert_super_manager(identity: IdentityContext, action: str) -> None:
    if not identity.enforce_access:
        audit_access_denied(
            identity,
            action=action,
            resource_type="identity_admin",
            reason_code="NOT_AUTHENTICATED",
            risk_level="medium",
        )
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not _is_super_manager(identity):
        audit_access_denied(
            identity,
            action=action,
            resource_type="identity_admin",
            reason_code="SUPER_MANAGER_REQUIRED",
            risk_level="medium",
            metadata={"requiredRole": TENANT_ADMIN_ROLE_CODE},
        )
        raise HTTPException(status_code=403, detail=f"Only super administrators can {action}")


def _assert_ai_base_server_credentials(client_id: str | None, client_secret: str | None) -> None:
    if not client_id or not client_secret:
        audit_access_denied(
            None,
            action="sso.logout_callback",
            resource_type="ai_base_server",
            reason_code="AI_BASE_CLIENT_CREDENTIALS_MISSING",
            risk_level="high",
            metadata={"clientIdPresent": bool(client_id), "clientSecretPresent": bool(client_secret)},
        )
        raise HTTPException(status_code=401, detail="AI Base client credentials are required")
    config = load_sso_config()
    if client_id != config.client_id or client_secret != config.client_secret:
        audit_access_denied(
            None,
            action="sso.logout_callback",
            resource_type="ai_base_server",
            reason_code="AI_BASE_CLIENT_CREDENTIALS_INVALID",
            risk_level="high",
            metadata={"clientIdPresent": bool(client_id), "clientSecretPresent": bool(client_secret)},
        )
        raise HTTPException(status_code=403, detail="AI Base client credentials are invalid")


def _rag_public_url(path: str, redirect_uri: str) -> str:
    parsed = urlsplit(redirect_uri or "")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return path