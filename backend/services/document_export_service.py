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

#
# ============================================================================
# 备份导出功能
# ============================================================================
#
# 【功能概述】
# 备份导出是一种完整的数据导出方式，将文档切片及其向量数据导出为可恢复的 CSV 文件。
# 与普通导出不同，备份导出包含：
# - 完整的向量数据（embedding_json）：可直接用于恢复向量索引，无需重新计算
# - 文档来源信息（source_storage, source_path, source_url）：记录原始文件位置
# - 解析器信息（parser_provider）：便于追溯解析方式
# - 图片路径（image_path）：图片切片的关联信息
#
# 【适用场景】
# 1. 数据迁移：将知识库从一个环境迁移到另一个环境，保留完整语义能力
# 2. 灾难恢复：定期备份切片和向量，应对数据丢失风险
# 3. 环境同步：开发/测试/生产环境之间的数据同步
# 4. 离线分析：导出完整数据用于研究或质量审计
#
# 【与普通导出的区别】
# ┌─────────────────┬─────────────────────┬─────────────────────┐
# │       特性       │      普通导出       │      备份导出       │
# ├─────────────────┼─────────────────────┼─────────────────────┤
# │ 向量数据        │ 仅标记是否有向量    │ 包含完整向量 JSON   │
# │ 文件用途        │ 人工审核/分析       │ 数据恢复/迁移       │
# │ 强制要求        │ 无                  │ 必须包含向量数据    │
# │ 输出字段数      │ 23 个               │ 30 个               │
# │ 文件后缀        │ -chunks.csv         │ -backup.csv         │
# │ Schema 版本     │ 无                  │ 有版本标识          │
# └─────────────────┴─────────────────────┴─────────────────────┘
#
# 【Schema 版本说明】
# BACKUP_CSV_SCHEMA_VERSION 用于标识备份文件的格式版本。
# 当备份格式发生变更（如新增字段、修改字段含义）时，需要更新版本号。
# 导入工具可根据版本号选择合适的解析逻辑，确保向后兼容。
# 当前版本：wisewe-rag-backup-v1
#

# 备份 CSV 的 Schema 版本标识符
# 格式：wisewe-rag-backup-v{主版本号}
# 主版本号变更场景：字段删除、字段类型变更、字段含义发生不兼容变化
# 次版本变更可通过新增可选字段实现，无需更新主版本号
BACKUP_CSV_SCHEMA_VERSION = "wisewe-rag-backup-v1"

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


