"""
访问控制模块

这个模块就像是系统的"门卫"，负责检查用户有没有权限访问各种资源。

【访问控制是干什么的？】
想象一下，你的系统里有很多数据：
- 知识库：各种文档集合
- 文档：单个文件
- 任务：导入任务、处理任务等
- 草稿：切片草稿

不同用户应该只能看到和操作自己的数据。访问控制就是确保：
1. 用户A不能访问用户B的知识库
2. 没有权限的人看不到敏感数据
3. 所有非法访问都会被记录下来

【核心概念】
- IdentityContext（身份上下文）：包含当前用户是谁、有什么权限
- enforce_access（是否强制访问控制）：
  - True：正式环境，严格检查权限
  - False：开发/测试模式，放行所有请求

【模块功能】
1. 权限检查函数：require_* 系列
   - 检查通过：静默返回，继续执行
   - 检查失败：抛出 404 异常（不暴露资源存在性）

2. 过滤函数：filter_* 系列
   - 从列表中剔除无权访问的项

3. 辅助函数：查询数据库获取关联信息
   - 文档属于哪个知识库
   - 草稿属于哪个任务和知识库

4. 审计日志：记录所有被拒绝的访问尝试
"""

from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext
from core.db.knowledge_base import get_knowledge_base, list_knowledge_bases
from core.db.query_logs import AuditLogRecord, append_audit_log


def require_kb_access(
    kb_id: str | None,
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str = "knowledge_base",
    resource_id: str | None = None,
) -> None:
    """
    检查用户是否有权限访问某个知识库

    这是最核心的权限检查函数，其他资源（文档、任务、草稿）的权限检查
    最终都会归结到知识库权限的检查。

    【参数说明】
    - kb_id: 知识库ID，就是要检查访问权限的那个知识库
    - identity: 当前用户的身份信息（谁在访问、有什么权限）
    - action: 用户想做什么操作，比如 "read"、"write"、"delete"
    - resource_type: 资源类型，默认是 "knowledge_base"
    - resource_id: 具体资源ID，用于审计日志

    【工作流程】
    1. 如果 enforce_access=False（开发模式），直接放行
    2. 清理和标准化 kb_id（去掉空格、处理空值）
    3. 查询知识库，验证用户是否有权限访问
    4. 如果知识库不存在或用户无权访问：
       - 记录审计日志（记下谁在什么时候想访问什么）
       - 抛出 404 异常（用 404 而不是 403，避免泄露资源存在性）

    【安全设计】
    - 返回 404 而不是 403：不让攻击者知道资源是否存在
    - 所有被拒绝的访问都会记录审计日志
    - 即使权限检查失败，也不会暴露敏感信息

    【使用示例】
    >>> # 检查用户能否读取某个知识库
    >>> require_kb_access("kb-123", user_identity, action="read")
    >>> # 如果能执行到这里，说明有权限
    >>> # 如果抛出异常，说明没权限或知识库不存在

    【注意事项】
    - 这个函数不返回任何值（None）
    - 检查通过时静默返回，检查失败时抛出异常
    """
    # 开发/测试模式，不强制检查权限，直接放行
    if not identity.enforce_access:
        return

    # 清理 kb_id：去除空格、处理 None
    normalized_kb_id = str(kb_id or "").strip()

    # 检查知识库是否存在，且用户是否有权访问
    # get_knowledge_base 会根据 identity 进行权限过滤
    if not normalized_kb_id or get_knowledge_base(normalized_kb_id, identity) is None:
        # 记录这次被拒绝的访问（审计日志）
        _audit_denied(identity, action=action, resource_type=resource_type, resource_id=resource_id, kb_id=normalized_kb_id)
        # 抛出 404，不暴露资源是否真实存在
        raise HTTPException(status_code=404, detail=f"{resource_type} not found")


