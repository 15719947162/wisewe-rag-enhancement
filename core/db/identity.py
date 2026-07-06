"""
身份认证与授权管理模块

这个模块负责管理用户的身份信息，包括登录会话、用户/租户/角色的同步。

## 核心概念（大白话解释）

1. **租户（Tenant）**：就像一个公司或组织。每个租户是独立的，数据互不干扰。
   比如公司A和公司B是两个租户，他们的员工互看不到对方的知识库。

2. **用户（User）**：具体的登录者，每个用户属于一个租户。
   比如张三是公司A的员工，李四是公司B的员工。

3. **角色（Role）**：用户的权限身份，比如"管理员"、"普通用户"。
   一个用户可以有多个角色，就像一个人可以同时是"项目经理"和"技术主管"。

4. **用户-角色关系（User-Role Relation）**：记录哪个用户拥有哪个角色。

## 主要功能

- **会话管理**：用户登录后创建会话，后续请求通过会话验证身份
- **身份同步**：从外部系统（如SSO）同步用户、租户、角色信息到本地数据库
- **SSO凭证防重放**：防止一次性登录凭证被重复使用
- **知识库权限转移**：当用户被删除或禁用时，标记其知识库待转移

## 数据流向

外部身份系统 → 同步到本地快照表 → 创建会话 → API请求验证会话 → 返回身份上下文
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db.connection import get_db_connection


# ============ 常量定义 ============

# 租户管理员的角色代码，拥有这个角色的用户可以管理租户内的所有资源
TENANT_ADMIN_ROLE_CODE = "superManager"

# 会话Cookie的名称，前端通过这个名字获取会话Token
SESSION_COOKIE_NAME = "kb_session"

# 默认会话有效期：4小时（单位：秒）
# 用户登录后4小时内不需要再次登录，超过后需要重新登录
DEFAULT_SESSION_TTL_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class IdentityContext:
    """
    用户身份上下文 - 存储当前登录用户的完整身份信息

    这个类是不可变的（frozen=True），创建后不能修改，确保身份信息在请求过程中不会被篡改。

    属性说明：
        tenant_id: 租户ID，用户所属的公司/组织
        user_id: 用户ID，用户的唯一标识
        username: 用户名，用于登录
        display_name: 显示名称，用户的友好名称（如"张三"）
        tenant_name: 租户名称（如"XX科技公司"）
        is_tenant_admin: 是否是租户管理员，管理员有更高的权限
        is_platform_admin: 是否是平台管理员（超级管理员，管理所有租户）
        is_authenticated: 是否已登录认证
        source: 身份来源（如 "sso"、"identity_snapshot"、"anonymous"）
        role_codes: 用户拥有的角色代码列表

    使用场景：
        - 每个API请求都会解析出 IdentityContext
        - 根据其中的信息判断用户是否有权限访问资源
    """

    tenant_id: str | None = None
    user_id: str | None = None
    username: str = ""
    display_name: str = ""
    tenant_name: str = ""
    is_tenant_admin: bool = False
    is_platform_admin: bool = False
    is_authenticated: bool = False
    source: str = "anonymous"
    role_codes: tuple[str, ...] = ()

    @property
    def enforce_access(self) -> bool:
        """
        检查是否需要强制权限控制

        只有同时满足以下条件才需要权限控制：
        1. 用户已登录（is_authenticated）
        2. 有租户ID和用户ID

        返回：
            True 表示需要验证权限，False 表示匿名用户或信息不完整
        """
        return self.is_authenticated and bool(self.tenant_id and self.user_id)


# ============ 工具函数 ============

def anonymous_identity() -> IdentityContext:
    """
    创建匿名用户身份上下文

    当用户未登录或会话无效时，返回这个匿名身份。
    匿名用户没有租户ID、用户ID，is_authenticated 为 False。

    返回：
        一个所有字段都是默认值（空或False）的 IdentityContext
    """
    return IdentityContext()


def hash_secret(value: str) -> str:
    """
    使用 SHA256 对敏感值进行哈希

    把明文转换成不可逆的哈希值，用于：
    - 存储会话Token（不存明文，只存哈希）
    - 存储密码等敏感信息

    参数：
        value: 要哈希的明文字符串

    返回：
        64字符的十六进制哈希字符串
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint_credential(value: str) -> str:
    """
    生成凭证指纹（前32位哈希）

    用于SSO凭证的去重检查。只用前32位是因为：
    - 指纹不需要加密级别的安全性
    - 更短的指纹便于索引和比较

    参数：
        value: 凭证内容（如一次性登录Token）

    返回：
        32字符的指纹字符串
    """
    return hash_secret(value)[:32]


def _as_utc(value: datetime | None) -> datetime | None:
    """
    将时间转换为UTC时区

    确保所有时间都以UTC存储和比较，避免时区混乱。

    参数：
        value: 任意时区的datetime对象，或None

    返回：
        转换为UTC时区的datetime，或None
    """
    if value is None:
        return None
    if value.tzinfo is None:
        # 没有时区信息，假设是UTC
        return value.replace(tzinfo=timezone.utc)
    # 有时区信息，转换为UTC
    return value.astimezone(timezone.utc)


def _normalize_status(value: Any) -> str:
    """
    标准化状态字符串

    外部系统可能用不同的方式表示状态（如 "Active"、"ENABLED"、"1"、"true"）。
    这个函数统一转换为我们内部使用的标准状态。

    参数：
        value: 原始状态值，可以是字符串、数字或其他类型

    返回：
        标准化后的状态字符串：
        - "active" 表示正常/启用
        - "disabled"、"deleted" 等表示异常状态
    """
    status = str(value or "active").strip().lower()
    # 各种"正常"状态都统一为 "active"
    if status in {"active", "enabled", "normal", "1", "true"}:
        return "active"
    # 各种"异常"状态保留原样
    if status in {"disabled", "disable", "inactive", "deleted", "removed", "resigned", "frozen", "0", "false"}:
        return status
    return status or "inactive"


