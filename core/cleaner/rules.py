from __future__ import annotations

import re

from core.models.content_block import ContentBlock

from .base import CleanResult, CleanerRule, RemovedBlock

RULE_LABELS = {
    "remove_empty": "空白/极短块",
    "remove_short": "短文本块",
    "remove_punctuation": "纯标点块",
    "remove_copyright": "版权/广告",
    "remove_duplicate_images": "同页重复图片",
}


def _removed(rule_name: str, block: ContentBlock) -> RemovedBlock:
    label = RULE_LABELS.get(rule_name, rule_name)
    if block.type.value == "image":
        preview = block.image_path or block.text.strip()[:60] or "[图片无路径]"
    else:
        preview = block.text.strip()[:60] or f"[{block.type.value}]"
    return RemovedBlock(rule=label, text=preview, page_idx=block.page_idx, block_type=block.type.value)


class RemoveEmptyBlocks(CleanerRule):
    name = "remove_empty"

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        kept, removed_blocks = [], []
        for b in blocks:
            if b.is_table or b.type.value == "image" or (b.text.strip() and len(b.text.strip()) >= 3):
                kept.append(b)
            else:
                removed_blocks.append(_removed(self.name, b))
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除空白/极短块: {removed} 个"] if removed else [])


class RemoveShortBlocks(CleanerRule):
    name = "remove_short"

    def __init__(self, min_chars: int = 10):
        self.min_chars = min_chars

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        kept, removed_blocks = [], []
        for b in blocks:
            if b.is_table or b.type.value == "image" or b.type.value == "title" or len(b.text.strip()) >= self.min_chars:
                kept.append(b)
            else:
                removed_blocks.append(_removed(self.name, b))
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除短文本块(<{self.min_chars}字): {removed} 个"] if removed else [])


class RemovePunctuation(CleanerRule):
    name = "remove_punctuation"

    def __init__(self, threshold: float = 0.8):
        self.threshold = threshold

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        kept, removed_blocks = [], []
        for b in blocks:
            if b.type.value == "image":
                kept.append(b)
                continue
            text = b.text.strip()
            if not text:
                kept.append(b)
                continue
            punct_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
            if punct_count / len(text) > self.threshold and not b.is_table:
                removed_blocks.append(_removed(self.name, b))
            else:
                kept.append(b)
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除纯标点块: {removed} 个"] if removed else [])


class RemoveCopyrightAds(CleanerRule):
    name = "remove_copyright"

    PATTERNS = [
        r"[Cc]opyright\s*[©(c)]*\s*\d{4}",
        r"All\s+[Rr]ights\s+[Rr]eserved",
        r"版权所有",
        r"未经.*许可.*不得",
        r"https?://\S+",
    ]

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        kept, removed_blocks = [], []
        for b in blocks:
            if b.type.value == "image":
                kept.append(b)
                continue
            text = b.text.strip()
            if len(text) < 200 and any(re.search(p, text) for p in self.PATTERNS):
                removed_blocks.append(_removed(self.name, b))
            else:
                kept.append(b)
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除版权/广告: {removed} 个"] if removed else [])


class RemoveDuplicateImages(CleanerRule):
    """Remove duplicate image blocks on the same page, keeping the first occurrence.

    Two image blocks are considered duplicates when they share the same page and
    either their image_path or (if path is absent) their text content is identical.
    """

    name = "remove_duplicate_images"

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        kept, removed_blocks = [], []
        # key: (page_idx, dedup_key) → already seen
        seen: set[tuple[int, str]] = set()
        for b in blocks:
            if b.type.value != "image":
                kept.append(b)
                continue
            dedup_key = b.image_path or b.text.strip()
            signature = (b.page_idx, dedup_key)
            if signature in seen:
                removed_blocks.append(_removed(self.name, b))
            else:
                seen.add(signature)
                kept.append(b)
        removed = len(removed_blocks)
        return CleanResult(
            blocks=kept,
            removed_count=removed,
            removed_blocks=removed_blocks,
            details=[f"移除同页重复图片: {removed} 张"] if removed else [],
        )


DEFAULT_RULES: list[CleanerRule] = [
    RemoveEmptyBlocks(),
    RemoveShortBlocks(min_chars=2),
    RemovePunctuation(threshold=0.8),
    RemoveCopyrightAds(),
    RemoveDuplicateImages(),
]
