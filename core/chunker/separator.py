"""
分隔符切片策略 - 按自定义分隔符切分文档

这个模块实现了基于分隔符的文档切片策略。

为什么使用分隔符切片？
====================
有些文档有固定的格式，比如：
- 用空行分隔的列表项
- 用分号分隔的条款
- 用句号分隔的句子

这时候，按分隔符切分比按段落切分更合适。

工作原理：
=========
1. 尝试每个分隔符，找到最有效的那个
2. 如果设置 keep_separator=True，分隔符会保留在切片末尾
3. 图片和表格单独处理，不参与分隔

默认分隔符：
===========
["\\n\\n", "\\n", "。", "；", ". "]
优先使用空行，如果没有效果则尝试换行符、句号、分号等。

使用示例：
=========
    from core.chunker.separator import SeparatorStrategy

    # 使用默认分隔符
    strategy = SeparatorStrategy()
    chunks = strategy.chunk(blocks)

    # 自定义分隔符（比如按分号切分）
    strategy = SeparatorStrategy(separators=["；", ";"])
"""

import re

from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class SeparatorStrategy(ChunkingStrategy):
    """分隔符切片策略。

    按自定义的分隔符切分文本。

    Attributes:
        name: 策略名称
        separators: 分隔符列表（按优先级尝试）
        keep_separator: 是否保留分隔符在切片中
    """

    name = "separator"

    def __init__(self, separators: list[str] | None = None, keep_separator: bool = True):
        """初始化分隔符切片策略。

        Args:
            separators: 自定义分隔符列表，默认为 ["\\n\\n", "\\n", "。", "；", ". "]
            keep_separator: 是否保留分隔符，默认 True（保留在切片末尾）
        """
        self.separators = separators or ["\n\n", "\n", "。", "；", ". "]
        self.keep_separator = keep_separator

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """将内容块列表按分隔符切分成切片。

        处理流程：
        1. 图片块单独成切片
        2. 表格块单独成切片
        3. 文本块按分隔符切分

        Args:
            blocks: PDF 解析后的内容块列表

        Returns:
            切片列表
        """
        chunks: list[Chunk] = []
        idx = 0

        for block in blocks:
            # 图片块：单独处理
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            # 表格块：单独处理
            if block.is_table:
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    is_table_chunk=True,
                ))
                idx += 1
                continue

            # 文本块：按分隔符切分
            text = block.text.strip()
            if not text:
                continue

            parts = self._split_text(text)
            for part in parts:
                part = part.strip()
                if part:
                    chunks.append(self._make_chunk(
                        content=part,
                        source=block.source_file,
                        page=block.page_idx,
                        chunk_index=idx,
                    ))
                    idx += 1

        return chunks

    def _split_text(self, text: str) -> list[str]:
        """按分隔符切分文本。

        策略：
        1. 按优先级依次尝试每个分隔符
        2. 如果某个分隔符能产生多个片段，就使用它
        3. 如果所有分隔符都无效，返回原文

        Args:
            text: 要切分的文本

        Returns:
            切分后的文本片段列表
        """
        for sep in self.separators:
            # 转义分隔符中的特殊字符
            pattern = re.escape(sep)

            if self.keep_separator:
                # 保留分隔符：使用捕获组，分隔符会保留在结果中
                parts = re.split(f"({pattern})", text)
                # 合并文本和分隔符
                merged = []
                for i in range(0, len(parts) - 1, 2):
                    merged.append(parts[i] + parts[i + 1])
                # 处理最后一个元素（如果总数是奇数）
                if len(parts) % 2 == 1:
                    merged.append(parts[-1])
                parts = merged
            else:
                # 不保留分隔符
                parts = text.split(sep)

            # 过滤空白，如果产生了多个片段就返回
            parts = [p for p in parts if p.strip()]
            if len(parts) > 1:
                return parts

        # 所有分隔符都无效，返回原文
        return [text]
