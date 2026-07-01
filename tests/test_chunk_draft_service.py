from __future__ import annotations

from unittest.mock import patch

from backend.services.chunk_draft_service import load_confirmable_chunks


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _sql, _params=None):
        return None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.rows)

    def close(self):
        return None


def test_load_confirmable_chunks_preserves_enhanced_metadata():
    rows = [
        (
            "chunk-1",
            "summary text",
            "demo.pdf",
            3,
            0,
            "hierarchical",
            "章节A",
            False,
            False,
            "enhanced",
            "parent-1",
            ["chunk-2"],
            "data/output/images/fig1.jpg",
            "enhanced body",
            [{"name": "细胞", "type": "Concept", "aliases": ["cell"]}],
            [{"s": "细胞", "p": "属于", "o": "生物", "confidence": 0.9, "source_chunk": "chunk-1"}],
            [{"target_id": "chunk-2", "rel_type": "adjacent", "weight": 1.0, "source": "rule", "evidence": ""}],
        )
    ]

    with patch("backend.services.chunk_draft_service.cleanup_expired_chunk_drafts"), patch(
        "backend.services.chunk_draft_service.get_db_connection",
        return_value=_FakeConn(rows),
    ), patch("backend.services.chunk_draft_service.ensure_db_schema"):
        chunks = load_confirmable_chunks("task-1")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.layer == "enhanced"
    assert chunk.image_path == "data/output/images/fig1.jpg"
    assert chunk.enhanced_text == "enhanced body"
    assert chunk.extracted_entities[0].name == "细胞"
    assert chunk.extracted_triples[0].s == "细胞"
    assert chunk.relations[0].target_id == "chunk-2"
