"""
API Key 管理模块
================

这个模块负责 API Key 的完整生命周期管理，包括创建、验证、更新、轮换和删除。

什么是 API Key？
----------------
API Key 就像一把"数字钥匙"，让外部程序可以安全地访问你的 RAG 知识库。
每个 API Key 都是唯一的，可以绑定到特定的知识库（kb_ids），并拥有特定的权限（capabilities）。

API Key 的格式：
    wwkb_{key_id}_{secret}
    例如：wwkb_ak_a1b2c3d4e5f6_x7y8z9...

    - wwkb：前缀，表示这是"wisewe knowledge base"的 key
    - ak_xxx：key_id，用于标识这个 key
    - 后面的长字符串：secret，只有创建时能看到一次，存储时只保存哈希值

API Key 的安全机制：
-------------------
1. **签名验证**：可配置是否需要对请求进行签名验证，防止请求被篡改
2. **IP 白名单**：可以限制只有特定 IP 才能使用这个 Key
3. **有效期**：可以设置过期时间
4. **限额控制**：可以设置每分钟请求数（RPM）和每日请求数限制
5. **Nonce 防重放**：签名验证时使用 nonce 防止同一请求被重复提交

API Key 的生命周期：
-------------------
1. **创建** → 生成明文 Key（只显示一次）+ 存储哈希值
2. **使用** → 每次请求时验证 Key 的有效性、权限、限额等
3. **轮换** → 生成新的 secret，旧 Key 失效
4. **禁用/删除** → 软删除，标记为 deleted 状态

主要功能：
---------
- create_api_key()：创建新的 API Key
- authenticate_api_key()：验证 API Key（核心认证逻辑）
- rotate_api_key()：轮换 API Key（更换 secret）
- update_api_key()：更新 API Key 属性
- delete_api_key()：软删除 API Key
- list_api_keys()：列出所有 API Key

OpenAPI 应用管理：
----------------
除了 API Key，还支持管理"OpenAPI 应用"（kb_openapi_apps 表）。
每个应用可以关联多个 API Key，方便按应用分组管理。
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext, anonymous_identity
from core.db.init_db import ensure_db_schema

# ============================================================================
# 常量定义
# ============================================================================

# 默认权限：查询知识库 + 图谱查询
DEFAULT_CAPABILITIES = ("rag.query", "rag.graph_query")

# 单个 API Key 最多绑定 20 个知识库
MAX_BOUND_KB_IDS = 20

# API Key 状态常量
ACTIVE_STATUS = "active"      # 正常使用中
DISABLED_STATUS = "disabled"  # 已禁用（暂停使用）
DELETED_STATUS = "deleted"    # 已删除（软删除）

# OpenAPI 应用允许的状态（应用没有 deleted，因为删除是软删除）
APP_STATUSES = {ACTIVE_STATUS, DISABLED_STATUS}


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass(frozen=True)
class ApiKeyAuthResult:
    """
    API Key 认证成功后的结果

    认证通过后，这个类封装了所有需要的信息：
    - identity：用户身份上下文（用于后续权限判断）
    - api_key_id：Key 的唯一标识
    - capabilities：这个 Key 拥有的权限列表
    - kb_ids：这个 Key 能访问的知识库 ID 列表
    - require_signature：是否要求签名验证
    - allowed_ips：IP 白名单
    - rpm_limit：每分钟请求限制（0 表示无限制）
    - daily_request_limit：每日请求限制（0 表示无限制）
    - app_id：关联的应用 ID（可选）

    frozen=True 表示这个类是不可变的，创建后不能修改，
    这样可以保证认证结果在传递过程中不被篡改。
    """
    identity: IdentityContext
    api_key_id: str
    capabilities: tuple[str, ...]
    kb_ids: tuple[str, ...]
    require_signature: bool = True
    allowed_ips: tuple[str, ...] = ()
    rpm_limit: int = 0
    daily_request_limit: int = 0
    app_id: str | None = None


@dataclass(frozen=True)
class ApiKeySignaturePayload:
    """
    签名验证的请求数据

    当 API Key 要求签名验证时，客户端需要提供以下信息：
    - method：HTTP 方法（GET、POST 等）
    - path：请求路径
    - body：请求体（原始字节）
    - timestamp：时间戳（防重放攻击）
    - nonce：随机字符串（防重放攻击）
    - body_sha256：请求体的 SHA256 哈希（防篡改）
    - signature：签名值

    签名算法：
        signature = HMAC-SHA256(plain_key, canonical_string)
        canonical_string = METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + BODY_SHA256
    """
    method: str
    path: str
    body: bytes
    timestamp: str | None
    nonce: str | None
    body_sha256: str | None
    signature: str | None


class ApiKeyError(ValueError):
    """
    API Key 相关错误

    所有 API Key 验证失败都会抛出这个异常。

    属性：
    - code：错误代码（如 "INVALID_API_KEY"、"API_KEY_EXPIRED" 等）
    - message：人类可读的错误信息
    - api_key_id：相关的 API Key ID（可选，用于日志追踪）

    示例：
        raise ApiKeyError("INVALID_API_KEY", "API Key is invalid", api_key_id="ak_xxx")
    """
    def __init__(self, code: str, message: str, *, api_key_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.api_key_id = api_key_id


# ============================================================================
# API Key 创建与管理
# ============================================================================

def create_api_key(
    *,
    name: str,
    kb_ids: list[str],
    capabilities: list[str] | None = None,
    note: str = "",
    require_signature: bool = True,
    allowed_ips: list[str] | None = None,
    rpm_limit: int = 0,
    daily_request_limit: int = 0,
    app_id: str | None = None,
    expires_at: datetime | None = None,
    identity: IdentityContext | None = None,
) -> dict[str, Any]:
    """
    创建一个新的 API Key

    这是最核心的创建函数。创建完成后，明文 Key 只会返回一次，
    之后数据库中只存储哈希值，无法反推原始 Key。

    参数说明：
    ---------
    name : str
        API Key 的名称，方便识别（如"生产环境 Key"、"测试 Key"）

    kb_ids : list[str]
        绑定的知识库 ID 列表。这个 Key 只能访问这些知识库。
        最多绑定 20 个知识库。

    capabilities : list[str] | None
        权限列表。默认是 ["rag.query", "rag.graph_query"]。
        常见权限：
        - "rag.query"：查询知识库
        - "rag.graph_query"：图谱查询

    note : str
        备注信息，用于记录 Key 的用途等

    require_signature : bool
        是否要求签名验证。默认 True（推荐开启）。
        开启后，每次请求都需要提供有效的签名。

    allowed_ips : list[str] | None
        IP 白名单。支持单个 IP 或 CIDR 格式（如 "192.168.1.0/24"）。
        为空表示不限制 IP。

    rpm_limit : int
        每分钟请求限制（Requests Per Minute）。0 表示无限制。

    daily_request_limit : int
        每日请求限制。0 表示无限制。

    app_id : str | None
        关联的 OpenAPI 应用 ID。用于按应用分组管理 Key。

    expires_at : datetime | None
        过期时间。为空表示永不过期。

    identity : IdentityContext | None
        调用者身份。用于多租户场景，确保只能在自己的租户下创建 Key。

    返回值：
    -------
    dict[str, Any]：包含 Key 详细信息的字典。
    重要：返回值中的 "plainKey" 字段是明文 Key，只在创建时返回一次！

    创建流程：
    ---------
    1. 生成 key_id（如 "ak_a1b2c3d4..."）
    2. 生成 40 字节的随机 secret
    3. 拼接成完整 Key：wwkb_{key_id}_{secret}
    4. 对完整 Key 进行 SHA256 哈希
    5. 存储哈希值到数据库（不存储明文）
    6. 返回明文 Key（仅此一次）

    示例：
    -----
    >>> result = create_api_key(
    ...     name="测试 Key",
    ...     kb_ids=["kb_001", "kb_002"],
    ...     capabilities=["rag.query"],
    ...     note="仅供测试使用"
    ... )
    >>> print(result["plainKey"])  # 保存这个！只显示一次
    wwkb_ak_xxx_yyy...
    """
    identity = identity or anonymous_identity()
    normalized_kb_ids = _normalize_kb_ids(kb_ids)
    normalized_capabilities = _normalize_capabilities(capabilities)
    normalized_allowed_ips = _normalize_allowed_ips(allowed_ips or [])
    key_id = _new_key_id()
    secret = _new_secret()
    plain_key = _format_plain_key(key_id, secret)
    key_hash = _hash_key(plain_key)
    key_prefix = plain_key[:16]
    key_suffix = plain_key[-8:]

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_api_keys(
                    id, app_id, name, tenant_id, created_by, key_hash, key_prefix, key_suffix,
                    status, kb_ids, capabilities, require_signature, allowed_ips,
                    rpm_limit, daily_request_limit, note, expires_at
                )
                VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    'active', %s::jsonb, %s::jsonb, %s, %s::jsonb,
                    %s, %s, %s, %s
                )
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (
                    key_id,
                    (app_id or "").strip() or None,
                    name.strip(),
                    identity.tenant_id if identity.enforce_access else None,
                    identity.user_id if identity.enforce_access else None,
                    key_hash,
                    key_prefix,
                    key_suffix,
                    _json_array(normalized_kb_ids),
                    _json_array(normalized_capabilities),
                    bool(require_signature),
                    _json_array(normalized_allowed_ips),
                    _normalize_non_negative_int(rpm_limit),
                    _normalize_non_negative_int(daily_request_limit),
                    note.strip(),
                    expires_at,
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

    return {**_row_to_payload(row, cols), "plainKey": plain_key}


def list_api_keys(identity: IdentityContext | None = None) -> list[dict[str, Any]]:
    """
    列出所有 API Key

    根据调用者的身份，返回其有权查看的 API Key 列表。
    - 如果是平台管理员，可以看到所有 Key
    - 如果是普通租户用户，只能看到自己租户的 Key
    - 如果是匿名调用，可以看到所有 Key（需要根据业务场景判断是否允许）

    参数：
    -----
    identity : IdentityContext | None
        调用者身份上下文

    返回：
    -----
    list[dict[str, Any]]：API Key 信息列表，按创建时间倒序排列

    注意：返回的列表不包含完整的 Key 值，只有前缀和后缀用于识别。
    例如：wwkb_ak_xxx...yyy（中间部分隐藏）
    """
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                       status, kb_ids, capabilities, require_signature, allowed_ips,
                       rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                       created_at, updated_at, deleted_at
                FROM kb_api_keys
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


# ============================================================================
# OpenAPI 应用管理
# ============================================================================

def create_openapi_app(
    *,
    name: str,
    note: str = "",
    identity: IdentityContext | None = None,
) -> dict[str, Any]:
    """
    创建一个 OpenAPI 应用

    OpenAPI 应用是一个分组概念，可以把多个 API Key 归类到同一个应用下。
    例如：创建一个"数据分析平台"应用，然后为这个应用创建多个 Key。

    参数：
    -----
    name : str
        应用名称

    note : str
        备注信息

    identity : IdentityContext | None
        调用者身份（用于多租户隔离）

    返回：
    -----
    dict[str, Any]：新创建的应用信息，包含 app_id
    """
    identity = identity or anonymous_identity()
    app_id = _new_app_id()
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_openapi_apps(id, name, tenant_id, owner_user_id, status, note)
                VALUES(%s, %s, %s, %s, 'active', %s)
                RETURNING id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                """,
                (
                    app_id,
                    name.strip(),
                    identity.tenant_id if identity.enforce_access else None,
                    identity.user_id if identity.enforce_access else None,
                    note.strip(),
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
    return _app_row_to_payload(row, cols)


def list_openapi_apps(identity: IdentityContext | None = None) -> list[dict[str, Any]]:
    """
    列出所有 OpenAPI 应用

    根据调用者身份返回其有权查看的应用列表。

    参数：
    -----
    identity : IdentityContext | None
        调用者身份上下文

    返回：
    -----
    list[dict[str, Any]]：应用信息列表，按创建时间倒序排列
    """
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity)
    app_where = where.replace("deleted_at IS NULL", "deleted_at IS NULL")
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                FROM kb_openapi_apps
                {app_where}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [_app_row_to_payload(row, cols) for row in rows]


