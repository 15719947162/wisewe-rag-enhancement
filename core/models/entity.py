from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "Concept",
    "Procedure",
    "Equipment",
    "Standard",
    "Quantity",
    "Person",
    "Time",
    "Unknown",
]


class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    type: EntityType = "Unknown"
    definition: str | None = None
    source_chunks: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
