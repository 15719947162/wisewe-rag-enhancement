"""
【核心数据模型模块 - relation.py】

本文件定义了切片间关系（Relation）的数据结构，用于表示知识图谱中切片之间的关联关系。

主要包含三类核心定义：
1. RelType - 关系类型枚举（定义切片之间可能的关系类型）
2. RelSource - 关系来源枚举（定义关系是如何被发现的）
3. Relation - 关系模型（具体的关系实例）

数据流向：
ContentBlock → Chunk → Relation（切片生成时建立关系）

应用场景：
1. 检索扩展：根据关系找到相关切片
2. 知识图谱：构建切片间的关系网络
3. 智能推荐：基于关系推荐相关内容
4. 问答系统：追溯答案的证据链

作者：RAG 项目组
创建时间：2024
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ============================================================================
# 关系类型定义
# ============================================================================

RelType = Literal[
    "refers_to",         # 引用关系：一个切片引用了另一个切片的内容
    "adjacent",          # 相邻关系：在原文中物理相邻（如前后段落）
    "sibling",           # 兄弟关系：属于同一章节或同一主题下的并列内容
    "semantic_similar",  # 语义相似：内容主题相似，可能讨论相同概念
    "duplicate_of",      # 重复关系：内容高度重复或完全相同
    "next_step",         # 下一步：流程/步骤中的下一个环节
    "prev_step",         # 上一步：流程/步骤中的上一个环节
    "cause_of",          # 因果关系：一个切片是另一个切片的原因
    "effect_of",         # 结果关系：一个切片是另一个切片的结果
    "mentions",          # 提及关系：提到了另一个切片中的关键实体或概念
    "explains",          # 解释关系：对另一个切片内容的详细解释
    "example_of",        # 举例关系：提供具体的例子说明另一个切片的概念
    "depends_on",        # 依赖关系：理解一个切片需要先理解另一个切片
    "contrasts",         # 对比关系：与另一个切片的内容形成对比或差异
    "co_occurs",         # 共现关系：在同一上下文中经常一起出现
]
"""
【关系类型枚举 - RelType】

定义切片之间可能存在的所有关系类型。使用 Literal 类型而非 Enum，
因为这样更灵活，可以直接与字符串比较，且易于扩展。

知识点 - Python Literal 类型：
- Literal 是 typing 模块的类型提示，限制变量只能取特定的几个值
- 与 Enum 相比更轻量，适合简单的枚举场景
- 类型检查器可以检查赋值是否合法
- 可以直接使用字符串，无需 .value 访问

关系类型分类：
-----------------
1. 【结构关系】基于文档物理结构
   - adjacent: 相邻关系，原文中物理相邻
   - sibling: 兄弟关系，同一章节下的并列内容

2. 【语义关系】基于内容语义相似性
   - semantic_similar: 语义相似，讨论相同主题
   - duplicate_of: 内容重复
   - refers_to: 引用关系

3. 【流程关系】基于步骤顺序
   - next_step: 下一步骤
   - prev_step: 上一步骤

4. 【逻辑关系】基于因果逻辑
   - cause_of: 因果关系（A 导致 B）
   - effect_of: 结果关系（B 是 A 的结果）
   - depends_on: 依赖关系

5. 【内容关系】基于内容关联
   - mentions: 提及关系
   - explains: 解释关系
   - example_of: 举例关系
   - contrasts: 对比关系
   - co_occurs: 共现关系

详细说明：
---------

refers_to（引用关系）：
- 定义：切片 A 明确引用了切片 B 的内容
- 例子：文档中说"如上文所述..."引用了前面的内容
- 应用：追溯引用链，构建引用网络

adjacent（相邻关系）：
- 定义：切片在原文中物理相邻（前后段落、相邻页面）
- 例子：第 3 段和第 4 段，或者跨页的连续内容
- 应用：上下文理解，阅读顺序还原

sibling（兄弟关系）：
- 定义：属于同一父切片（章节）下的并列内容
- 例子：同一章节下的 3.1 节和 3.2 节
- 应用：章节导航，并列内容推荐

semantic_similar（语义相似）：
- 定义：内容主题相似，讨论相同概念或话题
- 发现方式：通过 embedding 向量相似度计算
- 例子：两段都讨论"数据清洗"，但角度不同
- 应用：相似内容推荐，去重

duplicate_of（重复关系）：
- 定义：内容高度重复或完全相同
- 发现方式：文本相似度 > 0.95 或 LLM 判断
- 例子：同一内容在不同章节重复出现
- 应用：去重，识别冗余内容

next_step / prev_step（步骤关系）：
- 定义：流程中的前后步骤
- 发现方式：procedure_linker 模块检测流程模式
- 例子：步骤1"数据采集" → 步骤2"数据清洗"
- 应用：流程导航，步骤推荐

