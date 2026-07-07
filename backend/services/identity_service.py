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

from fastapi import Cookie, HTTPException

from core.db.identity import (
    SESSION_COOKIE_NAME,
    IdentityContext,
    anonymous_identity,
    latest_identity_snapshot_synced_at,
    resolve_auth_session,
)
from core.db.query_logs import AuditLogRecord, append_audit_log
from core.runtime_settings import resolve_runtime_setting


def get_current_identity(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> IdentityContext:
    """
    从 KB 会话 Cookie 解析当前请求的用户身份。

    ========================================
    为什么移除旧版请求头认证？
    ========================================

    旧版支持通过 X-KB-Tenant-Id 和 X-KB-User-Id 请求头传递身份，
    这种方式存在以下安全风险：

    1. 【信任边界模糊】请求头可被客户端任意伪造，攻击者可冒充任意用户
    2. 【缺乏签名验证】无法验证请求头来源是否为可信的 AI 基座
    3. 【绕过会话管理】无法追踪会话状态、检测过期或强制登出

    新版仅接受通过 AI 基座 SSO 流程颁发的正式 session_token：
    - 会话由 AI 基座统一管理，有完整的生命周期
    - Token 通过安全 Cookie 传递，避免客户端篡改
    - 支持会话过期检测和刷新机制

    ========================================
    新的身份解析流程
    ========================================

    1. 【提取 Cookie】从请求中读取 SESSION_COOKIE_NAME 对应的会话令牌
    2. 【解析会话】调用 resolve_auth_session() 验证令牌有效性
       - 有效：返回 IdentityContext（包含租户、用户、角色、权限）
       - 无效/过期：返回 None
    3. 【审计失败】如果会话无效，记录审计日志并抛出 401 错误
    4. 【匿名身份】如果无 Cookie，返回匿名用户身份

    Args:
        session_token: 从 Cookie 中提取的会话令牌，由 AI 基座 SSO 颁发

    Returns:
        IdentityContext: 已认证用户的身份上下文，或匿名用户身份

    Raises:
        HTTPException: 会话无效或过期时返回 401 Unauthorized
    """
    if session_token:
        identity = resolve_auth_session(session_token)
        if identity is None:
            # 会话无效，记录审计日志并拒绝访问
            # 这有助于检测潜在的会话劫持或过期令牌重放攻击
            audit_access_denied(
                anonymous_identity(),
                action="identity.resolve",
                resource_type="auth_session",
                reason_code="KB_SESSION_INVALID",
                risk_level="medium",
                metadata={"authMethod": "kb_session"},
            )
            raise HTTPException(status_code=401, detail="KB session is invalid or expired")
        return identity
    # 无 Cookie，返回匿名身份（未登录用户）
    return anonymous_identity()


def audit_access_denied(
    identity: IdentityContext | None,
    *,
    action: str,
    resource_type: str,
    reason_code: str,
    resource_id: str | None = None,
    kb_id: str | None = None,
    risk_level: str = "medium",
    metadata: dict | None = None,
) -> None:
    """
    记录访问被拒绝事件到审计日志。

    ========================================
    审计日志的核心作用
    ========================================

    1. 【安全监控】
       - 检测异常访问模式（如暴力破解、会话劫持尝试）
       - 识别潜在的账户被盗或权限滥用行为
       - 支持安全事件的事后追溯和分析

    2. 【合规要求】
       - 满足企业安全审计要求（如 ISO 27001、SOC 2）
       - 提供可追溯的访问拒绝证据链
       - 支持数据保护法规（如 GDPR）的合规证明

    3. 【运维价值】
       - 帮助发现配置错误或权限设计问题
       - 统计高频拒绝原因，指导系统改进
       - 支持故障排查（用户投诉"无法访问"时的根因分析）

    4. 【风险分级】
       - risk_level 参数允许标记事件严重程度：
         - "low": 预期内的权限不足（如普通用户访问管理功能）
         - "medium": 可能存在安全问题（如无效会话）
         - "high": 高风险事件（如身份快照过期、可疑的访问尝试）

    ========================================
    使用场景示例
    ========================================

    1. 会话验证失败 → reason_code="KB_SESSION_INVALID"
    2. 身份快照过期 → reason_code="IDENTITY_SNAPSHOT_STALE"
    3. 权限检查失败 → reason_code="PERMISSION_DENIED"
    4. 资源不存在拒绝 → reason_code="RESOURCE_NOT_FOUND"

    Args:
        identity: 被拒绝的身份上下文（可能为匿名或已认证用户）
        action: 尝试执行的操作（如 "identity.resolve", "kb.delete"）
        resource_type: 资源类型（如 "auth_session", "knowledge_base"）
        reason_code: 拒绝原因代码，用于分类统计和报警
        resource_id: 可选，具体资源 ID
        kb_id: 可选，知识库 ID
        risk_level: 风险等级，默认 "medium"
        metadata: 额外的上下文信息，用于详细分析

    Note:
        审计日志写入失败不应影响主流程，因此内部捕获所有异常静默处理
    """
    details = {
        "reasonCode": reason_code,
        "action": action,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "kbId": kb_id,
    }
    if metadata:
        details.update(metadata)
    try:
        append_audit_log(
            AuditLogRecord(
                action="access.denied",
                resource_type=resource_type,
                resource_id=resource_id,
                kb_id=kb_id,
                identity=identity,
                outcome="denied",
                risk_level=risk_level,
                summary=f"Rejected {action}: {reason_code}",
                metadata=details,
            )
        )
    except Exception:
        # 审计日志写入失败不应阻塞主流程，静默忽略
        pass


def identity_snapshot_freshness(identity: IdentityContext) -> dict:
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
    audit_access_denied(
        identity,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        kb_id=kb_id,
        reason_code=reason_code,
        risk_level="high",
        metadata={
            "identitySnapshotStale": True,
            "syncedAt": freshness["syncedAt"],
            "ageSeconds": freshness["ageSeconds"],
            "maxAgeSeconds": freshness["maxAgeSeconds"],
        },
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
    return identity.enforce_access and str(identity.source or "").startswith("ai_base_sso_")


def _identity_snapshot_max_age_seconds() -> int:
    try:
        value = int(resolve_runtime_setting("KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS")[0])
    except Exception:
        value = 600
    return max(60, value)