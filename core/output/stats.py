from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from core.models.content_block import Chunk


@dataclass
class ChunkStats:
    strategy: str
    total_chunks: int
    table_chunks: int
    avg_char_count: float
    min_char_count: int
    max_char_count: int
    pages_covered: int


def compute_stats(chunks: list[Chunk]) -> ChunkStats:
    """Compute statistics for a list of chunks."""
    if not chunks:
        return ChunkStats(
            strategy="", total_chunks=0, table_chunks=0,
            avg_char_count=0, min_char_count=0, max_char_count=0, pages_covered=0,
        )

    char_counts = [c.char_count for c in chunks]
    return ChunkStats(
        strategy=chunks[0].strategy,
        total_chunks=len(chunks),
        table_chunks=sum(1 for c in chunks if c.is_table_chunk),
        avg_char_count=sum(char_counts) / len(char_counts),
        min_char_count=min(char_counts),
        max_char_count=max(char_counts),
        pages_covered=len(set(c.page for c in chunks)),
    )


def format_stats_report(all_stats: list[ChunkStats]) -> str:
    """Format a comparison report for multiple strategies."""
    lines = [
        "=" * 60,
        "  Chunking Strategy Comparison Report",
        "=" * 60,
        "",
        f"  {'Strategy':<15} {'Chunks':<8} {'Tables':<8} {'Avg':<8} {'Min':<6} {'Max':<6} {'Pages':<6}",
        f"  {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*6}",
    ]

    for s in all_stats:
        lines.append(
            f"  {s.strategy:<15} {s.total_chunks:<8} {s.table_chunks:<8} "
            f"{s.avg_char_count:<8.0f} {s.min_char_count:<6} {s.max_char_count:<6} {s.pages_covered:<6}"
        )

    lines.extend(["", "=" * 60])
    return "\n".join(lines)
