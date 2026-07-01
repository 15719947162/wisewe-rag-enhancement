"""Verify PDF parsing pipeline: PDF -> MinerU -> ContentBlock list.

Usage:
    python -m core.parser.verify_parse data/input/sample.pdf
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from core.models.content_block import BlockType, ContentBlock


def real_parse(pdf_path: str) -> list[ContentBlock]:
    """Parse PDF using the current MinerU cloud adapter."""
    from core.parser.mineru_parser import parse_pdf

    return parse_pdf(pdf_path, output_dir="data/output")


def print_stats(blocks: list[ContentBlock]) -> None:
    type_counts = Counter(b.type.value for b in blocks)
    table_count = sum(1 for b in blocks if b.is_table)
    pages = set(b.page_idx for b in blocks)

    print(f"\n{'=' * 50}")
    print("  PDF Parse Results")
    print(f"{'=' * 50}")
    print(f"  Total blocks: {len(blocks)}")
    print(f"  Pages covered: {len(pages)} (idx {min(pages)}-{max(pages)})")
    print("  Type distribution:")
    for block_type, count in sorted(type_counts.items()):
        print(f"    - {block_type}: {count}")
    print(f"  Table chunks (independent): {table_count}")
    print(f"{'=' * 50}\n")


def print_sample(blocks: list[ContentBlock], n: int = 3) -> None:
    print(f"  First {min(n, len(blocks))} blocks:\n")
    for i, block in enumerate(blocks[:n]):
        print(f"  [{i}] type={block.type.value}, page={block.page_idx}, level={block.text_level}")
        text_preview = block.text[:80] + "..." if len(block.text) > 80 else block.text
        print(f"      text: {text_preview}")
        if block.is_table:
            print("      [TABLE - independent chunk candidate]")
        print()


def save_results(blocks: list[ContentBlock], output_path: str) -> None:
    data = [block.model_dump() for block in blocks]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    print(f"  Results saved to: {output_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m core.parser.verify_parse <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"  Parsing: {pdf_path}")
    blocks = real_parse(pdf_path)

    print_stats(blocks)
    print_sample(blocks)

    output_json = f"data/output/{Path(pdf_path).stem}_blocks.json"
    save_results(blocks, output_json)

    assert len(blocks) > 0, "No blocks parsed"
    assert any(block.type == BlockType.TEXT for block in blocks), "No text blocks found"
    assert all(block.source_file for block in blocks), "Missing source_file metadata"
    assert all(block.page_idx >= 0 for block in blocks), "Invalid page_idx"

    table_blocks = [block for block in blocks if block.is_table]
    if table_blocks:
        assert all(block.table_html for block in table_blocks), "Table blocks missing HTML"
        print(f"  [PASS] {len(table_blocks)} table(s) marked as independent chunks")

    print("\n  [PASS] All verification checks passed!")


if __name__ == "__main__":
    main()
