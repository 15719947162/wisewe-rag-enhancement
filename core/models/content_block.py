from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator
from pydantic import Field

from core.models.extracted_entity import ExtractedEntity
from core.models.relation import Relation
from core.models.triple import Triple


class BlockType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    TITLE = "title"


class ContentBlock(BaseModel):
    type: BlockType
    text: str
    page_idx: int
    text_level: Optional[int] = None
    is_table: bool = False
    table_html: Optional[str] = None
    source_file: str = ""
    image_path: Optional[str] = None  # local path to image file (for VL models)
    bbox: Optional[list[float]] = None  # [x0, y0, x1, y1] bounding box


class Chunk(BaseModel):
    id: str = ""
    content: str
    source: str
    page: int
    chunk_index: int
    strategy: str = ""
    title: Optional[str] = None
    char_count: int = 0
    is_table_chunk: bool = False
    is_image_chunk: bool = False
    is_procedure_chunk: bool = False
    image_path: Optional[str] = None  # local path to image file
    layer: str = "child"  # "parent" | "child" | "enhanced"
    parent_id: Optional[str] = None  # links child/enhanced to parent chunk
    procedure_order: Optional[int] = None
    enhanced_text: Optional[str] = None  # LLM-generated summary for retrieval
    extracted_entities: list[ExtractedEntity] = Field(default_factory=list)
    extracted_triples: list[Triple] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    token_cost: int = 0  # LLM token consumption for this chunk (enhanced only)

    @model_validator(mode="after")
    def _auto_fields(self) -> "Chunk":
        if not self.id:
            object.__setattr__(self, "id", str(uuid.uuid4()))
        if not self.char_count:
            object.__setattr__(self, "char_count", len(self.content))
        return self

    @property
    def related_ids(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for relation in self.relations:
            if relation.target_id and relation.target_id not in seen:
                seen.add(relation.target_id)
                ordered.append(relation.target_id)
        return ordered
