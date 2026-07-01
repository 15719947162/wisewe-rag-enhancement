from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntity(BaseModel):
    name: str
    type: str = "Unknown"
    aliases: list[str] = Field(default_factory=list)
