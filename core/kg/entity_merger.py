"""
实体合并器模块
===============

【知识图谱中为什么需要合并？】
想象你在一本书里多次看到"AI"和"人工智能"这两个词。
你心里知道它们是同一个东西，但计算机不知道。

如果知识图谱里分别存储"AI"和"人工智能"，就会出现：
- 重复的节点
- 用户搜索"AI"找不到"人工智能"的相关信息
- 知识图谱看起来很乱，不专业

【这个模块的作用】
这个模块是一个"智能去重器"，它会把本质上相同的实体合并成一个。

具体来说：
1. 识别哪些实体其实是同一个东西（比如"AI"和"人工智能"）
2. 把它们合并成一个实体
3. 合并后的实体有：
   - 一个标准名称（选最长的）
   - 所有别名（aliases）
   - 所有来源文档

【举例说明】
假设文档中提到了：
- 第一次："AI（人工智能）正在改变世界"
- 第二次："人工智能技术发展迅速"

不合并的话，会有两个实体：
- 实体1: 名字"AI"，来源文档A
- 实体2: 名字"人工智能"，来源文档B

合并后：
- 实体: 名字"人工智能"（选最长的），别名["AI"]，来源文档[A, B]

【工作流程】
1. 遍历所有提取出来的原始实体
2. 检查是否已经存在相似的实体
3. 如果存在，合并信息
4. 如果不存在，创建新实体

【什么算"相似"？】
- 类型相同（都是"概念"或都是"设备"）
- 名字相同或互为别名（不区分大小写）
"""

from __future__ import annotations

from core.models.entity import Entity
from core.models.extracted_entity import ExtractedEntity


# ============================================
# 允许的实体类型
# ============================================
# 知识图谱中只能有这几种类型的实体，其他类型会被归为"Unknown"
ALLOWED_ENTITY_TYPES = {
    "Concept",      # 概念（如"机器学习"、"深度学习"）
    "Procedure",    # 流程/步骤（如"数据预处理流程"）
    "Equipment",    # 设备（如"GPU服务器"）
    "Standard",     # 标准（如"ISO 9001"）
    "Quantity",     # 数量/指标（如"准确率"）
    "Person",       # 人物（如"Geoffrey Hinton"）
    "Time",         # 时间（如"2023年"）
}


def _normalize_name(value: str) -> str:
    """
    标准化实体名称（内部辅助函数）

    【作用】
    把实体名称整理成统一格式：
    - 去掉首尾空格
    - 把多个连续空格合并成一个

    【为什么要标准化？】
    "机器学习  " 和 "  机器学习" 和 "机器学习" 应该被视为同一个实体。
    标准化后，它们都变成 "机器学习"。

    【举例】
    输入: "  深度  学习  "
    输出: "深度 学习"
    """
    return " ".join(value.strip().split())


def _unique_ordered(values: list[str]) -> list[str]:
    """
    去重并保持顺序（内部辅助函数）

    【作用】
    从列表中去除重复项，但保持原有的顺序。

    【为什么需要这个函数？】
    合并实体时，会收集多个来源的别名，可能有重复。
    比如：实体A有别名["AI", "机器智能"]，实体B有别名["AI", "ML"]
    合并后应该是["AI", "机器智能", "ML"]，而不是["AI", "机器智能", "AI", "ML"]

    【去重逻辑】
    - 不区分大小写（"AI"和"ai"视为相同）
    - 保持第一次出现的顺序

    【举例】
    输入: ["AI", "人工智能", "ai", "机器智能", "人工智能"]
    输出: ["AI", "人工智能", "机器智能"]
    """
    ordered: list[str] = []
    seen: set[str] = set()

    for value in values:
        # 标准化名称
        normalized = _normalize_name(value)
        if not normalized:
            continue

        # 转小写用于比较（不区分大小写）
        lowered = normalized.casefold()

        # 如果没见过，加入结果列表
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(normalized)

    return ordered


