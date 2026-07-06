"""
Adapters - 适配器层

本包负责将 HTTP 层的调用转换为对 core 领域模块的调用。
可以把它理解为"翻译官"，让 backend 和 core 能够顺畅沟通。

## 为什么需要 Adapter？

```
┌──────────────────┐
│  HTTP 路由层      │  ← 理解 HTTP 请求、响应格式
└──────────────────┘
         ↓ 需要适配
┌──────────────────┐
│  核心领域层       │  ← 理解业务逻辑，不关心 HTTP
└──────────────────┘
```

Adapter 的作用：
1. **参数转换**：HTTP 请求参数 → core 函数参数
2. **结果转换**：core 返回结果 → HTTP 响应格式
3. **异常处理**：core 抛出的异常 → HTTP 错误响应
4. **日志记录**：记录调用链路，便于排查问题

## 适配器文件对照表

| 文件 | 桥接模块 | 主要功能 |
|------|----------|----------|
| parse_adapter.py | core/parser | PDF 解析相关调用 |
| kb_adapter.py | core/db | 知识库数据库操作 |
| rag_adapter.py | core/rag | RAG 检索生成调用 |

## 使用示例

```python
# 在 service 中使用 adapter
from backend.adapters.parse_adapter import ParseAdapter

class IngestionService:
    def __init__(self):
        self.parse_adapter = ParseAdapter()

    async def parse_pdf(self, pdf_path: str):
        # 通过 adapter 调用 core 的解析能力
        blocks = await self.parse_adapter.parse(pdf_path)
        return blocks
```

## 设计原则

1. **单向依赖**：backend/adapters → core（绝不能反向依赖）
2. **薄层设计**：adapter 应该尽量简单，只做转换，不写业务逻辑
3. **异常隔离**：core 的异常不应直接抛到 HTTP 层
"""

# Adapter 层不统一导出，各模块按需导入具体的 adapter
__all__: list[str] = []

