"""
pgvector 向量数据库写入模块
============================

本模块负责将切片（chunks）及其向量嵌入（embeddings）写入 PostgreSQL + pgvector 向量数据库，
实现 RAG 知识库的持久化存储和高效语义检索。

## 核心功能

1. **向量存储**: 将文本切片和对应的向量嵌入存入 pgvector 数据库
2. **文档去重**: 基于文件哈希避免重复写入相同文档
3. **批量写入**: 支持两种写入模式（VALUES/COPY）以优化性能
4. **关系存储**: 存储切片间的关联关系和知识图谱三元组

## 与 CSV 导出的区别

| 特性            | pgvector 写入                      | CSV 导出                        |
|----------------|-----------------------------------|--------------------------------|
| 存储类型         | 关系型数据库 + 向量索引             | 静态文件                        |
| 查询能力         | 支持 SQL + 向量相似度检索           | 不支持查询,需加载到内存          |
| 向量检索         | 原生支持 HNSW/IVFFlat 索引         | 需外部工具实现                   |
| 实时更新         | 支持增量插入和更新                 | 需重新生成文件                   |
| 适用场景         | 生产环境 RAG 系统                  | 数据迁移、备份、离线分析          |

## 向量索引说明

pgvector 支持两种主要的向量索引类型:

1. **HNSW (Hierarchical Navigable Small World)**
   - 基于图的近似最近邻算法
   - 查询速度快,召回率高
   - 构建时间较长,内存占用较高
   - 适合大规模数据集

2. **IVFFlat (Inverted File with Flat compression)**
   - 基于聚类的倒排索引
   - 构建速度快,内存占用少
   - 查询精度可通过 probes 参数调整
   - 适合中等规模数据集

数据库 schema 初始化时会在 embedding 列创建 IVFFlat 索引,默认 lists=100。

## 数据写入流程

```
chunks + embeddings
    ↓
计算文件哈希 → 检查是否已存在
    ↓
创建/更新 document 记录
    ↓
批量写入 chunks 表（含向量）
    ↓
写入切片关系（如有）
    ↓
写入知识图谱三元组（如有）
    ↓
提交事务
```

## 性能优化

- 使用批量插入（execute_values 或 COPY）减少数据库往返
- 支持分页写入（默认 500 条/页）
- 文件哈希缓存避免重复处理
- 事务保证数据一致性
"""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import time
from typing import Any

from core.db.connection import get_db_connection
from core.db.init_db import ensure_db_schema
from core.db.knowledge_base import ensure_default_kb

try:
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover - psycopg2 is optional in some tests
    execute_values = None


def _elapsed_ms(started_at: float) -> int:
    """
    计算从开始时间到现在经过的毫秒数。

    Args:
        started_at: 开始时间戳（time.perf_counter() 返回值）

    Returns:
        经过的毫秒数（整数）

    用于性能监控和写入耗时统计。
    """
    return int((time.perf_counter() - started_at) * 1000)


def _write_page_size() -> int:
    """
    获取批量写入的分页大小。

    从环境变量 PGVECTOR_WRITE_PAGE_SIZE 读取，默认 500。
    较大的值可减少数据库往返次数，但占用更多内存。

    Returns:
        每批写入的记录数（最小为 1）
    """
    return max(int(os.environ.get("PGVECTOR_WRITE_PAGE_SIZE", "500")), 1)


def _write_mode() -> str:
    """
    获取批量写入模式。

    支持两种模式:
    - 'values': 使用 execute_values 批量插入（默认）
    - 'copy': 使用 COPY 命令流式导入（更快但需要更多临时内存）

    环境变量: PGVECTOR_WRITE_MODE

    Returns:
        写入模式字符串 ('values' 或 'copy')
    """
    mode = os.environ.get("PGVECTOR_WRITE_MODE", "values").strip().lower()
    return mode if mode in {"values", "copy"} else "values"


