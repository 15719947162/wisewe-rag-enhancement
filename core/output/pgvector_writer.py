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
    return int((time.perf_counter() - started_at) * 1000)


def _write_page_size() -> int:
    return max(int(os.environ.get("PGVECTOR_WRITE_PAGE_SIZE", "500")), 1)


def _write_mode() -> str:
    mode = os.environ.get("PGVECTOR_WRITE_MODE", "values").strip().lower()
    return mode if mode in {"values", "copy"} else "values"


def _vector_literal(embedding: Any) -> str:
    return json.dumps([float(value) for value in embedding], separators=(",", ":"), allow_nan=False)


def _csv_value(value: Any) -> Any:
    return r"\N" if value is None else value


def _copy_rows(cur, copy_sql: str, rows: list[tuple[Any, ...]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])
    buffer.seek(0)
    cur.copy_expert(copy_sql, buffer)


def compute_file_hash(pdf_path: str) -> str:
    """Return SHA-256 hex digest of file contents, or empty string if file missing."""
    if not pdf_path or not os.path.exists(pdf_path):
        return ""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_document_id_by_hash(conn, kb_id: str, file_hash: str) -> str | None:
    """Return document id for a known hash in the knowledge base, if present."""
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
    """Insert or update a document record, returning its UUID."""
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
    parts = [
        getattr(chunk, "title", "") or "",
        getattr(chunk, "content", "") or "",
        getattr(chunk, "source", "") or "",
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def write_chunks_batch(conn, chunks: list, embeddings: list, kb_id: str, document_id: str) -> int:
    """Batch-insert chunks with embeddings into the chunks table."""
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
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple', %s),%s)",
                page_size=page_size,
            )
        else:
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
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s)",
                page_size=page_size,
            )
        else:
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
    rows: list[tuple[Any, ...]] = []
    for chunk in chunks:
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
            page_size = _write_page_size()
            execute_values(
                cur,
                insert_sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s)",
                page_size=page_size,
            )
        else:
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
    """Write chunks and embeddings to pgvector. Returns result dict."""
    if kb_id == "default":
        ensure_default_kb()

    total_start = time.perf_counter()
    timings: dict[str, int | str] = {"pgvectorWriteMode": _write_mode()}
    hash_start = time.perf_counter()
    file_hash = compute_file_hash(pdf_path) if pdf_path else ""
    timings["pgvectorHashMs"] = _elapsed_ms(hash_start)
    resolved_filename = filename or (os.path.basename(pdf_path) if pdf_path else "unknown")

    connect_start = time.perf_counter()
    conn = get_db_connection()
    timings["pgvectorConnectMs"] = _elapsed_ms(connect_start)
    try:
        schema_start = time.perf_counter()
        schema_ran = ensure_db_schema(conn)
        timings["pgvectorSchemaMs"] = _elapsed_ms(schema_start)
        timings["pgvectorSchemaRan"] = int(schema_ran)

        if file_hash:
            find_start = time.perf_counter()
            existing_document_id = find_document_id_by_hash(conn, kb_id, file_hash)
            timings["pgvectorFindExistingMs"] = _elapsed_ms(find_start)
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
                commit_start = time.perf_counter()
                conn.commit()
                timings["pgvectorCommitMs"] = _elapsed_ms(commit_start)
                timings["pgvectorTotalMs"] = _elapsed_ms(total_start)
                return {
                    "skipped": True,
                    "reason": "document unchanged",
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "metrics": timings,
                }

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
        chunks_start = time.perf_counter()
        written = write_chunks_batch(conn, chunks, embeddings, kb_id, document_id)
        timings["pgvectorChunksWriteMs"] = _elapsed_ms(chunks_start)
        timings["pgvectorChunkRows"] = written
        relations_start = time.perf_counter()
        relations_written = write_chunk_relations_batch(conn, chunks, kb_id)
        timings["pgvectorRelationsWriteMs"] = _elapsed_ms(relations_start)
        timings["pgvectorRelationRows"] = relations_written
        triples_start = time.perf_counter()
        triples_written = write_kg_triples_batch(conn, chunks, kb_id)
        timings["pgvectorTriplesWriteMs"] = _elapsed_ms(triples_start)
        timings["pgvectorTripleRows"] = triples_written
        commit_start = time.perf_counter()
        conn.commit()
        timings["pgvectorCommitMs"] = _elapsed_ms(commit_start)
    finally:
        conn.close()

    timings["pgvectorTotalMs"] = _elapsed_ms(total_start)
    return {
        "written": written,
        "document_id": document_id,
        "kb_id": kb_id,
        "skipped": False,
        "metrics": timings,
    }
