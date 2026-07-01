from unittest.mock import patch

from core.kg.extraction_pipeline import materialize_entities
from core.models.content_block import Chunk
from core.models.entity import Entity
from core.models.extracted_entity import ExtractedEntity


def _enhanced_chunk(parent_id: str, entities: list[ExtractedEntity]) -> Chunk:
    return Chunk(
        content="增强摘要",
        source="test",
        page=0,
        chunk_index=0,
        strategy="hierarchical",
        layer="enhanced",
        parent_id=parent_id,
        extracted_entities=entities,
    )


def _child_chunk(chunk_id: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        content="应急预案是针对突发事件制定的响应方案，用于指导组织开展处置。",
        source="test",
        page=0,
        chunk_index=0,
        strategy="hierarchical",
        layer="child",
    )


def test_materialize_entities_writes_mentions_relations():
    child = _child_chunk("child-1")
    enhanced = _enhanced_chunk(
        "child-1",
        [ExtractedEntity(name="应急预案", type="Concept", aliases=["预案"])],
    )
    with patch("core.kg.extraction_pipeline.embed_texts", return_value=[[0.1, 0.2]]), patch(
        "core.kg.extraction_pipeline.write_entities"
    ) as mocked_write:
        entities = materialize_entities(object(), [child, enhanced], "kb")

    assert len(entities) == 1
    assert entities[0].name == "应急预案"
    assert entities[0].definition.startswith("应急预案是针对突发事件制定的响应方案")
    assert child.related_ids == [entities[0].id]
    assert child.relations[0].rel_type == "mentions"
    mocked_write.assert_called_once()


def test_materialize_entities_merges_alias_mentions():
    child = _child_chunk("child-1")
    enhanced = _enhanced_chunk(
        "child-1",
        [
            ExtractedEntity(name="OHSAS", type="Standard", aliases=["GB/T 28001"]),
            ExtractedEntity(name="GB/T 28001", type="Standard", aliases=[]),
        ],
    )

    with patch("core.kg.extraction_pipeline.embed_texts", return_value=[[0.1, 0.2]]), patch(
        "core.kg.extraction_pipeline.write_entities"
    ):
        entities = materialize_entities(object(), [child, enhanced], "kb")

    assert len(entities) == 1
    assert child.related_ids == [entities[0].id]


def test_materialize_entities_returns_empty_when_no_enhanced_entities():
    child = _child_chunk("child-1")
    enhanced = _enhanced_chunk("child-1", [])
    with patch("core.kg.extraction_pipeline.write_entities") as mocked_write:
        entities = materialize_entities(object(), [child, enhanced], "kb")
    assert entities == []
    mocked_write.assert_not_called()
