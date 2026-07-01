from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag.retriever import _coarse_filter, _entity_retrieve, _expand_related, _rrf_merge


def test_rrf_merge_dedup():
    dense = [{"id": "a", "score": 0.9, "dense_score": 0.9}, {"id": "b", "score": 0.8, "dense_score": 0.8}]
    sparse = [{"id": "a", "score": 0.7, "dense_score": 0.0}, {"id": "c", "score": 0.6, "dense_score": 0.0}]
    merged = _rrf_merge(dense, sparse, [])
    assert [item["id"] for item in merged].count("a") == 1


def test_rrf_merge_structured_bonus():
    dense = [{"id": "a", "score": 0.9, "dense_score": 0.9}]
    structured = [{"id": "b", "score": 1.0, "dense_score": 0.0}]
    merged = _rrf_merge(dense, [], structured)
    by_id = {item["id"]: item["score"] for item in merged}
    assert by_id["b"] > by_id["a"]


def test_coarse_filter_threshold():
    candidates = [{"id": "a", "score": 0.2}, {"id": "b", "score": 0.4}]
    filtered = _coarse_filter(candidates, min_score=0.3, top_n=20)
    assert [item["id"] for item in filtered] == ["b"]


def test_coarse_filter_top_n():
    candidates = [{"id": str(i), "score": 1.0 - i * 0.1} for i in range(5)]
    filtered = _coarse_filter(candidates, min_score=0.0, top_n=2)
    assert len(filtered) == 2


def test_expand_related_dedup():
    candidates = [{"id": "a", "score": 0.8, "related_ids": ["a", "b"], "dense_score": 0.8}]

    class FakeCursor:
        description = [("id",), ("content",), ("source",), ("page",), ("layer",), ("parent_id",), ("related_ids",)]

        def execute(self, sql, params):
            assert "ANY(%s)" in sql
            self.params = params

        def fetchall(self):
            return [("b", "related", "doc.pdf", 1, "child", None, "[]")]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    with patch("core.rag.retriever.get_db_connection", return_value=FakeConn()):
        expanded = _expand_related(candidates, "kb")
    ids = [item["id"] for item in expanded]
    assert ids.count("a") == 1
    assert ids.count("b") == 1


def test_entity_retrieve_returns_chunk_hits():
    class FakeCursor:
        def __init__(self):
            self.step = 0
            self.description = []

        def execute(self, _sql, _params):
            self.step += 1
            if self.step == 1:
                self.description = [
                    ("id",),
                    ("name",),
                    ("aliases",),
                    ("type",),
                    ("definition",),
                    ("emb_score",),
                ]
            elif self.step == 2:
                self.description = [("entity_id",), ("chunk_id",)]
            else:
                self.description = [
                    ("id",),
                    ("content",),
                    ("source",),
                    ("page",),
                    ("layer",),
                    ("parent_id",),
                    ("related_ids",),
                    ("title",),
                    ("is_table_chunk",),
                    ("chunk_index",),
                ]

        def fetchall(self):
            if self.step == 1:
                return [("e1", "应急预案", ["预案"], "Concept", "定义", 0.95)]
            if self.step == 2:
                return [("e1", "c1")]
            return [("c1", "命中文本", "doc.pdf", 2, "child", None, "[]", "", False, 1)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    with patch("core.rag.retriever.get_db_connection", return_value=FakeConn()), patch(
        "core.rag.retriever._fetch_chunks_by_ids",
        return_value=[{"id": "c1", "content": "命中文本", "source": "doc.pdf", "page": 2, "layer": "child", "parent_id": None, "related_ids": []}],
    ):
        hits = _entity_retrieve("什么是应急预案", "kb", [0.1, 0.2], 5)
    assert len(hits) == 1
    assert hits[0]["id"] == "c1"
    assert hits[0]["entity"]["name"] == "应急预案"
