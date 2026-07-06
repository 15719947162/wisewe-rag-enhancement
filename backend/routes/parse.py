"""
PDF 解析路由模块

本模块提供 PDF 解析相关的 HTTP API 接口，支持：
1. 摄取任务列表查询 - 获取所有或指定知识库的 PDF 解析任务
2. 解析预览 - 快速预览 PDF 解析结果，无需完整管道处理

解析流程：
---------
1. 用户上传 PDF 文件
2. 系统将 PDF 上传至阿里云 OSS（对象存储服务）
3. 调用 302.ai MinerU 云端 API 提交解析任务
4. 轮询任务状态直至完成
5. 下载并解压解析结果（包含 content_list.json 和图片）
6. 将结果转换为 ContentBlock 列表返回给前端

API 调用示例：
------------
# 获取所有摄取任务
GET /api/ingestion/tasks

# 获取指定知识库的摄取任务
GET /api/ingestion/tasks?kb_id=kb_123456

# 预览 PDF 解析结果
POST /api/parse/preview
Content-Type: application/json
{
    "pdf_path": "data/input/sample.pdf"
}

依赖关系：
---------
- parse_service: 核心解析服务，封装 MinerU 解析逻辑
- ingestion_service: 摄取任务管理服务
- identity_service: 用户身份认证服务
- access_control: 访问控制服务
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.schemas.requests import ParsePreviewRequest
from backend.services.access_control import filter_tasks_by_identity, require_kb_access
from backend.services.identity_service import get_current_identity
from backend.services.ingestion_service import get_all_tasks, _task_to_payload
from core.db.identity import IdentityContext
from backend.services.parse_service import get_parse_preview

# 创建路由器实例，用于注册 PDF 解析相关的 API 端点
router = APIRouter()


@router.get("/api/ingestion/tasks")
def ingestion_tasks(
    kb_id: str | None = None,
    identity: IdentityContext = Depends(get_current_identity),
) -> list[dict]:
    """
    获取 PDF 摄取任务列表

    查询系统中所有的 PDF 解析/摄取任务，支持按知识库 ID 过滤。
    该接口会根据用户身份自动过滤可见的任务，确保用户只能查看
    自己有权限访问的任务。

    参数：
    -----
    kb_id : str | None, 可选
        知识库 ID，用于过滤特定知识库的任务
        - 如果提供，只返回该知识库的任务
        - 如果不提供，返回用户可见的所有任务

    identity : IdentityContext
        当前用户身份上下文，由 FastAPI 依赖注入自动提供
        包含用户 ID、角色、权限等信息

    返回：
    -----
    list[dict]
        任务字典列表，每个任务包含以下字段：
        - task_id: 任务唯一标识
        - kb_id: 所属知识库 ID
        - status: 任务状态（pending/processing/completed/failed）
        - created_at: 创建时间
        - updated_at: 更新时间
        - pdf_path: PDF 文件路径
        - error: 错误信息（如有）

    异常：
    -----
    HTTPException 403: 用户无权访问指定知识库
    HTTPException 401: 用户身份验证失败

    示例：
    -----
    # 获取所有任务
    curl -H "Authorization: Bearer <token>" \\
         http://localhost:8000/api/ingestion/tasks

    # 获取特定知识库的任务
    curl -H "Authorization: Bearer <token>" \\
         "http://localhost:8000/api/ingestion/tasks?kb_id=kb_001"
    """
    # 如果指定了知识库 ID，先检查用户访问权限
    if kb_id:
        require_kb_access(kb_id, identity, action="ingestion_task.list", resource_id=kb_id)

    # 获取所有任务（未经身份过滤）
    tasks = get_all_tasks()

    # 根据用户身份过滤任务，确保用户只能看到有权限的任务
    tasks = filter_tasks_by_identity(tasks, identity)

    # 如果指定了知识库 ID，进一步过滤
    if kb_id:
        tasks = [task for task in tasks if task.get("kb_id") == kb_id]

    # 将内部任务对象转换为 API 响应格式
    return [_task_to_payload(task) for task in tasks]


@router.post("/api/parse/preview")
def parse_preview(payload: ParsePreviewRequest) -> list[dict]:
    """
    预览 PDF 解析结果

    对指定的 PDF 文件执行解析预览，返回解析后的内容块列表。
    该接口用于快速查看 PDF 解析效果，无需执行完整的 RAG 管道
    （清洗、切片、向量化等步骤）。

    与完整管道的区别：
    -----------------
    - 仅执行 PDF 解析，不进行后续处理
    - 速度更快，适合前端实时预览
    - 不消耗清洗/切片/嵌入的 API 配额

    解析流程：
    ---------
    1. 上传 PDF 至阿里云 OSS
    2. 调用 302.ai MinerU 云端 API 解析
    3. 轮询任务状态直至完成
    4. 下载并解压结果
    5. 转换为 ContentBlock 列表

    参数：
    -----
    payload : ParsePreviewRequest
        请求体，包含：
        - pdf_path: PDF 文件路径（相对于项目根目录或绝对路径）

    返回：
    -----
    list[dict]
        内容块列表，每个块包含：
        - type: 块类型（text/table/image/formula）
        - text: 文本内容
        - page_idx: 所在页码（从 0 开始）
        - is_table: 是否为表格（布尔值）
        - table_html: 表格 HTML（仅表格块）
        - image_path: 图片路径（仅图片块）
        - bbox: 边界框坐标 [x0, y0, x1, y1]

    异常：
    -----
    HTTPException 400:
        - PDF 文件不存在
        - PDF 格式不正确
        - MinerU 解析失败
        - OSS 上传失败

    示例：
    -----
    # 请求
    POST /api/parse/preview
    Content-Type: application/json

    {
        "pdf_path": "data/input/sample.pdf"
    }

    # 响应
    [
        {
            "type": "text",
            "text": "第一章 概述\\n这是文档的第一段...",
            "page_idx": 0,
            "is_table": false,
            "bbox": [100, 200, 500, 350]
        },
        {
            "type": "table",
            "text": "表1: 数据对比",
            "page_idx": 1,
            "is_table": true,
            "table_html": "<table><tr><td>...</td></tr></table>",
            "bbox": [100, 100, 450, 300]
        },
        {
            "type": "image",
            "text": "图1: 系统架构图",
            "page_idx": 2,
            "image_path": "data/output/images/fig_001.png",
            "bbox": [50, 150, 400, 400]
        }
    ]
    """
    try:
        # 调用解析服务获取预览结果
        return get_parse_preview(payload.pdf_path)
    except Exception as exc:
        # 捕获所有异常并转换为 HTTP 400 错误
        # 避免暴露内部错误细节，同时提供有意义的错误信息
        raise HTTPException(status_code=400, detail=str(exc)) from exc
