from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str
    kb_id: str = "default"
    top_k: int = Field(default=8, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    use_llm_check: bool = False
    use_llm_score: bool = False


class GraphQueryRequest(BaseModel):
    query: str
    kb_id: str = "default"
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    explain: bool = False
    intent: str | None = None


class ParsePreviewRequest(BaseModel):
    pdf_path: str | None = None


TaskState = Literal["pending", "running", "success", "degraded", "failed", "empty"]


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical")


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical")


class KnowledgeBaseTransferOwnerRequest(BaseModel):
    ownerUserId: str = Field(..., min_length=1, max_length=64)


class ChunkDraftUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1)


class ChunkDraftMergeRequest(BaseModel):
    task_id: str
    draft_ids: list[str] = Field(default_factory=list, min_length=2)
