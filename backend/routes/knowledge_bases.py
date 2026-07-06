"""
知识库管理路由模块

本模块提供知识库（Knowledge Base）和文档（Document）的 CRUD 接口，支持：
- 知识库的创建、查询、更新、删除
- 文档的查询、删除、导出、源文件下载
- 知识库所有权转移
- 知识图谱查询
- 基于身份上下文的访问控制

## 架构说明

路由层职责：
- HTTP 请求解析与响应封装
- 身份认证与权限校验（通过 `get_current_identity` 依赖注入）
- 异常转换为 HTTP 状态码
- 调用 `backend.services.kb_service` 执行业务逻辑

依赖关系：
┌─────────────────────────────────────────────────────────┐
│  FastAPI Router (本模块)                                │
│    ↓ 调用                                               │
│  backend.services.kb_service (业务逻辑层)               │
│    ↓ 调用                                               │
│  core.db (数据库操作层)                                  │
└─────────────────────────────────────────────────────────┘

## 权限模型

当 `identity.enforce_access=True` 时，启用访问控制：
- 知识库操作：通过 `require_kb_access()` 校验用户是否有权访问指定知识库
- 文档操作：通过 `require_document_access()` 校验用户是否有权访问指定文档
- 转移所有权：仅知识库所有者可以执行

## API 示例

### 创建知识库
```bash
POST /api/knowledge-bases
Content-Type: application/json

{
  "name": "产品文档库",
  "description": "存储产品相关的技术文档",
  "strategy": "semantic"
}

# 响应 201
{
  "id": "a1b2c3d4e5f6...",
  "name": "产品文档库",
  "description": "存储产品相关的技术文档",
  "strategy": "semantic",
  "created_at": "2026-07-06T10:30:00Z"
}
```

### 查询知识库列表
```bash
GET /api/knowledge-bases

# 响应 200
[
  {
    "id": "a1b2c3d4e5f6...",
    "name": "产品文档库",
    "description": "存储产品相关的技术文档",
    "strategy": "semantic",
    "document_count": 5,
    "created_at": "2026-07-06T10:30:00Z"
  }
]
```

### 更新知识库
```bash
PUT /api/knowledge-bases/a1b2c3d4e5f6...
Content-Type: application/json

{
  "name": "产品文档库（已归档）",
  "description": "已归档的产品文档",
  "strategy": "hierarchical"
}

# 响应 200
{
  "id": "a1b2c3d4e5f6...",
  "name": "产品文档库（已归档）",
  "updated_at": "2026-07-06T11:00:00Z"
}
```

### 删除知识库
```bash
DELETE /api/knowledge-bases/a1b2c3d4e5f6...

# 响应 200
{
  "deleted": true,
  "kb_id": "a1b2c3d4e5f6..."
}
```

### 导出文档为 CSV
```bash
GET /api/documents/doc123/export.csv

# 响应 200
# Content-Type: text/csv; charset=utf-8
# Content-Disposition: attachment; filename="document_doc123.csv"
```
"""

from __future__ import annotations

from pathlib import Path
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from backend.schemas.requests import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseTransferOwnerRequest,
    KnowledgeBaseUpdateRequest,
)
from backend.services.document_export_service import build_csv_content_disposition
from backend.services.access_control import require_document_access, require_kb_access
from backend.services.identity_service import get_current_identity
from backend.services.kb_service import (
    create_knowledge_base_payload,
    delete_document_payload,
    delete_knowledge_base_payload,
    export_document_csv_payload,
    get_document_source_download_payload,
    get_document_detail_payload,
    get_document_graph_payload,
    get_documents_payload,
    get_knowledge_base_graph_payload,
    get_knowledge_bases_payload,
    transfer_knowledge_base_owner_payload,
    update_knowledge_base_payload,
)
from core.db.identity import IdentityContext

# ============================================================================
# 路由器配置
# ============================================================================

router = APIRouter()

# 输出目录，用于存放导出的文件（如 CSV）
_output_dir = Path("data/output")
_output_dir.mkdir(parents=True, exist_ok=True)


# ============================================================================
# 知识库 CRUD 接口
# ============================================================================