def update_openapi_app(
    app_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    note: str | None = None,
    identity: IdentityContext | None = None,
) -> dict[str, Any] | None:
    """
    更新 OpenAPI 应用的属性

    可以更新的字段：name、status、note

    参数：
    -----
    app_id : str
        应用 ID

    name : str | None
        新的应用名称

    status : str | None
        新状态，只能是 "active" 或 "disabled"

    note : str | None
        新的备注信息

    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    dict[str, Any] | None：更新后的应用信息，如果找不到则返回 None
    """
    identity = identity or anonymous_identity()
    assignments: list[str] = []
    values: list[Any] = []
    if name is not None:
        assignments.append("name = %s")
        values.append(name.strip())
    if status is not None:
        normalized_status = status.strip().lower()
        if normalized_status not in APP_STATUSES:
            raise ValueError("status must be active or disabled")
        assignments.append("status = %s")
        values.append(normalized_status)
    if note is not None:
        assignments.append("note = %s")
        values.append(note.strip())
    if not assignments:
        matches = [item for item in list_openapi_apps(identity) if item["id"] == app_id]
        return matches[0] if matches else None

    assignments.append("updated_at = NOW()")
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_openapi_apps
                SET {", ".join(assignments)}
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, name, tenant_id, owner_user_id, status, note, created_at, updated_at, deleted_at
                """,
                (*values, app_id, *params),
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
    return _app_row_to_payload(row, cols) if row else None


def delete_openapi_app(app_id: str, identity: IdentityContext | None = None) -> bool:
    """
    删除 OpenAPI 应用（软删除）

    注意：这是软删除，只是把状态改为 "deleted" 并设置删除时间，
    数据仍在数据库中，可以用于审计追溯。

    参数：
    -----
    app_id : str
        应用 ID

    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    bool：是否成功删除（True = 删除成功，False = 找不到或无权限）
    """
    identity = identity or anonymous_identity()
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_openapi_apps
                SET status = 'deleted',
                    deleted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                """,
                (app_id, *params),
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


