from __future__ import annotations

import asyncio
import csv
import ipaddress
import io
import json
import os
import re
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import AsyncIterator
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from backend.services.chunk_draft_service import (
    clear_chunk_drafts,
    load_confirmable_chunks,
    save_chunk_drafts,
)
from backend.services.task_store import delete_task, load_all_tasks, load_task, save_task
from core.chunker import link_related_chunks
from core.db.identity import IdentityContext
from core.db.knowledge_base import get_knowledge_base
from core.db.query_logs import (
    LlmCallLogRecord,
    ProcessingCostEventRecord,
    append_llm_call_log,
    append_processing_cost_event,
    has_llm_call_log,
    repair_usage_document_id,
)
from core.models.content_block import BlockType, Chunk, ContentBlock
from core.models.relation import Relation
from core.models.triple import Triple
from core.runtime_settings import resolve_runtime_setting

STAGE_KEYS = ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]
UPLOAD_DIR = os.path.join("data", "uploads")
LOG_DIR = os.path.join("data", "logs")
DEFAULT_INGESTION_STRATEGY = "hierarchical"
SOURCE_TYPE_FILE = "file"
SOURCE_TYPE_WEBPAGE = "webpage"
SOURCE_TYPE_BACKUP_CSV = "backup_csv"
SOURCE_TYPES = {SOURCE_TYPE_FILE, SOURCE_TYPE_WEBPAGE, SOURCE_TYPE_BACKUP_CSV}
BACKUP_CSV_SCHEMA_VERSION = "wisewe-rag-backup-v1"
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp"}
OFFICE_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
ALLOWED_FILE_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS | OFFICE_EXTENSIONS
SKIPPED_FAST_IMPORT_STAGES = ["parse", "clean", "chunk", "quality", "embedding"]
INGESTION_BASIC_READY_MODES = {"basic", "basic_ready", "ready_basic"}
STRATEGY_PRIORITY = [
    "hierarchical",
    "llm",
    "paragraph",
    "semantic",
    "separator",
    "fixed_length",
]
STAGE_LABELS = {
    "upload": "文件准备",
    "parse": "云端解析",
    "clean": "内容清洗",
    "chunk": "切片",
    "quality": "质量门控",
    "embedding": "向量化",
    "export": "索引落库",
}


def _new_stage_state() -> dict:
    return {
        "status": "pending",
        "progress": 0,
        "message": "",
        "latency_ms": 0,
        "input_count": 0,
        "output_count": 0,
        "metrics": {},
    }


def _set_task_status(task: dict | None, status: str, done: bool = False, error: str | None = None) -> None:
    if task is None:
        return
    task["status"] = status
    task["done"] = done
    task["error"] = error
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_task(task)


def _persist_uploaded_file(task_id: str, filename: str, file_bytes: bytes) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _, ext = os.path.splitext(filename)
    safe_ext = ext.lower() if ext else ".pdf"
    upload_path = os.path.join(UPLOAD_DIR, f"{task_id}{safe_ext}")
    with open(upload_path, "wb") as file:
        file.write(file_bytes)
    return upload_path


