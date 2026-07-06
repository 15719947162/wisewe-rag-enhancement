"""
Routes - HTTP 路由层

本包定义了所有的 REST API 端点，每个文件对应一类业务边界。
可以把路由理解为"菜单"，告诉客户端"我们提供什么服务"。

## 路由文件对照表

| 文件 | 路由前缀 | 功能说明 |
|------|----------|----------|
| health.py | /health | 健康检查，用于服务探活 |
| dashboard.py | /dashboard | 仪表盘数据，展示统计信息 |
| parse.py | /parse | PDF 解析，上传并解析文档 |
| ingestion.py | /ingestion | 数据接入，解析→清洗→切片→向量化全流程 |
| rag.py | /rag | RAG 问答，检索增强生成接口 |
| console.py | /console | 控制台，前端交互相关接口 |
| eval.py | /eval | 评估相关，RAG 效果评测 |
| identity.py | /identity | 身份认证，用户登录与权限 |
| knowledge_bases.py | /kb | 知识库管理，CRUD 操作 |
| openapi_v1.py | /api/v1 | OpenAPI 兼容接口，供第三方调用 |

## 典型请求流程

```
客户端请求 → FastAPI 路由匹配 → schemas 校验参数
                                    ↓
                            services 编排业务
                                    ↓
                            adapters 调用 core
                                    ↓
                            schemas 构造响应 → 返回客户端
```

## 如何添加新路由

1. 在 `backend/routes/` 下创建新文件，如 `my_feature.py`
2. 定义路由器：`router = APIRouter(prefix="/my-feature", tags=["My Feature"])`
3. 在 `backend/app.py` 中注册：`app.include_router(my_feature.router)`
"""

# 路由层不导出公共符号，各路由通过 app.py 注册
__all__: list[str] = []