# 备份导出的 CSV 列名列表，决定了备份文件的列顺序
# 包含以下额外字段（相比普通导出）：
# - schemaVersion: Schema 版本标识，用于导入时选择解析逻辑
# - exportedBy: 导出操作者，用于审计追踪
# - sourceStorage/sourcePath/sourceUrl: 文档来源信息，用于追溯原始文件
# - parserProvider: 解析器标识，记录使用了哪种 PDF 解析服务
# - imagePath: 图片切片的图片路径，普通导出不含此字段
# - embeddingJson: 完整向量数据 JSON，核心差异字段
# - embeddingModel: 向量化使用的模型名称
# - embeddingDimension: 向量维度
# 注意：普通导出有 hasEmbedding（布尔）和 relationCount/tripleCount，
#       备份导出移除了这些字段以减少冗余（可通过 JSON 字段计算得出）
BACKUP_EXPORT_FIELDNAMES = [
    # ========== 备份元数据 ==========
    "schemaVersion",    # Schema 版本标识，用于导入兼容性判断
    "exportedBy",       # 导出操作者，审计追踪用
    # ========== 文档信息 ==========
    "documentId",       # 文档 ID（主键）
    "kbId",             # 知识库 ID
    "filename",         # 原始文件名
    "fileHash",         # 文件哈希值（用于去重和校验）
    "documentChunkCount",  # 文档切片总数
    # ========== 来源信息（备份导出特有）==========
    "sourceStorage",    # 存储类型（如 aliyun-oss）
    "sourcePath",       # 存储路径
    "sourceUrl",        # 原始 URL（如有）
    "parserProvider",   # 解析器提供方（如 mineru-cloud-302ai）
    # ========== 切片基础信息 ==========
    "chunkId",           # 切片 ID（主键）
    "chunkIndex",        # 切片序号（从 0 开始）
    "page",              # 页码（从 1 开始，便于阅读）
    "strategy",          # 切片策略（fixed_length/paragraph/semantic/llm/hierarchical）
    "layer",             # 层级（parent/child/enhanced）
    "title",             # 标题（如有）
    "content",           # 切片内容
    "charCount",         # 字符数
    # ========== 切片类型标记 ==========
    "isTableChunk",      # 是否表格切片
    "isImageChunk",      # 是否图片切片
    "imagePath",         # 图片路径（备份导出特有，普通导出不含）
    # ========== 切片关系 ==========
    "parentId",          # 父切片 ID（层级切片用）
    "relatedIds",        # 关联切片 ID 列表 JSON
    "relationsJson",     # 关系列表 JSON
    "triplesJson",       # 三元组列表 JSON
    # ========== 向量数据（备份导出核心差异）==========
    "embeddingJson",     # 完整向量数据 JSON 数组（核心！用于恢复向量索引）
    "embeddingModel",    # 向量模型名称（当前固定为 text-embedding-v3）
    "embeddingDimension",  # 向量维度（从 embeddingJson 计算得出）
    # ========== 时间戳 ==========
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


def _render_csv(rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> bytes:
    """
    将数据行列表渲染为 CSV 文件内容

    参数：
        rows: 数据行列表

    返回：
        bytes: CSV 文件内容（UTF-8 BOM 编码，便于 Excel 打开）
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames or EXPORT_FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def export_document_backup_csv(document_id: str, exported_by: str = "system") -> tuple[str, bytes]:
    """
    导出文档备份 CSV（包含完整向量数据）

    【功能说明】
    将文档的所有切片导出为可恢复的备份 CSV 文件。
    与 export_document_csv() 的核心区别：
    - 包含完整向量数据（embeddingJson），可直接恢复向量索引
    - 包含文档来源信息，便于追溯
    - 必须所有切片都有向量才能导出（否则抛出异常）

    【适用场景】
    1. 知识库迁移：将数据从一个环境迁移到另一个环境
       示例：从开发环境迁移到生产环境，保留完整的语义检索能力
    2. 灾难恢复备份：定期备份，应对数据丢失风险
    3. 环境同步：在多个环境间同步知识库数据

    【参数说明】
    Args:
        document_id: 文档 ID（UUID 格式）
        exported_by: 导出操作者标识，默认为 "system"
                     实际使用时可传入用户 ID 或用户名，用于审计追踪

    【返回值】
    Returns:
        tuple[str, bytes]: (文件名, CSV 文件内容)
        - 文件名格式：{原文件名}-backup.csv
        - CSV 内容：UTF-8 BOM 编码，便于 Excel 打开

    【异常说明】
    Raises:
        ValueError: 文档不存在时抛出
        ValueError: 文档包含无向量的切片时抛出（备份导出强制要求所有切片有向量）

    【导出流程】
    1. 从数据库获取文档和切片数据
    2. 验证所有切片都有向量数据（核心校验）
    3. 构建备份导出行（包含向量 JSON）
    4. 生成备份文件名
    5. 渲染 CSV 内容并返回

    【示例】
    >>> filename, content = export_document_backup_csv("doc-uuid-123", "user-alice")
    >>> print(filename)  # "产品手册-backup.csv"
    >>> # 将 content 写入文件后可用于恢复

    【注意事项】
    - 备份文件包含完整向量，文件体积较大（每个切片约 4KB 向量数据）
    - 导入时需要根据 schemaVersion 选择合适的解析逻辑
    - 当前向量模型固定为 text-embedding-v3，未来可能支持多模型
    """
    record = fetch_document_export_record(document_id)
    if record is None:
        raise ValueError(f"Document '{document_id}' not found")

    document = record["document"]
    rows = [_build_backup_export_row(document, chunk, exported_by) for chunk in record["chunks"]]
    if any(not row["embeddingJson"] for row in rows):
        raise ValueError("Document contains chunks without embeddings and cannot be exported as backup CSV")
    filename = _build_backup_export_filename(document["filename"], document_id)
    return filename, _render_csv(rows, BACKUP_EXPORT_FIELDNAMES)


def _build_backup_export_row(document: dict[str, Any], chunk: dict[str, Any], exported_by: str) -> dict[str, Any]:
    """
    构建单条备份导出行

    【功能说明】
    将文档元数据和切片数据合并为一行备份 CSV 数据。
    相比普通导出行（_build_export_row），额外包含：
    - Schema 版本和导出者信息
    - 文档来源信息（source_storage/source_path/source_url/parser_provider）
    - 图片路径（imagePath）
    - 完整向量数据（embeddingJson/embeddingModel/embeddingDimension）

    【参数说明】
    Args:
        document: 文档元数据字典，包含：
            - id, kb_id, filename, file_hash, chunk_count
            - source_storage, source_path, source_url, parser_provider（备份特有）
            - created_at, updated_at
        chunk: 切片数据字典，包含：
            - id, chunk_index, page, strategy, layer, title, content, char_count
            - is_table_chunk, is_image_chunk, image_path
            - parent_id, related_ids, relations, triples
            - embedding（备份特有，向量数据）
            - created_at
        exported_by: 导出操作者标识

    【返回值】
    Returns:
        dict: 备份 CSV 行数据，键名与 BACKUP_EXPORT_FIELDNAMES 对应

    【实现逻辑】
    1. 复用 _build_export_row 获取基础字段（避免重复代码）
    2. 规范化向量数据为 JSON 字符串
    3. 合并文档来源信息
    4. 计算向量维度（从 embeddingJson 解析）
    5. 返回完整的备份行字典

    【注意事项】
    - 向量维度从 embeddingJson 计算，避免额外存储
    - embeddingModel 当前固定为 text-embedding-v3
    - 如果切片没有向量，embeddingJson 为空字符串，维度为 0
      （但 export_document_backup_csv 会在此之前校验并拒绝）
    """
    base = _build_export_row(document, chunk)
    embedding_json = _normalize_embedding_json(chunk.get("embedding"))
    return {
        "schemaVersion": BACKUP_CSV_SCHEMA_VERSION,
        "exportedBy": exported_by,
        "documentId": base["documentId"],
        "kbId": base["kbId"],
        "filename": base["filename"],
        "fileHash": base["fileHash"],
        "documentChunkCount": base["documentChunkCount"],
        "sourceStorage": document.get("source_storage", "") or "",
        "sourcePath": document.get("source_path", "") or "",
        "sourceUrl": document.get("source_url", "") or "",
        "parserProvider": document.get("parser_provider", "") or "",
        "chunkId": base["chunkId"],
        "chunkIndex": base["chunkIndex"],
        "page": base["page"],
        "strategy": base["strategy"],
        "layer": base["layer"],
        "title": base["title"],
        "content": base["content"],
        "charCount": base["charCount"],
        "isTableChunk": base["isTableChunk"],
        "isImageChunk": base["isImageChunk"],
        "imagePath": chunk.get("image_path", "") or "",
        "parentId": base["parentId"],
        "relatedIds": base["relatedIds"],
        "relationsJson": base["relationsJson"],
        "triplesJson": base["triplesJson"],
        "embeddingJson": embedding_json,
        "embeddingModel": "text-embedding-v3",
        "embeddingDimension": len(json.loads(embedding_json)) if embedding_json else 0,
        "documentCreatedAt": base["documentCreatedAt"],
        "documentUpdatedAt": base["documentUpdatedAt"],
        "chunkCreatedAt": base["chunkCreatedAt"],
    }


def _normalize_embedding_json(value: Any) -> str:
    """
    规范化向量数据为 JSON 字符串

    【功能说明】
    将各种格式的向量数据统一转换为紧凑的 JSON 字符串格式。
    向量数据在数据库中可能以多种形式存储，此函数确保导出格式一致。

    【支持格式】
    支持以下输入格式：
    1. 列表类型：[0.1, 0.2, 0.3, ...] 直接转换
    2. JSON 字符串：'[0.1, 0.2, 0.3, ...]' 解析后重新格式化
    3. None 或空值：返回空字符串

    【参数说明】
    Args:
        value: 原始向量数据，可能为：
            - list[float]: 向量列表
            - str: JSON 格式的向量字符串
            - None: 无向量数据

    【返回值】
    Returns:
        str: 规范化后的 JSON 字符串
        - 格式：紧凑 JSON，无空格，如 '[0.1,0.2,0.3]'
        - 空向量返回空字符串 ''
        - 解析失败返回空字符串 ''

    【格式化规则】
    使用 separators=(',', ':') 生成最紧凑的 JSON 格式，
    减少备份文件体积。每个向量元素强制转换为 float。

    【示例】
    >>> _normalize_embedding_json([0.1, 0.2, 0.3])
    '[0.1,0.2,0.3]'
    >>> _normalize_embedding_json('[0.1, 0.2, 0.3]')
    '[0.1,0.2,0.3]'
    >>> _normalize_embedding_json(None)
    ''
    >>> _normalize_embedding_json('invalid json')
    ''

    【注意事项】
    - 不验证向量维度，仅做格式转换
    - 解析失败时静默返回空字符串，不抛出异常
    - 空列表 '[]' 也是合法的向量数据（虽然实际不应出现）
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return json.dumps([float(item) for item in value], separators=(",", ":"))
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return json.dumps([float(item) for item in parsed], separators=(",", ":"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    return ""


def _build_backup_export_filename(filename: str, document_id: str) -> str:
    """
    构建备份导出文件名

    【功能说明】
    基于原始文件名生成备份 CSV 文件名。
    与普通导出文件名（_build_export_filename）的区别：
    - 后缀为 '-backup.csv'（普通导出为 '-chunks.csv'）
    - 明确标识这是可恢复的备份文件

    【参数说明】
    Args:
        filename: 原始文件名（包含扩展名）
        document_id: 文档 ID（用于生成默认文件名）

    【返回值】
    Returns:
        str: 安全的备份 CSV 文件名
        格式：{原文件名去掉扩展名}-backup.csv

    【文件名处理规则】
    1. 移除原始文件扩展名（.pdf 等）
    2. 替换非法字符（\\ / : * ? " < > | 换行符）为下划线
    3. 去除首尾空格、点、下划线
    4. 添加 '-backup.csv' 后缀
    5. 如果处理后的文件名为空，使用 'document-{document_id}-backup.csv'

    【示例】
    >>> _build_backup_export_filename("产品手册.pdf", "doc-123")
    '产品手册-backup.csv'
    >>> _build_backup_export_filename("test/file:name?.pdf", "doc-456")
    'test_file_name_-backup.csv'
    >>> _build_backup_export_filename("", "doc-789")
    'document-doc-789-backup.csv'

    【安全考虑】
    - 过滤 Windows/Unix 文件名非法字符
    - 确保文件名不为空
    - 保留 Unicode 字符（支持中文文件名）
    """
    stem = Path(filename or "").stem.strip()
    if not stem:
        stem = f"document-{document_id}"
    safe_stem = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", stem).strip(" ._") or f"document-{document_id}"
    return f"{safe_stem}-backup.csv"
