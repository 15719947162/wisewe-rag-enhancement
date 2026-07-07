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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response

from backend.schemas.requests import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseTransferOwnerRequest,
    KnowledgeBaseUpdateRequest,
)
from backend.services.document_export_service import build_csv_content_disposition, export_document_backup_csv
from backend.services.access_control import require_document_access, require_kb_access
from backend.services.identity_service import get_current_identity
from backend.services.identity_service import audit_access_denied
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
    inspect_knowledge_base_owner_migration_payload,
    transfer_knowledge_base_owner_payload,
    update_knowledge_base_payload,
)
from core.db.identity import IdentityContext

router = APIRouter()
_output_dir = Path("data/output")
_output_dir.mkdir(parents=True, exist_ok=True)


@router.get("/api/knowledge-bases")
def knowledge_bases(identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        if identity.enforce_access:
            return get_knowledge_bases_payload(identity)
        return get_knowledge_bases_payload()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/knowledge-bases", status_code=201)
def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    kb_id = _make_kb_id(payload.name)
    try:
        if identity.enforce_access:
            return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        return create_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/knowledge-bases/owner-migration/inspect")
def inspect_knowledge_base_owner_migration(
    apply: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    检查知识库所有者迁移状态

    扫描知识库中 ownerUserId 字段为空或缺失的记录，用于数据迁移和修复。
    支持预览模式和执行模式。

    参数:
        apply: 是否执行修复。默认 False（仅预览），True 则实际修复空值
        limit: 扫描记录数上限，默认 500，范围 1-2000
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        dict: 检查结果
            - scanned: 扫描的知识库数量
            - missing_owner: 缺少所有者的知识库数量
            - fixed: 已修复的数量（仅 apply=True 时）
            - items: 问题记录详情列表
            - apply: 是否执行了修复

    使用场景:
        - 系统升级后检查数据完整性
        - 修复历史遗留的无主知识库
        - 数据迁移前的预检

    权限要求:
        - 仅超级管理员（租户管理员）可调用

    请求示例:
        ```bash
        # 预览模式（不修改数据）
        GET /api/knowledge-bases/owner-migration/inspect?apply=false&limit=100

        # 执行修复
        GET /api/knowledge-bases/owner-migration/inspect?apply=true&limit=500
        ```

    响应示例:
        ```json
        {
          "scanned": 100,
          "missing_owner": 5,
          "fixed": 0,
          "apply": false,
          "items": [
            {
              "kb_id": "abc123",
              "kb_name": "测试知识库",
              "created_by": "user001",
              "owner_user_id": null
            }
          ]
        }
        ```

    错误情况:
        - 403: 非超级管理员，权限不足
        - 503: 服务不可用或数据库错误
    """
    try:
        return inspect_knowledge_base_owner_migration_payload(identity, apply=apply, limit=limit)
    except PermissionError as exc:
        audit_access_denied(
            identity,
            action="knowledge_base.owner_migration_inspect",
            resource_type="knowledge_base_owner_migration",
            reason_code="ADMIN_REQUIRED",
            risk_level="medium",
            metadata={"apply": bool(apply), "limit": limit},
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/api/knowledge-bases/{kb_id}")
def update_knowledge_base(
    kb_id: str,
    payload: KnowledgeBaseUpdateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    try:
        if identity.enforce_access:
            return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy, identity)
        return update_knowledge_base_payload(kb_id, payload.name, payload.description, payload.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
        if identity.enforce_access:
            result = delete_knowledge_base_payload(kb_id, identity)
        else:
            result = delete_knowledge_base_payload(kb_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/knowledge-bases/{kb_id}/transfer-owner")
def transfer_knowledge_base_owner(
    kb_id: str,
    payload: KnowledgeBaseTransferOwnerRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
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
    try:
        require_kb_access(kb_id, identity, action="knowledge_base.graph.read", resource_id=kb_id)
        return get_knowledge_base_graph_payload(kb_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents")
def documents(kb_id: str | None = None, identity: IdentityContext = Depends(get_current_identity)) -> list[dict]:
    try:
        if kb_id:
            require_kb_access(kb_id, identity, action="document.list", resource_type="knowledge_base", resource_id=kb_id)
        return get_documents_payload(kb_id, identity if identity.enforce_access else None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}")
def document_detail(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    try:
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
    try:
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
    try:
        require_document_access(document_id, identity, action="document.delete")
        result = delete_document_payload(document_id)
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/export.csv")
def export_document_csv(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> Response:
    try:
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


@router.get("/api/documents/{document_id}/backup.csv")
def export_document_backup_csv_endpoint(document_id: str, identity: IdentityContext = Depends(get_current_identity)) -> Response:
    """
    导出文档备份 CSV（包含向量）

    导出单个文档的完整备份数据，包含切片内容和向量嵌入。
    可用于数据迁移、备份恢复或跨环境复制。

    与普通 CSV 导出（/export.csv）的区别：
    - 包含向量嵌入（embedding 字段）
    - 包含更多元数据字段
    - 采用 wisewe-rag-backup-v1 格式
    - 可通过备份 CSV 导入接口快速恢复

    参数:
        document_id: 文档 ID（路径参数）
        identity: 当前身份上下文（通过依赖注入）

    返回值:
        Response: CSV 文件响应
            - Content-Type: text/csv; charset=utf-8
            - Content-Disposition: attachment; filename="backup_<document_id>_<timestamp>.csv"

    使用场景:
        - 数据备份和归档
        - 跨环境迁移文档
        - 快速恢复已删除的文档
        - 与其他系统集成

    权限要求:
        - 文档访问权限（通过 require_document_access 校验）

    请求示例:
        ```bash
        GET /api/documents/doc_abc123/backup.csv
        Authorization: Bearer <session_token>
        ```

    响应示例:
        ```csv
        chunk_id,document_id,kb_id,content,embedding,page,chunk_index,strategy,created_at
        "chunk_001","doc_abc123","kb_demo","这是切片内容...","[0.1,0.2,...]",1,0,"hierarchical","2026-07-06T10:00:00Z"
        ```

    错误情况:
        - 400: 文档格式错误或无法导出
        - 403: 无权访问该文档
        - 404: 文档不存在
        - 503: 服务不可用
    """
    try:
        require_document_access(document_id, identity, action="document.export.backup_csv")
        actor = identity.user_id if identity.enforce_access else "system"
        filename, content = export_document_backup_csv(document_id, exported_by=actor or "system")
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": build_csv_content_disposition(filename)},
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/documents/{document_id}/source")
def download_document_source(document_id: str, identity: IdentityContext = Depends(get_current_identity)):
    try:
        require_document_access(document_id, identity, action="document.source.download")
        payload = get_document_source_download_payload(document_id)
        if payload["kind"] == "oss":
            return RedirectResponse(payload["url"], status_code=302)
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


def _make_kb_id(name: str) -> str:
    """Generate an opaque public knowledge base ID.

    Names stay editable display metadata; IDs should not expose pinyin, subjects,
    tenants, or business wording in URLs, logs, and OpenAPI calls.
    """
    return _uuid.uuid4().hex[:24]
