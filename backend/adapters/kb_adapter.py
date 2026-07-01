from __future__ import annotations

from typing import Any

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext
from core.db.knowledge_base import list_knowledge_bases


def fetch_knowledge_bases(identity: IdentityContext | None = None) -> list[dict]:
    return list_knowledge_bases(identity)


def fetch_documents(kb_id: str | None = None) -> list[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """
                    SELECT id, kb_id, filename, file_hash, chunk_count, created_at, updated_at,
                           source_storage, source_path, source_url, parser_provider
                    FROM documents
                    WHERE kb_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (kb_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, kb_id, filename, file_hash, chunk_count, created_at, updated_at,
                           source_storage, source_path, source_url, parser_provider
                    FROM documents
                    ORDER BY updated_at DESC
                    """
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": str(row[0]),
            "kb_id": row[1],
            "filename": row[2],
            "file_hash": row[3],
            "chunk_count": int(row[4] or 0),
            "created_at": row[5],
            "updated_at": row[6],
            "source_storage": row[7] or "unknown",
            "source_path": row[8] or "",
            "source_url": row[9] or "",
            "parser_provider": row[10] or "",
        }
        for row in rows
    ]


def fetch_document_detail(document_id: str) -> dict[str, Any] | None:
    record = fetch_document_export_record(document_id)
    if record is None:
        return None
    return record


