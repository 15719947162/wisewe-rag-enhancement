"""
实体提取管道模块
=================

【知识图谱构建流程概览】
整个知识图谱的构建，就像是把一本书的内容变成一个知识网络：
1. 从文档中识别出关键信息（实体）→ 比如"机器学习"、"神经网络"
2. 整理这些信息（合并重复）→ 把"AI"和"人工智能"合并
3. 为每个信息添加定义 → "机器学习是人工智能的一个分支..."
4. 给每个信息生成向量 → 用于相似度搜索
5. 记录这些信息出现在哪些文档位置 → 方便追溯来源
6. 保存到数据库 → 持久化存储

【这个模块的作用】
这个模块是整个实体处理的"总指挥"——它协调各个环节，把原始文档变成知识图谱中的实体节点。

可以把它想象成一个工厂流水线：
- 输入：处理好的文档片段（Chunks）
- 输出：持久化的实体（Entities）

【处理流程详解】
第1步：收集原始实体
    从文档片段中，把之前LLM提取出来的实体都收集起来。
    这些实体还只是"原材料"，可能有重复，还没有定义。

第2步：合并去重
    调用 EntityMerger，把相同的实体合并。
    比如"AI"和"人工智能"合并成一个实体。

第3步：生成定义
    调用 definition_generator，为每个实体找到它的定义句子。
    这样用户看到实体时，知道它是什么意思。

第4步：生成向量
    调用 embed_texts，把实体的名称和定义转成向量。
    这样用户可以用语义搜索来查找实体。

第5步：建立关联
    在文档片段和实体之间建立"mentions"关系。
    这样查询文档时，可以看到它提到了哪些实体。

第6步：持久化
    把实体写入数据库，方便后续查询和使用。

【为什么叫"materialize"？】
materialize 原意是"使具体化、使成形"。
在这里，它把抽象的"提取出的实体"变成具体的"持久化实体"。
就像把设计图纸变成实际的产品。

【与其他模块的关系】
- entity_merger.py：负责合并相同实体
- definition_generator.py：负责生成实体定义
- relation_utils.py：负责建立实体和文档的关联关系
- entity_writer.py：负责把实体写入数据库
- embedding/client.py：负责生成向量
"""

from __future__ import annotations

from core.chunker.relation_utils import add_relation
from core.embedding.client import embed_texts
from core.kg.definition_generator import generate_definition
from core.kg.entity_merger import EntityMerger
from core.models.content_block import Chunk
from core.models.entity import Entity
from core.output.entity_writer import write_entities


