"""
知识库管理模块

本模块提供知识库（Knowledge Base）的完整 CRUD 操作，包括：
- 创建知识库
- 查询知识库列表和详情
- 更新知识库元数据
- 删除知识库（软删除）
- 所有者转让

知识库是 RAG 系统的顶层容器，用于组织和管理文档及其切片。
每个知识库可以配置默认的切片策略，并支持多租户访问控制。

数据库表结构说明：
- id: 知识库唯一标识
- name: 知识库名称
- description: 知识库描述
- default_strategy: 默认切片策略
- tenant_id: 租户 ID（多租户隔离）
- created_by: 创建者用户 ID
- owner_user_id: 所有者用户 ID
- owner_status: 所有者状态（active/pending_transfer）
- owner_invalid_reason: 所有者失效原因
- status: 知识库状态（active/deleted）
- deleted_at: 软删除时间戳

使用示例：
    # 创建知识库
    from core.db.identity import IdentityContext

    identity = IdentityContext(
        tenant_id="tenant-001",
        user_id="user-001",
        enforce_access=True
    )

    kb = create_knowledge_base(
        kb_id="kb-001",
        name="产品手册知识库",
        description="存储产品相关文档",
        default_strategy="hierarchical",
        identity=identity
    )

    # 列出知识库
    kbs = list_knowledge_bases(identity=identity)

    # 获取知识库详情
    kb = get_knowledge_base("kb-001", identity=identity)

    # 更新知识库
    kb = update_knowledge_base(
        kb_id="kb-001",
        name="新名称",
        identity=identity
    )

    # 删除知识库（软删除）
    delete_knowledge_base("kb-001", identity=identity)
"""
from __future__ import annotations

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext, anonymous_identity


# 所有者状态常量
ACTIVE_OWNER_STATUS = "active"  # 所有者状态正常
PENDING_TRANSFER_OWNER_STATUS = "pending_transfer"  # 所有者待转让


def create_knowledge_base(
    kb_id: str,
    name: str,
    description: str = "",
    default_strategy: str = "hierarchical",
    identity: IdentityContext | None = None,
) -> dict:
    """
    创建知识库，如果已存在则不执行任何操作（幂等性）。

    本函数向 knowledge_bases 表插入一条新记录，并设置所有者和租户信息。
    使用 PostgreSQL 的 ON CONFLICT DO NOTHING 语法保证幂等性。

    Args:
        kb_id: 知识库唯一标识符，建议使用 UUID 或有意义的字符串。
        name: 知识库名称，用于显示。
        description: 知识库描述，默认为空字符串。
        default_strategy: 默认切片策略，默认为 'hierarchical'。
            可选值：fixed_length, paragraph, semantic, separator, llm, hierarchical
        identity: 身份上下文，包含 tenant_id 和 user_id。
            如果为 None，使用匿名身份。

    Returns:
        dict: 创建的知识库信息字典，包含以下字段：
            - id: 知识库 ID
            - name: 知识库名称
            - description: 描述
            - default_strategy: 默认切片策略
            - tenant_id: 租户 ID
            - created_by: 创建者 ID
            - owner_user_id: 所有者 ID
            - owner_status: 所有者状态
            - owner_invalid_reason: 所有者失效原因
            - status: 知识库状态
            - created: 是否实际创建（False 表示已存在）

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> from core.db.identity import IdentityContext
        >>> identity = IdentityContext(tenant_id="t1", user_id="u1", enforce_access=True)
        >>> kb = create_knowledge_base("kb-001", "测试知识库", identity=identity)
        >>> kb["created"]
        True
    """
    identity = identity or anonymous_identity()
    tenant_id = identity.tenant_id if identity.enforce_access else None
    actor_id = identity.user_id if identity.enforce_access else None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 使用 ON CONFLICT DO NOTHING 保证幂等性
            # 如果 kb_id 已存在，不会插入也不会报错
            cur.execute(
                """
                INSERT INTO knowledge_bases(
                    id, name, description, default_strategy,
                    tenant_id, created_by, owner_user_id, owner_status, status
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, 'active', 'active')
                ON CONFLICT(id) DO NOTHING
                """,
                (kb_id, name, description, default_strategy, tenant_id, actor_id, actor_id),
            )
            # rowcount == 1 表示实际插入了新记录
            # rowcount == 0 表示记录已存在，未插入
            created = cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    return {
        "id": kb_id,
        "name": name,
        "description": description,
        "default_strategy": default_strategy,
        "tenant_id": tenant_id,
        "created_by": actor_id,
        "owner_user_id": actor_id,
        "owner_status": ACTIVE_OWNER_STATUS,
        "owner_invalid_reason": "",
        "status": "active",
        "created": created,
    }


