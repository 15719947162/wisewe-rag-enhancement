from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from core.models.extracted_entity import ExtractedEntity
from core.models.triple import Triple


class EnhancedOutput(BaseModel):
    summary: str = ""
    questions: list[str] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    triples: list[Triple] = Field(default_factory=list)


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
_RAW_JSON_RE = re.compile(r"(\{.*\})", re.S)


def _extract_json(raw: str) -> str | None:
    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        return fenced.group(1)
    direct = _RAW_JSON_RE.search(raw)
    if direct:
        return direct.group(1)
    return None


def _normalize_json_text(raw: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", raw.strip())


def parse_enhanced_response(
    raw: str,
    source_chunk: str,
    fallback_text: str | None = None,
) -> EnhancedOutput:
    payload = _extract_json(raw) or raw
    try:
        data = json.loads(_normalize_json_text(payload))
    except Exception:
        return EnhancedOutput(summary=(fallback_text or raw.strip())[:200])

    entities = [
        ExtractedEntity(
            name=item.get("name", "").strip(),
            type=item.get("type", "Unknown"),
            aliases=item.get("aliases", []) or [],
        )
        for item in (data.get("entities", []) or [])
        if item.get("name")
    ]

    triples: list[Triple] = []
    for item in (data.get("triples", []) or []):
        if not item.get("s") or not item.get("p") or not item.get("o"):
            continue
        triples.append(
            Triple(
                s=item["s"],
                p=item["p"],
                o=item["o"],
                confidence=float(item.get("confidence", 0.7)),
                source_chunk=source_chunk,
            )
        )

    return EnhancedOutput(
        summary=(data.get("summary") or fallback_text or "")[:300],
        questions=[str(item).strip() for item in (data.get("questions", []) or []) if str(item).strip()],
        entities=entities,
        triples=triples,
    )
