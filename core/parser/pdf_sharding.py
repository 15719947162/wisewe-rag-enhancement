from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.models.content_block import ContentBlock


@dataclass(frozen=True)
class PdfPageProfile:
    page_index: int
    text_chars: int
    text_blocks: int
    image_count: int
    drawing_count: int
    likely_scanned: bool
    weight: int


@dataclass(frozen=True)
class PdfInspection:
    page_count: int
    file_size_bytes: int
    sampled_text_chars: int
    sampled_pages: int
    likely_scanned: bool
    page_profiles: tuple[PdfPageProfile, ...] = ()

    @property
    def file_size_mb(self) -> float:
        return self.file_size_bytes / (1024 * 1024)


@dataclass(frozen=True)
class PdfShard:
    index: int
    start_page: int
    end_page: int
    path: Path
    weight: int = 0

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page

    @property
    def display_range(self) -> str:
        return f"P{self.start_page + 1}-{self.end_page}"


@dataclass(frozen=True)
class PdfShardSaveOptions:
    garbage: int = 4
    deflate: bool = True

    def save_kwargs(self) -> dict[str, object]:
        return {
            "garbage": min(4, max(0, int(self.garbage))),
            "deflate": bool(self.deflate),
        }


ShardBlockRecord = tuple[int, int, int, ContentBlock]


def import_fitz():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF sharding requires PyMuPDF. Install pymupdf from requirements.txt."
        ) from exc
    return fitz


def inspect_pdf(
    pdf_path: str,
    text_sample_pages: int = 5,
    *,
    profile_pages: bool = False,
) -> PdfInspection:
    """Inspect basic PDF shape before choosing single-task or sharded parsing."""
    fitz = import_fitz()
    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    file_size_bytes = pdf_path_obj.stat().st_size
    sampled_text_chars = 0
    sampled_pages = 0
    page_profiles: list[PdfPageProfile] = []

    doc = fitz.open(str(pdf_path_obj))
    try:
        page_count = int(doc.page_count)
        sampled_pages = min(max(int(text_sample_pages), 0), page_count)
        if profile_pages:
            for page_index in range(page_count):
                page = doc.load_page(page_index)
                profile = _profile_pdf_page(page, page_index)
                page_profiles.append(profile)
                if page_index < sampled_pages:
                    sampled_text_chars += profile.text_chars
        else:
            for page_index in range(sampled_pages):
                text = doc.load_page(page_index).get_text("text") or ""
                sampled_text_chars += len(text.strip())
    finally:
        doc.close()

    return PdfInspection(
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        sampled_text_chars=sampled_text_chars,
        sampled_pages=sampled_pages,
        likely_scanned=sampled_pages > 0 and sampled_text_chars == 0,
        page_profiles=tuple(page_profiles),
    )


def _profile_pdf_page(page: object, page_index: int) -> PdfPageProfile:
    text = _safe_page_text(page)
    text_chars = len(text.strip())
    text_blocks = _safe_text_block_count(page)
    image_count = _safe_len_call(page, "get_images")
    drawing_count = _safe_len_call(page, "get_drawings")
    likely_scanned = text_chars == 0 and (image_count > 0 or drawing_count > 0)
    weight = _score_pdf_page(
        text_chars=text_chars,
        text_blocks=text_blocks,
        image_count=image_count,
        drawing_count=drawing_count,
        likely_scanned=likely_scanned,
    )
    return PdfPageProfile(
        page_index=page_index,
        text_chars=text_chars,
        text_blocks=text_blocks,
        image_count=image_count,
        drawing_count=drawing_count,
        likely_scanned=likely_scanned,
        weight=weight,
    )


def _safe_page_text(page: object) -> str:
    try:
        return str(page.get_text("text") or "")  # type: ignore[attr-defined]
    except Exception:
        return ""


def _safe_text_block_count(page: object) -> int:
    try:
        blocks = page.get_text("blocks") or []  # type: ignore[attr-defined]
    except Exception:
        return 0
    count = 0
    for block in blocks:
        try:
            text = str(block[4] or "")
        except Exception:
            text = ""
        if text.strip():
            count += 1
    return count


def _safe_len_call(page: object, method_name: str) -> int:
    method = getattr(page, method_name, None)
    if method is None:
        return 0
    try:
        return len(method())
    except Exception:
        return 0


