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
    enabled: bool
    interval_seconds: int
    run_on_startup: bool


_SYNC_TASK: asyncio.Task | None = None
_RUN_LOCK = asyncio.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "startedAt": "",
    "stoppedAt": "",
    "runCount": 0,
    "failureCount": 0,
    "lastRun": None,
}


def load_identity_sync_scheduler_config() -> IdentitySyncSchedulerConfig:
    enabled = bool(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_ENABLED")[0])
    interval_seconds = int(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS")[0] or 300)
    run_on_startup = bool(resolve_runtime_setting("AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP")[0])
    return IdentitySyncSchedulerConfig(
        enabled=enabled,
        interval_seconds=max(60, interval_seconds),
        run_on_startup=run_on_startup,
    )


async def start_identity_sync_scheduler() -> dict[str, Any]:
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
    try:
        await run_identity_sync_once()
    except Exception:
        return


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
