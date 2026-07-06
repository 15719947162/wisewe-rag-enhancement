"""
因果链接器 - 识别和关联因果关系切片

这个模块专门用于识别文档中存在因果关系的切片，并建立关联。

什么是因果关系？
===============
文档中经常会有这样的表述：
- "因为下雨，所以路面湿滑"
- "由于系统故障，导致服务中断"
- "鉴于成本上升，使得价格上涨"

这些句子描述了一个事件导致另一个事件的关系，在 RAG 系统中，
当用户询问原因或结果时，我们希望能够同时检索到相关的两个切片。

识别方法：
=========
使用关键词匹配：
- 原因标记："因为"、"由于"、"鉴于"
- 结果标记："导致"、"使得"、"引起"、"造成"、"所以"、"因此"

建立的关系：
===========
如果切片A包含因果标记，且切片B是其前一个切片，则建立：
- A -> B: effect_of（A是B的结果）
- B -> A: cause_of（B是A的原因）

使用场景：
=========
适用于技术文档、事故报告、解释性文章等包含大量因果推理的内容。

示例：
=====
    from core.chunker.causal_linker import link_causal

    added = link_causal(chunks)
    print(f"建立了 {added} 条因果关系")
"""

from core.chunker.relation_utils import add_relation
from core.models.content_block import Chunk

# ============ 因果关系标记词 ============
# 这些词通常出现在因果关系句中，用于识别因果切片
_CAUSAL_MARKERS = ("因为", "由于", "鉴于", "导致", "使得", "引起", "造成", "所以", "因此")


def link_causal(chunks: list[Chunk]) -> int:
    """识别并建立切片之间的因果关系。

    工作原理：
    1. 遍历所有子切片（child layer）
    2. 检查切片开头200字符内是否包含因果标记词
    3. 如果找到因果标记，则与前一个切片建立因果关系
    4. 但要求两个切片必须在同一章节（parent_id相同或都没有parent_id）

    Args:
        chunks: 切片列表

    Returns:
        新增的关系数量

    示例：
        >>> chunks = [
        ...     Chunk(content="系统负载过高"),
        ...     Chunk(content="导致服务器响应缓慢")
        ... ]
        >>> added = link_causal(chunks)
        >>> # 切片1关联切片0：cause_of
        >>> # 切片0关联切片1：effect_of
    """
    added = 0

    # 只处理子切片（排除父级切片和增强切片）
    child_chunks = [chunk for chunk in chunks if chunk.layer == "child"]

    for idx, current in enumerate(child_chunks):
        # 检查当前切片开头是否包含因果标记
        # 只看前200字符，因为因果标记通常在句首
        if not any(marker in current.content[:200] for marker in _CAUSAL_MARKERS):
            continue

        # 第一个切片没有前驱，跳过
        if idx == 0:
            continue

        previous = child_chunks[idx - 1]

        # 确保两个切片在同一章节（跨章节的因果关系可能不可靠）
        if current.parent_id and previous.parent_id and current.parent_id != previous.parent_id:
            continue

        # 建立双向关系
        before = len(previous.relations)
        add_relation(previous, current.id, "cause_of", weight=0.7, source="rule", evidence=current.content[:20])
        add_relation(current, previous.id, "effect_of", weight=0.7, source="rule", evidence=previous.content[:20])

        # 统计新增关系数
        if len(previous.relations) > before:
            added += 1

    return added
