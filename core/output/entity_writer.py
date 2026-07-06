"""
实体写入模块

负责将提取的实体写入数据库，包括实体本身和它们的引用关系。

核心功能：
1. 批量写入实体（名称、类型、定义、别名、向量）
2. 记录实体在哪些切片中被提及（多对多关系）

写入策略：
- 使用 ON CONFLICT DO NOTHING 避免重复插入
- 事务保证：实体和引用要么都写入，要么都不写入
"""

from __future__ import annotations

from core.models.entity import Entity


def write_entities(conn, entities: list[Entity]) -> int:
    """
    批量写入实体及其引用关系到数据库

    这个函数会做两件事：
    1. 把实体信息写入 entities 表（名称、类型、定义、向量等）
    2. 把实体与切片的关联写入 entity_mentions 表（实体在哪些切片中出现）

    参数：
        conn: 数据库连接对象（ psycopg2 连接）
        entities: 实体列表，每个实体包含：
            - id: 实体唯一标识
            - kb_id: 知识库ID
            - name: 实体名称
            - aliases: 别名列表
            - type: 实体类型（人名、地名、组织等）
            - definition: 实体定义/解释
            - embedding: 向量表示
            - source_chunks: 出现过的切片ID列表

    返回：
        成功写入的实体数量

    注意事项：
        - 使用 ON CONFLICT DO NOTHING，如果实体已存在（kb_id+name相同），会跳过
        - 整个操作在一个事务中完成，保证数据一致性
        - 空列表直接返回0，不会执行任何数据库操作

    示例：
        >>> entities = [
        ...     Entity(id="e1", kb_id="kb1", name="张三", type="人名", ...),
        ...     Entity(id="e2", kb_id="kb1", name="北京", type="地名", ...),
        ... ]
        >>> count = write_entities(conn, entities)
        >>> print(f"写入了 {count} 个实体")
    """
    # 空列表直接返回，避免无意义的数据库操作
    if not entities:
        return 0

    # 使用 with 语句管理游标，自动关闭
    with conn.cursor() as cur:
        # 第一步：批量插入实体基本信息
        # executemany 可以一次执行多条 INSERT，比循环单条插入快得多
        cur.executemany(
            """
            INSERT INTO entities(id, kb_id, name, aliases, type, definition, embedding)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(kb_id, name) DO NOTHING
            """,
            [
                (
                    entity.id,           # 实体ID
                    entity.kb_id,        # 知识库ID
                    entity.name,         # 实体名称
                    entity.aliases,      # 别名列表（PostgreSQL数组类型）
                    entity.type,         # 实体类型
                    entity.definition,   # 实体定义
                    entity.embedding,    # 向量（PostgreSQL vector类型）
                )
                for entity in entities
            ],
        )

        # 第二步：批量插入实体引用关系（实体在哪些切片中出现）
        # 一个实体可能在多个切片中被提及，这里展开成多行
        # 例如：实体A在切片1、切片2中出现，就会插入两行记录
        mention_rows = [
            (entity.id, chunk_id, entity.kb_id)
            for entity in entities
            for chunk_id in entity.source_chunks  # 遍历每个实体出现的所有切片
        ]

        cur.executemany(
            """
            INSERT INTO entity_mentions(entity_id, chunk_id, kb_id)
            VALUES(%s,%s,%s)
            ON CONFLICT(entity_id, chunk_id) DO NOTHING
            """,
            mention_rows,
        )

    # 返回写入的实体数量
    return len(entities)