def fetch_document_graph(document_id: str, limit: int = 100) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, kb_id, filename
                FROM documents
                WHERE id::text = %s
                LIMIT 1
                """,
                (document_id,),
            )
            document_row = cur.fetchone()
            if not document_row:
                return None

            document_uuid = str(document_row[0])
            kb_id = document_row[1]
            cur.execute(
                """
                SELECT id, content, source, page, chunk_index, title, layer,
                       is_table_chunk, is_image_chunk, image_path
                FROM chunks
                WHERE document_id::text = %s
                ORDER BY chunk_index ASC, created_at ASC
                """,
                (document_uuid,),
            )
            chunk_rows = cur.fetchall()

            chunk_ids = [str(row[0]) for row in chunk_rows]
            relation_rows: list[tuple[Any, ...]] = []
            mention_rows: list[tuple[Any, ...]] = []
            triple_rows: list[tuple[Any, ...]] = []
            if chunk_ids:
                cur.execute(
                    """
                    SELECT src_id::text, dst_id::text, rel_type, weight, source, evidence
                    FROM chunk_relations
                    WHERE kb_id = %s
                      AND src_id::text = ANY(%s)
                      AND dst_id::text = ANY(%s)
                    ORDER BY rel_type, src_id, dst_id
                    """,
                    (kb_id, chunk_ids, chunk_ids),
                )
                relation_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT em.chunk_id::text, e.id::text, e.name, e.type, e.definition
                    FROM entity_mentions em
                    JOIN entities e ON e.id = em.entity_id AND e.kb_id = em.kb_id
                    WHERE em.kb_id = %s
                      AND em.chunk_id::text = ANY(%s)
                    ORDER BY e.name, em.chunk_id
                    """,
                    (kb_id, chunk_ids),
                )
                mention_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT id, s, p, o, confidence, source_chunk::text
                    FROM kg_triples
                    WHERE kb_id = %s
                      AND source_chunk::text = ANY(%s)
                    ORDER BY confidence DESC, s, p, o
                    """,
                    (kb_id, chunk_ids),
                )
                triple_rows = cur.fetchall()
    finally:
        conn.close()

    return _build_document_graph_payload(
        document_id=document_uuid,
        filename=document_row[2] or "",
        chunk_rows=chunk_rows,
        relation_rows=relation_rows,
        mention_rows=mention_rows,
        triple_rows=triple_rows,
        limit=max(int(limit or 100), 1),
    )


def fetch_knowledge_base_graph(kb_id: str, limit: int = 200) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name
                FROM knowledge_bases
                WHERE id = %s
                LIMIT 1
                """,
                (kb_id,),
            )
            kb_row = cur.fetchone()
            if not kb_row:
                return None

            normalized_limit = max(int(limit or 200), 1)
            cur.execute("SELECT COUNT(*) FROM documents WHERE kb_id = %s", (kb_id,))
            document_count = int((cur.fetchone() or [0])[0] or 0)

            cur.execute("SELECT COUNT(*) FROM chunks WHERE kb_id = %s", (kb_id,))
            total_chunk_count = int((cur.fetchone() or [0])[0] or 0)

            cur.execute(
                """
                SELECT src_id::text, dst_id::text
                FROM chunk_relations
                WHERE kb_id = %s
                ORDER BY weight DESC NULLS LAST, rel_type, src_id, dst_id
                LIMIT %s
                """,
                (kb_id, max(normalized_limit * 4, normalized_limit)),
            )
            relation_seed_rows = cur.fetchall()
            selected_chunk_ids = _select_graph_preview_chunk_ids(relation_seed_rows, normalized_limit)

            if len(selected_chunk_ids) < normalized_limit:
                cur.execute(
                    """
                    SELECT c.id::text
                    FROM chunks c
                    LEFT JOIN documents d ON d.id = c.document_id
                    WHERE c.kb_id = %s
                    ORDER BY d.updated_at DESC NULLS LAST, c.document_id, c.chunk_index ASC, c.created_at ASC
                    LIMIT %s
                    """,
                    (kb_id, max((normalized_limit - len(selected_chunk_ids)) * 2, normalized_limit)),
                )
                for (chunk_id,) in cur.fetchall():
                    if chunk_id not in selected_chunk_ids:
                        selected_chunk_ids.append(chunk_id)
                    if len(selected_chunk_ids) >= normalized_limit:
                        break

            chunk_rows: list[tuple[Any, ...]] = []
            if selected_chunk_ids:
                cur.execute(
                    """
                    SELECT c.id, c.content, c.source, c.page, c.chunk_index, c.title, c.layer,
                           c.is_table_chunk, c.is_image_chunk, c.image_path,
                           c.document_id::text, COALESCE(d.filename, c.source, '')
                    FROM chunks c
                    LEFT JOIN documents d ON d.id = c.document_id
                    WHERE c.kb_id = %s
                      AND c.id::text = ANY(%s)
                    """,
                    (kb_id, selected_chunk_ids),
                )
                order = {chunk_id: index for index, chunk_id in enumerate(selected_chunk_ids)}
                chunk_rows = sorted(cur.fetchall(), key=lambda row: order.get(str(row[0]), len(order)))

            chunk_ids = [str(row[0]) for row in chunk_rows]
            relation_rows: list[tuple[Any, ...]] = []
            mention_rows: list[tuple[Any, ...]] = []
            triple_rows: list[tuple[Any, ...]] = []
            if chunk_ids:
                cur.execute(
                    """
                    SELECT src_id::text, dst_id::text, rel_type, weight, source, evidence
                    FROM chunk_relations
                    WHERE kb_id = %s
                      AND src_id::text = ANY(%s)
                      AND dst_id::text = ANY(%s)
                    ORDER BY rel_type, src_id, dst_id
                    """,
                    (kb_id, chunk_ids, chunk_ids),
                )
                relation_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT em.chunk_id::text, e.id::text, e.name, e.type, e.definition
                    FROM entity_mentions em
                    JOIN entities e ON e.id = em.entity_id AND e.kb_id = em.kb_id
                    WHERE em.kb_id = %s
                      AND em.chunk_id::text = ANY(%s)
                    ORDER BY e.name, em.chunk_id
                    """,
                    (kb_id, chunk_ids),
                )
                mention_rows = cur.fetchall()

                cur.execute(
                    """
                    SELECT id, s, p, o, confidence, source_chunk::text
                    FROM kg_triples
                    WHERE kb_id = %s
                      AND source_chunk::text = ANY(%s)
                    ORDER BY confidence DESC, s, p, o
                    """,
                    (kb_id, chunk_ids),
                )
                triple_rows = cur.fetchall()
    finally:
        conn.close()

    payload = _build_document_graph_payload(
        document_id=f"kb:{kb_id}",
        filename=kb_row[1] or kb_id,
        chunk_rows=chunk_rows,
        relation_rows=relation_rows,
        mention_rows=mention_rows,
        triple_rows=triple_rows,
        limit=normalized_limit,
    )
    payload["kbId"] = kb_id
    payload["scope"] = "knowledge_base"
    payload["stats"]["documentCount"] = document_count
    payload["stats"]["totalChunkCount"] = total_chunk_count
    payload["stats"]["selectedChunkCount"] = len(chunk_rows)
    payload["stats"]["truncated"] = bool(payload["stats"]["truncated"] or total_chunk_count > len(chunk_rows))
    return payload


def delete_document(document_id: str) -> int:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text
                FROM chunks
                WHERE document_id::text = %s
                """,
                (document_id,),
            )
            chunk_ids = [row[0] for row in cur.fetchall()]

            if chunk_ids:
                cur.execute("DELETE FROM entity_mentions WHERE chunk_id::text = ANY(%s)", (chunk_ids,))
                cur.execute("DELETE FROM chunk_relations WHERE src_id::text = ANY(%s) OR dst_id::text = ANY(%s)", (chunk_ids, chunk_ids))
                cur.execute("DELETE FROM kg_triples WHERE source_chunk::text = ANY(%s)", (chunk_ids,))

            cur.execute("DELETE FROM documents WHERE id::text = %s", (document_id,))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def fetch_document_source_record(document_id: str) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, source_storage, source_path, source_url, parser_provider
                FROM documents
                WHERE id::text = %s
                LIMIT 1
                """,
                (document_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None
    return {
        "id": str(row[0]),
        "filename": row[1] or "document.pdf",
        "source_storage": row[2] or "unknown",
        "source_path": row[3] or "",
        "source_url": row[4] or "",
        "parser_provider": row[5] or "",
    }


def fetch_document_export_record(document_id: str) -> dict[str, Any] | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, kb_id, filename, file_hash, chunk_count, created_at, updated_at,
                       source_storage, source_path, source_url, parser_provider
                FROM documents
                WHERE id::text = %s
                LIMIT 1
                """,
                (document_id,),
            )
            document_row = cur.fetchone()
            if not document_row:
                return None

            cur.execute(
                """
                SELECT c.id,
                       c.kb_id,
                       c.document_id,
                       c.source,
                       c.page,
                       c.chunk_index,
                       c.strategy,
                       c.title,
                       c.content,
                       c.layer,
                       c.parent_id,
                       c.related_ids,
                       c.char_count,
                       c.is_table_chunk,
                       c.is_image_chunk,
                       c.image_path,
                       (c.embedding IS NOT NULL) AS has_embedding,
                       c.created_at,
                       COALESCE(
                           (
                               SELECT json_agg(
                                   json_build_object(
                                       'targetId', rel.dst_id,
                                       'relType', rel.rel_type,
                                       'weight', rel.weight,
                                       'source', rel.source,
                                       'evidence', rel.evidence
                                   )
                                   ORDER BY rel.rel_type, rel.dst_id
                               )
                               FROM chunk_relations rel
                               WHERE rel.kb_id = c.kb_id
                                 AND rel.src_id = c.id
                           ),
                           '[]'::json
                       ) AS relations,
                       COALESCE(
                           (
                               SELECT json_agg(
                                   json_build_object(
                                       's', triple.s,
                                       'p', triple.p,
                                       'o', triple.o,
                                       'confidence', triple.confidence
                                   )
                                   ORDER BY triple.s, triple.p, triple.o
                               )
                               FROM kg_triples triple
                               WHERE triple.kb_id = c.kb_id
                                 AND triple.source_chunk = c.id
                           ),
                           '[]'::json
                       ) AS triples
                FROM chunks c
                WHERE c.document_id = %s
                ORDER BY c.chunk_index ASC, c.created_at ASC
                """,
                (str(document_row[0]),),
            )
            chunk_rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "document": {
            "id": str(document_row[0]),
            "kb_id": document_row[1],
            "filename": document_row[2],
            "file_hash": document_row[3],
            "chunk_count": int(document_row[4] or 0),
            "created_at": document_row[5],
            "updated_at": document_row[6],
            "source_storage": document_row[7] or "unknown",
            "source_path": document_row[8] or "",
            "source_url": document_row[9] or "",
            "parser_provider": document_row[10] or "",
        },
        "chunks": [
            {
                "id": str(row[0]),
                "kb_id": row[1],
                "document_id": str(row[2]),
                "source": row[3] or "",
                "page": int(row[4] or 0),
                "chunk_index": int(row[5] or 0),
                "strategy": row[6] or "",
                "title": row[7] or "",
                "content": row[8] or "",
                "layer": row[9] or "child",
                "parent_id": str(row[10]) if row[10] else "",
                "related_ids": row[11] or "",
                "char_count": int(row[12] or 0),
                "is_table_chunk": bool(row[13]),
                "is_image_chunk": bool(row[14]),
                "image_path": row[15] or "",
                "has_embedding": bool(row[16]),
                "created_at": row[17],
                "relations": row[18] if isinstance(row[18], list) else [],
                "triples": row[19] if isinstance(row[19], list) else [],
            }
            for row in chunk_rows
        ],
    }