# ============================================================================
# API Key 更新与轮换
# ============================================================================

def update_api_key(
    key_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    kb_ids: list[str] | None = None,
    capabilities: list[str] | None = None,
    require_signature: bool | None = None,
    allowed_ips: list[str] | None = None,
    rpm_limit: int | None = None,
    daily_request_limit: int | None = None,
    app_id: str | None = None,
    app_id_provided: bool = False,
    note: str | None = None,
    expires_at: datetime | None = None,
    expires_at_provided: bool = False,
    identity: IdentityContext | None = None,
) -> dict[str, Any] | None:
    """
    更新 API Key 的属性

    可以更新的字段包括：
    - name：名称
    - status：状态（active/disabled）
    - kb_ids：绑定的知识库列表
    - capabilities：权限列表
    - require_signature：是否要求签名
    - allowed_ips：IP 白名单
    - rpm_limit：每分钟请求限制
    - daily_request_limit：每日请求限制
    - app_id：关联的应用 ID
    - note：备注
    - expires_at：过期时间

    特殊参数说明：
    -------------
    app_id_provided : bool
        因为 app_id 可以设为 None（清空关联），所以需要这个标志位
        来区分"没有传 app_id 参数"和"传了 app_id=None（清空）"

    expires_at_provided : bool
        同上，用于区分"没传过期时间"和"传了 None（永不过期）"

    返回：
    -----
    dict[str, Any] | None：更新后的 Key 信息，找不到则返回 None
    """
    identity = identity or anonymous_identity()
    assignments: list[str] = []
    values: list[Any] = []

    if name is not None:
        assignments.append("name = %s")
        values.append(name.strip())
    if status is not None:
        normalized_status = status.strip().lower()
        if normalized_status not in {ACTIVE_STATUS, DISABLED_STATUS}:
            raise ValueError("status must be active or disabled")
        assignments.append("status = %s")
        values.append(normalized_status)
    if kb_ids is not None:
        assignments.append("kb_ids = %s::jsonb")
        values.append(_json_array(_normalize_kb_ids(kb_ids)))
    if capabilities is not None:
        assignments.append("capabilities = %s::jsonb")
        values.append(_json_array(_normalize_capabilities(capabilities)))
    if require_signature is not None:
        assignments.append("require_signature = %s")
        values.append(bool(require_signature))
    if allowed_ips is not None:
        assignments.append("allowed_ips = %s::jsonb")
        values.append(_json_array(_normalize_allowed_ips(allowed_ips)))
    if rpm_limit is not None:
        assignments.append("rpm_limit = %s")
        values.append(_normalize_non_negative_int(rpm_limit))
    if daily_request_limit is not None:
        assignments.append("daily_request_limit = %s")
        values.append(_normalize_non_negative_int(daily_request_limit))
    if app_id_provided:
        assignments.append("app_id = %s")
        values.append((app_id or "").strip() or None)
    if note is not None:
        assignments.append("note = %s")
        values.append(note.strip())
    if expires_at_provided:
        assignments.append("expires_at = %s")
        values.append(expires_at)

    if not assignments:
        return get_api_key(key_id, identity)

    assignments.append("updated_at = NOW()")
    where, params = _scope_filter(identity, include_where=False)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_api_keys
                SET {", ".join(assignments)}
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (*values, key_id, *params),
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
    if not row:
        return None
    return _row_to_payload(row, cols)


