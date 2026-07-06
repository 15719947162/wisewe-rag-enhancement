"""
清洗规则模块

本模块实现了 PDF 内容块的清洗规则，用于过滤掉低质量或无关的内容块。
清洗是 RAG 管道中的重要环节，直接影响切片质量和检索效果。

规则列表：
- RemoveEmptyBlocks: 移除空白或极短（<3 字符）的内容块
- RemoveShortBlocks: 移除指定长度以下的短文本块（可配置阈值）
- RemovePunctuation: 移除标点符号占比过高的内容块
- RemoveCopyrightAds: 移除版权声明和广告链接
- RemoveDuplicateImages: 移除同一页面内的重复图片

重要机制：
1. 图片豁免：图片类型的内容块在所有规则中都享有豁免权，不会被误删
2. 表格豁免：表格类型的内容块在部分规则中享有豁免权
3. 标题豁免：标题类型的内容块在短文本规则中享有豁免权

使用示例：
    >>> from core.cleaner.rules import RemoveEmptyBlocks, RemoveShortBlocks
    >>> rules = [RemoveEmptyBlocks(), RemoveShortBlocks(min_chars=10)]
    >>> for rule in rules:
    ...     result = rule.apply(blocks)
    ...     blocks = result.blocks
"""
from __future__ import annotations

import re

from core.models.content_block import ContentBlock

from .base import CleanResult, CleanerRule, RemovedBlock

# ============================================================================
# 规则标签映射
# ============================================================================
# 用于在移除记录中显示人类可读的标签，方便日志输出和调试

RULE_LABELS = {
    "remove_empty": "空白/极短块",
    "remove_short": "短文本块",
    "remove_punctuation": "纯标点块",
    "remove_copyright": "版权/广告",
    "remove_duplicate_images": "同页重复图片",
}


def _removed(rule_name: str, block: ContentBlock) -> RemovedBlock:
    """
    创建移除记录的辅助函数。

    将被移除的内容块封装成 RemovedBlock 对象，用于记录和追踪清洗过程。

    参数：
        rule_name: 规则名称（如 "remove_empty"）
        block: 被移除的内容块

    返回：
        RemovedBlock 对象，包含：
        - rule: 人类可读的规则标签
        - text: 内容预览（文本块取前 60 字符，图片块取路径）
        - page_idx: 所在页码
        - block_type: 块类型

    示例：
        >>> block = ContentBlock(type=BlockType.TEXT, text="测试", page_idx=0)
        >>> removed = _removed("remove_short", block)
        >>> removed.rule  # "短文本块"
        >>> removed.text  # "测试"
    """
    label = RULE_LABELS.get(rule_name, rule_name)
    # 图片类型取路径作为预览，避免显示空白文本
    if block.type.value == "image":
        preview = block.image_path or block.text.strip()[:60] or "[图片无路径]"
    else:
        preview = block.text.strip()[:60] or f"[{block.type.value}]"
    return RemovedBlock(rule=label, text=preview, page_idx=block.page_idx, block_type=block.type.value)


# ============================================================================
# 空白块移除规则
# ============================================================================

class RemoveEmptyBlocks(CleanerRule):
    """
    移除空白或极短内容块的规则。

    判断逻辑：
        当内容块满足以下任一条件时保留：
        1. 是表格类型（is_table == True）
        2. 是图片类型（type == "image"）
        3. 文本内容非空且长度 >= 3 字符

        否则移除该内容块。

    图片豁免机制：
        图片类型的块始终保留，即使没有文本内容。
        这是因为图片通过 image_path 字段承载信息，不依赖 text 字段。

    数据示例：
        输入块列表：
        [
            ContentBlock(text="第一章 简介", page_idx=0),        # 保留：有效文本
            ContentBlock(text="", page_idx=0),                   # 移除：空白
            ContentBlock(text="  ", page_idx=0),                 # 移除：仅空格
            ContentBlock(text="AB", page_idx=0),                 # 移除：极短（<3）
            ContentBlock(type="image", image_path="img1.png"),   # 保留：图片豁免
            ContentBlock(is_table=True, table_html="<table>"),   # 保留：表格豁免
        ]

        输出：
        - kept: 3 个块（有效文本 + 图片 + 表格）
        - removed_count: 3 个块（空白 + 空格 + 极短）
    """

    name = "remove_empty"

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """应用空白块移除规则。"""
        kept, removed_blocks = [], []
        for b in blocks:
            # 判断条件：表格/图片豁免，或文本长度 >= 3
            if b.is_table or b.type.value == "image" or (b.text.strip() and len(b.text.strip()) >= 3):
                kept.append(b)
            else:
                removed_blocks.append(_removed(self.name, b))
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除空白/极短块: {removed} 个"] if removed else [])