def _chunk_type(is_table: bool, is_image: bool) -> str:
    if is_image:
        return "image"
    if is_table:
        return "table"
    return "text"


def _select_graph_preview_chunk_ids(relation_rows: list[tuple[Any, ...]], limit: int) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    normalized_limit = max(int(limit or 0), 0)
    if normalized_limit <= 0:
        return selected

    for row in relation_rows:
        if len(row) < 2:
            continue
        for raw_chunk_id in (row[0], row[1]):
            chunk_id = str(raw_chunk_id) if raw_chunk_id else ""
            if not chunk_id or chunk_id in seen:
                continue
            selected.append(chunk_id)
            seen.add(chunk_id)
            if len(selected) >= normalized_limit:
                return selected
    return selected


def _short_label(value: str, max_len: int = 28) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_len:
        return text
    return f"{text[:max_len - 1]}…"


def _term_node_id(value: str) -> str:
    safe = (value or "").strip().replace(":", "_")
    return f"entity:triple:{safe[:80]}"


def _build_document_graph_payload(
    document_id: str,
    filename: str,
    chunk_rows: list[tuple[Any, ...]],
    relation_rows: list[tuple[Any, ...]],
    mention_rows: list[tuple[Any, ...]],
    triple_rows: list[tuple[Any, ...]],
    limit: int,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    truncated = False

    def add_node(node: dict[str, Any]) -> bool:
        nonlocal truncated
        node_id = node["id"]
        if node_id in node_ids:
            return True
        if len(nodes) >= limit:
            truncated = True
            return False
        nodes.append(node)
        node_ids.add(node_id)
        return True

    chunk_id_set = {str(row[0]) for row in chunk_rows}
    for row in chunk_rows:
        chunk_id = str(row[0])
        content = row[1] or ""
        page = int(row[3] or 0)
        chunk_index = int(row[4] or 0)
        title = row[5] or ""
        chunk_type = _chunk_type(bool(row[7]), bool(row[8]))
        row_document_id = str(row[10]) if len(row) > 10 and row[10] else document_id
        row_filename = row[11] if len(row) > 11 and row[11] else filename
        add_node(
            {
                "id": f"chunk:{chunk_id}",
                "type": "chunk",
                "label": title or f"P.{page} · #{chunk_index + 1}",
                "chunkType": chunk_type,
                "meta": {
                    "chunkId": chunk_id,
                    "documentId": row_document_id,
                    "filename": row_filename,
                    "source": row[2] or "",
                    "page": page,
                    "chunkIndex": chunk_index,
                    "layer": row[6] or "child",
                    "content": content,
                    "imagePath": row[9] or None,
                },
            }
        )

    for src_id, dst_id, rel_type, weight, source, evidence in relation_rows:
        src_node = f"chunk:{src_id}"
        dst_node = f"chunk:{dst_id}"
        if src_node not in node_ids or dst_node not in node_ids:
            continue
        edges.append(
            {
                "id": f"rel:{src_id}:{dst_id}:{rel_type}",
                "source": src_node,
                "target": dst_node,
                "type": rel_type or "relation",
                "label": rel_type or "relation",
                "weight": float(weight or 1.0),
                "meta": {"source": source or "", "evidence": evidence or ""},
            }
        )

    for chunk_id, entity_id, name, entity_type, definition in mention_rows:
        chunk_node = f"chunk:{chunk_id}"
        entity_node = f"entity:{entity_id}"
        if chunk_node not in node_ids:
            continue
        if add_node(
            {
                "id": entity_node,
                "type": "entity",
                "label": name or "未命名实体",
                "entityType": entity_type or "unknown",
                "meta": {
                    "entityId": entity_id,
                    "entityType": entity_type or "unknown",
                    "definition": definition or "",
                },
            }
        ):
            edges.append(
                {
                    "id": f"mention:{chunk_id}:{entity_id}",
                    "source": chunk_node,
                    "target": entity_node,
                    "type": "mentions",
                    "label": "mentions",
                    "weight": 1.0,
                    "meta": {},
                }
            )

    for triple_id, subject, predicate, obj, confidence, source_chunk in triple_rows:
        chunk_node = f"chunk:{source_chunk}"
        if chunk_node not in node_ids or str(source_chunk) not in chunk_id_set:
            continue
        subject_node = _term_node_id(str(subject))
        object_node = _term_node_id(str(obj))
        add_node(
            {
                "id": subject_node,
                "type": "entity",
                "label": _short_label(str(subject)),
                "entityType": "triple_term",
                "meta": {"term": subject, "source": "kg_triples"},
            }
        )
        add_node(
            {
                "id": object_node,
                "type": "entity",
                "label": _short_label(str(obj)),
                "entityType": "triple_term",
                "meta": {"term": obj, "source": "kg_triples"},
            }
        )
        if subject_node in node_ids and object_node in node_ids:
            edges.append(
                {
                    "id": f"triple:{triple_id}",
                    "source": subject_node,
                    "target": object_node,
                    "type": "triple",
                    "label": predicate or "triple",
                    "weight": float(confidence or 0.0),
                    "meta": {
                        "sourceChunk": source_chunk,
                        "confidence": float(confidence or 0.0),
                    },
                }
            )
            edges.append(
                {
                    "id": f"triple-source:{triple_id}",
                    "source": chunk_node,
                    "target": subject_node,
                    "type": "triple_source",
                    "label": "source",
                    "weight": 0.5,
                    "meta": {},
                }
            )

    return {
        "documentId": document_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "chunkCount": len(chunk_rows),
            "entityCount": sum(1 for node in nodes if node.get("type") == "entity"),
            "tripleCount": len(triple_rows),
            "truncated": truncated,
        },
    }