def _vector_literal(embedding: Any) -> str:
    """
    将向量嵌入转换为 pgvector 可接受的字符串格式。

    pgvector 使用 '[1.0, 2.0, 3.0]' 格式表示向量。
    此函数将 embedding 数组转换为 JSON 数组字符串。

    Args:
        embedding: 向量嵌入数组（list 或类似对象）

    Returns:
        JSON 数组字符串，如 '[0.1,0.2,0.3]'

    Raises:
        ValueError: 如果向量包含 NaN 值
    """
    return json.dumps([float(value) for value in embedding], separators=(",", ":"), allow_nan=False)


def _csv_value(value: Any) -> Any:
    """
    将 Python 值转换为 PostgreSQL COPY 格式的 CSV 值。

    PostgreSQL COPY 使用 '\\N' 表示 NULL 值。

    Args:
        value: 要转换的 Python 值

    Returns:
        '\\N' (如果值为 None) 或原值
    """
    return r"\N" if value is None else value


def _copy_rows(cur, copy_sql: str, rows: list[tuple[Any, ...]]) -> None:
    """
    使用 PostgreSQL COPY 命令批量导入数据。

    COPY 是 PostgreSQL 最快的数据导入方式，适用于大批量数据写入。
    此方法将数据写入 CSV 格式的内存缓冲区，然后通过 COPY 导入。

    Args:
        cur: 数据库游标
        copy_sql: COPY 命令 SQL 语句
        rows: 要导入的数据行列表（每行是一个元组）

    性能优势:
    - 比 INSERT 快 5-10 倍
    - 减少数据库日志开销
    - 支持流式处理大数据集
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])
    buffer.seek(0)
    cur.copy_expert(copy_sql, buffer)


def compute_file_hash(pdf_path: str) -> str:
    """
    计算 PDF 文件的 SHA-256 哈希值，用于文档去重。

    通过哈希值识别相同内容的文件，避免重复处理和存储。
    这是实现增量更新的关键：只有内容变化的文件才会重新处理。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        SHA-256 哈希的十六进制字符串（64 字符），文件不存在则返回空字符串

    实现细节:
    - 使用流式读取（64KB 块）避免大文件内存溢出
    - SHA-256 提供足够的安全性（碰撞概率极低）
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return ""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_document_id_by_hash(conn, kb_id: str, file_hash: str) -> str | None:
    """
    根据知识库 ID 和文件哈希查找已存在的文档 ID。

    用于增量更新场景：如果文件内容未变化，可以跳过处理。

    Args:
        conn: 数据库连接
        kb_id: 知识库 ID
        file_hash: 文件哈希值

    Returns:
        文档 ID（UUID 字符串）或 None（未找到）

    查询逻辑:
    - 在 documents 表中匹配 kb_id 和 file_hash
    - 使用 LIMIT 1 优化查询（假设哈希唯一）
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM documents WHERE kb_id=%s AND file_hash=%s LIMIT 1",
            (kb_id, file_hash),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def upsert_document(
    conn,
    kb_id: str,
    filename: str,
    file_hash: str,
    chunk_count: int,
    source_storage: str = "unknown",
    source_path: str = "",
    source_url: str = "",
    parser_provider: str = "",
) -> str:
    """
    插入或更新文档记录，返回文档 UUID。

    使用 PostgreSQL 的 UPSERT 语法（INSERT ... ON CONFLICT）实现：
    - 如果文档不存在，创建新记录
    - 如果文档已存在（相同 kb_id + file_hash），更新元数据和统计信息

    Args:
        conn: 数据库连接
        kb_id: 知识库 ID
        filename: 文件名
        file_hash: 文件哈希值
        chunk_count: 切片数量
        source_storage: 源存储类型（如 'oss', 'local', 's3'）
        source_path: 源文件路径
        source_url: 源文件 URL
        parser_provider: 解析器提供者（如 'mineru', 'pypdf'）

    Returns:
        文档 ID（UUID 字符串）

    数据库字段:
    - kb_id + file_hash 构成唯一约束
    - updated_at 自动更新为当前时间
    - 所有源信息字段可为空（NULL）
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents(
                kb_id, filename, file_hash, chunk_count,
                source_storage, source_path, source_url, parser_provider
            )
            VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(kb_id, file_hash) DO UPDATE
                SET filename=EXCLUDED.filename,
                    chunk_count=EXCLUDED.chunk_count,
                    source_storage=EXCLUDED.source_storage,
                    source_path=EXCLUDED.source_path,
                    source_url=EXCLUDED.source_url,
                    parser_provider=EXCLUDED.parser_provider,
                    updated_at=NOW()
            RETURNING id
            """,
            (
                kb_id,
                filename,
                file_hash,
                chunk_count,
                source_storage or "unknown",
                source_path or None,
                source_url or None,
                parser_provider or None,
            ),
        )
        row = cur.fetchone()
    return str(row[0])


def _upsert_document_with_optional_source(
    conn,
    kb_id: str,
    filename: str,
    file_hash: str,
    chunk_count: int,
    source_storage: str,
    source_path: str,
    source_url: str,
    parser_provider: str,
) -> str:
    """
    带可选源信息的文档 upsert 包装函数。

    如果没有提供任何源信息（存储类型、路径、URL、解析器），
    则调用简化版 upsert_document，减少不必要的 NULL 参数传递。

    Args:
        所有参数同 upsert_document

    Returns:
        文档 ID（UUID 字符串）

    内部逻辑:
    - 检查是否有任何源信息
    - 有源信息：调用完整版 upsert_document
    - 无源信息：调用简化版（默认值）
    """
    if source_storage == "unknown" and not source_path and not source_url and not parser_provider:
        return upsert_document(conn, kb_id, filename, file_hash, chunk_count)
    return upsert_document(
        conn,
        kb_id,
        filename,
        file_hash,
        chunk_count,
        source_storage,
        source_path,
        source_url,
        parser_provider,
    )


def build_chunk_search_text(chunk: Any) -> str:
    """
    构建用于全文检索的文本字段。

    将切片的标题、内容和来源合并为一个字符串，
    用于 PostgreSQL 的全文检索（tsvector）。

    Args:
        chunk: 切片对象，包含 title、content、source 属性

    Returns:
        合并后的文本字符串，各部分用空格分隔

    用途:
    - 支持 LIKE 模糊查询
    - 构建 tsvector 全文检索向量
    - 提升关键词搜索的召回率
    """
    parts = [
        getattr(chunk, "title", "") or "",
        getattr(chunk, "content", "") or "",
        getattr(chunk, "source", "") or "",
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def write_chunks_batch(conn, chunks: list, embeddings: list, kb_id: str, document_id: str) -> int:
    """
    批量写入切片和向量嵌入到 chunks 表。

    这是核心写入函数，将切片内容和对应的向量嵌入存入数据库。
    支持两种写入模式：
    1. VALUES 模式：使用 execute_values 批量 INSERT
    2. COPY 模式：使用 PostgreSQL COPY 命令（更快）

    Args:
        conn: 数据库连接
        chunks: 切片对象列表（Chunk 模型实例）
        embeddings: 对应的向量嵌入列表（与 chunks 一一对应）
        kb_id: 知识库 ID
        document_id: 文档 ID

    Returns:
        成功写入的切片数量

    数据库字段:
    - id: 切片 UUID（来自 chunk.id）
    - kb_id, document_id: 关联知识库和文档
    - content, source, page, chunk_index: 切片内容元数据
    - strategy, title, char_count: 切片策略和统计信息
    - is_table_chunk, is_image_chunk: 类型标识
    - image_path, layer, parent_id, related_ids: 层级关系
    - search_text: 全文检索文本
    - search_vector: PostgreSQL tsvector（自动生成）
    - embedding: 向量嵌入（pgvector vector 类型）

    性能优化:
    - 批量插入减少数据库往返
    - COPY 模式比 INSERT 快 5-10 倍
    - 分页写入控制内存占用
    """
    if not chunks:
        return 0

    rows: list[tuple[Any, ...]] = []
    use_copy = _write_mode() == "copy"
    for chunk, embedding in zip(chunks, embeddings):
        search_text = build_chunk_search_text(chunk)
        embedding_value = _vector_literal(embedding) if use_copy else embedding
        rows.append((
            chunk.id,
            kb_id,
            document_id,
            chunk.content,
            chunk.source,
            chunk.page,
            chunk.chunk_index,
            chunk.strategy,
            chunk.title,
            chunk.char_count,
            chunk.is_table_chunk,
            chunk.is_image_chunk,
            chunk.image_path,
            chunk.layer,
            chunk.parent_id,
            json.dumps(chunk.related_ids),
            search_text,
            search_text,
            embedding_value,
        ))

    insert_sql = """
            INSERT INTO chunks(
                id, kb_id, document_id, content, source, page, chunk_index,
                strategy, title, char_count, is_table_chunk, is_image_chunk,
                image_path, layer, parent_id, related_ids, search_text, search_vector, embedding
            ) VALUES %s
            ON CONFLICT(id) DO NOTHING
            """

    with conn.cursor() as cur:
        if use_copy:
            # COPY 模式：创建临时表 → COPY 导入 → INSERT 到正式表
            # 优点：速度最快，适合大批量数据
            # 缺点：需要临时表，稍微复杂
            cur.execute(
                """
                CREATE TEMP TABLE tmp_chunks_upload(
                    id UUID,
                    kb_id VARCHAR(255),
                    document_id UUID,
                    content TEXT,
                    source VARCHAR(500),
                    page INTEGER,
                    chunk_index INTEGER,
                    strategy VARCHAR(100),
                    title VARCHAR(500),
                    char_count INTEGER,
                    is_table_chunk BOOLEAN,
                    is_image_chunk BOOLEAN,
                    image_path TEXT,
                    layer VARCHAR(50),
                    parent_id UUID,
                    related_ids TEXT,
                    search_text TEXT,
                    search_vector_text TEXT,
                    embedding_text TEXT
                ) ON COMMIT DROP
                """
            )
            _copy_rows(
                cur,
                """
                COPY tmp_chunks_upload(
                    id, kb_id, document_id, content, source, page, chunk_index,
                    strategy, title, char_count, is_table_chunk, is_image_chunk,
                    image_path, layer, parent_id, related_ids, search_text,
                    search_vector_text, embedding_text
                )
                FROM STDIN WITH (FORMAT CSV, NULL '\\N')
                """,
                rows,
            )
            cur.execute(
                """
                INSERT INTO chunks(
                    id, kb_id, document_id, content, source, page, chunk_index,
                    strategy, title, char_count, is_table_chunk, is_image_chunk,
                    image_path, layer, parent_id, related_ids, search_text, search_vector, embedding
                )
                SELECT
                    id, kb_id, document_id, content, source, page, chunk_index,
                    strategy, title, char_count, is_table_chunk, is_image_chunk,
                    image_path, layer, parent_id, related_ids, search_text,
                    to_tsvector('simple', search_vector_text), embedding_text::vector
                FROM tmp_chunks_upload
                ON CONFLICT(id) DO NOTHING
                """
            )
        elif execute_values is not None:
            # VALUES 模式（psycopg2）：使用 execute_values 批量插入
            # 优点：代码简洁，不需要临时表
            # 缺点：比 COPY 稍慢
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple', %s),%s)",
                page_size=page_size,
            )
        else:
            # VALUES 模式（降级）：使用 executemany 逐批插入
            # 用于 psycopg2 未安装时的降级处理
            cur.executemany(
                """
                INSERT INTO chunks(
                    id, kb_id, document_id, content, source, page, chunk_index,
                    strategy, title, char_count, is_table_chunk, is_image_chunk,
                    image_path, layer, parent_id, related_ids, search_text, search_vector, embedding
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple', %s),%s)
                ON CONFLICT(id) DO NOTHING
                """,
                rows,
            )
    return len(rows)


def write_chunk_relations_batch(conn, chunks: list, kb_id: str) -> int:
    """
    批量写入切片间的关系到 chunk_relations 表。

    切片间的关系用于表示：
    - 文本切片与关联表格的关系
    - 文本切片与关联图片的关系
    - 父子层级关系（hierarchical 策略）

    Args:
        conn: 数据库连接
        chunks: 切片对象列表（包含 relations 属性）
        kb_id: 知识库 ID

    Returns:
        成功写入的关系数量

    数据库字段:
    - kb_id: 知识库 ID
    - src_id: 源切片 ID
    - dst_id: 目标切片 ID
    - rel_type: 关系类型（如 'has_table', 'has_image'）
    - weight: 关系权重（0-1）
    - source: 关系来源（如 'linker', 'hierarchical'）
    - evidence: 关系证据/说明

    去重逻辑:
    - 使用 (src_id, dst_id, rel_type) 三元组去重
    - 同一关系只保留一次
    """
    rows: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        for relation in chunk.relations:
            key = (chunk.id, relation.target_id, relation.rel_type)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                (
                    kb_id,
                    chunk.id,
                    relation.target_id,
                    relation.rel_type,
                    relation.weight,
                    relation.source,
                    relation.evidence,
                )
            )

    if not rows:
        return 0

    insert_sql = """
            INSERT INTO chunk_relations(
                kb_id, src_id, dst_id, rel_type, weight, source, evidence
            ) VALUES %s
            ON CONFLICT(kb_id, src_id, dst_id, rel_type) DO NOTHING
            """

    with conn.cursor() as cur:
        if _write_mode() == "copy":
            # COPY 模式：使用临时表批量导入
            cur.execute(
                """
                CREATE TEMP TABLE tmp_chunk_relations_upload(
                    kb_id VARCHAR(255),
                    src_id UUID,
                    dst_id UUID,
                    rel_type VARCHAR(100),
                    weight REAL,
                    source VARCHAR(50),
                    evidence TEXT
                ) ON COMMIT DROP
                """
            )
            _copy_rows(
                cur,
                """
                COPY tmp_chunk_relations_upload(
                    kb_id, src_id, dst_id, rel_type, weight, source, evidence
                )
                FROM STDIN WITH (FORMAT CSV, NULL '\\N')
                """,
                rows,
            )
            cur.execute(
                """
                INSERT INTO chunk_relations(
                    kb_id, src_id, dst_id, rel_type, weight, source, evidence
                )
                SELECT kb_id, src_id, dst_id, rel_type, weight, source, evidence
                FROM tmp_chunk_relations_upload
                ON CONFLICT(kb_id, src_id, dst_id, rel_type) DO NOTHING
                """
            )
        elif execute_values is not None:
            # VALUES 模式（psycopg2）
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s)",
                page_size=page_size,
            )
        else:
            # VALUES 模式（降级）
            cur.executemany(
                """
                INSERT INTO chunk_relations(
                    kb_id, src_id, dst_id, rel_type, weight, source, evidence
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(kb_id, src_id, dst_id, rel_type) DO NOTHING
                """,
                rows,
            )
    return len(rows)


def write_kg_triples_batch(conn, chunks: list, kb_id: str) -> int:
    """
    批量写入知识图谱三元组到 kg_triples 表。

    知识图谱三元组用于表示实体间的语义关系，格式为：
    (主语 subject, 谓语 predicate, 宾语 object)

    只处理 enhanced 层级的切片，该层级的切片包含 LLM 提取的知识三元组。

    Args:
        conn: 数据库连接
        chunks: 切片对象列表（enhanced 层级的切片包含 extracted_triples）
        kb_id: 知识库 ID

    Returns:
        成功写入的三元组数量

    数据库字段:
    - kb_id: 知识库 ID
    - s: 主语（实体）
    - p: 谓语（关系）
    - o: 宾语（实体）
    - confidence: 置信度（0-1）
    - source_chunk: 来源切片 ID

    应用场景:
    - 知识图谱构建
    - 多跳推理
    - 实体关系查询
    """
    rows: list[tuple[Any, ...]] = []
    for chunk in chunks:
        # 只处理 enhanced 层级的切片
        if chunk.layer != "enhanced":
            continue
        for triple in chunk.extracted_triples:
            rows.append(
                (
                    kb_id,
                    triple.s,
                    triple.p,
                    triple.o,
                    triple.confidence,
                    chunk.parent_id or chunk.id,
                )
            )
    if not rows:
        return 0

    insert_sql = """
            INSERT INTO kg_triples(kb_id, s, p, o, confidence, source_chunk)
            VALUES %s
            """

    with conn.cursor() as cur:
        if _write_mode() == "copy":
            # COPY 模式：使用临时表批量导入
            cur.execute(
                """
                CREATE TEMP TABLE tmp_kg_triples_upload(
                    kb_id VARCHAR(255),
                    s TEXT,
                    p TEXT,
                    o TEXT,
                    confidence REAL,
                    source_chunk UUID
                ) ON COMMIT DROP
                """
            )
            _copy_rows(
                cur,
                """
                COPY tmp_kg_triples_upload(kb_id, s, p, o, confidence, source_chunk)
                FROM STDIN WITH (FORMAT CSV, NULL '\\N')
                """,
                rows,
            )
            cur.execute(
                """
                INSERT INTO kg_triples(kb_id, s, p, o, confidence, source_chunk)
                SELECT kb_id, s, p, o, confidence, source_chunk
                FROM tmp_kg_triples_upload
                """
            )
        elif execute_values is not None:
            # VALUES 模式（psycopg2）
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s)",
                page_size=page_size,
            )
        else:
            # VALUES 模式（降级）
            cur.executemany(
                """
                INSERT INTO kg_triples(kb_id, s, p, o, confidence, source_chunk)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )
    return len(rows)


def write_to_pgvector(
    chunks: list,
    embeddings: list,
    kb_id: str = "default",
    pdf_path: str = "",
    filename: str = "",
    source_storage: str = "unknown",
    source_path: str = "",
    source_url: str = "",
    parser_provider: str = "",
) -> dict:
    """
    将切片和向量嵌入写入 pgvector 向量数据库。

    这是模块的主入口函数，负责：
    1. 文档去重（基于文件哈希）
    2. 创建/更新文档记录
    3. 批量写入切片和向量
    4. 写入切片关系和知识图谱三元组
    5. 返回详细的性能指标

    Args:
        chunks: 切片对象列表（Chunk 模型实例）
        embeddings: 对应的向量嵌入列表（与 chunks 一一对应）
        kb_id: 知识库 ID，默认为 'default'
        pdf_path: PDF 文件路径（用于计算哈希）
        filename: 文件名（可选，默认从 pdf_path 提取）
        source_storage: 源存储类型（如 'oss', 'local', 's3'）
        source_path: 源文件路径
        source_url: 源文件 URL
        parser_provider: 解析器提供者（如 'mineru'）

    Returns:
        结果字典，包含以下字段：
        - written: 成功写入的切片数（未跳过时）
        - skipped: 是否跳过（文档未变化时为 True）
        - reason: 跳过原因（如 'document unchanged'）
        - document_id: 文档 UUID
        - kb_id: 知识库 ID
        - metrics: 性能指标字典（各阶段耗时，单位毫秒）

    性能指标（metrics）包括：
        - pgvectorWriteMode: 写入模式（'values' 或 'copy'）
        - pgvectorHashMs: 文件哈希计算耗时
        - pgvectorConnectMs: 数据库连接耗时
        - pgvectorSchemaMs: Schema 初始化耗时
        - pgvectorFindExistingMs: 查找已存在文档耗时
        - pgvectorDocumentUpsertMs: 文档 upsert 耗时
        - pgvectorChunksWriteMs: 切片写入耗时
        - pgvectorChunkRows: 写入的切片数
        - pgvectorRelationsWriteMs: 关系写入耗时
        - pgvectorRelationRows: 写入的关系数
        - pgvectorTriplesWriteMs: 三元组写入耗时
        - pgvectorTripleRows: 写入的三元组数
        - pgvectorCommitMs: 事务提交耗时
        - pgvectorTotalMs: 总耗时

    异常处理:
        - 数据库连接失败会抛出异常
        - 写入失败会回滚事务
        - 使用 try-finally 确保连接关闭

    使用示例:
        >>> chunks = [chunk1, chunk2, chunk3]
        >>> embeddings = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
        >>> result = write_to_pgvector(
        ...     chunks=chunks,
        ...     embeddings=embeddings,
        ...     kb_id="my_kb",
        ...     pdf_path="/path/to/document.pdf"
        ... )
        >>> print(f"Wrote {result['written']} chunks in {result['metrics']['pgvectorTotalMs']}ms")
    """
    # 确保默认知识库存在
    if kb_id == "default":
        ensure_default_kb()

    total_start = time.perf_counter()
    timings: dict[str, int | str] = {"pgvectorWriteMode": _write_mode()}

    # 步骤 1：计算文件哈希（用于去重）
    hash_start = time.perf_counter()
    file_hash = compute_file_hash(pdf_path) if pdf_path else ""
    timings["pgvectorHashMs"] = _elapsed_ms(hash_start)

    # 确定文件名（优先使用参数，否则从路径提取）
    resolved_filename = filename or (os.path.basename(pdf_path) if pdf_path else "unknown")

    # 步骤 2：建立数据库连接
    connect_start = time.perf_counter()
    conn = get_db_connection()
    timings["pgvectorConnectMs"] = _elapsed_ms(connect_start)

    try:
        # 步骤 3：确保数据库 schema 存在（表、索引等）
        schema_start = time.perf_counter()
        schema_ran = ensure_db_schema(conn)
        timings["pgvectorSchemaMs"] = _elapsed_ms(schema_start)
        timings["pgvectorSchemaRan"] = int(schema_ran)

        # 步骤 4：检查文档是否已存在（增量更新）
        if file_hash:
            find_start = time.perf_counter()
            existing_document_id = find_document_id_by_hash(conn, kb_id, file_hash)
            timings["pgvectorFindExistingMs"] = _elapsed_ms(find_start)

            # 如果文档已存在，更新元数据并跳过切片写入
            if existing_document_id:
                document_start = time.perf_counter()
                document_id = _upsert_document_with_optional_source(
                    conn,
                    kb_id,
                    resolved_filename,
                    file_hash,
                    len(chunks),
                    source_storage,
                    source_path,
                    source_url,
                    parser_provider,
                )
                timings["pgvectorDocumentUpsertMs"] = _elapsed_ms(document_start)

                # 提交事务
                commit_start = time.perf_counter()
                conn.commit()
                timings["pgvectorCommitMs"] = _elapsed_ms(commit_start)
                timings["pgvectorTotalMs"] = _elapsed_ms(total_start)

                # 返回跳过结果
                return {
                    "skipped": True,
                    "reason": "document unchanged",
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "metrics": timings,
                }

        # 步骤 5：创建或更新文档记录
        document_start = time.perf_counter()
        document_id = _upsert_document_with_optional_source(
            conn,
            kb_id,
            resolved_filename,
            file_hash or "no-hash",
            len(chunks),
            source_storage,
            source_path,
            source_url,
            parser_provider,
        )
        timings["pgvectorDocumentUpsertMs"] = _elapsed_ms(document_start)

        # 步骤 6：批量写入切片和向量嵌入
        chunks_start = time.perf_counter()
        written = write_chunks_batch(conn, chunks, embeddings, kb_id, document_id)
        timings["pgvectorChunksWriteMs"] = _elapsed_ms(chunks_start)
        timings["pgvectorChunkRows"] = written

        # 步骤 7：写入切片关系（如有）
        relations_start = time.perf_counter()
        relations_written = write_chunk_relations_batch(conn, chunks, kb_id)
        timings["pgvectorRelationsWriteMs"] = _elapsed_ms(relations_start)
        timings["pgvectorRelationRows"] = relations_written

        # 步骤 8：写入知识图谱三元组（如有）
        triples_start = time.perf_counter()
        triples_written = write_kg_triples_batch(conn, chunks, kb_id)
        timings["pgvectorTriplesWriteMs"] = _elapsed_ms(triples_start)
        timings["pgvectorTripleRows"] = triples_written

        # 步骤 9：提交事务
        commit_start = time.perf_counter()
        conn.commit()
        timings["pgvectorCommitMs"] = _elapsed_ms(commit_start)
    finally:
        # 确保连接关闭
        conn.close()

    timings["pgvectorTotalMs"] = _elapsed_ms(total_start)

    # 返回成功结果
    return {
        "written": written,
        "document_id": document_id,
        "kb_id": kb_id,
        "skipped": False,
        "metrics": timings,
    }
