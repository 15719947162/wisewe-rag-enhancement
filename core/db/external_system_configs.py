"""
外部系统配置管理模块

本模块负责管理外部系统（如 SSO 单点登录）的配置信息。

核心功能：
---------
1. 创建、查询、更新、删除外部系统配置
2. 管理SSO相关参数（base_url、client_id、client_secret等）
3. 支持多租户隔离和权限控制

SSO单点登录配置：
--------------
当系统需要与企业内部SSO系统集成时，需要配置以下参数：
- sso_base_url: SSO服务器地址
- sso_client_id: 客户端ID
- sso_client_secret: 客户端密钥
- sso_redirect_uri: 重定向URI
- sso_launch_base_url: 启动基础URL
- sso_launch_path: 启动路径
- sso_exchange_path: Token交换路径
- sso_user_snapshot_path_template: 用户快照路径模板
- sso_delta_path: 增量同步路径

数据流程：
---------
外部系统配置 → 创建配置记录 → SSO登录流程使用 → 身份验证 → 用户身份上下文

安全考虑：
---------
- client_secret 字段在返回时会被脱敏（只显示后4位）
- 只有在明确需要时才返回完整的 secret（如 get_active_external_system_config）
- 配置支持 active/disabled 状态管理
- 支持软删除（deleted_at字段）

使用示例：
---------
>>> # 创建SSO配置
>>> config = create_external_system_config(
...     sso_base_url="https://sso.example.com",
...     sso_client_id="my_client_id",
...     sso_client_secret="my_secret",
...     sso_redirect_uri="https://app.example.com/callback"
... )
>>> print(config["id"])  # ext_xxx

>>> # 获取当前激活的配置
>>> active_config = get_active_external_system_config()
>>> if active_config:
...     print(f"SSO URL: {active_config['ssoBaseUrl']}")

>>> # 更新配置
>>> updated = update_external_system_config(
...     config["id"],
...     sso_base_url="https://new-sso.example.com"
... )

>>> # 删除配置（软删除）
>>> delete_external_system_config(config["id"])
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext, anonymous_identity
from core.db.init_db import ensure_db_schema

# ============================================================================
# 常量定义
# ============================================================================

# 配置状态常量
ACTIVE_STATUS = "active"      # 激活状态：配置可用
DISABLED_STATUS = "disabled"  # 禁用状态：配置暂停使用
STATUSES = {ACTIVE_STATUS, DISABLED_STATUS}

# SSO默认路径配置
DEFAULT_SSO_LAUNCH_PATH = "/sso"  # SSO启动路径
DEFAULT_SSO_EXCHANGE_PATH = "/ai/system/internal/sso/exchange"  # Token交换路径
DEFAULT_SSO_USER_SNAPSHOT_PATH_TEMPLATE = "/ai/system/internal/identity/snapshot/users/{userId}"  # 用户快照路径模板
DEFAULT_SSO_DELTA_PATH = "/ai/system/internal/identity/snapshot/delta"  # 增量同步路径


# ============================================================================
# 外部系统配置 CRUD 操作
# ============================================================================

def create_external_system_config(
    *,
    sso_base_url: str,
    sso_client_id: str,
    sso_client_secret: str,
    sso_redirect_uri: str,
    sso_launch_base_url: str = "",
    sso_launch_path: str = DEFAULT_SSO_LAUNCH_PATH,
    sso_exchange_path: str = DEFAULT_SSO_EXCHANGE_PATH,
    sso_user_snapshot_path_template: str = DEFAULT_SSO_USER_SNAPSHOT_PATH_TEMPLATE,
    sso_delta_path: str = DEFAULT_SSO_DELTA_PATH,
    status: str = ACTIVE_STATUS,
    identity: IdentityContext | None = None,
) -> dict[str, Any]:
    """
    创建外部系统配置

    创建一个新的SSO配置记录。配置创建后，系统即可使用该配置进行SSO登录。

    参数：
    -----
    sso_base_url : str
        SSO服务器的基础URL（如 https://sso.example.com）

    sso_client_id : str
        在SSO系统注册的客户端ID

    sso_client_secret : str
        客户端密钥（敏感信息，返回时会被脱敏）

    sso_redirect_uri : str
        OAuth回调地址

    sso_launch_base_url : str
        SSO启动的基础URL（可选，用于构建完整的启动URL）

    sso_launch_path : str
        SSO启动路径，默认为 /sso

    sso_exchange_path : str
        Token交换接口路径

    sso_user_snapshot_path_template : str
        用户快照接口路径模板，支持 {userId} 占位符

    sso_delta_path : str
        增量同步接口路径

    status : str
        配置状态，"active" 或 "disabled"

    identity : IdentityContext | None
        调用者身份（用于多租户隔离）

    返回：
    -----
    dict[str, Any]：新创建的配置信息（client_secret已脱敏）

    示例：
    -----
    >>> config = create_external_system_config(
    ...     sso_base_url="https://sso.company.com",
    ...     sso_client_id="rag-app",
    ...     sso_client_secret="secret123",
    ...     sso_redirect_uri="https://rag.company.com/callback"
    ... )
    >>> print(config["ssoClientSecretMasked"])  # ****t123
    """
    identity = identity or anonymous_identity()
    normalized_status = _normalize_status(status)
    config_id = _new_config_id()
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_external_system_configs(
                    id, tenant_id, created_by, sso_base_url, sso_client_id, sso_client_secret, sso_redirect_uri,
                    sso_launch_base_url, sso_launch_path, sso_exchange_path, sso_user_snapshot_path_template,
                    sso_delta_path, status
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, tenant_id, created_by, sso_base_url, sso_client_id, sso_client_secret,
                          sso_redirect_uri, sso_launch_base_url, sso_launch_path, sso_exchange_path,
                          sso_user_snapshot_path_template, sso_delta_path, status, created_at, updated_at, deleted_at
                """,
                (
                    config_id,
                    identity.tenant_id if identity.enforce_access else None,
                    identity.user_id if identity.enforce_access else None,
                    sso_base_url.strip(),
                    sso_client_id.strip(),
                    sso_client_secret.strip(),
                    sso_redirect_uri.strip(),
                    sso_launch_base_url.strip(),
                    _normalize_path(sso_launch_path, DEFAULT_SSO_LAUNCH_PATH),
                    _normalize_path(sso_exchange_path, DEFAULT_SSO_EXCHANGE_PATH),
                    _normalize_path(sso_user_snapshot_path_template, DEFAULT_SSO_USER_SNAPSHOT_PATH_TEMPLATE),
                    _normalize_path(sso_delta_path, DEFAULT_SSO_DELTA_PATH),
                    normalized_status,
                ),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description]
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return _row_to_payload(row, cols)


