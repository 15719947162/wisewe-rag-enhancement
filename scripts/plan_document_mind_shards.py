from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.parser.pdf_sharding import (
    PdfInspection,
    PdfPageProfile,
    inspect_pdf,
    plan_weighted_page_ranges,
)


def _page_profiles_by_index(inspection: PdfInspection) -> dict[int, PdfPageProfile]:
    return {profile.page_index: profile for profile in inspection.page_profiles}


def _fixed_page_ranges(page_count: int, pages_per_shard: int) -> list[tuple[int, int]]:
    pages = max(0, int(page_count))
    max_pages = max(1, int(pages_per_shard))
    return [
        (start_page, min(start_page + max_pages, pages))
        for start_page in range(0, pages, max_pages)
    ]


def _summarize_range(
    profiles_by_index: dict[int, PdfPageProfile],
    *,
    index: int,
    start_page: int,
    end_page: int,
    source: str,
) -> dict[str, Any]:
    profiles = [profiles_by_index[page_index] for page_index in range(start_page, end_page)]
    weight = sum(max(1, int(profile.weight)) for profile in profiles)
    text_chars = sum(max(0, int(profile.text_chars)) for profile in profiles)
    text_blocks = sum(max(0, int(profile.text_blocks)) for profile in profiles)
    image_count = sum(max(0, int(profile.image_count)) for profile in profiles)
    drawing_count = sum(max(0, int(profile.drawing_count)) for profile in profiles)
    scanned_pages = sum(1 for profile in profiles if profile.likely_scanned)
    return {
        "source": source,
        "index": index,
        "startPage": start_page + 1,
        "endPage": end_page,
        "displayRange": f"P{start_page + 1}-{end_page}",
        "pageCount": end_page - start_page,
        "weight": weight,
        "textChars": text_chars,
        "textBlocks": text_blocks,
        "imageCount": image_count,
        "drawingCount": drawing_count,
        "scannedPages": scanned_pages,
    }


def _summarize_plan(shards: list[dict[str, Any]]) -> dict[str, Any]:
    if not shards:
        return {
            "shardCount": 0,
            "weightTotal": 0,
            "weightMax": 0,
            "weightMin": 0,
            "weightAvg": 0,
            "balanceRatio": 0.0,
            "heaviestShard": None,
            "heavyFirstOrder": [],
        }

    weights = [int(shard["weight"]) for shard in shards]
    weight_total = sum(weights)
    weight_avg = weight_total / len(shards)
    heaviest = max(shards, key=lambda shard: int(shard["weight"]))
    return {
        "shardCount": len(shards),
        "weightTotal": weight_total,
        "weightMax": max(weights),
        "weightMin": min(weights),
        "weightAvg": round(weight_avg, 2),
        "balanceRatio": round(max(weights) / weight_avg, 4) if weight_avg else 0.0,
        "heaviestShard": heaviest,
        "heavyFirstOrder": [
            {
                "index": shard["index"],
                "displayRange": shard["displayRange"],
                "weight": shard["weight"],
            }
            for shard in sorted(shards, key=lambda shard: (-int(shard["weight"]), int(shard["index"])))
        ],
    }


def _page_ranges_from_indices(page_indices: list[int]) -> list[dict[str, Any]]:
    if not page_indices:
        return []
    ranges: list[dict[str, Any]] = []
    sorted_indices = sorted(set(int(index) for index in page_indices))
    start = sorted_indices[0]
    previous = start
    for page_index in sorted_indices[1:]:
        if page_index == previous + 1:
            previous = page_index
            continue
        ranges.append(
            {
                "startPage": start + 1,
                "endPage": previous + 1,
                "displayRange": f"P{start + 1}-{previous + 1}",
                "pageCount": previous - start + 1,
            }
        )
        start = previous = page_index
    ranges.append(
        {
            "startPage": start + 1,
            "endPage": previous + 1,
            "displayRange": f"P{start + 1}-{previous + 1}",
            "pageCount": previous - start + 1,
        }
    )
    return ranges


