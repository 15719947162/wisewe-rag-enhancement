from __future__ import annotations

from core.parser.pdf_sharding import PdfPageProfile
from scripts.plan_document_mind_shards import select_canary_shards
from scripts.plan_document_mind_shards import summarize_hybrid_parse_candidates


def test_select_canary_shards_prefers_non_overlapping_ranges() -> None:
    fixed_shards = [
        {
            "source": "fixed",
            "index": 1,
            "startPage": 1,
            "endPage": 33,
            "displayRange": "P1-33",
            "pageCount": 33,
            "weight": 825,
        },
        {
            "source": "fixed",
            "index": 2,
            "startPage": 34,
            "endPage": 66,
            "displayRange": "P34-66",
            "pageCount": 33,
            "weight": 825,
        },
    ]
    weighted_shards = [
        {
            "source": "weighted",
            "index": 1,
            "startPage": 33,
            "endPage": 65,
            "displayRange": "P33-65",
            "pageCount": 33,
            "weight": 825,
        },
        {
            "source": "weighted",
            "index": 2,
            "startPage": 67,
            "endPage": 99,
            "displayRange": "P67-99",
            "pageCount": 33,
            "weight": 825,
        },
    ]

    selected = select_canary_shards(fixed_shards, weighted_shards, top_n=3)

    assert [shard["displayRange"] for shard in selected] == ["P33-65", "P1-33", "P67-99"]
    assert [shard["reason"] for shard in selected] == [
        "weighted_heaviest",
        "fixed_heaviest",
        "weighted_heaviest",
    ]
    assert selected[0]["startPage"] == 33
    assert selected[0]["endPage"] == 65


def test_summarize_hybrid_parse_candidates_classifies_pages_conservatively() -> None:
    profiles = [
        PdfPageProfile(0, text_chars=800, text_blocks=4, image_count=0, drawing_count=0, likely_scanned=False, weight=2),
        PdfPageProfile(1, text_chars=0, text_blocks=0, image_count=1, drawing_count=0, likely_scanned=True, weight=9),
        PdfPageProfile(2, text_chars=600, text_blocks=3, image_count=1, drawing_count=0, likely_scanned=False, weight=5),
        PdfPageProfile(3, text_chars=70, text_blocks=1, image_count=0, drawing_count=0, likely_scanned=False, weight=1),
        PdfPageProfile(4, text_chars=900, text_blocks=4, image_count=0, drawing_count=2, likely_scanned=False, weight=2),
        PdfPageProfile(5, text_chars=900, text_blocks=4, image_count=0, drawing_count=0, likely_scanned=False, weight=2),
    ]

    summary = summarize_hybrid_parse_candidates(
        profiles,
        min_text_chars=120,
        max_local_images=0,
        max_local_drawings=0,
    )

    assert summary["mode"] == "dry_run_only"
    assert summary["pageCount"] == 6
    assert summary["localTextCandidatePages"] == 2
    assert summary["cloudOrUncertainPages"] == 4
    assert summary["uncertainPages"] == 1
    assert summary["estimatedCloudPageReductionPct"] == 33.33
    assert summary["recommendation"] == "run_hybrid_canary"
    assert summary["reasonCounts"] == {
        "local_text": 2,
        "scanned_or_no_text": 1,
        "has_images": 1,
        "has_drawings": 1,
        "low_text": 1,
    }
    assert summary["localTextRanges"] == [
        {"startPage": 1, "endPage": 1, "displayRange": "P1-1", "pageCount": 1},
        {"startPage": 6, "endPage": 6, "displayRange": "P6-6", "pageCount": 1},
    ]
    assert summary["cloudOrUncertainRanges"] == [
        {"startPage": 2, "endPage": 5, "displayRange": "P2-5", "pageCount": 4},
    ]
