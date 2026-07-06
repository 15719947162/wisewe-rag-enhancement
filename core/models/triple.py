"""
【核心数据模型模块 - triple.py】

本文件定义了知识图谱中的三元组（Triple）数据结构，用于表示实体间的关系。

三元组是知识图谱的基本组成单元，由三部分组成：
1. 主语（Subject, s）- 关系的起点，通常是一个实体
2. 谓语（Predicate, p）- 关系的类型，描述两个实体之间的关系
3. 宾语（Object, o）- 关系的终点，通常是另一个实体或值

知识图谱概念：
- 知识图谱是一种用图结构表示知识的方式
- 节点代表实体（概念、对象、事件等）
- 边代表实体间的关系
- 三元组（主语, 谓语, 宾语）就是图中的边

三元组在 RAG 系统中的作用：
1. 结构化知识提取：从非结构化文本中提取实体关系
2. 知识增强检索：通过关系扩展相关内容
3. 推理能力：基于关系进行知识推理
4. 可视化：构建知识图谱可视化

数据结构示例：
    {
        "s": "数据清洗",
        "p": "属于",
        "o": "预处理步骤",
        "confidence": 0.95,
        "source_chunk": "550e8400-e29b-41d4-a716-446655440000"
    }

实际使用场景：
    # 从文本中提取三元组
    text = "Python 是一种编程语言，由 Guido van Rossum 创建"
    triples = [
        Triple(s="Python", p="是一种", o="编程语言"),
        Triple(s="Python", p="创建者", o="Guido van Rossum")
    ]

    # 用于知识图谱构建
    for triple in triples:
        graph.add_edge(triple.s, triple.o, relation=triple.p)

    # 用于检索增强
    related = graph.neighbors("Python")  # 找到所有相关实体

作者：RAG 项目组
创建时间：2024
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Triple(BaseModel):
    """
    【三元组模型 - 知识图谱的基本单元】

    三元组表示一个简单的事实陈述：主语和宾语之间存在某种关系。
    这是知识图谱中节点之间边的表示形式。

    知识点 - 知识图谱三元组：
    - 来源于语义网络（Semantic Network）理论
    - 标准 RDF（Resource Description Framework）格式
    - 可以表示任何类型的关系：层次、属性、因果、时序等
    - 支持推理：通过已有关系推导新知识

    三元组的含义：
        (主语, 谓语, 宾语) 表示 "主语 和 宾语 之间存在谓语关系"

    常见的关系类型：
    - 层次关系：A 是 B 的子类（is_a, subclass_of）
    - 属性关系：A 的属性是 B（has_attribute, has_property）
    - 部分整体：A 是 B 的组成部分（part_of）
    - 因果关系：A 导致 B（causes, leads_to）
    - 时序关系：A 在 B 之前（before, after）
    - 空间关系：A 在 B 的位置（located_at）
    - 动作关系：A 对 B 执行动作（acts_on, uses）

    数据结构示例：
        示例 1 - 层次关系：
        {
            "s": "决策树",
            "p": "是一种",
            "o": "机器学习算法"
        }

        示例 2 - 因果关系：
        {
            "s": "数据缺失",
            "p": "导致",
            "o": "模型性能下降"
        }

        示例 3 - 属性关系：
        {
            "s": "Python",
            "p": "设计者",
            "o": "Guido van Rossum"
        }

        示例 4 - 流程关系：
        {
            "s": "数据清洗",
            "p": "执行于",
            "o": "特征工程之前"
        }

    实际使用场景：
        # 从切片中提取三元组
        triples = extract_triples_from_text(chunk.content)

        # 添加到切片
        chunk.extracted_triples.extend(triples)

        # 构建知识图谱
        graph = build_knowledge_graph(all_triples)

        # 基于图谱进行检索增强
        def enhanced_search(query, graph):
            # 1. 识别查询中的实体
            entities = recognize_entities(query)
            # 2. 通过图谱扩展相关实体
            related = []
            for entity in entities:
                neighbors = graph.neighbors(entity)
                related.extend(neighbors)
            # 3. 检索包含这些实体的切片
            return search_by_entities(related)

    字段说明：
        s: 主语（Subject），关系的起点
        p: 谓语（Predicate），关系类型
        o: 宾语（Object），关系的终点
        confidence: 置信度，表示关系提取的可信程度（0-1）
        source_chunk: 来源切片 ID，记录三元组从哪个切片提取
    """

    # 主语（Subject）：关系的起点，通常是一个实体
    # 示例："数据清洗"、"Python"、"决策树算法"
    s: str

    # 谓语（Predicate）：关系的类型，描述主语和宾语之间的关系
    # 示例："是一种"、"属于"、"导致"、"创建者"
    p: str

    # 宾语（Object）：关系的终点，通常是一个实体或值
    # 示例："预处理步骤"、"编程语言"、"模型性能下降"
    o: str

    # 置信度：表示关系提取的可信程度
    # 取值范围：[0.0, 1.0]
    # - 1.0 表示完全确定
    # - 0.5 表示不确定
    # - 0.0 表示几乎不可信
    # 默认值：0.7（表示较高的可信度）
    # 用途：
    # 1. 过滤低置信度的三元组
    # 2. 在推理时给予不同权重
    # 3. 评估关系提取模型质量
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)

    # 来源切片 ID：记录这个三元组是从哪个切片中提取的
    # 用途：
    # 1. 可追溯性：可以回溯到原文
    # 2. 上下文关联：结合切片上下文理解三元组
    # 3. 去重：避免从相似切片重复提取
    # 示例："550e8400-e29b-41d4-a716-446655440000"
    source_chunk: str = ""
