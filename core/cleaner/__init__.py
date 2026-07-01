from __future__ import annotations

from core.models.content_block import ContentBlock

from .base import CleanResult, CleanerRule, RemovedBlock
from .llm_cleaner import LLMCleaner
from .quality_gate import QualityGateResult, apply_quality_gate
from .rules import DEFAULT_RULES


def clean_blocks(
    blocks: list[ContentBlock],
    use_rules: bool = True,
    use_llm: bool = False,
    rules: list[CleanerRule] | None = None,
    llm_system_prompt: str = "",
    llm_model: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
) -> CleanResult:
    """Run cleaning pipeline on content blocks."""
    current = list(blocks)
    total_removed = 0
    total_modified = 0
    all_details: list[str] = []
    all_removed_blocks: list[RemovedBlock] = []
    metrics: dict[str, int] = {}

    def merge_metrics(values: dict[str, int]) -> None:
        for key, value in values.items():
            metrics[key] = int(metrics.get(key, 0) or 0) + int(value or 0)

    if use_rules:
        active_rules = rules
        if active_rules is None:
            from .rules import (
                RemoveCopyrightAds,
                RemoveEmptyBlocks,
                RemovePunctuation,
                RemoveShortBlocks,
            )
            active_rules = [
                RemoveEmptyBlocks(),
                RemoveShortBlocks(min_chars=2),
                RemovePunctuation(threshold=0.8),
                RemoveCopyrightAds(),
            ]
        for rule in active_rules:
            result = rule.apply(current)
            current = result.blocks
            total_removed += result.removed_count
            total_modified += result.modified_count
            all_details.extend(result.details)
            all_removed_blocks.extend(result.removed_blocks)
            merge_metrics(result.metrics)

    if use_llm:
        llm = LLMCleaner(system_prompt=llm_system_prompt, model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        result = llm.apply(current)
        current = result.blocks
        total_removed += result.removed_count
        total_modified += result.modified_count
        all_details.extend(result.details)
        all_removed_blocks.extend(result.removed_blocks)
        merge_metrics(result.metrics)

    return CleanResult(
        blocks=current,
        removed_count=total_removed,
        modified_count=total_modified,
        details=all_details,
        removed_blocks=all_removed_blocks,
        metrics=metrics,
    )


__all__ = [
    "CleanResult",
    "CleanerRule",
    "RemovedBlock",
    "LLMCleaner",
    "QualityGateResult",
    "apply_quality_gate",
    "clean_blocks",
]
