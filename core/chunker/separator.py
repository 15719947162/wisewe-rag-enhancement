from __future__ import annotations

import re

from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class SeparatorStrategy(ChunkingStrategy):
    """Split text by custom separator patterns."""

    name = "separator"

    def __init__(self, separators: list[str] | None = None, keep_separator: bool = True):
        self.separators = separators or ["\n\n", "\n", "。", "；", ". "]
        self.keep_separator = keep_separator

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

            parts = self._split_text(text)
            for part in parts:
                part = part.strip()
                if part:
                    chunks.append(self._make_chunk(
                        content=part,
                        source=block.source_file,
                        page=block.page_idx,
                        chunk_index=idx,
                    ))
                    idx += 1

        return chunks

    def _split_text(self, text: str) -> list[str]:
        for sep in self.separators:
            pattern = re.escape(sep)
            if self.keep_separator:
                parts = re.split(f"({pattern})", text)
                merged = []
                for i in range(0, len(parts) - 1, 2):
                    merged.append(parts[i] + parts[i + 1])
                if len(parts) % 2 == 1:
                    merged.append(parts[-1])
                parts = merged
            else:
                parts = text.split(sep)

            parts = [p for p in parts if p.strip()]
            if len(parts) > 1:
                return parts

        return [text]
