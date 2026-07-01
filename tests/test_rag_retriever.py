from __future__ import annotations

from unittest.mock import patch

from core.rag import retriever
from core.rag.retriever import (
    HybridRetriever,
    _build_bm25_index,
    _extract_media_ref_query,
    _fold_enhanced_to_children,
    _media_ref_retrieve,
    _sparse_retrieve,
)


class _FakeCursor:
    description = [
        ("id",),
        ("content",),
        ("source",),
        ("page",),
        ("layer",),
        ("parent_id",),
        ("related_ids",),
    ]

    def execute(self, _sql, _params=None):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        return None


def test_extract_media_ref_query_normalizes_number() -> None:
    assert _extract_media_ref_query("帮我找 图 １－３－３") == ("图", "图133")
    assert _extract_media_ref_query("表1-3-1在哪里") == ("表", "表131")
    assert _extract_media_ref_query("高血压怎么判断") is None


def test_build_bm25_index_handles_empty_kb() -> None:
    with patch("core.rag.retriever.get_db_connection", return_value=_FakeConn()):
        _index, docs = _build_bm25_index("empty-kb")

    assert docs == []


def test_build_bm25_index_filters_to_child_and_enhanced_layers() -> None:
    class _Cursor(_FakeCursor):
        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

    class _Conn:
        cursor_obj = _Cursor()

        def cursor(self):
            return self.cursor_obj

        def close(self):
            return None

    conn = _Conn()
    with patch("core.rag.retriever.get_db_connection", return_value=conn):
        _build_bm25_index("kb")

    assert "c.layer = ANY" in conn.cursor_obj.sql
    assert conn.cursor_obj.params[1] == ["child", "enhanced"]


def test_sparse_retrieve_returns_empty_for_empty_kb() -> None:
    with patch("core.rag.retriever.get_db_connection", return_value=_FakeConn()):
        results = _sparse_retrieve("测试", "empty-kb")

    assert results == []


def test_sparse_retrieve_handles_numpy_like_scores() -> None:
    class _Scores:
        def __iter__(self):
            return iter([0.0, 2.0])

        def __bool__(self):
            raise ValueError("truth value is ambiguous")

    class _Index:
        def get_scores(self, _tokens):
            return _Scores()

    docs = [
        {
            "id": "low",
            "content": "unrelated",
            "source": "doc.pdf",
            "page": 1,
            "layer": "child",
            "parent_id": None,
            "related_ids": [],
            "score": 0.0,
            "dense_score": 0.0,
        },
        {
            "id": "hit",
            "content": "target",
            "source": "doc.pdf",
            "page": 2,
            "layer": "child",
            "parent_id": None,
            "related_ids": [],
            "score": 0.0,
            "dense_score": 0.0,
        },
    ]

    retriever._bm25_cache.pop("numpy-like-kb", None)
    with patch("core.rag.retriever._build_bm25_index", return_value=(_Index(), docs)):
        results = _sparse_retrieve("target", "numpy-like-kb")

    assert [item["id"] for item in results] == ["hit"]
    assert results[0]["score"] == 1.0


def test_fold_enhanced_to_children_returns_source_child() -> None:
    enhanced = {
        "id": "e1",
        "content": "[图片描述] 血液检查结果判读示意图",
        "source": "temp.pdf",
        "document_name": "带表格的教材片段.pdf",
        "page": 1,
        "layer": "enhanced",
        "parent_id": "c1",
        "score": 0.8,
        "dense_score": 0.7,
        "related_ids": [],
    }
    child = {
        "id": "c1",
        "content": "[图片 第1页]",
        "source": "temp.pdf",
        "document_name": "带表格的教材片段.pdf",
        "page": 1,
        "chunk_index": 2,
        "layer": "child",
        "parent_id": "p1",
        "related_ids": [],
        "is_image_chunk": True,
        "image_path": "data/output/images/page-1.png",
    }

    with patch("core.rag.retriever._fetch_chunks_by_ids", return_value=[child]):
        folded = _fold_enhanced_to_children([enhanced], "kb")

    assert folded[0]["id"] == "c1"
    assert folded[0]["layer"] == "child"
    assert folded[0]["matched_enhanced_id"] == "e1"
    assert folded[0]["score"] == 0.8 * 0.95
    assert folded[0]["is_image_chunk"] is True
    assert folded[0]["image_path"] == "data/output/images/page-1.png"


