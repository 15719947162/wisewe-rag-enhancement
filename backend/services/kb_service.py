from __future__ import annotations

from backend.adapters.kb_adapter import (
    delete_document,
    fetch_document_detail,
    fetch_document_graph,
    fetch_document_source_record,
    fetch_documents,
    fetch_knowledge_base_graph,
    fetch_knowledge_bases,
)
from backend.services.document_export_service import export_document_csv
from core.db.identity import IdentityContext
from core.db.query_logs import AuditLogRecord, append_audit_log


def get_knowledge_bases_payload(identity: IdentityContext | None = None) -> list[dict]:
    items = fetch_knowledge_bases(identity)
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item.get("description", "") or "",
            "strategy": item.get("default_strategy", "") or "hierarchical",
            "tenantId": item.get("tenant_id") or "",
            "createdBy": item.get("created_by") or "",
            "ownerUserId": item.get("owner_user_id") or "",
            "ownerStatus": item.get("owner_status") or "active",
            "ownerInvalidReason": item.get("owner_invalid_reason") or "",
            "status": item.get("status") or "active",
            "createdAt": item["created_at"].isoformat() if item.get("created_at") else "",
            "docCount": int(item.get("doc_count", 0) or 0),
            "chunkCount": int(item.get("chunk_count", 0) or 0),
            "lastUpdated": item["last_updated"].isoformat() if item.get("last_updated") else "",
            "duplicatePolicy": "hash-skip",
        }
        for item in items
    ]


def get_documents_payload(kb_id: str | None = None, identity: IdentityContext | None = None) -> list[dict]:
    rows = fetch_documents(kb_id)
    if identity and identity.enforce_access:
        visible_kb_ids = {str(item["id"]) for item in get_knowledge_bases_payload(identity)}
        rows = [row for row in rows if str(row.get("kb_id") or "") in visible_kb_ids]
    return [
        {
            "id": row["id"],
            "kbId": row["kb_id"],
            "filename": row["filename"],
            "fileHash": row["file_hash"],
            "chunkCount": row["chunk_count"],
            "createdAt": row["created_at"].isoformat() if row["created_at"] else "",
            "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else "",
            "status": "success" if row["chunk_count"] > 0 else "pending",
            "sourceStorage": row.get("source_storage", "unknown") or "unknown",
            "sourceAvailable": bool(row.get("source_path") or row.get("source_url")),
            "parserProvider": row.get("parser_provider", "") or "",
        }
        for row in rows
    ]


def get_document_detail_payload(document_id: str) -> dict:
    record = fetch_document_detail(document_id)
    if record is None:
        raise ValueError(f"Document '{document_id}' not found")

    document = record["document"]
    chunks = record["chunks"]
    hierarchical_layers = sorted({chunk.get("layer", "child") for chunk in chunks if chunk.get("strategy") == "hierarchical"})

    return {
        "document": {
            "id": document["id"],
            "kbId": document["kb_id"],
            "filename": document["filename"],
            "fileHash": document["file_hash"],
            "chunkCount": int(document.get("chunk_count", 0) or 0),
            "createdAt": document["created_at"].isoformat() if document.get("created_at") else "",
            "updatedAt": document["updated_at"].isoformat() if document.get("updated_at") else "",
            "status": "success" if int(document.get("chunk_count", 0) or 0) > 0 else "pending",
            "sourceStorage": document.get("source_storage", "unknown") or "unknown",
            "sourceAvailable": bool(document.get("source_path") or document.get("source_url")),
            "parserProvider": document.get("parser_provider", "") or "",
            "strategy": chunks[0].get("strategy", "") if chunks else "",
            "isHierarchical": any(chunk.get("strategy") == "hierarchical" for chunk in chunks),
            "hierarchicalLayers": hierarchical_layers,
        },
        "chunks": [
            {
                "id": chunk["id"],
                "documentId": chunk["document_id"],
                "kbId": chunk["kb_id"],
                "source": chunk.get("source", "") or "",
                "page": int(chunk.get("page", 0) or 0) + 1,
                "chunkIndex": int(chunk.get("chunk_index", 0) or 0),
                "title": chunk.get("title", "") or "",
                "content": chunk.get("content", "") or "",
                "strategy": chunk.get("strategy", "") or "",
                "layer": chunk.get("layer", "child") or "child",
                "parentId": chunk.get("parent_id", "") or None,
                "relatedIds": _normalize_related_ids(chunk.get("related_ids")),
                "charCount": int(chunk.get("char_count", 0) or 0),
                "isTableChunk": bool(chunk.get("is_table_chunk")),
                "isImageChunk": bool(chunk.get("is_image_chunk")),
                "imagePath": chunk.get("image_path", "") or None,
                "hasEmbedding": bool(chunk.get("has_embedding")),
                "createdAt": chunk["created_at"].isoformat() if chunk.get("created_at") else "",
                "relations": chunk.get("relations", []) if isinstance(chunk.get("relations"), list) else [],
                "triples": chunk.get("triples", []) if isinstance(chunk.get("triples"), list) else [],
            }
            for chunk in chunks
        ],
    }


def get_document_graph_payload(document_id: str) -> dict:
    payload = fetch_document_graph(document_id)
    if payload is None:
        raise ValueError(f"Document '{document_id}' not found")
    return payload


def get_knowledge_base_graph_payload(kb_id: str) -> dict:
    payload = fetch_knowledge_base_graph(kb_id)
    if payload is None:
        raise ValueError(f"Knowledge base '{kb_id}' not found")
    return payload


def delete_document_payload(document_id: str) -> dict:
    deleted = delete_document(document_id)
    return {"deleted": deleted > 0, "document_id": document_id}


