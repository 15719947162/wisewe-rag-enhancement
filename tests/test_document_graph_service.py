from __future__ import annotations

from backend.adapters.kb_adapter import _build_document_graph_payload, _select_graph_preview_chunk_ids


def test_build_document_graph_payload_includes_chunks_relations_entities_and_triples() -> None:
    payload = _build_document_graph_payload(
        document_id="doc-1",
        filename="教材.pdf",
        chunk_rows=[
            ("c1", "正文提到图1-1", "教材.pdf", 1, 0, "正文", "child", False, False, ""),
            ("c2", "图1-1 示意图", "教材.pdf", 1, 1, "图1-1", "child", False, True, "data/output/images/fig.png"),
        ],
        relation_rows=[("c1", "c2", "refers_to", 1.0, "rule", "图1-1")],
        mention_rows=[("c1", "e1", "血液检查", "concept", "医学检验项目")],
        triple_rows=[(1, "血液检查", "包含", "血常规", 0.8, "c1")],
        limit=100,
    )

    assert payload["documentId"] == "doc-1"
    assert payload["stats"]["chunkCount"] == 2
    assert payload["stats"]["tripleCount"] == 1
    node_ids = {node["id"] for node in payload["nodes"]}
    assert {"chunk:c1", "chunk:c2", "entity:e1", "entity:triple:血液检查", "entity:triple:血常规"} <= node_ids
    edge_types = {edge["type"] for edge in payload["edges"]}
    assert {"refers_to", "mentions", "triple", "triple_source"} <= edge_types
    image_node = next(node for node in payload["nodes"] if node["id"] == "chunk:c2")
    assert image_node["chunkType"] == "image"
    assert image_node["meta"]["imagePath"] == "data/output/images/fig.png"


def test_build_document_graph_payload_truncates_nodes() -> None:
    payload = _build_document_graph_payload(
        document_id="doc-1",
        filename="教材.pdf",
        chunk_rows=[
            ("c1", "A", "教材.pdf", 1, 0, "", "child", False, False, ""),
            ("c2", "B", "教材.pdf", 1, 1, "", "child", False, False, ""),
        ],
        relation_rows=[],
        mention_rows=[],
        triple_rows=[],
        limit=1,
    )

    assert len(payload["nodes"]) == 1
    assert payload["stats"]["truncated"] is True


def test_build_document_graph_payload_uses_chunk_document_metadata_when_present() -> None:
    payload = _build_document_graph_payload(
        document_id="kb:demo",
        filename="Demo KB",
        chunk_rows=[
            ("c1", "A", "doc-a.pdf", 1, 0, "", "child", False, False, "", "doc-a", "doc-a.pdf"),
            ("c2", "B", "doc-b.pdf", 1, 0, "", "child", False, False, "", "doc-b", "doc-b.pdf"),
        ],
        relation_rows=[],
        mention_rows=[],
        triple_rows=[],
        limit=10,
    )

    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["chunk:c1"]["meta"]["documentId"] == "doc-a"
    assert by_id["chunk:c1"]["meta"]["filename"] == "doc-a.pdf"
    assert by_id["chunk:c2"]["meta"]["documentId"] == "doc-b"
    assert by_id["chunk:c2"]["meta"]["filename"] == "doc-b.pdf"


def test_select_graph_preview_chunk_ids_prioritizes_relation_pairs() -> None:
    selected = _select_graph_preview_chunk_ids(
        [
            ("c1", "c2"),
            ("c2", "c3"),
            ("c4", "c5"),
        ],
        limit=4,
    )

    assert selected == ["c1", "c2", "c3", "c4"]