def _file_extension(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


def is_allowed_file_document(filename: str | None) -> bool:
    return _file_extension(filename) in ALLOWED_FILE_EXTENSIONS


def is_backup_csv_filename(filename: str | None) -> bool:
    return _file_extension(filename) == ".csv"


def _normalize_source_type(source_type: str | None) -> str:
    normalized = (source_type or SOURCE_TYPE_FILE).strip().lower()
    return normalized if normalized in SOURCE_TYPES else SOURCE_TYPE_FILE


def create_task(
    kb_id: str,
    filename: str,
    strategy: str,
    file_bytes: bytes | None = None,
    subject_type: str = "general",
    layout_type: str = "single_column",
    identity: IdentityContext | None = None,
    source_type: str = SOURCE_TYPE_FILE,
    source_summary: str | None = None,
    source_options: dict | None = None,
    source_url: str = "",
    fast_import: bool = False,
    skipped_stages: list[str] | None = None,
    api_key_id: str | None = None,
    app_id: str | None = None,
) -> str:
    source_type = _normalize_source_type(source_type)
    strategy = "backup_csv" if source_type == SOURCE_TYPE_BACKUP_CSV else _normalize_strategy(strategy)
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    source_path = _persist_uploaded_file(task_id, filename, file_bytes) if file_bytes else None
    task = {
        "id": task_id,
        "kb_id": kb_id,
        "filename": filename,
        "strategy": strategy,
        "subject_type": subject_type,
        "layout_type": layout_type,
        "source_type": source_type,
        "source_summary": source_summary or filename,
        "source_options": dict(source_options or {}),
        "source_url": source_url,
        "fast_import": bool(fast_import),
        "skipped_stages": list(skipped_stages or []),
        "source_path": source_path,
        "file_bytes": None,
        "status": "pending",
        "current_stage": None,
        "stages": {key: _new_stage_state() for key in STAGE_KEYS},
        "done": False,
        "error": None,
        "awaiting_confirmation": False,
        "chunk_count": 0,
        "blocks_preview": [],
        "chunks_preview": [],
        "removed_reasons": [],
        "quality_breakdown": [],
        "created_at": now,
        "updated_at": now,
    }
    if identity and identity.enforce_access:
        resolved_api_key_id = api_key_id or _api_key_id_from_identity(identity)
        task.update(
            {
                "tenant_id": identity.tenant_id,
                "tenant_name": identity.tenant_name,
                "actor_id": identity.user_id,
                "actor_name": identity.display_name or identity.username or identity.user_id,
                "actor_source": identity.source,
                "api_key_id": resolved_api_key_id,
                "app_id": app_id,
                "usage_target_type": "ingestion_task",
                "created_by": identity.user_id,
            }
        )
    elif api_key_id or app_id:
        task.update({"api_key_id": api_key_id or "", "app_id": app_id or "", "usage_target_type": "ingestion_task"})
    save_task(task)
    return task_id


def _normalize_strategy(strategy: str | None) -> str:
    normalized = (strategy or "").strip().lower()
    if normalized in STRATEGY_PRIORITY:
        return normalized
    return DEFAULT_INGESTION_STRATEGY


def get_task(task_id: str) -> dict | None:
    return load_task(task_id)


def get_all_tasks() -> list[dict]:
    return load_all_tasks()


def _is_path_under(path: str, root: str) -> bool:
    try:
        resolved_path = os.path.realpath(path)
        resolved_root = os.path.realpath(root)
        return os.path.commonpath([resolved_path, resolved_root]) == resolved_root
    except ValueError:
        return False


def _remove_file_if_safe(path: str | None, root: str) -> bool:
    if not path:
        return False
    if not _is_path_under(path, root):
        return False
    if not os.path.isfile(path):
        return False
    os.unlink(path)
    return True


def _build_document_source_metadata(task: dict) -> dict[str, str]:
    provider = str(task.get("parse_provider") or "")
    source_path = str(task.get("source_path") or "")
    filename = str(task.get("filename") or "")
    storage = "oss" if provider in {"mineru", "mineru_official"} else "local"
    source_url = ""

    if storage == "oss" and filename:
        try:
            from core.config import load_config
            from core.parser.oss_uploader import build_oss_object_key

            source_url = build_oss_object_key(load_config(), filename)
        except Exception:
            source_url = ""

    return {
        "source_storage": storage,
        "source_path": source_path,
        "source_url": source_url,
        "parser_provider": provider,
    }


def mark_ingestion_task_failed(task_id: str, reason: str = "", failed_by: str = "console") -> dict | None:
    task = get_task(task_id)
    if not task:
        return None

    current = str(task.get("current_stage") or "")
    task.setdefault("stages", {key: _new_stage_state() for key in STAGE_KEYS})
    for key in STAGE_KEYS:
        task["stages"].setdefault(key, _new_stage_state())
    if current in task["stages"]:
        task["stages"][current]["status"] = "failed"
        task["stages"][current]["message"] = reason or "管理员在任务队列治理中标记为失败"
    task["status"] = "failed"
    task["done"] = True
    task["awaiting_confirmation"] = False
    task["error"] = reason or "Marked failed from task queue governance"
    task["failed_by"] = failed_by
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_task(task)
    return task


def delete_ingestion_task(task_id: str, *, force: bool = False) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None

    status = task.get("status")
    if status in {"pending", "running"} and not task.get("done") and not force:
        raise RuntimeError("任务仍在排队或运行中，当前版本暂不支持强制删除。请等待任务结束后再删除。")

    removed: dict[str, bool | int | str] = {
        "sourceFile": False,
        "logFile": False,
        "chunkDrafts": 0,
        "taskRecord": False,
    }

    try:
        removed["chunkDrafts"] = clear_chunk_drafts(task_id)
    except Exception as exc:
        removed["chunkDraftsError"] = str(exc)

    removed["sourceFile"] = _remove_file_if_safe(task.get("source_path"), UPLOAD_DIR)
    removed["logFile"] = _remove_file_if_safe(os.path.join(LOG_DIR, f"{task_id}.log"), LOG_DIR)
    removed["taskRecord"] = delete_task(task_id)

    return {"deleted": True, "task_id": task_id, "removed": removed}


def reset_task_for_retry(task_id: str) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None

    task["status"] = "pending"
    task["done"] = False
    task["error"] = None
    task["awaiting_confirmation"] = False
    task["current_stage"] = None
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    for key in STAGE_KEYS:
        task["stages"][key] = _new_stage_state()

    save_task(task)
    return task


def _set_stage(
    task: dict | None,
    key: str,
    status: str,
    message: str,
    latency_ms: int = 0,
    input_count: int | None = None,
    output_count: int | None = None,
    metrics: dict | None = None,
    progress: int | None = None,
) -> None:
    if task is None:
        return
    task["current_stage"] = key
    task["stages"][key]["status"] = status
    task["stages"][key]["message"] = message
    task["stages"][key]["latency_ms"] = latency_ms
    if input_count is not None:
        task["stages"][key]["input_count"] = input_count
    if output_count is not None:
        task["stages"][key]["output_count"] = output_count
    if metrics is not None:
        task["stages"][key]["metrics"] = metrics
    if status in ("success", "failed"):
        task["stages"][key]["progress"] = 100
    elif progress is not None:
        task["stages"][key]["progress"] = max(0, min(99, int(progress)))
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_task(task)


def _skip_stage(task: dict | None, key: str, message: str) -> None:
    _set_stage(
        task,
        key,
        "success",
        message,
        input_count=0,
        output_count=0,
        metrics={"skipped": 1},
        progress=100,
    )


class _ParseStageTracker:
    def __init__(self, task_id: str, provider: str) -> None:
        self.task_id = task_id
        self.provider = provider
        self.metrics: dict[str, int | str] = {
            "provider": provider,
            "shardCount": 0,
            "completedShards": 0,
            "pollCount": 0,
        }
        self._completed_shard_ids: set[int] = set()
        self._progress = 2

    def handle(self, message: str) -> None:
        self._observe(message)
        task = get_task(self.task_id)
        if not task:
            return
        _set_stage(
            task,
            "parse",
            "running",
            message,
            input_count=1,
            metrics=dict(self.metrics),
            progress=self._progress,
        )

    def finish_metrics(self, latency_ms: int, output_count: int) -> dict[str, int | str]:
        metrics = dict(self.metrics)
        metrics["parseWallMs"] = latency_ms
        metrics["outputBlocks"] = output_count
        return metrics

    @staticmethod
    def _extract_declared_shard_count(message: str) -> int | None:
        matches: list[int] = []
        for match in re.finditer(r"(\d+)([^\d]{0,20})shards?\b", message, flags=re.IGNORECASE):
            context = match.group(2).lower()
            if "per" in context or "page" in context:
                continue
            matches.append(int(match.group(1)))
        if not matches:
            return None
        return matches[-1]

    def _observe(self, message: str) -> None:
        normalized = message.lower()
        if "pdf" in normalized and ("inspection" in normalized or "inspect" in normalized):
            self._progress = max(self._progress, 5)
        if ("enabled" in normalized or "enable" in normalized) and "shard" in normalized:
            shard_count = self._extract_declared_shard_count(message)
            if shard_count is not None:
                self.metrics["shardCount"] = shard_count
                self._progress = max(self._progress, 12)
        if "single" in normalized and ("parse" in normalized or "task" in normalized):
            self.metrics["shardCount"] = max(int(self.metrics.get("shardCount", 0)), 1)
            self._progress = max(self._progress, 10)
        if "PDF" in message and "体检" in message:
            self._progress = max(self._progress, 5)
        shard_ref = re.search(r"shard\s+(\d+)\s*/\s*(\d+)", message, flags=re.IGNORECASE)
        if shard_ref:
            self.metrics["shardCount"] = max(int(self.metrics.get("shardCount", 0)), int(shard_ref.group(2)))

        if "启用" in message:
            shard_count = self._extract_declared_shard_count(message)
            if shard_count is not None:
                self.metrics["shardCount"] = shard_count
                self._progress = max(self._progress, 12)
        if "未命中" in message:
            self.metrics["shardCount"] = 1
            self._progress = max(self._progress, 10)
        if "提交" in message or "submit" in normalized:
            self._progress = max(self._progress, 20)
        if "job_id=" in message or "task_id=" in message:
            self._progress = max(self._progress, 28)
        if "轮询" in message or "poll" in normalized:
            self.metrics["pollCount"] = int(self.metrics.get("pollCount", 0)) + 1
            self._progress = max(self._progress, 35)
        if "result_url" in message or "result" in normalized or "结果地址" in message:
            self._progress = max(self._progress, 82)
        if "获取并转换" in message or "转换" in message or "convert" in normalized:
            self._progress = max(self._progress, 88)

        shard_done = (
            ("shard" in normalized)
            and (
                "输出" in message
                or "内容块" in message
                or "合并" in message
                or "output" in normalized
                or "block" in normalized
                or "merge" in normalized
                or "complete" in normalized
                or "done" in normalized
            )
        )
        if shard_done:
            if shard_ref:
                self._completed_shard_ids.add(int(shard_ref.group(1)))
                completed = len(self._completed_shard_ids)
            else:
                completed = int(self.metrics.get("completedShards", 0))
                self.metrics["completedShards"] = completed
                return
            self.metrics["completedShards"] = completed
            shard_count = int(self.metrics.get("shardCount", 0))
            if shard_count > 0:
                self._progress = max(self._progress, 30 + int(min(completed, shard_count) / shard_count * 52))
            else:
                self._progress = max(self._progress, 50)

        if "合并完成" in message:
            self._progress = max(self._progress, 90)
        if "完成" in message:
            self._progress = max(self._progress, min(98, self._progress))


def _chunk_progress_from_message(message: str) -> int | None:
    percent_match = re.search(r"\((\d+)%\)", message)
    if "正在扫描内容块" in message and percent_match:
        return 5 + int(int(percent_match.group(1)) * 0.25)
    if "基础切片完成" in message:
        return 35
    if "增强阶段开始" in message:
        return 40
    if "增强阶段跳过" in message:
        return 90
    if "增强进度" in message and percent_match:
        return 40 + int(int(percent_match.group(1)) * 0.5)
    if "切片完成" in message:
        return 92
    return None


def _format_chunk_timings(timings: dict) -> str:
    if not timings:
        return ""
    parts = []
    labels = [
        ("chunkBaseMs", "基础"),
        ("enhanceWallMs", "增强"),
        ("linkRelationsMs", "关系"),
        ("mergeChunksMs", "合并"),
    ]
    for key, label in labels:
        value = timings.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{label} {int(value)}ms")
    return "；".join(parts)


def _normalize_ingestion_ready_mode(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in INGESTION_BASIC_READY_MODES:
        return "basic"
    return "full"


def _resolve_ingestion_ready_mode() -> tuple[str, str]:
    try:
        from core.runtime_settings import resolve_runtime_setting

        value, source = resolve_runtime_setting("INGESTION_READY_MODE")
    except Exception:
        value = os.getenv("INGESTION_READY_MODE", "full")
        source = "env"
    return _normalize_ingestion_ready_mode(value), source


def _task_to_payload(task: dict) -> dict:
    stages = []
    for key in STAGE_KEYS:
        stage = task["stages"][key]
        stages.append(
            {
                "key": key,
                "label": STAGE_LABELS[key],
                "status": stage["status"],
                "progress": stage.get("progress", 0),
                "inputCount": stage.get("input_count", 0),
                "outputCount": stage.get("output_count", 0),
                "latencyMs": stage.get("latency_ms", 0),
                "reason": stage["message"],
                "metrics": stage.get("metrics", {}),
            }
        )
    return {
        "id": task["id"],
        "kbId": task["kb_id"],
        "documentName": task["filename"],
        "status": task["status"],
        "awaitingConfirmation": bool(task.get("awaiting_confirmation")),
        "strategy": task["strategy"],
        "createdAt": task.get("created_at", ""),
        "updatedAt": task.get("updated_at", ""),
        "parseMethod": task.get("parse_provider") or task.get("parse_method") or "mineru",
        "sourceType": task.get("source_type", SOURCE_TYPE_FILE),
        "sourceSummary": task.get("source_summary", task.get("filename", "")),
        "sourceOptions": task.get("source_options", {}),
        "fastImport": bool(task.get("fast_import")),
        "skippedStages": task.get("skipped_stages", []),
        "ingestionReadyMode": task.get("ingestion_ready_mode", "full"),
        "chunkCount": task.get("chunk_count", 0),
        "stages": stages,
        "blocks": task.get("blocks_preview", []),
        "chunks": task.get("chunks_preview", []),
        "removedReasons": task.get("removed_reasons", []),
        "qualityBreakdown": task.get("quality_breakdown", []),
        "chunkTimings": task.get("chunk_timings", {}),
    }


def _record_ingestion_llm_usage(
    *,
    task_id: str,
    task: dict,
    stage: str,
    feature_name: str,
    metrics: dict,
    token_prefix: str,
    latency_ms: int,
    provider: str,
    model_name: str,
    status: str = "success",
    error_code: str | None = None,
) -> None:
    prompt_tokens = int(metrics.get(f"{token_prefix}PromptTokens", 0) or 0)
    completion_tokens = int(metrics.get(f"{token_prefix}CompletionTokens", 0) or 0)
    total_tokens = int(metrics.get(f"{token_prefix}TotalTokens", 0) or 0)
    if total_tokens <= 0 and prompt_tokens + completion_tokens <= 0:
        return
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    append_llm_call_log(
        LlmCallLogRecord(
            request_id=task_id,
            pipeline_domain="ingestion",
            pipeline_stage=stage,
            feature_name=feature_name,
            provider=provider or "unknown",
            model_name=model_name or "unknown",
            kb_id=str(task.get("kb_id") or ""),
            identity=_task_identity_context(task),
            api_key_id=_task_api_key_id(task),
            app_id=_task_app_id(task),
            document_id=str(task.get("document_id") or ""),
            usage_target_type="ingestion_task",
            event_type="embedding" if stage == "embedding" else "llm_enhancement",
            usage_source="backfill" if bool(metrics.get("backfilled")) else "runtime",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=max(0, int(latency_ms or 0)),
            status=status,
            error_code=error_code,
        )
    )


def _record_processing_cost_event(
    *,
    task_id: str,
    task: dict,
    stage: str,
    event_type: str,
    feature_name: str,
    metric_value: int | float | None = None,
    metric_unit: str | None = None,
    duration_ms: int = 0,
    provider: str | None = None,
    model_name: str | None = None,
    external_job_id: str | None = None,
    status: str = "success",
    error_code: str | None = None,
    collection_status: str = "recorded",
    metadata: dict | None = None,
    usage_source: str = "runtime",
) -> None:
    append_processing_cost_event(
        ProcessingCostEventRecord(
            event_type=event_type,
            pipeline_domain="ingestion",
            pipeline_stage=stage,
            feature_name=feature_name,
            task_id=task_id,
            request_id=task_id,
            usage_target_type="ingestion_task",
            document_id=str(task.get("document_id") or ""),
            kb_id=str(task.get("kb_id") or ""),
            identity=_task_identity_context(task),
            api_key_id=_task_api_key_id(task),
            app_id=_task_app_id(task),
            provider=provider or None,
            model_name=model_name or None,
            external_job_id=external_job_id,
            metric_value=metric_value,
            metric_unit=metric_unit,
            duration_ms=max(0, int(duration_ms or 0)),
            status=status,
            error_code=error_code,
            cost_source="not_available",
            usage_source=usage_source,
            collection_status=collection_status,
            metadata=metadata or {},
        )
    )


def _record_oss_upload_cost_event(
    *,
    task_id: str,
    task: dict,
    source_path: str | None,
    parse_metrics: dict | None = None,
) -> None:
    parser_provider = str(task.get("parse_provider") or "")
    if parser_provider not in {"mineru", "mineru_official"}:
        return
    if not source_path or not os.path.exists(source_path):
        return
    size_bytes = os.path.getsize(source_path)
    metrics = parse_metrics if isinstance(parse_metrics, dict) else {}
    try:
        request_count = max(1, int(metrics.get("shardCount") or metrics.get("parseShardCount") or 1))
    except (TypeError, ValueError):
        request_count = 1
    _record_processing_cost_event(
        task_id=task_id,
        task=task,
        stage="upload",
        event_type="oss_upload",
        feature_name="云解析 OSS 中转",
        metric_value=size_bytes,
        metric_unit="bytes",
        # The cloud parser owns the actual OSS transfer; parsing latency must not be reused as upload latency.
        duration_ms=0,
        provider="oss",
        metadata={
            "filename": task.get("filename", ""),
            "parserProvider": parser_provider,
            "bytes": size_bytes,
            "putCount": request_count,
            "getCount": request_count,
            "shardCount": request_count,
            "usage": "parser_source_transfer",
            "durationCaptured": False,
        },
    )


def _parse_page_count(blocks: list[ContentBlock], metrics: dict | None = None) -> int:
    metric_source = metrics if isinstance(metrics, dict) else {}
    for key in ("pageCount", "page_count", "parseServiceInputPages", "inputPages", "totalPages"):
        try:
            value = int(metric_source.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    pages = [int(block.page_idx) for block in blocks if getattr(block, "page_idx", None) is not None]
    return max(pages) + 1 if pages else 0


def backfill_ingestion_llm_usage(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {"taskId": task_id, "backfilled": False, "reason": "task_not_found"}
    if has_llm_call_log(task_id, "chunk"):
        return {"taskId": task_id, "backfilled": False, "reason": "already_recorded"}

    metrics = _chunk_metrics_from_task_or_log(task)
    total_tokens = int(metrics.get("enhanceTotalTokens", 0) or 0)
    if total_tokens <= 0:
        return {"taskId": task_id, "backfilled": False, "reason": "missing_chunk_tokens"}

    latency_ms = int(metrics.get("enhanceWallMs", 0) or 0)
    metrics["backfilled"] = 1
    _record_ingestion_llm_usage(
        task_id=task_id,
        task=task,
        stage="chunk",
        feature_name="三层切片增强",
        metrics=metrics,
        token_prefix="enhance",
        latency_ms=latency_ms,
        provider=_runtime_str("LLM_BASE_URL", "openai-compatible"),
        model_name=_runtime_str("LLM_CLEANER_MODEL", "qwen-plus"),
    )
    return {
        "taskId": task_id,
        "backfilled": True,
        "pipelineStage": "chunk",
        "totalTokens": total_tokens,
        "latencyMs": latency_ms,
    }


def _chunk_metrics_from_task_or_log(task: dict) -> dict[str, int]:
    stage_metrics = ((task.get("stages") or {}).get("chunk") or {}).get("metrics")
    metrics = dict(stage_metrics) if isinstance(stage_metrics, dict) else {}
    if not metrics and isinstance(task.get("chunk_timings"), dict):
        metrics.update(task.get("chunk_timings") or {})

    normalized: dict[str, int] = {}
    for key, value in metrics.items():
        try:
            normalized[str(key)] = int(value or 0)
        except (TypeError, ValueError):
            continue

    if int(normalized.get("enhanceTotalTokens", 0) or 0) <= 0:
        log_metrics = _parse_chunk_metrics_from_log(str(task.get("id") or ""))
        normalized.update({key: value for key, value in log_metrics.items() if value > 0})
    return normalized


def _parse_chunk_metrics_from_log(task_id: str) -> dict[str, int]:
    if not task_id:
        return {}
    log_path = os.path.join(LOG_DIR, f"{task_id}.log")
    if not os.path.isfile(log_path):
        return {}
    metrics: dict[str, int] = {}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                if "[chunk]" not in line or "LLM tokens=" not in line:
                    continue
                token_match = re.search(r"LLM tokens=(\d+)", line)
                wall_match = re.search(r"(?:enhanceWallMs|增强墙钟耗时)=(\d+)ms", line)
                if token_match:
                    metrics["enhanceTotalTokens"] = int(token_match.group(1))
                if wall_match:
                    metrics["enhanceWallMs"] = int(wall_match.group(1))
    except OSError:
        return {}
    return metrics


def _task_identity_context(task: dict) -> IdentityContext | None:
    tenant_id = str(task.get("tenant_id") or "")
    tenant_name = str(task.get("tenant_name") or "")
    user_id = str(task.get("actor_id") or task.get("created_by") or "")
    display_name = str(task.get("actor_name") or "")
    source = str(task.get("actor_source") or "ingestion_task")

    if not tenant_id or not user_id:
        try:
            kb = get_knowledge_base(str(task.get("kb_id") or ""))
        except Exception:
            kb = None
        if kb:
            tenant_id = tenant_id or str(kb.get("tenant_id") or "")
            user_id = user_id or str(kb.get("owner_user_id") or kb.get("created_by") or "")

    if not tenant_id or not user_id:
        return None
    return IdentityContext(
        tenant_id=tenant_id,
        user_id=user_id,
        username=user_id,
        display_name=display_name or user_id,
        tenant_name=tenant_name,
        is_authenticated=True,
        source=source,
    )


def _api_key_id_from_identity(identity: IdentityContext | None) -> str | None:
    if not identity or identity.source != "api_key":
        return None
    value = identity.username or identity.user_id or ""
    if value.startswith("api_key:"):
        return value.split(":", 1)[1]
    return value or None


def _task_api_key_id(task: dict) -> str | None:
    value = str(task.get("api_key_id") or "")
    if value:
        return value
    nested = task.get("openapi") if isinstance(task.get("openapi"), dict) else {}
    value = str((nested or {}).get("api_key_id") or "")
    return value or None


def _task_app_id(task: dict) -> str | None:
    value = str(task.get("app_id") or "")
    if value:
        return value
    nested = task.get("openapi") if isinstance(task.get("openapi"), dict) else {}
    value = str((nested or {}).get("app_id") or "")
    return value or None


def _runtime_str(key: str, fallback: str = "") -> str:
    try:
        value, _source = resolve_runtime_setting(key)
    except Exception:
        value = os.getenv(key, fallback)
    return str(value or fallback)


def _runtime_bool(key: str, fallback: bool = False) -> bool:
    try:
        value, _source = resolve_runtime_setting(key)
    except Exception:
        value = os.getenv(key, "true" if fallback else "false")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_int(key: str, fallback: int = 0) -> int:
    try:
        value, _source = resolve_runtime_setting(key)
    except Exception:
        value = os.getenv(key, str(fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _cleaner_runtime_options() -> dict:
    return {
        "use_llm": _runtime_bool("LLM_CLEANER_ENABLED", False),
        "llm_system_prompt": _runtime_str("LLM_CLEANER_SYSTEM_PROMPT"),
        "llm_model": _runtime_str("LLM_CLEANER_MODEL", "qwen-plus"),
        "llm_base_url": _runtime_str("LLM_CLEANER_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
        "llm_api_key": _runtime_str("LLM_CLEANER_API_KEY", _runtime_str("LLM_API_KEY", "")),
    }


def _quality_runtime_options() -> dict:
    llm_enabled = _runtime_bool("LLM_QUALITY_GATE_ENABLED", False)
    return {
        "min_score": _runtime_int("LLM_QUALITY_GATE_MIN_SCORE", 3) if llm_enabled else 0,
        "llm_base_url": _runtime_str("LLM_QUALITY_GATE_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
        "llm_api_key": _runtime_str("LLM_QUALITY_GATE_API_KEY", _runtime_str("LLM_API_KEY", "")),
        "llm_api_key_pool": _runtime_str("LLM_QUALITY_GATE_API_KEY_POOL", _runtime_str("LLM_API_KEY_POOL", "")),
        "llm_model": _runtime_str("LLM_QUALITY_GATE_MODEL", "qwen-plus"),
        "llm_system_prompt": _runtime_str("LLM_QUALITY_GATE_SYSTEM_PROMPT"),
        "llm_key_retries": _runtime_int("LLM_QUALITY_GATE_KEY_RETRIES", 1),
        "llm_key_cooldown_seconds": _runtime_int("LLM_QUALITY_GATE_KEY_COOLDOWN_SECONDS", 30),
        "llm_batch_size": _runtime_int("LLM_QUALITY_GATE_BATCH_SIZE", 10),
        "llm_max_concurrency": _runtime_int("LLM_QUALITY_GATE_MAX_CONCURRENCY", 8),
    }


def _load_task_or_raise(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found")
    return task


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "br"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        self.text_parts.append(text)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self.text_parts)).strip()


def _is_public_http_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "仅允许 http / https 链接"
    if not parsed.hostname:
        return False, "URL 缺少可解析域名"
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return False, "不允许采集 localhost 或回环地址"
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 80, type=socket.SOCK_STREAM)}
    except OSError:
        return False, "域名无法解析"
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False, "域名解析结果不是有效 IP"
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False, "不允许采集内网、保留或链路本地地址"
    return True, ""


def _safe_fetch_html(url: str, *, max_bytes: int, timeout: int) -> tuple[str, str]:
    ok, reason = _is_public_http_url(url)
    if not ok:
        raise ValueError(reason)
    request = Request(url, headers={"User-Agent": "WiseWe-RAG-Ingestion/1.0"})
    try:
        response_ctx = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError(
                f"目标网站禁止爬虫爬取内容（HTTP {exc.code}）。该页面可能要求登录、Cookie、浏览器环境或反爬校验；请改用允许采集的网页，或先保存为 PDF/HTML 文件后通过文件入口入库。"
            ) from exc
        raise ValueError(f"网页请求失败（HTTP {exc.code}: {exc.reason}）") from exc
    with response_ctx as response:
        content_type = str(response.headers.get("content-type") or "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise ValueError(f"不支持的网页内容类型：{content_type or 'unknown'}")
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("页面内容超过单页大小上限")
    encoding = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    if match:
        encoding = match.group(1)
    return raw.decode(encoding, errors="replace"), content_type


def _matches_patterns(url: str, include_patterns: list[str], exclude_patterns: list[str]) -> bool:
    if include_patterns and not any(pattern and pattern in url for pattern in include_patterns):
        return False
    if exclude_patterns and any(pattern and pattern in url for pattern in exclude_patterns):
        return False
    return True


def _collect_webpage_blocks(options: dict, filename: str) -> tuple[list[ContentBlock], dict[str, int | str]]:
    start_url = str(options.get("url") or options.get("start_url") or "").strip()
    if not start_url:
        raise ValueError("网页采集缺少起始 URL")
    max_depth = max(0, min(int(options.get("max_depth", 1) or 1), 2))
    max_pages = max(1, min(int(options.get("max_pages", 1) or 1), 50))
    same_domain_only = bool(options.get("same_domain_only", True))
    include_patterns = [str(item).strip() for item in options.get("include_patterns") or [] if str(item).strip()]
    exclude_patterns = [str(item).strip() for item in options.get("exclude_patterns") or [] if str(item).strip()]
    max_bytes = max(64 * 1024, min(int(options.get("max_page_bytes", 2 * 1024 * 1024) or 0), 5 * 1024 * 1024))
    timeout = max(3, min(int(options.get("timeout_seconds", 12) or 12), 30))
    start_host = urlparse(start_url).hostname or ""

    queue: list[tuple[str, int]] = [(start_url, 0)]
    seen: set[str] = set()
    blocks: list[ContentBlock] = []
    while queue and len(blocks) < max_pages:
        url, depth = queue.pop(0)
        normalized_url = url.split("#", 1)[0]
        if normalized_url in seen or not _matches_patterns(normalized_url, include_patterns, exclude_patterns):
            continue
        seen.add(normalized_url)
        html, _content_type = _safe_fetch_html(normalized_url, max_bytes=max_bytes, timeout=timeout)
        parser = _HtmlTextExtractor()
        parser.feed(html)
        text = parser.text
        if text:
            title = parser.title or normalized_url
            blocks.append(
                ContentBlock(
                    type=BlockType.TEXT,
                    text=f"{title}\n\n{text}",
                    page_idx=len(blocks),
                    source_file=normalized_url,
                )
            )
        if depth >= max_depth:
            continue
        for href in parser.links:
            next_url = urljoin(normalized_url, href).split("#", 1)[0]
            parsed_next = urlparse(next_url)
            if parsed_next.scheme not in {"http", "https"}:
                continue
            if same_domain_only and parsed_next.hostname != start_host:
                continue
            if next_url not in seen:
                queue.append((next_url, depth + 1))

    if not blocks:
        raise ValueError("网页采集未提取到可入库正文")
    return blocks, {"provider": "webpage", "pageCount": len(blocks), "maxDepth": max_depth, "maxPages": max_pages}


def _strip_xml_text(raw_xml: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_xml)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return " ".join(text.split())


def _extract_office_text(path: str, ext: str) -> str:
    if ext in {".docx", ".pptx", ".xlsx"}:
        try:
            with ZipFile(path) as archive:
                parts = [
                    name
                    for name in archive.namelist()
                    if name.endswith(".xml")
                    and (
                        name.startswith("word/")
                        or name.startswith("ppt/slides/")
                        or name.startswith("xl/worksheets/")
                        or name.startswith("xl/sharedStrings")
                    )
                ]
                texts = [_strip_xml_text(archive.read(name).decode("utf-8", errors="replace")) for name in parts]
        except BadZipFile as exc:
            raise ValueError("Office 文件不是有效的 OpenXML 包") from exc
        return "\n".join(text for text in texts if text)

    raw = Path(path).read_bytes()
    decoded = raw.decode("utf-8", errors="ignore") or raw.decode("gb18030", errors="ignore")
    words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。！？；：、（）《》“”\"'.,;:!?()\[\]\-_/]{2,}", decoded)
    return " ".join(words)


def _adapt_file_blocks(path: str, filename: str) -> tuple[list[ContentBlock], dict[str, int | str]]:
    ext = _file_extension(filename)
    if ext in IMAGE_EXTENSIONS:
        return [
            ContentBlock(
                type=BlockType.IMAGE,
                text=f"图片文件：{filename}",
                page_idx=0,
                source_file=filename,
                image_path=path,
            )
        ], {"provider": "image_adapter", "blockCount": 1}
    if ext in OFFICE_EXTENSIONS:
        text = _extract_office_text(path, ext).strip()
        if not text:
            raise ValueError("Office 文件未提取到可入库文本")
        return [
            ContentBlock(
                type=BlockType.TEXT,
                text=text,
                page_idx=0,
                source_file=filename,
            )
        ], {"provider": "office_adapter", "blockCount": 1}
    raise ValueError(f"暂不支持的文件类型：{ext or 'unknown'}")


def _adapt_task_source_to_blocks(task: dict) -> tuple[list[ContentBlock], str, dict[str, int | str]]:
    source_type = _normalize_source_type(task.get("source_type"))
    if source_type == SOURCE_TYPE_WEBPAGE:
        blocks, metrics = _collect_webpage_blocks(task.get("source_options") or {}, task.get("filename") or "webpage")
        return blocks, "webpage", metrics
    source_path = str(task.get("source_path") or "")
    if not source_path or not os.path.exists(source_path):
        raise ValueError("源文件不存在，无法执行来源适配")
    blocks, metrics = _adapt_file_blocks(source_path, task.get("filename") or source_path)
    return blocks, str(metrics.get("provider") or "source_adapter"), metrics


def _parse_json_list(value: str, field_name: str) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 不是有效 JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} 必须是 JSON 数组")
    return parsed


def _parse_embedding(value: str) -> list[float]:
    embedding = _parse_json_list(value, "embeddingJson")
    if not embedding:
        raise ValueError("系统备份 CSV 缺少 embedding 向量")
    try:
        return [float(item) for item in embedding]
    except (TypeError, ValueError) as exc:
        raise ValueError("embeddingJson 包含非数值元素") from exc


def _chunk_from_backup_row(row: dict[str, str]) -> tuple[Chunk, list[float]]:
    embedding = _parse_embedding(row.get("embeddingJson", ""))
    dimension = int(row.get("embeddingDimension") or len(embedding))
    if dimension != len(embedding):
        raise ValueError("embeddingDimension 与 embeddingJson 长度不匹配")
    relations = []
    for item in _parse_json_list(row.get("relationsJson", "[]"), "relationsJson"):
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("targetId") or item.get("target_id") or "").strip()
        rel_type = str(item.get("relType") or item.get("rel_type") or "refers_to").strip()
        source = str(item.get("source") or "rule").strip()
        if target_id:
            try:
                relations.append(
                    Relation(
                        target_id=target_id,
                        rel_type=rel_type,  # type: ignore[arg-type]
                        weight=float(item.get("weight", 1.0) or 1.0),
                        source=source,  # type: ignore[arg-type]
                        evidence=str(item.get("evidence") or ""),
                    )
                )
            except Exception:
                continue
    triples = []
    for item in _parse_json_list(row.get("triplesJson", "[]"), "triplesJson"):
        if isinstance(item, dict) and item.get("s") and item.get("p") and item.get("o"):
            triples.append(
                Triple(
                    s=str(item.get("s")),
                    p=str(item.get("p")),
                    o=str(item.get("o")),
                    confidence=float(item.get("confidence", 0.7) or 0.7),
                )
            )
    chunk = Chunk(
        id=str(row.get("chunkId") or ""),
        content=str(row.get("content") or ""),
        source=str(row.get("source") or row.get("filename") or ""),
        page=max(int(row.get("page") or 1) - 1, 0),
        chunk_index=int(row.get("chunkIndex") or 0),
        strategy=str(row.get("strategy") or DEFAULT_INGESTION_STRATEGY),
        title=str(row.get("title") or "") or None,
        layer=str(row.get("layer") or "child"),
        parent_id=str(row.get("parentId") or "") or None,
        char_count=int(row.get("charCount") or len(str(row.get("content") or ""))),
        is_table_chunk=str(row.get("isTableChunk") or "").lower() in {"1", "true", "yes"},
        is_image_chunk=str(row.get("isImageChunk") or "").lower() in {"1", "true", "yes"},
        image_path=str(row.get("imagePath") or "") or None,
        relations=relations,
        extracted_triples=triples,
    )
    return chunk, embedding


def _import_backup_csv_to_pgvector(task: dict, csv_bytes: bytes) -> dict[str, int | str]:
    from core.db.connection import get_db_connection
    from core.db.init_db import ensure_db_schema
    from core.output.pgvector_writer import (
        build_chunk_search_text,
        write_chunk_relations_batch,
        write_chunks_batch,
        write_kg_triples_batch,
    )

    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError("系统备份 CSV 为空")
    if rows[0].get("schemaVersion") != BACKUP_CSV_SCHEMA_VERSION:
        raise ValueError("不是本系统生成的可恢复备份 CSV")

    chunks: list[Chunk] = []
    embeddings: list[list[float]] = []
    for row in rows:
        if row.get("schemaVersion") != BACKUP_CSV_SCHEMA_VERSION:
            raise ValueError("备份 CSV 存在不兼容 schemaVersion")
        chunk, embedding = _chunk_from_backup_row(row)
        chunks.append(chunk)
        embeddings.append(embedding)

    first = rows[0]
    kb_id = str(task.get("kb_id") or first.get("kbId") or "")
    document_id = str(first.get("documentId") or uuid.uuid4())
    filename = str(first.get("filename") or task.get("filename") or "restored-document")
    file_hash = str(first.get("fileHash") or f"backup-{document_id}")
    file_size_bytes = len(csv_bytes)
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents(
                    id, kb_id, filename, file_hash, file_size_bytes, chunk_count,
                    source_storage, source_path, source_url, parser_provider
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(kb_id, file_hash) DO UPDATE
                    SET filename=EXCLUDED.filename,
                        file_size_bytes=EXCLUDED.file_size_bytes,
                        chunk_count=EXCLUDED.chunk_count,
                        source_storage=EXCLUDED.source_storage,
                        source_path=EXCLUDED.source_path,
                        source_url=EXCLUDED.source_url,
                        parser_provider=EXCLUDED.parser_provider,
                        updated_at=NOW()
                RETURNING id
                """,
                (
                    document_id,
                    kb_id,
                    filename,
                    file_hash,
                    file_size_bytes,
                    len(chunks),
                    "backup_csv",
                    task.get("source_path") or "",
                    "",
                    "backup_csv",
                ),
            )
            document_id = str(cur.fetchone()[0])
            cur.execute("SELECT id::text FROM chunks WHERE document_id::text = %s", (document_id,))
            existing_chunk_ids = [row[0] for row in cur.fetchall()]
            if existing_chunk_ids:
                cur.execute(
                    "DELETE FROM chunk_relations WHERE src_id::text = ANY(%s) OR dst_id::text = ANY(%s)",
                    (existing_chunk_ids, existing_chunk_ids),
                )
                cur.execute("DELETE FROM kg_triples WHERE source_chunk::text = ANY(%s)", (existing_chunk_ids,))
                cur.execute("DELETE FROM entity_mentions WHERE chunk_id::text = ANY(%s)", (existing_chunk_ids,))
                cur.execute("DELETE FROM chunks WHERE document_id::text = %s", (document_id,))
        chunk_rows = write_chunks_batch(conn, chunks, embeddings, kb_id, document_id)
        relation_rows = write_chunk_relations_batch(conn, chunks, kb_id)
        triple_rows = write_kg_triples_batch(conn, chunks, kb_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "documentId": document_id,
        "chunkRows": chunk_rows,
        "relationRows": relation_rows,
        "tripleRows": triple_rows,
        "embeddingDimension": len(embeddings[0]) if embeddings else 0,
        "searchTextBytes": sum(len(build_chunk_search_text(chunk)) for chunk in chunks),
    }


async def _run_backup_csv_import_task(task_id: str, logger) -> None:
    task = _load_task_or_raise(task_id)
    source_path = str(task.get("source_path") or "")
    if not source_path or not os.path.exists(source_path):
        raise ValueError("备份 CSV 文件不存在")
    _set_stage(task, "upload", "success", "系统备份 CSV 已接收", input_count=1, output_count=1)
    _record_processing_cost_event(
        task_id=task_id,
        task=task,
        stage="upload",
        event_type="file_upload",
        feature_name="系统备份 CSV 接收",
        metric_value=os.path.getsize(source_path) if os.path.exists(source_path) else None,
        metric_unit="bytes",
        metadata={"filename": task.get("filename", ""), "sourceType": SOURCE_TYPE_BACKUP_CSV},
    )
    for key in SKIPPED_FAST_IMPORT_STAGES:
        task = _load_task_or_raise(task_id)
        _skip_stage(task, key, "系统备份 CSV 快速导入已跳过该阶段")
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage=key,
            event_type="stage_skipped",
            feature_name=f"{STAGE_LABELS.get(key, key)}跳过",
            metric_value=0,
            metric_unit="count",
            collection_status="skipped",
            metadata={"reason": "backup_csv_fast_import"},
        )
    task = _load_task_or_raise(task_id)
    _set_stage(task, "export", "running", "正在恢复系统备份 CSV 数据...")
    started = time.monotonic()
    csv_bytes = Path(source_path).read_bytes()
    metrics = await asyncio.to_thread(_import_backup_csv_to_pgvector, task, csv_bytes)
    task = _load_task_or_raise(task_id)
    written = int(metrics.get("chunkRows", 0) or 0)
    task["document_id"] = str(metrics.get("documentId") or "")
    if task["document_id"]:
        repair_usage_document_id(task_id, document_id=task["document_id"], kb_id=str(task.get("kb_id") or ""))
    _set_stage(
        task,
        "export",
        "success",
        f"备份恢复完成，共写入 {written} 个切片",
        latency_ms=int((time.monotonic() - started) * 1000),
        input_count=1,
        output_count=written,
        metrics=metrics,
    )
    _record_processing_cost_event(
        task_id=task_id,
        task=task,
        stage="export",
        event_type="storage_export",
        feature_name="系统备份 CSV 恢复写库",
        metric_value=written,
        metric_unit="rows",
        duration_ms=int((time.monotonic() - started) * 1000),
        provider="pgvector",
        metadata=metrics,
    )
    task["chunk_count"] = written
    task["parse_provider"] = "backup_csv"
    task["skipped_stages"] = SKIPPED_FAST_IMPORT_STAGES
    logger.info("[backup_csv] Restored %d chunks", written)
    _set_task_status(task, "success", done=True)


async def run_pipeline_real(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return

    from core.logging import close_task_logger, get_task_logger
    from core.llm_config import set_global_llm_config

    logger = get_task_logger(task_id)
    logger.info(
        "Pipeline started: file=%s strategy=%s subject=%s layout=%s",
        task.get("filename"),
        task.get("strategy"),
        task.get("subject_type", "general"),
        task.get("layout_type", "single_column"),
    )

    parse_tracker: _ParseStageTracker | None = None

    def stage_log(message: str) -> None:
        logger.info(message)
        if parse_tracker is not None:
            parse_tracker.handle(message)

    set_global_llm_config(
        subject_type=task.get("subject_type", "general"),
        layout_type=task.get("layout_type", "single_column"),
    )

    _set_task_status(task, "running")

    tmp_path: str | None = None
    parse_input_path: str | None = None

    try:
        if _normalize_source_type(task.get("source_type")) == SOURCE_TYPE_BACKUP_CSV:
            await _run_backup_csv_import_task(task_id, logger)
            return

        source_path = task.get("source_path")
        file_bytes: bytes | None = task.get("file_bytes")
        is_pdf_source = _file_extension(task.get("filename")) in PDF_EXTENSIONS

        if source_path and os.path.exists(source_path):
            parse_input_path = source_path
        elif file_bytes:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            parse_input_path = tmp_path
        elif _normalize_source_type(task.get("source_type")) != SOURCE_TYPE_WEBPAGE:
            raise ValueError("No source file available for real pipeline")

        output_dir = "data/output"
        os.makedirs(output_dir, exist_ok=True)

        task = get_task(task_id) or {}
        _set_stage(task, "upload", "running", "正在准备解析源文件...")
        logger.info("[upload] Source file preparation started")
        upload_start = time.monotonic()
        task = get_task(task_id) or {}
        _set_stage(
            task,
            "upload",
            "success",
            "源文件准备完成，将进入来源适配或云解析",
            latency_ms=int((time.monotonic() - upload_start) * 1000),
            input_count=1,
            output_count=1,
        )
        logger.info("[upload] Source file ready in %dms", int((time.monotonic() - upload_start) * 1000))
        task = get_task(task_id) or {}
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="upload",
            event_type="file_upload",
            feature_name="源文件接收",
            metric_value=os.path.getsize(parse_input_path) if parse_input_path and os.path.exists(parse_input_path) else None,
            metric_unit="bytes",
            duration_ms=int((time.monotonic() - upload_start) * 1000),
            metadata={
                "filename": task.get("filename", ""),
                "sourceType": task.get("source_type", SOURCE_TYPE_FILE),
                "sourcePathStored": bool(task.get("source_path")),
            },
        )

        task = get_task(task_id) or {}
        parser_provider = ""
        parse_start = time.monotonic()
        if is_pdf_source:
            from core.parser.provider import get_pdf_parser_provider, parse_pdf

            parser_provider = get_pdf_parser_provider()
            task["parse_provider"] = parser_provider
            save_task(task)
            _set_stage(task, "parse", "running", f"正在调用 {parser_provider} 文档解析 provider...")
            logger.info("[parse] Provider=%s parse started", parser_provider)
            parse_tracker = _ParseStageTracker(task_id, parser_provider)
            blocks = await asyncio.to_thread(
                parse_pdf,
                parse_input_path,
                output_dir,
                stage_log,
                task["filename"],
            )
            parse_metrics = parse_tracker.finish_metrics(int((time.monotonic() - parse_start) * 1000), len(blocks))
            if parser_provider == "ali_document_mind":
                try:
                    from core.parser.document_mind_parser import get_last_document_mind_key_pool_metrics

                    parse_metrics.update(get_last_document_mind_key_pool_metrics())
                except Exception as exc:
                    parse_metrics["parseKeyMetricsError"] = str(exc)
        else:
            _set_stage(task, "parse", "running", "正在执行来源适配...")
            logger.info("[parse] Source adapter started: type=%s file=%s", task.get("source_type"), task.get("filename"))
            blocks, parser_provider, parse_metrics = await asyncio.to_thread(_adapt_task_source_to_blocks, task)
            task["parse_provider"] = parser_provider
            save_task(task)
        blocks_preview = [_block_to_payload(block, index) for index, block in enumerate(blocks[:12])]
        task = get_task(task_id) or {}
        task["blocks_preview"] = blocks_preview
        parse_latency_ms = int((time.monotonic() - parse_start) * 1000)
        _set_stage(
            task,
            "parse",
            "success",
            f"解析完成，共 {len(blocks)} 个内容块",
            latency_ms=parse_latency_ms,
            input_count=1,
            output_count=len(blocks),
            metrics=parse_metrics,
        )
        logger.info("[parse] Done: %d blocks in %dms", len(blocks), int((time.monotonic() - parse_start) * 1000))
        task = get_task(task_id) or {}
        parse_page_count = _parse_page_count(blocks, parse_metrics)
        parse_event_metadata = dict(parse_metrics or {})
        parse_event_metadata["outputBlocks"] = len(blocks)
        parse_event_metadata["pageCount"] = parse_page_count
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="parse",
            event_type="parse_provider",
            feature_name="文档解析 provider",
            metric_value=parse_page_count or len(blocks),
            metric_unit="pages" if parse_page_count > 0 else "content_blocks",
            duration_ms=parse_latency_ms,
            provider=parser_provider or task.get("parse_provider") or "source_adapter",
            metadata=parse_event_metadata,
        )
        _record_oss_upload_cost_event(
            task_id=task_id,
            task=task,
            source_path=parse_input_path,
            parse_metrics=parse_event_metadata,
        )

        task = get_task(task_id)
        _set_stage(task, "clean", "running", "正在执行规则清洗...")
        logger.info("[clean] Rule cleaning started: %d blocks", len(blocks))
        clean_start = time.monotonic()
        from core.cleaner import clean_blocks

        clean_result = await asyncio.to_thread(
            clean_blocks,
            blocks,
            **_cleaner_runtime_options(),
        )
        cleaned = clean_result.blocks
        task = get_task(task_id)
        if not task:
            return
        task["removed_reasons"] = _summarize_removed_blocks(clean_result.removed_blocks)
        _set_stage(
            task,
            "clean",
            "success",
            f"清洗完成，保留 {len(cleaned)} 块，丢弃 {len(blocks) - len(cleaned)} 块",
            latency_ms=int((time.monotonic() - clean_start) * 1000),
            input_count=len(blocks),
            output_count=len(cleaned),
            metrics=dict(getattr(clean_result, "metrics", {}) or {}),
        )
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="clean",
            event_type="stage_processing",
            feature_name="内容清洗",
            metric_value=len(cleaned),
            metric_unit="content_blocks",
            duration_ms=int((time.monotonic() - clean_start) * 1000),
            metadata=dict(getattr(clean_result, "metrics", {}) or {}),
        )
        _record_ingestion_llm_usage(
            task_id=task_id,
            task=task,
            stage="clean",
            feature_name="清洗 LLM",
            metrics=dict(getattr(clean_result, "metrics", {}) or {}),
            token_prefix="llmCleaner",
            latency_ms=int((time.monotonic() - clean_start) * 1000),
            provider=_runtime_str("LLM_CLEANER_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
            model_name=_runtime_str("LLM_CLEANER_MODEL", "qwen-plus"),
        )
        save_task(task)

        task = get_task(task_id)
        if not task:
            return
        _set_stage(task, "chunk", "running", f"正在执行 {task['strategy']} 切片策略...", progress=1)
        logger.info("[chunk] Strategy=%s input=%d", task["strategy"], len(cleaned))
        chunk_start = time.monotonic()
        from core.chunker import get_strategy

        strategy_kwargs = {}
        requested_ready_mode, ready_mode_source = _resolve_ingestion_ready_mode()
        effective_ready_mode = "basic" if task["strategy"] == "hierarchical" and requested_ready_mode == "basic" else "full"
        task["ingestion_ready_mode"] = effective_ready_mode
        task["ingestion_ready_mode_requested"] = requested_ready_mode
        task["ingestion_ready_mode_source"] = ready_mode_source
        save_task(task)
        last_chunk_progress_update = 0.0

        def chunk_progress(message: str) -> None:
            nonlocal last_chunk_progress_update
            logger.info("[chunk] %s", message)
            now = time.monotonic()
            if now - last_chunk_progress_update < 2:
                return
            last_chunk_progress_update = now
            current_task = get_task(task_id)
            if current_task:
                _set_stage(
                    current_task,
                    "chunk",
                    "running",
                    message,
                    input_count=len(cleaned),
                    progress=_chunk_progress_from_message(message),
                )

        if task["strategy"] == "hierarchical":
            strategy_kwargs["enhanced_system_prompt"] = ""
            strategy_kwargs["progress_callback"] = chunk_progress
            if effective_ready_mode == "basic":
                strategy_kwargs["enable_enhanced"] = False
                logger.info(
                    "[chunk] Basic-ready mode enabled: hierarchical enhancement chunks will be skipped"
                )
        strategy_obj = get_strategy(task["strategy"], **strategy_kwargs)
        chunks = await asyncio.to_thread(strategy_obj.chunk, cleaned)
        chunk_timings = dict(getattr(strategy_obj, "last_timings", {}) or {})
        chunk_timings["readyMode"] = effective_ready_mode
        chunk_timings["requestedReadyMode"] = requested_ready_mode
        chunk_timings["readyModeSource"] = ready_mode_source
        chunk_timings["enhancementSkipped"] = 1 if effective_ready_mode == "basic" else 0
        task = get_task(task_id)
        if task:
            task["ingestion_ready_mode"] = effective_ready_mode
            task["ingestion_ready_mode_requested"] = requested_ready_mode
            task["ingestion_ready_mode_source"] = ready_mode_source
            task["chunk_timings"] = chunk_timings
            save_task(task)
            _set_stage(
                task,
                "chunk",
                "running",
                f"切片主体完成，正在构建图文/表格引用关系：{len(chunks)} 个切片...",
                input_count=len(cleaned),
                output_count=len(chunks),
                metrics=chunk_timings,
                progress=95,
            )
        logger.info("[chunk] Linking related chunks started: %d chunks", len(chunks))
        link_start = time.monotonic()
        chunks = await asyncio.to_thread(link_related_chunks, chunks)
        chunk_timings["linkRelationsMs"] = int((time.monotonic() - link_start) * 1000)
        logger.info("[chunk] Linking related chunks finished: %d chunks", len(chunks))
        task = get_task(task_id)
        if not task:
            return
        task["chunk_timings"] = chunk_timings
        task["chunks_preview"] = [
            _chunk_to_payload(chunk, task["kb_id"], task["strategy"], task["filename"])
            for chunk in chunks[:12]
        ]
        breakdown = _format_chunk_timings(chunk_timings)
        _set_stage(
            task,
            "chunk",
            "success",
            f"切片完成，共 {len(chunks)} 个切块；{breakdown}" if breakdown else f"切片完成，共 {len(chunks)} 个切块",
            latency_ms=int((time.monotonic() - chunk_start) * 1000),
            input_count=len(cleaned),
            output_count=len(chunks),
            metrics=chunk_timings,
        )
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="chunk",
            event_type="stage_processing",
            feature_name="切片与关系构建",
            metric_value=len(chunks),
            metric_unit="chunks",
            duration_ms=int((time.monotonic() - chunk_start) * 1000),
            metadata=chunk_timings,
        )
        _record_ingestion_llm_usage(
            task_id=task_id,
            task=task,
            stage="chunk",
            feature_name="三层切片增强",
            metrics=chunk_timings,
            token_prefix="enhance",
            latency_ms=int(chunk_timings.get("enhanceWallMs", 0) or int((time.monotonic() - chunk_start) * 1000)),
            provider=_runtime_str("LLM_BASE_URL", "openai-compatible"),
            model_name=_runtime_str("LLM_CLEANER_MODEL", "qwen-plus"),
        )
        save_task(task)

        await asyncio.to_thread(save_chunk_drafts, task_id, task["kb_id"], chunks)
        logger.info("[chunk] Direct finalize path enabled: %d chunks", len(chunks))
        await _finalize_pipeline(task_id, chunks)

    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        task = get_task(task_id) or {}
        current = task.get("current_stage") or "upload"
        task["stages"] = task.get("stages", {key: _new_stage_state() for key in STAGE_KEYS})
        for key in STAGE_KEYS:
            task["stages"].setdefault(key, _new_stage_state())
            task["stages"][key].setdefault("progress", 0)
            task["stages"][key].setdefault("message", "")
            task["stages"][key].setdefault("latency_ms", 0)
            task["stages"][key].setdefault("input_count", 0)
            task["stages"][key].setdefault("output_count", 0)
        task["stages"][current]["status"] = "failed"
        task["stages"][current]["message"] = f"错误：{exc}"
        task["status"] = "failed"
        task["error"] = str(exc)
        task["done"] = True
        task["awaiting_confirmation"] = False
        save_task(task)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        close_task_logger(task_id)


async def run_pipeline_and_confirm(task_id: str) -> None:
    """Run the direct ingestion pipeline; retained for compatibility."""
    await run_pipeline_real(task_id)


async def confirm_pipeline(task_id: str) -> dict:
    task = _load_task_or_raise(task_id)
    chunks = await asyncio.to_thread(load_confirmable_chunks, task_id)
    if not chunks:
        if task.get("status") == "success":
            return _task_to_payload(task)
        raise ValueError("No active chunk drafts found for confirmation")

    task["awaiting_confirmation"] = False
    _set_task_status(task, "running")

    from core.chunker.causal_linker import link_causal
    from core.chunker.procedure_linker import detect_procedure_chunks, link_procedure
    from core.chunker.semantic_linker import link_semantic
    from core.cleaner.quality_gate import apply_quality_gate
    from core.db.connection import get_db_connection
    from core.embedding.client import embed_texts_with_metrics
    from core.kg.extraction_pipeline import materialize_entities
    from core.output.pgvector_writer import write_to_pgvector

    try:
        _set_stage(task, "quality", "running", "正在执行质量门控...")
        from core.logging import get_task_logger

        logger = get_task_logger(task_id)
        quality_options = _quality_runtime_options()
        logger.info(
            "[quality] Quality gate started: input=%d batchSize=%s maxConcurrency=%s model=%s",
            len(chunks),
            quality_options.get("llm_batch_size"),
            quality_options.get("llm_max_concurrency"),
            quality_options.get("llm_model"),
        )
        quality_start = time.monotonic()
        last_quality_progress_update = 0.0

        def quality_progress(completed_batches: int, total_batches: int) -> None:
            nonlocal last_quality_progress_update
            now = time.monotonic()
            if completed_batches < total_batches and now - last_quality_progress_update < 5:
                return
            last_quality_progress_update = now
            progress = 1 if total_batches <= 0 else int(completed_batches / total_batches * 99)
            message = f"质量门控评分进度：{completed_batches}/{total_batches} 批"
            logger.info("[quality] %s", message)
            current_task = get_task(task_id)
            if current_task:
                _set_stage(
                    current_task,
                    "quality",
                    "running",
                    message,
                    input_count=len(chunks),
                    progress=progress,
                )

        quality_options["llm_progress_callback"] = quality_progress
        quality_result = await asyncio.to_thread(
            apply_quality_gate,
            chunks,
            **quality_options,
        )
        passed = quality_result.chunks
        quality_metrics = dict(getattr(quality_result, "metrics", {}) or {})
        task = _load_task_or_raise(task_id)
        task["quality_breakdown"] = _build_quality_breakdown(len(chunks), len(passed), quality_result.discarded_count)
        _set_stage(
            task,
            "quality",
            "success",
            f"质量门控完成，通过 {len(passed)} 块，丢弃 {len(chunks) - len(passed)} 块",
            latency_ms=int((time.monotonic() - quality_start) * 1000),
            input_count=len(chunks),
            output_count=len(passed),
            metrics=quality_metrics,
        )
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="quality",
            event_type="stage_processing",
            feature_name="质量门控",
            metric_value=len(passed),
            metric_unit="chunks",
            duration_ms=int((time.monotonic() - quality_start) * 1000),
            metadata=quality_metrics,
        )
        logger.info(
            "[quality] Quality gate finished: passed=%d discarded=%d batches=%s failures=%s latencyMs=%d",
            len(passed),
            quality_result.discarded_count,
            quality_metrics.get("qualityLlmBatchCount", 0),
            quality_metrics.get("qualityLlmBatchFailures", 0),
            int((time.monotonic() - quality_start) * 1000),
        )
        _record_ingestion_llm_usage(
            task_id=task_id,
            task=task,
            stage="quality",
            feature_name="质量审核 LLM",
            metrics=quality_metrics,
            token_prefix="qualityLlm",
            latency_ms=int((time.monotonic() - quality_start) * 1000),
            provider=_runtime_str("LLM_QUALITY_GATE_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
            model_name=_runtime_str("LLM_QUALITY_GATE_MODEL", "qwen-plus"),
        )

        _set_stage(task, "embedding", "running", "正在生成向量...")
        logger.info("[embedding] Embedding started: input=%d", len(passed))
        embedding_start = time.monotonic()
        texts = [chunk.content for chunk in passed]
        embedding_run = await asyncio.to_thread(embed_texts_with_metrics, texts)
        embeddings = embedding_run.embeddings
        embedding_metrics = dict(embedding_run.metrics)
        semantic_start = time.monotonic()
        await asyncio.to_thread(link_semantic, passed, embeddings)
        embedding_metrics["linkSemanticMs"] = int((time.monotonic() - semantic_start) * 1000)
        procedure_start = time.monotonic()
        await asyncio.to_thread(detect_procedure_chunks, passed)
        await asyncio.to_thread(link_procedure, passed)
        embedding_metrics["linkProcedureMs"] = int((time.monotonic() - procedure_start) * 1000)
        causal_start = time.monotonic()
        await asyncio.to_thread(link_causal, passed)
        embedding_metrics["linkCausalMs"] = int((time.monotonic() - causal_start) * 1000)
        task = _load_task_or_raise(task_id)
        _set_stage(
            task,
            "embedding",
            "success",
            f"向量化完成，共 {len(embeddings)} 个向量",
            latency_ms=int((time.monotonic() - embedding_start) * 1000),
            input_count=len(passed),
            output_count=len(embeddings),
            metrics=embedding_metrics,
        )
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="embedding",
            event_type="embedding",
            feature_name="Embedding 向量化",
            metric_value=len(embeddings),
            metric_unit="vectors",
            duration_ms=int((time.monotonic() - embedding_start) * 1000),
            provider=_runtime_str("LLM_EMBEDDING_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
            model_name=_runtime_str("LLM_EMBEDDING_MODEL", "text-embedding-v3"),
            metadata=embedding_metrics,
        )
        logger.info(
            "[embedding] Embedding finished: output=%d requests=%s latencyMs=%d",
            len(embeddings),
            embedding_metrics.get("embeddingRequests", 0),
            int((time.monotonic() - embedding_start) * 1000),
        )
        _record_ingestion_llm_usage(
            task_id=task_id,
            task=task,
            stage="embedding",
            feature_name="Embedding 向量化",
            metrics=embedding_metrics,
            token_prefix="embedding",
            latency_ms=int(embedding_metrics.get("embeddingWallMs", 0) or int((time.monotonic() - embedding_start) * 1000)),
            provider=_runtime_str("LLM_EMBEDDING_BASE_URL", _runtime_str("LLM_BASE_URL", "openai-compatible")),
            model_name=_runtime_str("LLM_EMBEDDING_MODEL", "text-embedding-v3"),
        )

        _set_stage(task, "export", "running", "正在写入 pgvector...")
        logger.info("[export] pgvector write started: input=%d", len(passed))
        export_start = time.monotonic()
        export_metrics: dict[str, int | str] = {}
        entity_conn = get_db_connection()
        try:
            entity_start = time.monotonic()
            await asyncio.to_thread(materialize_entities, entity_conn, passed, task["kb_id"])
            export_metrics["entityMaterializeMs"] = int((time.monotonic() - entity_start) * 1000)
            entity_conn.commit()
        except Exception:
            entity_conn.rollback()
            raise
        finally:
            entity_conn.close()

        write_start = time.monotonic()
        source_metadata = _build_document_source_metadata(task)
        write_result = await asyncio.to_thread(
            write_to_pgvector,
            passed,
            embeddings,
            task["kb_id"],
            task.get("source_path") or "",
            task["filename"],
            source_metadata["source_storage"],
            source_metadata["source_path"],
            source_metadata["source_url"],
            source_metadata["parser_provider"],
        )
        export_metrics["pgvectorWriteMs"] = int((time.monotonic() - write_start) * 1000)
        export_metrics.update(write_result.get("metrics", {}) or {})
        written = write_result.get("written", len(passed))
        task = _load_task_or_raise(task_id)
        task["document_id"] = str(write_result.get("document_id") or "")
        if task["document_id"]:
            repair_usage_document_id(task_id, document_id=task["document_id"], kb_id=str(task.get("kb_id") or ""))
        _set_stage(
            task,
            "export",
            "success",
            f"写入完成，共 {written} 条记录",
            latency_ms=int((time.monotonic() - export_start) * 1000),
            input_count=len(passed),
            output_count=written,
            metrics=export_metrics,
        )
        _record_processing_cost_event(
            task_id=task_id,
            task=task,
            stage="export",
            event_type="storage_export",
            feature_name="pgvector 写库",
            metric_value=written,
            metric_unit="rows",
            duration_ms=int((time.monotonic() - export_start) * 1000),
            provider="pgvector",
            metadata={**export_metrics, "documentId": task.get("document_id", "")},
        )
        logger.info(
            "[export] pgvector write finished: written=%d latencyMs=%d",
            written,
            int((time.monotonic() - export_start) * 1000),
        )
        task["chunk_count"] = written
        task["chunks_preview"] = [
            _chunk_to_payload(chunk, task["kb_id"], task["strategy"], task["filename"])
            for chunk in passed[:12]
        ]
        clear_chunk_drafts(task_id)
        _set_task_status(task, "success", done=True)
        return _task_to_payload(task)

    except Exception as exc:
        task = get_task(task_id) or {}
        current = task.get("current_stage") or "quality"
        task.setdefault("stages", {key: _new_stage_state() for key in STAGE_KEYS})
        for key in STAGE_KEYS:
            task["stages"].setdefault(key, _new_stage_state())
        task["stages"][current]["status"] = "failed"
        task["stages"][current]["message"] = f"确认阶段错误：{exc}"
        task["status"] = "failed"
        task["error"] = str(exc)
        task["done"] = True
        save_task(task)
        raise


async def _finalize_pipeline(task_id: str, chunks: list) -> dict:
    task = _load_task_or_raise(task_id)
    task["awaiting_confirmation"] = False
    task["chunk_count"] = len(chunks)
    task["chunks_preview"] = [
        _chunk_to_payload(chunk, task["kb_id"], task["strategy"], task["filename"])
        for chunk in chunks[:12]
    ]
    _set_task_status(task, "running")
    return await confirm_pipeline(task_id)


async def stream_task_events(task_id: str) -> AsyncIterator[str]:
    import json as _json

    task = get_task(task_id)
    if not task:
        yield f"event: error\ndata: {_json.dumps({'message': 'task not found'})}\n\n"
        return

    log_path = os.path.join("data", "logs", f"{task_id}.log")
    log_offset = 0
    last_stage_snapshot = None
    heartbeat_interval = 15.0
    elapsed_since_heartbeat = 0.0

    while True:
        task = get_task(task_id)
        if not task:
            break

        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as log_file:
                    log_file.seek(log_offset)
                    new_lines = log_file.readlines()
                    log_offset = log_file.tell()
                for line in new_lines:
                    line = line.rstrip("\n")
                    if line:
                        yield f"event: log\ndata: {_json.dumps({'line': line}, ensure_ascii=False)}\n\n"
            except OSError:
                pass

        current = task.get("current_stage")
        if current:
            stage_info = task["stages"][current]
            payload = {
                "stage_key": current,
                "stage_label": STAGE_LABELS.get(current, current),
                "status": stage_info["status"],
                "progress": stage_info.get("progress", 0),
                "message": stage_info["message"],
                "task_status": task["status"],
            }
            snapshot = (
                payload["stage_key"],
                payload["status"],
                payload["progress"],
                payload["message"],
                payload["task_status"],
            )
            if snapshot != last_stage_snapshot:
                yield f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                last_stage_snapshot = snapshot

        if task.get("done"):
            break

        await asyncio.sleep(0.5)
        elapsed_since_heartbeat += 0.5
        if elapsed_since_heartbeat >= heartbeat_interval:
            elapsed_since_heartbeat = 0.0
            yield f"event: heartbeat\ndata: {_json.dumps({'task_status': task.get('status', 'unknown')}, ensure_ascii=False)}\n\n"

    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as log_file:
                log_file.seek(log_offset)
                for line in log_file.readlines():
                    line = line.rstrip("\n")
                    if line:
                        yield f"event: log\ndata: {_json.dumps({'line': line}, ensure_ascii=False)}\n\n"
        except OSError:
            pass

    task = get_task(task_id) or {}
    final = {
        "stage_key": "done",
        "task_status": task.get("status", "unknown"),
        "message": "管道执行完成" if task.get("status") == "success" else f"管道执行失败：{task.get('error', '')}",
    }
    yield f"event: done\ndata: {_json.dumps(final, ensure_ascii=False)}\n\n"


def _block_to_payload(block, index: int) -> dict:
    return {
        "id": f"block-{index + 1:03d}",
        "type": block.type.value,
        "text": block.text,
        "page": int(block.page_idx) + 1,
        "level": block.text_level,
        "sourceFile": block.source_file,
        "tableHtml": block.table_html,
        "imagePath": block.image_path,
    }


def _chunk_to_payload(chunk, kb_id: str, strategy: str, source_file: str) -> dict:
    return {
        "id": chunk.id,
        "documentId": "",
        "kbId": kb_id,
        "source": chunk.source or source_file,
        "page": int(chunk.page) + 1 if isinstance(chunk.page, int) else int(chunk.page or 0),
        "chunkIndex": chunk.chunk_index,
        "title": chunk.title,
        "content": chunk.content,
        "strategy": chunk.strategy or strategy,
        "layer": chunk.layer,
        "parentId": chunk.parent_id,
        "relatedIds": chunk.related_ids,
        "charCount": chunk.char_count,
        "isTableChunk": bool(chunk.is_table_chunk),
        "isImageChunk": bool(chunk.is_image_chunk),
        "imagePath": chunk.image_path,
        "qualityScore": None,
        "rerankScore": None,
        "denseScore": None,
    }


def _summarize_removed_blocks(removed_blocks: list) -> list[dict]:
    counts: dict[str, int] = {}
    for item in removed_blocks:
        counts[item.rule] = counts.get(item.rule, 0) + 1
    return [{"label": label, "count": count} for label, count in sorted(counts.items())]


def _build_quality_breakdown(total: int, passed: int, discarded: int) -> list[dict]:
    return [
        {"label": "通过", "count": passed},
        {"label": "丢弃", "count": discarded},
        {"label": "总计", "count": total},
    ]
