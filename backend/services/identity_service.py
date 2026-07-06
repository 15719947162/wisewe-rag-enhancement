"""
身份服务模块

本模块负责处理用户身份认证和授权，主要包括：
1. 从会话或请求头解析用户身份
2. 验证身份快照的新鲜度
3. 检查用户权限并记录审计日志

认证方式：
1. 会话认证（推荐）：用户登录后，session_token 存储在 cookie 中
2. 请求头认证（兼容）：通过 X-KB-Tenant-Id 和 X-KB-User-Id 头传递身份

身份快照：
- AI 基座下发的用户身份信息，包含租户、角色、权限
- 有过期时间，需要定期同步更新
- 过期后需要重新同步或刷新
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Header, HTTPException

from core.db.identity import (
    SESSION_COOKIE_NAME,
    IdentityContext,
    anonymous_identity,
    latest_identity_snapshot_synced_at,
    resolve_auth_session,
    resolve_identity_snapshot,
)
from core.db.query_logs import AuditLogRecord, append_audit_log
from core.runtime_settings import resolve_runtime_setting


def is_legacy_header_auth_enabled() -> bool:
    """
    检查是否启用旧版请求头认证

    旧版认证方式用于本地开发调试，生产环境应禁用。

    返回：
        bool: 是否启用旧版认证

    说明：
        - 通过 KB_LEGACY_HEADER_AUTH_ENABLED 配置控制
        - 默认启用（便于本地开发）
        - 生产环境建议禁用
    """
    """Keep local bootstrap compatibility unless production explicitly disables it."""
    try:
        return bool(resolve_runtime_setting("KB_LEGACY_HEADER_AUTH_ENABLED")[0])
    except Exception:
        return True


def get_current_identity(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    tenant_id: str | None = Header(default=None, alias="X-KB-Tenant-Id"),
    user_id: str | None = Header(default=None, alias="X-KB-User-Id"),
) -> IdentityContext:
    """
    获取当前请求的用户身份

    这是身份认证的核心函数，按优先级解析身份：
    1. 如果有 session_token，从会话中解析身份
    2. 如果没有会话但有请求头，从请求头解析身份
    3. 如果都没有，返回匿名身份

    参数：
        session_token: Cookie 中的会话令牌
        tenant_id: 请求头中的租户 ID
        user_id: 请求头中的用户 ID

    返回：
        IdentityContext: 用户身份上下文对象

    异常：
        HTTPException(401): 会话无效或过期
        HTTPException(403): 身份快照不允许此用户

    使用示例：
        @router.get("/me")
        def get_me(identity: IdentityContext = Depends(get_current_identity)):
            return {"user_id": identity.user_id}
    """
    """Resolve request identity from the KB session or the legacy bootstrap headers."""
    if session_token:
        identity = resolve_auth_session(session_token)
        if identity is None:
            raise HTTPException(status_code=401, detail="KB session is invalid or expired")
        return identity

    tenant = (tenant_id or "").strip()
    user = (user_id or "").strip()
    if not tenant and not user:
        return anonymous_identity()
    if not is_legacy_header_auth_enabled():
        raise HTTPException(status_code=401, detail="Legacy X-KB-* header authentication is disabled")
    if not tenant or not user:
        raise HTTPException(status_code=401, detail="Both X-KB-Tenant-Id and X-KB-User-Id are required")

    identity = resolve_identity_snapshot(tenant, user)
    if identity is None:
        raise HTTPException(status_code=403, detail="AI base identity snapshot did not allow this user")
    return identity


def identity_snapshot_freshness(identity: IdentityContext) -> dict:
    """
    检查身份快照的新鲜度

    身份快照需要定期更新，此函数检查快照是否过期。

    参数：
        identity: 身份上下文对象

    返回：
        dict: 新鲜度信息：
            - enforced: 是否强制检查新鲜度
            - fresh: 快照是否新鲜（未过期）
            - reasonCode: 状态码（空/IDENTITY_SNAPSHOT_MISSING/IDENTITY_SNAPSHOT_STALE）
            - syncedAt: 上次同步时间
            - ageSeconds: 快照年龄（秒）
            - maxAgeSeconds: 最大允许年龄（秒）

    说明：
        - 只有 AI 基座 SSO 身份才强制检查
        - 其他身份（匿名、本地开发）不检查
    """
    """Return freshness metadata for formal AI Base SSO identities."""
    max_age_seconds = _identity_snapshot_max_age_seconds()
    if not _requires_fresh_snapshot(identity):
        return {
            "enforced": False,
            "fresh": True,
            "reasonCode": "",
            "syncedAt": "",
            "ageSeconds": 0,
            "maxAgeSeconds": max_age_seconds,
        }

    synced_at = latest_identity_snapshot_synced_at(str(identity.tenant_id), str(identity.user_id))
    if synced_at is None:
        return {
            "enforced": True,
            "fresh": False,
            "reasonCode": "IDENTITY_SNAPSHOT_MISSING",
            "syncedAt": "",
            "ageSeconds": None,
            "maxAgeSeconds": max_age_seconds,
        }

    age_seconds = max(0, int((datetime.now(timezone.utc) - synced_at).total_seconds()))
    return {
        "enforced": True,
        "fresh": age_seconds <= max_age_seconds,
        "reasonCode": "" if age_seconds <= max_age_seconds else "IDENTITY_SNAPSHOT_STALE",
        "syncedAt": synced_at.isoformat(),
        "ageSeconds": age_seconds,
        "maxAgeSeconds": max_age_seconds,
    }


def assert_fresh_identity_snapshot(
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    kb_id: str | None = None,
) -> None:
    """
    断言身份快照是新鲜的

    在执行敏感操作前，检查身份快照是否过期。
    如果过期，拒绝操作并记录审计日志。

    参数：
        identity: 身份上下文对象
        action: 操作名称（如 "create_document"）
        resource_type: 资源类型（如 "document"）
        resource_id: 资源 ID（可选）
        kb_id: 知识库 ID（可选）

    异常：
        HTTPException(403): 身份快照过期

    使用示例：
        assert_fresh_identity_snapshot(
            identity,
            action="delete_document",
            resource_type="document",
            resource_id="doc-123"
        )
    """
    freshness = identity_snapshot_freshness(identity)
    if freshness["fresh"]:
        return

    reason_code = str(freshness["reasonCode"] or "IDENTITY_SNAPSHOT_STALE")
    metadata = {
        "reasonCode": reason_code,
        "identitySnapshotStale": True,
        "syncedAt": freshness["syncedAt"],
        "ageSeconds": freshness["ageSeconds"],
        "maxAgeSeconds": freshness["maxAgeSeconds"],
        "action": action,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "kbId": kb_id,
    }
    append_audit_log(
        AuditLogRecord(
            action="access.denied",
            resource_type=resource_type,
            resource_id=resource_id,
            kb_id=kb_id,
            identity=identity,
            outcome="denied",
            risk_level="high",
            summary=f"Rejected {action} because AI Base identity snapshot is stale",
            metadata=metadata,
        )
    )
    raise HTTPException(
        status_code=403,
        detail={
            "code": reason_code,
            "message": "AI base identity snapshot is stale; please run identity delta sync or refresh current user",
            "details": metadata,
        },
    )


def _requires_fresh_snapshot(identity: IdentityContext) -> bool:
    """
    检查身份是否需要新鲜度检查

    只有 AI 基座 SSO 身份且启用访问控制时才需要检查。

    参数：
        identity: 身份上下文对象

    返回：
        bool: 是否需要检查新鲜度
    """
    return identity.enforce_access and str(identity.source or "").startswith("ai_base_sso_")


def _identity_snapshot_max_age_seconds() -> int:
    """
    获取身份快照最大允许年龄

    从配置中读取，默认 600 秒（10 分钟）。

    返回：
        int: 最大允许年龄（秒）
    """
    try:
        value = int(resolve_runtime_setting("KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS")[0])
    except Exception:
        value = 600
    return max(60, value)
