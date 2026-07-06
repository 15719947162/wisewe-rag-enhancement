"""
【实体提取模型模块 - extracted_entity.py】

本文件定义了从文本中提取实体的临时数据结构,用于知识图谱构建。

主要对象:
- ExtractedEntity - 从文本切片中临时提取的实体信息

数据流向:
文本切片 → LLM 实体抽取 → ExtractedEntity 列表 → 实体合并/去重 → Entity (知识库实体)

与 Entity 的区别:
- Entity: 存储在知识库中的完整实体,包含 ID、定义、来源切片、向量等完整信息
- ExtractedEntity: 从文本中临时提取的实体,只包含基本信息,用于后续处理

比喻:
- ExtractedEntity 就像"候选人名单" - 只有姓名和基本分类
- Entity 就像"正式员工档案" - 有完整的工号、职责、技能、项目经历等

作者:RAG 项目组
创建时间:2024
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntity(BaseModel):
    """
    【临时提取实体模型 - LLM 实体抽取的输出】

    这是从文本切片中临时提取的实体信息,包含最基本的属性。
    后续会被合并、去重,最终存储为 Entity(知识库实体)。

    知识点 - 知识图谱中的实体:
    - 实体是知识图谱的基本单元
    - 代表文本中的"事物":概念、流程、设备、标准、人物等
    - 通过关系(Relation)与其他实体连接,形成知识网络

    知识点 - Pydantic BaseModel:
    - 提供自动类型检查和验证
    - 支持 JSON 序列化/反序列化
    - Field(default_factory=list) 用于可变默认值

    数据结构示例:
        {
            "name": "数据清洗",
            "type": "Procedure",
            "aliases": ["数据预处理", "数据净化"]
        }

        {
            "name": "Transformer",
            "type": "Concept",
            "aliases": ["注意力机制", "自注意力模型"]
        }

        {
            "name": "ISO 9001",
            "type": "Standard",
            "aliases": []
        }

    实际使用场景:
        # 从切片中提取实体(由 LLM 完成)
        entities = extract_entities_from_text("数据清洗包括去重、补全、标准化...")

        # 输出示例
        for entity in entities:
            print(f"实体: {entity.name}, 类型: {entity.type}")
            print(f"别名: {entity.aliases}")

        # 后续处理:合并到知识库
        for entity in entities:
            existing = find_in_kb(entity.name)
            if existing:
                # 更新别名
                existing.aliases.extend(entity.aliases)
            else:
                # 创建新的知识库实体
                kb_entity = Entity(
                    name=entity.name,
                    type=entity.type,
                    aliases=entity.aliases,
                    kb_id=generate_kb_id(),
                    source_chunks=[chunk.id]
                )

    字段说明:
        name: 实体名称(最核心的标识,如"数据清洗"、"Transformer")
        type: 实体类型(概念/流程/设备/标准/人物等,默认 "Unknown")
        aliases: 实体别名列表(同一个实体的不同叫法,如"数据清洗"和"数据预处理")
    """

    # 实体名称:这是实体的主要标识,通常是一个名词或名词短语
    # 示例:"数据清洗"、"Transformer"、"ISO 9001"、"张三"
    name: str

    # 实体类型:对实体进行分类,方便后续处理和查询
    # 可能的值(来自 Entity 模型的 EntityType):
    # - "Concept": 概念(如"机器学习"、"深度学习")
    # - "Procedure": 流程/步骤(如"数据清洗"、"模型训练")
    # - "Equipment": 设备/工具(如"GPU"、"服务器")
    # - "Standard": 标准/规范(如"ISO 9001"、"IEEE 802.11")
    # - "Quantity": 数量/指标(如"准确率"、"F1 分数")
    # - "Person": 人物(如"Yann LeCun"、"Geoffrey Hinton")
    # - "Time": 时间(如"2024 年"、"上个月")
    # - "Unknown": 未知类型(默认值)
    type: str = "Unknown"

    # 实体别名列表:同一个实体的不同称呼或写法
    # 作用:
    # 1. 提高检索召回率(搜索"数据预处理"也能找到"数据清洗")
    # 2. 实体对齐和去重(识别出同一实体的不同表达)
    # 3. 知识融合(合并不同来源的同一实体信息)
    #
    # 示例:
    # - "数据清洗" 的别名: ["数据预处理", "数据净化", "Data Cleaning"]
    # - "Transformer" 的别名: ["注意力机制", "自注意力模型"]
    # - "GPT" 的别名: ["Generative Pre-trained Transformer", "ChatGPT"]
    aliases: list[str] = Field(default_factory=list)
