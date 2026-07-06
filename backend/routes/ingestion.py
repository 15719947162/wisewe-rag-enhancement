"""
文档导入路由模块

这个模块提供了文档导入(入库)的核心接口,包括:
- 上传 PDF 文档并开始处理流程
- 查询导入任务状态
- 删除导入任务
- 实时获取任务处理进度(通过 Server-Sent Events)
- 预览、编辑、合并切片草稿
- 确认将切片写入向量库

这是用户将文档导入知识库的主要入口。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.services.access_control import require_chunk_draft_access, require_kb_access, require_task_access
from backend.services.identity_service import get_current_identity
from backend.schemas.requests import ChunkDraftMergeRequest, ChunkDraftUpdateRequest
from backend.services.chunk_draft_service import (
    delete_chunk_draft,
    list_chunk_drafts,
    merge_chunk_drafts,
    update_chunk_draft,
)
from core.db.identity import IdentityContext
from backend.services.ingestion_service import (
    confirm_pipeline,
    create_task,
    delete_ingestion_task,
    get_task,
    reset_task_for_retry,
    run_pipeline_and_confirm,
    run_pipeline_real,
    stream_task_events,
    _task_to_payload,
)

router = APIRouter()

# 文件大小限制:500MB
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB


@router.post("/api/ingestion/upload", status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    kb_id: str = "default",
    strategy: str = "hierarchical",
    subject_type: str = "general",
    layout_type: str = "single_column",
    auto_confirm: bool = False,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    上传 PDF 文档并开始处理

    这是文档导入的入口接口。用户上传 PDF 文件后,系统会:
    1. 上传 PDF 到 OSS
    2. 调用 MinerU 解析 PDF
    3. 清洗内容
    4. 按指定策略切片
    5. (如果 auto_confirm=True) 自动确认并写入向量库

    参数:
        file: 上传的 PDF 文件
        kb_id: 目标知识库 ID,默认 "default"
        strategy: 切片策略,默认 "hierarchical"(三层切片)
                 可选: fixed_length, paragraph, semantic, separator, llm, hierarchical
        subject_type: 文档主题类型(如教材、合同、技术文档等),默认 "general"
                     用于优化清洗和切片策略
        layout_type: 文档布局类型(如单栏、双栏、三栏),默认 "single_column"
                    用于优化解析结果
        auto_confirm: 是否自动确认,默认 False
                     - False: 切片后生成草稿,需要人工确认
                     - True: 切片后自动写入向量库,无需人工确认

    返回值:
        dict: 任务信息
            - task_id: 任务 ID,后续可用来查询状态
            - status: 任务状态,初始为 "pending"
            - filename: 文件名
            - mode: 运行模式("real" 表示真实解析)
            - auto_confirm: 是否自动确认

    使用场景:
        - 用户在控制台上传文档
        - 批量导入文档到知识库
        - OpenAPI 方式导入文档

    错误情况:
        - 422: 文件格式错误(不是 PDF)或文件过大(超过 500MB)
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="只支持 PDF 文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail="文件大小不能超过 500MB")

    require_kb_access(kb_id, identity, action="ingestion.upload", resource_id=kb_id)
    task_id = create_task(
        kb_id,
        file.filename,
        strategy,
        file_bytes=content,
        subject_type=subject_type,
        layout_type=layout_type,
        identity=identity if identity.enforce_access else None,
    )
    if auto_confirm:
        background_tasks.add_task(run_pipeline_and_confirm, task_id)
    else:
        background_tasks.add_task(run_pipeline_real, task_id)

    return {
        "task_id": task_id,
        "status": "pending",
        "filename": file.filename,
        "mode": "real",
        "auto_confirm": auto_confirm,
    }



@router.get("/api/ingestion/tasks/{task_id}")
def get_ingestion_task(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    查询导入任务详情

    通过任务 ID 查询导入任务的详细状态和信息。

    参数:
        task_id: 任务 ID

    返回值:
        dict: 任务详情,包含:
            - taskId: 任务 ID
            - kbId: 所属知识库 ID
            - filename: 文件名
            - status: 任务状态(pending/running/success/error)
            - strategy: 切片策略
            - createdAt: 创建时间
            - updatedAt: 更新时间

    使用场景:
        - 用户在控制台查看导入进度
        - 判断文档处理是否完成
        - 查看错误详情

    错误情况:
        - 404: 任务不存在
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="ingestion_task.read")
    return _task_to_payload(task)


@router.delete("/api/ingestion/tasks/{task_id}")
def delete_ingestion_task_endpoint(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    删除导入任务

    删除指定的导入任务及其相关数据(包括切片草稿)。

    参数:
        task_id: 要删除的任务 ID

    返回值:
        dict: 删除结果
            - deleted: 是否删除成功
            - taskId: 任务 ID

    使用场景:
        - 取消正在处理的导入任务
        - 清理失败的导入任务
        - 删除不需要的导入记录

    错误情况:
        - 404: 任务不存在
        - 409: 任务正在运行,无法删除(需要先停止)
    """
    try:
        if identity.enforce_access:
            task = get_task(task_id)
            require_task_access(task, identity, action="ingestion_task.delete")
        result = delete_ingestion_task(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return result


@router.get("/api/ingestion/stream/{task_id}")
async def stream_ingestion(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> StreamingResponse:
    """
    实时获取任务处理进度(流式推送)

    通过 Server-Sent Events (SSE) 实时推送任务处理进度。
    前端可以通过这个接口实时看到处理步骤和日志。

    参数:
        task_id: 任务 ID

    返回值:
        StreamingResponse: SSE 流,包含实时处理日志

    使用场景:
        - 控制台实时显示处理进度
        - 监控长时间运行的任务
        - 调试导入流程

    错误情况:
        - 404: 任务不存在
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="ingestion_task.stream")

    return StreamingResponse(
        stream_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/ingestion/tasks/{task_id}/retry", status_code=202)
async def retry_ingestion_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    重试失败的导入任务

    当导入任务失败时,可以通过这个接口重新尝试处理。
    系统会重置任务状态并重新运行处理流程。

    参数:
        task_id: 要重试的任务 ID

    返回值:
        dict: 重试结果
            - task_id: 任务 ID
            - status: 任务状态(重置为 pending)
            - retried: 是否已重试
            - mode: 运行模式("real")

    使用场景:
        - 处理临时失败的任务(如网络问题)
        - 修复配置后重新尝试
        - 更新策略后重新处理

    错误情况:
        - 404: 任务不存在
    """
    if identity.enforce_access:
        existing_task = get_task(task_id)
        require_task_access(existing_task, identity, action="ingestion_task.retry")
    task = reset_task_for_retry(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    background_tasks.add_task(run_pipeline_real, task_id)
    return {"task_id": task_id, "status": "pending", "retried": True, "mode": "real"}


@router.get("/api/ingestion/chunks/preview/{task_id}")
def preview_chunk_drafts(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    预览切片草稿

    在任务处理完成后(且未自动确认),可以查看生成的切片草稿。
    用户可以查看、编辑、合并或删除这些草稿后再确认入库。

    参数:
        task_id: 任务 ID

    返回值:
        dict: 草稿预览数据
            - taskId: 任务 ID
            - status: 任务状态
            - items: 切片草稿列表,每个草稿包含:
                - draftId: 草稿 ID
                - content: 切片内容
                - page: 页码
                - strategy: 切片策略
            - count: 草稿总数

    使用场景:
        - 人工审核切片质量
        - 编辑或合并切片
        - 删除不需要的切片

    错误情况:
        - 404: 任务不存在
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="chunk_draft.preview")
    drafts = list_chunk_drafts(task_id)
    return {"taskId": task_id, "status": task.get("status", "pending"), "items": drafts, "count": len(drafts)}


@router.put("/api/ingestion/chunks/{draft_id}")
def edit_chunk_draft(
    draft_id: str,
    payload: ChunkDraftUpdateRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    编辑切片草稿内容

    修改指定切片草稿的内容文本。

    参数:
        draft_id: 草稿 ID
        payload: 更新请求,包含新的内容文本

    返回值:
        dict: 更新后的草稿信息
            - draftId: 草稿 ID
            - content: 更新后的内容
            - updatedAt: 更新时间

    使用场景:
        - 修正切片内容错误
        - 优化切片文本质量
        - 添加或删除内容

    错误情况:
        - 404: 草稿不存在
    """
    require_chunk_draft_access(draft_id, identity, action="chunk_draft.update")
    updated = update_chunk_draft(draft_id, payload.content)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return updated


@router.delete("/api/ingestion/chunks/{draft_id}")
def remove_chunk_draft(draft_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    删除切片草稿

    删除指定的切片草稿,不会入库到向量库。

    参数:
        draft_id: 要删除的草稿 ID

    返回值:
        dict: 删除结果
            - deleted: 是否删除成功
            - draftId: 草稿 ID

    使用场景:
        - 删除质量差的切片
        - 删除重复内容
        - 只保留需要的切片

    错误情况:
        - 404: 草稿不存在
    """
    require_chunk_draft_access(draft_id, identity, action="chunk_draft.delete")
    deleted = delete_chunk_draft(draft_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return {"deleted": True, "draftId": draft_id}


@router.post("/api/ingestion/chunks/merge")
def merge_drafts(
    payload: ChunkDraftMergeRequest,
    identity: IdentityContext = Depends(get_current_identity),
) -> dict:
    """
    合并多个切片草稿

    将多个切片草稿合并成一个新草稿,内容会按顺序拼接。

    参数:
        payload: 合并请求,包含:
            - task_id: 任务 ID
            - draft_ids: 要合并的草稿 ID 列表

    返回值:
        dict: 合并后的新草稿信息
            - draftId: 新草稿 ID
            - content: 合并后的内容
            - mergedFrom: 来源草稿 ID 列表

    使用场景:
        - 合并过短的切片
        - 合含相邻的相关切片
        - 优化切片粒度

    错误情况:
        - 400: 无法合并(如草稿不存在或任务状态不对)
        - 404: 任务不存在
    """
    task = get_task(payload.task_id)
    require_task_access(task, identity, action="chunk_draft.merge")
    for draft_id in payload.draft_ids:
        require_chunk_draft_access(draft_id, identity, action="chunk_draft.merge")
    merged = merge_chunk_drafts(payload.task_id, payload.draft_ids)
    if not merged:
        raise HTTPException(status_code=400, detail="无法合并指定草稿")
    return merged


@router.post("/api/ingestion/chunks/confirm/{task_id}")
async def confirm_chunk_drafts(task_id: str, identity: IdentityContext = Depends(get_current_identity)) -> dict:
    """
    确认切片草稿并入库

    用户审核完切片草稿后,通过这个接口确认入库。
    系统会将草稿转换为正式切片,写入向量库。

    参数:
        task_id: 任务 ID

    返回值:
        dict: 确认结果
            - taskId: 任务 ID
            - status: 任务状态(变为 success)
            - chunksCreated: 创建的切片数量
            - vectorsCreated: 创建的向量数量

    使用场景:
        - 审核完成后的最终确认
        - 人工把关质量的入库流程

    错误情况:
        - 400: 确认失败(如没有草稿或草稿数量为 0)
        - 404: 任务不存在
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    require_task_access(task, identity, action="chunk_draft.confirm")
    try:
        return await confirm_pipeline(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
