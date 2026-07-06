"""
【知识图谱实体模型模块 - entity.py】

本文件定义了知识图谱中的核心对象：实体（Entity）。

主要用途：
1. 知识图谱构建 - 从文本中提取实体，构建实体库
2. 实体链接 - 在检索结果中关联相关实体
3. 实体检索 - 支持基于实体的精准搜索

什么是实体？
实体是知识图谱的基本单元，代表现实世界中的一个具体事物或概念。
例如：
- 概念：机器学习、深度学习、自然语言处理
- 流程：数据预处理、模型训练、模型部署
- 设备：GPU服务器、TPU集群
- 标准：ISO 9001、IEEE 802.11
- 数量：准确率95%、延迟100ms
- 人物：图灵、Hinton
- 时间：2024年、第三季度

数据流向：
PDF文档 → 实体抽取 → Entity 对象 → 存储到知识库 → 实体检索/关联

作者：RAG 项目组
创建时间：2024
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


# ============================================================================
# 实体类型定义
# ============================================================================

EntityType = Literal[
    "Concept",      # 概念：抽象的理论或术语，如"机器学习"、"神经网络"
    "Procedure",    # 流程：操作步骤或过程，如"数据清洗"、"模型训练"
    "Equipment",    # 设备：硬件设施，如"GPU服务器"、"数据采集器"
    "Standard",     # 标准：规范或标准，如"ISO 9001"、"GB/T 19001"
    "Quantity",     # 数量：数值或度量，如"准确率95%"、"响应时间100ms"
    "Person",       # 人物：真实人物，如"图灵"、"Hinton"
    "Time",         # 时间：时间点或时间段，如"2024年"、"第三季度"
    "Unknown",      # 未知：未能识别的类型
]
"""
【实体类型类型别名】

知识点 - Python Literal 类型：
- Literal 是 Python 3.8+ 的类型提示，用于限制变量只能是特定的几个值
- 与枚举不同，Literal 只是类型别名，不是新类型
- 优点：轻量、可直接当字符串用、IDE 有提示
- 缺点：不能添加方法或属性

为什么用 Literal 而不是 Enum？
- 实体类型需要直接序列化为 JSON，Literal 更方便
- 可以直接与字符串比较：entity.type == "Concept"
- 在 Pydantic 模型中使用更简洁

实体类型详解：
1. Concept（概念）：
   - 抽象的理论、术语、方法
   - 示例：机器学习、卷积神经网络、反向传播
   - 特点：可以被定义、有层次关系（父概念/子概念）

2. Procedure（流程）：
   - 操作步骤、工作流程、算法过程
   - 示例：数据预处理、模型训练、AB测试
   - 特点：有顺序性、有输入输出、可拆分为子步骤

3. Equipment（设备）：
   - 物理设备、硬件设施、工具
   - 示例：GPU服务器、传感器、数据采集器
   - 特点：有规格参数、可能涉及采购/维护

4. Standard（标准）：
   - 技术标准、行业规范、法规条文
   - 示例：ISO 9001、IEEE 802.11、GDPR
   - 特点：有发布机构、版本号、生效日期

5. Quantity（数量）：
   - 数值、度量、指标
   - 示例：准确率95%、延迟100ms、吞吐量1000 QPS
   - 特点：有数值、有单位、可用于量化分析

6. Person（人物）：
   - 真实存在的人物
   - 示例：图灵、Hinton、LeCun
   - 特点：有生平、有贡献、可能有关联人物

7. Time（时间）：
   - 时间点或时间段
   - 示例：2024年、第三季度、2023-2024财年
   - 特点：可排序、可计算时间跨度

8. Unknown（未知）：
   - 未能识别的实体类型
   - 作为默认值使用

使用示例：
    def process_entity(entity_type: EntityType, name: str):
        if entity_type == "Concept":
            print(f"处理概念: {name}")
        elif entity_type == "Procedure":
            print(f"处理流程: {name}")