def require_document_access(document_id: str, identity: IdentityContext, *, action: str) -> None:
    """
    检查用户是否有权限访问某个文档

    【设计思路】
    文档属于知识库，所以文档权限本质上就是知识库权限。
    这个函数先查出文档属于哪个知识库，然后用知识库权限检查。

    【参数说明】
    - document_id: 文档ID
    - identity: 用户身份信息
    - action: 操作类型（read/write/delete 等）

    【工作流程】
    1. 开发模式直接放行
    2. 查询文档所属的知识库ID
    3. 如果文档不存在，抛出 404
    4. 复用 require_kb_access 检查知识库权限

    【为什么要两步检查？】
    - 第一步：验证文档是否存在
    - 第二步：验证用户是否有权访问文档所在的知识库
    这样既能保证数据完整性，又能准确判断权限。

    【使用示例】
    >>> # 检查用户能否读取某个文档
    >>> require_document_access("doc-456", user_identity, action="read")
    """
    # 开发/测试模式，直接放行
    if not identity.enforce_access:
        return

    # 查询文档所属的知识库ID
    kb_id = get_document_kb_id(document_id)

    # 文档不存在
    if not kb_id:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")

    # 检查用户对知识库的访问权限
    require_kb_access(kb_id, identity, action=action, resource_type="document", resource_id=document_id)


def require_task_access(task: dict | None, identity: IdentityContext, *, action: str) -> None:
    """
    检查用户是否有权限访问某个任务

    任务通常是指文档导入任务、处理任务等，它们也属于某个知识库。

    【参数说明】
    - task: 任务对象（字典形式），必须包含 'id' 和 'kb_id' 字段
    - identity: 用户身份信息
    - action: 操作类型

    【设计特点】
    这个函数接收的是 task 对象而不是 task_id，因为：
    1. 调用方通常已经查询到了任务对象
    2. 避免重复查询数据库
    3. 可以直接获取任务的知识库ID

    【工作流程】
    1. 任务对象为空 → 直接报 404
    2. 开发模式 → 放行
    3. 提取任务的 kb_id
    4. 检查知识库访问权限

    【注意事项】
    - 必须先检查 task 是否为 None，否则会抛出异常
    - task 参数是必需的，不像其他函数可以接受 None
    """
    # 任务不存在，直接返回 404
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 开发/测试模式，直接放行
    if not identity.enforce_access:
        return

    # 提取任务ID和知识库ID
    task_id = str(task.get("id") or "")
    kb_id = str(task.get("kb_id") or "")

    # 检查知识库权限（任务权限 = 任务所属知识库的权限）
    require_kb_access(kb_id, identity, action=action, resource_type="ingestion_task", resource_id=task_id)


def require_chunk_draft_access(draft_id: str, identity: IdentityContext, *, action: str) -> None:
    """
    检查用户是否有权限访问某个切片草稿

    切片草稿是文档处理过程中的中间产物，也属于某个知识库。

    【参数说明】
    - draft_id: 切片草稿ID
    - identity: 用户身份信息
    - action: 操作类型

    【工作流程】
    1. 开发模式 → 放行
    2. 查询草稿的"作用域"（属于哪个任务和知识库）
    3. 草稿不存在 → 抛出 404
    4. 检查知识库权限

    【什么是"作用域"？】
    草稿的作用域包括：
    - task_id: 属于哪个处理任务
    - kb_id: 属于哪个知识库

    这些信息用于权限检查和审计日志。

    【使用场景】
    当用户要查看、编辑或删除切片草稿时，先调用这个函数验证权限。
    """
    # 开发/测试模式，直接放行
    if not identity.enforce_access:
        return

    # 查询草稿的作用域信息（任务ID和知识库ID）
    scope = get_chunk_draft_scope(draft_id)

    # 草稿不存在
    if not scope:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")

    # 检查知识库权限
    require_kb_access(
        scope["kb_id"],
        identity,
        action=action,
        resource_type="chunk_draft",
        resource_id=draft_id,
    )


