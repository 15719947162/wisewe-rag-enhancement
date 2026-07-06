"""
健康检查路由模块

这个模块提供了服务的健康检查接口,用于监控服务是否正常运行。
通常用于负载均衡器、容器编排系统(如 Kubernetes)或监控系统来判断服务状态。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    """
    健康检查接口

    这个接口非常简单,用于快速判断服务是否在线。不需要任何认证或参数。

    返回值:
        dict: 包含状态信息的字典
            - status: 服务状态,值为 "ok" 表示服务正常

    使用场景:
        - 负载均衡器探测后端服务是否存活
        - 容器健康检查
        - 监控系统告警判断

    示例响应:
        {
            "status": "ok"
        }
    """
    return {"status": "ok"}
