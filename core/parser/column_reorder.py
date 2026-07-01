"""Column-aware reordering for multi-column page layouts."""
from __future__ import annotations

from core.models.content_block import ContentBlock

LAYOUT_OPTIONS = {
    "single": "单列（默认）",
    "two_col_lr": "双列-从左到右",
    "two_col_rl": "双列-从右到左",
    "three_col": "三列-从左到右",
}


def reorder_blocks_by_columns(
    blocks: list[ContentBlock],
    layout: str = "single",
) -> list[ContentBlock]:
    """Reorder blocks according to column layout.

    Groups blocks by page, splits into columns by x-coordinate midpoint,
    then reorders within each page according to the chosen layout.
    """
    if layout == "single" or not blocks:
        return blocks

    pages: dict[int, list[ContentBlock]] = {}
    for b in blocks:
        pages.setdefault(b.page_idx, []).append(b)

    result: list[ContentBlock] = []
    for page_idx in sorted(pages.keys()):
        page_blocks = pages[page_idx]

        blocks_with_bbox = [(b, b.bbox) for b in page_blocks if b.bbox]
        blocks_no_bbox = [b for b in page_blocks if not b.bbox]

        if not blocks_with_bbox:
            result.extend(page_blocks)
            continue

        page_width = max(bb[2] for _, bb in blocks_with_bbox)

        if layout == "two_col_lr":
            mid = page_width / 2
            left = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < mid],
                key=lambda x: x[1][1],
            )
            right = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= mid],
                key=lambda x: x[1][1],
            )
            result.extend(b for b, _ in left)
            result.extend(b for b, _ in right)

        elif layout == "two_col_rl":
            mid = page_width / 2
            left = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < mid],
                key=lambda x: x[1][1],
            )
            right = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= mid],
                key=lambda x: x[1][1],
            )
            result.extend(b for b, _ in right)
            result.extend(b for b, _ in left)

        elif layout == "three_col":
            t1 = page_width / 3
            t2 = page_width * 2 / 3
            col1 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < t1],
                key=lambda x: x[1][1],
            )
            col2 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if t1 <= (bb[0] + bb[2]) / 2 < t2],
                key=lambda x: x[1][1],
            )
            col3 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= t2],
                key=lambda x: x[1][1],
            )
            result.extend(b for b, _ in col1)
            result.extend(b for b, _ in col2)
            result.extend(b for b, _ in col3)

        result.extend(blocks_no_bbox)

    return result
