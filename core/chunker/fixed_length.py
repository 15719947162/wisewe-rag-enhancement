from __future__ import annotations

from core.models.content_block import BlockType, Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class FixedLengthStrategy(ChunkingStrategy):
    """Split text into fixed-length chunks with overlap."""

    name = "fixed_length"

    def __init__(self, chunk_size: int = 1000, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0

        for block in blocks:
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            if block.is_table:
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    is_table_chunk=True,
                ))
                idx += 1
                continue

            text = block.text.strip()
            if not text:
                continue

            start = 0
            while start < len(text):
                end = start + self.chunk_size
                chunk_text = text[start:end]
                chunks.append(self._make_chunk(
                    content=chunk_text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                ))
                idx += 1
                start = end - self.overlap if end < len(text) else end

        return chunks
