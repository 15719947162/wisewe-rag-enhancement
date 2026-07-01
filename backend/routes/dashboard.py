from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.db.connection import get_db_connection

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats() -> dict:
    try:
        return _live_stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _live_stats() -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM knowledge_bases")
            kb_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM documents")
            doc_count = cur.fetchone()[0]

            cur.execute("SELECT COALESCE(SUM(chunk_count), 0) FROM documents")
            chunk_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT id, kb_id, filename, updated_at
                FROM documents
                ORDER BY updated_at DESC
                LIMIT 10
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    recent_tasks = [
        {
            "id": str(r[0]),
            "kbId": r[1],
            "documentName": r[2],
            "status": "success",
            "updatedAt": r[3].isoformat() if r[3] else "",
        }
        for r in rows
    ]

    return {
        "kb_count": kb_count,
        "doc_count": doc_count,
        "chunk_count": int(chunk_count),
        "recent_tasks": recent_tasks,
    }
