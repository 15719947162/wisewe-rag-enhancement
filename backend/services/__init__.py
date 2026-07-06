"""
Services - 业务服务编排层

本包负责编排复杂的业务流程，协调多个 core 模块完成端到端任务。
可以把它理解为"主厨"，根据菜单（routes）的订单，调度各个后厨（core）完成菜品。

## 为什么需要 Service 层？

假设一个"数据接入"接口需要：
1. 调用 parser 解析 PDF
2. 调用 cleaner 清洗内容
3. 调用 chunker 切片
4. 调用 embedding 向量化
5. 调用 output 写入数据库

如果把这些逻辑都写在路由里，代码会变得臃肿难维护。
Service 层就是把这些"编排逻辑"抽出来，让路由保持简洁。

## 服务文件对照表

| 文件 | 职责 |
|------|------|
| ingestion_service.py | 数据接入全流程：解析→清洗→切片→向量化→存储 |
| parse_service.py | PDF 解析服务，调用 parser 模块 |
| rag_service.py | RAG 问答服务，协调检索、重排、生成 |
| kb_service.py | 知识库管理服务，CRUD 操作 |
| console_service.py | 控制台服务，前端交互相关 |
| task_store.py | 任务状态存储，跟踪异步任务进度 |
| evaluation_store.py | 评估结果存储 |
| document_export_service.py | 文档导出服务 |
| chunk_draft_service.py | 切片草稿服务 |
| identity_service.py | 身份认证服务 |
| access_control.py | 访问控制，权限校验 |

## 典型使用方式

```python
# 在路由中调用 service
from backend.services.ingestion_service import IngestionService

@router.post("/ingest")
async def ingest(request: IngestRequest):
    service = IngestionService()
    result = await service.run(
        pdf_path=request.pdf_path,
        strategy=request.strategy,
    )
    return result
```

## Service vs Adapter 的区别

- **Service**：编排复杂流程，一个 service 可能调用多个 core 模块
- **Adapter**：简单桥接，一个 adapter 通常只对接一个 core 模块

Service 更"高层"，Adapter 更"底层"。
"""

# Service 层是实例化的类，不在此处统一导出
__all__: list[str] = []