def _score_pdf_page(
    *,
    text_chars: int,
    text_blocks: int,
    image_count: int,
    drawing_count: int,
    likely_scanned: bool,
) -> int:
    text_weight = min(max(text_chars, 0) // 600, 6)
    block_weight = min(max(text_blocks, 0) // 8, 4)
    image_weight = min(max(image_count, 0) * 4, 16)
    drawing_weight = min(max(drawing_count, 0) // 4, 8)
    scanned_weight = 8 if likely_scanned else 0
    return max(1, 1 + text_weight + block_weight + image_weight + drawing_weight + scanned_weight)


def split_pdf_to_shards(
    pdf_path: str,
    shard_dir: Path,
    pages_per_shard: int,
    *,
    save_options: PdfShardSaveOptions | None = None,
) -> list[PdfShard]:
    fitz = import_fitz()
    shard_dir.mkdir(parents=True, exist_ok=True)
    source = fitz.open(str(pdf_path))
    shards: list[PdfShard] = []
    save_kwargs = (save_options or PdfShardSaveOptions()).save_kwargs()
    try:
        page_count = int(source.page_count)
        for index, start_page in enumerate(range(0, page_count, max(1, pages_per_shard)), start=1):
            end_page = min(start_page + max(1, pages_per_shard), page_count)
            shard_path = shard_dir / f"shard_{index:03d}_p{start_page + 1:04d}-{end_page:04d}.pdf"
            shard_doc = fitz.open()
            try:
                shard_doc.insert_pdf(source, from_page=start_page, to_page=end_page - 1)
                shard_doc.save(str(shard_path), **save_kwargs)
            finally:
                shard_doc.close()
            shards.append(
                PdfShard(
                    index=index,
                    start_page=start_page,
                    end_page=end_page,
                    path=shard_path,
                )
            )
    finally:
        source.close()
    return shards


def split_pdf_to_weighted_shards(
    pdf_path: str,
    shard_dir: Path,
    *,
    page_profiles: tuple[PdfPageProfile, ...] | list[PdfPageProfile],
    max_pages_per_shard: int,
    save_options: PdfShardSaveOptions | None = None,
) -> list[PdfShard]:
    fitz = import_fitz()
    shard_dir.mkdir(parents=True, exist_ok=True)
    source = fitz.open(str(pdf_path))
    shards: list[PdfShard] = []
    save_kwargs = (save_options or PdfShardSaveOptions()).save_kwargs()
    try:
        page_count = int(source.page_count)
        ranges = plan_weighted_page_ranges(
            page_profiles,
            page_count=page_count,
            max_pages_per_shard=max_pages_per_shard,
        )
        for index, (start_page, end_page, weight) in enumerate(ranges, start=1):
            shard_path = shard_dir / f"shard_{index:03d}_p{start_page + 1:04d}-{end_page:04d}.pdf"
            shard_doc = fitz.open()
            try:
                shard_doc.insert_pdf(source, from_page=start_page, to_page=end_page - 1)
                shard_doc.save(str(shard_path), **save_kwargs)
            finally:
                shard_doc.close()
            shards.append(
                PdfShard(
                    index=index,
                    start_page=start_page,
                    end_page=end_page,
                    path=shard_path,
                    weight=weight,
                )
            )
    finally:
        source.close()
    return shards


def plan_weighted_page_ranges(
    page_profiles: tuple[PdfPageProfile, ...] | list[PdfPageProfile],
    *,
    page_count: int,
    max_pages_per_shard: int,
) -> list[tuple[int, int, int]]:
    pages = max(0, int(page_count))
    max_pages = max(1, int(max_pages_per_shard))
    if pages <= 0:
        return []

    profiles_by_page = {profile.page_index: profile for profile in page_profiles}
    if any(index not in profiles_by_page for index in range(pages)):
        return _fixed_page_ranges(pages, max_pages)

    target_shard_count = max(1, (pages + max_pages - 1) // max_pages)
    weights = [max(1, int(profiles_by_page[index].weight)) for index in range(pages)]
    prefix_weights = [0]
    for weight in weights:
        prefix_weights.append(prefix_weights[-1] + weight)

    # Keep the request count equal to fixed page sharding, but choose contiguous
    # boundaries that minimize the heaviest shard and then the overall imbalance.
    costs: list[dict[int, tuple[int, int]]] = [{0: (0, 0)}] + [dict() for _ in range(target_shard_count)]
    previous: list[dict[int, int]] = [dict() for _ in range(target_shard_count + 1)]
    for shard_count in range(1, target_shard_count + 1):
        min_end = shard_count
        max_end = pages - (target_shard_count - shard_count)
        for end_page in range(min_end, max_end + 1):
            best_cost: tuple[int, int] | None = None
            best_start: int | None = None
            min_start = max(shard_count - 1, end_page - max_pages)
            max_start = end_page - 1
            for start_page in range(min_start, max_start + 1):
                prior = costs[shard_count - 1].get(start_page)
                if prior is None:
                    continue
                shard_weight = prefix_weights[end_page] - prefix_weights[start_page]
                candidate = (
                    max(prior[0], shard_weight),
                    prior[1] + shard_weight * shard_weight,
                )
                if best_cost is None or candidate < best_cost:
                    best_cost = candidate
                    best_start = start_page
            if best_cost is not None and best_start is not None:
                costs[shard_count][end_page] = best_cost
                previous[shard_count][end_page] = best_start

    if pages not in costs[target_shard_count]:
        return _fixed_page_ranges(pages, max_pages)

    ranges: list[tuple[int, int, int]] = []
    end_page = pages
    for shard_count in range(target_shard_count, 0, -1):
        start_page = previous[shard_count][end_page]
        weight = prefix_weights[end_page] - prefix_weights[start_page]
        ranges.append((start_page, end_page, weight))
        end_page = start_page
    ranges.reverse()
    return ranges


def _fixed_page_ranges(page_count: int, pages_per_shard: int) -> list[tuple[int, int, int]]:
    pages = max(0, int(page_count))
    max_pages = max(1, int(pages_per_shard))
    return [
        (start_page, min(start_page + max_pages, pages), min(start_page + max_pages, pages) - start_page)
        for start_page in range(0, pages, max_pages)
    ]


def offset_shard_blocks(
    blocks: list[ContentBlock],
    shard: PdfShard,
    source_name: str,
) -> list[ShardBlockRecord]:
    records: list[ShardBlockRecord] = []
    for order, block in enumerate(blocks):
        global_page_idx = int(block.page_idx) + shard.start_page
        adjusted = block.model_copy(
            update={
                "page_idx": global_page_idx,
                "source_file": source_name,
            }
        )
        records.append((global_page_idx, shard.index, order, adjusted))
    return records


def merge_shard_records(records: list[ShardBlockRecord]) -> list[ContentBlock]:
    records.sort(key=lambda item: (item[0], item[1], item[2]))
    return [record[3] for record in records]
