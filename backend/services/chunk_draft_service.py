"""
切片草稿服务模块

本模块负责管理文档入库过程中的切片草稿数据，支持：
1. 保存切片草稿（供用户预览和编辑）
2. 加载切片草稿列表
3. 更新、删除、合并切片草稿
4. 加载可确认的切片数据（用于最终入库）

为什么需要切片草稿？
- 用户在确认入库前需要预览切片效果
- 用户可以手动编辑切片内容
- 用户可以删除不需要的切片
- 用户可以合并相邻的切片

数据存储在 PostgreSQL 的 chunk_drafts 表中，有过期时间（expires_at）自动清理。
"""

from __future__ import annotations

import json
from typing import Any

from core.db.connection import get_db_connection
from core.db.init_db import ensure_db_schema
from core.models.content_block import Chunk
from core.models.extracted_entity import ExtractedEntity
from core.models.relation import Relation
from core.models.triple import Triple


def _serialize_related_ids(value: list[str] | None) -> str:
    """将关联 ID 列表序列化为 JSON 字符串"""
    return json.dumps(value or [], ensure_ascii=False)


def _serialize_models(items: list[Any]) -> str:
    """将模型对象列表序列化为 JSON 字符串"""
    return json.dumps([item.model_dump() for item in items], ensure_ascii=False)


