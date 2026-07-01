from __future__ import annotations

import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Optional
from urllib.parse import urlparse
import json

import httpx

from core.config import load_config
from core.http_client import create_httpx_client
from core.models.content_block import BlockType, ContentBlock
from core.parser.oss_uploader import upload_to_oss
from core.parser.pdf_sharding import (
    PdfInspection,
    PdfShard,
    ShardBlockRecord,
    import_fitz,
    inspect_pdf,
    merge_shard_records,
    offset_shard_blocks,
    split_pdf_to_shards,
)

_split_pdf_to_shards = split_pdf_to_shards


def parse_pdf(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """Parse a PDF through MinerU, using sharded cloud tasks for large files."""
    config = load_config()
    if _is_sharding_enabled(config):
        try:
            inspection = inspect_pdf(
                pdf_path,
                text_sample_pages=_get_sharding_config(config)["text_sample_pages"],
            )
        except Exception as exc:
            if log_fn:
                log_fn(f"PDF 体检失败，回退为单个 MinerU 云端任务解析：{type(exc).__name__}: {exc}")
        else:
            _log_pdf_inspection(inspection, log_fn)
            if _should_parse_with_shards(inspection, config):
                return parse_pdf_sharded(
                    pdf_path,
                    output_dir=output_dir,
                    log_fn=log_fn,
                    original_name=original_name,
                    inspection=inspection,
                    config=config,
                )
            if log_fn:
                log_fn("未命中分片阈值，使用单个 MinerU 云端任务解析。")

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
    config: dict | None = None,
) -> list[ContentBlock]:
    """Split a large PDF into page shards, parse in parallel, then merge blocks."""
    active_config = config or load_config()
    sharding_cfg = _get_sharding_config(active_config)
    source_name = original_name or Path(pdf_path).name
    inspection = inspection or inspect_pdf(
        pdf_path,
        text_sample_pages=sharding_cfg["text_sample_pages"],
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    def _log(message: str) -> None:
        if log_fn:
            log_fn(message)

    with TemporaryDirectory(prefix="mineru_shards_") as tmp_dir:
        shards = _split_pdf_to_shards(
            pdf_path,
            Path(tmp_dir),
            pages_per_shard=sharding_cfg["pages_per_shard"],
        )
        if not shards:
            return []

        max_workers = min(sharding_cfg["max_concurrency"], len(shards))
        _log(
            "启用 MinerU 分片解析："
            f"{inspection.page_count} 页，{inspection.file_size_mb:.1f} MB，"
            f"{len(shards)} 个 shard，每片最多 {sharding_cfg['pages_per_shard']} 页，"
            f"并发 {max_workers}"
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
                    shard_records = future.result()
                except Exception as exc:
                    raise RuntimeError(
                        "MinerU 分片解析失败："
                        f"shard_{shard.index:03d} {shard.display_range}"
                    ) from exc
                records.extend(shard_records)
                _log(
                    f"[shard {shard.index:03d}/{len(shards):03d}] "
                    f"合并完成，输出 {len(shard_records)} 个内容块"
                )

        blocks = merge_shard_records(records)
        _log(f"分片解析合并完成，共 {len(blocks)} 个内容块，页码已回写为原 PDF 全局页码")
        return blocks


def upload_pdf_to_oss(
    pdf_path: str,
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> str:
    """Upload a PDF to Aliyun OSS and return a signed URL for MinerU parsing."""

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    config = load_config()
    _log("上传 PDF 到阿里云 OSS...")
    pdf_url = upload_to_oss(
        str(pdf_path_obj),
        config,
        log_fn=log_fn,
        original_name=original_name or pdf_path_obj.name,
    )
    parsed = urlparse(pdf_url)
    _log(f"OSS 上传成功，签名地址主机: {parsed.netloc}")
    return pdf_url


def parse_pdf_from_url(
    pdf_url: str,
    pdf_path: str = "",
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """Submit a MinerU parse task for a PDF already on OSS and return content blocks."""

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    config = load_config()
    source_name = original_name or (Path(pdf_path).name if pdf_path else "document.pdf")

    _log("步骤 1/3：提交 302.ai MinerU 解析任务...")
    task_id = _submit_task(pdf_url, config, log_fn=log_fn)
    _log(f"任务已提交，task_id={task_id}")

    _log("步骤 2/3：轮询任务状态...")
    result_url = _poll_task(task_id, config, log_fn=log_fn)
    result_parsed = urlparse(result_url)
    _log(
        "解析完成，结果地址已返回："
        f"scheme={result_parsed.scheme} host={result_parsed.netloc} path={result_parsed.path}"
    )

    _log("步骤 3/3：下载并解析结果 ZIP...")
    zip_bytes = _download_zip(result_url, log_fn=log_fn)
    _log(f"ZIP 下载完成（{len(zip_bytes) / 1024:.1f} KB），开始提取内容块...")

    blocks = _extract_and_map(zip_bytes, source_name, output_path)
    _log(f"提取完成，共 {len(blocks)} 个内容块")
    return blocks


def _submit_task(
    pdf_url: str,
    config: dict,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    api_key = os.getenv("302AI_API_KEY", "")
    if not api_key:
        raise ValueError("302AI_API_KEY environment variable is not set")

    api_base = os.getenv("302AI_API_BASE", "")
    if not api_base:
        raise ValueError("302AI_API_BASE environment variable is not set")

    cloud_cfg = config.get("parser", {}).get("cloud", {})
    parse_method = cloud_cfg.get("parse_method", "auto")
    version = cloud_cfg.get("version", "2.5")
    enable_formula = bool(cloud_cfg.get("enable_formula", True))
    enable_table_html = bool(cloud_cfg.get("enable_table_html", True))
    language = str(cloud_cfg.get("language", "ch"))
    is_ocr = bool(cloud_cfg.get("is_ocr", False))
    model_version = str(cloud_cfg.get("model_version", "v2"))

    url = f"{api_base}/302/v2/mineru/task"
    payload = {
        "pdf_url": pdf_url,
        "parse_method": parse_method,
        "version": version,
        "enable_formula": enable_formula,
        "enable_table_html": enable_table_html,
        "language": language,
        "is_ocr": is_ocr,
        "model_version": model_version,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_attempts = max(int(cloud_cfg.get("submit_retry_attempts", 3)), 1)

    if log_fn:
        log_fn(
            "POST "
            f"{url} parse_method={parse_method} version={version} "
            f"formula={enable_formula} table_html={enable_table_html} "
            f"language={language} is_ocr={is_ocr} model_version={model_version}"
        )

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with create_httpx_client(timeout=30) as client:
                response = client.post(url, json=payload, headers=headers)
            break
        except _retryable_http_exceptions() as exc:
            last_exc = exc
            if attempt >= max_attempts:
                raise RuntimeError(
                    "302.ai MinerU task submit failed after "
                    f"{max_attempts} attempts: {type(exc).__name__}: {exc}"
                ) from exc
            if log_fn:
                log_fn(
                    "  提交 MinerU 任务遇到上游连接中断，"
                    f"准备重试 #{attempt + 1}/{max_attempts}: {type(exc).__name__}: {exc}"
                )
            time.sleep(min(2.0 * attempt, 8.0))
    else:
        raise RuntimeError(f"302.ai MinerU task submit failed: {last_exc}")

    response.raise_for_status()
    data = response.json()

    task_id = data.get("data", {}).get("task_id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"302.ai did not return a task_id: {data}")
    return task_id


def _poll_task(
    task_id: str,
    config: dict,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    api_key = os.getenv("302AI_API_KEY", "")
    api_base = os.getenv("302AI_API_BASE", "")
    if not api_base:
        raise ValueError("302AI_API_BASE environment variable is not set")

    cloud_cfg = config.get("parser", {}).get("cloud", {})
    timeout = int(cloud_cfg.get("timeout", 3600))
    poll_interval = float(cloud_cfg.get("poll_interval", 3))
    max_network_errors = max(int(cloud_cfg.get("poll_retry_attempts", 5)), 1)

    url = f"{api_base}/302/v2/mineru/task"
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + timeout
    interval = poll_interval
    poll_count = 0
    consecutive_network_errors = 0

    while True:
        try:
            with create_httpx_client(timeout=30) as client:
                response = client.get(url, params={"task_id": task_id}, headers=headers)
            consecutive_network_errors = 0
        except _retryable_http_exceptions() as exc:
            consecutive_network_errors += 1
            if consecutive_network_errors >= max_network_errors:
                raise RuntimeError(
                    "302.ai MinerU task polling failed after "
                    f"{consecutive_network_errors} consecutive network errors "
                    f"for task_id={task_id}: {type(exc).__name__}: {exc}"
                ) from exc
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Polling timed out after {timeout}s for task_id={task_id}") from exc
            if log_fn:
                log_fn(
                    "  轮询 MinerU 任务遇到上游连接中断，"
                    f"继续重试 {consecutive_network_errors}/{max_network_errors}: "
                    f"{type(exc).__name__}: {exc}"
                )
            time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
            interval = min(interval * 1.5, 15.0)
            continue

        response.raise_for_status()
        raw = response.json()
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        state = data.get("state", "")
        poll_count += 1

        if log_fn:
            elapsed = timeout - (deadline - time.monotonic())
            log_fn(f"  轮询 #{poll_count} 状态={state} 已等待={elapsed:.0f}s")

        if state == "SUCCESS":
            result_url = data.get("result_url")
            if not result_url:
                raise RuntimeError(f"Task {task_id} succeeded but result_url is missing")
            return result_url

        if state == "FAILED":
            err = data.get("err_msg", "unknown error")
            raise RuntimeError(f"302.ai task {task_id} failed: {err}")

        if time.monotonic() >= deadline:
            raise TimeoutError(f"Polling timed out after {timeout}s for task_id={task_id}")

        time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
        interval = min(interval * 1.5, 15.0)


def _retryable_http_exceptions() -> tuple[type[BaseException], ...]:
    import ssl as _ssl

    return (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        _ssl.SSLError,
        OSError,
    )


def _download_zip(
    result_url: str,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bytes:
    """Download the MinerU result ZIP with retries and detailed diagnostics."""
    import ssl as _ssl

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    parsed = urlparse(result_url)
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)

    # SSL EOF (UNEXPECTED_EOF_WHILE_READING) surfaces as RemoteProtocolError or
    # as a plain ssl.SSLError depending on the httpx/OpenSSL version.
    retry_excs = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
        _ssl.SSLError,
        OSError,
    )

    _log(
        "准备下载结果 ZIP："
        f"scheme={parsed.scheme} host={parsed.netloc} path={parsed.path}"
    )

    max_attempts = 6
    last_exc: Exception = RuntimeError("download failed")
    for attempt in range(1, max_attempts + 1):
        try:
            _log(f"  下载尝试 #{attempt}: host={parsed.netloc}")
            with create_httpx_client(
                http2=False,
                verify=True,
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                response = client.get(result_url)
                response.raise_for_status()
                _log(f"  下载成功（{len(response.content) / 1024:.1f} KB）")
                return response.content
        except retry_excs as exc:
            last_exc = exc
            _log(f"  第 {attempt} 次失败: {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                # Exponential backoff: 2, 4, 8, 16, 30 seconds (capped)
                wait_seconds = min(2 ** attempt, 30)
                _log(f"  等待 {wait_seconds}s 后重试...")
                time.sleep(wait_seconds)
        except Exception as exc:
            # Non-retryable error (e.g. 4xx HTTP status)
            last_exc = exc
            _log(f"  不可重试错误: {type(exc).__name__}: {exc}")
            break

    message = (
        f"下载 MinerU 结果 ZIP 失败（已重试 {max_attempts} 次）。"
        f" 目标主机={parsed.netloc or 'unknown'}，"
        "请检查网络连通性或稍后重试。"
    )
    _log(f"  {message}")
    raise RuntimeError(message) from last_exc


def _import_fitz():
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF 分片解析需要 PyMuPDF，请先安装 requirements.txt 中的 pymupdf。"
        ) from exc
    return fitz


def _get_sharding_config(config: dict) -> dict[str, int | float | bool]:
    sharding = config.get("parser", {}).get("cloud", {}).get("sharding", {})
    if not isinstance(sharding, dict):
        sharding = {}
    pages_per_shard = max(1, int(sharding.get("pages_per_shard", 40)))
    max_concurrency = max(1, int(sharding.get("max_concurrency", 2)))
    return {
        "enabled": bool(sharding.get("enabled", True)),
        "min_pages": max(1, int(sharding.get("min_pages", 120))),
        "min_file_mb": max(1.0, float(sharding.get("min_file_mb", 80))),
        "pages_per_shard": pages_per_shard,
        "max_concurrency": max_concurrency,
        "text_sample_pages": max(0, int(sharding.get("text_sample_pages", 5))),
    }


def _is_sharding_enabled(config: dict) -> bool:
    return bool(_get_sharding_config(config)["enabled"])


def _should_parse_with_shards(inspection: PdfInspection, config: dict) -> bool:
    sharding_cfg = _get_sharding_config(config)
    if not sharding_cfg["enabled"]:
        return False
    if inspection.page_count <= int(sharding_cfg["pages_per_shard"]):
        return False
    return (
        inspection.page_count >= int(sharding_cfg["min_pages"])
        or inspection.file_size_mb >= float(sharding_cfg["min_file_mb"])
    )


def _log_pdf_inspection(
    inspection: PdfInspection,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    if not log_fn:
        return
    scanned_text = "是" if inspection.likely_scanned else "否"
    log_fn(
        "PDF 体检："
        f"{inspection.page_count} 页，{inspection.file_size_mb:.1f} MB，"
        f"采样 {inspection.sampled_pages} 页文本 {inspection.sampled_text_chars} 字符，"
        f"疑似扫描型={scanned_text}"
    )


def _parse_pdf_shard(
    shard: PdfShard,
    output_path: Path,
    source_name: str,
    total_shards: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> list[ShardBlockRecord]:
    prefix = f"[shard {shard.index:03d}/{total_shards:03d} {shard.display_range}]"

    def shard_log(message: str) -> None:
        if log_fn:
            log_fn(f"{prefix} {message}")

    shard_log(f"开始上传与解析：{shard.path.name}，{shard.page_count} 页")
    source_path = Path(source_name)
    shard_upload_name = f"{source_path.stem}-shard-{shard.index:03d}{source_path.suffix or '.pdf'}"
    pdf_url = upload_pdf_to_oss(
        str(shard.path),
        log_fn=shard_log,
        original_name=shard_upload_name,
    )
    shard_output_dir = output_path / "shards" / f"shard_{shard.index:03d}"
    blocks = parse_pdf_from_url(
        pdf_url,
        pdf_path=str(shard.path),
        output_dir=str(shard_output_dir),
        log_fn=shard_log,
        original_name=source_name,
    )

    records = offset_shard_blocks(blocks, shard, source_name)
    shard_log(f"解析完成，局部内容块 {len(blocks)} 个，已应用页码 offset={shard.start_page}")
    return records


def _extract_and_map(zip_bytes: bytes, source_file: str, output_path: Path) -> list[ContentBlock]:
    images_dir = output_path / "images"
    if images_dir.exists() and not images_dir.is_dir():
        images_dir.unlink()
    images_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        content_list_name = next((name for name in zf.namelist() if name.endswith("_content_list.json")), None)
        if content_list_name is None:
            raise RuntimeError("Result zip does not contain a *_content_list.json file")

        content_list: list[dict] = json.loads(zf.read(content_list_name))

        for image_name in (name for name in zf.namelist() if name.startswith("images/")):
            if image_name.endswith("/"):
                continue
            destination = output_path / image_name
            if destination.parent.exists() and not destination.parent.is_dir():
                destination.parent.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zf.read(image_name))

    return _convert_content_list(content_list, source_file, output_path)


def _convert_content_list(
    content_list: list[dict],
    source_file: str,
    output_path: Path,
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []

    for item in content_list:
        raw_type = item.get("type", "text")
        if raw_type == "page_number":
            continue

        block_type = _map_category(raw_type)
        text = item.get("text", "")
        page_idx = int(item.get("page_idx", 0))
        text_level: Optional[int] = item.get("text_level")
        is_table = block_type == BlockType.TABLE
        table_html: Optional[str] = None
        image_path: Optional[str] = None

        raw_bbox = item.get("bbox")
        bbox: Optional[list[float]] = list(raw_bbox) if raw_bbox and len(raw_bbox) == 4 else None

        if is_table:
            table_html = item.get("table_body") or text or ""

        if block_type == BlockType.IMAGE:
            image_relative_path = item.get("img_path")
            if image_relative_path:
                image_path = str(output_path / image_relative_path)

        blocks.append(
            ContentBlock(
                type=block_type,
                text=text,
                page_idx=page_idx,
                text_level=text_level,
                is_table=is_table,
                table_html=table_html,
                source_file=source_file,
                image_path=image_path,
                bbox=bbox,
            )
        )

    return blocks


def _map_category(category: str) -> BlockType:
    """Map content_list type string to BlockType."""
    mapping = {
        "text": BlockType.TEXT,
        "header": BlockType.TITLE,
        "title": BlockType.TITLE,
        "table": BlockType.TABLE,
        "image": BlockType.IMAGE,
        "interline_equation": BlockType.TEXT,
    }
    return mapping.get(category, BlockType.TEXT)


def _convert_to_blocks(content_list: list[dict], source_file: str) -> list[ContentBlock]:
    """Convert raw content_list format to ContentBlock list for legacy compatibility."""
    return _convert_content_list(content_list, source_file, Path("data/output"))


def _map_type(raw_type: str) -> BlockType:
    """Map legacy type string to BlockType enum for legacy compatibility."""
    return _map_category(raw_type)
