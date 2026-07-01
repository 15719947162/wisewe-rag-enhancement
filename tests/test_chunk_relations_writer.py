from core.output.pgvector_writer import write_chunk_relations_batch, write_kg_triples_batch
from core.models.content_block import Chunk
from core.models.relation import Relation
from core.models.triple import Triple


class _FakeCursor:
    def __init__(self) -> None:
        self.rows = []

    def executemany(self, _sql, rows):
        self.rows.extend(rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj


class _RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []
        self.copied: list[tuple[str, str]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def copy_expert(self, sql, file):
        self.copied.append((sql, file.read()))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RecordingConn:
    def __init__(self) -> None:
        self.cursor_obj = _RecordingCursor()

    def cursor(self):
        return self.cursor_obj


def test_write_chunk_relations_batch_dedups(monkeypatch):
    monkeypatch.setattr("core.output.pgvector_writer.execute_values", None)
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "values")

    chunk = Chunk(
        content="a",
        source="s",
        page=1,
        chunk_index=0,
        relations=[
            Relation(target_id="b", rel_type="adjacent", source="rule"),
            Relation(target_id="b", rel_type="adjacent", source="rule"),
        ],
    )
    conn = _FakeConn()
    written = write_chunk_relations_batch(conn, [chunk], "kb")
    assert written == 1
    assert len(conn.cursor_obj.rows) == 1


def test_write_chunk_relations_batch_uses_execute_values(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_execute_values(cur, sql, rows, template=None, page_size=None):
        calls["cur"] = cur
        calls["sql"] = sql
        calls["rows"] = rows
        calls["template"] = template
        calls["page_size"] = page_size

    monkeypatch.setattr("core.output.pgvector_writer.execute_values", _fake_execute_values)
    monkeypatch.setenv("PGVECTOR_WRITE_PAGE_SIZE", "3")
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "values")

    chunk = Chunk(
        content="a",
        source="s",
        page=1,
        chunk_index=0,
        relations=[Relation(target_id="b", rel_type="adjacent", source="rule")],
    )
    conn = _FakeConn()

    written = write_chunk_relations_batch(conn, [chunk], "kb")

    assert written == 1
    assert calls["cur"] is conn.cursor_obj
    assert "VALUES %s" in calls["sql"]
    assert calls["template"] == "(%s,%s,%s,%s,%s,%s,%s)"
    assert calls["page_size"] == 3
    assert calls["rows"] == [("kb", chunk.id, "b", "adjacent", 1.0, "rule", "")]


def test_write_chunk_relations_batch_copy_mode(monkeypatch):
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "copy")

    chunk = Chunk(
        content="a",
        source="s",
        page=1,
        chunk_index=0,
        relations=[Relation(target_id="b", rel_type="adjacent", source="rule")],
    )
    conn = _RecordingConn()

    written = write_chunk_relations_batch(conn, [chunk], "kb")

    assert written == 1
    assert len(conn.cursor_obj.copied) == 1
    copy_sql, csv_text = conn.cursor_obj.copied[0]
    assert "COPY tmp_chunk_relations_upload" in copy_sql
    assert "adjacent" in csv_text
    assert any("CREATE TEMP TABLE tmp_chunk_relations_upload" in sql for sql, _ in conn.cursor_obj.executed)
    assert any("FROM tmp_chunk_relations_upload" in sql for sql, _ in conn.cursor_obj.executed)


def test_write_kg_triples_batch_copy_mode(monkeypatch):
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "copy")

    chunk = Chunk(
        content="a",
        source="s",
        page=1,
        chunk_index=0,
        layer="enhanced",
        parent_id="parent-1",
        extracted_triples=[Triple(s="A", p="is", o="B", confidence=0.8)],
    )
    conn = _RecordingConn()

    written = write_kg_triples_batch(conn, [chunk], "kb")

    assert written == 1
    assert len(conn.cursor_obj.copied) == 1
    copy_sql, csv_text = conn.cursor_obj.copied[0]
    assert "COPY tmp_kg_triples_upload" in copy_sql
    assert "parent-1" in csv_text
    assert any("CREATE TEMP TABLE tmp_kg_triples_upload" in sql for sql, _ in conn.cursor_obj.executed)
    assert any("FROM tmp_kg_triples_upload" in sql for sql, _ in conn.cursor_obj.executed)


def test_write_kg_triples_batch_uses_execute_values(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_execute_values(cur, sql, rows, template=None, page_size=None):
        calls["cur"] = cur
        calls["sql"] = sql
        calls["rows"] = rows
        calls["template"] = template
        calls["page_size"] = page_size

    monkeypatch.setattr("core.output.pgvector_writer.execute_values", _fake_execute_values)
    monkeypatch.setenv("PGVECTOR_WRITE_PAGE_SIZE", "4")
    monkeypatch.setenv("PGVECTOR_WRITE_MODE", "values")

    chunk = Chunk(
        content="a",
        source="s",
        page=1,
        chunk_index=0,
        layer="enhanced",
        parent_id="parent-1",
        extracted_triples=[Triple(s="A", p="是", o="B", confidence=0.8)],
    )
    conn = _FakeConn()

    written = write_kg_triples_batch(conn, [chunk], "kb")

    assert written == 1
    assert calls["cur"] is conn.cursor_obj
    assert "VALUES %s" in calls["sql"]
    assert calls["template"] == "(%s,%s,%s,%s,%s,%s)"
    assert calls["page_size"] == 4
    assert calls["rows"] == [("kb", "A", "是", "B", 0.8, "parent-1")]