cause_of / effect_of（因果关系）：
- 定义：逻辑上的因果关联
- 发现方式：LLM 分析或因果关键词检测
- 例子："系统过载"导致"性能下降"
- 应用：因果链追溯，根因分析

mentions（提及关系）：
- 定义：切片中提到了另一个切片的关键实体或概念
- 发现方式：实体识别 + 共指消解
- 例子：段落 A 提到"BERT 模型"，段落 B 详细介绍 BERT
- 应用：实体关联，概念跳转

explains（解释关系）：
- 定义：切片 A 对切片 B 的内容进行了详细解释
- 例子：标题切片（"什么是机器学习"）→ 正文切片（详细解释）
- 应用：概念学习，深度阅读

example_of（举例关系）：
- 定义：切片提供了具体例子说明另一个切片的概念
- 例子：概念切片（"监督学习"）→ 例子切片（"图像分类就是监督学习的一个例子"）
- 应用：案例学习，概念理解

depends_on（依赖关系）：
- 定义：理解切片 A 需要先理解切片 B
- 例子：高级概念（"反向传播"）依赖基础概念（"梯度下降"）
- 应用：学习路径规划，前置知识推荐

contrasts（对比关系）：
- 定义：切片内容形成对比或差异
- 例子："方法 A 的优点"与"方法 A 的缺点"形成对比
- 应用：全面理解，多角度分析

co_occurs（共现关系）：
- 定义：两个切片在同一上下文中经常一起出现
- 发现方式：统计分析共现频率
- 例子："数据清洗"和"数据标准化"经常在同一个文档中出现
- 应用：协同推荐，主题发现

示例用法：
---------
    >>> from core.models.relation import RelType
    >>> rel_type: RelType = "semantic_similar"  # 类型检查通过
    >>> rel_type == "semantic_similar"
    True
"""

RelSource = Literal["rule", "embedding", "llm", "entity"]
"""
【关系来源枚举 - RelSource】

定义关系是如何被发现的，用于追溯关系的可靠性。

知识点 - 关系发现方式的重要性：
- 不同来源的关系有不同的可靠性
- LLM 发现的关系可能需要人工验证
- 规则发现的关系确定性高但覆盖面有限
- Embedding 发现的关系基于统计相似性

来源类型说明：
-------------

rule（规则发现）：
- 定义：通过预定义的规则或启发式方法发现
- 例子：adjacent 关系通过原文顺序判断，sibling 关系通过章节层级判断
- 特点：确定性高，但覆盖面有限
- 可靠性：⭐⭐⭐⭐⭐（最高）

embedding（向量相似度发现）：
- 定义：通过文本向量相似度计算发现
- 例子：semantic_similar 关系，通过余弦相似度 > 0.8 判断
- 特点：统计驱动，能发现隐含相似性
- 可靠性：⭐⭐⭐⭐（较高）

llm（大语言模型发现）：
- 定义：通过 LLM 分析文本后判断关系
- 例子：cause_of、effect_of 等复杂逻辑关系
- 特点：能理解深层语义，但可能产生幻觉
- 可靠性：⭐⭐⭐（中等，建议验证）

entity（实体识别发现）：
- 定义：通过实体识别和共指消解发现
- 例子：mentions 关系，通过识别相同的实体
- 特点：基于命名实体识别和消解，准确度取决于 NER 质量
- 可靠性：⭐⭐⭐⭐（较高，但依赖 NER 准确度）

应用场景：
---------
    # 根据来源过滤关系
    high_confidence_relations = [
        r for r in chunk.relations
        if r.source in ["rule", "embedding"]
    ]

    # LLM 发现的关系需要验证
    llm_relations = [
        r for r in chunk.relations
        if r.source == "llm"
    ]
