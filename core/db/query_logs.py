from __future__ import annotations

import hashlib
import csv
import io
import json
import os
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db.connection import get_db_connection
from core.db.identity import IdentityContext
from core.db.init_db import ensure_db_schema


@dataclass(frozen=True)
class RagQueryLogRecord:
    request_id: str
    pipeline_domain: str
    kb_id: str
    query: str
    answer: str = ""
    identity: IdentityContext | None = None
    cannot_answer: bool = False
    relevance_score: float | None = None
    faithfulness_score: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error_code: str | None = None
    api_key_id: str | None = None


@dataclass(frozen=True)
class LlmCallLogRecord:
    pipeline_domain: str
    pipeline_stage: str
    feature_name: str
    provider: str
    model_name: str
    kb_id: str | None = None
    request_id: str | None = None
    identity: IdentityContext | None = None
    api_key_id: str | None = None
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    status: str = "success"
    error_code: str | None = None


@dataclass(frozen=True)
class AuditLogRecord:
    action: str
    resource_type: str
    resource_id: str | None = None
    identity: IdentityContext | None = None
    request_id: str | None = None
    kb_id: str | None = None
    api_key_id: str | None = None
    outcome: str = "success"
    risk_level: str = "low"
    summary: str = ""
    metadata: dict[str, Any] | None = None


