"""
切片链接器 - 建立切片之间的关联关系

这个模块的主要作用是在切片之间建立"关系网"，让原本孤立的切片能够互相找到对方。

为什么要建立关系？
================
在 RAG（检索增强生成）系统中，用户提问时通常会检索相关的文本片段。
但问题是：有些文本片段提到了"如图1所示"或"见表2"，而图和表可能在其他切片里。
如果没有关系，检索到的文本就可能缺少关键的图表信息，导致回答不完整。

解决方案：
=========
本模块通过4条规则自动建立切片之间的关联关系：

规则1 - 引用匹配（最精准）：
    文本中明确说"如图1-3-3-6所示"，我们就在所有切片中找到标题为"图1-3-3-6"的图片切片，
    然后建立双向链接。这样检索到这段文本时，就能顺藤摸瓜找到对应的图片。

规则2 - 邻近关联（兜底策略）：
    有些图片/表格没有被文本明确引用，但它们在物理位置上很接近（比如前后5个切片内）。
    这种情况下，我们就把它们关联起来，因为它们很可能在讲同一件事。

规则3 - 同父级关联（兄弟关系）：
    在层次化切片中，同一个章节（parent_id相同）下的所有图片/表格和文本，
    即使没有直接引用关系，也关联起来。因为同一章节的内容通常相关。

规则4 - 增强切片继承（传递关系）：
    增强切片（LLM生成的摘要）会继承其父切片的所有关系，避免信息丢失。

使用示例：
=========
    from core.chunker.linker import link_related_chunks

    chunks = chunker.chunk(blocks)  # 先完成切片
    chunks = link_related_chunks(chunks)  # 再建立关联

注意：
=====
- 所有规则都是"规则驱动"的，不依赖大模型，保证可审计、可解释
- 关系是双向的：如果A关联B，则B也关联A
- 每个切片的 relations 列表会记录所有关联信息
"""
from __future__ import annotations

import re

from core.models.content_block import Chunk
from core.models.relation import RelSource, RelType, Relation

# ============ 正则表达式定义 ============
# 这些正则用于从文本中提取"图X-X"和"表X-X"这样的引用

# 数字编号的正则，支持阿拉伯数字和全角数字，支持范围（如1-3-3-6）
_REF_NUMBER = r"([0-9０-９]+(?:\s*[-－–—]\s*[0-9０-９]+){0,6})"

# 匹配"图1-3-3-6"、"如图1-3-3-6所示"等格式
_FIG_REF = re.compile(rf"(?:如\s*)?图\s*{_REF_NUMBER}")

# 匹配"表1-3-3-1"、"如表1-3-3-1所示"等格式
_TABLE_REF = re.compile(rf"(?:如\s*)?表\s*{_REF_NUMBER}")

# 匹配图片/表格标题中的编号（用于识别哪个切片是哪个图/表）
_FIG_LABEL = re.compile(rf"图\s*{_REF_NUMBER}")
_TABLE_LABEL = re.compile(rf"表\s*{_REF_NUMBER}")

# 全角数字到半角数字的转换表（用于统一编号格式）
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_ref(ref: str) -> str:
    """标准化引用编号，便于匹配比较。

    把各种格式的编号统一成标准形式：
    - 全角数字转半角："１２３" -> "123"
    - 移除空白和横线："1 - 2 - 3" -> "123"

    这样"图1-3-3-6"、"图１－３－３－６"、"图 1-3-3-6" 都能匹配到同一个图。

    Args:
        ref: 原始编号字符串

    Returns:
        标准化后的编号字符串
    """
    normalized = ref.translate(_FULLWIDTH_DIGITS)
    return re.sub(r"[\s\-－–—]", "", normalized)


