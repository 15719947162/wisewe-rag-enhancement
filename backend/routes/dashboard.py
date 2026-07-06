"""
仪表板统计路由模块

这个模块提供仪表板首页所需的统计数据,包括知识库数量、文档数量、切片数量等。
用于前端控制台展示整体系统概览。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.db.connection import get_db_connection

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats() -> dict:
    """
    获取仪表板统计数据

    这个接口返回系统整体的统计数据,包括知识库、文档、切片的总数,
    以及最近处理的任务列表。用于控制台首页展示。

    返回值:
        dict: 统计数据
            - kb_count: 知识库总数
            - doc_count: 文档总数
            - chunk_count: 切片总数(所有文档的切片数之和)
            - recent_tasks: 最近10条任务记录,每个任务包含:
                - id: 任务ID
                - kbId: 所属知识库ID
                - documentName: 文档名称
                - status: 任务状态(默认都是 success)
                - updatedAt: 更新时间

    使用场景:
        - 控制台首页展示系统概览
        - 快速了解系统使用情况
        - 监控最近的文档处理活动

    错误情况:
        - 503: 数据库连接失败或查询出错
    """
    try:
        return _live_stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _live_stats() -> dict:
    """
    内部函数:从数据库获取实时统计数据

    从数据库中查询知识库、文档、切片的统计数据,
    并获取最近处理的10条文档记录。

    返回值:
        dict: 格式化后的统计数据
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 查询知识库总数
            cur.execute("SELECT COUNT(*) FROM knowledge_bases")
            kb_count = cur.fetchone()[0]

            # 查询文档总数
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_count = cur.fetchone()[0]

            # 查询切片总数(对所有文档的切片数求和)
            cur.execute("SELECT COALESCE(SUM(chunk_count), 0) FROM documents")
            chunk_count = cur.fetchone()[0]

            # 查询最近10条文档记录
            cur.execute(
                """
                SELECT id, kb_id, filename, updated_at
                FROM documents
                ORDER BY updated_at DESC
                LIMIT 10
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    # 将数据库记录转换为前端需要的格式
    recent_tasks = [
        {
            "id": str(r[0]),
            "kbId": r[1],
            "documentName": r[2],
            "status": "success",
            "updatedAt": r[3].isoformat() if r[3] else "",
        }
        for r in rows
    ]

    return {
        "kb_count": kb_count,
        "doc_count": doc_count,
        "chunk_count": int(chunk_count),
        "recent_tasks": recent_tasks,
    }
