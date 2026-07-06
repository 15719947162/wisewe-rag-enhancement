"""
DB - 数据库操作模块

本包负责所有数据库相关的操作，包括 PostgreSQL/pgvector 存储、
知识库管理、用户身份认证等。是 RAG 系统的"仓库管理员"。

## 数据库架构

项目使用 PostgreSQL 作为主数据库，配合 pgvector 扩展存储向量：

```
PostgreSQL + pgvector
├── knowledge_bases      # 知识库元数据
├── documents            # 文档记录
├── chunks               # 知识片段（含向量）
├── api_keys             # API 密钥管理
├── identities           # 用户身份
└── query_logs           # 查询日志
```

## 文件对照表

| 文件 | 职责 |
|------|------|
| connection.py | 数据库连接管理，连接池配置 |
| schema.py | 表结构定义，SQLAlchemy 模型 |
| init_db.py | 数据库初始化，创建表和索引 |
| knowledge_base.py | 知识库 CRUD 操作 |
| api_keys.py | API 密钥管理 |
| identity.py | 用户身份存储 |
| query_logs.py | 查询日志记录 |

## 使用示例

### 初始化数据库

```python
from core.db import init_db

# 创建表和索引
init_db()
```

### 写入知识片段（含向量）

```python
from core.db.knowledge_base import save_chunks

chunks = [...]  # Chunk 对象列表
vectors = [...]  # 向量列表

save_chunks(
    kb_id="kb_001",
    doc_id="doc_001",
    chunks=chunks,
    vectors=vectors,
)
```

### 查询向量相似度

```python
from core.db.knowledge_base import search_similar

# 查询与 query_vector 最相似的 5 个片段
results = search_similar(
    kb_id="kb_001",
    query_vector=[0.1, 0.2, ...],
    top_k=5,
)
```

## 环境配置

数据库连接通过环境变量配置：

```bash
# .env 文件
PGVECTOR_HOST=localhost
PGVECTOR_PORT=5432
PGVECTOR_DB=rag_db
PGVECTOR_USER=postgres
PGVECTOR_PASSWORD=your_password
```

## 注意事项

1. **向量维度**：pgvector 需要在建表时指定向量维度，需与 embedding 模型匹配
2. **索引优化**：大规模向量检索建议使用 IVFFlat 或 HNSW 索引
3. **连接池**：生产环境建议使用连接池，避免频繁建连
"""

# 数据库模块按需导入具体功能
__all__: list[str] = []
