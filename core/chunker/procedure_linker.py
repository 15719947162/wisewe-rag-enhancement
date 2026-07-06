"""
流程链接器 - 识别和关联流程步骤切片

这个模块专门用于识别文档中的操作流程、步骤序列，并建立顺序关系。

什么是流程切片？
===============
文档中经常会有这样的步骤描述：
- "第一步：安装软件"
- "第二步：配置参数"
- "第三步：启动服务"
- 或："首先打开电源，接着按下启动键，然后等待系统启动"

这些步骤有明确的先后顺序，在 RAG 系统中，当用户询问"如何做某事"时，
我们希望能够按顺序检索到所有相关步骤，而不仅仅是某一个步骤。

识别方法：
=========
1. 数字编号模式："第1步"、"第2步"、"1."、"1)"等
2. 时间顺序词："首先"、"接着"、"然后"、"随后"、"最后"等

建立的关系：
===========
如果切片A和切片B都属于同一个流程，则建立：
- A -> B: next_step（B是A的下一步）
- B -> A: prev_step（A是B的上一步）

使用场景：
=========
适用于操作手册、教程文档、流程规范等包含步骤说明的内容。

示例：
=====
    from core.chunker.procedure_linker import link_procedure

    added = link_procedure(chunks)
    print(f"建立了 {added} 条流程关系")
"""

import re

from core.chunker.relation_utils import add_relation
from core.models.content_block import Chunk

# ============ 流程步骤识别的正则表达式 ============
# 匹配格式："第1步"、"第2步"、"1."、"1)"、"第一步"、"第二步"等
_ORDER_RE = re.compile(r"^(?:第)?([0-9一二三四五六七八九十]+)(?:步|\.|\))")

# ============ 时间顺序关键词 ============
# 这些词表示步骤的先后顺序
_TEMPORAL_HEAD = ("首先", "接着", "然后", "随后", "最后", "紧接着", "之后", "最终", "step")

# ============ 中文数字映射表 ============
# 用于将"第一步"中的"一"转换为数字1
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_order(token: str) -> int | None:
    """解析步骤编号，将数字或中文数字转换为整数。

    Args:
        token: 编号字符串，可能是"1"、"一"、"二"等

    Returns:
        整数编号，解析失败返回None

    示例：
        >>> _parse_order("1")
        1
        >>> _parse_order("一")
        1
        >>> _parse_order("abc")
        None
    """
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    return None


def detect_procedure_chunks(chunks: list[Chunk]) -> None:
    """识别所有流程切片，标记它们的属性。

    这个函数会修改切片对象的以下属性：
    - is_procedure_chunk: 标记为流程切片
    - procedure_order: 步骤编号（如果能解析出来）

    识别规则：
    1. 切片开头匹配数字编号模式（如"第1步"、"1."）
    2. 切片开头包含时间顺序词（如"首先"、"然后"）

    Args:
        chunks: 切片列表（会被原地修改）
    """
    for chunk in chunks:
        # 只处理子切片，排除图片、表格、父级切片
        if chunk.layer != "child" or chunk.is_table_chunk or chunk.is_image_chunk:
            continue

        # 检查切片开头（前80字符）
        text = chunk.content[:80].strip().lower()

        # 尝试匹配数字编号
        match = _ORDER_RE.match(text)
        if match:
            chunk.is_procedure_chunk = True
            chunk.procedure_order = _parse_order(match.group(1))
            continue

        # 尝试匹配时间顺序词
        if any(text.startswith(head.lower()) for head in _TEMPORAL_HEAD):
            chunk.is_procedure_chunk = True


def link_procedure(chunks: list[Chunk]) -> int:
    """为同一章节内的流程切片建立顺序关系。

    工作原理：
    1. 按 parent_id 分组（同一章节内的步骤才关联）
    2. 在每个组内找出所有流程切片
    3. 按步骤编号排序
    4. 相邻步骤建立 next_step/prev_step 关系

    Args:
        chunks: 切片列表

    Returns:
        新增的关系数量

    示例：
        >>> chunks = [
        ...     Chunk(content="第1步：安装", parent_id="chapter1"),
        ...     Chunk(content="第2步：配置", parent_id="chapter1"),
        ...     Chunk(content="第3步：启动", parent_id="chapter1"),
        ... ]
        >>> detect_procedure_chunks(chunks)
        >>> added = link_procedure(chunks)
        >>> # 切片0 -> 切片1: next_step
        >>> # 切片1 -> 切片0: prev_step
        >>> # 切片1 -> 切片2: next_step
        >>> # 切片2 -> 切片1: prev_step
    """
    added = 0

    # 按 parent_id 分组
    groups: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        if chunk.layer == "child" and chunk.parent_id:
            groups.setdefault(chunk.parent_id, []).append(chunk)

    # 对每个组建立流程关系
    for group in groups.values():
        # 提取组内的流程切片
        proc = [chunk for chunk in group if chunk.is_procedure_chunk]

        # 按步骤编号排序（无编号的排在后面）
        proc.sort(key=lambda item: (item.procedure_order or 999, item.chunk_index))

        # 至少要有2个流程切片才建立关系
        if len(proc) < 2:
            continue

        # 为相邻步骤建立关系
        for prev, curr in zip(proc, proc[1:]):
            before = len(prev.relations)
            add_relation(prev, curr.id, "next_step", source="rule", evidence=curr.content[:20])
            add_relation(curr, prev.id, "prev_step", source="rule", evidence=prev.content[:20])
            if len(prev.relations) > before:
                added += 1

    return added