def append_rag_query_log(record: RagQueryLogRecord) -> bool:
    """Append a sanitized query log. Logging failure must not break user queries."""
    try:
        conn = get_db_connection()
    except Exception:
        return False

    identity = record.identity
    api_key_id = record.api_key_id or _api_key_id_from_identity(identity)
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_rag_query_logs(
                    request_id, pipeline_domain, pipeline_stage, tenant_id, actor_id,
                    kb_id, api_key_id, query_hash, query_summary, answer_summary,
                    cannot_answer, relevance_score, faithfulness_score,
                    prompt_tokens, completion_tokens, total_tokens,
                    latency_ms, status, error_code
                )
                VALUES(
                    %s, %s, 'query', %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT(request_id) DO NOTHING
                """,
                (
                    record.request_id,
                    record.pipeline_domain,
                    identity.tenant_id if identity and identity.enforce_access else None,
                    identity.user_id if identity and identity.enforce_access else None,
                    record.kb_id,
                    api_key_id,
                    _sha256(record.query),
                    _summary(record.query),
                    _summary(record.answer),
                    record.cannot_answer,
                    record.relevance_score,
                    record.faithfulness_score,
                    max(0, int(record.prompt_tokens or 0)),
                    max(0, int(record.completion_tokens or 0)),
                    max(0, int(record.total_tokens or 0)),
                    max(0, int(record.latency_ms or 0)),
                    record.status,
                    record.error_code,
                ),
            )
        conn.commit()
        return True
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        return False
    finally:
        conn.close()


def append_llm_call_log(record: LlmCallLogRecord) -> bool:
    """Append a model-call usage log. Logging failure must not break pipelines."""
    try:
        conn = get_db_connection()
    except Exception:
        return False

    identity = record.identity
    api_key_id = record.api_key_id or _api_key_id_from_identity(identity)
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_llm_call_logs(
                    request_id, tenant_id, actor_id, kb_id, api_key_id,
                    pipeline_domain, pipeline_stage, feature_name,
                    provider, model_name, model_version,
                    prompt_tokens, completion_tokens, total_tokens,
                    latency_ms, status, error_code
                )
                VALUES(
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    record.request_id,
                    identity.tenant_id if identity and identity.enforce_access else None,
                    identity.user_id if identity and identity.enforce_access else None,
                    record.kb_id,
                    api_key_id,
                    record.pipeline_domain,
                    record.pipeline_stage,
                    record.feature_name,
                    record.provider,
                    record.model_name,
                    record.model_version,
                    max(0, int(record.prompt_tokens or 0)),
                    max(0, int(record.completion_tokens or 0)),
                    max(0, int(record.total_tokens or 0)),
                    max(0, int(record.latency_ms or 0)),
                    record.status,
                    record.error_code,
                ),
            )
            _upsert_token_usage_hourly(cur, record, identity=identity, api_key_id=api_key_id)
        conn.commit()
        return True
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        return False
    finally:
        conn.close()


def _upsert_token_usage_hourly(
    cur: Any,
    record: LlmCallLogRecord,
    *,
    identity: IdentityContext | None,
    api_key_id: str | None,
) -> None:
    prompt_tokens = max(0, int(record.prompt_tokens or 0))
    completion_tokens = max(0, int(record.completion_tokens or 0))
    total_tokens = max(0, int(record.total_tokens or 0))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    estimated_cost = _estimate_token_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    cur.execute(
        """
        INSERT INTO kb_token_usage_hourly(
            hour_bucket, tenant_id, kb_id, api_key_id, pipeline_domain, pipeline_stage, feature_name,
            request_count, prompt_tokens, completion_tokens, total_tokens,
            latency_ms_sum, error_count, estimated_cost, updated_at
        )
        VALUES(
            date_trunc('hour', NOW()), %s, %s, %s, %s, %s, %s,
            1, %s, %s, %s,
            %s, %s, %s, NOW()
        )
        ON CONFLICT(hour_bucket, tenant_id, kb_id, api_key_id, pipeline_domain, pipeline_stage, feature_name)
        DO UPDATE SET
            request_count = kb_token_usage_hourly.request_count + EXCLUDED.request_count,
            prompt_tokens = kb_token_usage_hourly.prompt_tokens + EXCLUDED.prompt_tokens,
            completion_tokens = kb_token_usage_hourly.completion_tokens + EXCLUDED.completion_tokens,
            total_tokens = kb_token_usage_hourly.total_tokens + EXCLUDED.total_tokens,
            latency_ms_sum = kb_token_usage_hourly.latency_ms_sum + EXCLUDED.latency_ms_sum,
            error_count = kb_token_usage_hourly.error_count + EXCLUDED.error_count,
            estimated_cost = kb_token_usage_hourly.estimated_cost + EXCLUDED.estimated_cost,
            updated_at = NOW()
        """,
        (
            identity.tenant_id if identity and identity.enforce_access else "",
            record.kb_id or "",
            api_key_id or "",
            record.pipeline_domain,
            record.pipeline_stage,
            record.feature_name,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            max(0, int(record.latency_ms or 0)),
            0 if record.status == "success" else 1,
            estimated_cost,
        ),
    )


def refresh_token_usage_hourly(
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict[str, Any]:
    """Rebuild hourly token rollups from call logs for a time window."""
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        if start_at:
            where.append("created_at >= %s")
            params.append(start_at)
        if end_at:
            where.append("created_at <= %s")
            params.append(end_at)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        delete_where = where_sql.replace("created_at", "hour_bucket")

        with conn.cursor() as cur:
            if delete_where:
                cur.execute(f"DELETE FROM kb_token_usage_hourly {delete_where}", tuple(params))
            else:
                cur.execute("DELETE FROM kb_token_usage_hourly")
            deleted = int(getattr(cur, "rowcount", 0) or 0)
            prompt_rate, completion_rate, total_rate = _token_cost_rates()
            cur.execute(
                f"""
                INSERT INTO kb_token_usage_hourly(
                    hour_bucket, tenant_id, kb_id, api_key_id, pipeline_domain, pipeline_stage, feature_name,
                    request_count, prompt_tokens, completion_tokens, total_tokens,
                    latency_ms_sum, error_count, estimated_cost, updated_at
                )
                SELECT
                    date_trunc('hour', created_at) AS hour_bucket,
                    COALESCE(tenant_id, '') AS tenant_id,
                    COALESCE(kb_id, '') AS kb_id,
                    COALESCE(api_key_id, '') AS api_key_id,
                    pipeline_domain,
                    pipeline_stage,
                    feature_name,
                    COUNT(*)::int AS request_count,
                    COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
                    COALESCE(SUM(latency_ms), 0)::bigint AS latency_ms_sum,
                    COUNT(*) FILTER (WHERE status <> 'success')::int AS error_count,
                    (
                        COALESCE(SUM(prompt_tokens), 0) / 1000.0 * %s::numeric
                        + COALESCE(SUM(completion_tokens), 0) / 1000.0 * %s::numeric
                        + COALESCE(SUM(total_tokens), 0) / 1000.0 * %s::numeric
                    )::numeric(18, 6) AS estimated_cost,
                    NOW() AS updated_at
                FROM kb_llm_call_logs
                {where_sql}
                GROUP BY hour_bucket, tenant_id, kb_id, api_key_id, pipeline_domain, pipeline_stage, feature_name
                """,
                (prompt_rate, completion_rate, total_rate, *params),
            )
            inserted = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
        return {"refreshed": True, "deleted": deleted, "inserted": inserted}
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()


def has_llm_call_log(request_id: str, pipeline_stage: str | None = None) -> bool:
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        where = ["request_id = %s"]
        params: list[Any] = [request_id]
        if pipeline_stage:
            where.append("pipeline_stage = %s")
            params.append(pipeline_stage)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM kb_llm_call_logs WHERE {' AND '.join(where)} LIMIT 1",
                tuple(params),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def repair_llm_call_log_identity(
    request_id: str,
    *,
    identity: IdentityContext | None = None,
    kb_id: str | None = None,
    pipeline_stage: str | None = None,
) -> dict[str, Any]:
    """Fill missing tenant/actor scope on existing model-call logs.

    Historical ingestion usage rows may already contain token totals. Updating
    their scope is safer than inserting another row because this table does not
    enforce one row per request/stage.
    """
    if not request_id:
        return {"requestId": request_id, "repaired": False, "updated": 0, "reason": "missing_request_id"}
    if not identity or not identity.enforce_access:
        return {"requestId": request_id, "repaired": False, "updated": 0, "reason": "missing_identity"}

    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        where = [
            "request_id = %s",
            "(tenant_id IS NULL OR tenant_id = '' OR actor_id IS NULL OR actor_id = '' OR kb_id IS NULL OR kb_id = '')",
        ]
        params: list[Any] = [
            identity.tenant_id,
            identity.user_id,
            kb_id or None,
            request_id,
        ]
        if pipeline_stage:
            where.append("pipeline_stage = %s")
            params.append(pipeline_stage)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_llm_call_logs
                SET tenant_id = COALESCE(NULLIF(tenant_id, ''), %s),
                    actor_id = COALESCE(NULLIF(actor_id, ''), %s),
                    kb_id = COALESCE(NULLIF(kb_id, ''), %s)
                WHERE {' AND '.join(where)}
                """,
                tuple(params),
            )
            updated = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
        return {
            "requestId": request_id,
            "pipelineStage": pipeline_stage or "",
            "repaired": updated > 0,
            "updated": updated,
            "reason": "updated" if updated > 0 else "nothing_to_update",
        }
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        conn.close()


