"""
切片模块（Chunker Module）

本模块提供 PDF 内容的切片策略，将解析后的 ContentBlock 列表切分为适合向量化
和检索的 Chunk 列表。支持多种切片策略，可通过策略名称动态获取实例。

## 设计理念

采用「策略模式 + 装饰器注册」架构：

1. **抽象基类**：`ChunkingStrategy` 定义统一的 `chunk()` 接口
2. **策略注册表**：全局字典 `_REGISTRY` 存储名称到类的映射
3. **装饰器注册**：`@register_strategy` 自动将策略类注册到注册表
4. **工厂函数**：`get_strategy(name, **params)` 按名称创建实例

## 策略注册表工作原理

注册表在 `core/chunker/base.py` 中定义：

    _REGISTRY: dict[str, type[ChunkingStrategy]] = {}

当策略类使用 `@register_strategy` 装饰器时，装饰器自动执行：

    @register_strategy
    class FixedLengthStrategy(ChunkingStrategy):
        name = "fixed_length"  # 注册表键名
        ...

    # 等价于：
    # _REGISTRY["fixed_length"] = FixedLengthStrategy

本模块导入各策略类时，装饰器立即执行注册，因此导入顺序决定注册顺序。

## 可用策略

| 策略名称           | 类名                  | 说明                           |
|--------------------|-----------------------|--------------------------------|
| fixed_length       | FixedLengthStrategy   | 按字符数硬切，带重叠           |
| paragraph          | ParagraphStrategy     | 自然段落边界，合并短段         |
| semantic           | SemanticStrategy      | 按 MinerU 标题层级分组        |
| separator          | SeparatorStrategy     | 按可配置标点切分               |
| llm                | LLMChunkingStrategy   | LLM 判断语义边界               |
| hierarchical       | HierarchicalStrategy  | 三层结构：章节→知识点→摘要     |

## 使用示例

    from core.chunker import get_strategy, list_strategies, ChunkingStrategy
    from core.models.content_block import ContentBlock

    # 查看所有可用策略
    print(list_strategies())
    # 输出: ['fixed_length', 'paragraph', 'semantic', 'separator', 'llm', 'hierarchical']

    # 获取策略实例（带参数）
    strategy = get_strategy("fixed_length", chunk_size=500, overlap=50)

    # 执行切片
    blocks: list[ContentBlock] = [...]  # 从解析器获取
    chunks = strategy.chunk(blocks)

    # 处理切片
    for chunk in chunks:
        print(f"第 {chunk.chunk_index} 个切片: {chunk.content[:50]}...")

    # 关联文本与图表切片
    from core.chunker import link_related_chunks
    linked_chunks = link_related_chunks(chunks)

## 如何添加新策略

1. 在 `core/chunker/` 下创建新文件，如 `my_strategy.py`

2. 定义策略类，继承 `ChunkingStrategy`：

    from core.chunker.base import ChunkingStrategy, register_strategy
    from core.models.content_block import Chunk, ContentBlock

    @register_strategy  # 必须添加装饰器
    class MyStrategy(ChunkingStrategy):
        name = "my_strategy"  # 必须定义 name 属性

        def __init__(self, custom_param: int = 10):
            self.custom_param = custom_param

        def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
            # 实现切片逻辑
            chunks = []
            for i, block in enumerate(blocks):
                chunk = self._make_chunk(
                    content=block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=i,
                )
                chunks.append(chunk)
            return chunks

3. 在本文件（`__init__.py`）中导入新策略：

    from .my_strategy import MyStrategy  # 添加导入

4. 将新策略添加到 `__all__` 列表：

    __all__ = [
        ...,
        "MyStrategy",  # 添加导出
    ]

完成以上步骤后，新策略即可通过 `get_strategy("my_strategy")` 获取。

## 模块依赖关系

    core/chunker/
    ├── __init__.py      ← 本文件（统一入口）
    ├── base.py          ← 抽象基类 + 注册机制
    ├── fixed_length.py  ← 固定长度切片
    ├── paragraph.py     ← 段落切片
    ├── semantic.py      ← 语义切片
    ├── separator.py     ← 分隔符切片
    ├── llm_chunker.py   ← LLM 切片
    ├── hierarchical.py  ← 层级切片
    └── linker.py        ← 切片关联后处理
"""

from .base import ChunkingStrategy, get_strategy, list_strategies, register_strategy
from .fixed_length import FixedLengthStrategy
from .hierarchical import HierarchicalStrategy
from .linker import link_related_chunks
from .llm_chunker import LLMChunkingStrategy
from .paragraph import ParagraphStrategy
from .semantic import SemanticStrategy
from .separator import SeparatorStrategy

__all__ = [
    # 抽象基类
    "ChunkingStrategy",
    # 策略实现类
    "FixedLengthStrategy",
    "HierarchicalStrategy",
    "ParagraphStrategy",
    "SemanticStrategy",
    "SeparatorStrategy",
    "LLMChunkingStrategy",
    # 工厂函数
    "get_strategy",
    "list_strategies",
    "register_strategy",
    # 后处理函数
    "link_related_chunks",
]
