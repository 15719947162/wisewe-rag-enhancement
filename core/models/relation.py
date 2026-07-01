from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RelType = Literal[
    "refers_to",
    "adjacent",
    "sibling",
    "semantic_similar",
    "duplicate_of",
    "next_step",
    "prev_step",
    "cause_of",
    "effect_of",
    "mentions",
    "explains",
    "example_of",
    "depends_on",
    "contrasts",
    "co_occurs",
]

RelSource = Literal["rule", "embedding", "llm", "entity"]


class Relation(BaseModel):
    target_id: str
    rel_type: RelType
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    source: RelSource
    evidence: str = ""