def test_media_ref_retrieve_hits_image_without_embedding() -> None:
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
            ("title",),
            ("is_table_chunk",),
            ("is_image_chunk",),
            ("image_path",),
            ("chunk_index",),
        ]

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchall(self):
            return [
                (
                    "img-1",
                    "图1-3-3 血液检查结果判读示意图",
                    "upload.pdf",
                    "doc-1",
                    "教材.pdf",
                    1,
                    "child",
                    "parent-1",
                    [],
                    "",
                    False,
                    True,
                    "data/output/images/fig.png",
                    15,
                )
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        cursor_obj = _Cursor()

        def cursor(self):
            return self.cursor_obj

        def close(self):
            return None

    conn = _Conn()
    with patch("core.rag.retriever.get_db_connection", return_value=conn):
        results = _media_ref_retrieve("图1-3-3", "kb", top_n=5)

    assert "embedding" not in conn.cursor_obj.sql.lower()
    assert conn.cursor_obj.params[1:3] == ("图", "图")
    assert results[0]["id"] == "img-1"
    assert results[0]["retrieval_mode"] == "media_ref"
    assert results[0]["matched_media_ref"] == "图133"
    assert results[0]["is_image_chunk"] is True


def test_hybrid_retriever_media_ref_fast_path_skips_embedding() -> None:
    hit = {
        "id": "img-1",
        "content": "图1-3-3",
        "source": "doc.pdf",
        "page": 1,
        "layer": "child",
        "parent_id": None,
        "related_ids": [],
        "score": 1.0,
        "dense_score": 0.0,
        "retrieval_mode": "media_ref",
    }

    with patch("core.rag.retriever._media_ref_retrieve", return_value=[hit]), patch(
        "core.rag.retriever._expand_related",
        return_value=[hit],
    ), patch("core.rag.retriever.embed_texts") as mocked_embed:
        results = HybridRetriever().retrieve("图1-3-3", "kb")

    mocked_embed.assert_not_called()
    assert results == [hit]


def test_hybrid_retriever_uses_cached_query_embedding() -> None:
    hit = {
        "id": "c1",
        "content": "content",
        "source": "doc.pdf",
        "page": 1,
        "layer": "child",
        "parent_id": None,
        "related_ids": [],
        "score": 1.0,
        "dense_score": 1.0,
    }

    retriever_instance = HybridRetriever()
    with patch("core.rag.retriever._media_ref_retrieve", return_value=[]), patch(
        "core.rag.retriever.embed_query_cached",
        return_value=([0.1, 0.2], True),
    ) as mocked_embed, patch(
        "core.rag.retriever._dense_retrieve",
        return_value=[hit],
    ), patch(
        "core.rag.retriever._sparse_retrieve",
        return_value=[],
    ), patch(
        "core.rag.retriever._structured_retrieve",
        return_value=[],
    ), patch(
        "core.rag.retriever._snapshot_enabled",
        return_value=False,
    ), patch(
        "core.rag.retriever._fold_enhanced_to_children",
        side_effect=lambda items, _kb_id: items,
    ), patch(
        "core.rag.retriever._expand_related",
        side_effect=lambda items, _kb_id: items,
    ):
        results = retriever_instance.retrieve("query", "kb")

    mocked_embed.assert_called_once_with("query")
    assert results[0]["id"] == "c1"
    assert results[0]["sources"] == ["embedding"]
    assert retriever_instance.last_timings["query_embedding_cache_hit"] is True


def test_hybrid_retriever_snapshot_path_uses_single_fetch(monkeypatch) -> None:
    monkeypatch.setenv("RAG_RETRIEVAL_SNAPSHOT", "true")
    base = {
        "id": "c1",
        "content": "content",
        "source": "doc.pdf",
        "page": 1,
        "layer": "child",
        "parent_id": None,
        "related_ids": [],
        "score": 0.8,
        "dense_score": 0.8,
        "sources": ["embedding"],
        "matched_by": [],
    }

    retriever_instance = HybridRetriever()
    with patch("core.rag.retriever._media_ref_retrieve", return_value=[]), patch(
        "core.rag.retriever.embed_query_cached",
        return_value=([0.1, 0.2], False),
    ), patch(
        "core.rag.retriever.fetch_retrieval_snapshot",
        return_value=([base], {"c1": base}),
    ) as mocked_snapshot, patch(
        "core.rag.retriever._dense_retrieve"
    ) as mocked_dense:
        results = retriever_instance.retrieve("query", "kb")

    mocked_snapshot.assert_called_once()
    mocked_dense.assert_not_called()
    assert results[0]["id"] == "c1"
    assert "snapshot" in retriever_instance.last_timings


def test_hybrid_retriever_snapshot_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RAG_RETRIEVAL_SNAPSHOT", "false")
    hit = {
        "id": "c1",
        "content": "content",
        "source": "doc.pdf",
        "page": 1,
        "layer": "child",
        "parent_id": None,
        "related_ids": [],
        "score": 1.0,
        "dense_score": 1.0,
    }

    with patch("core.rag.retriever._media_ref_retrieve", return_value=[]), patch(
        "core.rag.retriever.embed_query_cached",
        return_value=([0.1, 0.2], False),
    ), patch(
        "core.rag.retriever.fetch_retrieval_snapshot"
    ) as mocked_snapshot, patch(
        "core.rag.retriever._dense_retrieve",
        return_value=[hit],
    ), patch(
        "core.rag.retriever._sparse_retrieve",
        return_value=[],
    ), patch(
        "core.rag.retriever._structured_retrieve",
        return_value=[],
    ), patch(
        "core.rag.retriever._fold_enhanced_to_children",
        side_effect=lambda items, _kb_id: items,
    ), patch(
        "core.rag.retriever._expand_related",
        side_effect=lambda items, _kb_id: items,
    ):
        results = HybridRetriever().retrieve("query", "kb")

    mocked_snapshot.assert_not_called()
    assert results[0]["id"] == "c1"
