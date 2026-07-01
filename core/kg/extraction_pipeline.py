from __future__ import annotations

from core.chunker.relation_utils import add_relation
from core.embedding.client import embed_texts
from core.kg.definition_generator import generate_definition
from core.kg.entity_merger import EntityMerger
from core.models.content_block import Chunk
from core.models.entity import Entity
from core.output.entity_writer import write_entities


def materialize_entities(conn, chunks: list[Chunk], kb_id: str) -> list[Entity]:
    raw: list[tuple] = []
    chunk_map = {chunk.id: chunk for chunk in chunks}
    for chunk in chunks:
        if chunk.layer != "enhanced":
            continue
        for entity in chunk.extracted_entities:
            raw.append((entity, chunk.parent_id or chunk.id))

    entities = EntityMerger().merge(kb_id, raw)
    if not entities:
        return []

    for entity in entities:
        entity.definition = generate_definition(entity, chunk_map)

    embeddings = embed_texts([f"{entity.name} {entity.definition or ''}" for entity in entities])
    for entity, embedding in zip(entities, embeddings):
        entity.embedding = embedding

    by_chunk: dict[str, list[Entity]] = {}
    for entity in entities:
        for chunk_id in entity.source_chunks:
            by_chunk.setdefault(chunk_id, []).append(entity)

    for chunk in chunks:
        for entity in by_chunk.get(chunk.id, []):
            add_relation(chunk, entity.id, rel_type="mentions", source="entity", evidence=entity.name[:20])

    write_entities(conn, entities)
    return entities