def list_knowledge_bases(identity: IdentityContext | None = None) -> list[dict]:
    """
    查询当前用户可见的知识库列表，包含文档统计信息。

    本函数根据身份上下文进行访问控制：
    - 平台管理员：可看到所有未删除的知识库
    - 租户管理员：可看到租户下所有知识库（包括无租户的历史数据）
    - 普通用户：只能看到自己作为所有者的知识库

    查询结果按创建时间倒序排列，并包含每个知识库的文档数量、
    切片总数和最后更新时间。

    Args:
        identity: 身份上下文，用于访问控制。
            如果为 None，使用匿名身份（无访问限制）。

    Returns:
        list[dict]: 知识库列表，每个字典包含：
            - id: 知识库 ID
            - name: 知识库名称
            - description: 描述
            - default_strategy: 默认切片策略
            - tenant_id: 租户 ID
            - created_by: 创建者 ID
            - owner_user_id: 所有者 ID
            - owner_status: 所有者状态
            - owner_invalid_reason: 所有者失效原因
            - status: 知识库状态
            - deleted_at: 删除时间
            - created_at: 创建时间
            - doc_count: 文档数量
            - chunk_count: 切片总数
            - last_updated: 最后更新时间（文档或知识库）

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> kbs = list_knowledge_bases(identity=identity)
        >>> for kb in kbs:
        ...     print(f"{kb['name']}: {kb['doc_count']} docs, {kb['chunk_count']} chunks")
    """
    identity = identity or anonymous_identity()
    where_sql, params = _access_filter_sql(identity, "kb")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # LEFT JOIN 关联 documents 表，统计文档和切片数量
            # COALESCE 处理 NULL 值，确保即使没有文档也返回 0
            cur.execute(
                f"""
                SELECT kb.id,
                       kb.name,
                       kb.description,
                       kb.default_strategy,
                       kb.tenant_id,
                       kb.created_by,
                       kb.owner_user_id,
                       kb.owner_status,
                       kb.owner_invalid_reason,
                       kb.status,
                       kb.deleted_at,
                       kb.created_at,
                       COUNT(d.id) AS doc_count,
                       COALESCE(SUM(d.chunk_count), 0) AS chunk_count,
                       COALESCE(MAX(d.updated_at), kb.created_at) AS last_updated
                FROM knowledge_bases kb
                LEFT JOIN documents d ON d.kb_id = kb.id
                {where_sql}
                GROUP BY kb.id
                ORDER BY kb.created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
            # 从 cursor.description 获取列名，构建字典
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_knowledge_base(kb_id: str, identity: IdentityContext | None = None) -> dict | None:
    """
    根据 ID 获取单个知识库的详细信息。

    本函数会进行访问控制检查，确保用户有权限访问该知识库。
    如果知识库不存在或用户无权访问，返回 None。

    Args:
        kb_id: 知识库唯一标识符。
        identity: 身份上下文，用于访问控制。
            如果为 None，使用匿名身份（无访问限制）。

    Returns:
        dict | None: 知识库信息字典，包含：
            - id: 知识库 ID
            - name: 知识库名称
            - description: 描述
            - default_strategy: 默认切片策略
            - tenant_id: 租户 ID
            - created_by: 创建者 ID
            - owner_user_id: 所有者 ID
            - owner_status: 所有者状态
            - owner_invalid_reason: 所有者失效原因
            - status: 知识库状态
            - deleted_at: 删除时间
            - created_at: 创建时间
        如果知识库不存在或无权访问，返回 None。

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> kb = get_knowledge_base("kb-001", identity=identity)
        >>> if kb:
        ...     print(f"知识库名称: {kb['name']}")
        ... else:
        ...     print("知识库不存在或无权访问")
    """
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 使用访问控制过滤条件
            # _where_to_and 将 WHERE 转换为 AND，以便与 kb_id 条件组合
            cur.execute(
                f"""
                SELECT id, name, description, default_strategy,
                       tenant_id, created_by, owner_user_id,
                       owner_status, owner_invalid_reason,
                       status, deleted_at, created_at
                FROM knowledge_bases
                WHERE id = %s {_where_to_and(access_sql)}
                """,
                (kb_id, *params),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()
    return dict(zip(cols, row))


def update_knowledge_base(
    kb_id: str,
    name: str,
    description: str = "",
    default_strategy: str = "hierarchical",
    identity: IdentityContext | None = None,
) -> dict | None:
    """
    更新知识库的可编辑元数据。

    本函数更新知识库的名称、描述和默认切片策略。
    会进行访问控制检查，确保用户有权限修改该知识库。

    注意：本函数不修改租户、所有者等敏感字段，这些字段需要
    通过专门的转让函数修改。

    Args:
        kb_id: 知识库唯一标识符。
        name: 新的知识库名称。
        description: 新的描述，默认为空字符串。
        default_strategy: 新的默认切片策略，默认为 'hierarchical'。
        identity: 身份上下文，用于访问控制。
            如果为 None，使用匿名身份（无访问限制）。

    Returns:
        dict | None: 更新后的知识库信息字典，字段同 get_knowledge_base。
            如果知识库不存在或无权访问，返回 None。

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> kb = update_knowledge_base(
        ...     kb_id="kb-001",
        ...     name="更新后的名称",
        ...     description="新的描述",
        ...     identity=identity
        ... )
        >>> if kb:
        ...     print(f"更新成功: {kb['name']}")
    """
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 使用 RETURNING 子句返回更新后的记录
            # 这样可以在一次请求中完成更新和读取
            cur.execute(
                f"""
                UPDATE knowledge_bases
                SET name = %s,
                    description = %s,
                    default_strategy = %s
                WHERE id = %s {_where_to_and(access_sql)}
                RETURNING id, name, description, default_strategy,
                          tenant_id, created_by, owner_user_id,
                          owner_status, owner_invalid_reason,
                          status, deleted_at, created_at
                """,
                (name, description, default_strategy, kb_id, *params),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(cols, row))


def delete_knowledge_base(kb_id: str, identity: IdentityContext | None = None) -> int:
    """
    软删除知识库（标记为已删除，不物理删除）。

    本函数将知识库的 status 设为 'deleted'，并记录删除时间。
    软删除的知识库不会出现在常规查询中，但数据仍保留在数据库中，
    便于后续恢复或审计。

    注意：这是软删除，不是物理删除。删除后知识库仍可通过
    数据库直接查询，但通过本模块的查询函数将不可见。

    Args:
        kb_id: 知识库唯一标识符。
        identity: 身份上下文，用于访问控制。
            如果为 None，使用匿名身份（无访问限制）。

    Returns:
        int: 实际删除的知识库数量。
            - 1: 删除成功
            - 0: 知识库不存在或无权访问

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> deleted = delete_knowledge_base("kb-001", identity=identity)
        >>> if deleted:
        ...     print("删除成功")
        ... else:
        ...     print("知识库不存在或无权访问")
    """
    identity = identity or anonymous_identity()
    access_sql, params = _access_filter_sql(identity, "knowledge_bases")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 软删除：设置 status 和 deleted_at，不实际删除记录
            cur.execute(
                f"""
                UPDATE knowledge_bases
                SET status = 'deleted',
                    deleted_at = NOW()
                WHERE id = %s {_where_to_and(access_sql)}
                """,
                (kb_id, *params),
            )
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def mark_knowledge_bases_pending_transfer_for_user(tenant_id: str, user_id: str, reason: str) -> int:
    """
    将指定用户拥有的所有知识库标记为待转让状态。

    当用户被删除或禁用时，其拥有的知识库需要转让给其他用户。
    本函数将这些知识库的 owner_status 设为 'pending_transfer'，
    并记录失效原因。

    这是用户生命周期管理的一部分，通常在以下场景调用：
    - 用户被删除（reason='deleted'）
    - 用户被禁用（reason='disabled'）

    Args:
        tenant_id: 租户 ID。
        user_id: 需要转让的用户 ID。
        reason: 失效原因，只能是 'deleted' 或 'disabled'。
            其他值会被规范化为 'disabled'。

    Returns:
        int: 被标记的知识库数量。

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> # 用户被删除时调用
        >>> count = mark_knowledge_bases_pending_transfer_for_user(
        ...     tenant_id="tenant-001",
        ...     user_id="user-001",
        ...     reason="deleted"
        ... )
        >>> print(f"已标记 {count} 个知识库待转让")
    """
    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    # 规范化原因，只接受 deleted 或 disabled
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason not in {"deleted", "disabled"}:
        normalized_reason = "disabled"
    if not tenant or not user:
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 只标记未删除的知识库
            cur.execute(
                """
                UPDATE knowledge_bases
                SET owner_status = %s,
                    owner_invalid_reason = %s
                WHERE tenant_id = %s
                  AND owner_user_id = %s
                  AND deleted_at IS NULL
                  AND status <> 'deleted'
                """,
                (PENDING_TRANSFER_OWNER_STATUS, normalized_reason, tenant, user),
            )
            updated = cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()


def transfer_knowledge_base_owner(
    kb_id: str,
    new_owner_user_id: str,
    identity: IdentityContext | None = None,
) -> dict | None:
    """
    转让知识库的所有者。

    本函数将知识库的所有者转让给新用户。只有租户管理员或平台管理员
    可以执行此操作。新所有者必须是同一租户下的活跃用户。

    转让成功后，会记录转让时间、转让操作者，并清除待转让状态。

    Args:
        kb_id: 知识库唯一标识符。
        new_owner_user_id: 新所有者的用户 ID。
        identity: 操作者的身份上下文，必须是租户管理员或平台管理员。
            如果为 None，使用匿名身份（无访问限制）。

    Returns:
        dict | None: 更新后的知识库信息字典。
            如果知识库不存在或无权访问，返回 None。

    Raises:
        PermissionError: 操作者不是租户管理员或平台管理员。
        ValueError: 新所有者 ID 为空，或新所有者不在同一租户或不是活跃用户。
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> # 需要管理员权限
        >>> admin_identity = IdentityContext(
        ...     tenant_id="tenant-001",
        ...     user_id="admin-001",
        ...     is_tenant_admin=True,
        ...     enforce_access=True
        ... )
        >>> kb = transfer_knowledge_base_owner(
        ...     kb_id="kb-001",
        ...     new_owner_user_id="user-002",
        ...     identity=admin_identity
        ... )
    """
    identity = identity or anonymous_identity()
    # 权限检查：只有租户管理员或平台管理员可以转让
    if not (identity.is_tenant_admin or identity.is_platform_admin):
        raise PermissionError("Only tenant or platform administrators can transfer knowledge base ownership")

    new_owner = str(new_owner_user_id or "").strip()
    if not new_owner:
        raise ValueError("new_owner_user_id is required")

    access_sql, params = _access_filter_sql(identity, "kb")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 第一步：查询知识库并验证访问权限
            cur.execute(
                f"""
                SELECT kb.id, kb.tenant_id
                FROM knowledge_bases kb
                {access_sql}
                  AND kb.id = %s
                LIMIT 1
                """,
                (*params, kb_id),
            )
            kb_row = cur.fetchone()
            if not kb_row:
                return None
            kb_tenant_id = str(kb_row[1] or "")

            # 第二步：如果知识库有租户，验证新所有者是同一租户的活跃用户
            if kb_tenant_id:
                cur.execute(
                    """
                    SELECT 1
                    FROM kb_identity_users
                    WHERE tenant_id = %s
                      AND user_id = %s
                      AND user_status = 'active'
                    LIMIT 1
                    """,
                    (kb_tenant_id, new_owner),
                )
                if cur.fetchone() is None:
                    raise ValueError("new owner must be an active user in the same tenant")

            # 第三步：更新所有者信息
            cur.execute(
                """
                UPDATE knowledge_bases
                SET owner_user_id = %s,
                    owner_transferred_at = NOW(),
                    owner_transferred_by = %s,
                    owner_status = %s,
                    owner_invalid_reason = NULL
                WHERE id = %s
                RETURNING id, name, description, default_strategy,
                          tenant_id, created_by, owner_user_id,
                          owner_status, owner_invalid_reason,
                          status, deleted_at, created_at
                """,
                (new_owner, identity.user_id if identity.enforce_access else None, ACTIVE_OWNER_STATUS, kb_id),
            )
            row = cur.fetchone()
            cols = [desc[0] for desc in cur.description] if row else []
        conn.commit()
    finally:
        conn.close()

    if row is None:
        return None
    return dict(zip(cols, row))


def ensure_default_kb() -> None:
    """
    确保默认知识库存在，如果不存在则创建。

    默认知识库的 ID 为 'default'，用于存储没有明确指定知识库的文档。
    这是一个便捷函数，通常在系统初始化时调用。

    本函数使用 create_knowledge_base 的幂等性，即使多次调用也安全。

    Raises:
        psycopg2.Error: 数据库操作失败时抛出。

    使用示例：
        >>> # 在系统启动时调用
        >>> ensure_default_kb()
        >>> # 现在可以确信 'default' 知识库存在
    """
    create_knowledge_base("default", "Default knowledge base", "Automatically created default knowledge base")


def _access_filter_sql(identity: IdentityContext, table_alias: str) -> tuple[str, tuple[str, ...]]:
    """
    构建访问控制的 SQL WHERE 子句和参数。

    本函数根据身份上下文生成适当的访问控制条件：
    - 未启用访问控制：只过滤已删除的记录
    - 平台管理员：只过滤已删除的记录（可看到所有租户的数据）
    - 租户管理员：过滤已删除记录 + 租户匹配（包括无租户的历史数据）
    - 普通用户：过滤已删除记录 + 租户匹配 + 所有者匹配

    Args:
        identity: 身份上下文，包含租户、用户和管理员信息。
        table_alias: 表别名，用于 SQL 中引用表的字段。
            例如 'kb' 或 'knowledge_bases'。

    Returns:
        tuple[str, tuple[str, ...]]: 返回元组 (where_sql, params)
            - where_sql: WHERE 子句，如 "WHERE deleted_at IS NULL AND tenant_id = %s"
            - params: 参数元组，用于参数化查询

    使用示例：
        >>> where_sql, params = _access_filter_sql(identity, "kb")
        >>> sql = f"SELECT * FROM knowledge_bases kb {where_sql}"
        >>> cur.execute(sql, params)

    注意：
        租户管理员可以看到 tenant_id IS NULL 的历史知识库，这是为了
        兼容在身份治理功能上线前创建的知识库。
    """
    qualifier = f"{table_alias}." if table_alias else ""
    # 基础条件：排除已删除的记录
    clauses = [f"{qualifier}deleted_at IS NULL"]
    params: list[str] = []

    # 根据身份类型添加访问控制条件
    if identity.enforce_access and not identity.is_platform_admin:
        if identity.is_tenant_admin:
            # 租户管理员：可看到租户下所有知识库 + 无租户的历史数据
            # Legacy knowledge bases created before identity governance have no tenant owner.
            # Tenant admins may see them so the migration path does not hide existing content.
            clauses.append(f"({qualifier}tenant_id = %s OR {qualifier}tenant_id IS NULL)")
            params.append(identity.tenant_id or "")
        else:
            # 普通用户：只能看到自己作为所有者的知识库
            clauses.append(f"{qualifier}tenant_id = %s")
            params.append(identity.tenant_id or "")
            clauses.append(f"{qualifier}owner_user_id = %s")
            params.append(identity.user_id or "")

    return "WHERE " + " AND ".join(clauses), tuple(params)


def _where_to_and(where_sql: str) -> str:
    """
    将 WHERE 子句转换为 AND 子句。

    当需要在已有条件（如 kb_id = %s）后追加访问控制条件时使用。
    将 "WHERE deleted_at IS NULL AND tenant_id = %s" 转换为
    "AND deleted_at IS NULL AND tenant_id = %s"。

    Args:
        where_sql: 原始 WHERE 子句。

    Returns:
        str: 转换后的 AND 子句。

    使用示例：
        >>> where_sql = "WHERE deleted_at IS NULL"
        >>> sql = f"SELECT * FROM kb WHERE id = %s {_where_to_and(where_sql)}"
        >>> # 结果：SELECT * FROM kb WHERE id = %s AND deleted_at IS NULL
    """
    return where_sql.replace("WHERE", "AND", 1)
