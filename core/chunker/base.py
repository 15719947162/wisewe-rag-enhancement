from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.content_block import Chunk, ContentBlock


class ChunkingStrategy(ABC):
    """Base class for all chunking strategies."""

    name: str = "base"

    @abstractmethod
    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """Split content blocks into chunks."""
        ...

    def _make_chunk(
        self,
        content: str,
        source: str,
        page: int,
        chunk_index: int,
        title: str | None = None,
        is_table_chunk: bool = False,
        is_image_chunk: bool = False,
        image_path: str | None = None,
    ) -> Chunk:
        return Chunk(
            content=content,
            source=source,
            page=page,
            chunk_index=chunk_index,
            strategy=self.name,
            title=title,
            is_table_chunk=is_table_chunk,
            is_image_chunk=is_image_chunk,
            image_path=image_path,
        )

    def _make_image_chunk(self, block: ContentBlock, chunk_index: int) -> Chunk:
        """Create a chunk from an image block, preserving image_path."""
        content = block.text.strip() or f"[图片 第{block.page_idx + 1}页]"
        return self._make_chunk(
            content=content,
            source=block.source_file,
            page=block.page_idx,
            chunk_index=chunk_index,
            is_image_chunk=True,
            image_path=block.image_path,
        )


_REGISTRY: dict[str, type[ChunkingStrategy]] = {}


def register_strategy(cls: type[ChunkingStrategy]) -> type[ChunkingStrategy]:
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str, **kwargs) -> ChunkingStrategy:
    if name not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return _REGISTRY[name](**kwargs)


def list_strategies() -> list[str]:
    return list(_REGISTRY.keys())
