from __future__ import annotations

from unittest.mock import patch

from core.rag.retrieval_snapshot import (
    expand_related_snapshot,
    fetch_retrieval_snapshot,
    fold_enhanced_snapshot,
    snapshot_row_to_candidate,
)


def test_snapshot_row_to_candidate_preserves_payload_fields() -> None:
    candidate = snapshot_row_to_candidate(
        {
            "id": "c1",
            "content": "content",
            "source": "doc.pdf",
            "document_id": "doc-1",
            "document_name": "教材.pdf",
            "page": 2,
            "chunk_index": 3,
            "layer": "child",
            "parent_id": None,
            "related_ids": '["c2"]',
            "title": "标题",
            "is_table_chunk": True,
            "is_image_chunk": False,
            "image_path": None,
            "dense_score": 0.9,
            "dense_rank": 1,
            "sparse_score": 0.5,
            "sparse_rank": 2,
            "sources": ["embedding", "bm25"],
            "snapshot_role": "base",
        }
    )

    assert candidate["id"] == "c1"
    assert candidate["document_name"] == "教材.pdf"
    assert candidate["related_ids"] == ["c2"]
    assert candidate["is_table_chunk"] is True
    assert candidate["dense_score"] == 0.9
    assert candidate["sparse_score"] == 0.5
    assert candidate["sources"] == ["embedding", "bm25"]
    assert candidate["rrf_score"] > 0
    assert candidate["score"] >= 0.9


def test_snapshot_row_to_candidate_keeps_relevance_scale_for_min_score() -> None:
    candidate = snapshot_row_to_candidate(
        {
            "id": "c1",
            "content": "针灸治疗学是在熟悉经络、腧穴基础上发展出的临床学科。",
            "page": 257,
            "chunk_index": 1711,
            "layer": "child",
            "dense_score": 0.753,
            "dense_rank": 1,
            "sparse_score": None,
            "sparse_rank": None,
            "snapshot_role": "base",
        }
    )

    assert candidate["score"] >= 0.753
    assert candidate["score"] > 0.3


def test_fetch_retrieval_snapshot_uses_one_candidate_query() -> None:
    class _Cursor:
        description = [
            ("id",),
            ("content",),
            ("source",),
            ("document_id",),
            ("document_name",),
            ("page",),
            ("layer",),
            ("parent_id",),
            ("related_ids",),
            ("chunk_index",),
            ("title",),
            ("is_table_chunk",),
            ("is_image_chunk",),
            ("image_path",),
            ("dense_score",),
            ("dense_rank",),
            ("sparse_score",),
            ("sparse_rank",),
            ("sources",),
            ("snapshot_role",),
        ]

        def __init__(self) -> None:
            self.execute_calls = 0
            self.sql = ""

        def execute(self, sql, _params=None):
            self.execute_calls += 1
            self.sql = sql

        def fetchall(self):
            return [
                (
                    "c1",
                    "content",
                    "doc.pdf",
                    "doc-1",
                    "教材.pdf",
                    1,
                    "child",
                    None,
                    "[]",
                    0,
                    "title",
                    False,
                    False,
                    None,
                    0.9,
                    1,
                    None,
                    None,
                    ["embedding"],
                    "base",
                )
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def __init__(self) -> None:
            self.cursor_obj = _Cursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    conn = _Conn()
    with patch("core.rag.retrieval_snapshot.get_db_connection", return_value=conn):
        candidates, snapshot_by_id = fetch_retrieval_snapshot("query", [0.1, 0.2], "kb")

    assert conn.cursor_obj.execute_calls == 1
    assert "WITH dense AS" in conn.cursor_obj.sql
    assert "FULL OUTER JOIN sparse" in conn.cursor_obj.sql
    assert candidates[0]["id"] == "c1"
    assert snapshot_by_id["c1"]["document_name"] == "教材.pdf"
    assert conn.closed is True


def test_fold_enhanced_snapshot_returns_child_evidence() -> None:
    enhanced = {
        "id": "e1",
        "content": "[图像描述] 图1-1",
        "layer": "enhanced",
        "parent_id": "c1",
        "score": 0.8,
        "dense_score": 0.7,
        "sparse_score": 0.0,
        "sources": ["embedding"],
        "matched_by": [],
    }
    child = {
        "id": "c1",
        "content": "[图片 第1页]",
        "source": "doc.pdf",
        "document_name": "教材.pdf",
        "page": 1,
        "chunk_index": 0,
        "layer": "child",
        "parent_id": None,
        "related_ids": [],
        "is_image_chunk": True,
        "image_path": "data/output/images/fig.png",
        "score": 0,
        "dense_score": 0,
        "sparse_score": 0,
        "sources": [],
        "matched_by": [],
    }

    folded = fold_enhanced_snapshot([enhanced], {"e1": enhanced, "c1": child})

    assert folded[0]["id"] == "c1"
    assert folded[0]["matched_enhanced_id"] == "e1"
    assert folded[0]["score"] == 0.8 * 0.95
    assert folded[0]["is_image_chunk"] is True


def test_expand_related_snapshot_uses_prefetched_rows() -> None:
    base = {"id": "c1", "related_ids": ["c2"], "score": 0.8, "dense_score": 0.8, "matched_by": [], "sources": []}
    related = {
        "id": "c2",
        "content": "related",
        "related_ids": [],
        "score": 0.0,
        "dense_score": 0.0,
        "matched_by": [],
        "sources": [],
    }

    expanded = expand_related_snapshot([base], {"c1": base, "c2": related})

    assert [item["id"] for item in expanded] == ["c1", "c2"]
    assert expanded[1]["score"] == 0.8 * 0.85
    assert expanded[1]["matched_by"] == ["related"]


def test_expand_related_snapshot_downranks_duplicate_related_images() -> None:
    base = {
        "id": "c1",
        "related_ids": ["img-1", "img-2"],
        "score": 0.8,
        "dense_score": 0.8,
        "matched_by": [],
        "sources": [],
    }
    image_one = {
        "id": "img-1",
        "content": "[image p1]",
        "document_id": "doc",
        "page": 1,
        "related_ids": [],
        "is_image_chunk": True,
        "score": 0.0,
        "dense_score": 0.0,
        "matched_by": [],
        "sources": [],
    }
    image_two = {
        **image_one,
        "id": "img-2",
        "content": "[image p1 duplicate]",
    }

    expanded = expand_related_snapshot(
        [base],
        {"c1": base, "img-1": image_one, "img-2": image_two},
    )

    assert [item["id"] for item in expanded] == ["c1", "img-1"]
    assert expanded[1]["score"] == 0.8 * 0.45
    assert expanded[1]["matched_by"] == ["related"]
