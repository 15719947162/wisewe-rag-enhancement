from __future__ import annotations

from pathlib import Path
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from backend.schemas.requests import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseTransferOwnerRequest,
    KnowledgeBaseUpdateRequest,
)
from backend.services.document_export_service import build_csv_content_disposition
from backend.services.access_control import require_document_access, require_kb_access
from backend.services.identity_service import get_current_identity
from backend.services.kb_service import (
    create_knowledge_base_payload,
    delete_document_payload,
    delete_knowledge_base_payload,
    export_document_csv_payload,
    get_document_source_download_payload,
    get_document_detail_payload,
    get_document_graph_payload,
    get_documents_payload,
    get_knowledge_base_graph_payload,
    get_knowledge_bases_payload,
    transfer_knowledge_base_owner_payload,
    update_knowledge_base_payload,
)
from core.db.identity import IdentityContext

router = APIRouter()
_output_dir = Path("data/output")
_output_dir.mkdir(parents=True, exist_ok=True)


@router.get("/api/knowledge-bases")
def knowledge_bases(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        if identity.enforce_access:
            return get_knowledge_bases_payload(identity)
        return get_knowledge_bases_payload()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/knowledge-bases", status_code=201)
def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    kb_id = _make_kb_id(payload.name)
    try:
        if identity.enforce_access:
            return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/knowledge-bases/{kb_id}")
def update_knowledge_base(
    kb_id: str,
    payload: KnowledgeBaseUpdateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        if identity.enforce_access:
            return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        if identity.enforce_access:
            result = delete_knowledge_base_payload(kb_id, identity)
        else:
            result = delete_knowledge_base_payload(kb_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/knowledge-bases/{kb_id}/transfer-owner")
def transfer_knowledge_base_owner(
    kb_id: str,
    payload: KnowledgeBaseTransferOwnerRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        return transfer_knowledge_base_owner_payload(kb_id, payload.ownerUserId, identity)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/knowledge-bases/{kb_id}/graph")
def knowledge_base_graph(kb_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        require_kb_access(kb_id, identity, action="knowledge_base.graph.read", resource_id=kb_id)
        return get_knowledge_base_graph_payload(kb_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents")
def documents(kb_id: str | None = None, identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        if kb_id:
            require_kb_access(kb_id, identity, action="document.list", resource_type="knowledge_base", resource_id=kb_id)
        return get_documents_payload(kb_id, identity if identity.enforce_access else None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}")
def document_detail(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        require_document_access(document_id, identity, action="document.detail.read")
        return get_document_detail_payload(document_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/graph")
def document_graph(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        require_document_access(document_id, identity, action="document.graph.read")
        return get_document_graph_payload(document_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        require_document_access(document_id, identity, action="document.delete")
        result = delete_document_payload(document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/export.csv")
def export_document_csv(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> Response:
    try:
        require_document_access(document_id, identity, action="document.export_csv")
        filename, content = export_document_csv_payload(document_id)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": build_csv_content_disposition(filename)},
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/source")
def download_document_source(document_id: str, identity: IdentityContext = Depends(get_current_identity)):
    try:
        require_document_access(document_id, identity, action="document.source.download")
        payload = get_document_source_download_payload(document_id)
        if payload["kind"] == "oss":
            return RedirectResponse(payload["url"], status_code=302)
        return FileResponse(
            payload["path"],
            media_type="application/pdf",
            filename=payload["filename"],
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _make_kb_id(name: str) -> str:
    """Generate an opaque public knowledge base ID.

    Names stay editable display metadata; IDs should not expose pinyin, subjects,
    tenants, or business wording in URLs, logs, and OpenAPI calls.
    """
    return _uuid.uuid4().hex[:24]