def _flag_is_true(value: Any) -> bool:
    """
    判断标志位是否为真

    数据库中的布尔字段可能有多种表示方式：
    - Python bool: True/False
    - 整数: 1/0
    - 字符串: "1", "true", "yes", "y", "on"

    参数：
        value: 任意类型的标志值

    返回：
        True 如果值表示"真"，否则 False
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _has_flag(item: dict[str, Any], *keys: str) -> bool:
    """
    检查字典中是否存在指定的标志字段

    用于判断对象是否有某个状态字段，即使值为空也算有。

    参数：
        item: 数据字典
        *keys: 要检查的字段名列表（任意一个存在即可）

    返回：
        True 如果任一字段存在且不为None
    """
    return any(key in item and item.get(key) is not None for key in keys)


def _normalize_user_status(user: dict[str, Any]) -> str:
    """
    标准化用户状态

    优先检查删除/禁用标志，然后检查状态字段。
    这是因为有些系统用字段表示状态（deleted=true），
    有些用状态值表示（status="deleted"）。

    参数：
        user: 用户数据字典

    返回：
        标准化后的状态："active"、"deleted" 或 "disabled"
    """
    # 优先检查删除标志
    if _flag_is_true(user.get("deleted")) or _flag_is_true(user.get("is_deleted")) or _flag_is_true(user.get("isDeleted")):
        return "deleted"
    # 其次检查禁用标志
    if _flag_is_true(user.get("disabled")) or _flag_is_true(user.get("is_disabled")) or _flag_is_true(user.get("isDisabled")):
        return "disabled"
    # 检查状态字段
    status_value = user.get("status") or user.get("user_status")
    # 如果没有状态字段但有标志字段，说明是正常用户
    if status_value in (None, "") and (
        _has_flag(user, "deleted", "is_deleted", "isDeleted") or _has_flag(user, "disabled", "is_disabled", "isDisabled")
    ):
        return "active"
    return _normalize_status(status_value)


def _raw_user_status(user: dict[str, Any]) -> str:
    """
    获取用户原始状态值（不做转换）

    用于保存原始状态，便于后续排查问题。
    与 _normalize_user_status 的区别是：
    - 这个函数保留原始值（如 "Normal"、"ENABLED"）
    - 那个函数转换为标准值（如 "active"）

    参数：
        user: 用户数据字典

    返回：
        原始状态字符串
    """
    status_value = user.get("status") or user.get("user_status")
    if status_value not in (None, ""):
        return str(status_value)
    # 没有状态字段时，根据标志推断
    if _flag_is_true(user.get("deleted")) or _flag_is_true(user.get("is_deleted")) or _flag_is_true(user.get("isDeleted")):
        return "deleted"
    if _flag_is_true(user.get("disabled")) or _flag_is_true(user.get("is_disabled")) or _flag_is_true(user.get("isDisabled")):
        return "disabled"
    if _has_flag(user, "deleted", "is_deleted", "isDeleted") or _has_flag(user, "disabled", "is_disabled", "isDisabled"):
        return "active"
    return "active"


def _mark_knowledge_bases_pending_transfer(cur, tenant_id: str, user_id: str, reason: str) -> None:
    """
    标记用户的知识库待转移

    当用户被删除或禁用时，其创建的知识库需要转移给其他管理员。
    这个函数将知识库的 owner_status 设置为 'pending_transfer'，
    后续由管理员手动处理转移。

    为什么需要转移？
    - 删除用户后，其知识库可能还需要继续使用
    - 禁用用户后，需要其他管理员接手管理

    参数：
        cur: 数据库游标
        tenant_id: 租户ID（空字符串表示不限租户）
        user_id: 用户ID
        reason: 原因（"deleted" 或 "disabled"）
    """
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    normalized_reason = str(reason or "").strip().lower()
    # 只接受 deleted 或 disabled 两种原因
    if normalized_reason not in {"deleted", "disabled"}:
        normalized_reason = "disabled"
    if not user:
        return
    # 更新该用户创建的所有知识库，标记为待转移
    cur.execute(
        """
        UPDATE knowledge_bases
        SET owner_status = 'pending_transfer',
            owner_invalid_reason = %s
        WHERE deleted_at IS NULL
          AND status <> 'deleted'
          AND (%s = '' OR tenant_id = %s)
          AND (
              owner_user_id = %s
              OR (owner_user_id IS NULL AND created_by = %s)
          )
        """,
        (normalized_reason, tenant, tenant, user, user),
    )


def _source_timestamp(item: dict[str, Any]) -> Any:
    """
    从数据字典中提取更新时间戳

    不同系统可能用不同的字段名表示更新时间。
    这个函数按优先级依次尝试多个可能的字段名。

    参数：
        item: 数据字典

    返回：
        时间戳值（可能是datetime对象或字符串）
    """
    return (
        item.get("updated_at")
        or item.get("changed_at")
        or item.get("source_updated_at")
        or item.get("updateTime")
        or item.get("updatedTime")
        or item.get("updatedAt")
        or item.get("changedAt")
        or item.get("sourceUpdatedAt")
    )


def _as_dict_list(payload: Any) -> list[dict[str, Any]]:
    """
    将任意数据转换为字典列表

    用于处理可能不是列表格式的数据，确保返回值一定是列表。

    参数：
        payload: 任意类型的数据

    返回：
        字典列表（如果不是列表或元素不是字典，返回空列表）
    """
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    return []


def _first_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """
    从字典中获取第一个非空列表

    外部系统可能用不同的字段名存储列表数据（如 "users" 或 "userList"）。
    这个函数按顺序尝试多个字段名，返回第一个非空列表。

    参数：
        payload: 外部数据字典
        *keys: 可能的字段名列表

    返回：
        找到的第一个非空列表，或空列表
    """
    for key in keys:
        items = _as_dict_list(payload.get(key))
        if items:
            return items
    return []


def _normalize_identity_delta_record(item: dict[str, Any], mapping: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    """
    标准化身份数据记录的字段名

    外部系统的字段名可能与我们内部不一致（如 "userId" vs "user_id"）。
    这个函数根据映射关系，把外部字段名转换为内部标准字段名。

    参数：
        item: 原始数据字典
        mapping: 字段映射 {标准字段名: (可能的外部字段名, ...)}

    返回：
        标准化后的数据字典

    示例：
        mapping = {"user_id": ("userId", "id")}
        如果 item 有 "userId" 但没有 "user_id"，会添加 "user_id": item["userId"]
    """
    normalized = dict(item)
    for canonical_key, aliases in mapping.items():
        # 如果已经有标准字段名，跳过
        if normalized.get(canonical_key) not in (None, ""):
            continue
        # 尝试从别名中获取值
        for alias in aliases:
            if normalized.get(alias) not in (None, ""):
                normalized[canonical_key] = normalized[alias]
                break
    return normalized


def _normalize_deleted_events(raw_deleted: Any) -> list[dict[str, Any]]:
    """
    标准化删除事件列表

    外部系统可能以不同格式传递删除事件：
    - 列表格式：[{"entity_type": "user", "entity_id": "123"}, ...]
    - 分组格式：{"user_ids": ["123", "456"], "tenant_ids": ["789"]}

    这个函数统一转换为标准的事件列表格式。

    参数：
        raw_deleted: 原始删除数据

    返回：
        标准化后的删除事件列表，每个事件包含：
        - entity_type: 实体类型（tenant/user/role/user_role）
        - entity_id: 实体ID
    """
    if not raw_deleted:
        return []
    # 如果已经是列表格式，直接转换
    if isinstance(raw_deleted, list):
        return [dict(item) for item in raw_deleted if isinstance(item, dict)]
    if not isinstance(raw_deleted, dict):
        return []

    # 分组格式转换为事件列表
    events: list[dict[str, Any]] = []
    # 字段名到实体类型的映射
    grouped_keys = {
        "tenant_ids": "tenant",
        "tenantIds": "tenant",
        "user_ids": "user",
        "userIds": "user",
        "role_ids": "role",
        "roleIds": "role",
        "user_role_relation_ids": "user_role",
        "userRoleRelationIds": "user_role",
        "userRoleIds": "user_role",
    }
    for key, entity_type in grouped_keys.items():
        for entity_id in raw_deleted.get(key) or []:
            events.append({"entity_type": entity_type, "entity_id": entity_id})
    return events


def _apply_deleted_events(cur, deleted_events: list[dict[str, Any]]) -> None:
    """
    应用删除事件到数据库

    根据删除事件更新对应实体的状态为 'deleted'。
    这是软删除，只更新状态，不真正删除记录。

    参数：
        cur: 数据库游标
        deleted_events: 删除事件列表

    处理的实体类型：
        - tenant: 更新租户状态
        - user: 更新用户状态，并标记其知识库待转移
        - role: 更新角色状态
        - user_role: 更新用户-角色关系状态
    """
    for event in deleted_events:
        # 提取事件信息，支持多种字段名
        entity_type = str(event.get("entity_type") or event.get("entityType") or event.get("type") or "").strip()
        entity_id = str(event.get("entity_id") or event.get("entityId") or event.get("id") or "").strip()
        tenant_id = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
        user_id = str(event.get("user_id") or event.get("userId") or "").strip()
        role_id = str(event.get("role_id") or event.get("roleId") or "").strip()

        # 根据实体类型执行不同的更新
        if entity_type == "tenant" and entity_id:
            # 删除租户
            cur.execute(
                """
                UPDATE kb_identity_tenants
                SET tenant_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE tenant_id = %s
                """,
                (_source_timestamp(event), entity_id),
            )
        elif entity_type == "user" and entity_id:
            # 删除用户
            cur.execute(
                """
                UPDATE kb_identity_users
                SET user_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE user_id = %s
                  AND (%s = '' OR tenant_id = %s)
                """,
                (_source_timestamp(event), entity_id, tenant_id, tenant_id),
            )
        elif entity_type == "role" and entity_id:
            # 删除角色
            cur.execute(
                """
                UPDATE kb_identity_roles
                SET role_status = 'deleted',
                    raw_status = 'deleted',
                    source_updated_at = COALESCE(%s, source_updated_at),
                    synced_at = NOW()
                WHERE role_id = %s
                  AND (%s = '' OR tenant_id = %s OR tenant_id IS NULL)
                """,
                (_source_timestamp(event), entity_id, tenant_id, tenant_id),
            )
        elif entity_type == "user_role":
            # 删除用户-角色关系
            if tenant_id and user_id and role_id:
                # 有完整信息，直接按主键更新
                cur.execute(
                    """
                    UPDATE kb_identity_user_roles
                    SET relation_status = 'deleted',
                        source_updated_at = COALESCE(%s, source_updated_at),
                        synced_at = NOW()
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND role_id = %s
                    """,
                    (_source_timestamp(event), tenant_id, user_id, role_id),
                )
            elif entity_id:
                # 只有关系ID，按源关系ID更新
                cur.execute(
                    """
                    UPDATE kb_identity_user_roles
                    SET relation_status = 'deleted',
                        source_updated_at = COALESCE(%s, source_updated_at),
                        synced_at = NOW()
                    WHERE source_relation_id = %s
                    """,
                    (_source_timestamp(event), entity_id),
                )


# ============ 身份同步函数 ============

def _extract_identity_summary(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    从SSO身份摘要中提取租户、用户、角色信息

    SSO登录成功后会返回一个身份摘要，包含当前用户的所有信息。
    这个函数将其拆分为四个部分。

    参数：
        summary: SSO返回的身份摘要，格式如下：
            {
                "tenant": {"tenant_id": "...", "tenant_name": "...", ...},
                "user": {"user_id": "...", "username": "...", ...},
                "roles": [{"role_id": "...", "role_code": "..."}, ...],
                "user_roles": [{"role_id": "..."}, ...]
            }

    返回：
        元组：(tenant, user, roles, user_roles)

    异常：
        ValueError: 如果缺少必需的 tenant_id 或 user_id
    """
    tenant = dict(summary.get("tenant") or {})
    user = dict(summary.get("user") or {})
    roles = list(summary.get("roles") or [])
    user_roles = list(summary.get("user_roles") or [])
    # 必须有租户ID和用户ID
    if not tenant.get("tenant_id") or not user.get("user_id"):
        raise ValueError("SSO identity summary requires tenant.tenant_id and user.user_id")
    # 确保用户有租户ID
    user.setdefault("tenant_id", tenant.get("tenant_id"))
    return tenant, user, roles, user_roles