def list_external_system_configs(identity: IdentityContext | None = None) -> list[dict[str, Any]]:
    """
    列出所有外部系统配置

    根据调用者身份返回其有权查看的配置列表。
    - 平台管理员：可看到所有配置
    - 租户管理员：只能看到自己租户的配置
    - 匿名用户：可看到所有配置

    参数：
    -----
    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    list[dict[str, Any]]：配置列表，按创建时间倒序排列
    """
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, tenant_id, created_by, sso_base_url, sso_client_id, sso_client_secret,
                       sso_redirect_uri, sso_launch_base_url, sso_launch_path, sso_exchange_path,
                       sso_user_snapshot_path_template, sso_delta_path, status, created_at, updated_at, deleted_at
                FROM kb_external_system_configs
                {where}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [_row_to_payload(row, cols) for row in rows]


def update_external_system_config(
    config_id: str,
    *,
    sso_base_url: str | None = None,
    sso_client_id: str | None = None,
    sso_redirect_uri: str | None = None,
    sso_launch_base_url: str | None = None,
    sso_launch_path: str | None = None,
    sso_exchange_path: str | None = None,
    sso_user_snapshot_path_template: str | None = None,
    sso_delta_path: str | None = None,
    status: str | None = None,
    identity: IdentityContext | None = None,
) -> dict[str, Any] | None:
    """
    更新外部系统配置

    只更新提供的字段，未提供的字段保持不变。这是一种部分更新（PATCH）语义。

    参数：
    -----
    config_id : str
        配置ID（格式：ext_xxx）

    sso_base_url : str | None
        新的SSO服务器基础URL

    sso_client_id : str | None
        新的客户端ID

    sso_redirect_uri : str | None
        新的OAuth回调地址

    sso_launch_base_url : str | None
        新的SSO启动基础URL

    sso_launch_path : str | None
        新的SSO启动路径

    sso_exchange_path : str | None
        新的Token交换接口路径

    sso_user_snapshot_path_template : str | None
        新的用户快照接口路径模板

    sso_delta_path : str | None
        新的增量同步接口路径

    status : str | None
        新的配置状态（"active" 或 "disabled"）

    identity : IdentityContext | None
        调用者身份（用于多租户隔离和权限控制）

    返回：
    -----
    dict[str, Any] | None
        更新后的配置信息（client_secret已脱敏），如果找不到配置则返回 None

    示例：
    -----
    >>> # 更新SSO服务器地址
    >>> config = update_external_system_config(
    ...     "ext_abc123",
    ...     sso_base_url="https://new-sso.company.com"
    ... )
    >>> if config:
    ...     print(f"Updated: {config['ssoBaseUrl']}")

    >>> # 禁用配置
    >>> config = update_external_system_config(
    ...     "ext_abc123",
    ...     status="disabled"
    ... )

    >>> # 更新多个字段
    >>> config = update_external_system_config(
    ...     "ext_abc123",
    ...     sso_base_url="https://sso.company.com",
    ...     sso_client_id="new-client-id",
    ...     sso_redirect_uri="https://app.company.com/new-callback"
    ... )
    """
    identity = identity or anonymous_identity()
    assignments: list[str] = []
    values: list[Any] = []
    if sso_base_url is not None:
        assignments.append("sso_base_url = %s")
        values.append(sso_base_url.strip())
    if sso_client_id is not None:
        assignments.append("sso_client_id = %s")
        values.append(sso_client_id.strip())
    if sso_redirect_uri is not None:
        assignments.append("sso_redirect_uri = %s")
        values.append(sso_redirect_uri.strip())
    if sso_launch_base_url is not None:
        assignments.append("sso_launch_base_url = %s")
        values.append(sso_launch_base_url.strip())
    if sso_launch_path is not None:
        assignments.append("sso_launch_path = %s")
        values.append(_normalize_path(sso_launch_path, DEFAULT_SSO_LAUNCH_PATH))
    if sso_exchange_path is not None:
        assignments.append("sso_exchange_path = %s")
        values.append(_normalize_path(sso_exchange_path, DEFAULT_SSO_EXCHANGE_PATH))
    if sso_user_snapshot_path_template is not None:
        assignments.append("sso_user_snapshot_path_template = %s")
        values.append(_normalize_path(sso_user_snapshot_path_template, DEFAULT_SSO_USER_SNAPSHOT_PATH_TEMPLATE))
    if sso_delta_path is not None:
        assignments.append("sso_delta_path = %s")
        values.append(_normalize_path(sso_delta_path, DEFAULT_SSO_DELTA_PATH))
    if status is not None:
        assignments.append("status = %s")
        values.append(_normalize_status(status))
    if not assignments:
        matches = [item for item in list_external_system_configs(identity) if item["id"] == config_id]
        return matches[0] if matches else None

    assignments.append("updated_at = NOW()")
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_external_system_configs
                SET {", ".join(assignments)}
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, tenant_id, created_by, sso_base_url, sso_client_id, sso_client_secret,
                          sso_redirect_uri, sso_launch_base_url, sso_launch_path, sso_exchange_path,
                          sso_user_snapshot_path_template, sso_delta_path, status, created_at, updated_at, deleted_at
                """,
                (*values, config_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return _row_to_payload(row, cols) if row else None


def delete_external_system_config(config_id: str, identity: IdentityContext | None = None) -> bool:
    """
    删除外部系统配置（软删除）

    将配置标记为已删除，实际数据仍保留在数据库中。

    参数：
    -----
    config_id : str
        配置ID

    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    bool：是否成功删除
    """
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_external_system_configs
                SET status = 'disabled',
                    deleted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                """,
                (config_id, *params),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    return deleted


