from __future__ import annotations

from backend.adapters.parse_adapter import fetch_parse_preview
from core.models.content_block import ContentBlock


def get_parse_preview(pdf_path: str | None) -> list[dict]:
    blocks = fetch_parse_preview(pdf_path)
    return [_block_to_payload(block, i) for i, block in enumerate(blocks)]


def _block_to_payload(block: ContentBlock, index: int) -> dict:
    return {
        "id": f"block-{index + 1:03d}",
        "type": block.type.value,
        "text": block.text,
        "page": int(block.page_idx) + 1,
        "level": block.text_level,
        "sourceFile": block.source_file,
        "tableHtml": block.table_html,
        "imagePath": block.image_path,
    }