def create_knowledge_base_payload(
    kb_id: str,
    name: str,
    description: str,
    strategy: str = "hierarchical",
    identity: IdentityContext | None = None,
) -> dict:
    from core.db.knowledge_base import create_knowledge_base, get_knowledge_base

    result = create_knowledge_base(kb_id, name, description, strategy, identity)
    kb = get_knowledge_base(result["id"], identity)
    if kb is None:
        raise ValueError(f"Knowledge base '{result['id']}' was not persisted")

    return {
        "id": kb["id"],
        "name": kb["name"],
        "description": kb.get("description", "") or "",
        "strategy": kb.get("default_strategy", "") or "hierarchical",
        "tenantId": kb.get("tenant_id") or "",
        "createdBy": kb.get("created_by") or "",
        "ownerUserId": kb.get("owner_user_id") or "",
        "ownerStatus": kb.get("owner_status") or "active",
        "ownerInvalidReason": kb.get("owner_invalid_reason") or "",
        "status": kb.get("status") or "active",
        "createdAt": kb["created_at"].isoformat() if kb.get("created_at") else "",
        "docCount": 0,
        "chunkCount": 0,
        "lastUpdated": kb["created_at"].isoformat() if kb.get("created_at") else "",
        "duplicatePolicy": "hash-skip",
    }


def update_knowledge_base_payload(
    kb_id: str,
    name: str,
    description: str,
    strategy: str,
    identity: IdentityContext | None = None,
) -> dict:
    from core.db.knowledge_base import update_knowledge_base

    kb = update_knowledge_base(kb_id, name, description, strategy, identity)
    if kb is None:
        raise ValueError(f"Knowledge base '{kb_id}' not found")

    matching = [item for item in get_knowledge_bases_payload(identity) if item["id"] == kb_id]
    if matching:
        return matching[0]

    return {
        "id": kb["id"],
        "name": kb["name"],
        "description": kb.get("description", "") or "",
        "strategy": kb.get("default_strategy", "") or "hierarchical",
        "tenantId": kb.get("tenant_id") or "",
        "createdBy": kb.get("created_by") or "",
        "ownerUserId": kb.get("owner_user_id") or "",
        "ownerStatus": kb.get("owner_status") or "active",
        "ownerInvalidReason": kb.get("owner_invalid_reason") or "",
        "status": kb.get("status") or "active",
        "createdAt": kb["created_at"].isoformat() if kb.get("created_at") else "",
        "docCount": 0,
        "chunkCount": 0,
        "lastUpdated": kb["created_at"].isoformat() if kb.get("created_at") else "",
        "duplicatePolicy": "hash-skip",
    }


def delete_knowledge_base_payload(kb_id: str, identity: IdentityContext | None = None) -> dict:
    from core.db.knowledge_base import delete_knowledge_base

    deleted = delete_knowledge_base(kb_id, identity)
    return {"deleted": deleted > 0, "kb_id": kb_id}


def transfer_knowledge_base_owner_payload(
    kb_id: str,
    owner_user_id: str,
    identity: IdentityContext | None = None,
) -> dict:
    from core.db.knowledge_base import transfer_knowledge_base_owner

    kb = transfer_knowledge_base_owner(kb_id, owner_user_id, identity)
    if kb is None:
        raise ValueError(f"Knowledge base '{kb_id}' not found")
    append_audit_log(
        AuditLogRecord(
            action="knowledge_base.transfer_owner",
            resource_type="knowledge_base",
            resource_id=kb_id,
            kb_id=kb_id,
            identity=identity,
            outcome="success",
            risk_level="medium",
            summary=f"Transferred knowledge base {kb_id} owner",
            metadata={"newOwnerUserId": kb.get("owner_user_id")},
        )
    )
    return {
        "id": kb["id"],
        "name": kb["name"],
        "description": kb.get("description", "") or "",
        "strategy": kb.get("default_strategy", "") or "hierarchical",
        "tenantId": kb.get("tenant_id") or "",
        "createdBy": kb.get("created_by") or "",
        "ownerUserId": kb.get("owner_user_id") or "",
        "ownerStatus": kb.get("owner_status") or "active",
        "ownerInvalidReason": kb.get("owner_invalid_reason") or "",
        "status": kb.get("status") or "active",
        "createdAt": kb["created_at"].isoformat() if kb.get("created_at") else "",
    }


def export_document_csv_payload(document_id: str) -> tuple[str, bytes]:
    return export_document_csv(document_id)


def get_document_source_download_payload(document_id: str) -> dict:
    record = fetch_document_source_record(document_id)
    if record is None:
        raise ValueError(f"Document '{document_id}' not found")

    filename = record["filename"]
    storage = (record.get("source_storage") or "unknown").lower()
    source_path = record.get("source_path") or ""
    source_url = record.get("source_url") or ""

    if storage == "oss" and source_url:
        try:
            from core.config import load_config
            from core.parser.oss_uploader import oss_object_exists, sign_oss_download_url

            config = load_config()
            if oss_object_exists(source_url, config):
                return {
                    "kind": "oss",
                    "filename": filename,
                    "url": sign_oss_download_url(source_url, config),
                    "sourceStorage": "oss",
                }
        except Exception:
            pass

    if source_path:
        from pathlib import Path

        from backend.services.ingestion_service import UPLOAD_DIR, _is_path_under

        path = Path(source_path)
        if path.is_file() and _is_path_under(str(path), UPLOAD_DIR):
            return {
                "kind": "local",
                "filename": filename,
                "path": str(path),
                "sourceStorage": "local" if storage != "oss" else "local_fallback",
            }

    raise FileNotFoundError(f"Source file for document '{document_id}' is not available")


def _normalize_related_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []
