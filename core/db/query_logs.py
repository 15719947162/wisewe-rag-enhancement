"""
查询日志记录模块
================

这个模块负责记录和管理 RAG 系统中的各类日志，包括：

1. RAG 查询日志 - 记录用户的问答请求，包括问题、答案、相关性评分等
2. LLM 调用日志 - 记录所有大模型 API 调用，包括 token 消耗、延迟、模型信息等
3. 审计日志 - 记录系统关键操作，用于安全审计和合规追溯

核心设计原则：
- 日志记录失败不能影响正常业务流程（用户查询不能因为日志写入失败而中断）
- 敏感信息脱敏：查询内容存储摘要和哈希值，不存储原始完整内容
- 支持 token 消耗统计和成本估算
- 支持按小时聚合的使用量统计，便于监控和计费

典型使用场景：
- 用户发起问答请求 → 记录 RAG 查询日志
- 调用 LLM 生成答案 → 记录 LLM 调用日志
- 创建知识库、删除文档 → 记录审计日志
- 运营人员查看使用情况 → 查询 token 使用统计

数据流向：
用户请求 → 业务逻辑 → 调用日志记录函数 → PostgreSQL 数据库
                           ↓
                    更新小时级聚合统计（用于快速查询）

安全考虑：
- 查询内容不完整存储，只存摘要和哈希
- API Key 等敏感字段自动脱敏
- 支持租户隔离（tenant_id）和用户追溯（actor_id）
"""

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
    """
    RAG 查询日志记录

    记录一次完整的 RAG 问答请求，包括用户问题、系统答案、评分指标等。
    这个记录会被写入 kb_rag_query_logs 表。

    属性说明：
        request_id: 请求唯一标识，用于追踪单次请求的全链路
        pipeline_domain: 管道域，如 'online_rag'（在线问答）、'graph_rag'（图谱问答）
        kb_id: 知识库 ID，标识查询的是哪个知识库
        query: 用户的原始问题（会被脱敏存储，只保存摘要和哈希）
        answer: 系统生成的答案（同样会被脱敏）
        identity: 用户身份信息，包括租户 ID、用户 ID 等
        cannot_answer: 是否无法回答（系统判断无法从知识库找到答案）
        relevance_score: 相关性评分（0-1），衡量召回内容与问题的相关程度
        faithfulness_score: 忠实度评分（0-1），衡量答案是否忠实于召回内容
        prompt_tokens: 提示词 token 数量
        completion_tokens: 生成答案的 token 数量
        total_tokens: 总 token 数量
        latency_ms: 请求耗时（毫秒）
        status: 请求状态，'success' 或 'error'
        error_code: 错误码，失败时记录具体错误类型
        api_key_id: API Key ID，用于追溯是通过哪个 API Key 发起的请求
    """
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
    """
    LLM 调用日志记录

    记录一次大模型 API 调用，用于成本核算和性能监控。
    这个记录会被写入 kb_llm_call_logs 表。

    与 RagQueryLogRecord 的区别：
        - RagQueryLogRecord 关注用户问答的整体结果
        - LlmCallLogRecord 关注每一次具体的模型调用细节
        - 一次 RAG 查询可能触发多次 LLM 调用（如重排序、答案生成等）

    属性说明：
        pipeline_domain: 管道域，如 'ingestion'（导入）、'online_rag'（在线问答）
        pipeline_stage: 管道阶段，如 'chunk'（切片）、'generation'（生成）
        feature_name: 功能名称，如 "切片 LLM"、"问答生成 LLM"
        provider: 模型提供商，如 'openai'、'dashscope'
        model_name: 模型名称，如 'gpt-4'、'qwen-max'
        kb_id: 知识库 ID（可选）
        request_id: 关联的请求 ID（可选，用于关联到具体的 RAG 查询）
        identity: 用户身份信息
        api_key_id: API Key ID
        model_version: 模型版本号
        prompt_tokens: 提示词 token 数
        completion_tokens: 生成 token 数
        total_tokens: 总 token 数
        latency_ms: 调用耗时（毫秒）
        status: 调用状态，'success' 或 'error'
        error_code: 错误码
    """
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
    """
    审计日志记录

    记录系统中的关键操作，用于安全审计和合规追溯。
    这个记录会被写入 kb_audit_logs 表。

    典型审计场景：
        - 创建/删除知识库
        - 上传/删除文档
        - 创建/撤销 API Key
        - 修改系统配置
        - 导出数据

    属性说明：
        action: 操作类型，如 'create_kb'、'delete_document'、'export_data'
        resource_type: 资源类型，如 'knowledge_base'、'document'、'api_key'
        resource_id: 资源 ID（可选）
        identity: 操作者身份
        request_id: 请求 ID（可选）
        kb_id: 知识库 ID（可选）
        api_key_id: API Key ID（可选）
        outcome: 操作结果，'success' 或 'failure'
        risk_level: 风险等级，'low'、'medium'、'high'
        summary: 操作摘要，人类可读的描述
        metadata: 额外的元数据（敏感字段会被自动脱敏）
    """
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
    """
    添加 RAG 查询日志到数据库

    这个函数负责将一次用户问答请求的完整信息记录到数据库。
    记录包括：查询摘要、答案摘要、token 消耗、延迟、评分指标等。

    重要特性：
        - 日志记录失败不会影响用户查询（失败时静默返回 False）
        - 查询内容会被脱敏：存储摘要和哈希值，不存储完整原始内容
        - 支持幂等性：相同 request_id 的记录只会写入一次（ON CONFLICT DO NOTHING）

    数据脱敏说明：
        - query 字段：存储 query_hash（SHA256 哈希）和 query_summary（前 160 字符）
        - answer 字段：只存储 answer_summary（前 160 字符）
        - 这样既保留了查询日志用于分析，又避免了敏感信息泄露

    参数：
        record: RagQueryLogRecord 对象，包含查询的所有信息

    返回：
        bool: True 表示成功写入，False 表示写入失败

    使用示例：
        >>> record = RagQueryLogRecord(
        ...     request_id="req_123",
        ...     pipeline_domain="online_rag",
        ...     kb_id="kb_456",
        ...     query="什么是机器学习？",
        ...     answer="机器学习是人工智能的一个分支...",
        ...     prompt_tokens=150,
        ...     completion_tokens=200,
        ...     latency_ms=1200
        ... )
        >>> success = append_rag_query_log(record)
    """
    try:
        conn = get_db_connection()
    except Exception:
        # 连接数据库失败，静默返回 False，不影响用户查询
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
    """
    添加 LLM 调用日志到数据库

    这个函数记录每一次大模型 API 调用的详细信息，用于：
        1. 成本核算：统计 token 消耗，估算 API 调用成本
        2. 性能监控：记录调用延迟，发现性能瓶颈
        3. 故障排查：记录错误码，快速定位问题

    与 append_rag_query_log 的区别：
        - append_rag_query_log 记录用户问答的整体结果
        - append_llm_call_log 记录具体的模型调用细节
        - 一次 RAG 查询可能触发多次 LLM 调用，每次都会单独记录

    自动同步更新：
        每次调用此函数时，会自动更新小时级的 token 使用统计表
        (kb_token_usage_hourly)，用于快速查询和成本计算。

    参数：
        record: LlmCallLogRecord 对象，包含模型调用的所有信息

    返回：
        bool: True 表示成功写入，False 表示写入失败

    使用示例：
        >>> record = LlmCallLogRecord(
        ...     pipeline_domain="online_rag",
        ...     pipeline_stage="generation",
        ...     feature_name="问答生成 LLM",
        ...     provider="openai",
        ...     model_name="gpt-4",
        ...     prompt_tokens=500,
        ...     completion_tokens=300,
        ...     latency_ms=800
        ... )
        >>> success = append_llm_call_log(record)
    """
    try:
        conn = get_db_connection()
    except Exception:
        # 连接数据库失败，静默返回 False，不影响业务流程
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
    """
    更新小时级 token 使用统计（内部函数）

    这个函数在记录 LLM 调用日志时自动调用，更新 kb_token_usage_hourly 表。
    该表按小时聚合 token 使用量，用于快速查询和成本估算。

    为什么需要小时级聚合？
        - LLM 调用日志表数据量很大，直接聚合查询慢
        - 小时级聚合表数据量小，查询速度快
        - 适合用于仪表盘、成本报表等场景

    聚合维度：
        - 时间：按小时聚合
        - 租户：tenant_id
        - 知识库：kb_id
        - API Key：api_key_id
        - 管道域：pipeline_domain
        - 管道阶段：pipeline_stage
        - 功能：feature_name

    使用 UPSERT 逻辑：
        如果对应小时的统计记录已存在，则累加数据
        如果不存在，则插入新记录

    参数：
        cur: 数据库游标
        record: LLM 调用日志记录
        identity: 用户身份信息
        api_key_id: API Key ID
    """
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
    """
    刷新小时级 token 使用统计

    这个函数用于重新计算小时级的 token 使用统计。
    当发现统计数据不准确或需要历史数据修正时，可以调用此函数。

    工作原理：
        1. 删除指定时间范围内的小时级统计记录
        2. 从 LLM 调用日志表重新聚合计算
        3. 重新插入统计记录

    适用场景：
        - 数据库迁移后的统计修正
        - 发现统计数据错误时的重新计算
        - 批量导入历史日志后的统计更新

    参数：
        start_at: 开始时间（可选），不传则重新计算所有
        end_at: 结束时间（可选），不传则重新计算到最新

    返回：
        dict: 包含刷新结果，格式如下：
            {
                "refreshed": True,
                "deleted": 100,   # 删除的旧记录数
                "inserted": 120   # 新插入的记录数
            }

    使用示例：
        >>> from datetime import datetime, timedelta
        >>> # 重新计算过去 7 天的统计
        >>> start = datetime.now() - timedelta(days=7)
        >>> result = refresh_token_usage_hourly(start_at=start)
        >>> print(f"删除了 {result['deleted']} 条，插入了 {result['inserted']} 条")
    """
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
    """
    检查是否存在指定的 LLM 调用日志

    这个函数用于判断某个请求是否已经记录过 LLM 调用日志。
    主要用于去重和判断日志状态。

    使用场景：
        - 判断某个请求是否已经处理过
        - 检查特定阶段是否生成了日志
        - 避免重复记录同一次调用

    参数：
        request_id: 请求 ID
        pipeline_stage: 管道阶段（可选），如果指定则只检查该阶段的日志

    返回：
        bool: True 表示存在日志，False 表示不存在

    使用示例：
        >>> # 检查请求是否已记录
        >>> if not has_llm_call_log("req_123"):
        ...     append_llm_call_log(record)
        >>> # 只检查生成阶段的日志
        >>> if has_llm_call_log("req_123", pipeline_stage="generation"):
        ...     print("生成阶段已完成")
    """
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
    """
    修复 LLM 调用日志的身份信息

    这个函数用于补全历史日志中缺失的身份信息（租户 ID、用户 ID、知识库 ID）。
    某些历史日志可能在记录时没有完整的身份信息，需要后期补充。

    为什么需要这个功能？
        - 系统升级后，日志格式可能发生变化
        - 某些异步任务可能在身份信息缺失时记录日志
        - 导入的历史数据可能不完整

    工作原理：
        1. 查找指定 request_id 且身份信息为空的日志记录
        2. 用传入的 identity 和 kb_id 补全缺失字段
        3. 使用 COALESCE 保证已有字段不被覆盖

    参数：
        request_id: 请求 ID
        identity: 用户身份信息（包含租户 ID 和用户 ID）
        kb_id: 知识库 ID
        pipeline_stage: 管道阶段（可选），用于更精确的匹配

    返回：
        dict: 包含修复结果，格式如下：
            {
                "requestId": "req_123",
                "pipelineStage": "generation",
                "repaired": True,    # 是否成功修复
                "updated": 1,        # 更新的记录数
                "reason": "updated"  # 原因说明
            }

    使用示例：
        >>> identity = IdentityContext(tenant_id="tenant_1", user_id="user_123")
        >>> result = repair_llm_call_log_identity(
        ...     "req_123",
        ...     identity=identity,
        ...     kb_id="kb_456"
        ... )
        >>> if result["repaired"]:
        ...     print(f"成功修复 {result['updated']} 条记录")
    """
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
    """
    添加审计日志到数据库

    这个函数记录系统中的关键操作，用于安全审计和合规追溯。
    审计日志是安全合规的重要组成部分，记录谁在什么时候对什么资源做了什么操作。

    审计日志的关键要素：
        1. 谁（Who）：操作者身份（tenant_id, actor_id, actor_name）
        2. 什么时候（When）：操作时间（created_at）
        3. 对什么（What）：资源类型和资源 ID（resource_type, resource_id）
        4. 做了什么（Action）：操作类型（action）
        5. 结果如何（Outcome）：操作结果（outcome）
        6. 风险等级（Risk）：操作的风险级别（risk_level）

    敏感信息处理：
        - metadata 字段中的敏感信息会被自动脱敏
        - 敏感字段包括：password, secret, token, api_key, credential 等
        - 这些字段会被替换为 "***"

    参数：
        record: AuditLogRecord 对象，包含审计事件的所有信息

    返回：
        bool: True 表示成功写入，False 表示写入失败

    典型使用场景：
        >>> # 记录知识库创建操作
        >>> record = AuditLogRecord(
        ...     action="create_kb",
        ...     resource_type="knowledge_base",
        ...     resource_id="kb_123",
        ...     identity=user_identity,
        ...     summary="用户创建了知识库：技术文档库",
        ...     risk_level="low"
        ... )
        >>> append_audit_log(record)

        >>> # 记录数据导出操作（高风险）
        >>> record = AuditLogRecord(
        ...     action="export_data",
        ...     resource_type="document",
        ...     kb_id="kb_123",
        ...     summary="导出了知识库的所有文档",
        ...     risk_level="high",
        ...     metadata={"export_format": "csv", "document_count": 150}
        ... )
        >>> append_audit_log(record)
    """
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
    """
    查询 RAG 查询日志

    这个函数用于从数据库查询用户的问答日志，支持多维度筛选。
    主要用于控制台的日志中心、用户查询历史等功能。

    支持的筛选条件：
        - tenant_id: 按租户筛选
        - kb_id: 按知识库筛选
        - request_id: 按请求 ID 精确查询
        - actor_id: 按用户 ID 筛选
        - api_key_id: 按 API Key 筛选
        - pipeline_domain: 按管道域筛选（online_rag, graph_rag 等）
        - start_at / end_at: 按时间范围筛选

    分页和安全：
        - limit 控制返回数量，默认 50 条
        - max_limit 限制最大返回数量，默认 200 条
        - 防止一次查询返回过多数据

    参数：
        tenant_id: 租户 ID（可选）
        kb_id: 知识库 ID（可选）
        request_id: 请求 ID（可选）
        actor_id: 用户 ID（可选）
        api_key_id: API Key ID（可选）
        pipeline_domain: 管道域（可选）
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）
        limit: 返回数量限制，默认 50
        max_limit: 最大返回数量，默认 200

    返回：
        list[dict]: 日志记录列表，每条记录包含：
            - requestId: 请求 ID
            - pipelineDomain: 管道域
            - kbId: 知识库 ID
            - querySummary: 查询摘要
            - answerSummary: 答案摘要
            - promptTokens / completionTokens / totalTokens: token 消耗
            - latencyMs: 延迟毫秒数
            - status: 状态
            - createdAt: 创建时间

    使用示例：
        >>> # 查询某个知识库最近的查询日志
        >>> logs = fetch_rag_query_logs(kb_id="kb_123", limit=10)
        >>> for log in logs:
        ...     print(f"{log['createdAt']}: {log['querySummary']}")

        >>> # 查询某个用户昨天的查询记录
        >>> from datetime import datetime, timedelta
        >>> yesterday = datetime.now() - timedelta(days=1)
        >>> logs = fetch_rag_query_logs(
        ...     actor_id="user_456",
        ...     start_at=yesterday
        ... )
    """
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
    """
    导出 RAG 查询日志为 CSV 文件

    这个函数将查询日志导出为 CSV 格式，方便用户进行离线分析或存档。
    CSV 文件使用 UTF-8 with BOM 编码，确保 Excel 可以正确打开。

    导出流程：
        1. 调用 fetch_rag_query_logs 获取数据
        2. 将数据转换为 CSV 格式
        3. 返回文件名和文件内容

    参数：
        与 fetch_rag_query_logs 相同，参见该函数的参数说明
        limit: 默认 10000，最大可导出 100000 条

    返回：
        tuple[str, bytes]: (文件名, 文件内容)
            文件名格式：rag-query-logs-{时间戳}.csv
            文件内容：UTF-8 编码的 CSV 数据

    使用示例：
        >>> filename, csv_data = export_rag_query_logs_csv(kb_id="kb_123")
        >>> # 保存到文件
        >>> with open(filename, 'wb') as f:
        ...     f.write(csv_data)
        >>> # 或者通过 HTTP 返回给用户
        >>> response = Response(csv_data, mimetype='text/csv')
        >>> response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    """
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
    """
    查询审计日志

    这个函数用于查询系统的审计日志，用于安全审计和合规追溯。
    支持多种筛选条件，可以灵活地查询特定类型的操作记录。

    支持的筛选条件：
        - tenant_id: 按租户筛选
        - actor_id: 按用户 ID 筛选
        - action: 按操作类型筛选（如 'create_kb', 'delete_document'）
        - resource_type: 按资源类型筛选（如 'knowledge_base', 'document'）
        - resource_id: 按资源 ID 精确查询
        - request_id: 按请求 ID 精确查询
        - kb_id: 按知识库筛选
        - outcome: 按操作结果筛选（'success' 或 'failure'）
        - start_at / end_at: 按时间范围筛选

    使用场景：
        - 安全审计：查看某个用户的操作记录
        - 故障排查：查看失败的操作记录
        - 合规审查：查看特定资源类型的所有操作

    参数：
        参见上述筛选条件说明
        limit: 返回数量限制，默认 50
        max_limit: 最大返回数量，默认 200

    返回：
        list[dict]: 审计日志列表，每条记录包含：
            - id: 日志 ID
            - requestId: 请求 ID
            - tenantId / actorId / actorName: 操作者信息
            - action: 操作类型
            - resourceType / resourceId: 资源信息
            - outcome: 操作结果
            - riskLevel: 风险等级
            - summary: 操作摘要
            - metadata: 操作详情（已脱敏）
            - createdAt: 创建时间

    使用示例：
        >>> # 查询某个知识库的所有删除操作
        >>> logs = fetch_audit_logs(
        ...     kb_id="kb_123",
        ...     action="delete_document",
        ...     outcome="success"
        ... )
        >>> for log in logs:
        ...     print(f"{log['createdAt']}: {log['summary']}")

        >>> # 查询失败的高风险操作
        >>> logs = fetch_audit_logs(
        ...     outcome="failure",
        ...     risk_level="high"
        ... )
    """
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
    """
    获取 token 使用统计摘要

    这个函数查询整个系统的 token 使用情况，包括总体消耗、各管道域消耗等。
    主要用于系统级别的监控和成本核算。

    数据来源：
        主要从 kb_llm_call_logs 表聚合
        如果 LLM 调用日志不存在，则从 kb_rag_query_logs 表聚合（兼容历史数据）

    参数：
        limit: 返回分组数据的数量限制，默认 10

    返回：
        dict: 统计摘要，参见 fetch_token_usage_summary_for_identity 的返回格式

    使用示例：
        >>> summary = fetch_token_usage_summary()
        >>> print(f"总 token 消耗: {summary['overall']['totalTokens']}")
        >>> print(f"预估成本: ¥{summary['costSummary']['estimatedCost']}")
    """
    return fetch_token_usage_summary_for_identity(limit=limit)


