"""
段落切片策略 - 按自然段落边界切分文档

这个模块实现了基于自然段落的文档切片策略。

为什么选择段落切片？
==================
段落是作者组织内容的基本单位，通常：
- 一个段落表达一个完整的观点
- 段落之间有自然的语义边界
- 比机械的字符切片更符合阅读习惯

段落切片的优势：
===============
1. 语义完整：不会把一个意思拆散到多个切片
2. 检索友好：用户查询的内容通常对应一个段落
3. 易于理解：检索结果展示时更符合阅读习惯

工作原理：
=========
1. 识别段落：按空行或换行符分割
2. 合并短段落：小于 min_chars 的段落与下一段合并
3. 拆分长段落：超过 max_chars 的段落按句子拆分
4. 处理特殊内容：图片和表格单独成为切片

参数说明：
=========
- min_chars: 最小段落长度，默认 64 字符
- max_chars: 最大段落长度，默认 512 字符
- max_depth: 最大合并轮数，避免无限合并

使用示例：
=========
    from core.chunker.paragraph import ParagraphStrategy

    strategy = ParagraphStrategy(min_chars=64, max_chars=512)
    chunks = strategy.chunk(blocks)
"""

import re

from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class ParagraphStrategy(ChunkingStrategy):
    """段落切片策略。

    按自然段落边界切分，合并短段落，拆分长段落。

    Attributes:
        name: 策略名称，用于注册和查找
        min_chars: 最小段落长度，小于此值会与下一段合并
        max_chars: 最大段落长度，超过此值会按句子拆分
        max_depth: 最大合并轮数（段落嵌套深度）
    """

    name = "paragraph"

    def __init__(self, min_chars: int = 64, max_chars: int = 512, max_depth: int = 3):
        """初始化段落切片策略。

        Args:
            min_chars: 最小段落长度，默认 64 字符
            max_chars: 最大段落长度，默认 512 字符
            max_depth: 最大合并轮数，默认 3（避免把整篇文章合并成一个切片）
        """
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.max_depth = max_depth  # 最大合并轮数（段落嵌套深度）

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """将内容块列表切分成段落切片。

        处理流程：
        1. 遍历所有内容块
        2. 图片和表格单独处理（不参与段落合并）
        3. 文本块累积到缓冲区，满足条件后刷新成切片
        4. 短文本会跨块合并（避免生成太碎的切片）

        Args:
            blocks: PDF 解析后的内容块列表

        Returns:
            切片列表
        """
        chunks: list[Chunk] = []
        idx = 0  # 切片索引计数器

        # 累积缓冲区：用于跨块合并短文本
        pending_text = ""
        pending_page = 0
        pending_source = ""

        def flush_pending() -> None:
            """刷新缓冲区，生成切片。

            将缓冲区的文本按段落处理：
            1. 分割成段落
            2. 合并短段落
            3. 拆分长段落
            4. 生成切片对象
            """
            nonlocal pending_text, pending_page, pending_source, idx
            if not pending_text.strip():
                return

            # 分割成段落
            paragraphs = self._split_paragraphs(pending_text)
            # 合并短段落
            merged = self._merge_short(paragraphs)

            # 为每个段落生成切片
            for para in merged:
                if len(para) <= self.max_chars:
                    # 正常长度，直接生成切片
                    chunks.append(self._make_chunk(
                        content=para,
                        source=pending_source,
                        page=pending_page,
                        chunk_index=idx,
                    ))
                    idx += 1
                else:
                    # 超长段落，按句子拆分
                    for part in self._split_oversized(para):
                        chunks.append(self._make_chunk(
                            content=part,
                            source=pending_source,
                            page=pending_page,
                            chunk_index=idx,
                        ))
                        idx += 1

            # 清空缓冲区
            pending_text = ""

        # 遍历所有内容块
        for block in blocks:
            # 处理图片块：单独成切片，不参与段落合并
            if block.type.value == "image":
                flush_pending()  # 先刷新缓冲区
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            # 处理表格块：单独成切片，不参与段落合并
            if block.is_table:
                flush_pending()  # 先刷新缓冲区
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    is_table_chunk=True,
                ))
                idx += 1
                continue

            # 处理文本块
            text = block.text.strip()
            if not text:
                continue

            # 跨块合并短文本
            if not pending_text:
                # 缓冲区为空，直接放入
                pending_text = text
                pending_page = block.page_idx
                pending_source = block.source_file
            elif len(pending_text) < self.min_chars:
                # 缓冲区文本太短，合并当前块
                pending_text = pending_text + "\n" + text
            elif len(pending_text) + len(text) <= self.max_chars:
                # 合并后不超过上限，直接合并
                pending_text = pending_text + "\n\n" + text
            else:
                # 缓冲区已够长，刷新后重新开始
                flush_pending()
                pending_text = text
                pending_page = block.page_idx
                pending_source = block.source_file

        # 处理剩余的缓冲区内容
        flush_pending()
        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        """将文本分割成段落。

        分割规则：
        1. 优先按空行（连续2个以上换行符）分割
        2. 如果没有空行，则按单个换行符分割

        Args:
            text: 输入文本

        Returns:
            段落列表（已去除空白）
        """
        # 先尝试按空行分割
        parts = re.split(r"\n{2,}", text)
        if len(parts) > 1:
            return [p.strip() for p in parts if p.strip()]

        # 降级：按单个换行符分割
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        return parts if parts else [text]

    def _merge_short(self, paragraphs: list[str]) -> list[str]:
        """合并短段落。

        规则：
        - 当前段落 + 下一段落 <= max_chars：合并
        - 当前段落 < min_chars：强制合并
        - 最大合并轮数限制为 max_depth

        Args:
            paragraphs: 段落列表

        Returns:
            合并后的段落列表
        """
        merged: list[str] = []
        buf = ""  # 缓冲区
        depth = 0  # 当前合并轮数

        for para in paragraphs:
            if not buf:
                # 缓冲区为空，直接放入
                buf = para
                depth = 1
            elif len(buf) < self.min_chars and depth < self.max_depth:
                # 缓冲区太短，必须合并
                buf = buf + "\n" + para
                depth += 1
            elif len(buf) + len(para) <= self.max_chars and depth < self.max_depth:
                # 合并后不超过上限，合并
                buf = buf + "\n" + para
                depth += 1
            else:
                # 缓冲区已够长或达到最大轮数，保存并重新开始
                merged.append(buf)
                buf = para
                depth = 1

        # 保存剩余内容
        if buf:
            merged.append(buf)
        return merged

    def _split_oversized(self, text: str) -> list[str]:
        """拆分超长段落。

        按句子边界拆分：
        - 中文句号：。！？
        - 英文句号：. ! ?

        Args:
            text: 超长文本

        Returns:
            拆分后的文本片段列表
        """
        # 按句子结束符分割（保留分隔符）
        sentences = re.split(r"(?<=[。！？.!?])", text)
        parts: list[str] = []
        buf = ""

        for s in sentences:
            if len(buf) + len(s) <= self.max_chars:
                # 累积到缓冲区
                buf += s
            else:
                # 缓冲区满了，保存并重新开始
                if buf:
                    parts.append(buf.strip())
                buf = s

        # 保存剩余内容
        if buf.strip():
            parts.append(buf.strip())

        return parts if parts else [text]
