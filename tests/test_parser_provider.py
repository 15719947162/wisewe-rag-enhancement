from __future__ import annotations

from pathlib import Path

import pytest

from core.models.content_block import BlockType, ContentBlock
from core.parser import provider


def test_default_provider_is_mineru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDF_PARSER_PROVIDER", raising=False)

    assert provider.get_pdf_parser_provider() == "mineru"


def test_provider_can_select_document_mind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDF_PARSER_PROVIDER", "ali_document_mind")

    assert provider.get_pdf_parser_provider() == "ali_document_mind"


def test_provider_can_select_official_mineru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDF_PARSER_PROVIDER", "mineru_official")

    assert provider.get_pdf_parser_provider() == "mineru_official"


def test_invalid_provider_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDF_PARSER_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="mineru_official"):
        provider.get_pdf_parser_provider()


def test_dispatches_to_mineru_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_parse_pdf(
        pdf_path: str,
        output_dir: str = "data/output",
        log_fn=None,
        original_name: str | None = None,
    ) -> list[ContentBlock]:
        calls.append((pdf_path, output_dir, original_name))
        return [ContentBlock(type=BlockType.TEXT, text="mineru", page_idx=0, source_file="doc.pdf")]

    monkeypatch.setenv("PDF_PARSER_PROVIDER", "mineru")
    monkeypatch.setattr("core.parser.mineru_parser.parse_pdf", fake_parse_pdf)

    blocks = provider.parse_pdf(str(tmp_path / "doc.pdf"), output_dir="out", original_name="origin.pdf")

    assert blocks[0].text == "mineru"
    assert calls == [(str(tmp_path / "doc.pdf"), "out", "origin.pdf")]


def test_dispatches_to_document_mind_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_parse_pdf(
        pdf_path: str,
        output_dir: str = "data/output",
        log_fn=None,
        original_name: str | None = None,
    ) -> list[ContentBlock]:
        calls.append((pdf_path, output_dir, original_name))
        return [ContentBlock(type=BlockType.TEXT, text="document mind", page_idx=0, source_file="doc.pdf")]

    monkeypatch.setenv("PDF_PARSER_PROVIDER", "ali_document_mind")
    monkeypatch.setattr("core.parser.document_mind_parser.parse_pdf", fake_parse_pdf)

    blocks = provider.parse_pdf(str(tmp_path / "doc.pdf"), output_dir="out", original_name="origin.pdf")

    assert blocks[0].text == "document mind"
    assert calls == [(str(tmp_path / "doc.pdf"), "out", "origin.pdf")]


def test_dispatches_to_official_mineru_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_parse_pdf(
        pdf_path: str,
        output_dir: str = "data/output",
        log_fn=None,
        original_name: str | None = None,
    ) -> list[ContentBlock]:
        calls.append((pdf_path, output_dir, original_name))
        return [ContentBlock(type=BlockType.TEXT, text="official mineru", page_idx=0, source_file="doc.pdf")]

    monkeypatch.setenv("PDF_PARSER_PROVIDER", "mineru_official")
    monkeypatch.setattr("core.parser.mineru_official_parser.parse_pdf", fake_parse_pdf)

    blocks = provider.parse_pdf(str(tmp_path / "doc.pdf"), output_dir="out", original_name="origin.pdf")

    assert blocks[0].text == "official mineru"
    assert calls == [(str(tmp_path / "doc.pdf"), "out", "origin.pdf")]
