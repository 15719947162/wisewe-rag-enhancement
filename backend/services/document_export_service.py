from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.adapters.kb_adapter import fetch_document_export_record

EXPORT_FIELDNAMES = [
    "documentId",
    "kbId",
    "filename",
    "fileHash",
    "documentChunkCount",
    "chunkId",
    "chunkIndex",
    "page",
    "strategy",
    "layer",
    "title",
    "content",
    "charCount",
    "isTableChunk",
    "isImageChunk",
    "parentId",
    "relatedIds",
    "hasEmbedding",
    "relationCount",
    "relationsJson",
    "tripleCount",
    "triplesJson",
    "documentCreatedAt",
    "documentUpdatedAt",
    "chunkCreatedAt",
]


def export_document_csv(document_id: str) -> tuple[str, bytes]:
    record = fetch_document_export_record(document_id)
    if record is None:
        raise ValueError(f"Document '{document_id}' not found")

    document = record["document"]
    rows = [_build_export_row(document, chunk) for chunk in record["chunks"]]
    filename = _build_export_filename(document["filename"], document_id)
    return filename, _render_csv(rows)


def build_csv_content_disposition(filename: str) -> str:
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "document-chunks.csv"
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(filename)}"


def _build_export_row(document: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    relations = chunk.get("relations") if isinstance(chunk.get("relations"), list) else []
    triples = chunk.get("triples") if isinstance(chunk.get("triples"), list) else []
    page = chunk.get("page")
    return {
        "documentId": document["id"],
        "kbId": document["kb_id"],
        "filename": document["filename"],
        "fileHash": document["file_hash"],
        "documentChunkCount": int(document.get("chunk_count", 0) or 0),
        "chunkId": chunk["id"],
        "chunkIndex": int(chunk.get("chunk_index", 0) or 0),
        "page": int(page) + 1 if page is not None else 0,
        "strategy": chunk.get("strategy", "") or "",
        "layer": chunk.get("layer", "") or "",
        "title": chunk.get("title", "") or "",
        "content": chunk.get("content", "") or "",
        "charCount": int(chunk.get("char_count", 0) or 0),
        "isTableChunk": bool(chunk.get("is_table_chunk")),
        "isImageChunk": bool(chunk.get("is_image_chunk")),
        "parentId": chunk.get("parent_id", "") or "",
        "relatedIds": _normalize_related_ids(chunk.get("related_ids")),
        "hasEmbedding": bool(chunk.get("has_embedding")),
        "relationCount": len(relations),
        "relationsJson": json.dumps(relations, ensure_ascii=False),
        "tripleCount": len(triples),
        "triplesJson": json.dumps(triples, ensure_ascii=False),
        "documentCreatedAt": document["created_at"].isoformat() if document.get("created_at") else "",
        "documentUpdatedAt": document["updated_at"].isoformat() if document.get("updated_at") else "",
        "chunkCreatedAt": chunk["created_at"].isoformat() if chunk.get("created_at") else "",
    }


def _normalize_related_ids(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return "[]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps([raw], ensure_ascii=False)
        return json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
    return "[]"


def _build_export_filename(filename: str, document_id: str) -> str:
    stem = Path(filename or "").stem.strip()
    if not stem:
        stem = f"document-{document_id}"
    safe_stem = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", stem).strip(" ._") or f"document-{document_id}"
    return f"{safe_stem}-chunks.csv"


def _render_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")