def append_audit_log(record: AuditLogRecord) -> bool:
    """Append a sanitized audit event. Audit failure must not break the action."""
    try:
        conn = get_db_connection()
    except Exception:
        return False

    identity = record.identity
    api_key_id = record.api_key_id or _api_key_id_from_identity(identity)
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_audit_logs(
                    request_id, tenant_id, actor_id, actor_name, actor_source,
                    action, resource_type, resource_id, kb_id, api_key_id,
                    outcome, risk_level, summary, metadata
                )
                VALUES(
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb
                )
                """,
                (
                    record.request_id,
                    identity.tenant_id if identity and identity.enforce_access else None,
                    identity.user_id if identity and identity.enforce_access else None,
                    _identity_actor_name(identity),
                    identity.source if identity else None,
                    record.action,
                    record.resource_type,
                    record.resource_id,
                    record.kb_id,
                    api_key_id,
                    record.outcome,
                    record.risk_level,
                    _summary(record.summary, 300),
                    json.dumps(_sanitize_audit_metadata(record.metadata or {}), ensure_ascii=False),
                ),
            )
        conn.commit()
        return True
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        return False
    finally:
        conn.close()


def fetch_rag_query_logs(
    *,
    tenant_id: str | None = None,
    kb_id: str | None = None,
    request_id: str | None = None,
    actor_id: str | None = None,
    api_key_id: str | None = None,
    pipeline_domain: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 50,
    max_limit: int = 200,
) -> list[dict[str, Any]]:
    """Return sanitized query logs for the console log center."""
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("tenant_id", tenant_id),
            ("kb_id", kb_id),
            ("request_id", request_id),
            ("actor_id", actor_id),
            ("api_key_id", api_key_id),
            ("pipeline_domain", pipeline_domain),
        ):
            if value:
                where.append(f"{column} = %s")
                params.append(value)
        if start_at:
            where.append("created_at >= %s")
            params.append(start_at)
        if end_at:
            where.append("created_at <= %s")
            params.append(end_at)

        sql = """
            SELECT request_id, pipeline_domain, pipeline_stage, tenant_id, actor_id,
                   kb_id, api_key_id, query_hash, query_summary, answer_summary,
                   cannot_answer, relevance_score, faithfulness_score,
                   prompt_tokens, completion_tokens, total_tokens,
                   latency_ms, status, error_code, created_at
            FROM kb_rag_query_logs
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(int(limit or 50), max(1, int(max_limit or 200)))))

        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        return [_log_row_to_payload(row) for row in rows]
    finally:
        conn.close()