def fetch_token_usage_summary_for_identity(
    *,
    limit: int = 10,
    tenant_id: str | None = None,
    include_all_tenants: bool = False,
    pipeline_domain: str | None = None,
) -> dict[str, Any]:
    """
    获取指定范围的 token 使用统计

    这是 token 使用统计的核心函数，支持按租户、管道域等维度查询。
    返回详细的统计数据，包括总体消耗、按管道域分组、按知识库分组等。

    统计维度：
        1. overall: 总体统计（请求数、token 数、平均延迟）
        2. byPipeline: 按管道域分组统计（online_rag, ingestion 等）
        3. byKnowledgeBase: 按知识库分组统计（Top N）
        4. byApiKey: 按 API Key 分组统计（Top N）
        5. pipelineStages: 按管道阶段详细统计
        6. llmCalls: 最近的 LLM 调用记录列表
        7. hourlyUsage: 小时级使用量趋势（用于绘制图表）
        8. costSummary: 成本估算摘要
        9. quota: 配额使用情况

    参数：
        limit: 分组数据的数量限制，默认 10
        tenant_id: 租户 ID，按租户筛选数据
        include_all_tenants: 是否包含所有租户（用于管理员查询）
        pipeline_domain: 管道域，按管道域筛选数据

    返回：
        dict: 详细统计信息，格式如下：
            {
                "source": "kb_llm_call_logs",
                "scope": "tenant",  # 或 "all_tenants", "unscoped"
                "overall": {
                    "requestCount": 1000,
                    "promptTokens": 50000,
                    "completionTokens": 30000,
                    "totalTokens": 80000,
                    "avgLatencyMs": 120.5
                },
                "byPipeline": [...],
                "byKnowledgeBase": [...],
                "byApiKey": [...],
                "pipelineStages": [...],
                "llmCalls": [...],
                "hourlyUsage": [...],
                "costSummary": {
                    "currency": "CNY",
                    "estimatedCost": 12.5,
                    "ratesPer1k": {"prompt": 0.01, "completion": 0.02, "total": 0.015}
                },
                "quota": {
                    "dailyTokenLimit": 100000,
                    "currentScopeTokenUsage": 80000,
                    "dailyUsageRatio": 0.8,
                    "alertThreshold": 0.8
                },
                "quotaAlerts": [
                    {
                        "id": "token-quota-daily",
                        "severity": "warning",
                        "title": "Daily token quota",
                        "message": "80% of configured quota used",
                        "usageRatio": 0.8
                    }
                ]
            }

    使用示例：
        >>> # 查询某个租户的 token 使用情况
        >>> stats = fetch_token_usage_summary_for_identity(
        ...     tenant_id="tenant_123",
        ...     limit=20
        ... )
        >>> print(f"本月消耗: {stats['overall']['totalTokens']} tokens")
        >>> print(f"预估成本: ¥{stats['costSummary']['estimatedCost']}")

        >>> # 查询在线问答的 token 使用情况
        >>> stats = fetch_token_usage_summary_for_identity(
        ...     pipeline_domain="online_rag"
        ... )
        >>> for stage in stats['pipelineStages']:
        ...     print(f"{stage['stageLabel']}: {stage['totalTokens']} tokens")
    """
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


