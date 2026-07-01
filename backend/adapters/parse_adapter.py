from __future__ import annotations

from core.models.content_block import ContentBlock


def fetch_parse_preview(pdf_path: str | None) -> list[ContentBlock]:

    if not pdf_path:
        raise ValueError("真实解析模式下必须提供 pdf_path")

    from core.parser.mineru_parser import parse_pdf
    return parse_pdf(pdf_path, output_dir="data/output")
