from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from core.models.content_block import ContentBlock

PDF_PARSER_PROVIDER_ENV = "PDF_PARSER_PROVIDER"
DEFAULT_PDF_PARSER_PROVIDER = "mineru"


@dataclass(frozen=True)
class ParserChannel:
    key: str
    label: str
    module: str
    description: str


PDF_PARSER_CHANNELS: dict[str, ParserChannel] = {
    "mineru": ParserChannel(
        key="mineru",
        label="302AI MinerU",
        module="core.parser.mineru_parser",
        description="Existing 302AI-hosted MinerU cloud parser.",
    ),
    "mineru_official": ParserChannel(
        key="mineru_official",
        label="MinerU Official Precision API",
        module="core.parser.mineru_official_parser",
        description="Official MinerU Precision API via mineru.net.",
    ),
    "ali_document_mind": ParserChannel(
        key="ali_document_mind",
        label="Alibaba Document Mind",
        module="core.parser.document_mind_parser",
        description="Alibaba Document Mind parser provider.",
    ),
}
SUPPORTED_PDF_PARSER_PROVIDERS = set(PDF_PARSER_CHANNELS)


def get_pdf_parser_provider() -> str:
    provider = os.getenv(PDF_PARSER_PROVIDER_ENV, DEFAULT_PDF_PARSER_PROVIDER).strip().lower()
    if not provider:
        provider = DEFAULT_PDF_PARSER_PROVIDER
    if provider not in SUPPORTED_PDF_PARSER_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PDF_PARSER_PROVIDERS))
        raise ValueError(f"Unsupported PDF parser provider '{provider}'. Allowed values: {allowed}")
    return provider


def parse_pdf(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    provider = get_pdf_parser_provider()
    channel = PDF_PARSER_CHANNELS[provider]
    module = __import__(channel.module, fromlist=["parse_pdf"])
    parse_with_channel = getattr(module, "parse_pdf")
    return parse_with_channel(
        pdf_path,
        output_dir=output_dir,
        log_fn=log_fn,
        original_name=original_name,
    )
