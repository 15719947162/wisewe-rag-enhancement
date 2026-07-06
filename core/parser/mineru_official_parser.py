"""
MinerU 官方 Precision API 解析器

这个模块实现了 MinerU 官方 API (mineru.net) 的 PDF 解析功能。
MinerU 是一个开源的 PDF 解析工具,官方提供的 Precision API 可以高质量提取 PDF 内容。

主要功能:
1. 单任务解析 - 将整个 PDF 上传到 OSS,提交解析任务,轮询等待结果
2. 分片解析 - 将大 PDF 切分成多个片段并行解析
3. 结果提取 - 从返回的 ZIP 包中提取解析结果和图片

与 302AI MinerU 的区别:
- mineru_parser.py: 使用 302AI 托管的 MinerU 服务
- mineru_official_parser.py: 使用 MinerU 官方 API (mineru.net)

环境变量配置:
- MINERU_OFFICIAL_API_TOKEN: API 访问令牌(必需)
- MINERU_OFFICIAL_API_BASE: API 地址(默认 https://mineru.net)
- MINERU_OFFICIAL_MODEL_VERSION: 模型版本(默认 vlm)
- MINERU_OFFICIAL_SHARDING_ENABLED: 是否启用分片解析
"""
from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from core.http_client import create_httpx_client
from core.models.content_block import ContentBlock
from core.parser.mineru_parser import (
    _download_zip,
    _extract_and_map,
    _retryable_http_exceptions,
    upload_pdf_to_oss,
)
from core.parser.pdf_sharding import (
    PdfInspection,
    PdfShard,
    ShardBlockRecord,
    inspect_pdf,
    merge_shard_records,
    offset_shard_blocks,
    split_pdf_to_shards,
)

_DEFAULT_API_BASE = "https://mineru.net"


def parse_pdf(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """
    使用 MinerU 官方 API 解析 PDF

    这是主要的解析入口函数,会自动判断是否需要分片解析。

    工作流程:
    1. 检查 PDF 文件大小和页数
    2. 如果超过阈值,启用分片解析
    3. 否则使用单任务解析:
       a. 上传 PDF 到阿里云 OSS 获取签名 URL
       b. 向 MinerU API 提交解析任务
       c. 轮询任务状态直到完成
       d. 下载结果 ZIP 包
       e. 提取并转换为 ContentBlock 列表

    参数:
        pdf_path: PDF 文件路径
        output_dir: 输出目录(存放解析结果和图片)
        log_fn: 日志回调函数,用于输出进度信息
        original_name: 原始文件名(用于元数据)

    返回:
        ContentBlock 列表,包含文本、表格、图片等内容块

    示例:
        blocks = parse_pdf("document.pdf", log_fn=print)
        for block in blocks:
            print(f"类型: {block.type}, 页码: {block.page_idx}")
    """
    if _is_sharding_enabled():
        try:
            inspection = inspect_pdf(pdf_path, text_sample_pages=_official_int("SHARDING_TEXT_SAMPLE_PAGES", 5))
        except Exception as exc:
            _log(log_fn, f"PDF inspection failed, fallback to single MinerU official task: {type(exc).__name__}: {exc}")
        else:
            _log_pdf_inspection(inspection, log_fn)
            if _should_parse_with_shards(inspection):
                return parse_pdf_sharded(
                    pdf_path,
                    output_dir=output_dir,
                    log_fn=log_fn,
                    original_name=original_name,
                    inspection=inspection,
                )
            _log(log_fn, "Single MinerU official task selected: sharding threshold not matched")

    pdf_url = upload_pdf_to_oss(pdf_path, log_fn=log_fn, original_name=original_name)
    return parse_pdf_from_url(
        pdf_url,
        pdf_path=pdf_path,
        output_dir=output_dir,
        log_fn=log_fn,
        original_name=original_name,
    )


def parse_pdf_sharded(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
    *,
    inspection: PdfInspection | None = None,
) -> list[ContentBlock]:
    """
    分片解析 PDF

    将大 PDF 文件切分成多个小片段,并行提交给 MinerU API 解析。

    为什么要分片?
    1. MinerU API 对单个任务有处理限制
    2. 并行处理可以显著提高解析速度
    3. 大文件单任务容易超时或失败

    工作流程:
    1. 检查 PDF 基本信息(页数、文件大小)
    2. 计算合适的分片策略(每片多少页)
    3. 切分 PDF 为多个片段文件
    4. 并行提交所有片段到 MinerU API
    5. 等待所有片段完成
    6. 合并所有片段的解析结果
    7. 调整全局页码

    参数:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        log_fn: 日志回调函数
        original_name: 原始文件名
        inspection: PDF 检查信息(可选)

    返回:
        合并后的 ContentBlock 列表
    """
    source_name = original_name or Path(pdf_path).name
    inspection = inspection or inspect_pdf(pdf_path, text_sample_pages=_official_int("SHARDING_TEXT_SAMPLE_PAGES", 5))
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pages_per_shard = _effective_pages_per_shard(inspection)
    with TemporaryDirectory(prefix="mineru_official_shards_") as tmp_dir:
        shards = split_pdf_to_shards(pdf_path, Path(tmp_dir), pages_per_shard=pages_per_shard)
        if not shards:
            return []

        max_workers = min(max(_official_int("SHARDING_MAX_CONCURRENCY", 2), 1), len(shards))
        _log(
            log_fn,
            "Enabled MinerU official sharded parse: "
            f"{inspection.page_count} pages, {inspection.file_size_mb:.1f} MB, "
            f"{len(shards)} shards, pages_per_shard={pages_per_shard}, workers={max_workers}",
        )

        records: list[ShardBlockRecord] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _parse_pdf_shard,
                    shard,
                    output_path,
                    source_name,
                    len(shards),
                    log_fn,
                ): shard
                for shard in shards
            }
            for future in as_completed(futures):
                shard = futures[future]
                try:
                    records.extend(future.result())
                except Exception as exc:
                    raise RuntimeError(
                        f"MinerU official shard parse failed: shard {shard.index:03d}/{len(shards):03d} "
                        f"{shard.display_range}"
                    ) from exc

        blocks = merge_shard_records(records)
        _log(log_fn, f"MinerU official sharded parse merge complete: output blocks={len(blocks)}")
        return blocks


