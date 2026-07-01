import pytest

from core.chunker.semantic_linker import _link_semantic_numpy, _link_semantic_python, link_semantic
from core.models.content_block import Chunk


def _chunk(content: str, idx: int, parent_id: str | None = None) -> Chunk:
    return Chunk(
        id=f"chunk-{idx}",
        content=content,
        source="s",
        page=0,
        chunk_index=idx,
        strategy="hierarchical",
        layer="child",
        parent_id=parent_id,
    )


def test_link_semantic_adds_relations():
    chunks = [_chunk("a", 0), _chunk("b", 1), _chunk("c", 2)]
    embeddings = [
        [1.0, 0.0],
        [0.99, 0.01],
        [0.0, 1.0],
    ]
    added = link_semantic(chunks, embeddings, threshold=0.8, topk=2, dup_threshold=0.98)
    assert added >= 1
    assert chunks[0].relations[0].rel_type in {"semantic_similar", "duplicate_of"}


def test_link_semantic_skips_same_parent():
    chunks = [_chunk("a", 0, parent_id="p1"), _chunk("b", 1, parent_id="p1"), _chunk("c", 2, parent_id="p2")]
    embeddings = [
        [1.0, 0.0],
        [0.99, 0.01],
        [0.98, 0.02],
    ]
    added = link_semantic(chunks, embeddings, threshold=0.8, topk=2, dup_threshold=0.995)
    assert added >= 1
    assert all(relation.target_id != chunks[1].id for relation in chunks[0].relations)


def test_link_semantic_marks_duplicate_of_above_dup_threshold():
    chunks = [_chunk("a", 0), _chunk("b", 1)]
    embeddings = [
        [1.0, 0.0],
        [1.0, 0.0],
    ]
    added = link_semantic(chunks, embeddings, threshold=0.8, topk=1, dup_threshold=0.95)
    assert added == 1
    assert chunks[0].relations[0].rel_type == "duplicate_of"
    assert chunks[0].relations[0].weight >= 0.95


def test_link_semantic_can_be_disabled():
    chunks = [_chunk("a", 0), _chunk("b", 1)]
    embeddings = [
        [1.0, 0.0],
        [0.99, 0.01],
    ]
    added = link_semantic(chunks, embeddings, enabled=False)
    assert added == 0
    assert chunks[0].relations == []


def test_link_semantic_rejects_mismatched_lengths():
    chunks = [_chunk("a", 0), _chunk("b", 1)]
    embeddings = [[1.0, 0.0]]
    try:
        link_semantic(chunks, embeddings)
    except ValueError as exc:
        assert "embeddings length" in str(exc)
    else:
        raise AssertionError("expected ValueError for mismatched lengths")


def test_link_semantic_falls_back_when_numpy_disabled(monkeypatch):
    monkeypatch.setenv("LINKER_SEMANTIC_NUMPY_ENABLED", "false")
    chunks = [_chunk("a", 0), _chunk("b", 1)]
    embeddings = [
        [1.0, 0.0],
        [0.99, 0.01],
    ]

    added = link_semantic(chunks, embeddings, threshold=0.8, topk=1, dup_threshold=0.98)

    assert added == 1
    assert chunks[0].relations[0].target_id == chunks[1].id


def _snapshot(chunks: list[Chunk]) -> list[list[tuple[str, str, float, str, str]]]:
    return [
        [
            (
                relation.target_id,
                relation.rel_type,
                round(relation.weight, 12),
                relation.source,
                relation.evidence,
            )
            for relation in chunk.relations
        ]
        for chunk in chunks
    ]


def test_numpy_semantic_linker_matches_python_path():
    pytest.importorskip("numpy")
    embeddings = [
        [1.0, 0.0, 0.0],
        [0.99, 0.1, 0.0],
        [0.98, 0.12, 0.0],
        [0.0, 1.0, 0.0],
        [0.97, 0.14, 0.0],
    ]
    chunks_python = [
        _chunk("a", 0, parent_id="p1"),
        _chunk("b", 1, parent_id="p1"),
        _chunk("c", 2, parent_id="p2"),
        _chunk("d", 3, parent_id="p3"),
        _chunk("e", 4, parent_id=None),
    ]
    chunks_numpy = [
        _chunk("a", 0, parent_id="p1"),
        _chunk("b", 1, parent_id="p1"),
        _chunk("c", 2, parent_id="p2"),
        _chunk("d", 3, parent_id="p3"),
        _chunk("e", 4, parent_id=None),
    ]

    kwargs = {
        "threshold": 0.8,
        "topk": 2,
        "dup_threshold": 0.995,
        "skip_same_parent": True,
    }
    added_python = _link_semantic_python(
        [(idx, chunk) for idx, chunk in enumerate(chunks_python)],
        embeddings,
        **kwargs,
    )
    added_numpy = _link_semantic_numpy(
        [(idx, chunk) for idx, chunk in enumerate(chunks_numpy)],
        embeddings,
        **kwargs,
    )

    assert added_numpy == added_python
    assert _snapshot(chunks_numpy) == _snapshot(chunks_python)
