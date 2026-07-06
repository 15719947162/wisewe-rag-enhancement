"""
MinerU 云端 PDF 解析器
=======================

本模块实现基于 302.ai MinerU 云端服务的 PDF 解析功能，将 PDF 文档转换为结构化的 ContentBlock 列表。

## 核心流程概述

整个解析过程分为三个主要步骤：

1. **OSS 上传** (upload_pdf_to_oss)
   - 将本地 PDF 文件上传到阿里云 OSS
   - 生成带签名的临时访问 URL
   - 签名 URL 有效期可配置（默认 1 小时）

2. **MinerU 云端解析** (parse_pdf_from_url)
   - 向 302.ai 提交解析任务
   - 轮询任务状态直到完成
   - 下载解析结果 ZIP 文件

3. **结果提取与转换** (_extract_and_map)
   - 解压 ZIP 文件
   - 提取 `*_content_list.json` 和图片
   - 转换为统一的 ContentBlock 格式

## 分片解析机制（大文件优化）

对于大型 PDF（默认 ≥120 页 或 ≥80 MB），启用分片解析：

1. 使用 PyMuPDF 将 PDF 按页分片（默认每片 40 页）
2. 并发提交多个 MinerU 任务
3. 合并各分片的解析结果
4. 自动修正页码映射

分片配置在 config.yaml 的 parser.cloud.sharding 节点。

## 完整调用示例

```python
from core.parser.mineru_parser import parse_pdf, upload_pdf_to_oss, parse_pdf_from_url

# 方式一：一站式解析（推荐）
blocks = parse_pdf(
    pdf_path="data/input/document.pdf",
    output_dir="data/output",
    log_fn=print,
    original_name="我的文档.pdf"
)

# 方式二：分步调用（适用于已有 OSS URL 的场景）
pdf_url = upload_pdf_to_oss("data/input/document.pdf", log_fn=print)
blocks = parse_pdf_from_url(
    pdf_url,
    pdf_path="data/input/document.pdf",
    output_dir="data/output",
    log_fn=print
)

# 处理解析结果
for block in blocks:
    print(f"页码 {block.page_idx}: {block.type.value} - {block.text[:50]}...")
    if block.is_table:
        print(f"  表格 HTML: {len(block.table_html)} 字符")
    if block.image_path:
        print(f"  图片路径: {block.image_path}")
```

## 环境变量要求

- `302AI_API_KEY`: 302.ai 平台 API 密钥（必需）
- `302AI_API_BASE`: 302.ai API 基础地址（必需）
- `OSS_ACCESS_KEY_ID`: 阿里云 OSS AccessKey ID
- `OSS_ACCESS_KEY_SECRET`: 阿里云 OSS AccessKey Secret
- `OSS_ENDPOINT`: OSS 端点地址
- `OSS_BUCKET`: OSS 存储桶名称

## 输出结构

解析后的文件结构：
```
data/output/
├── images/                    # 提取的图片
│   ├── image_001.jpg
│   └── image_002.png
└── shards/                    # 分片解析时的临时目录
    ├── shard_000/
    └── shard_001/
```

ContentBlock 字段说明：
- `type`: 内容类型（TEXT/TITLE/TABLE/IMAGE）
- `text`: 文本内容
- `page_idx`: 页码（从 0 开始）
- `text_level`: 标题层级（仅标题块有效）
- `is_table`: 是否为表格
- `table_html`: 表格的 HTML 表示
- `image_path`: 图片的本地路径
- `bbox`: 边界框坐标 [x0, y0, x1, y1]
- `source_file`: 源文件名

## 错误处理

- 网络错误：自动重试（可配置次数）
- 解析超时：默认 1 小时，可在 config.yaml 调整
- 任务失败：抛出 RuntimeError，包含错误详情
"""

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
    """
    解析 PDF 文件的统一入口（一站式解析）。

    这是本模块最主要的公共 API，封装了完整的解析流程。
    会自动判断是否启用分片解析，适用于绝大多数场景。

    ## 工作流程

    1. 加载配置，检查是否启用分片解析
    2. 如果启用分片，检查 PDF 是否满足分片条件
    3. 满足条件 → 调用 parse_pdf_sharded() 进行分片解析
    4. 不满足条件 → 调用 upload_pdf_to_oss() + parse_pdf_from_url() 进行单任务解析
    5. 返回统一的 ContentBlock 列表

    ## 分片判断条件

    同时满足以下条件才启用分片：
    - 配置中 sharding.enabled = true（默认启用）
    - PDF 页数 > pages_per_shard（默认 40 页）
    - PDF 页数 ≥ min_pages（默认 120 页）或 文件大小 ≥ min_file_mb（默认 80 MB）

    ## 参数说明

    Args:
        pdf_path: PDF 文件的本地路径（绝对路径或相对路径）
        output_dir: 解析结果的输出目录，默认 "data/output"
            - images/ 子目录存放提取的图片
            - shards/ 子目录存放分片解析的临时文件
        log_fn: 日志回调函数，用于输出解析进度信息
            - 接收一个字符串参数
            - 例如: `log_fn=print` 或 `log_fn=lambda msg: logger.info(msg)`
            - 传入 None 则不输出日志
        original_name: 原始文件名（可选）
            - 用于在 ContentBlock.source_file 中记录来源
            - 不传入则使用 pdf_path 的文件名

    Returns:
        list[ContentBlock]: 解析后的内容块列表
            - 按页码顺序排列
            - 包含文本、标题、表格、图片等多种类型
            - 每个块包含页码、类型、内容等元数据

    Raises:
        FileNotFoundError: PDF 文件不存在
        ValueError: 缺少必要的环境变量（302AI_API_KEY 等）
        RuntimeError: MinerU 任务失败
        TimeoutError: 解析超时（默认 3600 秒）

    ## 使用示例

    ```python
    # 基础用法
    blocks = parse_pdf("data/input/report.pdf", log_fn=print)

    # 指定输出目录和原始文件名
    blocks = parse_pdf(
        pdf_path="/path/to/document.pdf",
        output_dir="custom/output",
        original_name="2024年度报告.pdf",
        log_fn=lambda msg: print(f"[解析] {msg}")
    )

    # 处理返回结果
    for block in blocks:
        if block.type == BlockType.TABLE:
            print(f"第 {block.page_idx + 1} 页发现表格")
            print(block.table_html)
    ```

    ## 配置参考

    config.yaml 相关配置：
    ```yaml
    parser:
      mode: cloud
      cloud:
        timeout: 3600           # 单任务超时（秒）
        poll_interval: 3        # 轮询间隔（秒）
        sharding:
          enabled: true         # 是否启用分片
          min_pages: 120        # 最小页数阈值
          min_file_mb: 80       # 最小文件大小阈值（MB）
          pages_per_shard: 40   # 每片页数
          max_concurrency: 2    # 最大并发数
    ```
    """
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
    """
    使用分片策略解析大型 PDF 文件。

    将大文件拆分为多个分片（shard），并发提交到 MinerU 云端解析，
    然后合并结果并修正页码映射。

    ## 适用场景

    - 大型 PDF（≥120 页 或 ≥80 MB，可在配置中调整）
    - 需要加速解析的场景（并发处理）
    - 单个 MinerU 任务可能超时的场景

    ## 分片策略

    1. 使用 PyMuPDF 将 PDF 按页拆分为多个小文件
    2. 每个分片默认包含 40 页（可配置）
    3. 分片文件保存到临时目录
    4. 并发上传和解析各分片（默认并发数 2）
    5. 合并结果，修正页码为原 PDF 的全局页码

    ## 页码修正机制

    每个分片解析出的 ContentBlock.page_idx 是分片内的相对页码。
    合并时会加上分片的起始页码偏移量，转换为原 PDF 的全局页码。

    例如：
    - shard_0: 第 0-39 页 → page_idx 不变
    - shard_1: 第 40-79 页 → page_idx += 40
    - shard_2: 第 80-119 页 → page_idx += 80

    ## 参数说明

    Args:
        pdf_path: PDF 文件的本地路径
        output_dir: 解析结果的输出目录
        log_fn: 日志回调函数
        original_name: 原始文件名
        inspection: PDF 体检结果（可选，避免重复检查）
        config: 配置字典（可选，避免重复加载）

    Returns:
        list[ContentBlock]: 合并后的内容块列表，页码已修正为全局页码

    Raises:
        RuntimeError: 任一分片解析失败
        ValueError: 配置错误或环境变量缺失

    ## 日志输出示例

    ```
    启用 MinerU 分片解析：200 页，95.3 MB，5 个 shard，每片最多 40 页，并发 2
    [shard 000/005] 开始上传与解析：shard-000.pdf，40 页
    [shard 001/005] 开始上传与解析：shard-001.pdf，40 页
    [shard 000/005] 合并完成，输出 120 个内容块
    [shard 001/005] 合并完成，输出 135 个内容块
    ...
    分片解析合并完成，共 600 个内容块，页码已回写为原 PDF 全局页码
    ```

    ## 性能优化建议

    - 增大 max_concurrency 可加速解析（但会增加 API 调用成本）
    - pages_per_shard 太小会导致分片过多，管理开销增大
    - 对于扫描型 PDF（图片为主），可适当增大分片大小
    """
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
        # 步骤 1: 将 PDF 拆分为分片
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

        # 步骤 2: 并发提交和解析各分片
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

            # 步骤 3: 收集各分片的解析结果
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

        # 步骤 4: 合并所有分片的结果，修正页码
        blocks = merge_shard_records(records)
        _log(f"分片解析合并完成，共 {len(blocks)} 个内容块，页码已回写为原 PDF 全局页码")
        return blocks