def get_active_external_system_config(identity: IdentityContext | None = None) -> dict[str, Any] | None:
    """
    获取当前激活的外部系统配置

    返回当前租户（或全局）的激活配置。这是SSO登录流程中使用的主要函数。

    重要说明：
    ---------
    此函数返回完整的 client_secret（未脱敏），因为登录流程需要使用它。
    请确保不要在日志或API响应中暴露该字段。

    参数：
    -----
    identity : IdentityContext | None
        调用者身份（用于多租户隔离）。如果未提供，使用匿名身份。
        - 平台管理员：可获取任何激活的配置
        - 租户用户：只能获取自己租户的配置
        - 匿名用户：可获取任何激活的配置

    返回：
    -----
    dict[str, Any] | None
        激活的配置信息，包含完整的 ssoClientSecret 字段。
        如果没有激活的配置则返回 None。

    返回字段说明：
    -------------
    - id: 配置ID
    - ssoBaseUrl: SSO服务器基础URL
    - ssoClientId: 客户端ID
    - ssoClientSecret: 客户端密钥（完整，未脱敏）
    - ssoRedirectUri: OAuth回调地址
    - ssoLaunchBaseUrl: SSO启动基础URL
    - ssoLaunchPath: SSO启动路径
    - ssoExchangePath: Token交换路径
    - ssoUserSnapshotPathTemplate: 用户快照路径模板
    - ssoDeltaPath: 增量同步路径
    - status: 配置状态
    - createdAt: 创建时间
    - updatedAt: 更新时间

    示例：
    -----
    >>> # 获取激活的配置用于SSO登录
    >>> config = get_active_external_system_config()
    >>> if config:
    ...     # 构建OAuth授权URL
    ...     auth_url = (
    ...         f"{config['ssoBaseUrl']}/oauth/authorize"
    ...         f"?client_id={config['ssoClientId']}"
    ...         f"&redirect_uri={config['ssoRedirectUri']}"
    ...         f"&response_type=code"
    ...     )
    ...     # 使用 secret 调用 token 交换接口
    ...     secret = config['ssoClientSecret']
    ...     print(f"Redirect to: {auth_url}")
    ... else:
    ...     print("No active SSO configuration found")

    >>> # 在特定租户上下文中获取配置
    >>> from core.db.identity import IdentityContext
    >>> identity = IdentityContext(
    ...     user_id="user123",
    ...     tenant_id="tenant456",
    ...     enforce_access=True
    ... )
    >>> config = get_active_external_system_config(identity)
    """
    identity = identity or anonymous_identity()
    if identity.enforce_access and not identity.is_platform_admin:
        where = "WHERE deleted_at IS NULL AND status = %s AND tenant_id = %s"
        params: tuple[Any, ...] = (ACTIVE_STATUS, identity.tenant_id or "")
    else:
        where = "WHERE deleted_at IS NULL AND status = %s"
        params = (ACTIVE_STATUS,)

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, tenant_id, created_by, sso_base_url, sso_client_id, sso_client_secret,
                       sso_redirect_uri, sso_launch_base_url, sso_launch_path, sso_exchange_path,
                       sso_user_snapshot_path_template, sso_delta_path, status, created_at, updated_at, deleted_at
                FROM kb_external_system_configs
                {where}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
    finally:
        conn.close()
    return _row_to_payload(row, cols, include_secret=True) if row else None


