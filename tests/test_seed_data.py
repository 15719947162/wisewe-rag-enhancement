from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.db.seed_data import (
    BASE_RUNTIME_SETTINGS,
    PROFILE_ALL,
    PROFILE_BASE,
    PROFILE_DEMO,
    PROFILE_INTEGRATION_TEMPLATE,
    build_seed_plan,
    normalize_profiles,
    seed_database,
)


class _SeedCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._rows: list[tuple] = []
        self._one: tuple | None = None

    def execute(self, sql, params=None):
        params_tuple = tuple(params or ())
        self.executed.append((sql, params_tuple))
        normalized_sql = " ".join(str(sql).split())
        self._rows = []
        self._one = None
        if "RETURNING key" in normalized_sql:
            self._one = (params_tuple[0],)
        elif "RETURNING id" in normalized_sql:
            self._one = (params_tuple[0],)
        elif "RETURNING tenant_id" in normalized_sql:
            self._one = (params_tuple[1],)
        elif "RETURNING user_id" in normalized_sql:
            self._one = (params_tuple[2] if "kb_identity_user_roles" in normalized_sql else params_tuple[1],)
        elif "RETURNING role_id" in normalized_sql:
            self._one = (params_tuple[1],)
        elif "RETURNING entity_id" in normalized_sql:
            self._one = (params_tuple[0],)
        elif normalized_sql.startswith("SELECT key, value FROM console_settings"):
            self._rows = [(key, value) for key, value in BASE_RUNTIME_SETTINGS.items()]
        elif normalized_sql.startswith("SELECT id FROM kg_triples"):
            self._one = None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        row = self._one
        self._one = None
        return row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _SeedConn:
    def __init__(self) -> None:
        self.cursor_obj = _SeedCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_normalize_profiles_expands_all_and_deduplicates() -> None:
    assert normalize_profiles([PROFILE_ALL]) == (PROFILE_BASE, PROFILE_INTEGRATION_TEMPLATE, PROFILE_DEMO)
    assert normalize_profiles([PROFILE_BASE, PROFILE_BASE]) == (PROFILE_BASE,)


def test_build_seed_plan_defaults_to_base() -> None:
    plan = build_seed_plan()

    assert any(item.table == "console_settings" for item in plan)
    assert any(item.key == "bootstrap" for item in plan)


def test_seed_database_dry_run_does_not_connect() -> None:
    with patch("core.db.seed_data.get_db_connection") as mocked:
        result = seed_database(profiles=[PROFILE_BASE])

    mocked.assert_not_called()
    assert result["dryRun"] is True
    assert result["changedCount"] == 0


def test_seed_base_apply_writes_settings_and_version() -> None:
    conn = _SeedConn()

    with patch("core.db.seed_data.ensure_db_schema") as ensure:
        result = seed_database(profiles=[PROFILE_BASE], apply=True, conn=conn)

    ensure.assert_called_once_with(conn)
    assert conn.committed is True
    assert result["dryRun"] is False
    assert result["changedCount"] >= len(BASE_RUNTIME_SETTINGS)
    executed_sql = "\n".join(sql for sql, _ in conn.cursor_obj.executed)
    assert "INSERT INTO console_settings" in executed_sql
    assert "INSERT INTO console_settings_versions" in executed_sql
    assert "KB_TOKEN_MODEL_RATES_JSON" not in json.dumps(result, ensure_ascii=False)


def test_integration_template_refuses_secret_without_explicit_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_SSO_CLIENT_SECRET", "secret-value")

    with pytest.raises(ValueError, match="SEED_SSO_CLIENT_SECRET"):
        seed_database(profiles=[PROFILE_INTEGRATION_TEMPLATE], apply=True, conn=_SeedConn())


def test_integration_template_apply_omits_secret_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEED_SSO_CLIENT_SECRET", raising=False)
    conn = _SeedConn()

    result = seed_database(profiles=[PROFILE_INTEGRATION_TEMPLATE], apply=True, conn=conn)

    assert result["changedCount"] == 2
    external_insert = next(
        params
        for sql, params in conn.cursor_obj.executed
        if "INSERT INTO kb_external_system_configs" in sql
    )
    assert external_insert[7] == ""


def test_demo_seed_uses_insert_only_by_default() -> None:
    conn = _SeedConn()

    result = seed_database(profiles=[PROFILE_DEMO], apply=True, conn=conn)

    assert result["changedCount"] > 0
    executed_sql = "\n".join(sql for sql, _ in conn.cursor_obj.executed)
    assert "ON CONFLICT(id) DO NOTHING" in executed_sql
    assert "INSERT INTO chunks" in executed_sql
