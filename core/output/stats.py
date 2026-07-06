"""
切片统计模块

用于计算和展示不同切片策略的效果对比数据。

核心功能：
1. 计算单个策略的切片统计信息（数量、字符数、页数等）
2. 生成多策略对比报告（表格形式）

统计指标：
- total_chunks: 切片总数
- table_chunks: 表格切片数
- avg_char_count: 平均字符数
- min_char_count: 最小字符数
- max_char_count: 最大字符数
- pages_covered: 覆盖的页数

使用场景：
- 切片策略效果对比
- 切片质量评估
- 实验报告生成
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from core.models.content_block import Chunk


@dataclass
class ChunkStats:
    """
    切片统计信息的数据容器

    用大白话说，这就是一个"成绩单"，记录某个切片策略的各项指标：

    字段说明：
        strategy: 策略名称（如 "fixed_length"、"semantic" 等）
        total_chunks: 切了总共多少块
        table_chunks: 其中有多少是表格块
        avg_char_count: 平均每块有多少字符（反映粒度）
        min_char_count: 最小块的字符数（检查是否有异常小的碎片）
        max_char_count: 最大块的字符数（检查是否有异常大的块）
        pages_covered: 覆盖了多少页（检查是否有遗漏）

    示例：
        >>> stats = ChunkStats(
        ...     strategy="semantic",
        ...     total_chunks=150,
        ...     table_chunks=12,
        ...     avg_char_count=450.5,
        ...     min_char_count=50,
        ...     max_char_count=2000,
        ...     pages_covered=30,
        ... )
    """
    strategy: str           # 策略名称
    total_chunks: int       # 切片总数
    table_chunks: int       # 表格切片数
    avg_char_count: float   # 平均字符数
    min_char_count: int     # 最小字符数
    max_char_count: int     # 最大字符数
    pages_covered: int      # 覆盖的页数


def compute_stats(chunks: list[Chunk]) -> ChunkStats:
    """
    计算一组切片的统计信息

    这个函数会遍历所有切片，统计各项指标：
    - 切片数量
    - 表格切片数量
    - 字符数的平均值、最小值、最大值
    - 覆盖的页数（去重）

    参数：
        chunks: 切片列表，每个切片包含页码、字符数、是否表格等信息

    返回：
        ChunkStats 对象，包含所有统计指标

    特殊情况：
        - 如果 chunks 为空，返回一个全为 0 的统计对象
        - 策略名称取第一个切片的 strategy 字段

    示例：
        >>> chunks = [Chunk(content="...", page=1, char_count=100), ...]
        >>> stats = compute_stats(chunks)
        >>> print(f"总共 {stats.total_chunks} 个切片")

    实现思路：
        1. 空列表检查，避免除零错误
        2. 提取所有切片的字符数列表
        3. 统计表格切片（is_table_chunk 为 True）
        4. 统计页数（用 set 去重）
        5. 计算平均值、最小值、最大值
    """
    # 空列表处理：返回全零统计对象
    if not chunks:
        return ChunkStats(
            strategy="", total_chunks=0, table_chunks=0,
            avg_char_count=0, min_char_count=0, max_char_count=0, pages_covered=0,
        )

    # 提取所有切片的字符数，用于计算平均值和极值
    char_counts = [c.char_count for c in chunks]

    # 构造并返回统计对象
    return ChunkStats(
        strategy=chunks[0].strategy,                              # 策略名称（取第一个切片的）
        total_chunks=len(chunks),                                 # 切片总数
        table_chunks=sum(1 for c in chunks if c.is_table_chunk), # 表格切片数量
        avg_char_count=sum(char_counts) / len(char_counts),      # 平均字符数
        min_char_count=min(char_counts),                          # 最小字符数
        max_char_count=max(char_counts),                          # 最大字符数
        pages_covered=len(set(c.page for c in chunks)),          # 覆盖页数（去重）
    )


def format_stats_report(all_stats: list[ChunkStats]) -> str:
    """
    生成多策略对比报告（文本表格格式）

    这个函数把多个策略的统计信息格式化成一个漂亮的表格，
    方便直接打印出来看对比效果。

    参数：
        all_stats: 多个策略的统计信息列表

    返回：
        格式化的文本报告，包含：
        - 标题行
        - 表头（Strategy、Chunks、Tables、Avg、Min、Max、Pages）
        - 每个策略一行数据
        - 对齐的分隔线

    输出示例：
        ============================================================
          Chunking Strategy Comparison Report
        ============================================================

          Strategy        Chunks   Tables   Avg      Min    Max    Pages
          --------------- -------- -------- -------- ------ ------ ------
          fixed_length    120      8        450      100    800    25
          semantic        150      12       380      50     2000   30

        ============================================================

    使用场景：
        - 实验报告生成
        - 控制台输出对比结果
        - 日志记录

    示例：
        >>> stats1 = compute_stats(chunks_from_strategy_a)
        >>> stats2 = compute_stats(chunks_from_strategy_b)
        >>> report = format_stats_report([stats1, stats2])
        >>> print(report)
    """
    # 构建报告的每一行
    lines = [
        "=" * 60,  # 顶部分隔线
        "  Chunking Strategy Comparison Report",  # 标题
        "=" * 60,  # 标题下分隔线
        "",  # 空行
        # 表头：左对齐的列名
        f"  {'Strategy':<15} {'Chunks':<8} {'Tables':<8} {'Avg':<8} {'Min':<6} {'Max':<6} {'Pages':<6}",
        # 表头下的分隔线
        f"  {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*6}",
    ]

    # 为每个策略添加一行数据
    for s in all_stats:
        lines.append(
            f"  {s.strategy:<15} {s.total_chunks:<8} {s.table_chunks:<8} "
            f"{s.avg_char_count:<8.0f} {s.min_char_count:<6} {s.max_char_count:<6} {s.pages_covered:<6}"
        )

    # 添加底部分隔线
    lines.extend(["", "=" * 60])

    # 用换行符连接所有行
    return "\n".join(lines)