"""


# ============================================================================
# 关系模型定义
# ============================================================================

class Relation(BaseModel):
    """
    【关系模型 - 切片间关系的具体实例】

    表示一个切片与另一个切片之间的关系。
    这是知识图谱的基础边（Edge）结构。

    知识点 - Pydantic BaseModel：
    - Pydantic 是 Python 数据验证库
    - BaseModel 提供自动类型检查、JSON 序列化/反序列化
    - 字段类型注解会被自动验证
    - Field 用于设置默认值、约束、描述等

    数据结构示例：
    -------------
        # 语义相似关系（embedding 发现）
        {
            "target_id": "550e8400-e29b-41d4-a716-446655440000",
            "rel_type": "semantic_similar",
            "weight": 0.85,
            "source": "embedding",
            "evidence": "两段都讨论数据清洗方法，余弦相似度 0.85"
        }

        # 因果关系（LLM 发现）
        {
            "target_id": "660e8400-e29b-41d4-a716-446655440001",
            "rel_type": "cause_of",
            "weight": 0.92,
            "source": "llm",
            "evidence": "系统过载导致性能下降，明确的因果表述"
        }

        # 相邻关系（规则发现）
        {
            "target_id": "770e8400-e29b-41d4-a716-446655440002",
            "rel_type": "adjacent",
            "weight": 1.0,
            "source": "rule",
            "evidence": "原文第 5 段和第 6 段连续"
        }

        # 步骤关系（规则发现）
        {
            "target_id": "880e8400-e29b-41d4-a716-446655440003",
            "rel_type": "next_step",
            "weight": 1.0,
            "source": "rule",
            "evidence": "步骤 2 紧接步骤 1"
        }

    实际使用场景：
    -------------
        # 创建关系
        relation = Relation(
            target_id="target-chunk-uuid",
            rel_type="semantic_similar",
            weight=0.85,
            source="embedding",
            evidence="内容主题相似"
        )

        # 添加到切片
        chunk.relations.append(relation)

        # 检索时根据关系扩展
        def expand_by_relations(chunk, rel_types):
            '''根据关系类型扩展相关切片'''
            related_ids = [
                r.target_id
                for r in chunk.relations
                if r.rel_type in rel_types
            ]
            return fetch_chunks_by_ids(related_ids)

        # 根据权重过滤弱关系
        strong_relations = [
            r for r in chunk.relations
            if r.weight > 0.8
        ]

    字段说明：
    ---------
        target_id: 目标切片 ID（被关联的切片）
        rel_type: 关系类型（RelType 中定义的类型）
        weight: 关系权重（0.0-1.0，表示关系的强弱或置信度）
        source: 关系来源（RelSource 中定义的来源）
        evidence: 关系证据（解释为什么存在这个关系）
    """

    target_id: str
    """
    目标切片 ID：被关联的切片的唯一标识符。

    这是关系的"目标端点"，表示当前切片与哪个切片有关系。
    格式：UUID 字符串，如 "550e8400-e29b-41d4-a716-446655440000"

    示例：
        如果切片 A 与切片 B 有关系：
        - A.relations 包含 Relation(target_id=B.id, ...)
        - 这是一个有向关系：A → B
    """

    rel_type: RelType
    """
    关系类型：定义关系的语义含义。

    取值范围：RelType 中定义的 15 种关系类型之一。

    常见类型：
    - semantic_similar: 语义相似（最常见，用于相似推荐）
    - adjacent: 相邻关系（用于上下文理解）
    - next_step/prev_step: 流程步骤（用于流程导航）
    - cause_of/effect_of: 因果关系（用于因果分析）
    """

    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    """
    关系权重：表示关系的强弱或置信度。

    约束：必须在 [0.0, 1.0] 范围内（ge=0.0 表示 >= 0.0，le=1.0 表示 <= 1.0）
    默认值：1.0（表示确定性关系）

    权重含义：
    - 1.0: 确定性关系（如 adjacent、sibling 等结构关系）
    - 0.8-0.9: 高置信度关系（如语义相似度很高的内容）
    - 0.5-0.7: 中等置信度关系
    - 0.0-0.4: 低置信度关系（可能是弱关联）

    应用：
    - 过滤弱关系：weight > 0.7
    - 排序推荐：按 weight 降序
    - 评分计算：考虑 weight 作为相关性因子
    """

    source: RelSource
    """
    关系来源：定义关系是如何被发现的。

    取值范围：RelSource 中定义的 4 种来源之一。

    不同来源的可靠性：
    - rule: ⭐⭐⭐⭐⭐ 规则发现，确定性高
    - embedding: ⭐⭐⭐⭐ 向量相似，统计驱动
    - llm: ⭐⭐⭐ LLM 分析，可能需验证
    - entity: ⭐⭐⭐⭐ 实体识别，依赖 NER 质量

    应用：
    - 高置信度过滤：source in ["rule", "embedding"]
    - 人工验证：source == "llm"
    """

    evidence: str = ""
    """
    关系证据：解释为什么存在这个关系。

    默认值：空字符串（可以不提供证据）

    内容建议：
    - 规则发现：描述触发的规则，如"原文第 5-6 段连续"
    - Embedding 发现：记录相似度分数，如"余弦相似度 0.85"
    - LLM 发现：记录 LLM 的推理过程，如"明确提到'导致'关键词"
    - Entity 发现：记录匹配的实体，如"两段都提到'数据清洗'"

    示例：
        evidence="两段都讨论数据清洗方法，主题相似度高"
        evidence="步骤 2 使用步骤 1 的输出作为输入"
        evidence="明确表述'因此'，表示因果关系"

    应用：
    - 调试：理解关系为何被建立
    - 解释：向用户解释推荐理由
    - 验证：人工检查关系合理性
    """
