"""
切片策略模块 - 基类定义与注册机制

本模块定义了 RAG 系统中切片策略的抽象基类和注册机制。
切片（Chunking）是将长文档分割成语义单元的关键步骤，直接影响检索质量。

设计模式说明：
====================

1. 策略模式（Strategy Pattern）
   - ChunkingStrategy 是抽象策略接口
   - 具体策略（fixed_length, semantic 等）实现具体算法
   - 客户端通过统一接口调用，无需关心具体实现

2. 注册机制（Registry Pattern）
   - 使用装饰器 @register_strategy 自动注册策略
   - 通过 get_strategy(name) 获取策略实例
   - 实现了解耦：策略定义与使用分离

典型工作流程：
====================

    ContentBlock 列表 → ChunkingStrategy.chunk() → Chunk 列表

其中：
- ContentBlock: PDF 解析后的原始内容块（文本、表格、图片等）
- Chunk: 切片后的语义单元，包含内容、元数据、关联信息

使用示例：
====================

    # 1. 直接实例化具体策略
    from core.chunker.fixed_length import FixedLengthChunker
    strategy = FixedLengthChunker(chunk_size=500, overlap=50)
    chunks = strategy.chunk(blocks)

    # 2. 通过注册机制获取策略（推荐）
    from core.chunker.base import get_strategy
    strategy = get_strategy('fixed_length', chunk_size=500, overlap=50)
    chunks = strategy.chunk(blocks)

    # 3. 查看所有可用策略
    from core.chunker.base import list_strategies
    print(list_strategies())  # ['fixed_length', 'paragraph', 'semantic', ...]

模块架构：
====================

core/chunker/
├── base.py              # 本文件：抽象基类 + 注册机制
├── fixed_length.py      # 固定长度切片
├── paragraph.py         # 段落切片
├── semantic.py          # 语义切片（按标题层级）
├── separator.py         # 分隔符切片
├── llm_chunker.py       # LLM 智能切片
├── hierarchical.py      # 层次化切片
└── linker.py            # 切片关联后处理
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.content_block import Chunk, ContentBlock


class ChunkingStrategy(ABC):
    """
    切片策略抽象基类

    所有切片策略必须继承此类并实现 chunk() 方法。
    该设计遵循「依赖倒置原则」：高层模块依赖抽象，而非具体实现。

    核心概念：
    --------

    1. 输入：ContentBlock 列表
       - 来自 PDF 解析器（MinerU）
       - 每个块可能是文本、表格、图片等类型
       - 包含文本内容、位置信息、元数据

    2. 输出：Chunk 列表
       - 切片后的语义单元
       - 包含内容、来源、页码、策略名等
       - 可选：父子关系、关联块、增强文本

    3. 切片原则：
       - 保持语义完整性（避免断句、断表）
       - 控制切片大小（平衡检索精度和召回）
       - 保留上下文信息（标题、位置、关联）

    设计要点：
    --------

    - name 属性：策略标识符，用于注册和日志
    - chunk() 方法：核心算法，由子类实现
    - _make_chunk()：工厂方法，标准化 Chunk 创建
    - _make_image_chunk()：图片块特殊处理

    扩展指南：
    --------

    创建新策略需要：
    1. 继承 ChunkingStrategy
    2. 设置唯一的 name 属性
    3. 实现 chunk() 方法
    4. 使用 @register_strategy 装饰器注册

    示例：
        @register_strategy
        class MyChunker(ChunkingStrategy):
            name = "my_strategy"

            def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
                # 实现切片逻辑
                ...
    """

    name: str = "base"

    @abstractmethod
    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """
        将内容块列表切分为切片列表

        这是切片策略的核心方法，所有具体策略必须实现此方法。
        不同的策略会根据不同的规则进行切分。

        参数：
        --------
        blocks : list[ContentBlock]
            待切分的内容块列表
            - 通常来自 PDF 解析器（MinerU）
            - 包含文本、表格、图片等多种类型
            - 按 PDF 页面顺序排列

        返回：
        --------
        list[Chunk]
            切分后的切片列表，每个切片包含：
            - content: 切片文本内容
            - source: 来源文件名
            - page: 页码（0-indexed）
            - chunk_index: 切片序号（在当前策略下的全局序号）
            - strategy: 策略名称
            - title: 可选，所属章节标题
            - is_table_chunk / is_image_chunk: 类型标记
            - image_path: 图片块的文件路径

        切片原则：
        --------
        1. 语义完整性
           - 不要在句子中间切分（除非固定长度策略）
           - 保持表格、图片的完整性
           - 章节标题应与内容在一起

        2. 大小控制
           - 避免过大切片（影响检索精度）
           - 避免过小切片（信息不足）
           - 通常建议 200-1000 字符

        3. 元数据保留
           - 记录来源页码，便于溯源
           - 保留章节层级信息
           - 标记特殊类型（表格、图片）

        实现示例：
        --------
        固定长度切片：
            def chunk(self, blocks):
                chunks = []
                text = "\\n\\n".join(b.text for b in blocks)
                for i in range(0, len(text), self.chunk_size):
                    chunk_text = text[i:i+self.chunk_size]
                    chunks.append(self._make_chunk(...))
                return chunks

        段落切片：
            def chunk(self, blocks):
                chunks = []
                for block in blocks:
                    paragraphs = block.text.split("\\n\\n")
                    for para in paragraphs:
                        chunks.append(self._make_chunk(...))
                return chunks
        """
        ...

    def _make_chunk(
        self,
        content: str,
        source: str,
        page: int,
        chunk_index: int,
        title: str | None = None,
        is_table_chunk: bool = False,
        is_image_chunk: bool = False,
        image_path: str | None = None,
    ) -> Chunk:
        """
        工厂方法：创建标准 Chunk 对象

        该方法封装了 Chunk 创建逻辑，确保所有策略生成的 Chunk
        具有统一的结构和默认值。

        参数：
        --------
        content : str
            切片文本内容
            - 通常是多个 ContentBlock 的文本组合
            - 应保持语义完整性

        source : str
            来源文件名
            - 通常继承自 ContentBlock.source_file
            - 用于溯源和日志

        page : int
            页码（0-indexed）
            - 切片主要所在的页码
            - 跨页切片可取起始页或主页面

        chunk_index : int
            切片序号
            - 在当前策略下的全局递增序号
            - 用于排序和引用

        title : str | None, optional
            所属章节标题
            - 用于上下文理解
            - 多级标题用分隔符连接

        is_table_chunk : bool, optional
            是否为表格切片
            - 表格需要特殊处理和展示

        is_image_chunk : bool, optional
            是否为图片切片
            - 图片包含视觉信息，检索方式不同

        image_path : str | None, optional
            图片文件路径
            - 仅当 is_image_chunk=True 时有效
            - 用于图片检索和展示

        返回：
        --------
        Chunk
            标准化的切片对象，包含所有元数据

        设计说明：
        --------
        - 自动填充 strategy 字段（使用 self.name）
        - 未来可扩展：parent_id, related_ids, enhanced_text 等
        - 统一创建逻辑，便于维护和测试
        """
        return Chunk(
            content=content,
            source=source,
            page=page,
            chunk_index=chunk_index,
            strategy=self.name,
            title=title,
            is_table_chunk=is_table_chunk,
            is_image_chunk=is_image_chunk,
            image_path=image_path,
        )

    def _make_image_chunk(self, block: ContentBlock, chunk_index: int) -> Chunk:
        """
        从图片块创建切片

        图片块需要特殊处理，因为它们包含视觉信息而非纯文本。
        该方法保留图片的文件路径，用于后续的图片检索和展示。

        参数：
        --------
        block : ContentBlock
            图片类型的内容块
            - block.type == BlockType.IMAGE
            - block.image_path 包含图片文件路径
            - block.text 可能包含图片说明文字

        chunk_index : int
            切片序号
            - 全局递增序号

        返回：
        --------
        Chunk
            图片切片对象，特点：
            - is_image_chunk=True
            - image_path 保留原图片路径
            - content 为图片说明或占位文本

        处理逻辑：
        --------
        1. 优先使用 block.text 作为内容（图片说明）
        2. 如果无文本，生成占位符 "[图片 第X页]"
        3. 保留 image_path 用于图片检索
        4. 标记为图片类型切片

        使用场景：
        --------
        在切片策略中处理图片块：
            for block in blocks:
                if block.type == BlockType.IMAGE:
                    chunks.append(self._make_image_chunk(block, idx))
                    idx += 1
        """
        content = block.text.strip() or f"[图片 第{block.page_idx + 1}页]"
        return self._make_chunk(
            content=content,
            source=block.source_file,
            page=block.page_idx,
            chunk_index=chunk_index,
            is_image_chunk=True,
            image_path=block.image_path,
        )


# ============================================================================
# 切片策略注册机制
# ============================================================================
#
# 注册机制实现了策略模式 + 注册表模式的组合：
#
# 1. 注册表（Registry）
#    - _REGISTRY 是全局字典，存储 name -> Class 的映射
#    - 装饰器 @register_strategy 自动将类注册到字典
#
# 2. 工厂方法（Factory Method）
#    - get_strategy(name, **kwargs) 根据名称创建实例
#    - 支持运行时参数传递
#
# 3. 优势：
#    - 解耦：策略定义与使用分离
#    - 扩展性：新增策略只需添加文件和装饰器
#    - 配置化：可通过配置文件选择策略
#
# 使用示例：
# ----------
#
# 定义新策略：
#     @register_strategy
#     class MyChunker(ChunkingStrategy):
#         name = "my_strategy"
#         def chunk(self, blocks): ...
#
# 使用策略：
#     strategy = get_strategy("my_strategy", param1=value1)
#     chunks = strategy.chunk(blocks)
#
# 列出所有策略：
#     all_strategies = list_strategies()
#     # ['fixed_length', 'paragraph', 'semantic', ...]
#

_REGISTRY: dict[str, type[ChunkingStrategy]] = {}
"""策略注册表：存储策略名称到策略类的映射"""


def register_strategy(cls: type[ChunkingStrategy]) -> type[ChunkingStrategy]:
    """
    策略注册装饰器

    将策略类注册到全局注册表，使其可通过名称访问。

    参数：
    --------
    cls : type[ChunkingStrategy]
        待注册的策略类（必须已定义 name 属性）

    返回：
    --------
    type[ChunkingStrategy]
        原策略类（装饰器模式，不修改类本身）

    使用示例：
    --------
    @register_strategy
    class MyChunker(ChunkingStrategy):
        name = "my_chunker"
        ...

    # 注册后可通过 get_strategy("my_chunker") 获取

    设计说明：
    --------
    - 装饰器在类定义时执行，立即注册
    - 如果 name 重复，会覆盖之前的注册（允许覆盖）
    - 注册表是全局的，跨模块共享
    """
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str, **kwargs) -> ChunkingStrategy:
    """
    根据名称获取策略实例

    从注册表中查找策略类并实例化，支持传递初始化参数。

    参数：
    --------
    name : str
        策略名称，对应策略类的 name 属性
        例如：'fixed_length', 'semantic', 'hierarchical'

    **kwargs : dict
        策略初始化参数
        不同策略支持的参数不同：
        - fixed_length: chunk_size, overlap
        - semantic: 无
        - hierarchical: parent_chunk_size, child_chunk_size, enable_enhancement
        - llm_chunker: model, api_key, base_url

    返回：
    --------
    ChunkingStrategy
        策略实例，可直接调用 chunk() 方法

    异常：
    --------
    ValueError
        当策略名称不存在时抛出，并提示可用策略列表

    使用示例：
    --------
    # 获取固定长度切片器
    strategy = get_strategy('fixed_length', chunk_size=500, overlap=50)
    chunks = strategy.chunk(blocks)

    # 获取语义切片器
    strategy = get_strategy('semantic')
    chunks = strategy.chunk(blocks)

    # 错误处理
    try:
        strategy = get_strategy('unknown')
    except ValueError as e:
        print(e)  # Unknown strategy 'unknown'. Available: fixed_length, ...
    """
    if name not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return _REGISTRY[name](**kwargs)


def list_strategies() -> list[str]:
    """
    列出所有已注册的策略名称

    返回：
    --------
    list[str]
        已注册策略名称列表

    使用场景：
    --------
    1. 配置验证：检查配置的策略是否存在
    2. 用户界面：展示可选策略列表
    3. 文档生成：自动枚举所有策略

    使用示例：
    --------
    strategies = list_strategies()
    print("可用策略:", strategies)
    # 可用策略: ['fixed_length', 'paragraph', 'semantic', 'separator', 'llm', 'hierarchical']
    """
    return list(_REGISTRY.keys())
