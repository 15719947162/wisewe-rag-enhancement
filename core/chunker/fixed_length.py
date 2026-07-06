"""
固定长度切片策略（Fixed Length Chunking Strategy）

本模块实现了最基础的切片策略——固定长度切片。该策略按照预设的字符数对文本进行切分，
是最简单直观的切片方法，适用于对语义完整性要求不高、更关注处理效率的场景。

## 核心原理

固定长度切片将文本视为连续的字符流，按照固定的窗口大小（chunk_size）进行切分。
每个切片包含固定数量的字符，不受句子、段落等自然语言边界的限制。

## 重叠机制（Overlap）

为了避免切片边界处的信息丢失，本策略实现了滑动窗口式的重叠机制：
- 相邻切片之间会有 overlap 个字符的重叠区域
- 重叠确保了边界处的上下文连续性
- 例如：chunk_size=100, overlap=20 时，第二个切片会包含第一个切片的最后 20 个字符

## 数据示例

假设 chunk_size=10, overlap=3，文本为："人工智能正在改变世界，未来已来"（15字符）

切片过程：
  切片0: "人工智能正在改变"  (位置 0-9)
  切片1: "改变世界，未来已"  (位置 7-16，与切片0重叠"改变"两字)
  切片2: "未来已来"          (位置 14-18，与切片1重叠"未来已"三字)

从上例可以看出：
- 重叠区域确保了"改变"、"未来已"等关键词在相邻切片中都有出现
- 即使查询跨越切片边界，也能在重叠区域中找到完整答案

## 适用场景

优点：
- 实现简单，计算开销小
- 切片大小可控，便于管理存储和检索成本
- 重叠机制缓解了边界信息丢失问题

缺点：
- 不考虑语义边界，可能切断句子或段落
- 对于结构化文档（如标题、列表）效果较差
- 重叠增加了存储和检索的冗余

适用场景：
- 对语义完整性要求不高的通用文档
- 需要精确控制切片大小的场景
- 作为基准对比其他高级切片策略
"""

from __future__ import annotations

from core.models.content_block import BlockType, Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class FixedLengthStrategy(ChunkingStrategy):
    """
    固定长度切片策略类

    该策略将文本按照固定字符数进行切分，支持相邻切片之间的重叠。
    图片和表格作为特殊块保持完整，不进行切分。

    Attributes:
        name: 策略名称，用于注册和查找
        chunk_size: 每个切片的字符数（不含重叠部分）
        overlap: 相邻切片重叠的字符数

    Example:
        >>> strategy = FixedLengthStrategy(chunk_size=500, overlap=50)
        >>> chunks = strategy.chunk(blocks)
    """

    name = "fixed_length"

    def __init__(self, chunk_size: int = 1000, overlap: int = 50):
        """
        初始化固定长度切片策略

        Args:
            chunk_size: 每个切片的字符数，默认 1000 字符
                - 较大的值（如 1500-2000）：适合长文档，减少切片数量
                - 较小的值（如 300-500）：适合精确检索，增加切片数量
                - 建议根据 embedding 模型的上下文窗口大小调整

            overlap: 相邻切片的重叠字符数，默认 50 字符
                - 通常设置为 chunk_size 的 5-10%
                - 过大：增加冗余，影响检索效率
                - 过小：边界信息可能丢失
                - 建议：50-200 字符之间，根据文档类型调整

        Note:
            重叠值必须小于 chunk_size，否则会导致无限循环。
            在实际应用中，overlap 一般不应超过 chunk_size 的一半。
        """
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """
        对内容块列表进行固定长度切片

        处理流程：
        1. 遍历所有内容块（ContentBlock）
        2. 特殊块直接保留：图片块、表格块不进行切分
        3. 文本块按固定长度切分，支持重叠
        4. 返回所有切片的列表

        Args:
            blocks: 待切片的内容块列表，来自 PDF 解析器的输出

        Returns:
            list[Chunk]: 切片后的 Chunk 对象列表，每个 Chunk 包含：
                - content: 切片内容（文本或 HTML）
                - source: 来源文件名
                - page: 页码索引
                - chunk_index: 切片序号
                - is_table: 是否为表格切片（仅表格块为 True）

        处理策略详解：

        1. 图片块处理：
           - 图片是不可分割的内容单元
           - 直接创建图片切片，保留图片路径和元信息
           - 不参与文本切片逻辑

        2. 表格块处理：
           - 表格的 HTML 结构不宜拆分
           - 保持表格完整性，使用 table_html 或 text 作为内容
           - 标记为 is_table_chunk=True，便于后续特殊处理

        3. 文本块处理：
           - 核心切片逻辑，使用滑动窗口算法
           - 每次切分 chunk_size 个字符
           - 下一个切片起点后退 overlap 个字符
           - 循环直到处理完整个文本

        数据示例（假设 chunk_size=15, overlap=5）：

            文本: "人工智能技术正在深刻改变各个行业的面貌"
            长度: 19 字符

            第1轮切片：
              start=0, end=15
              chunk_text="人工智能技术正在深刻改变"  (15字符)
              下一个起点 = 15 - 5 = 10

            第2轮切片：
              start=10, end=25 (超过文本长度，实际取到19)
              chunk_text="深刻改变各个行业的面貌"  (实际9字符)
              下一个起点 = 25 (超过长度，结束循环)

            结果：2个切片，重叠区域为"深刻改变"（5字符）

        性能考虑：
            - 时间复杂度：O(n)，其中 n 为所有文本块的字符总数
            - 空间复杂度：O(m)，其中 m 为切片数量
            - 重叠会增加切片数量，但不影响时间复杂度
        """
        chunks: list[Chunk] = []
        idx = 0  # 全局切片序号计数器

        for block in blocks:
            # ===== 图片块：保持完整，不切分 =====
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            # ===== 表格块：保持完整，不切分 =====
            if block.is_table:
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    is_table_chunk=True,  # 标记为表格切片
                ))
                idx += 1
                continue

            # ===== 文本块：固定长度切片 =====
            text = block.text.strip()
            if not text:
                # 跳过空文本块
                continue

            # 滑动窗口切片算法
            start = 0  # 当前切片起始位置
            while start < len(text):
                # 计算切片结束位置（不包含）
                end = start + self.chunk_size

                # 提取切片文本（Python 切片会自动处理超出索引的情况）
                chunk_text = text[start:end]

                # 创建文本切片
                chunks.append(self._make_chunk(
                    content=chunk_text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                ))
                idx += 1

                # 计算下一个切片的起始位置
                # 关键逻辑：后退 overlap 个字符，实现重叠
                if end < len(text):
                    # 还有剩余文本，下一个切片后退 overlap 个字符
                    start = end - self.overlap
                else:
                    # 已到文本末尾，结束循环
                    start = end

        return chunks
