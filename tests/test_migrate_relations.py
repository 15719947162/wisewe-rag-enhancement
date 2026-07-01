from scripts.migrate_relations import migrate_for_kb


class _FakeCursor:
    def __init__(self, parent_rows, relation_rows):
        self.parent_rows = parent_rows
        self.relation_rows = relation_rows
        self.executed = []
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.executed.append((sql, params))

    def fetchall(self):
        if "SELECT id, parent_id FROM chunks" in self.last_sql:
            return self.parent_rows
        if "SELECT id, related_ids FROM chunks" in self.last_sql:
            return self.relation_rows
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, parent_rows, relation_rows):
        self.cursor_obj = _FakeCursor(parent_rows, relation_rows)

    def cursor(self):
        return self.cursor_obj


def test_migrate_relations_marks_sibling_when_parent_matches():
    conn = _FakeConn(
        parent_rows=[("a", "p1"), ("b", "p1")],
        relation_rows=[("a", '["b"]')],
    )
    migrated = migrate_for_kb(conn, "kb", dry_run=False)
    assert migrated == 1
    inserts = [item for item in conn.cursor_obj.executed if "INSERT INTO chunk_relations" in item[0]]
    assert inserts
    assert inserts[0][1][3] == "sibling"


def test_migrate_relations_uses_adjacent_when_parent_differs():
    conn = _FakeConn(
        parent_rows=[("a", "p1"), ("b", "p2")],
        relation_rows=[("a", '["b"]')],
    )
    migrated = migrate_for_kb(conn, "kb", dry_run=False)
    assert migrated == 1
    inserts = [item for item in conn.cursor_obj.executed if "INSERT INTO chunk_relations" in item[0]]
    assert inserts[0][1][3] == "adjacent"
