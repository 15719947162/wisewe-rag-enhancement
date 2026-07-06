"""
RAG - 检索增强生成模块

本包实现了 RAG（Retrieval-Augmented Generation）的核心流程：
把用户问题变成精准答案。可以把它理解为"智能问答系统的大脑"。

## RAG 是什么？

```
用户提问: "什么是机器学习？"
        ↓
    问题理解
        ↓
    知识检索 → 从知识库中找到相关内容
        ↓
    重排序 → 把最相关的内容排在前面
        ↓
    答案生成 → LLM 基于检索结果生成答案
        ↓
答案: "机器学习是人工智能的一个分支，它使计算机能够从数据中学习..."
```

## 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| HybridRetriever | retriever.py | 混合检索，结合向量检索和关键词检索 |
| ParentChildReranker | reranker.py | 重排序，优化检索结果顺序 |
| RAGGenerator | generator.py | 答案生成，调用 LLM 生成最终回复 |
| RAGScorer | scorer.py | 效果评估，计算检索质量指标 |
| IntentRouter | intent_router.py | 意图识别，判断问题类型 |
| GraphRetriever | graph_retriever.py | 图谱检索，从知识图谱中检索 |
| GraphExpander | graph_expander.py | 图谱扩展，扩展相关实体 |

## 使用示例

### 完整 RAG 流程

```python
from core.rag import HybridRetriever, ParentChildReranker, RAGGenerator

# 1. 检索相关内容
retriever = HybridRetriever(kb_id="kb_001")
candidates = retriever.retrieve("什么是机器学习？", top_k=10)

# 2. 重排序，筛选最相关的
reranker = ParentChildReranker()
ranked = reranker.rerank(candidates, top_n=5)

# 3. 生成答案
generator = RAGGenerator()
answer = generator.generate(
    question="什么是机器学习？",
    context=ranked,
)

print(answer)
```

### 仅检索（不生成）

```python
from core.rag import HybridRetriever

retriever = HybridRetriever(kb_id="kb_001")
results = retriever.retrieve("机器学习的应用", top_k=5)

for i, result in enumerate(results):
    print(f"【第{i+1}条】{result.content[:100]}...")
    print(f"相似度: {result.score:.3f}")
```

## 检索策略

### 混合检索（Hybrid Retrieval）

结合两种检索方式，提高召回率：

1. **向量检索**：语义相似，能理解同义词
2. **关键词检索**：精确匹配，适合专业术语

```python
# 向量检索能找到 "AI" 和 "人工智能" 的关联
# 关键词检索能精确匹配 "GPT-4" 这样的专有名词
```

### 父子重排序（Parent-Child Reranking）

层级切片场景下的优化策略：

```
父切片（大段落）
    └── 子切片（小知识点）

检索时：用子切片匹配（粒度细）
重排时：返回父切片（上下文完整）
```

## 环境配置

```bash
# .env 文件

# LLM 用于答案生成
RAG_LLM_MODEL=qwen-max

# 向量数据库
PGVECTOR_HOST=localhost
PGVECTOR_PORT=5432
```
"""

## 知识图谱扩展（Graph Expansion）

当检索命中的片段提到某个实体时，我们会"顺藤摸瓜"找到其他也提到这个实体的片段。

```
用户问："如何配置数据库连接？"
        ↓
向量检索找到第 5 页的"数据库配置"段落
        ↓
图谱扩展发现：这个段落提到了"ConnectionPool"实体
        ↓
继续查找：第 12 页也提到了"ConnectionPool"
        ↓
结果：把第 12 页的"连接池优化"内容也加入结果
```

这样用户能看到更多相关内容，即使这些内容字面上和问题不完全匹配。

## 检索快照（Retrieval Snapshot）

传统检索需要多次查询数据库（向量、BM25、相关片段各查一次）。
检索快照用一个"超级 SQL"把所有需要的内容一次性拉出来，后续都在内存中处理。

**性能提升：**
- 原来：4-5 次数据库查询
- 现在：1 次超级查询
- 典型场景：延迟从 200ms 降到 50ms

**快照包含：**
- base：向量/BM25 直接命中的片段
- fold：层级折叠用的 parent 片段
- related：相关片段（表格、图片等）
"""

from core.rag.generator import RAGGenerator
from core.rag.reranker import ParentChildReranker
from core.rag.retriever import HybridRetriever
from core.rag.scorer import RAGScorer

__all__ = [
    "HybridRetriever",      # 混合检索器
    "ParentChildReranker",  # 父子重排序器
    "RAGGenerator",         # 答案生成器
    "RAGScorer",            # 效果评估器
]

