"""
身份增量同步调度器模块

本模块负责定时从 AI 基座同步用户身份数据，主要包括：
1. 配置加载（是否启用、同步间隔、启动时同步）
2. 启动/停止定时同步任务
3. 手动触发一次同步
4. 获取同步状态

工作原理：
- 使用 asyncio.create_task 创建后台任务
- 按配置的间隔时间定期调用同步接口
- 记录同步次数、失败次数、最后同步结果

使用场景：
- 服务启动时自动开始同步（如果配置了 run_on_startup）
- 通过 API 手动触发同步
- 通过 API 查看同步状态
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.services.ai_base_sso_service import format_identity_sync_timestamp
from core.db.identity import get_latest_identity_sync_watermark
from core.runtime_settings import resolve_runtime_setting


@dataclass(frozen=True)
class IdentitySyncSchedulerConfig:
    """
    身份同步调度器配置

    属性：
        enabled: 是否启用定时同步
        interval_seconds: 同步间隔（秒），最小 60 秒
        run_on_startup: 服务启动时是否立即同步一次
    """
    enabled: bool
    interval_seconds: int
    run_on_startup: bool


# ========== 全局状态 ==========

# 当前运行的同步任务
_SYNC_TASK: asyncio.Task | None = None
# 运行锁，防止并发同步
_RUN_LOCK = asyncio.Lock()
# 调度器状态
_STATE: dict[str, Any] = {
    "running": False,       # 是否正在运行
    "startedAt": "",        # 启动时间
    "stoppedAt": "",        # 停止时间
    "runCount": 0,          # 总同步次数
    "failureCount": 0,      # 失败次数
    "lastRun": None,        # 最后一次同步结果
}


def load_identity_sync_scheduler_config() -> IdentitySyncSchedulerConfig:
    """
    加载身份同步调度器配置

    从运行时配置中读取：
    - AI_BASE_IDENTITY_SYNC_ENABLED: 是否启用
    - AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS: 同步间隔
    - AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP: 启动时同步

    返回：
        IdentitySyncSchedulerConfig: 配置对象
    """
    enabled = bool(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_ENABLED")[0])
    interval_seconds = int(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS")[0] or 300)
    run_on_startup = bool(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP")[0])
    return IdentitySyncSchedulerConfig(
        enabled=enabled,
        interval_seconds=max(60, interval_seconds),
        run_on_startup=run_on_startup,
    )


async def start_identity_sync_scheduler() -> dict[str, Any]:
    """
    启动身份同步调度器

    根据配置决定是否启动后台同步任务：
    1. 如果配置未启用，停止现有任务
    2. 如果配置已启用且任务未运行，创建新任务
    3. 如果任务已运行，返回当前状态

    返回：
        dict: 调度器状态信息
    """
    global _SYNC_TASK

    config = load_identity_sync_scheduler_config()
    if not config.enabled:
        if _SYNC_TASK is not None and not _SYNC_TASK.done():
            await stop_identity_sync_scheduler()
        _STATE["running"] = False
        return get_identity_sync_status(config=config)

    if _SYNC_TASK is not None and not _SYNC_TASK.done():
        return get_identity_sync_status(config=config)

    _STATE["running"] = True
    _STATE["startedAt"] = _utc_now()
    _STATE["stoppedAt"] = ""
    _SYNC_TASK = asyncio.create_task(_identity_sync_loop(config))
    return get_identity_sync_status(config=config)


async def stop_identity_sync_scheduler() -> dict[str, Any]:
    """
    停止身份同步调度器

    取消正在运行的后台任务。

    返回：
        dict: 调度器状态信息
    """
    global _SYNC_TASK

    task = _SYNC_TASK
    _SYNC_TASK = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _STATE["running"] = False
    _STATE["stoppedAt"] = _utc_now()
    return get_identity_sync_status()


async def run_identity_sync_once(*, last_sync_at: str | None = None) -> dict[str, Any]:
    """
    手动执行一次身份同步

    立即触发一次同步，不等待定时器。

    参数：
        last_sync_at: 上次同步时间，不传则使用数据库记录的水位线

    返回：
        dict: 同步结果

    说明：
        - 使用锁防止并发同步
        - 当前版本返回"需要 SSO 超级管理员"提示
        - 实际同步逻辑在 ai_base_sso_service.py 中实现
    """
    async with _RUN_LOCK:
        started_at = _utc_now()
        result = {
            "mode": "http_delta",
            "status": "skipped",
            "reasonCode": "SSO_SUPER_ADMIN_REQUIRED",
            "message": "Identity delta sync requires an SSO-authenticated super administrator",
            "lastSyncAt": format_identity_sync_timestamp(last_sync_at),
        }
        _STATE["lastRun"] = {
            "status": "skipped",
            "startedAt": started_at,
            "finishedAt": _utc_now(),
            "errorCode": "SSO_SUPER_ADMIN_REQUIRED",
            "errorMessage": "Identity delta sync requires an SSO-authenticated super administrator",
            "result": result,
        }
        return result


def get_identity_sync_status(config: IdentitySyncSchedulerConfig | None = None) -> dict[str, Any]:
    """
    获取身份同步调度器状态

    返回调度器的当前状态信息，用于监控和诊断。

    参数：
        config: 可选的配置对象，不传则自动加载

    返回：
        dict: 状态信息：
            - enabled: 是否启用
            - running: 是否正在运行
            - intervalSeconds: 同步间隔
            - runOnStartup: 启动时同步
            - startedAt: 启动时间
            - stoppedAt: 停止时间
            - runCount: 总同步次数
            - failureCount: 失败次数
            - latestWatermark: 最新水位线
            - latestWatermarkError: 水位线错误信息
            - lastRun: 最后一次同步结果
    """
    config = config or load_identity_sync_scheduler_config()
    running = bool(_STATE.get("running") and _SYNC_TASK is not None and not _SYNC_TASK.done())
    latest_watermark = ""
    latest_watermark_error = ""
    try:
        latest_watermark = format_identity_sync_timestamp(get_latest_identity_sync_watermark())
    except Exception as exc:
        latest_watermark_error = str(exc)

    return {
        "enabled": config.enabled,
        "running": running,
        "intervalSeconds": config.interval_seconds,
        "runOnStartup": config.run_on_startup,
        "startedAt": _STATE.get("startedAt") or "",
        "stoppedAt": _STATE.get("stoppedAt") or "",
        "runCount": int(_STATE.get("runCount") or 0),
        "failureCount": int(_STATE.get("failureCount") or 0),
        "latestWatermark": latest_watermark,
        "latestWatermarkError": latest_watermark_error,
        "lastRun": _STATE.get("lastRun"),
    }


async def _identity_sync_loop(config: IdentitySyncSchedulerConfig) -> None:
    """
    身份同步循环任务

    后台任务的主循环：
    1. 如果配置了 run_on_startup，先同步一次
    2. 等待 interval_seconds 秒
    3. 执行同步
    4. 重复步骤 2-3

    参数：
        config: 调度器配置
    """
    try:
        if config.run_on_startup:
            await _run_scheduled_sync_once()
        while True:
            await asyncio.sleep(config.interval_seconds)
            await _run_scheduled_sync_once()
    finally:
        _STATE["running"] = False
        _STATE["stoppedAt"] = _utc_now()


async def _run_scheduled_sync_once() -> None:
    """
    执行一次定时同步（内部函数）

    捕获所有异常，防止异常中断定时任务。
    """
    try:
        await run_identity_sync_once()
    except Exception:
        return


def _utc_now() -> str:
    """获取当前 UTC 时间（ISO 格式）"""
    return datetime.now(timezone.utc).isoformat()
