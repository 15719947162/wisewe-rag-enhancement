from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.services.document_export_service import (
    build_csv_content_disposition,
    export_document_csv,
)


def test_export_document_csv_renders_rows_with_relations_and_triples() -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    record = {
        "document": {
            "id": "doc-1",
            "kb_id": "kb-1",
            "filename": "教材样例.pdf",
            "file_hash": "hash-1",
            "chunk_count": 2,
            "created_at": now,
            "updated_at": now,
        },
        "chunks": [
            {
                "id": "chunk-1",
                "kb_id": "kb-1",
                "document_id": "doc-1",
                "source": "教材样例.pdf",
                "page": 3,
                "chunk_index": 0,
                "strategy": "hierarchical",
                "title": "第一章",
                "content": "正式入库切片",
                "layer": "child",
                "parent_id": "",
                "related_ids": '["chunk-2"]',
                "char_count": 6,
                "is_table_chunk": False,
                "is_image_chunk": False,
                "has_embedding": True,
                "created_at": now,
                "relations": [{"targetId": "chunk-2", "relType": "adjacent"}],
                "triples": [{"s": "细胞", "p": "属于", "o": "生物", "confidence": 0.9}],
            }
        ],
    }

    with patch("backend.services.document_export_service.fetch_document_export_record", return_value=record):
        filename, content = export_document_csv("doc-1")

    assert filename == "教材样例-chunks.csv"
    text = content.decode("utf-8-sig")
    assert "documentId,kbId,filename,fileHash,documentChunkCount" in text
    assert "正式入库切片" in text
    assert ",4,hierarchical," in text
    assert "chunk-2" in text
    assert '"adjacent"' in text
    assert '"细胞"' in text
    assert "0.9" in text


def test_export_document_csv_raises_when_document_missing() -> None:
    with patch("backend.services.document_export_service.fetch_document_export_record", return_value=None):
        with pytest.raises(ValueError, match="Document 'doc-missing' not found"):
            export_document_csv("doc-missing")


def test_build_csv_content_disposition_supports_utf8_filename() -> None:
    header = build_csv_content_disposition("教材样例-chunks.csv")
    assert "attachment;" in header
    assert "filename*=UTF-8''" in header