# ==================== 内部辅助函数 ====================

def _sha256(value: str) -> str:
    """
    计算字符串的 SHA256 哈希值

    用于对查询内容进行脱敏处理，存储哈希值而不是原始内容。
    这样既保护了用户隐私，又可以通过哈希值进行关联分析。
    """
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _api_key_id_from_identity(identity: IdentityContext | None) -> str | None:
    """
    从用户身份信息中提取 API Key ID

    如果用户是通过 API Key 进行身份验证的，则提取 API Key ID。
    API Key ID 格式通常为 "api_key:xxx"，这个函数会去掉前缀。
    """
    if not identity or identity.source != "api_key":
        return None
    value = identity.username or identity.user_id or ""
    if value.startswith("api_key:"):
        return value.split(":", 1)[1]
    return value or None


def _identity_actor_name(identity: IdentityContext | None) -> str | None:
    """
    获取操作者的显示名称

    优先使用显示名称（display_name），其次是用户名（username），最后是用户 ID。
    用于审计日志中的人类可读的操作者名称。
    """
    if not identity or not identity.enforce_access:
        return None
    return identity.display_name or identity.username or identity.user_id


def _decimal_env(name: str, default: str = "0") -> Decimal:
    """
    从环境变量中读取 Decimal 值

    用于读取 token 成本费率等配置，这些配置需要高精度计算。
    如果环境变量不存在或格式错误，返回默认值。
    """
    value = os.getenv(name, default)
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _token_cost_rates() -> tuple[Decimal, Decimal, Decimal]:
    """
    获取 token 成本费率

    从环境变量读取每千 token 的成本费率，用于成本估算。
    返回三个费率：(提示词费率, 生成词费率, 总token费率)

    环境变量：
        KB_TOKEN_COST_PER_1K_PROMPT: 提示词每千 token 成本
        KB_TOKEN_COST_PER_1K_COMPLETION: 生成词每千 token 成本
        KB_TOKEN_COST_PER_1K_TOTAL: 总 token 每千 token 成本
    """
    return (
        _decimal_env("KB_TOKEN_COST_PER_1K_PROMPT"),
        _decimal_env("KB_TOKEN_COST_PER_1K_COMPLETION"),
        _decimal_env("KB_TOKEN_COST_PER_1K_TOTAL"),
    )


