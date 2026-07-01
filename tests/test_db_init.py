from __future__ import annotations

from unittest.mock import patch

from core.db.init_db import _DESCRIPTIONS, ensure_db_schema
from core.db.schema import INIT_SQLS


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self) -> None:
        self.autocommit = False
        self.cursor_obj = _FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_ensure_db_schema_runs_once_per_process():
    conn = _FakeConn()

    with patch("core.db.init_db._SCHEMA_READY", False):
        first = ensure_db_schema(conn)
        second = ensure_db_schema(conn)

    assert first is True
    assert second is False
    assert conn.cursor_obj.executed
    assert len(conn.cursor_obj.executed) == len(INIT_SQLS)


def test_init_sql_descriptions_cover_all_statements():
    assert len(_DESCRIPTIONS) == len(INIT_SQLS)


def test_ensure_db_schema_closes_owned_connection():
    conn = _FakeConn()

    with patch("core.db.init_db._SCHEMA_READY", False), patch(
        "core.db.init_db.get_db_connection",
        return_value=conn,
    ):
        executed = ensure_db_schema()

    assert executed is True
    assert conn.closed is True