def rotate_api_key(key_id: str, identity: IdentityContext | None = None) -> dict[str, Any] | None:
    """
    轮换 API Key（更换 Secret）

    当怀疑 Key 可能泄露，或者需要定期更换时，使用此函数。
    会生成新的 secret，旧的 Key 立即失效。

    轮换流程：
    ---------
    1. 生成新的 40 字节随机 secret
    2. 重新拼接完整 Key：wwkb_{key_id}_{new_secret}
    3. 更新数据库中的哈希值
    4. 返回新的明文 Key（只显示一次）

    重要：
    -----
    - 轮换后，旧的 Key 立即失效
    - 新 Key 只会返回一次，请妥善保存
    - 如果 Key 之前是 disabled 状态，轮换后会自动变为 active

    参数：
    -----
    key_id : str
        要轮换的 Key ID（如 "ak_xxx"）

    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    dict[str, Any] | None：包含新 Key 的信息，重点看 "plainKey" 字段
    """
    identity = identity or anonymous_identity()
    secret = _new_secret()
    plain_key = _format_plain_key(key_id, secret)
    key_hash = _hash_key(plain_key)
    key_prefix = plain_key[:16]
    key_suffix = plain_key[-8:]
    where, params = _scope_filter(identity, include_where=False)

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_api_keys
                SET key_hash = %s,
                    key_prefix = %s,
                    key_suffix = %s,
                    status = 'active',
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                RETURNING id, app_id, name, tenant_id, created_by, key_prefix, key_suffix,
                          status, kb_ids, capabilities, require_signature, allowed_ips,
                          rpm_limit, daily_request_limit, note, expires_at, last_used_at,
                          created_at, updated_at, deleted_at
                """,
                (key_hash, key_prefix, key_suffix, key_id, *params),
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
    if not row:
        return None
    return {**_row_to_payload(row, cols), "plainKey": plain_key}


def delete_api_key(key_id: str, identity: IdentityContext | None = None) -> bool:
    """
    删除 API Key（软删除）

    这是软删除，数据仍在数据库中，可以用于审计追溯。
    删除后的 Key 状态变为 "deleted"，无法再使用。

    参数：
    -----
    key_id : str
        要删除的 Key ID

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
                UPDATE kb_api_keys
                SET status = 'deleted',
                    deleted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND deleted_at IS NULL
                  {where}
                """,
                (key_id, *params),
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


def get_api_key(key_id: str, identity: IdentityContext | None = None) -> dict[str, Any] | None:
    """
    获取单个 API Key 的详细信息

    这是一个便捷函数，内部调用 list_api_keys 然后筛选。

    参数：
    -----
    key_id : str
        Key ID

    identity : IdentityContext | None
        调用者身份

    返回：
    -----
    dict[str, Any] | None：Key 信息，找不到则返回 None
    """
    matches = [item for item in list_api_keys(identity) if item["id"] == key_id]
    return matches[0] if matches else None


# ============================================================================
# API Key 认证（核心安全逻辑）
# ============================================================================

def authenticate_api_key(
    plain_key: str,
    *,
    kb_id: str,
    capability: str,
    signature: ApiKeySignaturePayload | None = None,
    client_ip: str | None = None,
    force_signature: bool = False,
) -> ApiKeyAuthResult:
    """
    验证 API Key 并返回认证结果

    这是整个模块最核心的函数！每次 API 请求都会调用这个函数来验证 Key。

    验证流程（按顺序执行）：
    ----------------------
    1. **基础检查**
       - Key 是否为空
       - kb_id 是否为空

    2. **数据库查询**
       - 根据 Key 的哈希值查找记录
       - 如果找不到 → INVALID_API_KEY

    3. **状态检查**
       - 是否被禁用（disabled） → API_KEY_DISABLED
       - 是否已过期 → API_KEY_EXPIRED

    4. **权限检查**
       - kb_id 是否在绑定的知识库列表中（kb_id="*" 表示全部）
       - capability 是否在权限列表中

    5. **IP 白名单检查**
       - 如果设置了 allowed_ips，检查客户端 IP 是否在白名单中

    6. **签名验证**（如果 require_signature=True 或 force_signature=True）
       - 检查请求的签名是否正确
       - 使用 HMAC-SHA256 算法验证
       - 防重放攻击：检查 timestamp（5 分钟窗口）+ nonce（防重复使用）

    7. **限额检查**
       - 检查每分钟请求数是否超限
       - 检查每日请求数是否超限

    8. **更新使用时间**
       - 更新 last_used_at 字段

    参数说明：
    ---------
    plain_key : str
        完整的 API Key（如 "wwkb_ak_xxx_yyy"）

    kb_id : str
        要访问的知识库 ID。特殊值 "*" 表示访问所有绑定的知识库。

    capability : str
        需要的权限（如 "rag.query"）

    signature : ApiKeySignaturePayload | None
        签名数据（如果需要签名验证）

    client_ip : str | None
        客户端 IP 地址（用于 IP 白名单验证）

    force_signature : bool
        强制要求签名验证（即使 Key 的 require_signature=False）

    返回值：
    -------
    ApiKeyAuthResult：认证成功后的信息，包含：
    - identity：用户身份上下文（后续权限判断使用）
    - api_key_id：Key ID
    - capabilities：权限列表
    - kb_ids：绑定的知识库列表
    - 其他限制参数

    异常：
    -----
    抛出 ApiKeyError，包含具体的错误代码：
    - API_KEY_REQUIRED：Key 为空
    - INVALID_API_KEY：Key 无效
    - API_KEY_DISABLED：Key 已禁用
    - API_KEY_EXPIRED：Key 已过期
    - KB_BINDING_DENIED：Key 未绑定此知识库
    - CAPABILITY_DENIED：Key 缺少权限
    - IP_NOT_ALLOWED：IP 不在白名单
    - SIGNATURE_REQUIRED：缺少签名
    - INVALID_SIGNATURE：签名无效
    - RATE_LIMITED：超过 RPM 限制
    - QUOTA_EXCEEDED：超过每日限额

    示例：
    -----
    >>> try:
    ...     result = authenticate_api_key(
    ...         "wwkb_ak_xxx_yyy",
    ...         kb_id="kb_001",
    ...         capability="rag.query",
    ...         client_ip="192.168.1.100"
    ...     )
    ...     print("认证成功！")
    ... except ApiKeyError as e:
    ...     print(f"认证失败: {e.code} - {e.message}")
    """
    normalized_key = (plain_key or "").strip()
    if not normalized_key:
        raise ApiKeyError("API_KEY_REQUIRED", "API Key is required")
    if not kb_id.strip():
        raise ApiKeyError("KB_ID_REQUIRED", "kb_id is required for OpenAPI calls")

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, status, kb_ids, capabilities, expires_at,
                       require_signature, allowed_ips, rpm_limit, daily_request_limit, app_id
                FROM kb_api_keys
                WHERE key_hash = %s
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (_hash_key(normalized_key),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise ApiKeyError("INVALID_API_KEY", "API Key is invalid")

    key_id = str(row[0])
    tenant_id, status, kb_ids, capabilities, expires_at, require_signature, allowed_ips = row[1:8]
    rpm_limit = _int_at(row, 8)
    daily_request_limit = _int_at(row, 9)
    app_id = _str_at(row, 10)
    if status == DISABLED_STATUS:
        raise ApiKeyError("API_KEY_DISABLED", "API Key is disabled", api_key_id=key_id)
    if status != ACTIVE_STATUS:
        raise ApiKeyError("INVALID_API_KEY", "API Key is not active", api_key_id=key_id)
    if expires_at and _ensure_aware(expires_at) <= datetime.now(timezone.utc):
        raise ApiKeyError("API_KEY_EXPIRED", "API Key is expired", api_key_id=key_id)

    kb_scope = tuple(str(item) for item in _list_from_json(kb_ids))
    capability_scope = tuple(str(item) for item in _list_from_json(capabilities))
    if kb_id != "*" and kb_id not in kb_scope:
        raise ApiKeyError("KB_BINDING_DENIED", "API Key is not bound to this knowledge base", api_key_id=key_id)
    if capability not in capability_scope:
        raise ApiKeyError("CAPABILITY_DENIED", "API Key lacks the required capability", api_key_id=key_id)

    allowed_ip_scope = tuple(str(item) for item in _list_from_json(allowed_ips))
    _verify_client_ip(client_ip, allowed_ip_scope, api_key_id=key_id)
    if bool(require_signature) or force_signature:
        _verify_signature(
            key_id=key_id,
            plain_key=normalized_key,
            signature=signature,
        )

    _enforce_api_key_quota(
        key_id=key_id,
        rpm_limit=rpm_limit,
        daily_request_limit=daily_request_limit,
    )
    _mark_api_key_used(key_id)
    return ApiKeyAuthResult(
        identity=IdentityContext(
            tenant_id=str(tenant_id) if tenant_id else None,
            user_id=f"api_key:{key_id}",
            username=key_id,
            display_name=f"API Key {key_id}",
            is_tenant_admin=True,
            is_authenticated=True,
            source="api_key",
        ),
        api_key_id=key_id,
        capabilities=capability_scope,
        kb_ids=kb_scope,
        require_signature=bool(require_signature),
        allowed_ips=allowed_ip_scope,
        rpm_limit=rpm_limit,
        daily_request_limit=daily_request_limit,
        app_id=app_id,
    )


# ============================================================================
# 内部辅助函数
# ============================================================================

def _mark_api_key_used(key_id: str) -> None:
    """
    更新 API Key 的最后使用时间

    每次认证成功后调用，记录 last_used_at 字段。
    这个操作是"尽力而为"的，即使失败也不会影响主流程。
    """
    try:
        conn = get_db_connection()
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE kb_api_keys SET last_used_at = NOW() WHERE id = %s", (key_id,))
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
    finally:
        conn.close()


def _verify_client_ip(client_ip: str | None, allowed_ips: tuple[str, ...], *, api_key_id: str | None = None) -> None:
    """
    验证客户端 IP 是否在白名单中

    支持两种格式：
    - 单个 IP：如 "192.168.1.100"
    - CIDR 网段：如 "192.168.1.0/24"（表示整个网段）

    参数：
    -----
    client_ip : str | None
        客户端 IP 地址（可能包含多个 IP，用逗号分隔，取第一个）

    allowed_ips : tuple[str, ...]
        IP 白名单列表

    api_key_id : str | None
        Key ID（用于错误信息）
    """
    if not allowed_ips:
        return
    value = (client_ip or "").split(",", 1)[0].strip()
    if not value:
        raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id)
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id) from exc
    for item in allowed_ips:
        try:
            # CIDR 格式：检查 IP 是否在网段内
            if "/" in item and address in ipaddress.ip_network(item, strict=False):
                return
            # 单个 IP：精确匹配
            if "/" not in item and address == ipaddress.ip_address(item):
                return
        except ValueError:
            continue
    raise ApiKeyError("IP_NOT_ALLOWED", "Client IP is not allowed for this API Key", api_key_id=api_key_id)


def _verify_signature(
    *,
    key_id: str,
    plain_key: str,
    signature: ApiKeySignaturePayload | None,
) -> None:
    """
    验证请求签名

    签名算法说明：
    -------------
    1. 构造"规范化字符串"：
       canonical = METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + BODY_SHA256

    2. 使用 API Key 作为密钥，计算 HMAC-SHA256 签名：
       signature = HMAC-SHA256(plain_key, canonical)

    验证步骤：
    ---------
    1. 检查签名参数是否完整（timestamp、nonce、body_sha256、signature）
    2. 验证 body 的 SHA256 哈希是否匹配
    3. 验证时间戳是否在 5 分钟窗口内（防重放）
    4. 计算期望的签名值，与提供的签名比较
    5. 记录 nonce，防止同一请求被重复使用（防重放攻击）

    参数：
    -----
    key_id : str
        Key ID

    plain_key : str
        完整的 API Key（作为签名密钥）

    signature : ApiKeySignaturePayload | None
        签名数据
    """
    if signature is None:
        raise ApiKeyError("SIGNATURE_REQUIRED", "Signed OpenAPI headers are required for this API Key", api_key_id=key_id)
    timestamp = (signature.timestamp or "").strip()
    nonce = (signature.nonce or "").strip()
    body_sha256 = (signature.body_sha256 or "").strip().lower()
    provided_signature = (signature.signature or "").strip().lower()
    if not timestamp or not nonce or not body_sha256 or not provided_signature:
        raise ApiKeyError("SIGNATURE_REQUIRED", "Signed OpenAPI headers are required for this API Key", api_key_id=key_id)
    if len(nonce) > 128:
        raise ApiKeyError("INVALID_NONCE", "Nonce is too long", api_key_id=key_id)

    # 验证 body 的 SHA256 哈希
    expected_body_sha256 = hashlib.sha256(signature.body).hexdigest()
    if not hmac.compare_digest(body_sha256, expected_body_sha256):
        raise ApiKeyError("BODY_HASH_MISMATCH", "Request body hash does not match", api_key_id=key_id)

    # 验证时间戳（允许 ±5 分钟的时间偏差）
    signed_at = _parse_timestamp(timestamp, api_key_id=key_id)
    now = datetime.now(timezone.utc)
    if abs((now - signed_at).total_seconds()) > 300:
        raise ApiKeyError("TIMESTAMP_EXPIRED", "Request timestamp is outside the allowed 5 minute window", api_key_id=key_id)

    # 构造规范化字符串并验证签名
    canonical = "\n".join(
        [
            signature.method.upper(),
            signature.path,
            timestamp,
            nonce,
            body_sha256,
        ]
    )
    expected_signature = hmac.new(plain_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ApiKeyError("INVALID_SIGNATURE", "Request signature is invalid", api_key_id=key_id)

    # 记录 nonce，防止重放攻击
    _record_nonce(
        key_id=key_id,
        nonce=nonce,
        request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(minutes=10),
    )


def _parse_timestamp(value: str, *, api_key_id: str | None = None) -> datetime:
    """
    解析时间戳字符串

    支持两种格式：
    - Unix 时间戳（整数）：如 "1704067200"
    - ISO 8601 格式：如 "2024-01-01T00:00:00Z"

    返回带时区的 datetime 对象（UTC）。
    """
    try:
        if value.isdigit():
            parsed = datetime.fromtimestamp(int(value), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiKeyError("INVALID_TIMESTAMP", "Request timestamp is invalid", api_key_id=api_key_id) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_nonce(*, key_id: str, nonce: str, request_hash: str, expires_at: datetime) -> None:
    """
    记录 nonce 到数据库（防重放攻击）

    流程：
    1. 先清理过期的 nonce 记录
    2. 尝试插入新的 nonce 记录
    3. 如果 nonce 已存在（ON CONFLICT），说明是重放攻击，拒绝请求

    参数：
    -----
    key_id : str
        Key ID

    nonce : str
        随机字符串

    request_hash : str
        请求的哈希值（用于审计）

    expires_at : datetime
        过期时间（nonce 的有效期）
    """
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            # 清理过期的 nonce
            cur.execute("DELETE FROM kb_api_key_nonces WHERE expires_at <= NOW()")
            # 插入新 nonce，如果已存在则不插入（ON CONFLICT DO NOTHING）
            cur.execute(
                """
                INSERT INTO kb_api_key_nonces(api_key_id, nonce, request_hash, expires_at)
                VALUES(%s, %s, %s, %s)
                ON CONFLICT(api_key_id, nonce) DO NOTHING
                """,
                (key_id, nonce, request_hash, expires_at),
            )
            inserted = cur.rowcount > 0
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()
    # 如果没有插入成功，说明 nonce 已存在，可能是重放攻击
    if not inserted:
        raise ApiKeyError("NONCE_REPLAYED", "Request nonce has already been used", api_key_id=key_id)


def _enforce_api_key_quota(*, key_id: str, rpm_limit: int, daily_request_limit: int) -> None:
    """
    执行请求限额检查

    支持两种限额：
    - rpm_limit：每分钟请求限制（Requests Per Minute）
    - daily_request_limit：每日请求限制

    实现原理：
    ---------
    使用 PostgreSQL 的 UPSERT 功能（INSERT ... ON CONFLICT）：
    1. 尝试插入新的计数记录（count=1）
    2. 如果记录已存在，则更新 count = count + 1（只有当 count < limit 时）
    3. 如果更新失败（count >= limit），说明超限，抛出异常

    参数：
    -----
    key_id : str
        Key ID

    rpm_limit : int
        每分钟限制（0 表示无限制）

    daily_request_limit : int
        每日限制（0 表示无限制）
    """
    if rpm_limit <= 0 and daily_request_limit <= 0:
        return
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            if rpm_limit > 0:
                _consume_quota_window(cur, key_id=key_id, window_type="minute", limit=rpm_limit)
            if daily_request_limit > 0:
                _consume_quota_window(cur, key_id=key_id, window_type="day", limit=daily_request_limit)
        conn.commit()
    except ApiKeyError:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()


def _consume_quota_window(cur: Any, *, key_id: str, window_type: str, limit: int) -> None:
    """
    消耗一个时间窗口内的配额

    时间窗口类型：
    - "minute"：每分钟窗口（用于 RPM 限制）
    - "day"：每日窗口（用于每日限额）

    这是原子操作，使用数据库的 UPSERT 保证并发安全。
    """
    if window_type == "minute":
        bucket_sql = "date_trunc('minute', NOW())"
        code = "RATE_LIMITED"
        message = "API Key minute request limit exceeded"
    else:
        bucket_sql = "date_trunc('day', NOW())"
        code = "QUOTA_EXCEEDED"
        message = "API Key daily request quota exceeded"
    cur.execute(
        f"""
        INSERT INTO kb_api_key_usage_windows(api_key_id, window_type, window_start, request_count, updated_at)
        VALUES(%s, %s, {bucket_sql}, 1, NOW())
        ON CONFLICT(api_key_id, window_type, window_start)
        DO UPDATE SET
            request_count = kb_api_key_usage_windows.request_count + 1,
            updated_at = NOW()
        WHERE kb_api_key_usage_windows.request_count < %s
        RETURNING request_count
        """,
        (key_id, window_type, limit),
    )
    # 如果返回 None，说明 WHERE 条件不满足，即已超限
    if cur.fetchone() is None:
        raise ApiKeyError(code, message, api_key_id=key_id)


# ============================================================================
# 参数标准化与验证
# ============================================================================

def _normalize_kb_ids(kb_ids: list[str]) -> list[str]:
    """
    标准化知识库 ID 列表

    - 去除空白和空值
    - 去重
    - 至少需要绑定一个知识库
    - 最多绑定 20 个知识库
    """
    result = []
    for item in kb_ids:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    if not result:
        raise ValueError("At least one knowledge base must be bound")
    if len(result) > MAX_BOUND_KB_IDS:
        raise ValueError(f"An API Key can bind at most {MAX_BOUND_KB_IDS} knowledge bases")
    return result


def _normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    """
    标准化权限列表

    - 如果未提供，使用默认权限（rag.query + rag.graph_query）
    - 去除空白和空值
    - 去重
    - 至少需要一个权限
    """
    raw = capabilities or list(DEFAULT_CAPABILITIES)
    result = []
    for item in raw:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    if not result:
        raise ValueError("At least one capability is required")
    return result


def _normalize_allowed_ips(allowed_ips: list[str]) -> list[str]:
    """
    标准化 IP 白名单列表

    - 去除空白和空值
    - 去重
    - 验证格式（支持单个 IP 或 CIDR 网段）

    示例有效格式：
    - "192.168.1.100"（单个 IP）
    - "10.0.0.0/8"（CIDR 网段）
    - "2001:db8::/32"（IPv6 CIDR）
    """
    result: list[str] = []
    for item in allowed_ips:
        value = str(item or "").strip()
        if not value or value in result:
            continue
        try:
            # CIDR 格式
            if "/" in value:
                ipaddress.ip_network(value, strict=False)
            else:
                # 单个 IP
                ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"Invalid allowed IP or CIDR: {value}") from exc
        result.append(value)
    return result


def _normalize_non_negative_int(value: int | str | None) -> int:
    """
    标准化非负整数

    用于 rpm_limit、daily_request_limit 等字段。
    - None 或空值转换为 0
    - 必须是非负整数
    """
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit fields must be non-negative integers") from exc
    if parsed < 0:
        raise ValueError("limit fields must be non-negative integers")
    return parsed


def _scope_filter(identity: IdentityContext, *, include_where: bool = True) -> tuple[str, tuple[str, ...]]:
    """
    生成 SQL 查询的租户范围过滤条件

    根据调用者的身份，生成适当的 WHERE 子句：
    - 平台管理员：不限制租户（可以看到所有数据）
    - 普通租户用户：只能看到自己租户的数据
    - 匿名用户：可以看到所有数据（根据业务需求调整）

    参数：
    -----
    identity : IdentityContext
        调用者身份

    include_where : bool
        是否包含 "WHERE" 关键字
        - True：返回 "WHERE deleted_at IS NULL AND tenant_id = %s"
        - False：返回 "AND tenant_id = %s"（用于拼接在其他条件后）

    返回：
    -----
    tuple[str, tuple[str, ...]]：(WHERE 子句, 参数元组)
    """
    if identity.enforce_access and not identity.is_platform_admin:
        clause = "tenant_id = %s"
        prefix = "WHERE" if include_where else "AND"
        return f"{prefix} deleted_at IS NULL AND {clause}" if include_where else f"AND {clause}", (identity.tenant_id or "",)
    return ("WHERE deleted_at IS NULL" if include_where else ""), ()


# ============================================================================
# ID 与 Key 生成
# ============================================================================

def _new_key_id() -> str:
    """
    生成新的 API Key ID

    格式：ak_{24位十六进制}
    例如：ak_a1b2c3d4e5f6g7h8i9j0k1l2
    """
    return f"ak_{secrets.token_hex(12)}"


def _new_app_id() -> str:
    """
    生成新的应用 ID

    格式：app_{16位十六进制}
    例如：app_a1b2c3d4e5f6g7h8
    """
    return f"app_{secrets.token_hex(8)}"


def _new_secret() -> str:
    """
    生成新的 Secret（密钥部分）

    使用 secrets 模块生成密码学安全的随机字符串。
    长度：40 个字符（去掉 - 和 _，只保留字母数字）
    """
    return secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:40]


def _format_plain_key(key_id: str, secret: str) -> str:
    """
    格式化完整的 API Key

    格式：wwkb_{key_id}_{secret}
    例如：wwkb_ak_a1b2c3d4_x7y8z9w0...

    - wwkb：前缀，表示 wisewe knowledge base
    - key_id：Key 的唯一标识
    - secret：密钥部分（只有创建时能看到）
    """
    return f"wwkb_{key_id}_{secret}"


def _hash_key(value: str) -> str:
    """
    对 API Key 进行 SHA256 哈希

    数据库中只存储哈希值，不存储明文 Key。
    这样即使数据库泄露，攻击者也无法还原原始 Key。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ============================================================================
