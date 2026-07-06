"""
Output - 结果输出模块

本包负责将处理后的数据导出为各种格式，是 RAG 管道的"最后一公里"。
就像快递员把包裹送到你手里，这个模块把处理结果送到目的地。

## 支持的输出方式

| 方式 | 文件 | 用途 |
|------|------|------|
| CSV 文件 | csv_writer.py | 导出知识库 CSV，供其他系统导入 |
| pgvector 数据库 | pgvector_writer.py | 写入 PostgreSQL，支持向量检索 |
| 实体导出 | entity_writer.py | 导出知识图谱实体 |
| 统计报告 | stats.py | 生成处理统计信息 |

## 典型使用流程

```
切片列表 (list[Chunk])
    ↓
compute_stats() → 统计信息（切片数量、长度分布等）
    ↓
format_stats_report() → 文本报告（用于对比不同策略）
    ↓
write_knowledge_csv() → CSV 文件（数据交换）
    ↓
write_to_pgvector() → 向量数据库（在线检索）
    ↓
write_entities() → 实体表 + 引用表（知识图谱）
```

## 使用示例

### 导出 CSV 给其他系统

```python
from core.output import write_knowledge_csv
from core.models import Chunk

chunks = [
    Chunk(content="知识点1", source="doc.pdf", page=1, ...),
    Chunk(content="知识点2", source="doc.pdf", page=2, ...),
]

write_knowledge_csv(
    chunks=chunks,
    output_path="data/output/knowledge.csv",
)

# CSV 格式：
# id,content,source,page,chunk_index,strategy
# 1,知识点1,doc.pdf,1,0,paragraph
# 2,知识点2,doc.pdf,2,1,paragraph
```

### 写入 pgvector 数据库

```python
from core.output import write_to_pgvector
from core.models import Chunk

chunks = [...]  # Chunk 列表
vectors = [...]  # 对应的向量列表

write_to_pgvector(
    kb_id="kb_001",
    doc_id="doc_001",
    chunks=chunks,
    vectors=vectors,
)
```

### 生成统计报告

```python
from core.output import compute_stats, format_stats_report

# 计算统计数据
stats = compute_stats(chunks)

# 生成可读报告
report = format_stats_report(stats)
print(report)

# 输出示例：
# ===== 切片统计报告 =====
# 总切片数: 150
# 平均长度: 256 字符
# 最大长度: 512 字符
# 最小长度: 50 字符
```

## CSV 输出格式说明

标准知识库 CSV 包含以下列：

| 列名 | 说明 |
|------|------|
| id | 切片唯一标识 |
| content | 切片内容 |
| source | 来源文件名 |
| page | 所在页码 |
| chunk_index | 切片序号 |
| strategy | 切片策略 |
| layer | 层级（parent/child/enhanced） |
| parent_id | 父切片 ID |
| related_ids | 关联切片 ID |

## 与数据库写入的区别

- **CSV 导出**：适合数据交换、备份、离线分析
- **pgvector 写入**：适合在线检索、实时查询

两者可以同时使用，互不冲突。
"""

from core.output.csv_writer import write_knowledge_csv
from core.output.entity_writer import write_entities
from core.output.pgvector_writer import write_to_pgvector
from core.output.stats import ChunkStats, compute_stats, format_stats_report

__all__ = [
    # CSV 导出
    "write_knowledge_csv",  # 导出知识库 CSV
    # 数据库写入
    "write_to_pgvector",    # 写入 pgvector 数据库
    # 实体导出
    "write_entities",       # 导出实体和引用关系
    # 统计报告
    "ChunkStats",           # 切片统计数据类
    "compute_stats",        # 计算切片统计
    "format_stats_report",  # 格式化统计报告
]