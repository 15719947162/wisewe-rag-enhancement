"""
文档导出服务模块

本模块负责将知识库中的文档切片导出为 CSV 文件，支持：
1. 按文档导出所有切片数据
2. 包含文档元数据（文件名、哈希、切片数等）
3. 包含切片详情（内容、页码、策略、层级等）
4. 包含知识图谱数据（实体、三元组、关系）

导出的 CSV 文件可以直接用于：
- 离线数据分析
- 迁移到其他系统
- 人工审核切片质量
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.adapters.kb_adapter import fetch_document_export_record

# CSV 导出的列名列表，决定了导出文件的列顺序
EXPORT_FIELDNAMES = [
    "documentId",      # 文档 ID
    "kbId",            # 知识库 ID
    "filename",        # 文件名
    "fileHash",        # 文件哈希值
    "documentChunkCount",  # 文档切片总数
    "chunkId",         # 切片 ID
    "chunkIndex",      # 切片序号
    "page",            # 页码
    "strategy",        # 切片策略
    "layer",           # 层级（parent/child/enhanced）
    "title",           # 标题
    "content",         # 内容
    "charCount",       # 字符数
    "isTableChunk",    # 是否表格切片
    "isImageChunk",    # 是否图片切片
    "parentId",        # 父切片 ID
    "relatedIds",      # 关联切片 ID 列表
    "hasEmbedding",    # 是否有向量
    "relationCount",   # 关系数量
    "relationsJson",   # 关系 JSON
    "tripleCount",     # 三元组数量
    "triplesJson",     # 三元组 JSON
    "documentCreatedAt",   # 文档创建时间
    "documentUpdatedAt",   # 文档更新时间
    "chunkCreatedAt",      # 切片创建时间
]


def export_document_csv(document_id: str) -> tuple[str, bytes]:
    """
    导出文档切片为 CSV 文件

    参数：
        document_id: 文档 ID

    返回：
        tuple[str, bytes]: (文件名, CSV 文件内容)

    异常：
        ValueError: 文档不存在时抛出

    流程：
        1. 从数据库获取文档和切片数据
        2. 将切片数据转换为 CSV 行
        3. 生成文件名（基于原始文件名）
        4. 返回文件名和 CSV 内容
    """
    record = fetch_document_export_record(document_id)
    if record is None:
        raise ValueError(f"Document '{document_id}' not found")

    document = record["document"]
    rows = [_build_export_row(document, chunk) for chunk in record["chunks"]]
    filename = _build_export_filename(document["filename"], document_id)
    return filename, _render_csv(rows)


def build_csv_content_disposition(filename: str) -> str:
    """
    构建 HTTP 响应的 Content-Disposition 头

    支持 UTF-8 编码的文件名，确保中文文件名能正确下载。

    参数：
        filename: 文件名

    返回：
        str: Content-Disposition 头值

    格式：
        attachment; filename="safe_name.csv"; filename*=UTF-8''编码后的文件名
    """
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "document-chunks.csv"
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(filename)}"


def _build_export_row(document: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    """
    构建单条 CSV 导出行

    将文档和切片数据合并为一行 CSV 数据。

    参数：
        document: 文档元数据字典
        chunk: 切片数据字典

    返回：
        dict: CSV 行数据
    """
    relations = chunk.get("relations") if isinstance(chunk.get("relations"), list) else []
    triples = chunk.get("triples") if isinstance(chunk.get("triples"), list) else []
    page = chunk.get("page")
    return {
        "documentId": document["id"],
        "kbId": document["kb_id"],
        "filename": document["filename"],
        "fileHash": document["file_hash"],
        "documentChunkCount": int(document.get("chunk_count", 0) or 0),
        "chunkId": chunk["id"],
        "chunkIndex": int(chunk.get("chunk_index", 0) or 0),
        "page": int(page) + 1 if page is not None else 0,
        "strategy": chunk.get("strategy", "") or "",
        "layer": chunk.get("layer", "") or "",
        "title": chunk.get("title", "") or "",
        "content": chunk.get("content", "") or "",
        "charCount": int(chunk.get("char_count", 0) or 0),
        "isTableChunk": bool(chunk.get("is_table_chunk")),
        "isImageChunk": bool(chunk.get("is_image_chunk")),
        "parentId": chunk.get("parent_id", "") or "",
        "relatedIds": _normalize_related_ids(chunk.get("related_ids")),
        "hasEmbedding": bool(chunk.get("has_embedding")),
        "relationCount": len(relations),
        "relationsJson": json.dumps(relations, ensure_ascii=False),
        "tripleCount": len(triples),
        "triplesJson": json.dumps(triples, ensure_ascii=False),
        "documentCreatedAt": document["created_at"].isoformat() if document.get("created_at") else "",
        "documentUpdatedAt": document["updated_at"].isoformat() if document.get("updated_at") else "",
        "chunkCreatedAt": chunk["created_at"].isoformat() if chunk.get("created_at") else "",
    }


def _normalize_related_ids(value: Any) -> str:
    """
    规范化关联 ID 字段

    将各种格式的 related_ids 转换为 JSON 字符串：
    - 列表：直接转 JSON
    - 字符串：解析后再转 JSON
    - 其他：返回空列表 JSON

    参数：
        value: 原始值

    返回：
        str: JSON 字符串
    """
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return "[]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps([raw], ensure_ascii=False)
        return json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
    return "[]"


def _build_export_filename(filename: str, document_id: str) -> str:
    """
    构建导出文件名

    基于原始文件名生成 CSV 文件名：
    - 移除扩展名
    - 替换非法字符
    - 添加 -chunks.csv 后缀

    参数：
        filename: 原始文件名
        document_id: 文档 ID（备用）

    返回：
        str: 安全的 CSV 文件名
    """
    stem = Path(filename or "").stem.strip()
    if not stem:
        stem = f"document-{document_id}"
    safe_stem = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", stem).strip(" ._") or f"document-{document_id}"
    return f"{safe_stem}-chunks.csv"


def _render_csv(rows: list[dict[str, Any]]) -> bytes:
    """
    将数据行列表渲染为 CSV 文件内容

    参数：
        rows: 数据行列表

    返回：
        bytes: CSV 文件内容（UTF-8 BOM 编码，便于 Excel 打开）
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")