def upsert_identity_snapshot_from_summary(summary: dict[str, Any]) -> IdentityContext:
    """
    从SSO身份摘要创建或更新身份快照，并返回身份上下文

    这个函数在用户SSO登录成功后调用：
    1. 保存/更新租户信息到 kb_identity_tenants 表
    2. 保存/更新用户信息到 kb_identity_users 表
    3. 保存/更新角色信息到 kb_identity_roles 表
    4. 保存/更新用户-角色关系到 kb_identity_user_roles 表
    5. 如果用户被删除/禁用，标记其知识库待转移
    6. 返回完整的身份上下文

    参数：
        summary: SSO返回的身份摘要

    返回：
        IdentityContext: 当前用户的身份上下文

    异常：
        ValueError: 如果身份摘要无效或无法解析
    """
    tenant, user, roles, user_roles = _extract_identity_summary(summary)
    tenant_id = str(tenant["tenant_id"])
    user_id = str(user["user_id"])
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. 插入或更新租户信息
            cur.execute(
                """
                INSERT INTO kb_identity_tenants (
                    tenant_id, tenant_name, tenant_code, tenant_status, raw_status,
                    contact_name, contact_mobile_masked, source_updated_at, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL, NULL, NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                    tenant_name = EXCLUDED.tenant_name,
                    tenant_code = EXCLUDED.tenant_code,
                    tenant_status = EXCLUDED.tenant_status,
                    raw_status = EXCLUDED.raw_status,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    tenant_id,
                    str(tenant.get("tenant_name") or tenant_id),
                    tenant.get("tenant_code"),
                    _normalize_status(tenant.get("status") or tenant.get("tenant_status")),
                    str(tenant.get("status") or tenant.get("tenant_status") or "active"),
                ),
            )
            # 2. 插入或更新用户信息
            cur.execute(
                """
                INSERT INTO kb_identity_users (
                    user_id, tenant_id, username, display_name, mobile_masked, email_masked,
                    user_status, raw_status, source_updated_at, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    username = EXCLUDED.username,
                    display_name = EXCLUDED.display_name,
                    mobile_masked = EXCLUDED.mobile_masked,
                    email_masked = EXCLUDED.email_masked,
                    user_status = EXCLUDED.user_status,
                    raw_status = EXCLUDED.raw_status,
                    source_updated_at = EXCLUDED.source_updated_at,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    tenant_id,
                    str(user.get("username") or user_id),
                    user.get("display_name") or user.get("displayName") or user.get("username") or user_id,
                    user.get("mobile_masked") or user.get("mobileMasked"),
                    user.get("email_masked") or user.get("emailMasked"),
                    _normalize_user_status(user),
                    _raw_user_status(user),
                ),
            )
            # 3. 如果用户被删除/禁用，标记其知识库待转移
            user_status = _normalize_user_status(user)
            if user_status in {"deleted", "disabled"}:
                _mark_knowledge_bases_pending_transfer(cur, tenant_id, user_id, user_status)

            # 4. 插入或更新角色信息
            active_role_ids: set[str] = set()
            for role in roles:
                role_id = str(role.get("role_id") or role.get("id") or role.get("role_code") or "")
                role_code = str(role.get("role_code") or role.get("code") or "")
                if not role_id or not role_code:
                    continue
                active_role_ids.add(role_id)
                cur.execute(
                    """
                    INSERT INTO kb_identity_roles (
                        role_id, tenant_id, role_code, role_name, role_status, raw_status,
                        source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (role_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        role_code = EXCLUDED.role_code,
                        role_name = EXCLUDED.role_name,
                        role_status = EXCLUDED.role_status,
                        raw_status = EXCLUDED.raw_status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        role_id,
                        role.get("tenant_id") or tenant_id,
                        role_code,
                        str(role.get("role_name") or role.get("name") or role_code),
                        _normalize_status(role.get("status") or role.get("role_status")),
                        str(role.get("status") or role.get("role_status") or "active"),
                    ),
                )

            # 5. 插入或更新用户-角色关系
            if user_roles:
                # 如果有明确的用户-角色关系列表
                for relation in user_roles:
                    rel_user_id = str(relation.get("user_id") or user_id)
                    role_id = str(relation.get("role_id") or "")
                    rel_tenant_id = str(relation.get("tenant_id") or tenant_id)
                    if rel_user_id != user_id or rel_tenant_id != tenant_id or not role_id:
                        continue
                    active_role_ids.add(role_id)
                    cur.execute(
                        """
                        INSERT INTO kb_identity_user_roles (
                            tenant_id, user_id, role_id, relation_status,
                            source_relation_id, source_updated_at, synced_at
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                            relation_status = EXCLUDED.relation_status,
                            source_relation_id = EXCLUDED.source_relation_id,
                            source_updated_at = EXCLUDED.source_updated_at,
                            synced_at = NOW()
                        """,
                        (
                            tenant_id,
                            user_id,
                            role_id,
                            _normalize_status(relation.get("status") or relation.get("relation_status")),
                            relation.get("source_relation_id") or relation.get("id"),
                        ),
                    )
            else:
                # 如果没有明确的用户-角色关系，假设用户拥有所有角色
                for role_id in active_role_ids:
                    cur.execute(
                        """
                        INSERT INTO kb_identity_user_roles (
                            tenant_id, user_id, role_id, relation_status,
                            source_relation_id, source_updated_at, synced_at
                        )
                        VALUES (%s, %s, %s, 'active', NULL, NOW(), NOW())
                        ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                            relation_status = 'active',
                            source_updated_at = EXCLUDED.source_updated_at,
                            synced_at = NOW()
                        """,
                        (tenant_id, user_id, role_id),
                    )
            conn.commit()
    finally:
        conn.close()

    # 6. 返回身份上下文
    identity = resolve_identity_snapshot(tenant_id, user_id)
    if identity is None:
        raise ValueError("SSO identity summary did not resolve to an active snapshot")
    return identity


def upsert_identity_delta_snapshot(delta: dict[str, Any]) -> dict[str, int]:
    """
    批量同步身份数据增量更新

    与 upsert_identity_snapshot_from_summary 不同，这个函数处理批量增量同步：
    - 从外部系统获取变更数据（新增/修改/删除的用户、租户、角色）
    - 批量更新到本地数据库

    使用场景：
        - 定时任务同步外部身份系统的变更
        - 接收外部系统的Webhook推送

    参数：
        delta: 增量数据，格式如下：
            {
                "tenants": [{"tenant_id": "...", ...}, ...],
                "users": [{"user_id": "...", ...}, ...],
                "roles": [{"role_id": "...", ...}, ...],
                "user_roles": [{"user_id": "...", "role_id": "..."}, ...],
                "deleted": [{"entity_type": "user", "entity_id": "..."}, ...]
            }

    返回：
        各类实体的处理数量统计：
        {
            "tenants": 处理的租户数,
            "users": 处理的用户数,
            "roles": 处理的角色数,
            "user_roles": 处理的关系数,
            "deleted": 处理的删除事件数
        }
    """
    # 1. 标准化各类数据，转换字段名
    tenants = [
        _normalize_identity_delta_record(
            item,
            {
                "tenant_id": ("tenantId", "id"),
                "tenant_name": ("tenantName", "name"),
                "tenant_code": ("tenantCode", "code"),
                "tenant_status": ("tenantStatus",),
                "contact_name": ("contactName",),
                "contact_mobile_masked": ("contactMobileMasked",),
            },
        )
        for item in _first_list(delta, "tenants", "tenantList", "tenant_list")
    ]
    users = [
        _normalize_identity_delta_record(
            item,
            {
                "user_id": ("userId", "id"),
                "tenant_id": ("tenantId",),
                "user_name": ("userName",),
                "display_name": ("displayName", "nickName"),
                "mobile_masked": ("mobileMasked",),
                "email_masked": ("emailMasked",),
                "user_status": ("userStatus",),
                "is_deleted": ("isDeleted",),
                "is_disabled": ("isDisabled",),
            },
        )
        for item in _first_list(delta, "users", "userList", "user_list")
    ]
    roles = [
        _normalize_identity_delta_record(
            item,
            {
                "role_id": ("roleId", "id"),
                "tenant_id": ("tenantId",),
                "role_code": ("roleCode", "code"),
                "role_name": ("roleName", "name"),
                "role_status": ("roleStatus",),
            },
        )
        for item in _first_list(delta, "roles", "roleList", "role_list")
    ]
    user_roles = [
        _normalize_identity_delta_record(
            item,
            {
                "relation_id": ("relationId", "id"),
                "tenant_id": ("tenantId",),
                "user_id": ("userId",),
                "role_id": ("roleId",),
                "relation_status": ("relationStatus",),
                "source_relation_id": ("sourceRelationId",),
            },
        )
        for item in _first_list(delta, "user_roles", "userRoles", "userRoleList", "user_role_list")
    ]
    deleted = _normalize_deleted_events(delta.get("deleted") or delta.get("deletedList") or delta.get("deleted_list"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 2. 同步租户数据
            for tenant in tenants:
                tenant_id = str(tenant.get("tenant_id") or "")
                if not tenant_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO kb_identity_tenants (
                        tenant_id, tenant_name, tenant_code, tenant_status, raw_status,
                        contact_name, contact_mobile_masked, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        tenant_name = EXCLUDED.tenant_name,
                        tenant_code = EXCLUDED.tenant_code,
                        tenant_status = EXCLUDED.tenant_status,
                        raw_status = EXCLUDED.raw_status,
                        contact_name = EXCLUDED.contact_name,
                        contact_mobile_masked = EXCLUDED.contact_mobile_masked,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        tenant_id,
                        str(tenant.get("tenant_name") or tenant.get("name") or tenant_id),
                        tenant.get("tenant_code") or tenant.get("code"),
                        _normalize_status(tenant.get("status") or tenant.get("tenant_status")),
                        str(tenant.get("status") or tenant.get("tenant_status") or "active"),
                        tenant.get("contact_name"),
                        tenant.get("contact_mobile_masked"),
                        _source_timestamp(tenant),
                    ),
                )

            # 3. 同步用户数据
            for user in users:
                user_id = str(user.get("user_id") or "")
                tenant_id = str(user.get("tenant_id") or "")
                if not user_id or not tenant_id:
                    continue
                cur.execute(
                    """
                    INSERT INTO kb_identity_users (
                        user_id, tenant_id, username, display_name, mobile_masked, email_masked,
                        user_status, raw_status, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        username = EXCLUDED.username,
                        display_name = EXCLUDED.display_name,
                        mobile_masked = EXCLUDED.mobile_masked,
                        email_masked = EXCLUDED.email_masked,
                        user_status = EXCLUDED.user_status,
                        raw_status = EXCLUDED.raw_status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        user_id,
                        tenant_id,
                        str(user.get("username") or user.get("user_name") or user_id),
                        user.get("display_name") or user.get("displayName") or user.get("nick_name") or user.get("username") or user_id,
                        user.get("mobile_masked") or user.get("mobileMasked"),
                        user.get("email_masked") or user.get("emailMasked"),
                        _normalize_user_status(user),
                        _raw_user_status(user),
                        _source_timestamp(user),
                    ),
                )
                # 如果用户被删除/禁用，标记其知识库待转移
                user_status = _normalize_user_status(user)
                if user_status in {"deleted", "disabled"}:
                    _mark_knowledge_bases_pending_transfer(cur, tenant_id, user_id, user_status)

            # 4. 同步角色数据
            for role in roles:
                role_id = str(role.get("role_id") or role.get("id") or "")
                role_code = str(role.get("role_code") or role.get("code") or "")
                if not role_id or not role_code:
                    continue
                tenant_id = role.get("tenant_id")
                cur.execute(
                    """
                    INSERT INTO kb_identity_roles (
                        role_id, tenant_id, role_code, role_name, role_status, raw_status,
                        source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (role_id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        role_code = EXCLUDED.role_code,
                        role_name = EXCLUDED.role_name,
                        role_status = EXCLUDED.role_status,
                        raw_status = EXCLUDED.raw_status,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        role_id,
                        str(tenant_id) if tenant_id is not None else None,
                        role_code,
                        str(role.get("role_name") or role.get("name") or role_code),
                        _normalize_status(role.get("status") or role.get("role_status")),
                        str(role.get("status") or role.get("role_status") or "active"),
                        _source_timestamp(role),
                    ),
                )

            # 5. 同步用户-角色关系
            for relation in user_roles:
                tenant_id = str(relation.get("tenant_id") or "")
                user_id = str(relation.get("user_id") or "")
                role_id = str(relation.get("role_id") or "")
                if not tenant_id or not user_id or not role_id:
                    continue
                status = _normalize_status(relation.get("status") or relation.get("relation_status"))
                cur.execute(
                    """
                    INSERT INTO kb_identity_user_roles (
                        tenant_id, user_id, role_id, relation_status,
                        source_relation_id, source_updated_at, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tenant_id, user_id, role_id) DO UPDATE SET
                        relation_status = EXCLUDED.relation_status,
                        source_relation_id = EXCLUDED.source_relation_id,
                        source_updated_at = EXCLUDED.source_updated_at,
                        synced_at = NOW()
                    """,
                    (
                        tenant_id,
                        user_id,
                        role_id,
                        status,
                        relation.get("source_relation_id") or relation.get("relation_id") or relation.get("id"),
                        _source_timestamp(relation),
                    ),
                )

            # 6. 应用删除事件
            _apply_deleted_events(cur, deleted)
            for event in deleted:
                entity_type = str(event.get("entity_type") or event.get("entityType") or event.get("type") or "").strip()
                entity_id = str(event.get("entity_id") or event.get("entityId") or event.get("id") or "").strip()
                tenant_id = str(event.get("tenant_id") or event.get("tenantId") or "").strip()
                if entity_type == "user" and entity_id:
                    _mark_knowledge_bases_pending_transfer(cur, tenant_id, entity_id, "deleted")
        conn.commit()
    finally:
        conn.close()

    return {
        "tenants": len(tenants),
        "users": len(users),
        "roles": len(roles),
        "user_roles": len(user_roles),
        "deleted": len(deleted),
    }


# ============ 会话管理函数 ============

def _identity_from_session_row(row) -> IdentityContext:
    """
    从数据库会话记录构建身份上下文

    会话表中存储了用户的身份信息，这个函数将其转换为 IdentityContext 对象。

    参数：
        row: 数据库查询结果的一行，包含：
            [0] tenant_id, [1] user_id, [2] username, [3] display_name,
            [4] tenant_name, [5] is_tenant_admin, [6] role_codes, [7] auth_source

    返回：
        IdentityContext: 用户的身份上下文
    """
    role_codes = tuple(str(item) for item in (row[6] or []))
    return IdentityContext(
        tenant_id=str(row[0]),
        user_id=str(row[1]),
        username=row[2] or "",
        display_name=row[3] or "",
        tenant_name=row[4] or "",
        is_tenant_admin=bool(row[5]),
        role_codes=role_codes,
        is_authenticated=True,
        source=str(row[7] or "kb_session"),
    )


def create_auth_session(
    identity: IdentityContext,
    *,
    auth_source: str,
    credential_fingerprint: str | None = None,
    identity_snapshot_version: str | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> dict[str, Any]:
    """
    创建登录会话

    用户登录成功后，创建一个会话记录，后续请求可以通过会话Token验证身份。
    会话信息存储在 kb_auth_sessions 表中。

    参数：
        identity: 用户身份上下文
        auth_source: 认证来源（如 "sso"、"password"）
        credential_fingerprint: 凭证指纹（用于防重放攻击）
        identity_snapshot_version: 身份快照版本（用于追踪身份变更）
        ttl_seconds: 会话有效期（秒），默认4小时

    返回：
        会话信息字典：
        {
            "sessionToken": 会话Token（返回给前端）,
            "expiresAt": 过期时间,
            "identity": 用户身份信息
        }

    异常：
        ValueError: 如果传入匿名身份（没有用户ID和租户ID）

    工作流程：
        1. 生成随机会话Token（32字节URL安全字符串）
        2. 对Token进行哈希（数据库只存哈希值，不存明文）
        3. 计算过期时间
        4. 插入会话记录到数据库
        5. 返回会话Token和相关信息给前端
    """
    if not identity.enforce_access:
        raise ValueError("Cannot create a session for anonymous identity")

    # 生成随机Token
    session_token = secrets.token_urlsafe(32)
    # 哈希后存储（安全考虑：即使数据库泄露，也无法还原原始Token）
    session_hash = hash_secret(session_token)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=max(60, int(ttl_seconds)))
    # 确保管理员角色在角色列表中
    role_codes = list(identity.role_codes)
    if identity.is_tenant_admin and TENANT_ADMIN_ROLE_CODE not in role_codes:
        role_codes.append(TENANT_ADMIN_ROLE_CODE)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_auth_sessions (
                    session_hash, tenant_id, user_id, username, display_name, tenant_name,
                    role_codes, is_tenant_admin, auth_source, credential_fingerprint,
                    identity_snapshot_version, issued_at, expires_at, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_hash,
                    identity.tenant_id,
                    identity.user_id,
                    identity.username,
                    identity.display_name,
                    identity.tenant_name,
                    json.dumps(role_codes, ensure_ascii=False),
                    identity.is_tenant_admin,
                    auth_source,
                    credential_fingerprint,
                    identity_snapshot_version,
                    issued_at,
                    expires_at,
                    issued_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "sessionToken": session_token,
        "expiresAt": expires_at.isoformat(),
        "identity": identity_to_payload(identity),
    }


def resolve_auth_session(session_token: str) -> IdentityContext | None:
    """
    验证会话Token并返回身份上下文

    每次API请求都会调用这个函数：
    1. 检查会话Token是否有效（未过期、未撤销）
    2. 返回用户的身份信息
    3. 更新最后访问时间（用于会话活跃度追踪）

    参数：
        session_token: 会话Token（从前端Cookie或Header获取）

    返回：
        IdentityContext: 如果会话有效，返回用户身份上下文
        None: 如果会话无效或不存在

    注意事项：
        - Token哈希后查询，不直接比较明文
        - 只返回未过期且未撤销的会话
        - 更新last_seen_at用于追踪会话活跃度
    """
    token = (session_token or "").strip()
    if not token:
        return None
    session_hash = hash_secret(token)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 查询有效会话（未过期、未撤销）
            cur.execute(
                """
                SELECT tenant_id, user_id, username, display_name, tenant_name,
                       is_tenant_admin, role_codes, auth_source
                FROM kb_auth_sessions
                WHERE session_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                LIMIT 1
                """,
                (session_hash,),
            )
            row = cur.fetchone()
            if row:
                # 更新最后访问时间
                cur.execute(
                    "UPDATE kb_auth_sessions SET last_seen_at = NOW() WHERE session_hash = %s",
                    (session_hash,),
                )
                conn.commit()
    finally:
        conn.close()
    if not row:
        return None
    return _identity_from_session_row(row)


def revoke_auth_session(session_token: str) -> bool:
    """
    撤销（注销）单个会话

    用户主动注销登录时调用，将指定会话标记为已撤销。

    参数：
        session_token: 要撤销的会话Token

    返回：
        True: 成功撤销了会话
        False: 会话不存在或已经被撤销

    注意：
        这是软删除（设置 revoked_at 时间），不物理删除记录，
        便于后续审计和安全分析。
    """
    token = (session_token or "").strip()
    if not token:
        return False
    session_hash = hash_secret(token)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_auth_sessions
                SET revoked_at = NOW()
                WHERE session_hash = %s
                  AND revoked_at IS NULL
                """,
                (session_hash,),
            )
            revoked = cur.rowcount > 0
        conn.commit()
        return revoked
    finally:
        conn.close()


def revoke_auth_sessions_for_identity(tenant_id: str, user_id: str | None = None) -> int:
    """
    撤销某个用户或租户的所有会话

    使用场景：
        - 用户被删除/禁用时，强制其立即下线
        - 租户被停用时，强制所有用户下线
        - 管理员强制踢出某个用户

    参数：
        tenant_id: 租户ID
        user_id: 用户ID（可选，不传则撤销整个租户的所有会话）

    返回：
        撤销的会话数量
    """
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    if not tenant:
        return 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if user:
                # 撤销指定用户的所有会话
                cur.execute(
                    """
                    UPDATE kb_auth_sessions
                    SET revoked_at = NOW()
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    """,
                    (tenant, user),
                )
            else:
                # 撤销整个租户的所有会话
                cur.execute(
                    """
                    UPDATE kb_auth_sessions
                    SET revoked_at = NOW()
                    WHERE tenant_id = %s
                      AND revoked_at IS NULL
                    """,
                    (tenant,),
                )
            revoked = int(cur.rowcount or 0)
        conn.commit()
        return revoked
    finally:
        conn.close()


# ============ 身份快照查询函数 ============

def latest_identity_snapshot_synced_at(tenant_id: str, user_id: str) -> datetime | None:
    """
    获取用户身份快照的最后同步时间

    用于判断是否需要重新同步用户身份信息。
    如果外部系统的数据更新时间晚于本地同步时间，则需要重新同步。

    参数：
        tenant_id: 租户ID
        user_id: 用户ID

    返回：
        最后同步时间（UTC），如果用户不存在则返回 None

    说明：
        同步时间取用户、租户、角色、关系中最大的 synced_at 时间。
        因为任何一项更新都可能导致身份信息变化。
    """
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    if not tenant or not user:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 查询用户、租户、角色、关系中最新的同步时间
            cur.execute(
                """
                SELECT MAX(
                           GREATEST(
                               COALESCE(u.synced_at, 'epoch'::timestamptz),
                               COALESCE(t.synced_at, 'epoch'::timestamptz),
                               COALESCE(r.synced_at, 'epoch'::timestamptz),
                               COALESCE(ur.synced_at, 'epoch'::timestamptz)
                           )
                       ) AS synced_at
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                LEFT JOIN kb_identity_user_roles ur
                  ON ur.tenant_id = u.tenant_id
                 AND ur.user_id = u.user_id
                 AND ur.relation_status = 'active'
                LEFT JOIN kb_identity_roles r
                  ON r.role_id = ur.role_id
                 AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                 AND r.role_status = 'active'
                WHERE u.tenant_id = %s
                  AND u.user_id = %s
                  AND u.user_status = 'active'
                  AND t.tenant_status = 'active'
                """,
                (tenant, user),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return _as_utc(row[0])


def get_latest_identity_sync_watermark() -> str | None:
    """
    获取最近一次成功同步的水位标记

    增量同步时，需要知道上次同步到哪里了，从那个位置继续同步。
    水位标记通常是最后一条记录的更新时间。

    返回：
        最近一次成功同步的 max_updated_at，如果没有记录则返回 None

    使用场景：
        定时任务调用增量同步API时：
        1. 先获取水位标记
        2. 请求外部系统时带上这个标记，只获取变更的数据
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max_updated_at
                FROM kb_identity_sync_runs
                WHERE sync_mode = 'http_delta'
                  AND status = 'success'
                  AND COALESCE(max_updated_at, '') <> ''
                ORDER BY finished_at DESC NULLS LAST, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def list_identity_sync_runs(limit: int = 50) -> list[dict[str, Any]]:
    """
    获取身份同步历史记录列表

    用于查看同步任务的历史执行情况，包括成功/失败、同步数量等。

    参数：
        limit: 最大返回数量（1-200，默认50）

    返回：
        同步记录列表，每条记录包含：
        - id: 记录ID
        - syncMode: 同步模式（如 "http_delta"）
        - sourceHost: 数据源地址
        - tenantsCount: 同步的租户数
        - usersCount: 同步的用户数
        - status: 状态（"success"/"failed"）
        - startedAt/finishedAt: 开始/结束时间
        等字段
    """
    capped_limit = max(1, min(int(limit or 50), 200))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       sync_mode,
                       source_host,
                       source_schema,
                       requested_limit,
                       tenants_count,
                       users_count,
                       roles_count,
                       user_roles_count,
                       deleted_count,
                       last_sync_at,
                       max_updated_at,
                       snapshot_version,
                       has_more,
                       status,
                       error_message,
                       started_at,
                       finished_at
                FROM kb_identity_sync_runs
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, id DESC
                LIMIT %s
                """,
                (capped_limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": int(row[0]),
            "syncMode": str(row[1] or ""),
            "sourceHost": row[2] or "",
            "sourceSchema": row[3] or "",
            "requestedLimit": int(row[4] or 0),
            "tenantsCount": int(row[5] or 0),
            "usersCount": int(row[6] or 0),
            "rolesCount": int(row[7] or 0),
            "userRolesCount": int(row[8] or 0),
            "deletedCount": int(row[9] or 0),
            "lastSyncAt": row[10] or "",
            "maxUpdatedAt": row[11] or "",
            "snapshotVersion": row[12] or "",
            "hasMore": bool(row[13]),
            "status": str(row[14] or ""),
            "errorMessage": row[15] or "",
            "startedAt": row[16].isoformat() if hasattr(row[16], "isoformat") else str(row[16] or ""),
            "finishedAt": row[17].isoformat() if hasattr(row[17], "isoformat") else str(row[17] or ""),
        }
        for row in rows
    ]


def record_identity_sync_run(
    *,
    sync_mode: str,
    source_host: str,
    requested_limit: int,
    counts: dict[str, int],
    status: str,
    source_schema: str | None = None,
    error_message: str | None = None,
    last_sync_at: str | None = None,
    max_updated_at: str | None = None,
    snapshot_version: str | None = None,
    has_more: bool = False,
) -> None:
    """
    记录一次身份同步任务的执行结果

    每次同步任务完成后，需要调用这个函数记录结果，便于：
    - 追踪同步历史
    - 获取水位标记（用于增量同步）
    - 排查同步问题

    参数：
        sync_mode: 同步模式（如 "http_delta"）
        source_host: 数据源地址
        requested_limit: 请求的数据量限制
        counts: 各类实体的同步数量 {"tenants": N, "users": N, ...}
        status: 状态（"success" 或 "failed"）
        source_schema: 数据源模式（可选）
        error_message: 错误信息（失败时填写）
        last_sync_at: 上次同步时间
        max_updated_at: 本次同步的最大更新时间（水位标记）
        snapshot_version: 快照版本
        has_more: 是否还有更多数据未同步
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_identity_sync_runs(
                    sync_mode, source_host, source_schema, requested_limit,
                    tenants_count, users_count, roles_count, user_roles_count, deleted_count,
                    last_sync_at, max_updated_at, snapshot_version, has_more,
                    status, error_message, finished_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    sync_mode,
                    source_host,
                    source_schema,
                    int(requested_limit),
                    int(counts.get("tenants", 0)),
                    int(counts.get("users", 0)),
                    int(counts.get("roles", 0)),
                    int(counts.get("user_roles", 0)),
                    int(counts.get("deleted", 0)),
                    last_sync_at,
                    max_updated_at,
                    snapshot_version,
                    bool(has_more),
                    status,
                    error_message[:1000] if error_message else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ============ SSO 凭证管理 ============

def mark_sso_credential_used(
    credential_fingerprint: str,
    *,
    credential_type: str,
    tenant_id: str | None,
    user_id: str | None,
    expires_at: datetime | None = None,
) -> bool:
    """
    标记SSO凭证已使用

    防止一次性登录凭证被重复使用（防重放攻击）。
    当用户通过SSO登录成功后，将凭证指纹存入数据库。

    参数：
        credential_fingerprint: 凭证指纹（32位哈希）
        credential_type: 凭证类型（如 "sso_token"）
        tenant_id: 租户ID
        user_id: 用户ID
        expires_at: 凭证过期时间（可选）

    返回：
        True: 成功标记（首次使用）
        False: 凭证已存在（重复使用，应拒绝）

    使用场景：
        SSO登录流程：
        1. 获取SSO登录凭证（如一次性Token）
        2. 计算凭证指纹
        3. 检查是否已使用（is_sso_credential_used）
        4. 使用凭证登录
        5. 标记凭证已使用（本函数）
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_sso_used_credentials (
                    credential_fingerprint, credential_type, tenant_id, user_id, expires_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (credential_fingerprint) DO NOTHING
                """,
                (credential_fingerprint, credential_type, tenant_id, user_id, _as_utc(expires_at)),
            )
            inserted = cur.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def is_sso_credential_used(credential_fingerprint: str) -> bool:
    """
    检查SSO凭证是否已被使用

    在使用凭证之前调用，防止重复使用一次性凭证。

    参数：
        credential_fingerprint: 凭证指纹

    返回：
        True: 凭证已被使用（应拒绝登录）
        False: 凭证未使用（可以使用）
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM kb_sso_used_credentials
                WHERE credential_fingerprint = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                (credential_fingerprint,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


# ============ 辅助函数 ============

def identity_to_payload(identity: IdentityContext) -> dict[str, Any]:
    """
    将身份上下文转换为API响应格式

    后端返回给前端的用户信息需要特定的字段名（驼峰命名）。
    这个函数将 IdentityContext 转换为前端期望的格式。

    参数：
        identity: 用户身份上下文

    返回：
        前端格式的用户信息字典：
        {
            "tenantId": "租户ID",
            "userId": "用户ID",
            "username": "用户名",
            "displayName": "显示名称",
            "tenantName": "租户名称",
            "roleCodes": ["角色代码列表"],
            "isTenantAdmin": 是否是管理员,
            "source": "身份来源"
        }
    """
    role_codes = list(identity.role_codes)
    if identity.is_tenant_admin and TENANT_ADMIN_ROLE_CODE not in role_codes:
        role_codes.append(TENANT_ADMIN_ROLE_CODE)
    return {
        "tenantId": identity.tenant_id,
        "userId": identity.user_id,
        "username": identity.username,
        "displayName": identity.display_name or identity.username or identity.user_id,
        "tenantName": identity.tenant_name,
        "roleCodes": role_codes,
        "isTenantAdmin": identity.is_tenant_admin,
        "source": identity.source,
    }


def resolve_identity_snapshot(tenant_id: str, user_id: str) -> IdentityContext | None:
    """
    从身份快照表解析用户身份

    根据租户ID和用户ID，从本地数据库的身份快照表中查询用户信息。
    与 resolve_auth_session 不同，这个函数不验证会话，只查身份信息。

    使用场景：
        - 同步完成后验证身份信息
        - 后台任务中需要用户身份信息但无会话时
        - 服务端渲染页面时获取用户信息

    参数：
        tenant_id: 租户ID
        user_id: 用户ID

    返回：
        IdentityContext: 如果用户存在且状态正常，返回身份上下文
        None: 如果用户不存在、已删除或租户已删除
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 联表查询用户、租户、角色信息
            cur.execute(
                """
                SELECT u.user_id,
                       u.tenant_id,
                       u.username,
                       u.display_name,
                       t.tenant_name,
                       EXISTS (
                           SELECT 1
                           FROM kb_identity_user_roles ur
                           JOIN kb_identity_roles r
                             ON r.role_id = ur.role_id
                            AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                           WHERE ur.tenant_id = u.tenant_id
                             AND ur.user_id = u.user_id
                             AND ur.relation_status = 'active'
                             AND r.role_status = 'active'
                             AND r.role_code = %s
                       ) AS is_tenant_admin
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                WHERE u.tenant_id = %s
                  AND u.user_id = %s
                  AND u.user_status = 'active'
                  AND t.tenant_status = 'active'
                LIMIT 1
                """,
                (TENANT_ADMIN_ROLE_CODE, tenant_id, user_id),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    # 构建身份上下文
    role_codes = [TENANT_ADMIN_ROLE_CODE] if bool(row[5]) else []
    return IdentityContext(
        user_id=str(row[0]),
        tenant_id=str(row[1]),
        username=row[2] or "",
        display_name=row[3] or "",
        tenant_name=row[4] or "",
        is_tenant_admin=bool(row[5]),
        is_platform_admin=False,
        is_authenticated=True,
        source="identity_snapshot",
        role_codes=tuple(role_codes),
    )


def list_identity_snapshot_users(limit: int = 10) -> list[dict]:
    """
    获取身份快照中的用户列表

    用于管理后台查看已同步的用户列表，支持分页。

    参数：
        limit: 最大返回数量（默认10，最大100万）

    返回：
        用户列表，每个用户包含：
        - tenantId/userId: 租户和用户ID
        - username/displayName: 用户名和显示名
        - tenantName: 租户名称
        - roleCodes/roleNames: 角色代码和名称列表
        - ragRole: RAG系统角色（"租户管理员"或"普通用户"）
        - isTenantAdmin: 是否是管理员
        - syncedAt: 同步时间
    """
    capped_limit = max(1, min(int(limit), 1_000_000))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 查询用户、租户、角色信息，聚合角色列表
            cur.execute(
                """
                SELECT u.tenant_id,
                       u.user_id,
                       u.username,
                       u.display_name,
                       t.tenant_name,
                       COALESCE(
                           ARRAY_AGG(DISTINCT r.role_code)
                             FILTER (
                               WHERE r.role_code IS NOT NULL
                                 AND r.role_status = 'active'
                                 AND ur.relation_status = 'active'
                             ),
                           ARRAY[]::text[]
                       ) AS role_codes,
                       COALESCE(
                           ARRAY_AGG(DISTINCT r.role_name)
                             FILTER (
                               WHERE r.role_name IS NOT NULL
                                 AND r.role_status = 'active'
                                 AND ur.relation_status = 'active'
                             ),
                           ARRAY[]::text[]
                       ) AS role_names,
                       COALESCE(
                           BOOL_OR(
                               r.role_code = %s
                               AND r.role_status = 'active'
                               AND ur.relation_status = 'active'
                           ),
                           FALSE
                       ) AS is_tenant_admin,
                       MAX(
                           GREATEST(
                               COALESCE(u.synced_at, 'epoch'::timestamptz),
                               COALESCE(t.synced_at, 'epoch'::timestamptz),
                               COALESCE(r.synced_at, 'epoch'::timestamptz),
                               COALESCE(ur.synced_at, 'epoch'::timestamptz)
                           )
                       ) AS synced_at
                FROM kb_identity_users u
                JOIN kb_identity_tenants t
                  ON t.tenant_id = u.tenant_id
                LEFT JOIN kb_identity_user_roles ur
                  ON ur.tenant_id = u.tenant_id
                 AND ur.user_id = u.user_id
                LEFT JOIN kb_identity_roles r
                  ON r.role_id = ur.role_id
                 AND (r.tenant_id = ur.tenant_id OR r.tenant_id IS NULL)
                WHERE u.user_status = 'active'
                  AND t.tenant_status = 'active'
                GROUP BY u.tenant_id, u.user_id, u.username, u.display_name, t.tenant_name
                ORDER BY u.tenant_id, u.user_id
                LIMIT %s
                """,
                (TENANT_ADMIN_ROLE_CODE, capped_limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "tenantId": str(row[0]),
            "userId": str(row[1]),
            "username": row[2] or "",
            "displayName": row[3] or row[2] or str(row[1]),
            "tenantName": row[4] or "",
            "roleCodes": list(row[5] or []),
            "roleNames": list(row[6] or []),
            "ragRole": "租户管理员" if bool(row[7]) else "普通用户",
            "isTenantAdmin": bool(row[7]),
            "source": "identity_snapshot",
            "syncedAt": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8] or ""),
        }
        for row in rows
    ]


def clear_identity_snapshot_data() -> dict[str, int]:
    """
    清空身份快照数据

    用于测试环境或重置系统时清空本地身份数据。
    注意：这只是清空本地快照，不影响外部身份系统的数据。

    清空的表：
        - kb_identity_user_roles: 用户-角色关系
        - kb_identity_users: 用户
        - kb_identity_roles: 角色
        - kb_identity_tenants: 租户
        - kb_identity_sync_runs: 同步记录

    返回：
        各表删除的记录数 {"userRoles": N, "users": N, "roles": N, "tenants": N, "syncRuns": N}

    警告：
        这个操作不可逆，生产环境慎用！
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            deleted: dict[str, int] = {}
            # 按依赖顺序删除（先删外键依赖，后删主表）
            for table, key in (
                ("kb_identity_user_roles", "userRoles"),
                ("kb_identity_users", "users"),
                ("kb_identity_roles", "roles"),
                ("kb_identity_tenants", "tenants"),
                ("kb_identity_sync_runs", "syncRuns"),
            ):
                cur.execute(f"DELETE FROM {table}")
                deleted[key] = int(cur.rowcount or 0)
        conn.commit()
        return deleted
    finally:
        conn.close()
