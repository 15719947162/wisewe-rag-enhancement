from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag.reranker import ParentChildReranker, _aggregate_to_parents, _rerank_children, _window_reorder


def test_rerank_children_only_child_layer():
    candidates = [
        {"id": "1", "content": "child", "layer": "child", "score": 0.4},
        {"id": "2", "content": "parent", "layer": "parent", "score": 0.7},
    ]
    with patch("core.rag.reranker._call_reranker_api", return_value=[0.9]) as mock_call:
        _rerank_children("query", candidates)
    docs = mock_call.call_args[0][1]
    assert docs == ["child"]


def test_rerank_children_api_failure_fallback():
    candidates = [{"id": "1", "content": "child", "layer": "child", "score": 0.4}]
    with patch("core.rag.reranker._call_reranker_api", return_value=[0.0]):
        reranked = _rerank_children("query", candidates)
    assert reranked[0]["rerank_score"] == 0.4


def test_aggregate_to_parents_max_score():
    candidates = [
        {"id": "c1", "layer": "child", "parent_id": "p1", "rerank_score": 0.4, "dense_score": 0.2},
        {"id": "c2", "layer": "child", "parent_id": "p1", "rerank_score": 0.8, "dense_score": 0.6},
    ]

    class FakeCursor:
        description = [
            ("id",), ("content",), ("source",), ("page",), ("chunk_index",),
            ("layer",), ("parent_id",), ("related_ids",),
        ]

        def execute(self, sql, params):
            assert "ANY(%s)" in sql

        def fetchall(self):
            return [("p1", "parent content", "doc.pdf", 3, 0, "parent", None, "[]")]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    with patch("core.rag.reranker.get_db_connection", return_value=FakeConn()):
        aggregated = _aggregate_to_parents(candidates, "kb")
    parent = aggregated[0]
    assert parent["layer"] == "parent"
    assert parent["rerank_score"] == 0.8


def test_window_reorder_context_window_exists():
    candidates = [{"id": "p1", "layer": "parent", "content": "parent", "best_child_id": "c2"}]

    class FakeCursor:
        def execute(self, sql, params):
            self.sql = sql

        def fetchall(self):
            return [("c1", "part 1", 1), ("c2", "part 2", 2), ("c3", "part 3", 3)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    with patch("core.rag.reranker.get_db_connection", return_value=FakeConn()):
        windowed = _window_reorder(candidates, "kb")
    assert "context_window" in windowed[0]
    assert "part 2" in windowed[0]["context_window"]


def test_reranker_empty_candidates():
    assert ParentChildReranker().rerank("query", [], "kb") == []
