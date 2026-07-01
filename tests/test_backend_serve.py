from __future__ import annotations

from unittest.mock import patch

from backend import serve


def test_ensure_db_schema_before_serving_invokes_init():
    with patch("core.db.init_db.ensure_db_schema") as mocked:
        serve._ensure_db_schema_before_serving()

    mocked.assert_called_once_with()


def test_ensure_db_schema_before_serving_swallow_errors(capsys):
    with patch("core.db.init_db.ensure_db_schema", side_effect=RuntimeError("db down")):
        serve._ensure_db_schema_before_serving()

    captured = capsys.readouterr()
    assert "WARN Database schema auto-init skipped: db down" in captured.out