def cleanup_expired_chunk_drafts() -> int:
    """
    清理过期的切片草稿

    删除 expires_at 小于当前时间的草稿记录。

    返回：
        int: 删除的记录数量
    """
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE expires_at < NOW()")
            deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def save_chunk_drafts(task_id: str, kb_id: str, chunks: list[Chunk]) -> int:
    """
    保存切片草稿到数据库

    在切片完成后，将切片数据保存到草稿表，供用户预览和编辑。

    参数：
        task_id: 任务 ID
        kb_id: 知识库 ID
        chunks: 切片列表

    返回：
        int: 保存的切片数量

    说明：
        - 先清理过期草稿
        - 删除同一 task_id 的旧草稿
        - 批量插入新草稿记录
    """
    try:
        cleanup_expired_chunk_drafts()
    except Exception:
        pass
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE task_id = %s", (task_id,))
            rows = [
                (
                    chunk.id,
                    task_id,
                    kb_id,
                    chunk.id,
                    chunk.chunk_index,
                    chunk.content,
                    chunk.source,
                    int(chunk.page) + 1,
                    chunk.strategy,
                    chunk.layer,
                    chunk.title,
                    chunk.parent_id,
                    _serialize_related_ids(chunk.related_ids),
                    chunk.is_table_chunk,
                    chunk.is_image_chunk,
                    chunk.image_path,
                    chunk.enhanced_text,
                    _serialize_models(chunk.extracted_entities),
                    _serialize_models(chunk.extracted_triples),
                    _serialize_models(chunk.relations),
                )
                for chunk in chunks
            ]
            cur.executemany(
                """
                INSERT INTO chunk_drafts(
                    id, task_id, kb_id, chunk_id, chunk_index, content, source, page,
                    strategy, layer, title, parent_id, related_ids, is_table_chunk, is_image_chunk,
                    image_path, enhanced_text, extracted_entities, extracted_triples, relations
                ) VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb
                )
                """,
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return len(chunks)


def _row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    """
    将数据库行转换为 API 响应格式的字典

    参数：
        row: 数据库查询结果行

    返回：
        dict: API 响应格式的切片草稿数据
    """
    return {
        "id": str(row[0]),
        "taskId": row[1],
        "kbId": row[2],
        "chunkId": str(row[3]) if row[3] else None,
        "chunkIndex": int(row[4] or 0),
        "content": row[5] or "",
        "source": row[6] or "",
        "page": int(row[7] or 0),
        "strategy": row[8] or "",
        "layer": row[9] or "child",
        "title": row[10] or "",
        "parentId": str(row[11]) if row[11] else None,
        "relatedIds": row[12] if isinstance(row[12], list) else [],
        "isTableChunk": bool(row[13]),
        "isImageChunk": bool(row[14]),
        "imagePath": row[15] or None,
        "enhancedText": row[16] or "",
        "extractedEntities": row[17] if isinstance(row[17], list) else [],
        "extractedTriples": row[18] if isinstance(row[18], list) else [],
        "relations": row[19] if isinstance(row[19], list) else [],
        "userEdited": bool(row[20]),
        "isDeleted": bool(row[21]),
        "createdAt": row[22].isoformat() if row[22] else "",
        "expiresAt": row[23].isoformat() if row[23] else "",
    }


def list_chunk_drafts(task_id: str) -> list[dict[str, Any]]:
    """
    获取指定任务的切片草稿列表

    参数：
        task_id: 任务 ID

    返回：
        list[dict]: 切片草稿列表，按 chunk_index 排序

    说明：
        - 会自动清理过期草稿
        - 返回所有字段，包括用户编辑状态和删除标记
    """
    try:
        cleanup_expired_chunk_drafts()
    except Exception:
        pass
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, task_id, kb_id, chunk_id, chunk_index, content, source, page,
                       strategy, layer, title, parent_id, related_ids, is_table_chunk,
                       is_image_chunk, image_path, enhanced_text, extracted_entities, extracted_triples,
                       relations, user_edited, is_deleted, created_at, expires_at
                FROM chunk_drafts
                WHERE task_id = %s
                ORDER BY chunk_index ASC, created_at ASC
                """,
                (task_id,),
            )
            rows = cur.fetchall()
        return [_row_to_payload(row) for row in rows]
    finally:
        conn.close()


def update_chunk_draft(draft_id: str, content: str) -> dict[str, Any] | None:
    """
    更新切片草稿内容

    用户手动编辑切片内容后调用此接口。

    参数：
        draft_id: 草稿 ID
        content: 新的切片内容

    返回：
        dict | None: 更新后的草稿数据，如果草稿不存在则返回 None

    说明：
        - 更新 content 字段
        - 设置 user_edited = True 标记用户编辑过
    """
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chunk_drafts
                SET content = %s,
                    user_edited = TRUE
                WHERE id::text = %s
                RETURNING id, task_id, kb_id, chunk_id, chunk_index, content, source, page,
                          strategy, layer, title, parent_id, related_ids, is_table_chunk,
                          is_image_chunk, image_path, enhanced_text, extracted_entities, extracted_triples,
                          relations, user_edited, is_deleted, created_at, expires_at
                """,
                (content, draft_id),
            )
            row = cur.fetchone()
        conn.commit()
        return _row_to_payload(row) if row else None
    finally:
        conn.close()


