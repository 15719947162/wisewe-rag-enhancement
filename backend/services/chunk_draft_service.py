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
    return json.dumps(value or [], ensure_ascii=False)


def _serialize_models(items: list[Any]) -> str:
    return json.dumps([item.model_dump() for item in items], ensure_ascii=False)


def cleanup_expired_chunk_drafts() -> int:
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
