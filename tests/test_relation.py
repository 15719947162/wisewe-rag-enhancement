import pytest

from core.chunker.relation_utils import add_relation
from core.models.content_block import Chunk
from core.models.relation import Relation


def test_relation_defaults_and_constraints():
    relation = Relation(target_id="b", rel_type="refers_to", source="rule")
    assert relation.weight == 1.0
    assert relation.evidence == ""


def test_relation_rejects_invalid_weight():
    with pytest.raises(Exception):
        Relation(target_id="b", rel_type="refers_to", source="rule", weight=1.5)


def test_add_relation_clamps_tiny_float_weight_overflow():
    chunk = Chunk(content="a", source="s", page=1, chunk_index=0, strategy="hierarchical")

    add_relation(
        chunk,
        target_id="b",
        rel_type="semantic_similar",
        weight=1.0000000000000002,
        source="embedding",
    )

    assert chunk.relations[0].weight == 1.0


def test_add_relation_still_rejects_clearly_invalid_weight():
    chunk = Chunk(content="a", source="s", page=1, chunk_index=0, strategy="hierarchical")

    with pytest.raises(Exception):
        add_relation(
            chunk,
            target_id="b",
            rel_type="semantic_similar",
            weight=1.5,
            source="embedding",
        )