# ============================================================================
# 短文本块移除规则
# ============================================================================

class RemoveShortBlocks(CleanerRule):
    """
    移除指定长度以下的短文本块。

    判断逻辑：
        当内容块满足以下任一条件时保留：
        1. 是表格类型（is_table == True）
        2. 是图片类型（type == "image"）
        3. 是标题类型（type == "title"）
        4. 文本长度 >= min_chars（默认 10 字符）

        否则移除该内容块。

    图片豁免机制：
        图片类型的块始终保留，因为图片通过路径承载信息，不依赖文本长度。
        即使图片的 alt 文本很短，图片本身可能包含重要信息。

    标题豁免机制：
        标题类型的内容块通常很短（如"第一章"、"引言"），
        但在文档结构中具有重要作用，因此予以保留。

    参数：
        min_chars: 最小字符数阈值，默认 10。
                   注意：DEFAULT_RULES 中使用 min_chars=2，
                   以避免与 RemoveEmptyBlocks 规则冲突。

    数据示例：
        假设 min_chars=10

        输入块列表：
        [
            ContentBlock(text="这是一段正常长度的文本内容", page_idx=0),  # 保留：>= 10 字符
            ContentBlock(text="短文本", page_idx=0),                      # 移除：< 10 字符
            ContentBlock(text="引言", type="title", page_idx=0),          # 保留：标题豁免
            ContentBlock(type="image", text="", page_idx=0),              # 保留：图片豁免
        ]

        输出：
        - kept: 3 个块
        - removed_count: 1 个块
    """

    name = "remove_short"

    def __init__(self, min_chars: int = 10):
        """
        初始化短文本移除规则。

        参数：
            min_chars: 最小字符数阈值，低于此值的内容块将被移除。
        """
        self.min_chars = min_chars

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """应用短文本移除规则。"""
        kept, removed_blocks = [], []
        for b in blocks:
            # 判断条件：表格/图片/标题豁免，或文本长度达标
            if b.is_table or b.type.value == "image" or b.type.value == "title" or len(b.text.strip()) >= self.min_chars:
                kept.append(b)
            else:
                removed_blocks.append(_removed(self.name, b))
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除短文本块(<{self.min_chars}字): {removed} 个"] if removed else [])


# ============================================================================
# 纯标点块移除规则
# ============================================================================