def materialize_entities(conn, chunks: list[Chunk], kb_id: str) -> list[Entity]:
    """
    将提取的实体实体化（持久化到数据库）

    这是实体处理的主入口函数，完成从"原始提取结果"到"持久化实体"的全流程。

    【参数说明】
    conn: 数据库连接对象
        - 用于将实体写入数据库

    chunks: 文档片段列表
        - 包含已经提取出来的实体信息
        - 只有 layer="enhanced" 的片段才包含提取的实体
        - 每个片段的 extracted_entities 字段存储着LLM提取的原始实体

    kb_id: 知识库ID
        - 标识这些实体属于哪个知识库
        - 用于后续按知识库查询实体

    【返回值】
    返回处理后的实体列表，每个实体都已：
    - 合并去重
    - 生成定义
    - 生成向量
    - 建立文档关联
    - 写入数据库

    【处理流程 - 用大白话说】

    === 第一步：收集原材料 ===
    就像做饭前先准备食材，这里收集所有提取出来的实体。

    从所有 enhanced 层级的片段中，把 LLM 提取的实体都拿出来。
    每个实体记录它的来源片段（parent_id 或自己的 id）。

    为什么要从 enhanced 层级收集？
    - enhanced 层级是 LLM 增强后的片段，包含了高质量的实体提取结果
    - 这些片段经过了 LLM 的理解和总结，实体识别更准确

    === 第二步：合并整理 ===
    调用 EntityMerger 合并相同的实体。

    假设文档片段1提取出"AI"，片段2提取出"人工智能"，
    合并后它们会变成一个实体，有两个来源片段。

    === 第三步：生成定义 ===
    为每个实体从原文中找到定义句子。

    就像字典里的词条一样，每个实体都需要一个解释。
    定义生成器会去原文中找第一个包含实体名称的句子作为定义。

    === 第四步：生成向量 ===
    把实体名称和定义拼接，生成向量。

    向量是什么？可以理解为把文字转换成一串数字，这串数字代表了文字的语义。
    有了向量，就可以做语义搜索：
    - 用户搜索"机器学习"，系统也能找到"深度学习"相关的实体
    - 因为它们在语义上相似

    === 第五步：建立关联 ===
    在文档片段和实体之间建立"mentions"关系。

    这样做的好处：
    - 查看文档时，可以看到它提到了哪些关键实体
    - 查看实体时，可以看到它在哪些文档中出现

    === 第六步：持久化 ===
    把所有实体写入数据库。

    这样即使程序重启，实体数据也不会丢失。
    后续可以直接从数据库查询和使用。

    【代码示例】
    # 假设已有数据库连接和处理好的片段
    entities = materialize_entities(conn, chunks, kb_id="kb_001")
    print(f"成功处理 {len(entities)} 个实体")
    for entity in entities:
        print(f"- {entity.name}: {entity.definition}")
    """
    # ========================================
    # 第一步：收集原始实体
    # ========================================
    raw: list[tuple] = []  # 存储 (原始实体, 来源片段ID) 的列表

    # 建立片段ID到片段的映射，方便后续查找
    chunk_map = {chunk.id: chunk for chunk in chunks}

    # 遍历所有片段，收集提取出的实体
    for chunk in chunks:
        # 只处理 enhanced 层级的片段（这些片段包含 LLM 提取的实体）
        if chunk.layer != "enhanced":
            continue

        # 遍历这个片段中的所有实体
        for entity in chunk.extracted_entities:
            # 记录实体和它的来源片段
            # 如果片段有 parent_id，用 parent_id（这是分层切片的父级片段）
            # 否则用自己的 id
            raw.append((entity, chunk.parent_id or chunk.id))

    # ========================================
    # 第二步：合并实体
    # ========================================
    # 调用 EntityMerger 进行合并去重
    entities = EntityMerger().merge(kb_id, raw)

    # 如果没有实体，直接返回空列表
    if not entities:
        return []

    # ========================================
    # 第三步：生成定义
    # ========================================
    # 为每个实体生成定义
    for entity in entities:
        entity.definition = generate_definition(entity, chunk_map)

    # ========================================
    # 第四步：生成向量
    # ========================================
    # 准备向量化的文本：实体名称 + 定义
    texts_for_embedding = [
        f"{entity.name} {entity.definition or ''}"
        for entity in entities
    ]

    # 调用嵌入模型生成向量
    embeddings = embed_texts(texts_for_embedding)

    # 把向量赋值给每个实体
    for entity, embedding in zip(entities, embeddings):
        entity.embedding = embedding

    # ========================================
    # 第五步：建立文档-实体关联
    # ========================================
    # 先整理每个片段对应哪些实体
    by_chunk: dict[str, list[Entity]] = {}
    for entity in entities:
        # 遍历实体的所有来源片段
        for chunk_id in entity.source_chunks:
            # 按片段ID分组
            by_chunk.setdefault(chunk_id, []).append(entity)

    # 为每个片段添加 mentions 关系
    for chunk in chunks:
        # 获取这个片段关联的实体
        for entity in by_chunk.get(chunk.id, []):
            # 添加关系：这个片段提到了这个实体
            add_relation(
                chunk,
                entity.id,
                rel_type="mentions",  # 关系类型：提及
                source="entity",       # 来源：实体
                evidence=entity.name[:20]  # 证据：实体名称的前20个字符
            )

    # ========================================
    # 第六步：持久化到数据库
    # ========================================
    write_entities(conn, entities)

    return entities