# JSON 与数据转换
# ============================================================================

def _json_array(values: list[str]) -> str:
    """
    将字符串列表转换为 JSON 数组字符串

    用于 PostgreSQL 的 JSONB 类型字段。
    """
    import json

    return json.dumps(values, ensure_ascii=False)


def _list_from_json(value: Any) -> list[Any]:
    """
    从 JSON 值中提取列表

    处理数据库返回的 JSONB 字段，可能是：
    - None：返回空列表
    - list：直接返回
    - tuple：转换为列表
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _ensure_aware(value: datetime) -> datetime:
    """
    确保 datetime 对象带有时区信息

    如果没有时区，默认设为 UTC。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _int_at(row: tuple[Any, ...], index: int, default: int = 0) -> int:
    """
    从数据库行中安全地提取整数值

    用于处理可能为 None 或超出范围的字段。
    """
    if len(row) <= index:
        return default
    try:
        return max(0, int(row[index] or 0))
    except (TypeError, ValueError):
        return default


def _str_at(row: tuple[Any, ...], index: int) -> str | None:
    """
    从数据库行中安全地提取字符串值

    返回去除首尾空白的字符串，空字符串返回 None。
    """
    if len(row) <= index or row[index] is None:
        return None
    value = str(row[index]).strip()
    return value or None


