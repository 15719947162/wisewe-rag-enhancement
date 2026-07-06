"""
【核心数据模型模块 - content_block.py】

本文件定义了整个 RAG 系统最核心的数据结构，所有模块都依赖这些模型。

主要包含两类核心对象：
1. ContentBlock - 解析器输出的原始内容块（从 PDF 解析出来的最小单元）
2. Chunk - 切片器输出的切片单元（用于向量化和检索）

数据流向：
PDF文件 → 解析器 → ContentBlock 列表 → 清洗器 → 切片器 → Chunk 列表 → 向量化 → 存储

作者：RAG 项目组
创建时间：2024
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator
from pydantic import Field

# 导入实体和关系相关的模型，用于知识图谱功能
from core.models.extracted_entity import ExtractedEntity
from core.models.relation import Relation
from core.models.triple import Triple


class BlockType(str, Enum):
    """
    【内容块类型枚举】

    定义 PDF 解析后可能产生的内容类型。
    这是一个枚举类，继承自 str 和 Enum，可以当作字符串使用。

    知识点 - Python 枚举：
    - Enum 用于定义一组命名的常量
    - 继承 str 可以直接与字符串比较，如 block.type == "text"
    - 枚举值在代码中有提示，避免拼写错误

    可能的值：
    - TEXT: 普通文本段落（最常见）
    - TABLE: 表格内容（可能包含 table_html）
    - IMAGE: 图片（会保存到本地，image_path 记录路径）
    - TITLE: 标题（通常有 text_level 标识层级，如 H1/H2）

    示例：
        >>> BlockType.TEXT
        <BlockType.TEXT: 'text'>
        >>> BlockType.TEXT == "text"
        True
    """
    TEXT = "text"    # 普通文本内容
    TABLE = "table"  # 表格，会有 table_html 字段
    IMAGE = "image"  # 图片，会有 image_path 字段
    TITLE = "title"  # 标题，会有 text_level 字段


class ContentBlock(BaseModel):
    """
    【内容块模型 - 解析器的输出单元】

    这是 PDF 解析后产生的最小内容单元。
    一个 PDF 页面可能被解析成多个 ContentBlock。

    知识点 - Pydantic BaseModel：
    - Pydantic 是 Python 数据验证库
    - BaseModel 提供自动类型检查、JSON 序列化/反序列化
    - 字段类型注解会被自动验证
    - 支持嵌套模型、Optional、默认值等

    数据结构示例：
        {
            "type": "text",
            "text": "这是一段示例文本...",
            "page_idx": 0,
            "text_level": null,
            "is_table": false,
            "table_html": null,
            "source_file": "sample.pdf",
            "image_path": null,
            "bbox": [100.5, 200.3, 400.2, 350.8]
        }

    实际使用场景：
        # 从 PDF 解析出内容块
        blocks = parse_pdf("document.pdf")
        for block in blocks:
            if block.type == BlockType.TABLE:
                print(f"发现表格: {block.table_html}")
            elif block.type == BlockType.IMAGE:
                print(f"发现图片: {block.image_path}")

    字段说明：
        type: 内容类型（文本/表格/图片/标题）
        text: 文本内容，所有类型都有这个字段
        page_idx: 页码索引（从 0 开始）
        text_level: 标题层级（仅标题类型有效，如 1=H1, 2=H2）
        is_table: 是否为表格（冗余字段，方便判断）
        table_html: 表格的 HTML 表示（仅表格类型有效）
        source_file: 来源文件名
        image_path: 图片本地路径（仅图片类型有效）
        bbox: 边界框坐标 [左上x, 左上y, 右下x, 右下y]
    """

    # 内容类型：text/table/image/title
    type: BlockType

    # 文本内容：所有类型都有这个字段，图片类型的 text 可能是 OCR 结果或描述
    text: str

    # 页码索引：从 0 开始，表示这是 PDF 的第几页
    page_idx: int

    # 标题层级：仅当 type=TITLE 时有效
    # 1 = 一级标题（H1），2 = 二级标题（H2），以此类推
    # None 表示不是标题，或者是普通文本
    text_level: Optional[int] = None

    # 是否为表格：这是一个冗余字段，方便快速判断
    # 等价于 type == BlockType.TABLE
    is_table: bool = False

    # 表格的 HTML 表示：仅当 type=TABLE 时有效
    # 包含完整的 <table> 标签，可以直接渲染
    # 示例：'<table><tr><td>单元格1</td><td>单元格2</td></tr></table>'
    table_html: Optional[str] = None

    # 来源文件名：记录这个内容块来自哪个 PDF 文件
    source_file: str = ""

    # 图片本地路径：仅当 type=IMAGE 时有效
    # 记录图片文件在本地磁盘的相对路径
    # 示例："images/page_5_img_2.png"
    # 用于后续的视觉模型处理（VL models）
    image_path: Optional[str] = None

    # 边界框坐标：记录内容在 PDF 页面上的位置
    # 格式：[x0, y0, x1, y1] 左上角和右下角的坐标
    # 用于：
    # 1. 定位内容在页面上的位置
    # 2. 多栏布局重排序
    # 3. 内容块的视觉关系判断
    bbox: Optional[list[float]] = None


class Chunk(BaseModel):
    """
    【切片模型 - 切片器的输出单元】

    这是内容经过切片策略处理后的基本单元，也是向量化和检索的核心对象。
    一个 Chunk 可能包含一个或多个 ContentBlock 的内容。

    知识点 - RAG 切片的重要性：
    - PDF 通常很长，不能直接向量化（向量维度限制、检索精度问题）
    - 切片是将长文本拆分成适合检索的小段落
    - 不同的切片策略（固定长度、语义、层次化）会影响检索质量

    数据结构示例：
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "content": "这是切片后的文本内容...",
            "source": "sample.pdf",
            "page": 5,
            "chunk_index": 12,
            "strategy": "semantic",
            "title": "第三章 数据处理流程",
            "char_count": 256,
            "is_table_chunk": false,
            "is_image_chunk": false,
            "is_procedure_chunk": false,
            "image_path": null,
            "layer": "child",
            "parent_id": "parent-chunk-uuid",
            "procedure_order": null,
            "enhanced_text": "这段内容讲述了...",
            "extracted_entities": [...],
            "extracted_triples": [...],
            "relations": [...],
            "token_cost": 0
        }

    层次化切片说明：
    - parent: 父级切片（通常是整个章节）
    - child: 子级切片（章节内的小段落）
    - enhanced: 增强切片（LLM 生成的摘要，用于提高检索精度）

    实际使用场景：
        # 使用切片策略生成切片
        strategy = get_strategy("semantic")
        chunks = strategy.chunk(blocks)

        # 向量化切片
        texts = [chunk.content for chunk in chunks]
        embeddings = embed_texts(texts)

        # 检索相关切片
        relevant_chunks = retriever.search(query, top_k=10)

    字段说明：
        id: 唯一标识符，自动生成 UUID
        content: 切片文本内容
        source: 来源文件名
        page: 页码（可能是跨页切片，取主要页码）
        chunk_index: 切片序号（在所有切片中的位置）
        strategy: 切片策略名称（fixed_length/semantic/hierarchical 等）
        title: 切片所属的标题（如果有）
        char_count: 字符数（自动计算）
        is_table_chunk: 是否为表格切片
        is_image_chunk: 是否为图片切片
        is_procedure_chunk: 是否为流程步骤切片
        image_path: 关联的图片路径（如果有的话）
        layer: 切片层级（parent/child/enhanced）
        parent_id: 父切片 ID（层次化切片使用）
        procedure_order: 流程步骤序号（流程切片使用）
        enhanced_text: LLM 生成的摘要/增强文本
        extracted_entities: 提取的实体列表（知识图谱）
        extracted_triples: 提取的三元组列表（知识图谱）
        relations: 切片间关系列表
        token_cost: LLM token 消耗（仅 enhanced 切片记录）
    """

    # 唯一标识符：UUID 格式，如果没有提供则自动生成
    # 示例："550e8400-e29b-41d4-a716-446655440000"
    id: str = ""

    # 切片文本内容：这是实际向量化用于检索的内容
    # 长度取决于切片策略，通常 200-1000 字符
    content: str

    # 来源文件名：记录切片来自哪个 PDF
    source: str

    # 页码：记录切片主要来自哪一页
    # 注意：一个切片可能跨越多页，这里记录主要页码
    page: int

    # 切片序号：在所有切片中的位置，从 0 开始
    chunk_index: int

    # 切片策略名称：记录使用了哪种切片策略
    # 可能的值：fixed_length, paragraph, semantic, separator, llm, hierarchical
    strategy: str = ""

    # 切片所属的标题：如果切片属于某个章节，记录章节标题
    # 示例："3.2 数据预处理方法"
    title: Optional[str] = None

    # 字符数：自动计算，无需手动设置
    char_count: int = 0

    # 是否为表格切片：标记这个切片是否包含表格内容
    # 表格切片通常需要特殊处理（如保留 HTML 格式）
    is_table_chunk: bool = False

    # 是否为图片切片：标记这个切片是否关联图片
    is_image_chunk: bool = False

    # 是否为流程步骤切片：标记这是否是流程/步骤类内容
    # 用于流程链接器（procedure_linker）
    is_procedure_chunk: bool = False

    # 关联的图片路径：如果切片关联了图片，记录图片位置
    image_path: Optional[str] = None

    # 切片层级：用于层次化切片策略
    # - "parent": 父级切片（大粒度，如整个章节）
    # - "child": 子级切片（小粒度，如段落）
    # - "enhanced": 增强切片（LLM 生成的摘要）
    layer: str = "child"

    # 父切片 ID：记录子切片或增强切片的父切片
    # 用于层次化检索：先检索父切片，再深入子切片
    parent_id: Optional[str] = None

    # 流程步骤序号：如果这是流程切片，记录步骤顺序
    # 示例：第 1 步、第 2 步...
    procedure_order: Optional[int] = None

    # LLM 生成的增强文本：用于提高检索精度
    # 可能是摘要、关键词提取、问题生成等
    # 示例："本节讲述了数据预处理的三个步骤：清洗、转换、标准化..."
    enhanced_text: Optional[str] = None

    # 提取的实体列表：知识图谱相关，记录切片中提到的实体
    # 实体包括：概念、流程、设备、标准、数量、人物、时间等
    extracted_entities: list[ExtractedEntity] = Field(default_factory=list)

    # 提取的三元组列表：知识图谱相关，记录实体间的关系
    # 三元组格式：(主语, 谓语, 宾语)
    # 示例：(数据清洗, 属于, 预处理步骤)
    extracted_triples: list[Triple] = Field(default_factory=list)

    # 切片间关系列表：记录这个切片与其他切片的关系
    # 关系类型：相邻、相似、引用、因果关系等
    # 用于检索时扩展相关切片
    relations: list[Relation] = Field(default_factory=list)

    # LLM token 消耗：记录生成增强文本消耗的 token 数
    # 用于成本统计和分析
    token_cost: int = 0

    @model_validator(mode="after")
    def _auto_fields(self) -> "Chunk":
        """
        【自动字段填充验证器】

        知识点 - Pydantic model_validator：
        - mode="after" 表示在所有字段验证完成后执行
        - 可以自动计算或填充某些字段
        - 通过 object.__setattr__ 修改字段值（因为 Pydantic 模型默认不可变）

        自动填充的字段：
        1. id: 如果没有提供，自动生成 UUID
        2. char_count: 如果没有提供，自动计算 content 的长度

        为什么需要这个方法？
        - 避免手动设置这些字段
        - 保证字段值的正确性
        - 简化模型使用

        示例：
            # 创建切片时不需要手动设置 id 和 char_count
            chunk = Chunk(
                content="这是内容",
                source="test.pdf",
                page=1,
                chunk_index=0
            )
            # id 和 char_count 会自动填充
            print(chunk.id)  # 自动生成的 UUID
            print(chunk.char_count)  # 6
        """
        # 如果 id 为空，自动生成 UUID
        if not self.id:
            # 使用 object.__setattr__ 因为 Pydantic 模型默认是不可变的
            object.__setattr__(self, "id", str(uuid.uuid4()))

        # 如果 char_count 为 0，自动计算
        if not self.char_count:
            object.__setattr__(self, "char_count", len(self.content))

        return self

    @property
    def related_ids(self) -> list[str]:
        """
        【相关切片 ID 列表属性】

        从 relations 中提取所有相关切片的 ID，去重并保持顺序。

        知识点 - Python property 装饰器：
        - @property 将方法变成属性，调用时不需要括号
        - 计算属性，每次访问时重新计算
        - 提供更清晰的接口

        为什么需要这个属性？
        - 检索时需要快速获取相关切片 ID
        - 避免每次都手动遍历 relations 列表
        - 自动去重和保持顺序

        实现逻辑：
        1. 创建一个 set 用于去重
        2. 创建一个 list 用于保持顺序
        3. 遍历所有关系，提取 target_id
        4. 如果 target_id 不在 set 中，添加到列表

        示例：
            >>> chunk.relations = [
            ...     Relation(target_id="id1", rel_type="adjacent", source="rule"),
            ...     Relation(target_id="id2", rel_type="semantic_similar", source="embedding"),
            ...     Relation(target_id="id1", rel_type="duplicate_of", source="llm"),  # 重复
            ... ]
            >>> chunk.related_ids
            ["id1", "id2"]  # 去重且保持顺序

        返回：
            list[str]: 相关切片 ID 的列表（去重后）
        """
        # 使用 set 去重，使用 list 保持顺序
        seen: set[str] = set()
        ordered: list[str] = []

        # 遍历所有关系
        for relation in self.relations:
            # 如果有关系目标且目标 ID 未出现过
            if relation.target_id and relation.target_id not in seen:
                seen.add(relation.target_id)  # 标记为已出现
                ordered.append(relation.target_id)  # 添加到有序列表

        return ordered