def delete_chunk_draft(draft_id: str) -> bool:
    """
    删除切片草稿（软删除）

    用户标记不需要的切片后调用此接口。

    参数：
        draft_id: 草稿 ID

    返回：
        bool: 是否删除成功

    说明：
        - 不是物理删除，而是设置 is_deleted = True
        - 同时设置 user_edited = True
    """
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chunk_drafts
                SET is_deleted = TRUE, user_edited = TRUE
                WHERE id::text = %s
                """,
                (draft_id,),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0
    finally:
        conn.close()


def merge_chunk_drafts(task_id: str, draft_ids: list[str]) -> dict[str, Any] | None:
    """
    合并多个切片草稿

    用户选择相邻切片合并后调用此接口。

    参数：
        task_id: 任务 ID
        draft_ids: 要合并的草稿 ID 列表

    返回：
        dict | None: 合并后的草稿数据，如果无法合并则返回 None

    合并逻辑：
        1. 按 chunk_index 排序
        2. 将内容用 \\n\\n 连接
        3. 合并 related_ids
        4. 保留第一个草稿，删除其他草稿
    """
    if len(draft_ids) < 2:
        return None

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, chunk_index, related_ids, title
                FROM chunk_drafts
                WHERE task_id = %s AND id::text = ANY(%s) AND is_deleted = FALSE
                ORDER BY chunk_index ASC
                """,
                (task_id, draft_ids),
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                return None

            first_id = str(rows[0][0])
            merged_content = "\n\n".join((row[1] or "").strip() for row in rows if (row[1] or "").strip())
            related_ids: list[str] = []
            for row in rows:
                values = row[3] if isinstance(row[3], list) else []
                for item in values:
                    if item not in related_ids:
                        related_ids.append(item)

            cur.execute(
                """
                UPDATE chunk_drafts
                SET content = %s,
                    related_ids = %s::jsonb,
                    user_edited = TRUE
                WHERE id::text = %s
                """,
                (merged_content, json.dumps(related_ids, ensure_ascii=False), first_id),
            )
            cur.execute(
                """
                UPDATE chunk_drafts
                SET is_deleted = TRUE, user_edited = TRUE
                WHERE task_id = %s AND id::text = ANY(%s) AND id::text <> %s
                """,
                (task_id, draft_ids, first_id),
            )

            cur.execute(
                """
                SELECT id, task_id, kb_id, chunk_id, chunk_index, content, source, page,
                       strategy, layer, title, parent_id, related_ids, is_table_chunk,
                       is_image_chunk, image_path, enhanced_text, extracted_entities, extracted_triples,
                       relations, user_edited, is_deleted, created_at, expires_at
                FROM chunk_drafts
                WHERE id::text = %s
                """,
                (first_id,),
            )
            row = cur.fetchone()
        conn.commit()
        return _row_to_payload(row) if row else None
    finally:
        conn.close()


def load_confirmable_chunks(task_id: str) -> list[Chunk]:
    """
    加载可确认入库的切片列表

    用户确认入库时，从草稿表中加载未被删除的切片。

    参数：
        task_id: 任务 ID

    返回：
        list[Chunk]: Chunk 对象列表，可直接用于后续处理

    说明：
        - 会自动清理过期草稿
        - 只加载 is_deleted = FALSE 的记录
        - 按 chunk_index 排序
        - 反序列化 extracted_entities、extracted_triples、relations 字段
    """
    cleanup_expired_chunk_drafts()
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, content, source, page, chunk_index, strategy, title,
                       is_table_chunk, is_image_chunk, layer, parent_id, related_ids,
                       image_path, enhanced_text, extracted_entities, extracted_triples, relations
                FROM chunk_drafts
                WHERE task_id = %s AND is_deleted = FALSE
                ORDER BY chunk_index ASC, created_at ASC
                """,
                (task_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        Chunk(
            id=str(row[0]) if row[0] else "",
            content=row[1] or "",
            source=row[2] or "",
            page=max(int(row[3] or 1) - 1, 0),
            chunk_index=index,
            strategy=row[5] or "",
            title=row[6] or "",
            is_table_chunk=bool(row[7]),
            is_image_chunk=bool(row[8]),
            image_path=row[12] or None,
            layer=row[9] or "child",
            parent_id=str(row[10]) if row[10] else None,
            enhanced_text=row[13] or None,
            extracted_entities=[ExtractedEntity(**item) for item in (row[14] if isinstance(row[14], list) else [])],
            extracted_triples=[Triple(**item) for item in (row[15] if isinstance(row[15], list) else [])],
            relations=[Relation(**item) for item in (row[16] if isinstance(row[16], list) else [])],
        )
        for index, row in enumerate(rows)
    ]


def clear_chunk_drafts(task_id: str) -> int:
    """
    清除指定任务的所有切片草稿

    任务完成或取消后调用，清理草稿数据。

    参数：
        task_id: 任务 ID

    返回：
        int: 删除的记录数量
    """
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk_drafts WHERE task_id = %s", (task_id,))
            deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()
