from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class EvalRecord(BaseModel):
    id: str
    kb_id: str
    query: str
    intent: str
    ground_truth_chunks: list[str]
    ground_truth_answer: str | None = None
    cross_section: bool = False
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


def load_dataset(path: str) -> list[EvalRecord]:
    target = Path(path)
    if not target.exists():
        return []
    records: list[EvalRecord] = []
    with target.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            records.append(EvalRecord.model_validate(json.loads(line)))
    return records
