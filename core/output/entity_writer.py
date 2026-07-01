from __future__ import annotations

from core.models.entity import Entity


def write_entities(conn, entities: list[Entity]) -> int:
    if not entities:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO entities(id, kb_id, name, aliases, type, definition, embedding)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(kb_id, name) DO NOTHING
            """,
            [
                (
                    entity.id,
                    entity.kb_id,
                    entity.name,
                    entity.aliases,
                    entity.type,
                    entity.definition,
                    entity.embedding,
                )
                for entity in entities
            ],
        )
        mention_rows = [
            (entity.id, chunk_id, entity.kb_id)
            for entity in entities
            for chunk_id in entity.source_chunks
        ]
        cur.executemany(
            """
            INSERT INTO entity_mentions(entity_id, chunk_id, kb_id)
            VALUES(%s,%s,%s)
            ON CONFLICT(entity_id, chunk_id) DO NOTHING
            """,
            mention_rows,
        )
    return len(entities)
