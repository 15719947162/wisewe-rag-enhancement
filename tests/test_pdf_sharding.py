from __future__ import annotations

from pathlib import Path

from core.models.content_block import BlockType, ContentBlock
from core.parser.pdf_sharding import (
    PdfPageProfile,
    PdfShard,
    PdfShardSaveOptions,
    inspect_pdf,
    merge_shard_records,
    offset_shard_blocks,
    plan_weighted_page_ranges,
    split_pdf_to_shards,
    split_pdf_to_weighted_shards,
)


def _make_pdf(path: Path, page_count: int) -> None:
    import fitz

    doc = fitz.open()
    try:
        for index in range(page_count):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {index + 1}")
        doc.save(str(path))
    finally:
        doc.close()


def test_inspect_pdf_reads_page_count_and_text_sample(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 4)

    inspection = inspect_pdf(str(pdf_path), text_sample_pages=2)

    assert inspection.page_count == 4
    assert inspection.file_size_bytes > 0
    assert inspection.sampled_pages == 2
    assert inspection.sampled_text_chars > 0
    assert inspection.likely_scanned is False
    assert inspection.page_profiles == ()


def test_inspect_pdf_can_profile_all_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "profiled.pdf"
    _make_pdf(pdf_path, 3)

    inspection = inspect_pdf(str(pdf_path), text_sample_pages=2, profile_pages=True)

    assert inspection.page_count == 3
    assert len(inspection.page_profiles) == 3
    assert [profile.page_index for profile in inspection.page_profiles] == [0, 1, 2]
    assert all(profile.weight >= 1 for profile in inspection.page_profiles)


def test_split_pdf_to_shards_creates_expected_page_ranges(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _make_pdf(pdf_path, 7)

    shards = split_pdf_to_shards(str(pdf_path), tmp_path / "shards", pages_per_shard=3)

    assert [(shard.start_page, shard.end_page) for shard in shards] == [(0, 3), (3, 6), (6, 7)]
    assert [shard.display_range for shard in shards] == ["P1-3", "P4-6", "P7-7"]
    assert all(shard.path.exists() for shard in shards)


def test_pdf_shard_save_options_clamps_garbage_range() -> None:
    assert PdfShardSaveOptions(garbage=99, deflate=False).save_kwargs() == {
        "garbage": 4,
        "deflate": False,
    }
    assert PdfShardSaveOptions(garbage=-5, deflate=True).save_kwargs() == {
        "garbage": 0,
        "deflate": True,
    }


def test_plan_weighted_page_ranges_balances_contiguous_weights() -> None:
    profiles = [
        PdfPageProfile(
            index,
            text_chars=0,
            text_blocks=0,
            image_count=0,
            drawing_count=0,
            likely_scanned=False,
            weight=weight,
        )
        for index, weight in enumerate([1, 1, 20, 1, 1, 1, 1])
    ]

    ranges = plan_weighted_page_ranges(profiles, page_count=7, max_pages_per_shard=3)

    assert [(start, end) for start, end, _weight in ranges] == [(0, 2), (2, 4), (4, 7)]
    assert [weight for _start, _end, weight in ranges] == [2, 21, 3]


def test_plan_weighted_page_ranges_falls_back_when_profiles_are_incomplete() -> None:
    profiles = [
        PdfPageProfile(0, text_chars=0, text_blocks=0, image_count=0, drawing_count=0, likely_scanned=False, weight=10),
        PdfPageProfile(2, text_chars=0, text_blocks=0, image_count=0, drawing_count=0, likely_scanned=False, weight=10),
    ]

    ranges = plan_weighted_page_ranges(profiles, page_count=5, max_pages_per_shard=2)

    assert ranges == [(0, 2, 2), (2, 4, 2), (4, 5, 1)]


def test_split_pdf_to_weighted_shards_preserves_pages_and_weights(tmp_path: Path) -> None:
    pdf_path = tmp_path / "weighted.pdf"
    _make_pdf(pdf_path, 7)
    profiles = [
        PdfPageProfile(
            index,
            text_chars=0,
            text_blocks=0,
            image_count=0,
            drawing_count=0,
            likely_scanned=False,
            weight=weight,
        )
        for index, weight in enumerate([1, 1, 20, 1, 1, 1, 1])
    ]

    shards = split_pdf_to_weighted_shards(
        str(pdf_path),
        tmp_path / "weighted-shards",
        page_profiles=profiles,
        max_pages_per_shard=3,
    )

    assert [(shard.start_page, shard.end_page, shard.weight) for shard in shards] == [
        (0, 2, 2),
        (2, 4, 21),
        (4, 7, 3),
    ]
    assert all(shard.path.exists() for shard in shards)


def test_offset_and_merge_shard_blocks_preserves_global_pages_and_source_file() -> None:
    shard_2 = PdfShard(index=2, start_page=3, end_page=6, path=Path("shard_002.pdf"))
    shard_1 = PdfShard(index=1, start_page=0, end_page=3, path=Path("shard_001.pdf"))

    records = []
    records.extend(
        offset_shard_blocks(
            [ContentBlock(type=BlockType.TEXT, text="p4", page_idx=0, source_file="wrong.pdf")],
            shard_2,
            "book.pdf",
        )
    )
    records.extend(
        offset_shard_blocks(
            [ContentBlock(type=BlockType.TEXT, text="p1", page_idx=0, source_file="wrong.pdf")],
            shard_1,
            "book.pdf",
        )
    )

    blocks = merge_shard_records(records)

    assert [block.text for block in blocks] == ["p1", "p4"]
    assert [block.page_idx for block in blocks] == [0, 3]
    assert all(block.source_file == "book.pdf" for block in blocks)
