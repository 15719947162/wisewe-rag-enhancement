from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.models.content_block import ContentBlock


@dataclass
class RemovedBlock:
    rule: str
    text: str
    page_idx: int
    block_type: str


@dataclass
class CleanResult:
    blocks: list[ContentBlock]
    removed_count: int = 0
    modified_count: int = 0
    details: list[str] = field(default_factory=list)
    removed_blocks: list[RemovedBlock] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)


class CleanerRule(ABC):
    name: str = "base"

    @abstractmethod
    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        ...