def upload_pdf_to_oss(
    pdf_path: str,
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> str:
    """
    将 PDF 文件上传到阿里云 OSS，返回签名 URL。

    这是解析流程的第一步，为 MinerU 云端解析提供可访问的文件地址。

    ## OSS 上传流程

    1. 检查 PDF 文件是否存在
    2. 加载 OSS 配置（从 config.yaml 和环境变量）
    3. 调用 oss_uploader.upload_to_oss() 上传文件
    4. 生成带签名的临时访问 URL
    5. 返回签名 URL 供 MinerU 服务访问

    ## 签名 URL 说明

    - 有效期：默认 1 小时，可在 config.yaml 配置
    - 权限：只读访问
    - 安全性：URL 包含签名参数，过期后无法访问

    ## 参数说明

    Args:
        pdf_path: PDF 文件的本地路径
        log_fn: 日志回调函数
        original_name: 原始文件名（可选）
            - 用于 OSS 存储路径中的标识
            - 不传入则使用 pdf_path 的文件名

    Returns:
        str: OSS 签名 URL
            - 格式: https://bucket.oss-region.aliyuncs.com/path/file.pdf?OSSAccessKeyId=...&Signature=...
            - 有效期由配置决定（默认 3600 秒）

    Raises:
        FileNotFoundError: PDF 文件不存在
        ValueError: 缺少 OSS 环境变量（OSS_ACCESS_KEY_ID 等）
        RuntimeError: OSS 上传失败

    ## 使用示例

    ```python
    # 上传并获取签名 URL
    pdf_url = upload_pdf_to_oss("data/input/report.pdf", log_fn=print)
    print(f"签名 URL: {pdf_url}")

    # 指定原始文件名
    pdf_url = upload_pdf_to_oss(
        "/tmp/upload.pdf",
        original_name="2024年度报告.pdf",
        log_fn=lambda msg: logger.info(msg)
    )
    ```

    ## 配置参考

    config.yaml 相关配置：
    ```yaml
    parser:
      oss:
        prefix: mineru/           # OSS 路径前缀
        url_expiry: 3600          # 签名 URL 有效期（秒）
    ```

    环境变量：
    - OSS_ACCESS_KEY_ID: 阿里云 AccessKey ID
    - OSS_ACCESS_KEY_SECRET: 阿里云 AccessKey Secret
    - OSS_ENDPOINT: OSS 端点（如 oss-cn-hangzhou.aliyuncs.com）
    - OSS_BUCKET: 存储桶名称

    ## 注意事项

    - 确保 OSS 存储桶已开通公网访问权限
    - 签名 URL 在有效期内可被任何人访问，请妥善保管
    - 上传大文件时可能需要较长时间，建议使用进度回调
    """

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
    """
    解析已在 OSS 上的 PDF 文件（通过签名 URL）。

    这是解析流程的核心函数，负责提交 MinerU 任务、轮询状态、下载结果。
    适用于已有 OSS URL 的场景（如分片解析）。

    ## 完整解析流程

    ### 步骤 1: 提交解析任务 (_submit_task)

    向 302.ai 提交 MinerU 解析任务：

    - API 端点: `{302AI_API_BASE}/302/v2/mineru/task`
    - 请求方法: POST
    - 请求体:
        ```json
        {
            "pdf_url": "https://bucket.oss.aliyuncs.com/...",
            "parse_method": "auto",
            "version": "2.5",
            "enable_formula": true,
            "enable_table_html": true,
            "language": "ch",
            "is_ocr": false,
            "model_version": "v2"
        }
        ```
    - 返回值: `task_id`（任务唯一标识）

    ### 步骤 2: 轮询任务状态 (_poll_task)

    周期性查询任务状态，直到完成或超时：

    - API 端点: `{302AI_API_BASE}/302/v2/mineru/task?task_id={task_id}`
    - 请求方法: GET
    - 轮询间隔: 默认 3 秒（指数退避，最长 15 秒）
    - 超时时间: 默认 3600 秒（1 小时）

    任务状态：
    - `INIT`: 任务初始化中
    - `PROCESSING`: 正在解析
    - `SUCCESS`: 解析成功，返回 `result_url`
    - `FAILED`: 解析失败，返回 `err_msg`

    ### 步骤 3: 下载并解析结果 (_extract_and_map)

    从 result_url 下载 ZIP 文件，提取内容：

    - ZIP 结构:
        ```
        result.zip
        ├── xxx_content_list.json    # 内容列表（主要解析结果）
        └── images/                  # 提取的图片
            ├── image_001.jpg
            └── image_002.png
        ```
    - 解析 JSON 为 ContentBlock 列表
    - 保存图片到 output_dir/images/

    ## 参数说明

    Args:
        pdf_url: OSS 签名 URL（由 upload_pdf_to_oss 返回）
        pdf_path: 原 PDF 文件路径（可选，用于日志和元数据）
        output_dir: 解析结果输出目录
        log_fn: 日志回调函数
        original_name: 原始文件名（可选）

    Returns:
        list[ContentBlock]: 解析后的内容块列表

    Raises:
        ValueError: 缺少环境变量（302AI_API_KEY、302AI_API_BASE）
        RuntimeError: MinerU 任务失败
        TimeoutError: 解析超时

    ## 使用示例

    ```python
    # 已有 OSS URL 的场景
    pdf_url = "https://bucket.oss.aliyuncs.com/path/file.pdf?签名参数..."
    blocks = parse_pdf_from_url(
        pdf_url,
        pdf_path="data/input/file.pdf",
        output_dir="data/output",
        log_fn=print
    )
    ```

    ## 日志输出示例

    ```
    步骤 1/3：提交 302.ai MinerU 解析任务...
    任务已提交，task_id=abc123def456
    步骤 2/3：轮询任务状态...
      轮询 #1 状态=INIT 已等待=0s
      轮询 #2 状态=PROCESSING 已等待=3s
      轮询 #3 状态=PROCESSING 已等待=6s
      ...
      轮询 #45 状态=SUCCESS 已等待=135s
    解析完成，结果地址已返回：scheme=https host=oss.aliyuncs.com path=/result.zip
    步骤 3/3：下载并解析结果 ZIP...
    ZIP 下载完成（2048.5 KB），开始提取内容块...
    提取完成，共 156 个内容块
    ```

    ## 配置参考

    config.yaml 相关配置：
    ```yaml
    parser:
      cloud:
        parse_method: auto       # 解析方法：auto/txt/ocr
        version: "2.5"           # MinerU 版本
        enable_formula: true     # 启用公式识别
        enable_table_html: true  # 启用表格 HTML 输出
        language: ch             # 文档语言：ch/en
        is_ocr: false            # 强制 OCR 模式
        model_version: v2        # 模型版本
        timeout: 3600            # 超时时间（秒）
        poll_interval: 3         # 轮询间隔（秒）
        submit_retry_attempts: 3 # 提交重试次数
        poll_retry_attempts: 5   # 轮询网络错误重试次数
    ```
    """

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
    """
    向 302.ai 提交 MinerU 解析任务。

    这是 MinerU 云端解析的第一步，将 PDF URL 提交给云端服务进行解析。

    ## 请求详情

    - 端点: `{302AI_API_BASE}/302/v2/mineru/task`
    - 方法: POST
    - 认证: Bearer Token（通过 302AI_API_KEY）

    ## 请求参数说明

    | 参数 | 类型 | 默认值 | 说明 |
    |------|------|--------|------|
    | pdf_url | str | (必需) | OSS 签名 URL |
    | parse_method | str | "auto" | 解析方法：auto（自动）/ txt（文本）/ ocr（OCR） |
    | version | str | "2.5" | MinerU 版本 |
    | enable_formula | bool | true | 启用公式识别 |
    | enable_table_html | bool | true | 启用表格 HTML 输出 |
    | language | str | "ch" | 文档语言：ch（中文）/ en（英文） |
    | is_ocr | bool | false | 强制使用 OCR 模式 |
    | model_version | str | "v2" | 模型版本 |

    ## 响应格式

    成功响应：
    ```json
    {
        "data": {
            "task_id": "abc123def456"
        }
    }
    ```

    ## 错误处理

    - 网络错误：自动重试（最多 3 次）
    - 4xx 错误：不重试，直接抛出异常
    - 5xx 错误：自动重试

    重试策略：
    - 间隔：指数退避（2s, 4s, 8s，上限 8s）
    - 重试异常类型：ConnectError, ConnectTimeout, ReadTimeout,
                   WriteTimeout, PoolTimeout, RemoteProtocolError,
                   SSLError, OSError

    Args:
        pdf_url: OSS 签名 URL
        config: 配置字典
        log_fn: 日志回调函数

    Returns:
        str: 任务 ID（task_id）

    Raises:
        ValueError: 缺少环境变量 302AI_API_KEY 或 302AI_API_BASE
        RuntimeError: 提交失败（所有重试耗尽）

    ## 使用示例

    ```python
    config = load_config()
    task_id = _submit_task(
        "https://bucket.oss.aliyuncs.com/path/file.pdf?signature=...",
        config,
        log_fn=print
    )
    print(f"任务已提交: {task_id}")
    ```
    """
    api_key = os.getenv("302AI_API_KEY", "")
    if not api_key:
        raise ValueError("302AI_API_KEY environment variable is not set")

    api_base = os.getenv("302AI_API_BASE", "")
    if not api_base:
        raise ValueError("302AI_API_BASE environment variable is not set")

    # 从配置中获取解析参数
    cloud_cfg = config.get("parser", {}).get("cloud", {})
    parse_method = cloud_cfg.get("parse_method", "auto")
    version = cloud_cfg.get("version", "2.5")
    enable_formula = bool(cloud_cfg.get("enable_formula", True))
    enable_table_html = bool(cloud_cfg.get("enable_table_html", True))
    language = str(cloud_cfg.get("language", "ch"))
    is_ocr = bool(cloud_cfg.get("is_ocr", False))
    model_version = str(cloud_cfg.get("model_version", "v2"))

    # 构建请求
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

    # 带重试的请求发送
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

    # 提取 task_id
    task_id = data.get("data", {}).get("task_id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"302.ai did not return a task_id: {data}")
    return task_id


def _poll_task(
    task_id: str,
    config: dict,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    """
    轮询 MinerU 任务状态，直到完成或超时。

    这是 MinerU 解析流程中最耗时的部分，通过周期性查询任务状态来获取解析进度。

    ## 轮询机制详解

    ### 基本流程

    1. 发送 GET 请求查询任务状态
    2. 根据状态决定下一步操作：
       - `INIT` 或 `PROCESSING`: 继续等待
       - `SUCCESS`: 返回 result_url
       - `FAILED`: 抛出异常
    3. 如果未完成，等待一段时间后重复步骤 1

    ### 指数退避策略

    为了避免频繁请求，采用指数退避算法：

    - 初始间隔: 3 秒（poll_interval）
    - 每次轮询后间隔 * 1.5
    - 最大间隔: 15 秒
    - 计算公式: `interval = min(interval * 1.5, 15.0)`

    例如：
    ```
    轮询 #1: 等待 3s
    轮询 #2: 等待 4.5s
    轮询 #3: 等待 6.75s
    轮询 #4: 等待 10.125s
    轮询 #5+: 等待 15s（已达上限）
    ```

    ### 超时处理

    - 默认超时: 3600 秒（1 小时）
    - 使用单调时钟（time.monotonic）避免系统时间调整的影响
    - 超时后抛出 TimeoutError

    ### 网络错误处理

    - 遇到网络错误时继续重试
    - 最多允许连续 5 次网络错误
    - 超过限制后抛出 RuntimeError

    ## 任务状态说明

    | 状态 | 含义 | 处理方式 |
    |------|------|----------|
    | INIT | 任务初始化中 | 继续轮询 |
    | PROCESSING | 正在解析 | 继续轮询 |
    | SUCCESS | 解析成功 | 返回 result_url |
    | FAILED | 解析失败 | 抛出 RuntimeError |

    ## 请求详情

    - 端点: `{302AI_API_BASE}/302/v2/mineru/task?task_id={task_id}`
    - 方法: GET
    - 认证: Bearer Token

    ## 响应格式

    进行中：
    ```json
    {
        "data": {
            "state": "PROCESSING"
        }
    }
    ```

    成功：
    ```json
    {
        "data": {
            "state": "SUCCESS",
            "result_url": "https://oss.aliyuncs.com/result.zip?签名..."
        }
    }
    ```

    失败：
    ```json
    {
        "data": {
            "state": "FAILED",
            "err_msg": "解析失败原因"
        }
    }
    ```

    Args:
        task_id: 任务 ID（由 _submit_task 返回）
        config: 配置字典
        log_fn: 日志回调函数

    Returns:
        str: 结果 ZIP 文件的下载 URL（result_url）

    Raises:
        TimeoutError: 轮询超时
        RuntimeError: 任务失败或网络错误次数超限

    ## 日志输出示例

    ```
      轮询 #1 状态=INIT 已等待=0s
      轮询 #2 状态=PROCESSING 已等待=3s
      轮询 #3 状态=PROCESSING 已等待=7s
      ...
      轮询 #45 状态=SUCCESS 已等待=135s
    ```
    """
    api_key = os.getenv("302AI_API_KEY", "")
    api_base = os.getenv("302AI_API_BASE", "")
    if not api_base:
        raise ValueError("302AI_API_BASE environment variable is not set")

    # 从配置中获取轮询参数
    cloud_cfg = config.get("parser", {}).get("cloud", {})
    timeout = int(cloud_cfg.get("timeout", 3600))
    poll_interval = float(cloud_cfg.get("poll_interval", 3))
    max_network_errors = max(int(cloud_cfg.get("poll_retry_attempts", 5)), 1)

    url = f"{api_base}/302/v2/mineru/task"
    headers = {"Authorization": f"Bearer {api_key}"}

    # 初始化轮询状态
    deadline = time.monotonic() + timeout
    interval = poll_interval
    poll_count = 0
    consecutive_network_errors = 0

    while True:
        # 发送轮询请求
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

        # 记录日志
        if log_fn:
            elapsed = timeout - (deadline - time.monotonic())
            log_fn(f"  轮询 #{poll_count} 状态={state} 已等待={elapsed:.0f}s")

        # 处理不同状态
        if state == "SUCCESS":
            result_url = data.get("result_url")
            if not result_url:
                raise RuntimeError(f"Task {task_id} succeeded but result_url is missing")
            return result_url

        if state == "FAILED":
            err = data.get("err_msg", "unknown error")
            raise RuntimeError(f"302.ai task {task_id} failed: {err}")

        # 检查超时
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Polling timed out after {timeout}s for task_id={task_id}")

        # 等待下一次轮询
        time.sleep(min(interval, max(0.1, deadline - time.monotonic())))
        interval = min(interval * 1.5, 15.0)


def _retryable_http_exceptions() -> tuple[type[BaseException], ...]:
    """
    返回可重试的 HTTP 异常类型元组。

    这些异常通常由网络问题引起，重试后可能成功。

    包含的异常类型：
    - httpx.ConnectError: 连接错误
    - httpx.ConnectTimeout: 连接超时
    - httpx.ReadTimeout: 读取超时
    - httpx.WriteTimeout: 写入超时
    - httpx.PoolTimeout: 连接池超时
    - httpx.RemoteProtocolError: 远程协议错误
    - ssl.SSLError: SSL 错误
    - OSError: 操作系统错误（网络相关的）

    Returns:
        tuple: 可重试的异常类型元组
    """
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
    """
    下载 MinerU 解析结果 ZIP 文件。

    从 result_url 下载包含解析结果的 ZIP 文件，支持重试和详细诊断。

    ## 下载策略

    ### 重试机制

    - 最大重试次数: 6 次
    - 重试间隔: 指数退避（2, 4, 8, 16, 30 秒）
    - 可重试的异常: 网络相关异常（ConnectError, Timeout, SSLError 等）
    - 不可重试: HTTP 4xx 错误（客户端错误）

    ### 超时设置

    - 连接超时: 30 秒
    - 读取超时: 300 秒（5 分钟，大文件需要更长时间）
    - 写入超时: 60 秒
    - 连接池超时: 30 秒

    ## 参数说明

    Args:
        result_url: 结果 ZIP 文件的下载 URL（由 _poll_task 返回）
        log_fn: 日志回调函数

    Returns:
        bytes: ZIP 文件的二进制内容

    Raises:
        RuntimeError: 下载失败（所有重试耗尽或遇到不可重试错误）

    ## 日志输出示例

    ```
    准备下载结果 ZIP：scheme=https host=oss.aliyuncs.com path=/result.zip
      下载尝试 #1: host=oss.aliyuncs.com
      下载成功（2048.5 KB）
    ```

    网络错误时的重试日志：
    ```
      下载尝试 #1: host=oss.aliyuncs.com
      第 1 次失败: ConnectError: Connection refused
      等待 2s 后重试...
      下载尝试 #2: host=oss.aliyuncs.com
      下载成功（2048.5 KB）
    ```
    """
    import ssl as _ssl

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    parsed = urlparse(result_url)
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)

    # 可重试的异常类型
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
                # 指数退避: 2, 4, 8, 16, 30 秒（有上限）
                wait_seconds = min(2 ** attempt, 30)
                _log(f"  等待 {wait_seconds}s 后重试...")
                time.sleep(wait_seconds)
        except Exception as exc:
            # 不可重试的错误（如 4xx HTTP 状态码）
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
    """
    导入 PyMuPDF（fitz）模块。

    用于分片解析时拆分 PDF 文件。如果未安装 PyMuPDF，抛出 RuntimeError。

    PyMuPDF 是一个高性能的 PDF 处理库，支持：
    - PDF 文件拆分
    - 页面提取
    - 文本提取
    - PDF 合并

    Returns:
        module: PyMuPDF 模块对象

    Raises:
        RuntimeError: 未安装 PyMuPDF

    ## 安装方法

    ```bash
    pip install pymupdf
    ```
    """
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF 分片解析需要 PyMuPDF，请先安装 requirements.txt 中的 pymupdf。"
        ) from exc
    return fitz


def _get_sharding_config(config: dict) -> dict[str, int | float | bool]:
    """
    获取分片解析的配置参数。

    从 config.yaml 的 parser.cloud.sharding 节点读取配置，
    并设置默认值和边界约束。

    ## 配置参数

    | 参数 | 默认值 | 说明 |
    |------|--------|------|
    | enabled | true | 是否启用分片解析 |
    | min_pages | 120 | 最小页数阈值（页数 ≥ 此值才分片） |
    | min_file_mb | 80 | 最小文件大小阈值（MB） |
    | pages_per_shard | 40 | 每个分片的页数 |
    | max_concurrency | 2 | 最大并发解析数 |
    | text_sample_pages | 5 | 文本采样页数（用于 PDF 体检） |

    ## 参数验证

    - 所有数值参数都有最小值约束（≥1 或 ≥1.0）
    - pages_per_shard 必须 ≥ 1
    - max_concurrency 必须 ≥ 1 且 ≤ 分片数

    Args:
        config: 配置字典（由 load_config() 返回）

    Returns:
        dict: 分片配置字典，包含上述参数

    ## 示例

    ```python
    config = load_config()
    sharding_cfg = _get_sharding_config(config)
    # {
    #     'enabled': True,
    #     'min_pages': 120,
    #     'min_file_mb': 80.0,
    #     'pages_per_shard': 40,
    #     'max_concurrency': 2,
    #     'text_sample_pages': 5
    # }
    ```
    """
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
    """
    检查是否启用分片解析功能。

    Args:
        config: 配置字典

    Returns:
        bool: 是否启用分片解析

    ## 配置示例

    ```yaml
    parser:
      cloud:
        sharding:
          enabled: true  # 启用分片
    ```
    """
    return bool(_get_sharding_config(config)["enabled"])


def _should_parse_with_shards(inspection: PdfInspection, config: dict) -> bool:
    """
    判断是否应该使用分片解析。

    根据 PDF 的体检结果和配置阈值决定是否启用分片。

    ## 判断逻辑

    1. 分片功能必须启用（enabled = true）
    2. PDF 页数必须 > pages_per_shard（否则没必要分片）
    3. 满足以下条件之一：
       - 页数 ≥ min_pages
       - 文件大小 ≥ min_file_mb

    ## 参数说明

    Args:
        inspection: PDF 体检结果（由 inspect_pdf() 返回）
            - page_count: 总页数
            - file_size_mb: 文件大小（MB）
            - likely_scanned: 是否疑似扫描型
            - sampled_text_chars: 采样文本字符数
        config: 配置字典

    Returns:
        bool: 是否应该使用分片解析

    ## 示例

    ```python
    inspection = inspect_pdf("large.pdf")
    if _should_parse_with_shards(inspection, config):
        print(f"PDF 有 {inspection.page_count} 页，启用分片解析")
    else:
        print("使用单任务解析")
    ```
    """
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
    """
    输出 PDF 体检结果的日志。

    PDF 体检用于判断文件是否适合分片解析，以及是否为扫描型文档。

    ## 输出内容

    - 总页数
    - 文件大小（MB）
    - 采样页数
    - 采样文本字符数
    - 是否疑似扫描型文档

    Args:
        inspection: PDF 体检结果
        log_fn: 日志回调函数

    ## 日志示例

    ```
    PDF 体检：200 页，95.3 MB，采样 5 页文本 1500 字符，疑似扫描型=否
    PDF 体检：50 页，120.5 MB，采样 5 页文本 50 字符，疑似扫描型=是
    ```

    ## 扫描型文档判断

    如果采样页的平均文本字符数很少（< 100 字符/页），
    则判断为扫描型文档（以图片为主，文字较少）。
    """
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
    """
    解析单个 PDF 分片。

    这是分片解析的核心执行函数，处理一个分片的完整解析流程：
    上传 → 提交任务 → 轮询 → 下载 → 转换 → 记录。

    ## 处理流程

    1. 为分片生成上传文件名（包含分片索引）
    2. 上传分片到 OSS
    3. 提交 MinerU 任务并解析
    4. 应用页码偏移量（将相对页码转为全局页码）
    5. 返回 ShardBlockRecord（包含解析结果和元数据）

    ## 页码偏移量

    每个分片的 page_idx 是相对于分片的起始页码。
    例如：
    - shard_0 (第 0-39 页): 偏移量 = 0
    - shard_1 (第 40-79 页): 偏移量 = 40
    - shard_2 (第 80-119 页): 偏移量 = 80

    Args:
        shard: 分片对象（由 split_pdf_to_shards() 生成）
            - path: 分片文件路径
            - index: 分片索引（从 0 开始）
            - start_page: 起始页码
            - end_page: 结束页码
            - page_count: 页数
            - display_range: 显示范围（如 "1-40"）
        output_path: 输出目录
        source_name: 原始 PDF 文件名
        total_shards: 总分片数
        log_fn: 日志回调函数

    Returns:
        list[ShardBlockRecord]: 分片解析记录列表
            - 包含解析后的 ContentBlock
            - 包含分片的元数据（索引、页码范围等）

    ## 日志示例

    ```
    [shard 000/005 1-40] 开始上传与解析：shard-000.pdf，40 页
    [shard 000/005 1-40] 上传 PDF 到阿里云 OSS...
    [shard 000/005 1-40] OSS 上传成功，签名地址主机: oss.aliyuncs.com
    [shard 000/005 1-40] 步骤 1/3：提交 302.ai MinerU 解析任务...
    ...
    [shard 000/005 1-40] 解析完成，局部内容块 120 个，已应用页码 offset=0
    ```
    """
    prefix = f"[shard {shard.index:03d}/{total_shards:03d} {shard.display_range}]"

    def shard_log(message: str) -> None:
        if log_fn:
            log_fn(f"{prefix} {message}")

    # 步骤 1: 上传分片到 OSS
    shard_log(f"开始上传与解析：{shard.path.name}，{shard.page_count} 页")
    source_path = Path(source_name)
    shard_upload_name = f"{source_path.stem}-shard-{shard.index:03d}{source_path.suffix or '.pdf'}"
    pdf_url = upload_pdf_to_oss(
        str(shard.path),
        log_fn=shard_log,
        original_name=shard_upload_name,
    )

    # 步骤 2: 解析分片
    shard_output_dir = output_path / "shards" / f"shard_{shard.index:03d}"
    blocks = parse_pdf_from_url(
        pdf_url,
        pdf_path=str(shard.path),
        output_dir=str(shard_output_dir),
        log_fn=shard_log,
        original_name=source_name,
    )

    # 步骤 3: 应用页码偏移量
    records = offset_shard_blocks(blocks, shard, source_name)
    shard_log(f"解析完成，局部内容块 {len(blocks)} 个，已应用页码 offset={shard.start_page}")
    return records


def _extract_and_map(zip_bytes: bytes, source_file: str, output_path: Path) -> list[ContentBlock]:
    """
    从 ZIP 文件中提取内容并转换为 ContentBlock 列表。

    这是解析流程的最后一步，负责解压 ZIP 文件并提取结构化内容。

    ## ZIP 文件结构

    MinerU 返回的 ZIP 文件包含：

    ```
    result.zip
    ├── xxx_content_list.json    # 核心解析结果（JSON 格式）
    └── images/                  # 提取的图片文件
        ├── image_001.jpg
        ├── image_002.png
        └── ...
    ```

    ## 处理流程

    1. 创建输出目录（output_path/images/）
    2. 解压 ZIP 到内存
    3. 定位 *_content_list.json 文件
    4. 提取所有 images/ 下的图片到 output_path/images/
    5. 调用 _convert_content_list() 将 JSON 转换为 ContentBlock 列表

    ## 参数说明

    Args:
        zip_bytes: ZIP 文件的二进制内容（由 _download_zip 返回）
        source_file: 源文件名（用于 ContentBlock.source_file）
        output_path: 输出目录路径（用于保存图片）

    Returns:
        list[ContentBlock]: 转换后的内容块列表

    Raises:
        RuntimeError: ZIP 文件中找不到 *_content_list.json

    ## 示例

    ```python
    zip_bytes = _download_zip(result_url, log_fn=print)
    blocks = _extract_and_map(
        zip_bytes,
        source_file="report.pdf",
        output_path=Path("data/output")
    )
    ```

    ## 文件输出

    图片会被提取到：
    - `data/output/images/image_001.jpg`
    - `data/output/images/image_002.png`
    - ...

    这些图片路径会被记录在对应 ContentBlock 的 image_path 字段中。
    """
    # 创建图片输出目录
    images_dir = output_path / "images"
    if images_dir.exists() and not images_dir.is_dir():
        images_dir.unlink()
    images_dir.mkdir(parents=True, exist_ok=True)

    # 解压 ZIP 文件
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # 查找 content_list.json 文件
        content_list_name = next((name for name in zf.namelist() if name.endswith("_content_list.json")), None)
        if content_list_name is None:
            raise RuntimeError("Result zip does not contain a *_content_list.json file")

        # 读取并解析 JSON
        content_list: list[dict] = json.loads(zf.read(content_list_name))

        # 提取所有图片
        for image_name in (name for name in zf.namelist() if name.startswith("images/")):
            if image_name.endswith("/"):
                continue
            destination = output_path / image_name
            if destination.parent.exists() and not destination.parent.is_dir():
                destination.parent.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zf.read(image_name))

    # 转换为 ContentBlock 列表
    return _convert_content_list(content_list, source_file, output_path)