def parse_pdf_from_url(
    pdf_url: str,
    pdf_path: str = "",
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """
    从 URL 解析 PDF

    当 PDF 已经上传到 OSS 并有可访问 URL 时,直接使用该 URL 解析。

    步骤:
    1. 向 MinerU API 提交解析任务,传入 PDF URL
    2. 轮询任务状态,等待完成
    3. 获取结果 ZIP 包的下载 URL
    4. 下载 ZIP 包并提取内容
    5. 转换为 ContentBlock 列表

    参数:
        pdf_url: PDF 文件的 OSS URL
        pdf_path: 本地 PDF 路径(可选,用于获取原始文件名)
        output_dir: 输出目录
        log_fn: 日志回调函数
        original_name: 原始文件名

    返回:
        ContentBlock 列表
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    source_name = original_name or (Path(pdf_path).name if pdf_path else "document.pdf")

    _log(log_fn, "Step 1/3: submit MinerU official Precision API task")
    task_id = _submit_task(pdf_url, log_fn=log_fn)
    _log(log_fn, f"MinerU official task submitted: task_id={task_id}")

    _log(log_fn, "Step 2/3: poll MinerU official task status")
    result_url = _poll_task(task_id, log_fn=log_fn)
    parsed = urlparse(result_url)
    _log(log_fn, f"MinerU official result full_zip_url returned: host={parsed.netloc} path={parsed.path}")

    _log(log_fn, "Step 3/3: download and map MinerU official result ZIP")
    zip_bytes = _download_zip(result_url, log_fn=log_fn)
    blocks = _extract_and_map(zip_bytes, source_name, output_path)
    _log(log_fn, f"MinerU official parse complete: output blocks={len(blocks)}")
    return blocks


def _submit_task(pdf_url: str, log_fn: Optional[Callable[[str], None]] = None) -> str:
    """
    向 MinerU API 提交解析任务

    调用 MinerU API 的 /api/v4/extract/task 接口提交 PDF 解析任务。

    参数:
        pdf_url: PDF 文件的 URL
        log_fn: 日志回调函数

    返回:
        task_id: 任务 ID,用于后续查询任务状态

    环境变量配置:
        MINERU_OFFICIAL_MODEL_VERSION: 模型版本(vlm/ocr 等)
        MINERU_OFFICIAL_IS_OCR: 是否启用 OCR
        MINERU_OFFICIAL_ENABLE_FORMULA: 是否识别公式
        MINERU_OFFICIAL_ENABLE_TABLE: 是否识别表格
        MINERU_OFFICIAL_LANGUAGE: 文档语言(ch/en 等)
    """
    token = _official_token()
    api_base = _official_api_base()
    url = f"{api_base}/api/v4/extract/task"
    payload = _official_payload({"url": pdf_url})
    headers = _official_headers(token)
    max_attempts = max(_official_int("SUBMIT_RETRY_ATTEMPTS", 3), 1)

    _log(log_fn, f"POST {url} model_version={payload.get('model_version')} no_cache={payload.get('no_cache')}")
    data = _request_json_with_retries("post", url, headers=headers, json=payload, attempts=max_attempts, log_fn=log_fn)
    _ensure_success_response(data, "MinerU official task submit")
    task_id = _read_nested(data, ("data", "task_id")) or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"MinerU official did not return task_id: {data}")
    return str(task_id)


def _poll_task(task_id: str, log_fn: Optional[Callable[[str], None]] = None) -> str:
    """
    轮询 MinerU 任务状态

    定期查询任务状态,直到任务完成或超时。

    任务状态:
    - done: 任务完成,可以下载结果
    - failed: 任务失败
    - processing: 任务正在处理中

    参数:
        task_id: 任务 ID
        log_fn: 日志回调函数

    返回:
        result_url: 结果 ZIP 包的下载 URL

    环境变量配置:
        MINERU_OFFICIAL_TIMEOUT: 最大等待时间(秒)
        MINERU_OFFICIAL_POLL_INTERVAL: 轮询间隔(秒)

    异常:
        RuntimeError: 任务失败
        TimeoutError: 任务超时
    """
    token = _official_token()
    url = f"{_official_api_base()}/api/v4/extract/task/{task_id}"
    headers = _official_headers(token)
    timeout = max(_official_int("TIMEOUT", 1800), 1)
    interval = max(_official_float("POLL_INTERVAL", 3.0), 0.1)
    max_network_errors = max(_official_int("POLL_RETRY_ATTEMPTS", 5), 1)
    deadline = time.monotonic() + timeout
    poll_count = 0
    network_errors = 0

    while True:
        try:
            with create_httpx_client(timeout=30) as client:
                response = client.get(url, headers=headers)
            network_errors = 0
        except _retryable_http_exceptions() as exc:
            network_errors += 1
            if network_errors >= max_network_errors:
                raise RuntimeError(
                    f"MinerU official polling failed after {network_errors} network errors for task_id={task_id}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Polling timed out after {timeout}s for task_id={task_id}") from exc
            _log(log_fn, f"poll network error {network_errors}/{max_network_errors}: {type(exc).__name__}: {exc}")
            time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
            interval = min(interval * 1.5, 15.0)
            continue

        response.raise_for_status()
        raw = response.json()
        _ensure_success_response(raw, "MinerU official task poll")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        state = str(data.get("state", "")).lower()
        poll_count += 1
        progress = data.get("extract_progress") if isinstance(data.get("extract_progress"), dict) else {}
        extracted = progress.get("extracted_pages")
        total = progress.get("total_pages")
        _log(log_fn, f"poll #{poll_count} state={state} extracted={extracted}/{total}")

        if state == "done":
            result_url = data.get("full_zip_url")
            if not result_url:
                raise RuntimeError(f"MinerU official task {task_id} done but full_zip_url is missing")
            return str(result_url)
        if state == "failed":
            raise RuntimeError(f"MinerU official task {task_id} failed: {data.get('err_msg', 'unknown error')}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Polling timed out after {timeout}s for task_id={task_id}")

        time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
        interval = min(interval * 1.5, 15.0)


def _parse_pdf_shard(
    shard: PdfShard,
    output_path: Path,
    source_name: str,
    total_shards: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> list[ShardBlockRecord]:
    prefix = f"[shard {shard.index:03d}/{total_shards:03d} {shard.display_range}]"

    def shard_log(message: str) -> None:
        _log(log_fn, f"{prefix} {message}")

    source_path = Path(source_name)
    shard_upload_name = f"{source_path.stem}-shard-{shard.index:03d}{source_path.suffix or '.pdf'}"
    shard_log(f"submit official MinerU parse for {shard.path.name}, pages={shard.page_count}")
    pdf_url = upload_pdf_to_oss(str(shard.path), log_fn=shard_log, original_name=shard_upload_name)
    shard_output_dir = output_path / "official_shards" / f"shard_{shard.index:03d}"
    blocks = parse_pdf_from_url(
        pdf_url,
        pdf_path=str(shard.path),
        output_dir=str(shard_output_dir),
        log_fn=shard_log,
        original_name=source_name,
    )
    records = offset_shard_blocks(blocks, shard, source_name)
    shard_log(f"merge complete: output blocks={len(records)}, offset={shard.start_page}")
    return records


def _official_payload(base: dict[str, Any]) -> dict[str, Any]:
    payload = dict(base)
    payload.update(
        {
            "model_version": _official_str("MODEL_VERSION", "vlm"),
            "is_ocr": _official_bool("IS_OCR", False),
            "enable_formula": _official_bool("ENABLE_FORMULA", True),
            "enable_table": _official_bool("ENABLE_TABLE", True),
            "language": _official_str("LANGUAGE", "ch"),
            "no_cache": _official_bool("NO_CACHE", False),
            "cache_tolerance": _official_int("CACHE_TOLERANCE", 900),
        }
    )
    extra_formats = _official_list("EXTRA_FORMATS")
    if extra_formats:
        payload["extra_formats"] = extra_formats
    return payload


def _request_json_with_retries(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    attempts: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with create_httpx_client(timeout=30) as client:
                response = getattr(client, method)(url, headers=headers, json=json)
            response.raise_for_status()
            return response.json()
        except _retryable_http_exceptions() as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            _log(log_fn, f"MinerU official request retry #{attempt + 1}/{attempts}: {type(exc).__name__}: {exc}")
            time.sleep(min(2.0 * attempt, 8.0))
    raise RuntimeError(f"MinerU official request failed after {attempts} attempts: {last_exc}") from last_exc


def _ensure_success_response(data: dict[str, Any], action: str) -> None:
    code = data.get("code", 0)
    if code not in (0, "0", "00000", None):
        raise RuntimeError(f"{action} failed: code={code} msg={data.get('msg', '')} trace_id={data.get('trace_id', '')}")


def _official_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }


def _official_token() -> str:
    token = os.getenv("MINERU_OFFICIAL_API_TOKEN", "").strip()
    if not token:
        raise ValueError("MINERU_OFFICIAL_API_TOKEN environment variable is not set")
    return token


def _official_api_base() -> str:
    return os.getenv("MINERU_OFFICIAL_API_BASE", _DEFAULT_API_BASE).strip().rstrip("/") or _DEFAULT_API_BASE


def _official_env_name(name: str) -> str:
    return f"MINERU_OFFICIAL_{name}"


def _official_str(name: str, default: str) -> str:
    return os.getenv(_official_env_name(name), default).strip() or default


def _official_int(name: str, default: int) -> int:
    raw = os.getenv(_official_env_name(name), "")
    if raw == "":
        return default
    return int(raw)


def _official_float(name: str, default: float) -> float:
    raw = os.getenv(_official_env_name(name), "")
    if raw == "":
        return default
    return float(raw)


def _official_bool(name: str, default: bool) -> bool:
    raw = os.getenv(_official_env_name(name), "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _official_list(name: str) -> list[str]:
    raw = os.getenv(_official_env_name(name), "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_sharding_enabled() -> bool:
    return _official_bool("SHARDING_ENABLED", True)


def _should_parse_with_shards(inspection: PdfInspection) -> bool:
    if not _is_sharding_enabled():
        return False
    pages_per_shard = _effective_pages_per_shard(inspection)
    if inspection.page_count <= pages_per_shard:
        return False
    return (
        inspection.page_count >= _official_int("SHARDING_MIN_PAGES", 201)
        or inspection.file_size_mb >= _official_float("SHARDING_MIN_FILE_MB", 180.0)
    )


def _effective_pages_per_shard(inspection: PdfInspection) -> int:
    configured = max(_official_int("SHARDING_PAGES_PER_SHARD", 180), 1)
    max_file_mb = max(_official_float("SHARDING_MAX_FILE_MB_PER_SHARD", 180.0), 1.0)
    if inspection.page_count <= 0 or inspection.file_size_mb <= 0:
        return configured
    avg_mb_per_page = max(inspection.file_size_mb / inspection.page_count, 0.001)
    by_size = max(1, int(math.floor(max_file_mb / avg_mb_per_page)))
    return max(1, min(configured, by_size))


def _log_pdf_inspection(inspection: PdfInspection, log_fn: Optional[Callable[[str], None]] = None) -> None:
    _log(
        log_fn,
        "PDF inspection: "
        f"{inspection.page_count} pages, {inspection.file_size_mb:.1f} MB, "
        f"sampled_pages={inspection.sampled_pages}, sampled_text_chars={inspection.sampled_text_chars}",
    )


def _read_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _log(log_fn: Optional[Callable[[str], None]], message: str) -> None:
    if log_fn:
        log_fn(message)
