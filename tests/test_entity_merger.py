from core.kg.entity_merger import EntityMerger
from core.models.extracted_entity import ExtractedEntity


def test_entity_merger_dedups_by_name_type():
    raw = [
        (ExtractedEntity(name="应急预案", type="Concept", aliases=[]), "c1"),
        (ExtractedEntity(name="应急预案", type="Concept", aliases=["预案"]), "c2"),
    ]
    entities = EntityMerger().merge("kb", raw)
    assert len(entities) == 1
    assert set(entities[0].source_chunks) == {"c1", "c2"}


def test_entity_merger_merges_aliases():
    raw = [
        (ExtractedEntity(name="OHSAS", type="Standard", aliases=["GB/T 28001"]), "c1"),
        (ExtractedEntity(name="OHSAS", type="Standard", aliases=["职业健康安全体系"]), "c2"),
    ]
    entities = EntityMerger().merge("kb", raw)
    assert len(entities) == 1
    assert set(entities[0].aliases) == {"GB/T 28001", "职业健康安全体系"}


def test_entity_merger_unknown_type_fallback():
    raw = [
        (ExtractedEntity(name="未知术语", type="SomethingElse", aliases=[]), "c1"),
    ]
    entities = EntityMerger().merge("kb", raw)
    assert len(entities) == 1
    assert entities[0].type == "Unknown"


def test_entity_merger_merges_alias_named_entity():
    raw = [
        (ExtractedEntity(name="OHSAS", type="Standard", aliases=["GB/T 28001"]), "c1"),
        (ExtractedEntity(name="GB/T 28001", type="Standard", aliases=[]), "c2"),
    ]
    entities = EntityMerger().merge("kb", raw)
    assert len(entities) == 1
    assert entities[0].name == "GB/T 28001"
    assert entities[0].aliases == ["OHSAS"]
    assert set(entities[0].source_chunks) == {"c1", "c2"}