def _iso(value: Any) -> str | None:
    """
    将值转换为 ISO 格式字符串

    主要用于 datetime 对象的格式化输出。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_payload(row: tuple[Any, ...], cols: list[str]) -> dict[str, Any]:
    """
    将数据库行转换为 API Key 的 JSON 输出格式

    这是数据库记录到 API 响应的转换层：
    - 字段名从蛇形命名（snake_case）转换为驼峰命名（camelCase）
    - 处理 JSONB 字段的解析
    - 格式化时间字段为 ISO 字符串

    注意：返回值不包含完整的 Key，只有前缀和后缀用于识别。
    """
    data = dict(zip(cols, row))
    return {
        "id": data["id"],
        "appId": data.get("app_id"),
        "name": data["name"],
        "tenantId": data.get("tenant_id"),
        "createdBy": data.get("created_by"),
        "keyPrefix": data.get("key_prefix"),
        "keySuffix": data.get("key_suffix"),
        "status": data.get("status"),
        "kbIds": [str(item) for item in _list_from_json(data.get("kb_ids"))],
        "capabilities": [str(item) for item in _list_from_json(data.get("capabilities"))],
        "requireSignature": bool(data.get("require_signature")),
        "allowedIps": [str(item) for item in _list_from_json(data.get("allowed_ips"))],
        "rpmLimit": int(data.get("rpm_limit") or 0),
        "dailyRequestLimit": int(data.get("daily_request_limit") or 0),
        "note": data.get("note") or "",
        "expiresAt": _iso(data.get("expires_at")),
        "lastUsedAt": _iso(data.get("last_used_at")),
        "createdAt": _iso(data.get("created_at")),
        "updatedAt": _iso(data.get("updated_at")),
        "deletedAt": _iso(data.get("deleted_at")),
    }


def _app_row_to_payload(row: tuple[Any, ...], cols: list[str]) -> dict[str, Any]:
    """
    将数据库行转换为 OpenAPI 应用的 JSON 输出格式

    类似 _row_to_payload，但用于应用表。
    """
    data = dict(zip(cols, row))
    return {
        "id": data["id"],
        "name": data["name"],
        "tenantId": data.get("tenant_id"),
        "ownerUserId": data.get("owner_user_id"),
        "status": data.get("status"),
        "note": data.get("note") or "",
        "createdAt": _iso(data.get("created_at")),
        "updatedAt": _iso(data.get("updated_at")),
        "deletedAt": _iso(data.get("deleted_at")),
    }
