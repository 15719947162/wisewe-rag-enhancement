"""Unit tests for content_block models and parser utilities."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.content_block import BlockType, Chunk, ContentBlock
from core.parser.mineru_parser import _convert_to_blocks, _map_type


def test_block_type_mapping():
    assert _map_type("text") == BlockType.TEXT
    assert _map_type("table") == BlockType.TABLE
    assert _map_type("image") == BlockType.IMAGE
    assert _map_type("title") == BlockType.TITLE
    assert _map_type("unknown_type") == BlockType.TEXT


def test_content_block_creation():
    block = ContentBlock(
        type=BlockType.TEXT,
        text="Hello",
        page_idx=0,
        source_file="test.pdf",
    )
    assert block.type == BlockType.TEXT
    assert block.text == "Hello"
    assert block.page_idx == 0
    assert block.is_table is False
    assert block.table_html is None


def test_table_block():
    block = ContentBlock(
        type=BlockType.TABLE,
        text="table content",
        page_idx=1,
        is_table=True,
        table_html="<table><tr><td>A</td></tr></table>",
        source_file="test.pdf",
    )
    assert block.is_table is True
    assert block.table_html is not None


def test_chunk_auto_fields():
    chunk = Chunk(content="Test content", source="test.pdf", page=0, chunk_index=0)
    assert len(chunk.id) == 36
    assert chunk.char_count == 12


def test_chunk_explicit_id():
    chunk = Chunk(id="custom-id", content="abc", source="x.pdf", page=0, chunk_index=0)
    assert chunk.id == "custom-id"
    assert chunk.char_count == 3


def test_convert_to_blocks():
    raw = [
        {"type": "title", "text": "Title", "page_idx": 0, "text_level": 1},
        {"type": "text", "text": "Paragraph", "page_idx": 0},
        {"type": "table", "text": "", "page_idx": 1, "table_body": "<table></table>"},
    ]
    blocks = _convert_to_blocks(raw, source_file="doc.pdf")
    assert len(blocks) == 3
    assert blocks[0].type == BlockType.TITLE
    assert blocks[0].text_level == 1
    assert blocks[1].type == BlockType.TEXT
    assert blocks[2].is_table is True
    assert blocks[2].table_html == "<table></table>"
    assert all(b.source_file == "doc.pdf" for b in blocks)


def test_convert_empty_list():
    blocks = _convert_to_blocks([], source_file="empty.pdf")
    assert blocks == []


if __name__ == "__main__":
    test_block_type_mapping()
    test_content_block_creation()
    test_table_block()
    test_chunk_auto_fields()
    test_chunk_explicit_id()
    test_convert_to_blocks()
    test_convert_empty_list()
    print("All tests passed!")
