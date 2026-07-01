from __future__ import annotations

import json
import os
from datetime import datetime, timezone

EVALUATION_STORE_PATH = os.path.join("data", "results", "console_evaluations.json")
MAX_EVALUATION_RECORDS = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent_dir() -> None:
    os.makedirs(os.path.dirname(EVALUATION_STORE_PATH), exist_ok=True)


def load_evaluations() -> list[dict]:
    if not os.path.exists(EVALUATION_STORE_PATH):
        return []

    try:
        with open(EVALUATION_STORE_PATH, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    return [item for item in payload if isinstance(item, dict)]


def save_evaluations(records: list[dict]) -> None:
    _ensure_parent_dir()
    with open(EVALUATION_STORE_PATH, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)


def append_evaluation(record: dict) -> dict:
    records = load_evaluations()
    normalized = {
        "id": record.get("id") or f"eval-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "kbId": record.get("kbId", "default"),
        "query": record.get("query", ""),
        "answer": record.get("answer", ""),
        "relevanceScore": float(record.get("relevanceScore", 0.0) or 0.0),
        "faithfulnessScore": float(record.get("faithfulnessScore", 0.0) or 0.0),
        "llmScore": record.get("llmScore"),
        "cannotAnswer": bool(record.get("cannotAnswer", False)),
        "failureReason": record.get("failureReason"),
        "createdAt": record.get("createdAt") or _utc_now(),
    }
    records.append(normalized)
    records = sorted(records, key=lambda item: item.get("createdAt", ""), reverse=True)[:MAX_EVALUATION_RECORDS]
    save_evaluations(records)
    return normalized