# ============================================================================
# 内部辅助函数
# ============================================================================

def _normalize_status(value: str | None) -> str:
    """标准化配置状态，只接受 active 或 disabled"""
    normalized = str(value or ACTIVE_STATUS).strip().lower()
    if normalized not in STATUSES:
        raise ValueError("status must be active or disabled")
    return normalized


def _normalize_path(value: str | None, default: str) -> str:
    """标准化路径，确保以 / 开头"""
    text = str(value or "").strip() or default
    return text if text.startswith("/") else f"/{text}"


def _scope_filter(identity: IdentityContext, *, include_where: bool = True) -> tuple[str, tuple[str, ...]]:
    """生成租户范围过滤条件"""
    if identity.enforce_access and not identity.is_platform_admin:
        clause = "tenant_id = %s"
        prefix = "WHERE" if include_where else "AND"
        return f"{prefix} deleted_at IS NULL AND {clause}" if include_where else f"AND {clause}", (identity.tenant_id or "",)
    return ("WHERE deleted_at IS NULL" if include_where else ""), ()


def _new_config_id() -> str:
    """生成新的配置ID，格式：ext_{20位十六进制}"""
    return f"ext_{secrets.token_hex(10)}"


def _row_to_payload(row: tuple[Any, ...], cols: list[str], *, include_secret: bool = False) -> dict[str, Any]:
    """
    将数据库行转换为API响应格式

    执行字段名称转换（snake_case → camelCase）并处理敏感字段脱敏。

    参数：
    -----
    row : tuple[Any, ...]
        数据库查询返回的行数据

    cols : list[str]
        数据库列名列表（snake_case格式）

    include_secret : bool
        是否包含完整的 client_secret。
        - False（默认）：只返回脱敏的 ssoClientSecretMasked 字段
        - True：额外返回完整的 ssoClientSecret 字段

    返回：
    -----
    dict[str, Any]
        转换后的字典，字段名为 camelCase 格式：
        - id: 配置ID
        - tenantId: 租户ID
        - createdBy: 创建者ID
        - ssoBaseUrl: SSO基础URL
        - ssoClientId: 客户端ID
        - ssoClientSecretMasked: 脱敏后的密钥（只显示后4位）
        - ssoClientSecret: 完整密钥（仅当 include_secret=True 时）
        - ssoRedirectUri: 重定向URI
        - ssoLaunchBaseUrl: 启动基础URL
        - ssoLaunchPath: 启动路径
        - ssoExchangePath: Token交换路径
        - ssoUserSnapshotPathTemplate: 用户快照路径模板
        - ssoDeltaPath: 增量同步路径
        - status: 状态
        - createdAt: 创建时间（ISO格式）
        - updatedAt: 更新时间（ISO格式）
        - deletedAt: 删除时间（ISO格式，软删除时非空）

    示例：
    -----
    >>> row = ("ext_abc123", "tenant1", "user1", "https://sso.example.com", ...)
    >>> cols = ["id", "tenant_id", "created_by", "sso_base_url", ...]
    >>> payload = _row_to_payload(row, cols)
    >>> print(payload["ssoClientSecretMasked"])  # "****t123"
    """
    data = dict(zip(cols, row))
    payload = {
        "id": data["id"],
        "tenantId": data.get("tenant_id"),
        "createdBy": data.get("created_by"),
        "ssoBaseUrl": data.get("sso_base_url") or "",
        "ssoClientId": data.get("sso_client_id") or "",
        "ssoClientSecretMasked": _mask_secret(data.get("sso_client_secret")),
        "ssoRedirectUri": data.get("sso_redirect_uri") or "",
        "ssoLaunchBaseUrl": data.get("sso_launch_base_url") or "",
        "ssoLaunchPath": data.get("sso_launch_path") or DEFAULT_SSO_LAUNCH_PATH,
        "ssoExchangePath": data.get("sso_exchange_path") or DEFAULT_SSO_EXCHANGE_PATH,
        "ssoUserSnapshotPathTemplate": data.get("sso_user_snapshot_path_template") or DEFAULT_SSO_USER_SNAPSHOT_PATH_TEMPLATE,
        "ssoDeltaPath": data.get("sso_delta_path") or DEFAULT_SSO_DELTA_PATH,
        "status": data.get("status") or ACTIVE_STATUS,
        "createdAt": _iso(data.get("created_at")),
        "updatedAt": _iso(data.get("updated_at")),
        "deletedAt": _iso(data.get("deleted_at")),
    }
    # 只在明确需要时返回完整的 secret
    if include_secret:
        payload["ssoClientSecret"] = data.get("sso_client_secret") or ""
    return payload


def _mask_secret(value: Any) -> str:
    """脱敏密钥，只显示后4位"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"


def _iso(value: Any) -> str:
    """将时间值转换为ISO格式字符串"""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")