class RemovePunctuation(CleanerRule):
    """
    移除标点符号占比过高的内容块。

    判断逻辑：
        1. 图片类型的块直接保留（图片豁免）
        2. 空文本的块直接保留
        3. 计算非字母数字、非空格字符的比例
        4. 如果比例 > threshold 且不是表格，则移除
        5. 表格类型即使标点比例高也保留（表格豁免）

    图片豁免机制：
        图片类型的块在规则开始时就直接保留，不进行标点比例计算。
        这是因为图片的 text 字段可能是 alt 文本或 OCR 结果，
        不应该因为标点比例而被删除。

    表格豁免机制：
        表格通常包含大量分隔符和标点（如 |、-、: 等），
        但这些标点是表格结构的一部分，不应被视为低质量内容。
        因此表格类型即使标点比例超过阈值也予以保留。

    参数：
        threshold: 标点比例阈值，默认 0.8（即 80%）。
                   当标点占比超过此值时，内容块将被移除。

    数据示例：
        假设 threshold=0.8

        输入块列表：
        [
            ContentBlock(text="这是一段正常的文本，包含一些标点。", page_idx=0),  # 保留：标点比例低
            ContentBlock(text="......！！！????", page_idx=0),                  # 移除：标点比例 > 0.8
            ContentBlock(text="-----|-----|-----", is_table=True, page_idx=0), # 保留：表格豁免
            ContentBlock(type="image", text="!!!", page_idx=0),                # 保留：图片豁免
        ]

        输出：
        - kept: 3 个块
        - removed_count: 1 个块

    计算方法：
        标点数 = 非字母数字且非空格的字符数
        标点比例 = 标点数 / 文本总长度
    """

    name = "remove_punctuation"

    def __init__(self, threshold: float = 0.8):
        """
        初始化纯标点移除规则。

        参数：
            threshold: 标点比例阈值，默认 0.8。
                       标点占比超过此值的内容块将被移除。
        """
        self.threshold = threshold

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """应用纯标点移除规则。"""
        kept, removed_blocks = [], []
        for b in blocks:
            # 图片豁免：直接保留，不进行标点计算
            if b.type.value == "image":
                kept.append(b)
                continue
            text = b.text.strip()
            # 空文本直接保留
            if not text:
                kept.append(b)
                continue
            # 计算标点比例：非字母数字、非空格的字符数 / 总长度
            punct_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
            # 表格豁免：即使标点比例高也保留
            if punct_count / len(text) > self.threshold and not b.is_table:
                removed_blocks.append(_removed(self.name, b))
            else:
                kept.append(b)
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除纯标点块: {removed} 个"] if removed else [])


# ============================================================================
# 版权和广告移除规则
# ============================================================================

class RemoveCopyrightAds(CleanerRule):
    """
    移除版权声明和广告链接。

    这些内容通常是文档的噪音信息，对 RAG 检索没有价值：
    - 版权声明：如 "Copyright © 2024 Company"
    - 广告链接：如 URL 链接
    - 法律声明：如 "未经许可不得转载"

    判断逻辑：
        1. 图片类型的块直接保留（图片豁免）
        2. 文本长度 < 200 字符（版权声明通常很短）
        3. 匹配任一预定义的正则模式

    图片豁免机制：
        图片类型的块直接保留，不进行版权模式匹配。
        这是因为图片可能包含版权水印，但这些水印通常是图像的一部分，
        不应该因为这个规则而被删除。

    正则模式：
        - "Copyright" 相关模式（支持多种格式）
        - "All rights reserved" 模式
        - 中文版权声明："版权所有"、"未经...许可...不得"
        - URL 链接模式：https?://\S+

    为什么限制长度 < 200？
        版权声明通常很短（< 200 字符），而正常段落即使包含
        "版权"字样也不应被删除。例如：
        - 短文本："版权所有 © 2024" → 应该删除
        - 长文本："本文讨论版权法的历史..." → 应该保留

    数据示例：
        输入块列表：
        [
            ContentBlock(text="Copyright © 2024 Company Inc.", page_idx=0),     # 移除：匹配版权模式
            ContentBlock(text="All rights reserved.", page_idx=0),              # 移除：匹配保留权利模式
            ContentBlock(text="版权所有，未经许可不得转载", page_idx=0),          # 移除：匹配中文版权
            ContentBlock(text="访问 https://example.com 了解更多", page_idx=0),  # 移除：包含 URL
            ContentBlock(text="本文详细介绍了版权法的历史和演变..."*50, page_idx=0), # 保留：长度 >= 200
            ContentBlock(type="image", text="Copyright", page_idx=0),           # 保留：图片豁免
        ]

        输出：
        - kept: 2 个块
        - removed_count: 4 个块
    """

    name = "remove_copyright"

    # 正则模式列表：匹配常见的版权声明和广告链接
    PATTERNS = [
        r"[Cc]opyright\s*[©(c)]*\s*\d{4}",   # Copyright © 2024 或 Copyright (c) 2024
        r"All\s+[Rr]ights\s+[Rr]eserved",   # All rights reserved
        r"版权所有",                          # 中文版权声明
        r"未经.*许可.*不得",                   # 中文法律声明
        r"https?://\S+",                     # URL 链接
    ]

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """应用版权和广告移除规则。"""
        kept, removed_blocks = [], []
        for b in blocks:
            # 图片豁免：直接保留
            if b.type.value == "image":
                kept.append(b)
                continue
            text = b.text.strip()
            # 判断条件：长度 < 200 且匹配任一模式
            if len(text) < 200 and any(re.search(p, text) for p in self.PATTERNS):
                removed_blocks.append(_removed(self.name, b))
            else:
                kept.append(b)
        removed = len(removed_blocks)
        return CleanResult(blocks=kept, removed_count=removed, removed_blocks=removed_blocks,
                           details=[f"移除版权/广告: {removed} 个"] if removed else [])