def filter_tasks_by_identity(tasks: Iterable[dict], identity: IdentityContext) -> list[dict]:
    """
    根据用户权限过滤任务列表

    当用户请求"显示所有任务"时，我们不应该返回全部任务，
    而是只返回用户有权访问的任务。

    【参数说明】
    - tasks: 任务列表（可迭代对象）
    - identity: 用户身份信息

    【返回值】
    返回过滤后的任务列表（只包含用户有权访问的任务）

    【工作流程】
    1. 将可迭代对象转为列表（方便多次遍历）
    2. 开发模式 → 返回全部任务
    3. 获取用户有权访问的所有知识库ID集合
    4. 过滤：只保留 kb_id 在集合中的任务

    【性能考虑】
    - 使用集合（set）存储知识库ID，查找速度 O(1)
    - 列表推导式比 filter() 更直观、性能相近

    【使用示例】
    >>> all_tasks = get_all_tasks_from_db()
    >>> visible_tasks = filter_tasks_by_identity(all_tasks, user_identity)
    >>> # visible_tasks 只包含用户有权访问的任务

    【为什么需要这个函数？】
    假设用户A有知识库 KB1、KB2，用户B有知识库 KB3。
    当用户A查询"所有任务"时，不应该看到用户B的任务。
    这个函数就负责过滤掉用户无权访问的任务。
    """
    # 转为列表，方便后续处理
    task_list = list(tasks)

    # 开发/测试模式，返回全部任务
    if not identity.enforce_access:
        return task_list

    # 获取用户有权访问的所有知识库ID
    # list_knowledge_bases 会根据 identity 进行权限过滤
    visible_kb_ids = {str(item["id"]) for item in list_knowledge_bases(identity)}

    # 过滤任务：只保留知识库ID在可见集合中的任务
    return [task for task in task_list if str(task.get("kb_id") or "") in visible_kb_ids]


def get_document_kb_id(document_id: str) -> str | None:
    """
    查询文档所属的知识库ID

    这是一个数据库查询函数，用于获取文档的"归属"信息。

    【参数说明】
    - document_id: 文档ID

    【返回值】
    - 成功：返回知识库ID（字符串）
    - 文档不存在：返回 None

    【为什么要查知识库ID？】
    因为文档的权限检查归根结底是知识库的权限检查。
    所以我们需要知道文档属于哪个知识库。

    【SQL 说明】
    ```sql
    SELECT kb_id FROM documents WHERE id::text = %s LIMIT 1
    ```
    - id::text: 将 UUID 转为文本，便于和传入的字符串比较
    - LIMIT 1: 找到一条就返回，提高性能

    【数据库连接管理】
    - 使用 get_db_connection() 获取连接
    - 使用 try-finally 确保连接关闭（即使查询失败）
    - 这是资源管理的基本模式

    【使用示例】
    >>> kb_id = get_document_kb_id("doc-123")
    >>> if kb_id:
    ...     print(f"文档属于知识库 {kb_id}")
    ... else:
    ...     print("文档不存在")
    """
    # 获取数据库连接
    conn = get_db_connection()
    try:
        # 使用游标执行查询
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kb_id
                FROM documents
                WHERE id::text = %s
                LIMIT 1
                """,
                (document_id,),
            )
            row = cur.fetchone()
    finally:
        # 无论成功失败，都要关闭连接
        conn.close()

    # 处理查询结果
    # row[0] 是 kb_id 字段，如果存在且不为 None 则转为字符串返回
    return str(row[0]) if row and row[0] is not None else None


def get_chunk_draft_scope(draft_id: str) -> dict[str, str] | None:
    """
    查询切片草稿的作用域信息

    作用域包括草稿所属的任务ID和知识库ID，这些信息用于权限检查。

    【参数说明】
    - draft_id: 切片草稿ID

    【返回值】
    - 成功：返回字典 {"task_id": "...", "kb_id": "..."}
    - 草稿不存在：返回 None

    【什么是"作用域"？】
    作用域定义了草稿的归属关系：
    - task_id: 草稿是在哪个任务中创建的
    - kb_id: 草稿属于哪个知识库

    【SQL 说明】
    ```sql
    SELECT task_id, kb_id FROM chunk_drafts WHERE id::text = %s LIMIT 1
    ```
    查询 chunk_drafts 表，获取任务和知识库的关联信息。

    【数据库连接管理】
    同样使用 try-finally 模式确保连接关闭。

    【使用示例】
    >>> scope = get_chunk_draft_scope("draft-789")
    >>> if scope:
    ...     print(f"草稿属于任务 {scope['task_id']}, 知识库 {scope['kb_id']}")
    ... else:
    ...     print("草稿不存在")

    【为什么返回字典而不是元组？】
    字典有明确的键名，代码可读性更好：
    - scope["kb_id"] 比 scope[1] 更清晰
    - 避免字段顺序混淆
    """
    # 获取数据库连接
    conn = get_db_connection()
    try:
        # 使用游标执行查询
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, kb_id
                FROM chunk_drafts
                WHERE id::text = %s
                LIMIT 1
                """,
                (draft_id,),
            )
            row = cur.fetchone()
    finally:
        # 无论成功失败，都要关闭连接
        conn.close()

    # 处理查询结果
    if not row:
        return None

    # 将查询结果转为字典
    # row[0] 是 task_id, row[1] 是 kb_id
    return {"task_id": str(row[0] or ""), "kb_id": str(row[1] or "")}