def export_rag_query_logs_csv(
    *,
    tenant_id: str | None = None,
    kb_id: str | None = None,
    request_id: str | None = None,
    actor_id: str | None = None,
    api_key_id: str | None = None,
    pipeline_domain: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 10000,
) -> tuple[str, bytes]:
    rows = fetch_rag_query_logs(
        tenant_id=tenant_id,
        kb_id=kb_id,
        request_id=request_id,
        actor_id=actor_id,
        api_key_id=api_key_id,
        pipeline_domain=pipeline_domain,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        max_limit=100000,
    )
    filename = f"rag-query-logs-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    return filename, _render_export_csv(rows)


def fetch_audit_logs(
    *,
    tenant_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    kb_id: str | None = None,
    outcome: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 50,
    max_limit: int = 200,
) -> list[dict[str, Any]]:
    """Return sanitized audit events for governance log views."""
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("tenant_id", tenant_id),
            ("actor_id", actor_id),
            ("action", action),
            ("resource_type", resource_type),
            ("resource_id", resource_id),
            ("request_id", request_id),
            ("kb_id", kb_id),
            ("outcome", outcome),
        ):
            if value:
                where.append(f"{column} = %s")
                params.append(value)
        if start_at:
            where.append("created_at >= %s")
            params.append(start_at)
        if end_at:
            where.append("created_at <= %s")
            params.append(end_at)

        sql = """
            SELECT id, request_id, tenant_id, actor_id, actor_name, actor_source,
                   action, resource_type, resource_id, kb_id, api_key_id,
                   outcome, risk_level, summary, metadata, created_at
            FROM kb_audit_logs
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
        params.append(max(1, min(int(limit or 50), max(1, int(max_limit or 200)))))

        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        return [_audit_row_to_payload(row) for row in rows]
    finally:
        conn.close()


def fetch_token_usage_summary(limit: int = 10) -> dict[str, Any]:
    """Aggregate token usage from LLM call logs and sanitized query logs.

    `kb_llm_call_logs` is the target source for precise per-stage model cost
    accounting. Query-log aggregates remain as a compatibility fallback for
    online RAG totals until every LLM/embedding/parser call writes call logs.
    """
    return fetch_token_usage_summary_for_identity(limit=limit)


def fetch_token_usage_summary_for_identity(
    *,
    limit: int = 10,
    tenant_id: str | None = None,
    include_all_tenants: bool = False,
    pipeline_domain: str | None = None,
) -> dict[str, Any]:
    conn = get_db_connection()
    try:
        ensure_db_schema(conn)
        with conn.cursor() as cur:
            llm_where: list[str] = []
            llm_params: list[Any] = []
            rag_where: list[str] = []
            rag_params: list[Any] = []
            if tenant_id and not include_all_tenants:
                llm_where.append("tenant_id = %s")
                llm_params.append(tenant_id)
                rag_where.append("tenant_id = %s")
                rag_params.append(tenant_id)
            if pipeline_domain:
                llm_where.append("pipeline_domain = %s")
                llm_params.append(pipeline_domain)
                rag_where.append("pipeline_domain = %s")
                rag_params.append(pipeline_domain)

            llm_where_sql = f" WHERE {' AND '.join(llm_where)}" if llm_where else ""
            rag_where_sql = f" WHERE {' AND '.join(rag_where)}" if rag_where else ""

            capped_limit = max(1, min(int(limit or 10), 50))

            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS request_count,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(AVG(NULLIF(latency_ms, 0)), 0) AS avg_latency_ms
                FROM kb_llm_call_logs
                {llm_where_sql}
                """,
                tuple(llm_params),
            )
            overall = _aggregate_row(cur.fetchone())

            cur.execute(
                f"""
                SELECT pipeline_domain,
                       COUNT(*) AS request_count,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(AVG(NULLIF(latency_ms, 0)), 0) AS avg_latency_ms
                FROM kb_llm_call_logs
                {llm_where_sql}
                GROUP BY pipeline_domain
                ORDER BY total_tokens DESC, request_count DESC, pipeline_domain ASC
                """,
                tuple(llm_params),
            )
            by_pipeline = [_aggregate_row(row, "pipelineDomain") for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT kb_id,
                       COUNT(*) AS request_count,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(AVG(NULLIF(latency_ms, 0)), 0) AS avg_latency_ms
                FROM kb_llm_call_logs
                {llm_where_sql}
                GROUP BY kb_id
                ORDER BY total_tokens DESC, request_count DESC, kb_id ASC
                LIMIT %s
                """,
                tuple(llm_params + [capped_limit]),
            )
            by_kb = [_aggregate_row(row, "kbId") for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT COALESCE(api_key_id, 'console') AS api_key_bucket,
                       COUNT(*) AS request_count,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(AVG(NULLIF(latency_ms, 0)), 0) AS avg_latency_ms
                FROM kb_llm_call_logs
                {llm_where_sql}
                GROUP BY api_key_bucket
                ORDER BY total_tokens DESC, request_count DESC, api_key_bucket ASC
                LIMIT %s
                """,
                tuple(llm_params + [capped_limit]),
            )
            by_api_key = [_aggregate_row(row, "apiKeyId") for row in cur.fetchall()]

            cur.execute(
                f"""
                SELECT
                    pipeline_stage,
                    feature_name,
                    COUNT(*) AS request_count,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(AVG(NULLIF(latency_ms, 0)), 0) AS avg_latency_ms,
                    MAX(created_at) AS last_called_at
                FROM kb_llm_call_logs
                {llm_where_sql}
                GROUP BY pipeline_stage, feature_name
                ORDER BY total_tokens DESC, request_count DESC, pipeline_stage ASC, feature_name ASC
                """,
                tuple(llm_params),
            )
            logged_stage_rows = cur.fetchall()

            cur.execute(
                f"""
                SELECT
                    id,
                    request_id,
                    pipeline_domain,
                    pipeline_stage,
                    feature_name,
                    provider,
                    model_name,
                    COALESCE(model_version, '') AS model_version,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    latency_ms,
                    status,
                    error_code,
                    kb_id,
                    COALESCE(api_key_id, 'console') AS api_key_bucket,
                    created_at
                FROM kb_llm_call_logs
                {llm_where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tuple(llm_params + [capped_limit]),
            )
            llm_calls = [_llm_call_row_to_payload(row) for row in cur.fetchall()]

            hourly_where: list[str] = ["hour_bucket >= NOW() - INTERVAL '24 hours'"]
            hourly_params: list[Any] = []
            if tenant_id and not include_all_tenants:
                hourly_where.append("tenant_id = %s")
                hourly_params.append(tenant_id)
            if pipeline_domain:
                hourly_where.append("pipeline_domain = %s")
                hourly_params.append(pipeline_domain)
            hourly_where_sql = f" WHERE {' AND '.join(hourly_where)}"
            hourly_limit = max(capped_limit, 24)
            cur.execute(
                f"""
                SELECT
                    hour_bucket,
                    pipeline_domain,
                    pipeline_stage,
                    feature_name,
                    COALESCE(SUM(request_count), 0) AS request_count,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(latency_ms_sum), 0) AS latency_ms_sum,
                    COALESCE(SUM(error_count), 0) AS error_count,
                    COALESCE(SUM(estimated_cost), 0) AS estimated_cost
                FROM kb_token_usage_hourly
                {hourly_where_sql}
                GROUP BY hour_bucket, pipeline_domain, pipeline_stage, feature_name
                ORDER BY hour_bucket DESC, total_tokens DESC, pipeline_domain ASC, pipeline_stage ASC
                LIMIT %s
                """,
                tuple(hourly_params + [hourly_limit]),
            )
            hourly_usage = [_hourly_usage_row_to_payload(row) for row in cur.fetchall()]

        pipeline_stages = _build_pipeline_stage_usage(logged_stage_rows, llm_calls, by_pipeline, pipeline_domain)
        cost_summary = _build_cost_summary(hourly_usage, overall)
        quota = _token_quota_payload(overall)
        return {
            "source": "kb_llm_call_logs",
            "fallbackSource": "kb_rag_query_logs",
            "scope": "all_tenants" if include_all_tenants else ("tenant" if tenant_id else "unscoped"),
            "detailAvailable": bool(llm_calls),
            "overall": overall,
            "byPipeline": by_pipeline,
            "byKnowledgeBase": by_kb,
            "byApiKey": by_api_key,
            "pipelineStages": pipeline_stages,
            "llmCalls": llm_calls,
            "hourlyUsage": hourly_usage,
            "chartReady": bool(hourly_usage),
            "costSummary": cost_summary,
            "quota": quota,
            "quotaAlerts": _quota_alerts(quota),
        }
    finally:
        conn.close()


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _api_key_id_from_identity(identity: IdentityContext | None) -> str | None:
    if not identity or identity.source != "api_key":
        return None
    value = identity.username or identity.user_id or ""
    if value.startswith("api_key:"):
        return value.split(":", 1)[1]
    return value or None


def _identity_actor_name(identity: IdentityContext | None) -> str | None:
    if not identity or not identity.enforce_access:
        return None
    return identity.display_name or identity.username or identity.user_id


def _decimal_env(name: str, default: str = "0") -> Decimal:
    value = os.getenv(name, default)
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _token_cost_rates() -> tuple[Decimal, Decimal, Decimal]:
    return (
        _decimal_env("KB_TOKEN_COST_PER_1K_PROMPT"),
        _decimal_env("KB_TOKEN_COST_PER_1K_COMPLETION"),
        _decimal_env("KB_TOKEN_COST_PER_1K_TOTAL"),
    )


def _estimate_token_cost(*, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> Decimal:
    prompt_rate, completion_rate, total_rate = _token_cost_rates()
    cost = (
        Decimal(max(0, prompt_tokens)) / Decimal(1000) * prompt_rate
        + Decimal(max(0, completion_tokens)) / Decimal(1000) * completion_rate
        + Decimal(max(0, total_tokens)) / Decimal(1000) * total_rate
    )
    return cost.quantize(Decimal("0.000001"))


def _decimal_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


SENSITIVE_AUDIT_METADATA_KEYS = {
    "plainkey",
    "plain_key",
    "key",
    "keyhash",
    "key_hash",
    "apikey",
    "api_key",
    "secret",
    "password",
    "token",
    "credential",
    "authorization",
    "query",
    "answer",
    "prompt",
    "content",
}


def _sanitize_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized = str(key).replace("-", "_").lower()
        if normalized in SENSITIVE_AUDIT_METADATA_KEYS or any(token in normalized for token in ("secret", "password", "token")):
            safe[str(key)] = "***"
            continue
        if isinstance(value, dict):
            safe[str(key)] = _sanitize_audit_metadata(value)
        elif isinstance(value, list):
            safe[str(key)] = [
                _sanitize_audit_metadata(item) if isinstance(item, dict) else _safe_audit_scalar(item)
                for item in value[:50]
            ]
        else:
            safe[str(key)] = _safe_audit_scalar(value)
    return safe


def _safe_audit_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _summary(str(value), 200)


def _summary(value: str, max_len: int = 160) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _log_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "requestId": row[0],
        "pipelineDomain": row[1],
        "pipelineStage": row[2],
        "tenantId": row[3],
        "actorId": row[4],
        "kbId": row[5],
        "apiKeyId": row[6],
        "queryHash": row[7],
        "querySummary": row[8] or "",
        "answerSummary": row[9] or "",
        "cannotAnswer": bool(row[10]),
        "relevanceScore": row[11],
        "faithfulnessScore": row[12],
        "promptTokens": int(row[13] or 0),
        "completionTokens": int(row[14] or 0),
        "totalTokens": int(row[15] or 0),
        "latencyMs": int(row[16] or 0),
        "status": row[17],
        "errorCode": row[18],
        "createdAt": _iso(row[19]),
    }


def _audit_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    metadata = row[14] or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return {
        "id": int(row[0]),
        "requestId": row[1],
        "tenantId": row[2],
        "actorId": row[3],
        "actorName": row[4],
        "actorSource": row[5],
        "action": row[6],
        "resourceType": row[7],
        "resourceId": row[8],
        "kbId": row[9],
        "apiKeyId": row[10],
        "outcome": row[11],
        "riskLevel": row[12],
        "summary": row[13] or "",
        "metadata": metadata if isinstance(metadata, dict) else {},
        "createdAt": _iso(row[15]),
    }


def _aggregate_row(row: tuple[Any, ...], label_key: str | None = None) -> dict[str, Any]:
    offset = 1 if label_key else 0
    payload = {
        "requestCount": int(row[0 + offset] or 0),
        "promptTokens": int(row[1 + offset] or 0),
        "completionTokens": int(row[2 + offset] or 0),
        "totalTokens": int(row[3 + offset] or 0),
        "avgLatencyMs": round(float(row[4 + offset] or 0), 2),
    }
    if label_key:
        payload = {label_key: row[0], **payload}
    return payload


def _hourly_usage_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    request_count = int(row[4] or 0)
    latency_sum = int(row[8] or 0)
    return {
        "hourBucket": _iso(row[0]),
        "pipelineDomain": row[1],
        "pipelineStage": row[2],
        "featureName": row[3],
        "requestCount": request_count,
        "promptTokens": int(row[5] or 0),
        "completionTokens": int(row[6] or 0),
        "totalTokens": int(row[7] or 0),
        "latencyMsSum": latency_sum,
        "avgLatencyMs": round(latency_sum / request_count, 2) if request_count > 0 else 0,
        "errorCount": int(row[9] or 0),
        "estimatedCost": round(_decimal_float(row[10]), 6),
    }


def _build_cost_summary(hourly_usage: list[dict[str, Any]], overall: dict[str, Any]) -> dict[str, Any]:
    prompt_tokens = int(overall.get("promptTokens", 0) or 0)
    completion_tokens = int(overall.get("completionTokens", 0) or 0)
    total_tokens = int(overall.get("totalTokens", 0) or 0)
    estimated_total = _estimate_token_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    recent_24h = sum(float(item.get("estimatedCost", 0) or 0) for item in hourly_usage)
    prompt_rate, completion_rate, total_rate = _token_cost_rates()
    return {
        "currency": os.getenv("KB_TOKEN_COST_CURRENCY", "CNY"),
        "estimatedCost": round(float(estimated_total), 6),
        "recent24hEstimatedCost": round(recent_24h, 6),
        "ratesPer1k": {
            "prompt": float(prompt_rate),
            "completion": float(completion_rate),
            "total": float(total_rate),
        },
        "configured": any(rate > 0 for rate in (prompt_rate, completion_rate, total_rate)),
    }


def _token_quota_payload(overall: dict[str, Any]) -> dict[str, Any]:
    daily_limit = int(_decimal_env("KB_TOKEN_DAILY_QUOTA", "0") or 0)
    monthly_limit = int(_decimal_env("KB_TOKEN_MONTHLY_QUOTA", "0") or 0)
    used = int(overall.get("totalTokens", 0) or 0)
    return {
        "enforced": False,
        "dailyTokenLimit": daily_limit,
        "monthlyTokenLimit": monthly_limit,
        "currentScopeTokenUsage": used,
        "dailyUsageRatio": round(used / daily_limit, 4) if daily_limit > 0 else 0,
        "monthlyUsageRatio": round(used / monthly_limit, 4) if monthly_limit > 0 else 0,
        "alertThreshold": float(_decimal_env("KB_TOKEN_QUOTA_ALERT_RATIO", "0.8")),
    }


def _quota_alerts(quota: dict[str, Any]) -> list[dict[str, Any]]:
    threshold = float(quota.get("alertThreshold", 0.8) or 0.8)
    alerts: list[dict[str, Any]] = []
    for key, label in (("daily", "Daily token quota"), ("monthly", "Monthly token quota")):
        limit = int(quota.get(f"{key}TokenLimit", 0) or 0)
        ratio = float(quota.get(f"{key}UsageRatio", 0) or 0)
        if limit > 0 and ratio >= threshold:
            alerts.append(
                {
                    "id": f"token-quota-{key}",
                    "severity": "warning" if ratio < 1 else "critical",
                    "title": label,
                    "message": f"{ratio:.0%} of configured quota used",
                    "usageRatio": round(ratio, 4),
                    "limit": limit,
                }
            )
    return alerts


PIPELINE_STAGE_LABELS = {
    "parse": "解析",
    "clean": "清洗",
    "chunk": "切片",
    "quality": "质量审核",
    "embedding": "向量化",
    "retrieval": "召回",
    "rerank": "重排",
    "generation": "问答生成",
    "evaluation": "评测",
    "query": "在线问答",
}

EXPECTED_LLM_STAGES = [
    ("parse", "文档解析托管模型"),
    ("clean", "清洗 LLM"),
    ("chunk", "切片 LLM"),
    ("quality", "质量审核 LLM"),
    ("embedding", "Embedding 模型"),
    ("rerank", "重排模型"),
    ("generation", "问答生成 LLM"),
    ("evaluation", "评测打分 LLM"),
]

EXPECTED_LLM_STAGES_BY_DOMAIN = {
    "ingestion": EXPECTED_LLM_STAGES[:5],
    "online_rag": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],
    "graph_rag": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],
    "openapi": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],
    "evaluation": [EXPECTED_LLM_STAGES[7]],
}


def _empty_bucket() -> dict[str, Any]:
    return {
        "requestCount": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0,
        "avgLatencyMs": 0,
    }


def _build_pipeline_stage_usage(
    stage_rows: list[tuple[Any, ...]],
    llm_calls: list[dict[str, Any]],
    by_pipeline: list[dict[str, Any]],
    pipeline_domain: str | None = None,
) -> list[dict[str, Any]]:
    calls_by_stage_feature: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for call in llm_calls:
        stage = str(call.get("pipelineStage") or "unknown")
        feature_name = str(call.get("featureName") or PIPELINE_STAGE_LABELS.get(stage, stage))
        calls_by_stage_feature.setdefault((stage, feature_name), []).append(call)

    stages: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in stage_rows:
        stage = str(row[0] or "unknown")
        feature_name = str(row[1] or PIPELINE_STAGE_LABELS.get(stage, stage))
        seen.add((stage, feature_name))
        stages.append(
            {
                "pipelineStage": stage,
                "stageLabel": PIPELINE_STAGE_LABELS.get(stage, stage),
                "featureName": feature_name,
                "detailStatus": "recorded",
                "lastCalledAt": _iso(row[7]),
                **_aggregate_row(row[2:7]),
                "calls": calls_by_stage_feature.get((stage, feature_name), []),
            }
        )

    expected_stages = EXPECTED_LLM_STAGES_BY_DOMAIN.get(pipeline_domain or "", EXPECTED_LLM_STAGES)
    for stage, feature_name in expected_stages:
        if (stage, feature_name) in seen:
            continue
        stages.append(
            {
                "pipelineStage": stage,
                "stageLabel": PIPELINE_STAGE_LABELS.get(stage, stage),
                "featureName": feature_name,
                "detailStatus": "not_recorded",
                "lastCalledAt": "",
                **_empty_bucket(),
                "calls": calls_by_stage_feature.get((stage, feature_name), []),
            }
        )

    if by_pipeline and not any(stage["pipelineStage"] == "query" and stage["detailStatus"] == "recorded" for stage in stages):
        query_bucket = next((item for item in by_pipeline if item.get("pipelineDomain") in {"online_rag", "graph_rag"}), None)
        if query_bucket:
            stages.append(
                {
                    "pipelineStage": "query",
                    "stageLabel": "在线问答",
                    "featureName": "RAG 查询日志聚合",
                    "detailStatus": "query_log_fallback",
                    "lastCalledAt": "",
                    **{key: query_bucket[key] for key in _empty_bucket().keys()},
                    "calls": [],
                }
            )
    return stages


def _llm_call_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "requestId": row[1],
        "pipelineDomain": row[2],
        "pipelineStage": row[3],
        "featureName": row[4],
        "provider": row[5],
        "modelName": row[6],
        "modelVersion": row[7],
        "promptTokens": int(row[8] or 0),
        "completionTokens": int(row[9] or 0),
        "totalTokens": int(row[10] or 0),
        "latencyMs": int(row[11] or 0),
        "status": row[12],
        "errorCode": row[13],
        "kbId": row[14],
        "apiKeyId": row[15],
        "createdAt": _iso(row[16]),
    }


EXPORT_FIELDNAMES = [
    "createdAt",
    "requestId",
    "pipelineDomain",
    "pipelineStage",
    "tenantId",
    "actorId",
    "kbId",
    "apiKeyId",
    "queryHash",
    "querySummary",
    "answerSummary",
    "cannotAnswer",
    "relevanceScore",
    "faithfulnessScore",
    "promptTokens",
    "completionTokens",
    "totalTokens",
    "latencyMs",
    "status",
    "errorCode",
]


def _render_export_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in EXPORT_FIELDNAMES})
    return buffer.getvalue().encode("utf-8-sig")