"""


# ============================================================================
# 实体模型定义
# ============================================================================

class Entity(BaseModel):
    """
    【实体模型 - 知识图谱的基本单元】

    一个 Entity 代表知识图谱中的一个节点，存储实体的基本信息。

    知识点 - 知识图谱基础：
    - 知识图谱 = 节点（实体） + 边（关系）
    - 实体是节点，存储"是什么"
    - 关系是边，存储"有什么联系"
    - 例如：(机器学习) --[属于]--> (人工智能)

    数据结构示例：
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "kb_id": "kb_tech_manual_001",
            "name": "卷积神经网络",
            "aliases": ["CNN", "ConvNet", "卷积网络"],
            "type": "Concept",
            "definition": "一种专门用于处理网格状数据的深度学习模型...",
            "source_chunks": [
                "chunk_uuid_1",
                "chunk_uuid_2",
                "chunk_uuid_3"
            ],
            "embedding": [0.123, -0.456, 0.789, ...]
        }

    实际使用场景：
        # 场景1：从文本中提取实体
        text = "卷积神经网络（CNN）是深度学习的重要模型"
        entity = Entity(
            kb_id="kb_001",
            name="卷积神经网络",
            aliases=["CNN"],
            type="Concept",
            definition="一种专门用于处理网格状数据的深度学习模型",
            source_chunks=["chunk_123"]
        )

        # 场景2：实体检索
        query = "深度学习的典型模型有哪些？"
        related_entities = entity_store.search(query, top_k=10)

        # 场景3：实体链接
        chunk = "ResNet是CNN的一种变体"
        linked_entities = linker.link(chunk)  # 返回 [CNN, ResNet]

    字段说明：
        id: 实体唯一标识符（UUID 格式）
        kb_id: 所属知识库 ID（用于多租户场景）
        name: 实体名称（主要名称）
        aliases: 别名列表（同义词、缩写、翻译等）
        type: 实体类型（Concept/Procedure/Equipment等）
        definition: 实体定义（简短描述）
        source_chunks: 来源切片 ID 列表（记录实体从哪些切片提取）
        embedding: 实体向量（用于语义检索）

    与 Chunk 的区别：
        - Chunk 是文本切片，用于检索文本内容
        - Entity 是知识实体，用于结构化知识和精准检索
        - 一个 Chunk 可能包含多个 Entity
        - Entity 可能跨越多个 Chunk
    """

    # 唯一标识符：UUID 格式，自动生成
    # 示例："550e8400-e29b-41d4-a716-446655440000"
    # 用于在知识图谱中唯一标识一个实体节点
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # 知识库 ID：标识实体属于哪个知识库
    # 用于多租户场景，隔离不同用户/项目的数据
    # 示例："kb_tech_manual_001", "kb_medical_002"
    kb_id: str

    # 实体名称：实体的主要名称
    # 这是实体的核心标识，用于展示和检索
    # 示例："卷积神经网络", "数据预处理流程", "GPU服务器"
    name: str

    # 别名列表：实体的其他名称
    # 包括：缩写、同义词、翻译、简称等
    # 用于：名称消歧、实体链接、提高召回率
    # 示例：["CNN", "ConvNet", "卷积网络"]（对于"卷积神经网络"）
    aliases: list[str] = Field(default_factory=list)

    # 实体类型：标识实体的类别
    # 用于分类、过滤、推荐
    # 默认值为 "Unknown"，表示未识别
    type: EntityType = "Unknown"

    # 实体定义：简短的描述性定义
    # 用于：实体展示、问答、知识卡片
    # 示例："一种专门用于处理网格状数据的深度学习模型"
    # 注意：不是所有实体都有定义，可能为 None
    definition: str | None = None

    # 来源切片 ID 列表：记录实体从哪些切片中提取
    # 用于：
    # 1. 实体溯源：点击实体可跳转到原文
    # 2. 证据链：支持实体的原始文本
    # 3. 更新同步：切片删除时清理实体
    # 示例：["chunk_uuid_1", "chunk_uuid_2"]
    source_chunks: list[str] = Field(default_factory=list)

    # 实体向量：用于语义检索的嵌入向量
    # 通常由 LLM embedding 模型生成（如 text-embedding-ada-002）
    # 用于：
    # 1. 语义检索：找到语义相似的实体
    # 2. 实体聚类：发现相关实体
    # 3. 推荐系统：推荐相关实体
    # 维度：取决于使用的 embedding 模型（通常 768 或 1536 维）
    # 注意：创建实体时可能不立即生成，后续批量生成
    embedding: list[float] | None = None
