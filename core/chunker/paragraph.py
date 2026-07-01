from __future__ import annotations

import re

from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class ParagraphStrategy(ChunkingStrategy):
    """Split by natural paragraphs, merge short ones, cap oversized ones.

    A paragraph is a run of text separated by blank lines or block boundaries.
    Short paragraphs (< min_chars) are merged with the next one.
    Oversized paragraphs (> max_chars) are split at sentence boundaries.
    """

    name = "paragraph"

    def __init__(self, min_chars: int = 64, max_chars: int = 512, max_depth: int = 3):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.max_depth = max_depth  # max merge rounds (paragraph nesting depth)

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0

        # Accumulate short blocks across block boundaries before processing
        pending_text = ""
        pending_page = 0
        pending_source = ""

        def flush_pending() -> None:
            nonlocal pending_text, pending_page, pending_source, idx
            if not pending_text.strip():
                return
            paragraphs = self._split_paragraphs(pending_text)
            merged = self._merge_short(paragraphs)
            for para in merged:
                if len(para) <= self.max_chars:
                    chunks.append(self._make_chunk(
                        content=para,
                        source=pending_source,
                        page=pending_page,
                        chunk_index=idx,
                    ))
                    idx += 1
                else:
                    for part in self._split_oversized(para):
                        chunks.append(self._make_chunk(
                            content=part,
                            source=pending_source,
                            page=pending_page,
                            chunk_index=idx,
                        ))
                        idx += 1
            pending_text = ""

        for block in blocks:
            if block.type.value == "image":
                flush_pending()
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            if block.is_table:
                flush_pending()
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

            # Merge short blocks across block boundaries
            if not pending_text:
                pending_text = text
                pending_page = block.page_idx
                pending_source = block.source_file
            elif len(pending_text) < self.min_chars:
                pending_text = pending_text + "\n" + text
            elif len(pending_text) + len(text) <= self.max_chars:
                pending_text = pending_text + "\n\n" + text
            else:
                flush_pending()
                pending_text = text
                pending_page = block.page_idx
                pending_source = block.source_file

        flush_pending()
        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        # Split on blank lines first
        parts = re.split(r"\n{2,}", text)
        if len(parts) > 1:
            return [p.strip() for p in parts if p.strip()]
        # Fallback: split on single newlines
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        return parts if parts else [text]

    def _merge_short(self, paragraphs: list[str]) -> list[str]:
        merged: list[str] = []
        buf = ""
        depth = 0
        for para in paragraphs:
            if not buf:
                buf = para
                depth = 1
            elif len(buf) < self.min_chars and depth < self.max_depth:
                buf = buf + "\n" + para
                depth += 1
            elif len(buf) + len(para) <= self.max_chars and depth < self.max_depth:
                buf = buf + "\n" + para
                depth += 1
            else:
                merged.append(buf)
                buf = para
                depth = 1
        if buf:
            merged.append(buf)
        return merged

    def _split_oversized(self, text: str) -> list[str]:
        # Split at Chinese/English sentence endings
        sentences = re.split(r"(?<=[。！？.!?])", text)
        parts: list[str] = []
        buf = ""
        for s in sentences:
            if len(buf) + len(s) <= self.max_chars:
                buf += s
            else:
                if buf:
                    parts.append(buf.strip())
                buf = s
        if buf.strip():
            parts.append(buf.strip())
        return parts if parts else [text]
