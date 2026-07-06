"""
Models - 领域模型定义

本包定义了 RAG 系统的核心数据结构，是整个数据流转的"通用语言"。
所有模块都围绕这些模型工作，理解它们就理解了系统的数据骨架。

## 核心模型一览

| 模型 | 文件 | 说明 |
|------|------|------|
| ContentBlock | content_block.py | 解析器输出，代表 PDF 中的一个内容块 |
| Chunk | content_block.py | 切片器输出，代表一个知识片段 |
| Entity | entity.py | 实体，从文本中抽取的知识实体 |
| Relation | relation.py | 关系，实体之间的关联 |
| Triple | triple.py | 三元组，(主体, 谓语, 客体) 知识表示 |
| ExtractedEntity | extracted_entity.py | 抽取结果，带置信度的实体 |

## 最常用的两个模型

### ContentBlock - 解析后的内容块

```python
from core.models import ContentBlock

block = ContentBlock(
    type="text",           # 类型：text / table / image
    text="这是段落内容...",  # 文本内容
    page_idx=0,            # 所在页码
    source_file="doc.pdf", # 来源文件
    is_table=False,        # 是否为表格
    table_html=None,       # 表格的 HTML（如果是表格）
    image_path=None,       # 图片路径（如果是图片）
    bbox=[x1, y1, x2, y2], # 边界框坐标
)
```

### Chunk - 切片后的知识片段

```python
from core.models import Chunk

chunk = Chunk(
    content="知识片段内容...",
    source="doc.pdf",
    page=0,
    chunk_index=0,
    strategy="paragraph",  # 切片策略名称
    layer="child",         # 层级：parent/child/enhanced
    parent_id=None,        # 父切片 ID（层级切片用）
    related_ids=[],        # 关联切片 ID（图表关联用）
)
```

## 数据流转图

```
PDF 解析
    ↓
ContentBlock（原始内容块）
    ↓ 清洗
ContentBlock（干净内容块）
    ↓ 切片
Chunk（知识片段）
    ↓ 向量化
向量 + Chunk 元数据
    ↓ 存储
数据库
```

## 设计原则

1. **数据类优先**：使用 @dataclass，简单清晰
2. **不可变性**：创建后尽量不修改，需要变化时创建新实例
3. **类型标注完整**：所有字段都有类型提示，便于 IDE 补全
4. **单一职责**：每个模型只描述一类事物
"""

from core.models.content_block import Chunk, ContentBlock
from core.models.entity import Entity
from core.models.extracted_entity import ExtractedEntity
from core.models.relation import Relation
from core.models.triple import Triple

__all__ = [
    # 核心数据模型
    "ContentBlock",     # 解析后的内容块
    "Chunk",            # 切片后的知识片段
    # 知识图谱相关
    "Entity",           # 实体
    "Relation",         # 关系
    "Triple",           # 三元组
    "ExtractedEntity",  # 抽取结果
]
