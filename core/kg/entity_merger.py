from __future__ import annotations

from core.models.entity import Entity
from core.models.extracted_entity import ExtractedEntity

ALLOWED_ENTITY_TYPES = {
    "Concept",
    "Procedure",
    "Equipment",
    "Standard",
    "Quantity",
    "Person",
    "Time",
}


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().split())


def _unique_ordered(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_name(value)
        if not normalized:
            continue
        lowered = normalized.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(normalized)
    return ordered


class EntityMerger:
    def merge(self, kb_id: str, raw: list[tuple[ExtractedEntity, str]]) -> list[Entity]:
        entities: list[Entity] = []
        for extracted, source_chunk in raw:
            name = _normalize_name(extracted.name)
            if not name:
                continue
            entity_type = extracted.type if extracted.type in ALLOWED_ENTITY_TYPES else "Unknown"
            aliases = _unique_ordered(extracted.aliases)

            matched = self._find_match(entities, name, entity_type, aliases)
            if matched is None:
                entities.append(
                    Entity(
                        kb_id=kb_id,
                        name=name,
                        aliases=aliases,
                        type=entity_type,
                        definition=None,
                        source_chunks=[source_chunk],
                    )
                )
                continue

            self._merge_into(matched, name, aliases, source_chunk)
        return entities

    def _find_match(
        self,
        entities: list[Entity],
        name: str,
        entity_type: str,
        aliases: list[str],
    ) -> Entity | None:
        candidate_names = {name.casefold(), *(alias.casefold() for alias in aliases)}
        for entity in entities:
            if entity.type != entity_type:
                continue
            known_names = {entity.name.casefold(), *(alias.casefold() for alias in entity.aliases)}
            if candidate_names & known_names:
                return entity
        return None

    def _merge_into(
        self,
        entity: Entity,
        name: str,
        aliases: list[str],
        source_chunk: str,
    ) -> None:
        direct_names = _unique_ordered([entity.name, name])
        canonical_name = max(direct_names, key=len)
        all_names = _unique_ordered([entity.name, *entity.aliases, name, *aliases])
        entity.name = canonical_name
        entity.aliases = [candidate for candidate in all_names if candidate.casefold() != canonical_name.casefold()]
        if source_chunk not in entity.source_chunks:
            entity.source_chunks.append(source_chunk)
