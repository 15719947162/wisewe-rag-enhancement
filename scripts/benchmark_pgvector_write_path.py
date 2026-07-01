from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.connection import get_db_url
from core.db.schema import INIT_SQLS


DEFAULT_SOURCE_KB = "bench-p33-c4-no-llm-layout-full-20260615-082020-da45"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def connect_db(db_url: str | None = None):
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError("psycopg2 is required for this benchmark") from exc

    return psycopg2.connect(db_url or get_db_url(), client_encoding="UTF8")


def db_info(db_url: str | None = None) -> dict[str, str]:
    parsed = urlparse(db_url or get_db_url())
    return {
        "host": parsed.hostname or "",
        "port": str(parsed.port or ""),
        "db": parsed.path.lstrip("/"),
        "user": parsed.username or "",
    }


def ensure_target_schema(db_url: str | None) -> int:
    started_at = time.perf_counter()
    conn = connect_db(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for sql in INIT_SQLS:
                cur.execute(sql)
    finally:
        conn.close()
    return elapsed_ms(started_at)


def csv_value(value: Any) -> Any:
    return r"\N" if value is None else value


def build_csv_buffer(rows: list[tuple[Any, ...]]) -> tuple[io.StringIO, int, int]:
    started_at = time.perf_counter()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([csv_value(value) for value in row])
    size = buffer.tell()
    buffer.seek(0)
    return buffer, size, elapsed_ms(started_at)


def fetch_source_chunks(cur, source_kb: str, limit: int | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    sql = """
        SELECT
            id::text,
            content,
            source,
            page,
            chunk_index,
            strategy,
            title,
            char_count,
            is_table_chunk,
            is_image_chunk,
            image_path,
            layer,
            parent_id::text,
            related_ids,
            search_text,
            embedding::text
        FROM chunks
        WHERE kb_id = %s
        ORDER BY chunk_index, id
    """
    params: tuple[Any, ...]
    if limit and limit > 0:
        sql += " LIMIT %s"
        params = (source_kb, limit)
    else:
        params = (source_kb,)

    cur.execute(sql, params)
    rows = cur.fetchall()
    id_map = {row[0]: str(uuid.uuid4()) for row in rows}
    chunks: list[dict[str, Any]] = []
    for row in rows:
        chunks.append(
            {
                "old_id": row[0],
                "id": id_map[row[0]],
                "content": row[1],
                "source": row[2],
                "page": row[3],
                "chunk_index": row[4],
                "strategy": row[5],
                "title": row[6],
                "char_count": row[7],
                "is_table_chunk": row[8],
                "is_image_chunk": row[9],
                "image_path": row[10],
                "layer": row[11],
                "parent_id": id_map.get(row[12]) if row[12] else None,
                "related_ids": row[13],
                "search_text": row[14] or "",
                "embedding_text": row[15],
            }
        )
    return chunks, id_map


def fetch_source_relations(cur, source_kb: str, id_map: dict[str, str]) -> list[tuple[Any, ...]]:
    cur.execute(
        """
        SELECT src_id::text, dst_id::text, rel_type, weight, source, evidence
        FROM chunk_relations
        WHERE kb_id = %s
        ORDER BY id
        """,
        (source_kb,),
    )
    rows = []
    for src_id, dst_id, rel_type, weight, source, evidence in cur.fetchall():
        mapped_src = id_map.get(src_id)
        mapped_dst = id_map.get(dst_id)
        if not mapped_src or not mapped_dst:
            continue
        rows.append((mapped_src, mapped_dst, rel_type, weight, source, evidence))
    return rows


def create_temp_chunk_tables(cur) -> None:
    cur.execute(
        """
        CREATE TEMP TABLE tmp_bench_chunks_upload(
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
    cur.execute(
        """
        CREATE TEMP TABLE tmp_bench_chunks_typed(
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
            search_vector tsvector,
            embedding vector(1024)
        ) ON COMMIT DROP
        """
    )


def build_chunk_rows(chunks: list[dict[str, Any]], kb_id: str, document_id: str) -> list[tuple[Any, ...]]:
    rows = []
    for chunk in chunks:
        search_text = chunk["search_text"] or " ".join(
            part.strip()
            for part in [chunk["title"] or "", chunk["content"] or "", chunk["source"] or ""]
            if part and part.strip()
        )
        rows.append(
            (
                chunk["id"],
                kb_id,
                document_id,
                chunk["content"],
                chunk["source"],
                chunk["page"],
                chunk["chunk_index"],
                chunk["strategy"],
                chunk["title"],
                chunk["char_count"],
                chunk["is_table_chunk"],
                chunk["is_image_chunk"],
                chunk["image_path"],
                chunk["layer"],
                chunk["parent_id"],
                chunk["related_ids"],
                search_text,
                search_text,
                chunk["embedding_text"],
            )
        )
    return rows


def copy_chunks_to_temp(cur, rows: list[tuple[Any, ...]]) -> dict[str, int]:
    buffer, size, csv_build_ms = build_csv_buffer(rows)
    started_at = time.perf_counter()
    cur.copy_expert(
        """
        COPY tmp_bench_chunks_upload(
            id, kb_id, document_id, content, source, page, chunk_index,
            strategy, title, char_count, is_table_chunk, is_image_chunk,
            image_path, layer, parent_id, related_ids, search_text,
            search_vector_text, embedding_text
        )
        FROM STDIN WITH (FORMAT CSV, NULL '\\N')
        """,
        buffer,
    )
    return {
        "chunkCsvBytes": size,
        "chunkCsvBuildMs": csv_build_ms,
        "chunkCopyToTempMs": elapsed_ms(started_at),
    }


def insert_chunks_to_temp_typed(cur) -> int:
    started_at = time.perf_counter()
    cur.execute(
        """
        INSERT INTO tmp_bench_chunks_typed(
            id, kb_id, document_id, content, source, page, chunk_index,
            strategy, title, char_count, is_table_chunk, is_image_chunk,
            image_path, layer, parent_id, related_ids, search_text, search_vector, embedding
        )
        SELECT
            id, kb_id, document_id, content, source, page, chunk_index,
            strategy, title, char_count, is_table_chunk, is_image_chunk,
            image_path, layer, parent_id, related_ids, search_text,
            to_tsvector('simple', search_vector_text), embedding_text::vector
        FROM tmp_bench_chunks_upload
        """
    )
    return elapsed_ms(started_at)


def insert_chunks_to_actual(cur, source: str) -> int:
    started_at = time.perf_counter()
    if source == "typed":
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
                image_path, layer, parent_id, related_ids, search_text, search_vector, embedding
            FROM tmp_bench_chunks_typed
            ON CONFLICT(id) DO NOTHING
            """
        )
    else:
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
            FROM tmp_bench_chunks_upload
            ON CONFLICT(id) DO NOTHING
            """
        )
    return elapsed_ms(started_at)


def benchmark_relations(cur, kb_id: str, relation_source_rows: list[tuple[Any, ...]]) -> dict[str, int]:
    rows = [
        (kb_id, src_id, dst_id, rel_type, weight, source, evidence)
        for src_id, dst_id, rel_type, weight, source, evidence in relation_source_rows
    ]
    if not rows:
        return {
            "relationRows": 0,
            "relationCsvBytes": 0,
            "relationCsvBuildMs": 0,
            "relationCopyToTempMs": 0,
            "relationInsertActualMs": 0,
        }

    cur.execute(
        """
        CREATE TEMP TABLE tmp_bench_chunk_relations_upload(
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
    buffer, size, csv_build_ms = build_csv_buffer(rows)
    started_at = time.perf_counter()
    cur.copy_expert(
        """
        COPY tmp_bench_chunk_relations_upload(
            kb_id, src_id, dst_id, rel_type, weight, source, evidence
        )
        FROM STDIN WITH (FORMAT CSV, NULL '\\N')
        """,
        buffer,
    )
    copy_ms = elapsed_ms(started_at)

    started_at = time.perf_counter()
    cur.execute(
        """
        INSERT INTO chunk_relations(
            kb_id, src_id, dst_id, rel_type, weight, source, evidence
        )
        SELECT kb_id, src_id, dst_id, rel_type, weight, source, evidence
        FROM tmp_bench_chunk_relations_upload
        ON CONFLICT(kb_id, src_id, dst_id, rel_type) DO NOTHING
        """
    )
    insert_ms = elapsed_ms(started_at)
    return {
        "relationRows": len(rows),
        "relationCsvBytes": size,
        "relationCsvBuildMs": csv_build_ms,
        "relationCopyToTempMs": copy_ms,
        "relationInsertActualMs": insert_ms,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    load_env_file(Path(args.env_file))
    started_at = now_iso()
    total_start = time.perf_counter()
    source_url = args.source_db_url or get_db_url()
    target_url = args.target_db_url or get_db_url()
    ensure_schema_ms = ensure_target_schema(target_url) if args.ensure_target_schema else 0

    source_conn = connect_db(source_url)
    try:
        with source_conn.cursor() as source_cur:
            fetch_start = time.perf_counter()
            chunks, id_map = fetch_source_chunks(source_cur, args.source_kb, args.limit)
            relation_source_rows = fetch_source_relations(source_cur, args.source_kb, id_map)
            fetch_ms = elapsed_ms(fetch_start)
    finally:
        source_conn.close()

    if not chunks:
        raise RuntimeError(f"No chunks found for source KB: {args.source_kb}")

    conn = connect_db(target_url)
    rolled_back = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (f"{args.statement_timeout_seconds}s",))
            cur.execute("SET LOCAL lock_timeout = %s", (f"{args.lock_timeout_seconds}s",))
            kb_id = f"bench-writepath-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            cur.execute(
                """
                INSERT INTO knowledge_bases(id, name, description)
                VALUES(%s, %s, %s)
                """,
                (kb_id, kb_id, "pgvector write path benchmark; rolled back"),
            )
            cur.execute(
                """
                INSERT INTO documents(kb_id, filename, file_hash, chunk_count)
                VALUES(%s, %s, %s, %s)
                RETURNING id::text
                """,
                (kb_id, "write-path-benchmark.pdf", uuid.uuid4().hex, len(chunks)),
            )
            document_id = cur.fetchone()[0]

            temp_start = time.perf_counter()
            create_temp_chunk_tables(cur)
            temp_create_ms = elapsed_ms(temp_start)

            row_start = time.perf_counter()
            chunk_rows = build_chunk_rows(chunks, kb_id, document_id)
            chunk_row_build_ms = elapsed_ms(row_start)

            copy_metrics = copy_chunks_to_temp(cur, chunk_rows)
            temp_typed_ms = insert_chunks_to_temp_typed(cur)
            actual_indexed_ms = insert_chunks_to_actual(cur, args.actual_source)
            relation_metrics = benchmark_relations(cur, kb_id, relation_source_rows)

        rollback_start = time.perf_counter()
        conn.rollback()
        rolled_back = True
        rollback_ms = elapsed_ms(rollback_start)
    finally:
        if not rolled_back:
            conn.rollback()
        conn.close()

    return {
        "startedAt": started_at,
        "finishedAt": now_iso(),
        "ok": True,
        "sourceKb": args.source_kb,
        "limit": args.limit,
        "sourceDb": db_info(source_url),
        "targetDb": db_info(target_url),
        "dbHost": db_info(target_url)["host"],
        "dbPort": db_info(target_url)["port"],
        "dbName": db_info(target_url)["db"],
        "actualSource": args.actual_source,
        "rolledBack": rolled_back,
        "chunkRows": len(chunks),
        "ensureTargetSchemaMs": ensure_schema_ms,
        "fetchSourceMs": fetch_ms,
        "tempCreateMs": temp_create_ms,
        "chunkRowBuildMs": chunk_row_build_ms,
        **copy_metrics,
        "chunkInsertTempTypedMs": temp_typed_ms,
        "chunkInsertActualIndexedMs": actual_indexed_ms,
        **relation_metrics,
        "rollbackMs": rollback_ms,
        "totalMs": elapsed_ms(total_start),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark pgvector chunks write path with real rows.")
    parser.add_argument("--source-kb", default=DEFAULT_SOURCE_KB)
    parser.add_argument("--limit", type=int, default=0, help="Optional chunk row limit; 0 means all rows.")
    parser.add_argument("--output-jsonl", default="data/results/pgvector_write_path_benchmark.jsonl")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--source-db-url", default="")
    parser.add_argument("--target-db-url", default="")
    parser.add_argument("--ensure-target-schema", action="store_true")
    parser.add_argument("--statement-timeout-seconds", type=int, default=180)
    parser.add_argument("--lock-timeout-seconds", type=int, default=10)
    parser.add_argument(
        "--actual-source",
        choices=("upload", "typed"),
        default="upload",
        help="Source used for the real indexed chunks insert.",
    )
    args = parser.parse_args()
    args.limit = args.limit or None

    try:
        record = run_benchmark(args)
    except Exception as exc:  # noqa: BLE001 - benchmark should persist failure records
        record = {
            "startedAt": now_iso(),
            "finishedAt": now_iso(),
            "ok": False,
            "sourceKb": args.source_kb,
            "limit": args.limit,
            "error": str(exc),
        }

    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