def summarize_hybrid_parse_candidates(
    page_profiles: list[PdfPageProfile] | tuple[PdfPageProfile, ...],
    *,
    min_text_chars: int,
    max_local_images: int,
    max_local_drawings: int,
) -> dict[str, Any]:
    page_count = len(page_profiles)
    local_pages: list[int] = []
    cloud_pages: list[int] = []
    uncertain_pages: list[int] = []
    reason_counts: dict[str, int] = {
        "local_text": 0,
        "scanned_or_no_text": 0,
        "has_images": 0,
        "has_drawings": 0,
        "low_text": 0,
    }

    for profile in page_profiles:
        page_index = int(profile.page_index)
        text_chars = max(0, int(profile.text_chars))
        image_count = max(0, int(profile.image_count))
        drawing_count = max(0, int(profile.drawing_count))
        if profile.likely_scanned or text_chars <= 0:
            cloud_pages.append(page_index)
            reason_counts["scanned_or_no_text"] += 1
        elif image_count > max_local_images:
            cloud_pages.append(page_index)
            reason_counts["has_images"] += 1
        elif drawing_count > max_local_drawings:
            cloud_pages.append(page_index)
            reason_counts["has_drawings"] += 1
        elif text_chars < min_text_chars:
            uncertain_pages.append(page_index)
            reason_counts["low_text"] += 1
        else:
            local_pages.append(page_index)
            reason_counts["local_text"] += 1

    cloud_or_uncertain_pages = sorted(set(cloud_pages + uncertain_pages))
    local_count = len(local_pages)
    cloud_count = len(cloud_or_uncertain_pages)
    local_ratio = round(local_count / page_count, 4) if page_count else 0.0
    cloud_reduction_pct = round(local_ratio * 100.0, 2)
    recommendation = "keep_cloud_full"
    if local_ratio >= 0.5:
        recommendation = "prototype_hybrid_parser"
    elif local_ratio >= 0.2:
        recommendation = "run_hybrid_canary"

    return {
        "mode": "dry_run_only",
        "thresholds": {
            "minTextChars": int(min_text_chars),
            "maxLocalImages": int(max_local_images),
            "maxLocalDrawings": int(max_local_drawings),
        },
        "pageCount": page_count,
        "localTextCandidatePages": local_count,
        "cloudOrUncertainPages": cloud_count,
        "uncertainPages": len(uncertain_pages),
        "estimatedCloudPageReductionPct": cloud_reduction_pct,
        "recommendation": recommendation,
        "reasonCounts": reason_counts,
        "localTextRanges": _page_ranges_from_indices(local_pages),
        "cloudOrUncertainRanges": _page_ranges_from_indices(cloud_or_uncertain_pages),
        "uncertainRanges": _page_ranges_from_indices(uncertain_pages),
    }