@router.get("/api/knowledge-bases")
def knowledge_bases(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """
    获取知识库列表

    根据用户身份上下文返回知识库列表。如果启用了访问控制（enforce_access=True），
    则只返回用户有权访问的知识库；否则返回所有知识库。

    Args:
        identity: 身份上下文，通过依赖注入自动获取

    Returns:
        list[dict]: 知识库列表，每个字典包含：
            - id: 知识库 ID（24 位十六进制字符串）
            - name: 知识库名称
            - description: 知识库描述
            - strategy: 切片策略
            - document_count: 文档数量
            - created_at: 创建时间

    Raises:
        HTTPException(503): 服务不可用（数据库连接失败等）

    API 示例:
        GET /api/knowledge-bases

        响应:
        [
          {
            "id": "a1b2c3d4e5f6g7h8i9j0k1l2",
            "name": "产品文档库",
            "description": "存储产品相关的技术文档",
            "strategy": "semantic",
            "document_count": 5,
            "created_at": "2026-07-06T10:30:00Z"
          }
        ]
    """
    try:
        if identity.enforce_access:
            # 启用访问控制，只返回用户有权访问的知识库
            return get_knowledge_bases_payload(identity)
        # 未启用访问控制，返回所有知识库
        return get_knowledge_bases_payload()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/knowledge-bases", status_code=201)
def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    创建新知识库

    创建一个新的知识库，指定名称、描述和切片策略。系统会自动生成一个
    不透明的 24 位 ID，避免在 URL、日志和 API 调用中暴露业务信息。

    创建流程：
    1. 生成唯一的知识库 ID（24 位 UUID 十六进制）
    2. 调用 kb_service 创建知识库记录
    3. 如果启用访问控制，自动绑定所有者信息

    Args:
        payload: 创建请求体，包含：
            - name: 知识库名称（必填）
            - description: 知识库描述（可选）
            - strategy: 切片策略（fixed_length/paragraph/semantic/separator/llm/hierarchical）
        identity: 身份上下文

    Returns:
        dict: 创建的知识库信息，包含：
            - id: 知识库 ID
            - name: 知识库名称
            - description: 知识库描述
            - strategy: 切片策略
            - created_at: 创建时间

    Raises:
        HTTPException(503): 服务不可用

    API 示例:
        POST /api/knowledge-bases
        Content-Type: application/json

        {
          "name": "产品文档库",
          "description": "存储产品相关的技术文档",
          "strategy": "semantic"
        }

        响应 201:
        {
          "id": "a1b2c3d4e5f6g7h8i9j0k1l2",
          "name": "产品文档库",
          "description": "存储产品相关的技术文档",
          "strategy": "semantic",
          "created_at": "2026-07-06T10:30:00Z"
        }
    """
    kb_id = _make_kb_id(payload.name)
    try:
        if identity.enforce_access:
            # 启用访问控制，绑定所有者信息
            return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        # 未启用访问控制，创建公开知识库
        return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/knowledge-bases/{kb_id}")
def update_knowledge_base(
    kb_id: str,
    payload: KnowledgeBaseUpdateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    更新知识库信息

    更新指定知识库的名称、描述和切片策略。部分更新支持：可以只更新部分字段。

    更新流程：
    1. 验证知识库是否存在
    2. 如果启用访问控制，验证用户是否有权修改
    3. 更新知识库记录
    4. 返回更新后的知识库信息

    Args:
        kb_id: 知识库 ID（路径参数）
        payload: 更新请求体，包含：
            - name: 新的知识库名称（可选）
            - description: 新的知识库描述（可选）
            - strategy: 新的切片策略（可选）
        identity: 身份上下文

    Returns:
        dict: 更新后的知识库信息

    Raises:
        HTTPException(404): 知识库不存在
        HTTPException(503): 服务不可用

    API 示例:
        PUT /api/knowledge-bases/a1b2c3d4e5f6g7h8i9j0k1l2
        Content-Type: application/json

        {
          "name": "产品文档库（已归档）",
          "description": "已归档的产品文档"
        }

        响应 200:
        {
          "id": "a1b2c3d4e5f6g7h8i9j0k1l2",
          "name": "产品文档库（已归档）",
          "description": "已归档的产品文档",
          "strategy": "semantic",
          "updated_at": "2026-07-06T11:00:00Z"
        }
    """
    try:
        if identity.enforce_access:
            # 启用访问控制，验证权限并更新
            return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        # 未启用访问控制，直接更新
        return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    删除知识库

    删除指定的知识库及其所有关联的文档和切片数据。此操作不可逆。

    删除流程：
    1. 验证知识库是否存在
    2. 如果启用访问控制，验证用户是否有权删除
    3. 级联删除所有关联的文档、切片记录
    4. 返回删除确认信息

    Args:
        kb_id: 知识库 ID（路径参数）
        identity: 身份上下文

    Returns:
        dict: 删除确认信息，包含：
            - deleted: 是否成功删除（True/False）
            - kb_id: 被删除的知识库 ID

    Raises:
        HTTPException(404): 知识库不存在
        HTTPException(503): 服务不可用

    API 示例:
        DELETE /api/knowledge-bases/a1b2c3d4e5f6g7h8i9j0k1l2

        响应 200:
        {
          "deleted": true,
          "kb_id": "a1b2c3d4e5f6g7h8i9j0k1l2"
        }
    """
    try:
        if identity.enforce_access:
            # 启用访问控制，验证权限并删除
            result = delete_knowledge_base_payload(kb_id, identity)
        else:
            # 未启用访问控制，直接删除
            result = delete_knowledge_base_payload(kb_id)
        if not result["deleted"]:
            # 知识库不存在
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ============================================================================
# 知识库高级操作
# ============================================================================

@router.post("/api/knowledge-bases/{kb_id}/transfer-owner")
def transfer_knowledge_base_owner(
    kb_id: str,
    payload: KnowledgeBaseTransferOwnerRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    转移知识库所有权

    将知识库的所有权转移给另一个用户。只有当前所有者可以执行此操作。

    转移流程：
    1. 验证操作者是当前所有者
    2. 验证目标用户存在
    3. 更新知识库的所有者字段
    4. 返回转移确认信息

    Args:
        kb_id: 知识库 ID（路径参数）
        payload: 转移请求体，包含：
            - ownerUserId: 新所有者的用户 ID
        identity: 身份上下文（必须是当前所有者）

    Returns:
        dict: 转移确认信息

    Raises:
        HTTPException(403): 权限不足（非所有者）
        HTTPException(404): 知识库或目标用户不存在
        HTTPException(400): 参数错误
        HTTPException(503): 服务不可用

    API 示例:
        POST /api/knowledge-bases/a1b2c3d4e5f6g7h8i9j0k1l2/transfer-owner
        Content-Type: application/json

        {
          "ownerUserId": "user456"
        }

        响应 200:
        {
          "kb_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
          "previous_owner": "user123",
          "new_owner": "user456",
          "transferred_at": "2026-07-06T12:00:00Z"
        }
    """
    try:
        return transfer_knowledge_base_owner_payload(kb_id, payload.ownerUserId, identity)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/knowledge-bases/{kb_id}/graph")
def knowledge_base_graph(kb_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取知识库的知识图谱

    返回知识库的知识图谱数据，包括文档节点、切片节点以及它们之间的关系。
    可用于可视化知识库结构和进行知识检索。

    访问控制：
    - 需要 `knowledge_base.graph.read` 权限
    - 通过 `require_kb_access` 验证

    Args:
        kb_id: 知识库 ID（路径参数）
        identity: 身份上下文

    Returns:
        dict: 知识图谱数据，包含：
            - nodes: 节点列表（文档、切片等）
            - edges: 边列表（文档-切片关系、切片间关联等）
            - metadata: 图谱元数据（节点数、边数等）

    Raises:
        HTTPException(404): 知识库不存在
        HTTPException(503): 服务不可用

    API 示例:
        GET /api/knowledge-bases/a1b2c3d4e5f6g7h8i9j0k1l2/graph

        响应 200:
        {
          "nodes": [
            {"id": "doc1", "type": "document", "label": "产品手册.pdf"},
            {"id": "chunk1", "type": "chunk", "label": "产品介绍"}
          ],
          "edges": [
            {"source": "doc1", "target": "chunk1", "type": "contains"}
          ],
          "metadata": {
            "node_count": 2,
            "edge_count": 1
          }
        }
    """
    try:
        # 验证访问权限
        require_kb_access(kb_id, identity, action="knowledge_base.graph.read", resource_id=kb_id)
        return get_knowledge_base_graph_payload(kb_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ============================================================================
# 文档管理接口
# ============================================================================

@router.get("/api/documents")
def documents(kb_id: str | None = None, identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    """
    获取文档列表

    获取指定知识库的文档列表，或获取所有文档（不指定 kb_id 时）。

    访问控制：
    - 如果指定 kb_id，需要该知识库的 `document.list` 权限
    - 如果启用访问控制，只返回用户有权访问的文档

    Args:
        kb_id: 知识库 ID（可选查询参数），筛选指定知识库的文档
        identity: 身份上下文

    Returns:
        list[dict]: 文档列表，每个字典包含：
            - id: 文档 ID
            - kb_id: 所属知识库 ID
            - filename: 原始文件名
            - status: 处理状态（pending/processing/completed/failed）
            - chunk_count: 切片数量
            - created_at: 上传时间

    Raises:
        HTTPException(503): 服务不可用

    API 示例:
        # 获取所有文档
        GET /api/documents

        # 获取指定知识库的文档
        GET /api/documents?kb_id=a1b2c3d4e5f6g7h8i9j0k1l2

        响应 200:
        [
          {
            "id": "doc123",
            "kb_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
            "filename": "产品手册.pdf",
            "status": "completed",
            "chunk_count": 42,
            "created_at": "2026-07-06T10:30:00Z"
          }
        ]
    """
    try:
        if kb_id:
            # 指定了知识库 ID，验证访问权限
            require_kb_access(kb_id, identity, action="document.list", resource_type="knowledge_base", resource_id=kb_id)
        # 获取文档列表，根据是否启用访问控制传递不同参数
        return get_documents_payload(kb_id, identity if identity.enforce_access else None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}")
def document_detail(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取文档详情

    返回指定文档的详细信息，包括元数据、处理状态、切片统计等。

    访问控制：
    - 需要 `document.detail.read` 权限
    - 通过 `require_document_access` 验证

    Args:
        document_id: 文档 ID（路径参数）
        identity: 身份上下文

    Returns:
        dict: 文档详情，包含：
            - id: 文档 ID
            - kb_id: 所属知识库 ID
            - filename: 原始文件名
            - status: 处理状态
            - chunk_count: 切片数量
            - file_size: 文件大小（字节）
            - created_at: 上传时间
            - processed_at: 处理完成时间
            - metadata: 其他元数据

    Raises:
        HTTPException(404): 文档不存在
        HTTPException(503): 服务不可用

    API 示例:
        GET /api/documents/doc123

        响应 200:
        {
          "id": "doc123",
          "kb_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
          "filename": "产品手册.pdf",
          "status": "completed",
          "chunk_count": 42,
          "file_size": 2048576,
          "created_at": "2026-07-06T10:30:00Z",
          "processed_at": "2026-07-06T10:35:00Z"
        }
    """
    try:
        # 验证访问权限
        require_document_access(document_id, identity, action="document.detail.read")
        return get_document_detail_payload(document_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/graph")
def document_graph(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    获取文档的知识图谱

    返回指定文档的知识图谱数据，包括切片节点、切片间关联关系等。

    访问控制：
    - 需要 `document.graph.read` 权限
    - 通过 `require_document_access` 验证

    Args:
        document_id: 文档 ID（路径参数）
        identity: 身份上下文

    Returns:
        dict: 知识图谱数据，包含：
            - nodes: 切片节点列表
            - edges: 切片间关联关系
            - metadata: 图谱元数据

    Raises:
        HTTPException(404): 文档不存在
        HTTPException(503): 服务不可用

    API 示例:
        GET /api/documents/doc123/graph

        响应 200:
        {
          "nodes": [
            {"id": "chunk1", "type": "chunk", "label": "产品介绍"},
            {"id": "chunk2", "type": "chunk", "label": "功能特性"}
          ],
          "edges": [
            {"source": "chunk1", "target": "chunk2", "type": "related"}
          ]
        }
    """
    try:
        # 验证访问权限
        require_document_access(document_id, identity, action="document.graph.read")
        return get_document_graph_payload(document_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    删除文档

    删除指定的文档及其所有关联的切片数据。此操作不可逆。

    访问控制：
    - 需要 `document.delete` 权限
    - 通过 `require_document_access` 验证

    删除流程：
    1. 验证文档存在且用户有权删除
    2. 级联删除所有切片记录
    3. 删除文档元数据
    4. 返回删除确认信息

    Args:
        document_id: 文档 ID（路径参数）
        identity: 身份上下文

    Returns:
        dict: 删除确认信息，包含：
            - deleted: 是否成功删除（True/False）
            - document_id: 被删除的文档 ID

    Raises:
        HTTPException(404): 文档不存在
        HTTPException(503): 服务不可用

    API 示例:
        DELETE /api/documents/doc123

        响应 200:
        {
          "deleted": true,
          "document_id": "doc123"
        }
    """
    try:
        # 验证访问权限
        require_document_access(document_id, identity, action="document.delete")
        result = delete_document_payload(document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ============================================================================
# 文档导出与下载接口
# ============================================================================

@router.get("/api/documents/{document_id}/export.csv")
def export_document_csv(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> Response:
    """
    导出文档为 CSV 文件

    将指定文档的所有切片数据导出为 CSV 文件，可用于：
    - 数据备份
    - 导入其他系统
    - 离线分析

    访问控制：
    - 需要 `document.export_csv` 权限
    - 通过 `require_document_access` 验证

    CSV 格式包含字段：
    - content: 切片内容
    - source: 来源文档名
    - page: 页码
    - chunk_index: 切片索引
    - strategy: 切片策略
    - embedding: 向量（JSON 数组字符串）

    Args:
        document_id: 文档 ID（路径参数）
        identity: 身份上下文

    Returns:
        Response: CSV 文件响应
            - Content-Type: text/csv; charset=utf-8
            - Content-Disposition: attachment; filename="<filename>.csv"

    Raises:
        HTTPException(404): 文档不存在
        HTTPException(503): 服务不可用

    API 示例:
        GET /api/documents/doc123/export.csv

        响应 200:
        Content-Type: text/csv; charset=utf-8
        Content-Disposition: attachment; filename="product_manual_doc123.csv"

        CSV 内容:
        content,source,page,chunk_index,strategy,embedding
        "产品介绍...",product_manual.pdf,1,0,semantic,[0.1,0.2,...]
    """
    try:
        # 验证访问权限
        require_document_access(document_id, identity, action="document.export_csv")
        filename, content = export_document_csv_payload(document_id)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": build_csv_content_disposition(filename)},
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/source")
def download_document_source(document_id: str, identity: IdentityContext = Depends(get_current_identity)):
    """
    下载文档源文件

    下载原始 PDF 文件。支持两种存储方式：
    1. OSS 存储：返回 302 重定向到 OSS 签名 URL
    2. 本地存储：直接返回文件流

    访问控制：
    - 需要 `document.source.download` 权限
    - 通过 `require_document_access` 验证

    Args:
        document_id: 文档 ID（路径参数）
        identity: 身份上下文

    Returns:
        RedirectResponse | FileResponse:
            - OSS 存储：302 重定向到签名 URL
            - 本地存储：PDF 文件流

    Raises:
        HTTPException(404): 文档或文件不存在
        HTTPException(503): 服务不可用

    API 示例:
        GET /api/documents/doc123/source

        # OSS 存储响应
        HTTP/1.1 302 Found
        Location: https://bucket.oss-cn-hangzhou.aliyuncs.com/...

        # 本地存储响应
        HTTP/1.1 200 OK
        Content-Type: application/pdf
        Content-Disposition: attachment; filename="product_manual.pdf"

        <PDF 文件二进制内容>
    """
    try:
        # 验证访问权限
        require_document_access(document_id, identity, action="document.source.download")
        payload = get_document_source_download_payload(document_id)
        if payload["kind"] == "oss":
            # OSS 存储：重定向到签名 URL
            return RedirectResponse(payload["url"], status_code=302)
        # 本地存储：返回文件
        return FileResponse(
            payload["path"],
            media_type="application/pdf",
            filename=payload["filename"],
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ============================================================================
# 辅助函数
# ============================================================================

def _make_kb_id(name: str) -> str:
    """
    生成不透明的知识库 ID

    生成一个 24 位十六进制字符串作为知识库 ID。ID 与名称无关，
    避免在 URL、日志和 API 调用中暴露业务信息（拼音、主题、租户等）。

    设计考虑：
    - 使用 UUID 而非自增 ID，避免被枚举攻击
    - 截取前 24 位（96 位熵），足够唯一且长度适中
    - 不透明设计，防止通过 ID 推断业务含义

    Args:
        name: 知识库名称（仅用于日志记录，不影响 ID 生成）

    Returns:
        str: 24 位十六进制字符串，例如 "a1b2c3d4e5f6g7h8i9j0k1l2"

    示例:
        >>> _make_kb_id("产品文档库")
        'f47ac10b58cc4372a8670701'
        >>> _make_kb_id("产品文档库")  # 每次调用生成不同 ID
        '550e8400e29b41d4a7164466'
    """
    return _uuid.uuid4().hex[:24]
