from __future__ import annotations

import csv
from pathlib import Path

from core.models.content_block import Chunk


def write_knowledge_csv(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    output_path: str,
    encoding: str = "utf-8-sig",
) -> str:
    """Write chunks and embeddings to CSV knowledge base file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id", "content", "source", "page", "chunk_index",
        "strategy", "title", "char_count", "is_table_chunk", "embedding",
    ]

    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for chunk, embedding in zip(chunks, embeddings):
            row = chunk.model_dump()
            row["embedding"] = ",".join(f"{v:.6f}" for v in embedding)
            writer.writerow(row)

    return str(path)
