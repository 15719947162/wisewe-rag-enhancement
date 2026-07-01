from __future__ import annotations

from pydantic import BaseModel, Field


class Triple(BaseModel):
    s: str
    p: str
    o: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    source_chunk: str = ""
