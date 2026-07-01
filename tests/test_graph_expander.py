from unittest.mock import patch

from core.rag.graph_expander import graph_expand


def test_graph_expand_returns_neighbors():
    class FakeCursor:
        def __init__(self):
            self.sql = ""

        def execute(self, _sql, _params):
            self.sql = _sql
            return None

        def fetchall(self):
            if "FROM entity_mentions" in self.sql:
                return [("c",)]
            return [("b", "mentions", 1.0)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

    with patch("core.rag.graph_expander.get_db_connection", return_value=FakeConn()):
        results = graph_expand(["a"], "kb", "procedure", max_hops=1, max_neighbors=5)
    assert results[0]["id"] == "c"
    assert results[0]["path"][-1]["rel_type"] == "mentioned_in"