def _audit_denied(
    identity: IdentityContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    kb_id: str | None,
) -> None:
    """
    记录被拒绝的访问尝试（内部函数）

    这是一个私有函数（以 _ 开头），只在本模块内部使用。
    当权限检查失败时，调用此函数记录审计日志。

    【参数说明】
    - identity: 用户身份信息（谁在被拒绝）
    - action: 用户想做什么操作
    - resource_type: 资源类型（knowledge_base/document/task 等）
    - resource_id: 具体资源ID
    - kb_id: 知识库ID

    【审计日志的作用】
    1. 安全审计：追踪谁在什么时候尝试访问了什么
    2. 异常检测：发现可疑的访问模式（比如频繁访问不存在的资源）
    3. 合规要求：很多安全标准要求记录所有访问尝试
    4. 问题排查：当用户投诉"无法访问"时，可以查看日志

    【日志内容】
    - action: "access.denied"（访问被拒绝）
    - outcome: "denied"（结果：拒绝）
    - risk_level: "medium"（中等风险）
    - summary: 人类可读的描述
    - metadata: 详细的上下文信息（原因代码、资源类型、ID等）

    【为什么用 try-except？】
    记录审计日志不应该影响主流程：
    - 即使日志记录失败，也应该继续执行（抛出 404）
    - 日志失败通常是次要问题（比如日志系统故障）
    - 宁可少一条日志，也不要影响系统的正常权限控制

    【为什么返回 404 而不是 403？】
    这是一个重要的安全设计原则：
    - 404 Not Found：资源不存在
    - 403 Forbidden：资源存在但你没权限

    如果返回 403，攻击者可以通过枚举 ID 来判断哪些资源存在。
    返回 404 可以隐藏资源的存在性，增加攻击难度。

    【使用示例】
    >>> _audit_denied(
    ...     user_identity,
    ...     action="read",
    ...     resource_type="document",
    ...     resource_id="doc-123",
    ...     kb_id="kb-456"
    ... )
    """
    try:
        # 构建审计日志记录
        append_audit_log(
            AuditLogRecord(
                # 固定字段：访问被拒绝
                action="access.denied",
                resource_type=resource_type,
                resource_id=resource_id,
                kb_id=kb_id,
                identity=identity,
                # 结果：拒绝
                outcome="denied",
                # 风险等级：中等（因为可能是攻击者在探测）
                risk_level="medium",
                # 人类可读的摘要
                summary=f"Rejected {action} because resource is not accessible",
                # 详细的元数据，便于后续分析
                metadata={
                    "reasonCode": "RESOURCE_NOT_ACCESSIBLE",  # 原因代码
                    "action": action,  # 用户想做的操作
                    "resourceType": resource_type,  # 资源类型
                    "resourceId": resource_id,  # 资源ID
                    "kbId": kb_id,  # 知识库ID
                },
            )
        )
    except Exception:
        # 日志记录失败不应该影响主流程
        # 静默捕获异常，继续执行
        pass