def _estimate_token_cost(*, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> Decimal:
    """
    估算 token 使用成本

    根据提示词 token 数、生成词 token 数和总 token 数，结合费率计算成本。
    成本保留 6 位小数，以支持精确的成本核算。
    """
    prompt_rate, completion_rate, total_rate = _token_cost_rates()
    cost = (
        Decimal(max(0, prompt_tokens)) / Decimal(1000) * prompt_rate
        + Decimal(max(0, completion_tokens)) / Decimal(1000) * completion_rate
        + Decimal(max(0, total_tokens)) / Decimal(1000) * total_rate
    )
    return cost.quantize(Decimal("0.000001"))


def _decimal_float(value: Any) -> float:
    """
    将值转换为 float，失败时返回 0.0

    用于处理数据库查询结果中的 Decimal 类型，转换为 JSON 可序列化的 float。
    """
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ==================== 敏感信息脱敏 ====================

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
    """
    审计日志元数据脱敏

    对审计日志的 metadata 字段进行敏感信息脱敏处理。
    包含敏感关键字段的值会被替换为 "***"。

    敏感字段包括：
        - password, secret, token, credential, authorization
        - api_key, apikey, key, plainkey
        - query, answer, prompt, content

    参数：
        metadata: 原始元数据字典

    返回：
        dict: 脱敏后的元数据字典
    """
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
    """
    审计日志标量值脱敏

    对审计日志中的标量值进行处理：
        - None, bool, int, float：保持原样
        - 其他类型：截断为最多 200 字符的摘要
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _summary(str(value), 200)


def _summary(value: str, max_len: int = 160) -> str:
    """
    生成文本摘要

    将长文本截断为指定长度的摘要，用于日志记录。
    保留前 max_len-1 个字符，然后添加 "..."。

    这个函数用于：
        - 查询内容摘要（防止存储过长的查询）
        - 答案摘要
        - 审计日志摘要
    """
    text = " ".join((value or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _iso(value: Any) -> str:
    """
    将时间值转换为 ISO 格式字符串

    用于将数据库中的时间值转换为 JSON 可序列化的字符串格式。
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


# ==================== 数据库行转换函数 ====================

def _log_row_to_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    """
    将数据库查询结果行转换为 RAG 查询日志字典

    这个函数将数据库返回的元组（tuple）转换为更易用的字典格式。
    字段顺序与 SQL 查询语句中的 SELECT 顺序对应。

    参数：
        row: 数据库查询结果行（元组）

    返回：
        dict: 包含查询日志所有字段的字典
    """
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
    """
    将数据库查询结果行转换为审计日志字典

    与 _log_row_to_payload 类似，但用于审计日志。
    特别处理 metadata 字段，确保它是字典格式。
    """
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
    """
    将聚合查询结果行转换为统计字典

    用于处理 GROUP BY 查询的结果，包含统计字段（COUNT, SUM, AVG）。
    如果提供了 label_key，会将分组标签也包含在结果中。

    参数：
        row: 数据库查询结果行（元组）
        label_key: 分组标签的键名（可选），如 "pipelineDomain", "kbId"

    返回：
        dict: 包含统计信息的字典
    """
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
    """
    将小时级使用量查询结果行转换为字典

    用于处理 kb_token_usage_hourly 表的查询结果。
    包含请求计数、token 消耗、延迟总和、错误计数、预估成本等。
    """
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
    """
    构建成本摘要

    根据 token 使用量和费率，计算总成本和近期成本。
    用于成本核算和费用监控。

    参数：
        hourly_usage: 小时级使用量列表
        overall: 总体统计数据

    返回：
        dict: 成本摘要，包含：
            - currency: 货币单位（从环境变量 KB_TOKEN_COST_CURRENCY 读取）
            - estimatedCost: 总预估成本
            - recent24hEstimatedCost: 最近 24 小时预估成本
            - ratesPer1k: 每千 token 的费率
            - configured: 是否配置了费率
    """
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
    """
    构建 token 配额信息

    从环境变量读取配额限制，计算当前使用比例。
    用于配额监控和预警。

    环境变量：
        KB_TOKEN_DAILY_QUOTA: 每日 token 配额
        KB_TOKEN_MONTHLY_QUOTA: 每月 token 配额
        KB_TOKEN_QUOTA_ALERT_RATIO: 配额预警阈值（默认 0.8）

    参数：
        overall: 总体统计数据

    返回：
        dict: 配额信息，包含日配额、月配额、使用比例等
    """
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
    """
    生成配额预警

    根据配额使用比例，生成预警信息。
    当使用比例超过预警阈值时，生成 warning 级别的预警。
    当使用比例达到或超过 100% 时，生成 critical 级别的预警。

    参数：
        quota: 配额信息（来自 _token_quota_payload）

    返回：
        list[dict]: 预警列表，每个预警包含：
            - id: 预警 ID
            - severity: 严重程度（"warning" 或 "critical"）
            - title: 预警标题
            - message: 预警消息
            - usageRatio: 使用比例
            - limit: 配额限制
    """
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


# ==================== 管道阶段标签和配置 ====================

# 管道阶段的中文标签，用于在界面上显示更友好的名称
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

# 期望记录的 LLM 调用阶段列表
# 用于在统计报告中显示哪些阶段应该有日志
# 格式：(阶段代码, 功能名称)
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

# 按管道域分组的期望 LLM 阶段
# 不同的管道域（如导入、在线问答）会调用不同的模型阶段
EXPECTED_LLM_STAGES_BY_DOMAIN = {
    "ingestion": EXPECTED_LLM_STAGES[:5],  # 导入流程：解析、清洗、切片、质量、向量化
    "online_rag": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],  # 在线问答：重排、生成
    "graph_rag": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],  # 图谱问答：重排、生成
    "openapi": [EXPECTED_LLM_STAGES[5], EXPECTED_LLM_STAGES[6]],  # OpenAPI：重排、生成
    "evaluation": [EXPECTED_LLM_STAGES[7]],  # 评测：评测打分
}