def _extract_refs(pattern: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    """从文本中提取所有引用。

    比如文本中有"如图1-3-3-6和图2-5所示"，会提取出两个引用。

    Args:
        pattern: 正则表达式模式（图引用或表引用）
        text: 要搜索的文本

    Returns:
        列表，每项是 (标准化编号, 原始匹配文本) 的元组
        例如：[("1336", "图1-3-3-6"), ("25", "图2-5")]
    """
    refs: list[tuple[str, str]] = []
    for match in pattern.finditer(text or ""):
        refs.append((_normalize_ref(match.group(1)), match.group(0)))
    return refs


def link_related_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """为所有切片建立关联关系（主入口函数）。

    这是链接器的核心函数，依次执行4条规则来建立切片之间的关联。

    执行流程：
    1. 分类切片：图片切片、表格切片、文本切片
    2. 建立索引：记录已有关系，避免重复添加
    3. 规则1：解析文本中的"图X"、"表X"引用，匹配到对应的图/表切片
    4. 规则2：为没有引用关系的图/表，关联最近的文本切片
    5. 规则3：同一章节（parent_id相同）的图/表与文本互相关联
    6. 规则4：增强切片继承父切片的所有关系

    Args:
        chunks: 切片列表（通常是 chunker.chunk() 的输出）

    Returns:
        同一个列表（原地修改，添加了关系后返回）

    示例：
        >>> chunks = paragraph_strategy.chunk(blocks)
        >>> chunks = link_related_chunks(chunks)
        >>> # 现在每个切片的 relations 字段已填充
    """
    if not chunks:
        return chunks

    # 第一步：给切片分类
    # 图片切片列表：(全局索引, 切片对象)
    img_chunks = [(i, c) for i, c in enumerate(chunks) if c.is_image_chunk]
    # 表格切片列表：(全局索引, 切片对象)
    table_chunks = [(i, c) for i, c in enumerate(chunks) if c.is_table_chunk]
    # 文本切片列表：排除图片、表格、父级切片、增强切片
    text_chunks = [
        (i, c)
        for i, c in enumerate(chunks)
        if not c.is_image_chunk and not c.is_table_chunk and c.layer not in ("parent", "enhanced")
    ]

    # 去重索引：记录每个切片已有 (目标ID, 关系类型) 的组合
    # 用于避免重复添加相同的关系
    relation_keys: dict[str, set[tuple[str, str]]] = {
        chunk.id: {(relation.target_id, relation.rel_type) for relation in chunk.relations}
        for chunk in chunks
    }

    def _add_relation(
        chunk: Chunk,
        target_id: str,
        rel_type: RelType,
        weight: float = 1.0,
        source: RelSource = "rule",
        evidence: str = "",
    ) -> bool:
        """添加一条关系（内部辅助函数）。

        Args:
            chunk: 要添加关系的切片
            target_id: 目标切片的ID
            rel_type: 关系类型（如"refers_to"、"adjacent"、"sibling"）
            weight: 关系权重（0-1之间，越大越重要）
            source: 关系来源（"rule"表示规则推导，"embedding"表示向量相似）
            evidence: 证据文本（用于解释为什么建立这个关系）

        Returns:
            True表示添加成功，False表示已存在（去重）
        """
        key = (target_id, rel_type)
        keys = relation_keys.setdefault(chunk.id, set())
        if key in keys:
            return False  # 已存在相同关系，跳过
        chunk.relations.append(
            Relation(
                target_id=target_id,
                rel_type=rel_type,
                weight=weight,
                source=source,
                evidence=evidence[:20],  # 截断证据文本，避免过长
            )
        )
        keys.add(key)
        return True

    def _link(a_idx: int, b_idx: int, rel_type: RelType, evidence: str) -> None:
        """建立双向关系（内部辅助函数）。

        如果A关联B，则B也关联A。
        比如文本A引用图B，那么图B也被文本A引用。

        Args:
            a_idx: 切片A的全局索引
            b_idx: 切片B的全局索引
            rel_type: 关系类型
            evidence: 证据文本
        """
        _add_relation(chunks[a_idx], chunks[b_idx].id, rel_type=rel_type, source="rule", evidence=evidence)
        _add_relation(chunks[b_idx], chunks[a_idx].id, rel_type=rel_type, source="rule", evidence=evidence)

    # ============ 规则1：引用匹配 ============
    # 文本中明确提到"图X-X-X"或"表X-X-X"，找到对应的图片/表格切片

    # 建立编号到切片索引的映射
    # fig_label_map: {"1336": 切片索引, ...} 表示"图1-3-3-6"对应哪个切片
    fig_label_map: dict[str, int] = {}
    table_label_map: dict[str, int] = {}

    # 扫描所有图片切片，提取其标题中的编号
    for idx, chunk in img_chunks:
        for key, _label in _extract_refs(_FIG_LABEL, chunk.content[:300]):
            fig_label_map.setdefault(key, idx)  # 只记录第一个匹配的

    # 扫描所有表格切片，提取其标题中的编号
    for idx, chunk in table_chunks:
        for key, _label in _extract_refs(_TABLE_LABEL, chunk.content[:300]):
            table_label_map.setdefault(key, idx)

    # 扫描所有文本切片，查找引用，建立关联
    for idx, chunk in text_chunks:
        # 查找文本中的图片引用（如"如图1-3-3-6所示"）
        for key, evidence in _extract_refs(_FIG_REF, chunk.content):
            if key in fig_label_map:
                _link(idx, fig_label_map[key], "refers_to", evidence)
        # 查找文本中的表格引用（如"如表1-3-3-1所示"）
        for key, evidence in _extract_refs(_TABLE_REF, chunk.content):
            if key in table_label_map:
                _link(idx, table_label_map[key], "refers_to", evidence)

    # ============ 规则2：邻近关联 ============
    # 对于没有明确引用关系的图片/表格，关联到最近的文本切片
    # 限制在前后5个切片范围内，因为太远的可能不相关

    for idx, chunk in img_chunks + table_chunks:
        # 如果已经有关系了（被其他规则处理过），跳过
        if chunk.related_ids:
            continue
        # 向前查找最近的文本切片（最多往前找5个）
        for back in range(idx - 1, max(idx - 5, -1), -1):
            prev = chunks[back]
            # 必须是文本切片，不能是图片、表格、父级或增强切片
            if not prev.is_image_chunk and not prev.is_table_chunk and prev.layer not in ("parent", "enhanced"):
                _link(idx, back, "adjacent", "邻近5块内")
                break

    # ============ 规则3：同父级关联 ============
    # 同一章节（parent_id相同）内的图片/表格与文本互相关联
    # 这适用于层次化切片策略，同一章节的内容通常语义相关

    # 只有当存在层次结构时才执行此规则
    if any(chunk.parent_id for chunk in chunks):
        # 按 parent_id 分组
        parent_groups: dict[str, list[int]] = {}
        for idx, chunk in enumerate(chunks):
            if chunk.parent_id and chunk.layer == "child":
                parent_groups.setdefault(chunk.parent_id, []).append(idx)

        # 对每个组，让图片/表格与所有文本互相关联
        for group in parent_groups.values():
            # 组内的媒体切片（图片或表格）
            media_in_group = [idx for idx in group if chunks[idx].is_image_chunk or chunks[idx].is_table_chunk]
            # 组内的文本切片
            text_in_group = [idx for idx in group if not chunks[idx].is_image_chunk and not chunks[idx].is_table_chunk]
            # 建立关联
            for media_idx in media_in_group:
                for text_idx in text_in_group:
                    _link(media_idx, text_idx, "sibling", "同parent_id")

    # ============ 规则4：增强切片继承关系 ============
    # 增强切片（LLM生成的摘要）继承其父切片的所有关系
    # 这样检索到增强切片时，也能顺藤摸瓜找到相关的图表

    # 建立 child 切片 ID -> 全局索引 的映射
    child_id_to_idx = {chunk.id: idx for idx, chunk in enumerate(chunks) if chunk.layer == "child"}

    # 遍历所有增强切片
    for chunk in chunks:
        # 只处理增强切片
        if chunk.layer != "enhanced" or not chunk.parent_id:
            continue
        # 找到其父切片
        parent_idx = child_id_to_idx.get(chunk.parent_id)
        if parent_idx is None:
            continue
        # 继承父切片的所有关系
        for relation in chunks[parent_idx].relations:
            _add_relation(
                chunk,
                relation.target_id,
                relation.rel_type,
                weight=relation.weight,
                source=relation.source,
                evidence=relation.evidence,
            )

    return chunks
