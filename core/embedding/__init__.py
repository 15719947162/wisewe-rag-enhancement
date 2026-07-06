"""
Embedding - 文本向量化模块

本包负责将文本转换为向量（一串数字），让计算机能"理解"文本的语义。
是 RAG 检索的核心基础——有了向量才能做相似度搜索。

## 什么是 Embedding？

```
文本: "人工智能正在改变世界"
      ↓ Embedding 模型
向量: [0.123, -0.456, 0.789, ..., 0.234]  (通常 768~1536 维)
```

向量化的好处：
- 语义相似的文本，向量也相似
- 可以用数学方法计算相似度（余弦相似度）
- 把"文本匹配"变成"数学计算"，速度快

## 支持的 Embedding 模型

通过配置不同的 API Base URL，可以接入多种模型：

| 模型来源 | 配置方式 |
|----------|----------|
| DashScope（阿里云） | 设置 DASHSCOPE_API_KEY |
| OpenAI | 设置 OPENAI_API_KEY |
| 自定义 API | 设置 LLM_API_KEY + LLM_BASE_URL |

## 使用示例

```python
from core.embedding import embed_texts

# 批量向量化
texts = [
    "人工智能正在改变世界",
    "机器学习是 AI 的核心技术",
    "今天天气真好",
]

vectors = embed_texts(texts)

print(f"生成了 {len(vectors)} 个向量")
print(f"每个向量维度: {len(vectors[0])}")  # 如 1536

# 计算相似度
import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

sim = cosine_similarity(vectors[0], vectors[1])
print(f"前两句相似度: {sim:.3f}")  # 会比较高，因为语义相近
```

## 环境配置

```bash
# .env 文件

# 方式一：使用 DashScope（阿里云）
DASHSCOPE_API_KEY=sk-xxx

# 方式二：使用 OpenAI
OPENAI_API_KEY=sk-xxx

# 方式三：自定义 API
LLM_API_KEY=xxx
LLM_BASE_URL=https://your-api.com/v1
LLM_EMBEDDING_MODEL=text-embedding-3-small
LLM_EMBEDDING_BATCH_SIZE=100
```

## 性能考虑

1. **批量处理**：支持批量向量化，减少 API 调用次数
2. **缓存**：相同文本的向量可以缓存，避免重复计算
3. **成本**：Embedding API 通常按 token 计费，注意控制成本
"""

from core.embedding.client import embed_texts

__all__ = [
    "embed_texts",  # 文本向量化主函数
]
