from __future__ import annotations

from core.models.content_block import BlockType, Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class SemanticStrategy(ChunkingStrategy):
    """Structure-aware chunking based on headings and text_level."""

    name = "semantic"

    def __init__(self, max_chunk_size: int = 1000):
        self.max_chunk_size = max_chunk_size

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0
        current_title: str | None = None
        current_texts: list[str] = []
        current_page: int = 0
        current_source: str = ""

        for block in blocks:
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            if block.is_table:
                if current_texts:
                    chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                    idx += 1
                    current_texts = []
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    title=current_title,
                    is_table_chunk=True,
                ))
                idx += 1
                continue

            if block.type == BlockType.TITLE:
                if current_texts:
                    chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                    idx += 1
                    current_texts = []
                current_title = block.text
                current_page = block.page_idx
                current_source = block.source_file
                continue

            current_source = block.source_file
            current_page = block.page_idx
            text = block.text.strip()
            if not text:
                continue

            combined = "\n".join(current_texts + [text])
            if len(combined) > self.max_chunk_size and current_texts:
                chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                idx += 1
                current_texts = [text]
            else:
                current_texts.append(text)

        if current_texts:
            chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))

        return chunks

    def _flush(self, texts: list[str], source: str, page: int, idx: int, title: str | None) -> Chunk:
        return self._make_chunk(
            content="\n".join(texts),
            source=source,
            page=page,
            chunk_index=idx,
            title=title,
        )
