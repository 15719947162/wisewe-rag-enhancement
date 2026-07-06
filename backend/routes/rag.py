"""
RAG（检索增强生成）查询路由模块

本模块提供 RAG 系统的核心查询 API 端点，支持两种查询模式：
1. 标准 RAG 查询：基于向量相似度检索相关文档片段
2. 图 RAG 查询：结合知识图谱增强的检索生成

## 主要功能

- 向量检索：从 pgvector 数据库中检索与查询语义相似的文档切片
- LLM 生成：基于检索结果生成自然语言回答
- 身份鉴权：支持多租户数据隔离，按 identity 过滤检索范围
- 来源追溯：返回回答中引用的原始文档片段

## 查询流程

```
用户查询 → 身份验证 → 向量检索 → 构建提示词 → LLM 生成 → 返回结果
    │           │           │            │           │
    │           │           │            │           └─ 包含答案 + 来源引用
    │           │           │            └─ 将检索结果注入 LLM 上下文
    │           │           └─ embedding 相似度匹配
    │           └─ 按用户/租户过滤数据范围
    └─ 原始问题文本
```

## 错误处理

- 404: 未找到相关文档（ValueError）
- 503: 服务不可用（如 LLM API 故障）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.identity_service import get_current_identity
from backend.services.rag_service import run_graph_rag_query, run_rag_query
from core.db.identity import IdentityContext

# 创建路由器实例，用于挂载到 FastAPI 应用
router = APIRouter()


@router.post("/api/rag/query")
def rag_query(
    payload: QueryRequest,
    identity: IdentityContext = Depends(get_current_identity)
) -> dict:
    """
    标准 RAG 查询接口

    接收用户问题，从向量数据库检索相关文档，调用 LLM 生成回答。

    ## 请求参数 (QueryRequest)

    - `query`: 用户问题文本，必填
    - `top_k`: 返回的最大相关文档数量，默认 5
    - `min_score`: 相似度阈值，低于此值的文档将被过滤，默认 0.5
    - `collection`: 指定检索的知识库集合名，可选
    - `metadata_filter`: 元数据过滤条件，用于精确筛选文档，可选

    ## 响应结构 (dict)

    ```json
    {
        "answer": "LLM 生成的回答文本",
        "sources": [
            {
                "content": "检索到的文档片段内容",
                "score": 0.85,
                "metadata": {"source": "file.pdf", "page": 1}
            }
        ],
        "query": "原始查询文本",
        "latency_ms": 1234
    }
    ```

    ## 访问控制

    - 如果 `identity.enforce_access=True`，仅检索该用户有权限的文档
    - 权限过滤在向量检索层面实现，确保数据隔离

    ## 异常

    - HTTP 404: 未找到相关文档（抛出 ValueError）
    - HTTP 503: 服务不可用（如 LLM API 调用失败）
    """
    try:
        # 根据身份验证配置决定是否启用访问控制
        if identity.enforce_access:
            # 启用访问控制：仅检索该用户有权访问的文档
            return run_rag_query(payload, identity)
        # 无访问控制：检索所有文档
        return run_rag_query(payload)
    except ValueError as exc:
        # ValueError 通常表示"未找到相关文档"等业务逻辑错误
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # 其他异常归类为服务不可用（如网络错误、LLM API 故障）
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/rag/graph-query")
def graph_rag_query(
    payload: GraphQueryRequest,
    identity: IdentityContext = Depends(get_current_identity)
) -> dict:
    """
    图增强 RAG 查询接口

    在标准 RAG 基础上，结合知识图谱进行查询增强：
    1. 实体识别：从查询中提取关键实体
    2. 图谱遍历：在知识图谱中查找相关实体及其关系
    3. 上下文增强：将图谱信息注入检索上下文
    4. LLM 生成：基于增强后的上下文生成回答

    ## 请求参数 (GraphQueryRequest)

    继承 QueryRequest 的所有参数，额外包含：

    - `graph_depth`: 图谱遍历深度，默认 1
    - `relation_types`: 限制遍历的关系类型列表，可选
    - `include_entities`: 是否在响应中返回识别的实体，默认 True

    ## 响应结构 (dict)

    ```json
    {
        "answer": "LLM 生成的回答文本",
        "sources": [...],  // 同标准 RAG
        "entities": [
            {
                "name": "实体名称",
                "type": "PERSON|ORG|CONCEPT",
                "relations": [{"target": "关联实体", "type": "关系类型"}]
            }
        ],
        "graph_context": "图谱增强的上下文信息",
        "query": "原始查询文本",
        "latency_ms": 2345
    }
    ```

    ## 使用场景

    - 需要多跳推理的复杂问题
    - 涉及实体关系的查询（如"张三的上级是谁"）
    - 需要上下文关联的知识探索

    ## 异常

    - HTTP 404: 未找到相关文档或图谱数据
    - HTTP 503: 服务不可用（如向量库或图数据库连接失败）
    """
    try:
        # 根据身份验证配置决定是否启用访问控制
        if identity.enforce_access:
            # 启用访问控制：在图谱遍历和向量检索时均应用权限过滤
            return run_graph_rag_query(payload, identity)
        # 无访问控制：全量检索
        return run_graph_rag_query(payload)
    except ValueError as exc:
        # ValueError 通常表示"未找到相关文档或实体"等业务逻辑错误
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # 其他异常归类为服务不可用
        raise HTTPException(status_code=503, detail=str(exc)) from exc