# ============================================================================
# 重复图片移除规则
# ============================================================================

class RemoveDuplicateImages(CleanerRule):
    """
    移除同一页面内的重复图片块。

    在 PDF 解析过程中，同一图片可能被多次识别，导致重复。本规则
    通过页码和图片路径/文本内容来判断重复，保留首次出现，移除后续重复。

    判断逻辑：
        1. 非图片类型的块直接保留
        2. 图片类型的块，使用 (页码, 去重键) 作为签名
        3. 去重键 = image_path（如果存在）或 text 内容
        4. 如果签名已出现过，则移除；否则保留并记录签名

    去重键选择：
        - 优先使用 image_path：图片路径通常是唯一的
        - 如果没有 image_path，使用 text 内容：适用于 OCR 识别的图片

    为什么按页码去重？
        不同页面的相同图片可能是有意义的（如 logo、装饰图），
        只移除同一页面内的重复图片。

    数据示例：
        输入块列表（同一页面）：
        [
            ContentBlock(type="image", image_path="img/logo.png", page_idx=0),  # 保留：首次出现
            ContentBlock(type="image", image_path="img/logo.png", page_idx=0),  # 移除：重复
            ContentBlock(type="image", image_path="img/chart.png", page_idx=0), # 保留：新图片
            ContentBlock(type="image", image_path="img/logo.png", page_idx=1),  # 保留：不同页面
            ContentBlock(text="正常文本", page_idx=0),                          # 保留：非图片
        ]

        输出：
        - kept: 4 个块
        - removed_count: 1 个块
    """
    """Remove duplicate image blocks on the same page, keeping the first occurrence.

    Two image blocks are considered duplicates when they share the same page and
    either their image_path or (if path is absent) their text content is identical.
    """

    name = "remove_duplicate_images"

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """应用重复图片移除规则。"""
        kept, removed_blocks = [], []
        # 已见签名集合：key = (页码, 去重键)
        seen: set[tuple[int, str]] = set()
        for b in blocks:
            # 非图片类型直接保留
            if b.type.value != "image":
                kept.append(b)
                continue
            # 计算去重键：优先使用路径，否则使用文本内容
            dedup_key = b.image_path or b.text.strip()
            signature = (b.page_idx, dedup_key)
            # 判断是否重复
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


# ============================================================================
# 默认规则列表
# ============================================================================
# 清洗管道默认使用的规则序列。规则按顺序执行，每个规则的输出作为下一个规则的输入。
# 注意：RemoveShortBlocks 使用 min_chars=2 以避免与 RemoveEmptyBlocks 冲突。

DEFAULT_RULES: list[CleanerRule] = [
    RemoveEmptyBlocks(),                # 第一步：移除空白块
    RemoveShortBlocks(min_chars=2),     # 第二步：移除极短块（>=2 字符保留）
    RemovePunctuation(threshold=0.8),   # 第三步：移除纯标点块
    RemoveCopyrightAds(),               # 第四步：移除版权和广告
    RemoveDuplicateImages(),            # 第五步：移除重复图片
]