def _convert_content_list(
    content_list: list[dict],
    source_file: str,
    output_path: Path,
) -> list[ContentBlock]:
    """
    将 MinerU 的 content_list JSON 转换为 ContentBlock 对象列表。

    这是数据模型转换的核心函数，将 MinerU 的原始输出格式
    转换为本项目统一的 ContentBlock 格式。

    ## content_list 结构

    MinerU 返回的 content_list 是一个 JSON 数组，每个元素代表一个内容块：

    ```json
    [
        {
            "type": "text",
            "text": "这是一段普通文本...",
            "page_idx": 0,
            "bbox": [100, 200, 500, 250]
        },
        {
            "type": "header",
            "text": "第一章 简介",
            "page_idx": 0,
            "text_level": 1,
            "bbox": [100, 50, 500, 80]
        },
        {
            "type": "table",
            "text": "表格标题",
            "page_idx": 1,
            "table_body": "<table><tr><td>...</td></tr></table>",
            "bbox": [50, 100, 550, 400]
        },
        {
            "type": "image",
            "text": "图1: 示例图片",
            "page_idx": 2,
            "img_path": "images/image_001.jpg",
            "bbox": [100, 150, 400, 350]
        }
    ]
    ```

    ## 类型映射

    | MinerU type | BlockType | 说明 |
    |-------------|-----------|------|
    | text | TEXT | 普通文本 |
    | header | TITLE | 标题（含 text_level） |
    | title | TITLE | 标题（旧格式） |
    | table | TABLE | 表格（含 table_body） |
    | image | IMAGE | 图片（含 img_path） |
    | interline_equation | TEXT | 行间公式（作为文本处理） |

    ## 特殊处理

    ### 页码类型 (page_number)
    - 直接跳过，不生成 ContentBlock
    - 因为页码通常不需要单独处理

    ### 表格类型
    - `is_table` 设为 True
    - `table_html` 从 `table_body` 字段获取
    - 如果 `table_body` 为空，使用 `text` 字段

    ### 图片类型
    - `image_path` 设为完整本地路径
    - 路径格式: `{output_path}/images/image_xxx.jpg`

    ### 标题级别 (text_level)
    - 仅对标题类型有效
    - 值: 1, 2, 3... 表示一级、二级、三级标题
    - 其他类型为 None

    ## 参数说明

    Args:
        content_list: MinerU 返回的原始内容列表（JSON 反序列化后的字典列表）
        source_file: 源文件名（用于 ContentBlock.source_file）
        output_path: 输出目录路径（用于生成图片的完整路径）

    Returns:
        list[ContentBlock]: 转换后的内容块列表

    ## 示例

    ```python
    content_list = [
        {"type": "text", "text": "示例文本", "page_idx": 0},
        {"type": "header", "text": "标题", "page_idx": 0, "text_level": 1}
    ]
    blocks = _convert_content_list(
        content_list,
        source_file="report.pdf",
        output_path=Path("data/output")
    )
    # blocks[0].type == BlockType.TEXT
    # blocks[1].type == BlockType.TITLE
    # blocks[1].text_level == 1
    ```
    """
    blocks: list[ContentBlock] = []

    for item in content_list:
        # 获取原始类型
        raw_type = item.get("type", "text")

        # 跳过页码类型
        if raw_type == "page_number":
            continue

        # 映射类型
        block_type = _map_category(raw_type)

        # 提取基本字段
        text = item.get("text", "")
        page_idx = int(item.get("page_idx", 0))
        text_level: Optional[int] = item.get("text_level")

        # 初始化特殊字段
        is_table = block_type == BlockType.TABLE
        table_html: Optional[str] = None
        image_path: Optional[str] = None

        # 提取边界框
        raw_bbox = item.get("bbox")
        bbox: Optional[list[float]] = list(raw_bbox) if raw_bbox and len(raw_bbox) == 4 else None

        # 处理表格类型
        if is_table:
            table_html = item.get("table_body") or text or ""

        # 处理图片类型
        if block_type == BlockType.IMAGE:
            image_relative_path = item.get("img_path")
            if image_relative_path:
                image_path = str(output_path / image_relative_path)

        # 创建 ContentBlock
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
    """
    将 MinerU 的类型字符串映射为 BlockType 枚举。

    MinerU 返回的 type 字段有多种可能值，需要映射到统一的 BlockType。

    ## 映射关系

    | MinerU type | BlockType | 说明 |
    |-------------|-----------|------|
    | text | TEXT | 普通文本 |
    | header | TITLE | 标题 |
    | title | TITLE | 标题（旧格式） |
    | table | TABLE | 表格 |
    | image | IMAGE | 图片 |
    | interline_equation | TEXT | 行间公式（视为文本） |

    未知类型默认映射为 TEXT。

    Args:
        category: MinerU 返回的类型字符串

    Returns:
        BlockType: 对应的枚举值

    ## 示例

    ```python
    _map_category("text")      # BlockType.TEXT
    _map_category("header")    # BlockType.TITLE
    _map_category("table")     # BlockType.TABLE
    _map_category("unknown")   # BlockType.TEXT (默认)
    ```
    """
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
    """
    将 content_list 转换为 ContentBlock 列表（兼容旧版本）。

    这是一个兼容性函数，保留用于旧代码调用。
    新代码应直接使用 _convert_content_list()。

    Args:
        content_list: MinerU 返回的内容列表
        source_file: 源文件名

    Returns:
        list[ContentBlock]: 转换后的内容块列表
    """
    return _convert_content_list(content_list, source_file, Path("data/output"))


def _map_type(raw_type: str) -> BlockType:
    """
    将类型字符串映射为 BlockType 枚举（兼容旧版本）。

    这是一个兼容性函数，保留用于旧代码调用。
    新代码应使用 _map_category()。

    Args:
        raw_type: 类型字符串

    Returns:
        BlockType: 对应的枚举值
    """
    return _map_category(raw_type)
