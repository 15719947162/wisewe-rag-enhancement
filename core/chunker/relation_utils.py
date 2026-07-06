"""
关系工具函数 - 切片关系管理的辅助函数

这个模块提供了管理切片关系的通用工具函数，被各个链接器模块使用。

什么是切片关系？
===============
在 RAG 系统中，切片之间可能存在各种关系：
- refers_to: 文本引用了图片或表格
- adjacent: 切片在物理位置上相邻
- sibling: 切片属于同一章节
- next_step/prev_step: 流程中的前后步骤
- cause_of/effect_of: 因果关系
- semantic_similar: 语义相似
- duplicate_of: 重复内容

这些关系存储在每个切片的 `relations` 字段中，是一个 Relation 对象列表。

主要功能：
=========
- add_relation: 添加单向关系（A -> B）
- add_bidirectional_relation: 添加双向关系（A <-> B）
- has_relation: 检查关系是否存在
- filter_by_type: 按关系类型过滤

去重机制：
=========
每个切片维护一个去重集合，避免重复添加相同的关系。
判断依据：(目标切片ID, 关系类型) 的组合。

使用示例：
=========
    from core.chunker.relation_utils import add_bidirectional_relation

    # 建立双向关系
    add_bidirectional_relation(
        chunk_a,
        chunk_b,
        rel_type="refers_to",
        weight=0.9,
        source="rule",
        evidence="图1"
    )
"""

from core.models.content_block import Chunk
from core.models.relation import Relation, RelSource, RelType

# 浮点数精度容差，用于权重归一化
_WEIGHT_EPSILON = 1e-9


def has_relation(chunk: Chunk, target_id: str, rel_type: RelType | None = None) -> bool:
    """检查切片是否已存在某个关系。

    Args:
        chunk: 要检查的切片
        target_id: 目标切片 ID
        rel_type: 关系类型（可选，不指定则检查所有类型）

    Returns:
        True 表示关系已存在

    示例：
        >>> has_relation(chunk, "chunk_002", "refers_to")
        True
        >>> has_relation(chunk, "chunk_002")
        True  # 只要存在任何类型的关系就返回 True
    """
    return any(
        relation.target_id == target_id and (rel_type is None or relation.rel_type == rel_type)
        for relation in chunk.relations
    )


def add_relation(
    chunk: Chunk,
    target_id: str,
    rel_type: RelType,
    weight: float = 1.0,
    source: RelSource = "rule",
    evidence: str = "",
) -> None:
    """为切片添加一个关系（单向）。

    这个函数会自动去重，如果关系已存在则不添加。

    Args:
        chunk: 源切片
        target_id: 目标切片 ID
        rel_type: 关系类型（如 "refers_to"、"adjacent" 等）
        weight: 关系权重（0-1之间，越大越重要）
        source: 关系来源（"rule" 表示规则推导，"embedding" 表示向量相似）
        evidence: 证据文本（用于解释为什么建立这个关系）

    注意：
        - 这是单向关系：A -> B
        - 如需双向关系，请使用 add_bidirectional_relation
    """
    if has_relation(chunk, target_id, rel_type):
        return  # 已存在，跳过

    chunk.relations.append(
        Relation(
            target_id=target_id,
            rel_type=rel_type,
            weight=_normalize_weight(weight),
            source=source,
            evidence=evidence[:20],  # 截断证据文本
        )
    )


def add_bidirectional_relation(
    src: Chunk,
    dst: Chunk,
    rel_type: RelType,
    weight: float = 1.0,
    source: RelSource = "rule",
    evidence: str = "",
) -> None:
    """为两个切片添加双向关系。

    如果 A 关联 B，则 B 也关联 A。
    比如：文本 A 引用图片 B，那么图片 B 也被文本 A 引用。

    Args:
        src: 源切片
        dst: 目标切片
        rel_type: 关系类型
        weight: 关系权重
        source: 关系来源
        evidence: 证据文本
    """
    add_relation(src, dst.id, rel_type, weight=weight, source=source, evidence=evidence)
    add_relation(dst, src.id, rel_type, weight=weight, source=source, evidence=evidence)


def filter_by_type(relations: list[Relation], types: set[RelType]) -> list[Relation]:
    """按关系类型过滤关系列表。

    Args:
        relations: 关系列表
        types: 允许的关系类型集合

    Returns:
        过滤后的关系列表

    示例：
        >>> filter_by_type(chunk.relations, {"refers_to", "adjacent"})
        # 只返回引用和邻近关系
    """
    return [relation for relation in relations if relation.rel_type in types]


def _normalize_weight(weight: float) -> float:
    """归一化权重值，处理浮点数精度问题。

    主要处理边界情况：
    - 1.000000001 -> 1.0
    - -0.000000001 -> 0.0

    Args:
        weight: 原始权重

    Returns:
        归一化后的权重（0-1之间）
    """
    if 1.0 < weight <= 1.0 + _WEIGHT_EPSILON:
        return 1.0
    if -_WEIGHT_EPSILON <= weight < 0.0:
        return 0.0
    return weight