def select_canary_shards(
    fixed_shards: list[dict[str, Any]],
    weighted_shards: list[dict[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_ranges: set[tuple[int, int]] = set()
    selected_ranges: list[tuple[int, int]] = []

    def heavy_overlap(start_page: int, end_page: int) -> bool:
        page_count = max(1, end_page - start_page + 1)
        for selected_start, selected_end in selected_ranges:
            overlap = max(0, min(end_page, selected_end) - max(start_page, selected_start) + 1)
            if overlap / page_count > 0.5:
                return True
        return False

    def add(shard: dict[str, Any], reason: str) -> None:
        key = (int(shard["startPage"]), int(shard["endPage"]))
        if key in seen_ranges:
            return
        if heavy_overlap(key[0], key[1]):
            return
        seen_ranges.add(key)
        selected_ranges.append(key)
        selected.append(
            {
                "source": shard["source"],
                "index": shard["index"],
                "startPage": shard["startPage"],
                "endPage": shard["endPage"],
                "displayRange": shard["displayRange"],
                "pageCount": shard["pageCount"],
                "weight": shard["weight"],
                "reason": reason,
            }
        )

    ordered_weighted = sorted(weighted_shards, key=lambda item: (-int(item["weight"]), int(item["index"])))
    ordered_fixed = sorted(fixed_shards, key=lambda item: (-int(item["weight"]), int(item["index"])))
    max_candidates = max(len(ordered_weighted), len(ordered_fixed))
    for offset in range(max_candidates):
        if offset < len(ordered_weighted):
            add(ordered_weighted[offset], "weighted_heaviest")
            if len(selected) >= top_n:
                return selected
        if offset < len(ordered_fixed):
            add(ordered_fixed[offset], "fixed_heaviest")
            if len(selected) >= top_n:
                return selected

    return selected


def build_plan_record(
    *,
    pdf_path: str,
    pages_per_shard: int,
    text_sample_pages: int,
    top_n: int,
    candidate: str,
    hybrid_min_text_chars: int,
    hybrid_max_local_images: int,
    hybrid_max_local_drawings: int,
) -> dict[str, Any]:
    started_at = time.time()
    inspection = inspect_pdf(
        pdf_path,
        text_sample_pages=text_sample_pages,
        profile_pages=True,
    )
    profiles_by_index = _page_profiles_by_index(inspection)
    if len(profiles_by_index) != inspection.page_count:
        raise RuntimeError(
            "PDF profile is incomplete: "
            f"{len(profiles_by_index)} profiles for {inspection.page_count} pages"
        )

    fixed_shards = [
        _summarize_range(
            profiles_by_index,
            index=index,
            start_page=start_page,
            end_page=end_page,
            source="fixed",
        )
        for index, (start_page, end_page) in enumerate(
            _fixed_page_ranges(inspection.page_count, pages_per_shard),
            start=1,
        )
    ]
    weighted_shards = [
        _summarize_range(
            profiles_by_index,
            index=index,
            start_page=start_page,
            end_page=end_page,
            source="weighted",
        )
        for index, (start_page, end_page, _weight) in enumerate(
            plan_weighted_page_ranges(
                inspection.page_profiles,
                page_count=inspection.page_count,
                max_pages_per_shard=pages_per_shard,
            ),
            start=1,
        )
    ]

    fixed_summary = _summarize_plan(fixed_shards)
    weighted_summary = _summarize_plan(weighted_shards)
    fixed_max = int(fixed_summary["weightMax"])
    weighted_max = int(weighted_summary["weightMax"])
    max_weight_reduction_pct = (
        round((fixed_max - weighted_max) * 100.0 / fixed_max, 2)
        if fixed_max > 0
        else 0.0
    )
    canary_shards = select_canary_shards(
        fixed_shards,
        weighted_shards,
        top_n=max(1, int(top_n)),
    )
    canary_page_ranges = [
        f"{int(shard['startPage'])}-{int(shard['endPage'])}"
        for shard in canary_shards
    ]
    return {
        "candidate": candidate,
        "ok": True,
        "durationMs": int((time.time() - started_at) * 1000),
        "pdfPath": str(Path(pdf_path)),
        "settings": {
            "pagesPerShard": int(pages_per_shard),
            "textSamplePages": int(text_sample_pages),
            "topN": int(top_n),
        },
        "inspection": {
            "pageCount": inspection.page_count,
            "fileSizeBytes": inspection.file_size_bytes,
            "fileSizeMb": round(inspection.file_size_mb, 2),
            "sampledTextChars": inspection.sampled_text_chars,
            "sampledPages": inspection.sampled_pages,
            "likelyScanned": inspection.likely_scanned,
        },
        "plans": {
            "fixed": fixed_summary,
            "weighted": weighted_summary,
        },
        "hybridParse": summarize_hybrid_parse_candidates(
            inspection.page_profiles,
            min_text_chars=hybrid_min_text_chars,
            max_local_images=hybrid_max_local_images,
            max_local_drawings=hybrid_max_local_drawings,
        ),
        "comparison": {
            "fixedWeightMax": fixed_max,
            "weightedWeightMax": weighted_max,
            "maxWeightDelta": weighted_max - fixed_max,
            "maxWeightReductionPct": max_weight_reduction_pct,
            "fixedBalanceRatio": fixed_summary["balanceRatio"],
            "weightedBalanceRatio": weighted_summary["balanceRatio"],
        },
        "canaryPageRanges": canary_page_ranges,
        "canaryPageRangesArg": ",".join(canary_page_ranges),
        "canaryShards": canary_shards,
    }


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan fixed vs weighted Document Mind PDF shards without calling "
            "Document Mind. Use this as the fast validation gate before canary "
            "or full parse-only A/B."
        )
    )
    parser.add_argument("--pdf-path", required=True, help="Local PDF path to inspect.")
    parser.add_argument("--pages-per-shard", type=int, default=33)
    parser.add_argument("--text-sample-pages", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=4, help="Number of canary shard ranges to suggest.")
    parser.add_argument("--candidate", default="document-mind-weighted-plan-dry-run")
    parser.add_argument(
        "--hybrid-min-text-chars",
        type=int,
        default=120,
        help="Minimum PyMuPDF text chars for a page to be considered local-text parseable.",
    )
    parser.add_argument(
        "--hybrid-max-local-images",
        type=int,
        default=0,
        help="Maximum image count allowed for local-text parse candidates.",
    )
    parser.add_argument(
        "--hybrid-max-local-drawings",
        type=int,
        default=0,
        help="Maximum drawing object count allowed for local-text parse candidates.",
    )
    parser.add_argument(
        "--output-jsonl",
        default="data/results/document_mind_shard_plan_dry_run.jsonl",
        help="Append the dry-run record to this JSONL file. Use an empty string to disable.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    record = build_plan_record(
        pdf_path=args.pdf_path,
        pages_per_shard=args.pages_per_shard,
        text_sample_pages=args.text_sample_pages,
        top_n=args.top_n,
        candidate=args.candidate,
        hybrid_min_text_chars=args.hybrid_min_text_chars,
        hybrid_max_local_images=args.hybrid_max_local_images,
        hybrid_max_local_drawings=args.hybrid_max_local_drawings,
    )
    if args.output_jsonl:
        _write_jsonl(Path(args.output_jsonl), record)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