def _empty_bucket() -> dict[str, Any]:
    """
    创建空的统计数据桶

    用于初始化统计对象，当某个阶段没有数据时使用。
    所有统计字段初始化为 0。
    """
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
    """
    构建管道阶段使用统计

    这个函数整合 LLM 调用日志和期望的管道阶段，生成完整的阶段使用统计。
    对于已有日志的阶段，显示实际统计数据；对于没有日志的阶段，显示空统计。

    为什么需要这个功能？
        - 让用户了解每个管道阶段的模型使用情况
        - 发现某些阶段是否正常记录日志
        - 支持成本按阶段分解

    参数：
        stage_rows: 已记录的阶段统计行（从数据库查询）
        llm_calls: 最近的 LLM 调用记录列表
        by_pipeline: 按管道域分组的统计
        pipeline_domain: 当前查询的管道域

    返回：
        list[dict]: 阶段使用统计列表，每个元素包含：
            - pipelineStage: 阶段代码
            - stageLabel: 阶段中文标签
            - featureName: 功能名称
            - detailStatus: 状态（"recorded" 已记录，"not_recorded" 未记录，"query_log_fallback" 回退）
            - lastCalledAt: 最后调用时间
            - requestCount, promptTokens, completionTokens 等统计字段
            - calls: 该阶段的详细调用记录列表
    """
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
    """
    将 LLM 调用日志查询结果行转换为字典

    与 _log_row_to_payload 类似，但用于 LLM 调用日志表。
    包含模型名称、版本、提供商等特有字段。
    """
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


# ==================== CSV 导出配置 ====================

# CSV 导出的字段名列表（按顺序）
# 用于控制导出 CSV 的列顺序和字段名
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
    """
    将查询日志列表渲染为 CSV 格式

    将日志数据转换为 CSV 格式，使用 UTF-8 with BOM 编码。
    BOM (Byte Order Mark) 确保 Excel 可以正确识别 UTF-8 编码。

    参数：
        rows: 日志记录列表（来自 fetch_rag_query_logs）

    返回：
        bytes: UTF-8 编码的 CSV 数据
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in EXPORT_FIELDNAMES})
    return buffer.getvalue().encode("utf-8-sig")
