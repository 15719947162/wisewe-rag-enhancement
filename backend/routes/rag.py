from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.identity_service import get_current_identity
from backend.services.rag_service import run_graph_rag_query, run_rag_query
from core.db.identity import IdentityContext

router = APIRouter()


@router.post("/api/rag/query")
def rag_query(payload: QueryRequest, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        if identity.enforce_access:
            return run_rag_query(payload, identity)
        return run_rag_query(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/rag/graph-query")
def graph_rag_query(payload: GraphQueryRequest, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        if identity.enforce_access:
            return run_graph_rag_query(payload, identity)
        return run_graph_rag_query(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
