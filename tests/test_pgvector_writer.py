from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.models.content_block import Chunk
from core.output.pgvector_writer import build_chunk_search_text, write_chunks_batch, write_to_pgvector


class _FakeConn:
    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class _CursorConn:
    def __init__(self) -> None:
        self.cursor_obj = object()

    def cursor(self):
        conn = self

        class _Context:
            def __enter__(self):
                return conn.cursor_obj

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Context()


class _RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []
        self.copied: list[tuple[str, str]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))

    def copy_expert(self, sql, file):
        self.copied.append((sql, file.read()))


class _RecordingConn:
    def __init__(self) -> None:
        self.cursor_obj = _RecordingCursor()

    def cursor(self):
        conn = self

        class _Context:
            def __enter__(self):
                return conn.cursor_obj

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Context()


def test_write_to_pgvector_ensures_schema_before_writing():
    conn = _FakeConn()

    with patch("core.output.pgvector_writer.get_db_connection", return_value=conn), patch(
        "core.output.pgvector_writer.ensure_db_schema"
    ) as mocked_ensure, patch(
        "core.output.pgvector_writer.find_document_id_by_hash",
        return_value=None,
    ), patch(
        "core.output.pgvector_writer.upsert_document",
        return_value="doc-1",
    ), patch(
        "core.output.pgvector_writer.compute_file_hash",
        return_value="hash-1",
    ), patch(
        "core.output.pgvector_writer.write_chunks_batch",
        return_value=1,
    ), patch(
        "core.output.pgvector_writer.write_chunk_relations_batch",
        return_value=0,
    ), patch(
        "core.output.pgvector_writer.write_kg_triples_batch",
        return_value=0,
    ):
        result = write_to_pgvector([], [], kb_id="kb-1", filename="demo.pdf")

    mocked_ensure.assert_called_once_with(conn)
    assert result["document_id"] == "doc-1"
    assert result["written"] == 1
    assert conn.committed is True
    assert conn.closed is True


def test_write_to_pgvector_refreshes_filename_when_document_hash_already_exists():
    conn = _FakeConn()
    pdf_path = str(Path("data") / "uploads" / "task-1.pdf")

    with patch("core.output.pgvector_writer.get_db_connection", return_value=conn), patch(
        "core.output.pgvector_writer.ensure_db_schema"
    ), patch(
        "core.output.pgvector_writer.compute_file_hash",
        return_value="hash-existing",
    ), patch(
        "core.output.pgvector_writer.find_document_id_by_hash",
        return_value="doc-existing",
    ), patch(
        "core.output.pgvector_writer.upsert_document",
        return_value="doc-existing",
    ) as mocked_upsert, patch(
        "core.output.pgvector_writer.write_chunks_batch"
    ) as mocked_chunks, patch(
        "core.output.pgvector_writer.write_chunk_relations_batch"
    ) as mocked_relations, patch(
        "core.output.pgvector_writer.write_kg_triples_batch"
    ) as mocked_triples:
        result = write_to_pgvector([], [], kb_id="kb-1", pdf_path=pdf_path, filename="教材原名.pdf")

    mocked_upsert.assert_called_once_with(conn, "kb-1", "教材原名.pdf", "hash-existing", 0)
    mocked_chunks.assert_not_called()
    mocked_relations.assert_not_called()
    mocked_triples.assert_not_called()
    assert result["skipped"] is True
    assert result["document_id"] == "doc-existing"
    assert conn.committed is True
    assert conn.closed is True


def test_write_chunks_batch_uses_execute_values(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_execute_values(cur, sql, rows, template=None, page_size=None):
        calls["cur"] = cur
        calls["sql"] = sql
        calls["rows"] = rows
        calls["template"] = template
        calls["page_size"] = page_size

    monkeypatch.setattr("core.output.pgvector_writer.execute_values", _fake_execute_values)
    monkeypatch.setenv("PGVECTOR_WRITE_PAGE_SIZE", "2")
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "values")

    chunk = Chunk(content="hello", source="demo.pdf", page=1, chunk_index=0)
    conn = _CursorConn()
    written = write_chunks_batch(conn, [chunk], [[0.1, 0.2]], "kb", "doc-1")

    assert written == 1
    assert calls["cur"] is conn.cursor_obj
    assert "VALUES %s" in calls["sql"]
    assert calls["page_size"] == 2
    assert len(calls["rows"]) == 1
    assert "search_text, search_vector" in calls["sql"]
    assert "to_tsvector('simple', %s)" in calls["template"]
    assert calls["rows"][0][-3] == "hello demo.pdf"
    assert calls["rows"][0][-2] == "hello demo.pdf"


def test_write_chunks_batch_copy_mode(monkeypatch):
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "copy")

    chunk = Chunk(content="hello", source="demo.pdf", page=1, chunk_index=0)
    conn = _RecordingConn()

    written = write_chunks_batch(conn, [chunk], [[0.1, 0.2]], "kb", "doc-1")

    assert written == 1
    assert len(conn.cursor_obj.copied) == 1
    copy_sql, csv_text = conn.cursor_obj.copied[0]
    assert "COPY tmp_chunks_upload" in copy_sql
    assert "[0.1,0.2]" in csv_text
    assert any("CREATE TEMP TABLE tmp_chunks_upload" in sql for sql, _ in conn.cursor_obj.executed)
    assert any("embedding_text::vector" in sql for sql, _ in conn.cursor_obj.executed)


def test_build_chunk_search_text_combines_title_content_and_source():
    chunk = Chunk(content="正文", source="doc.pdf", page=1, chunk_index=0, title="标题")

    assert build_chunk_search_text(chunk) == "标题 正文 doc.pdf"
