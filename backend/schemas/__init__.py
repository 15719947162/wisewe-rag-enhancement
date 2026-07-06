"""
Schemas - 请求数据模式定义

本包使用 Pydantic 定义 HTTP 接口的请求和响应数据结构。
可以把它理解为"数据合同"，明确约定"发什么数据、收什么数据"。

## 为什么需要 Schemas？

1. **自动校验**：FastAPI 自动校验请求参数，不合法直接返回 422 错误
2. **自动文档**：自动生成 OpenAPI 文档（Swagger UI）
3. **类型安全**：IDE 自动补全，减少手误
4. **序列化**：自动转换 JSON ↔ Python 对象

## 文件结构

```
schemas/
├── __init__.py      ← 本文件
└── requests.py      ← 请求体定义（如 ParseRequest, RAGRequest）
```

## 使用示例

```python
# 在路由中使用 schemas
from backend.schemas.requests import ParseRequest
from fastapi import FastAPI

app = FastAPI()

@app.post("/parse")
async def parse(request: ParseRequest):
    # FastAPI 自动校验 request 的字段类型
    # 如果校验失败，自动返回 422 错误
    return {"status": "ok"}
```

## 与 core/models 的区别

- **backend/schemas**：HTTP 接口专用，定义 API 契约
- **core/models**：领域模型，定义业务实体（如 ContentBlock、Chunk）

两者职责不同，不要混用。schemas 负责"对外"，models 负责"对内"。
"""

# 目前 schemas 只有 requests.py，按需扩展
from backend.schemas.requests import ParseRequest, RAGRequest

__all__ = [
    "ParseRequest",   # PDF 解析请求
    "RAGRequest",     # RAG 问答请求
]