class EntityMerger:
    """
    实体合并器 - 把重复的实体合并成一个

    【核心职责】
    这就像是一个"档案整理员"——
    你给它一堆零散的实体记录，它负责把相同的合并，整理出一份干净的档案。

    【主要方法】
    - merge(): 主入口，执行合并操作
    - _find_match(): 查找是否有可合并的实体
    - _merge_into(): 把新信息合并到已有实体中

    【使用示例】
    merger = EntityMerger()
    entities = merger.merge(kb_id="kb_001", raw=[
        (extracted_entity1, "chunk_1"),
        (extracted_entity2, "chunk_2"),
    ])
    """

    def merge(self, kb_id: str, raw: list[tuple[ExtractedEntity, str]]) -> list[Entity]:
        """
        合并实体 - 主入口方法

        【参数说明】
        kb_id: 知识库ID（标识这些实体属于哪个知识库）
        raw: 原始实体列表，每个元素是一个元组：(提取的实体, 来源文档ID)
            - ExtractedEntity: 从文本中提取出来的原始实体信息
            - str: 这个实体来自哪个文档片段

        【返回值】
        返回合并后的实体列表，每个实体都是唯一的（没有重复）

        【工作流程 - 用大白话说】
        1. 准备一个空列表，存放合并后的实体
        2. 遍历每个原始实体：
           a. 标准化名称和别名
           b. 检查是否已经有相似的实体
           c. 如果有，把新信息合并进去
           d. 如果没有，创建一个新实体
        3. 返回最终的实体列表

        【为什么这样设计？】
        采用"增量合并"的策略：
        - 每来一个新实体，先检查是否已有相似的
        - 有就合并，没有就新建
        - 这样可以保证最终列表中没有重复

        【合并策略】
        - 名称：选最长的作为标准名称（"人工智能"优于"AI"）
        - 别名：收集所有不重复的别名
        - 来源：收集所有文档片段ID
        """
        entities: list[Entity] = []

        # 遍历所有原始实体
        for extracted, source_chunk in raw:
            # 标准化实体名称
            name = _normalize_name(extracted.name)
            if not name:
                # 名称为空，跳过
                continue

            # 检查实体类型是否合法，不合法的归为"Unknown"
            entity_type = extracted.type if extracted.type in ALLOWED_ENTITY_TYPES else "Unknown"

            # 标准化并去重别名
            aliases = _unique_ordered(extracted.aliases)

            # 查找是否已有可合并的实体
            matched = self._find_match(entities, name, entity_type, aliases)

            if matched is None:
                # 没找到相似的，创建新实体
                entities.append(
                    Entity(
                        kb_id=kb_id,
                        name=name,
                        aliases=aliases,
                        type=entity_type,
                        definition=None,  # 定义稍后生成
                        source_chunks=[source_chunk],
                    )
                )
                continue

            # 找到相似的，合并进去
            self._merge_into(matched, name, aliases, source_chunk)

        return entities

    def _find_match(
        self,
        entities: list[Entity],
        name: str,
        entity_type: str,
        aliases: list[str],
    ) -> Entity | None:
        """
        查找可以合并的实体

        【作用】
        在已有实体列表中，查找与新实体"相同"的那个。

        【什么算"相同"？】
        满足以下两个条件才算相同：
        1. 类型相同（都是"Concept"或都是"Equipment"等）
        2. 名称或别名有交集（不区分大小写）

        【举例】
        已有实体: 名称="AI", 类型="Concept", 别名=["人工智能"]
        新实体: 名称="人工智能", 类型="Concept", 别名=["AI"]

        判断：
        - 类型相同（都是Concept）✓
        - 名称/别名有交集（"AI"在两个实体的名称/别名中都出现）✓
        - 结论：可以合并

        【参数说明】
        entities: 已有的实体列表
        name: 新实体的名称
        entity_type: 新实体的类型
        aliases: 新实体的别名列表

        【返回值】
        返回找到的可合并实体，如果没有返回None
        """
        # 收集新实体的所有名称（名称 + 别名），转小写
        candidate_names = {name.casefold(), *(alias.casefold() for alias in aliases)}

        # 遍历已有实体
        for entity in entities:
            # 类型不同，跳过（概念不能和设备合并）
            if entity.type != entity_type:
                continue

            # 收集已有实体的所有名称（名称 + 别名），转小写
            known_names = {entity.name.casefold(), *(alias.casefold() for alias in entity.aliases)}

            # 检查是否有交集（两个集合有共同元素）
            if candidate_names & known_names:
                # 有交集，说明是同一个实体
                return entity

        # 没找到可合并的
        return None

    def _merge_into(
        self,
        entity: Entity,
        name: str,
        aliases: list[str],
        source_chunk: str,
    ) -> None:
        """
        将新实体的信息合并到已有实体中

        【作用】
        把新发现的信息补充到已有实体中，让它更完整。

        【合并什么？】
        1. 名称：在名称和别名中，选最长的作为标准名称
        2. 别名：合并所有不重复的别名
        3. 来源：添加新的文档片段ID

        【为什么选最长的名称？】
        通常更长的名称更正式、更清晰：
        - "人工智能" 比 "AI" 更正式
        - "机器学习" 比 "ML" 更清晰

        【举例】
        已有实体: 名称="AI", 别名=["人工智能"], 来源=["chunk_1"]
        新信息: 名称="人工智能", 别名=["AI技术"], 来源="chunk_2"

        合并后:
        - 名称候选: ["AI", "人工智能"] → 选最长的"人工智能"
        - 所有名称: ["AI", "人工智能", "AI技术"] → 去重并去掉标准名称
        - 别名: ["AI", "AI技术"]（去掉标准名称"人工智能"）
        - 来源: ["chunk_1", "chunk_2"]

        【参数说明】
        entity: 已有的实体（会被直接修改）
        name: 新实体的名称
        aliases: 新实体的别名
        source_chunk: 新实体来自的文档片段ID
        """
        # 从新旧名称中选择最长的作为标准名称
        direct_names = _unique_ordered([entity.name, name])
        canonical_name = max(direct_names, key=len)

        # 收集所有名称（已有名称 + 已有别名 + 新名称 + 新别名），去重
        all_names = _unique_ordered([entity.name, *entity.aliases, name, *aliases])

        # 更新实体的标准名称
        entity.name = canonical_name

        # 更新别名：从所有名称中去掉标准名称，剩下的就是别名
        entity.aliases = [
            candidate
            for candidate in all_names
            if candidate.casefold() != canonical_name.casefold()
        ]

        # 添加新的来源文档（如果还没有的话）
        if source_chunk not in entity.source_chunks:
            entity.source_chunks.append(source_chunk)
