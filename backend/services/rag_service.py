"""
RAG 服务层 — 检索增强生成（Retrieval-Augmented Generation）核心业务逻辑

本模块是 RAG 系统的应用层服务，负责将底层 RAG 组件（检索器、重排器、生成器、评分器）
组装成完整的端到端查询管道，并提供 HTTP 友好的响应格式。

================================================================================
架构位置与职责
================================================================================

位置层级：
    HTTP 路由层 (routes/) → 本模块 (rag_service.py) → RAG 适配器 (rag_adapter.py)
                                    ↓
                            核心能力层 (core/rag/)

核心职责：
    1. **请求编排**：协调检索、重排、生成、评分四个阶段的执行顺序
    2. **数据转换**：将核心层的领域模型转换为前端友好的 JSON 格式
    3. **权限校验**：验证用户对知识库的访问权限
    4. **日志记录**：记录查询日志和 LLM 调用日志，用于审计和分析
    5. **评估收集**：将查询结果存入评估存储，用于后续质量分析

================================================================================
RAG 管道完整流程（run_rag_query）
================================================================================

┌─────────────────────────────────────────────────────────────────────────────┐
│                          标准 RAG 查询管道                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 混合检索 (Retrieval)                                                     │
│     ├─ 稠密检索：向量相似度搜索（embedding + pgvector）                       │
│     ├─ 稀疏检索：关键词匹配（BM25/全文搜索）                                  │
│     ├─ 粗过滤：按 min_score 阈值过滤低质量候选                                │
│     └─ RRF 融合：倒数排名融合算法合并多路结果                                  │
│                                                                             │
│  2. 父子重排 (Rerank)                                                        │
│     ├─ 父子关联：利用层级切片的 parent-child 关系                             │
│     ├─ 窗口扩展：为最佳子块补充上下文窗口                                      │
│     ├─ LLM 检查（可选）：让 LLM 判断候选是否真正回答问题                      │
│     └─ 分数调整：综合稠密分数、稀疏分数、LLM 判断调整排序                     │
│                                                                             │
│  3. 答案生成 (Generate)                                                      │
│     ├─ 上下文构建：将重排后的候选组装成提示词上下文                            │
│     ├─ LLM 生成：调用大模型生成自然语言答案                                   │
│     ├─ 引用标注：在答案中标注来源引用 [1][2]...                               │
│     └─ 无法回答检测：当上下文不足时标记 cannot_answer                         │
│                                                                             │
│  4. 质量评分 (Score)                                                         │
│     ├─ 相关性评分：答案与问题的相关程度（规则 + 可选 LLM）                     │
│     ├─ 忠实度评分：答案是否有上下文支撑（避免幻觉）                            │
│     └─ LLM 评分（可选）：让 LLM 对答案质量打分（1-5 分）                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

================================================================================
Graph RAG 管道流程（run_graph_rag_query）
================================================================================

Graph RAG 是标准 RAG 的扩展，通过知识图谱增强检索：

    用户查询 → 意图识别 → 图谱遍历/子图提取 → 结构化推理 → 结果返回

适用于需要复杂推理、多跳查询的场景。

================================================================================
核心组件组合方式
================================================================================

标准 RAG 管道组件：
    - Retriever（检索器）：core/rag/retriever.py
    - Reranker（重排器）：core/rag/reranker.py
    - Generator（生成器）：core/rag/generator.py
    - Scorer（评分器）：core/rag/scorer.py

适配器层：
    - rag_adapter.py：将上述组件串联，返回包含详细元数据的结果

本服务层：
    - 接收适配器返回的原始结果
    - 执行权限校验
    - 转换为前端 JSON 格式
    - 记录日志和评估数据

================================================================================
关键数据结构
================================================================================

输入：
    - QueryRequest：包含查询文本、知识库 ID、top_k、min_score 等参数
    - GraphQueryRequest：Graph RAG 专用请求，额外包含意图和解释标志

输出（标准 RAG）：
    - requestId：请求唯一标识
    - answer：生成的答案文本
    - citations：引用列表，标注答案来源
    - candidates：重排后的候选块列表
    - scores：相关性、忠实度、LLM 评分
    - timings：各阶段耗时
    - recallChannels：召回渠道统计（稠密/稀疏/结构化/RRF/相关）
    - trace：执行追踪，用于调试和可视化

输出（Graph RAG）：
    - intent：识别的用户意图
    - intentSource：意图识别来源（规则/LLM）
    - results：图谱查询结果
    - stats：统计信息

================================================================================
使用示例
================================================================================

    from backend.services.rag_service import run_rag_query
    from backend.schemas.requests import QueryRequest

    request = QueryRequest(
        query="什么是机器学习？",
        kb_id="kb_001",
        top_k=10,
        min_score=0.5,
        use_llm_check=True,
        use_llm_score=True,
    )
    result = run_rag_query(request, identity=user_context)
    print(result["answer"])  # 生成的答案

================================================================================
"""

from __future__ import annotations

import uuid

from backend.adapters.rag_adapter import run_graph_rag_pipeline, run_rag_pipeline
from backend.schemas.requests import GraphQueryRequest, QueryRequest
from backend.services.evaluation_store import append_evaluation
from core.db.identity import IdentityContext
from core.db.knowledge_base import get_knowledge_base
from core.db.query_logs import LlmCallLogRecord, RagQueryLogRecord, append_llm_call_log, append_rag_query_log
from core.runtime_settings import resolve_runtime_setting


# =============================================================================
# 工具函数：图片资源 URL 转换
# =============================================================================


def _image_asset_url(image_path: str | None) -> str | None:
    """
    将图片路径转换为前端可访问的 API URL。

    处理逻辑：
        1. 空路径返回 None
        2. 已经是 HTTP(S) URL 或 Data URI 的直接返回
        3. 本地路径转换为 /api/assets/output/ 相对路径

    Args:
        image_path: 原始图片路径，可能是本地路径或 URL

    Returns:
        前端可访问的相对 URL，或 None（如果无法转换）

    Examples:
        >>> _image_asset_url("data/output/images/img.png")
        '/api/assets/output/images/img.png'
        >>> _image_asset_url("https://example.com/img.png")
        'https://example.com/img.png'
    """
    if not image_path:
        return None
    # 统一使用正斜杠
    normalized = image_path.replace("\\", "/")
    # 已经是完整 URL 或 Data URI，直接返回
    if normalized.startswith(("http://", "https://", "data:image/")):
        return normalized
    # 从路径中提取 data/output/ 之后的部分
    marker = "/data/output/"
    if marker in normalized:
        relative = normalized.split(marker, 1)[1]
    elif normalized.startswith("data/output/"):
        relative = normalized[len("data/output/") :]
    elif "/output/" in normalized:
        relative = normalized.split("/output/", 1)[1]
    else:
        return None
    return f"/api/assets/output/{relative.lstrip('/')}"


# =============================================================================
# 核心 RAG 查询服务
# =============================================================================


def run_rag_query(
    payload: QueryRequest,
    identity: IdentityContext | None = None,
    request_id: str | None = None,
    pipeline_domain: str = "online_rag",
) -> dict:
    """
    执行标准 RAG（检索增强生成）查询。

    这是 RAG 系统的主入口，编排完整的检索-重排-生成-评分管道路线。
    适用于问答、知识检索、文档摘要等场景。

    完整流程：
        1. 权限校验：验证用户是否有权访问目标知识库
        2. 混合检索：同时执行稠密检索（向量相似度）和稀疏检索（关键词）
        3. 父子重排：利用层级切片关系扩展上下文，可选 LLM 检查候选相关性
        4. 答案生成：基于重排后的候选上下文，调用 LLM 生成自然语言答案
        5. 质量评分：计算相关性、忠实度分数，可选 LLM 打分
        6. 日志记录：记录查询日志和 LLM 调用日志，用于审计和分析
        7. 评估收集：将结果存入评估存储，用于后续质量分析

    Args:
        payload: 查询请求参数
            - query: 用户查询文本
            - kb_id: 知识库 ID
            - top_k: 返回的候选数量上限
            - min_score: 候选最低分数阈值
            - use_llm_check: 是否使用 LLM 检查候选相关性
            - use_llm_score: 是否使用 LLM 对答案质量打分
        identity: 用户身份上下文，用于权限校验。None 表示跳过权限检查
        request_id: 请求唯一标识，不传则自动生成 UUID
        pipeline_domain: 管道域标识，用于日志分类，默认 "online_rag"

    Returns:
        包含完整查询结果的字典，结构如下：
            - requestId: 请求唯一标识
            - query: 原始查询文本
            - kbId: 知识库 ID
            - answer: 生成的答案文本
            - cannotAnswer: 是否标记为无法回答
            - citations: 引用列表，每个引用包含来源文档、页码、片段等
            - scores: 质量评分（相关性、忠实度、LLM 评分）
            - timings: 各阶段耗时详情
            - recallChannels: 召回渠道统计（稠密/稀疏/结构化/RRF/相关）
            - candidates: 重排后的候选块列表
            - contextWindow: 上下文窗口文本列表
            - trace: 执行追踪信息

    Raises:
        ValueError: 知识库不存在或用户无权访问

    Example:
        >>> from backend.schemas.requests import QueryRequest
        >>> payload = QueryRequest(query="什么是 AI?", kb_id="kb_001", top_k=5)
        >>> result = run_rag_query(payload)
        >>> print(result["answer"])
        'AI（人工智能）是计算机科学的一个分支...'
    """
    """
    # -------------------------------------------------------------------------
    # 第一阶段：权限校验
    # -------------------------------------------------------------------------
    # 验证用户是否有权访问目标知识库
    # 如果 identity 为 None 或 enforce_access=False，则跳过校验
    _assert_kb_access(payload.kb_id, identity)

    # 生成或使用传入的请求 ID，用于全链路追踪
    request_id = request_id or str(uuid.uuid4())

    # -------------------------------------------------------------------------
    # 第二阶段：执行 RAG 管道
    # -------------------------------------------------------------------------
    # 调用适配器层，依次执行：检索 → 重排 → 生成 → 评分
    # 返回四个结果：
    #   - candidates: 检索阶段的原始候选列表
    #   - reranked: 重排后的候选列表（用于答案生成）
    #   - answer: 生成器产出的答案对象（包含答案文本、引用、是否无法回答）
    #   - scores: 评分器产出的分数（相关性、忠实度、LLM 评分）
    candidates, reranked, answer, scores = run_rag_pipeline(
        query=payload.query,
        kb_id=payload.kb_id,
        top_k=payload.top_k,
        min_score=payload.min_score,
        use_llm_check=payload.use_llm_check,
        use_llm_score=payload.use_llm_score,
    )

    # 提取各阶段耗时数据
    latency_ms = scores.get("_latency_ms", {}) if isinstance(scores, dict) else {}

    # -------------------------------------------------------------------------
    # 第三阶段：召回渠道统计
    # -------------------------------------------------------------------------
    # 统计各召回渠道的候选数量，用于分析和优化检索策略
    recall_channels = [
        # 稠密检索：向量相似度搜索命中的候选
        {"channel": "dense", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) > 0)},
        # 稀疏检索：关键词匹配命中的候选（dense_score 为 0 表示仅稀疏命中）
        {"channel": "sparse", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) == 0)},
        # 结构化检索：目前暂未使用
        {"channel": "structured", "count": 0},
        # RRF 融合：最终融合后的候选总数
        {"channel": "rrf", "count": len(candidates)},
        # 相关候选：分数较低但可能相关的候选（用于补充上下文）
        {"channel": "related", "count": sum(1 for c in candidates if c.get("score", 0.0) <= 0.3)},
    ]

    # -------------------------------------------------------------------------
    # 第四阶段：构建响应结果
    # -------------------------------------------------------------------------
    result = {
        "requestId": request_id,
        "query": payload.query,
        "kbId": payload.kb_id,
        # 答案文本
        "answer": answer.get("answer", ""),
        # 是否标记为无法回答（上下文不足以支持回答）
        "cannotAnswer": bool(answer.get("cannot_answer", False)),
        # 引用列表：标注答案来源
        "citations": [_citation_to_payload(c) for c in answer.get("citations", [])],
        # 质量评分
        "scores": {
            "relevanceScore": float(scores.get("relevance_score", 0.0) or 0.0),
            "faithfulnessScore": float(scores.get("faithfulness_score", 0.0) or 0.0),
            "llmScore": scores.get("llm_score", None),
            "cannotAnswer": bool(answer.get("cannot_answer", False)),
            "interpretation": "结果由检索器、重排器、生成器和评分器共同产生。",
        },
        # 各阶段耗时
        "timings": _timings_to_payload(latency_ms),
        # 召回渠道统计
        "recallChannels": recall_channels,
        # 重排后的候选列表
        "candidates": [_candidate_to_payload(c) for c in reranked],
        # 上下文窗口：用于答案生成的文本片段
        "contextWindow": [c.get("context_window", c.get("content", "")) for c in reranked],
        # 执行追踪：用于调试和可视化
        "trace": [
            {"key": "retrieval", "label": "混合检索", "status": "success", "detail": "稠密与稀疏候选经粗过滤后完成融合。"},
            {"key": "rerank", "label": "父子重排", "status": "success", "detail": "最佳子块窗口被提升进答案上下文。"},
            {"key": "generate", "label": "答案生成", "status": "success", "detail": "生成器仅允许使用提供的上下文内容。"},
            {
                "key": "score",
                "label": "质量评分",
                "status": "success" if not answer.get("cannot_answer", False) else "degraded",
                "detail": "规则评分结合可选的 LLM 评分。",
            },
        ],
    }

    # 为追踪记录添加各阶段耗时
    _attach_trace_timings(result["trace"], latency_ms)

    # -------------------------------------------------------------------------
    # 第五阶段：记录评估数据
    # -------------------------------------------------------------------------
    # 将查询结果存入评估存储，用于后续质量分析和人工评估
    append_evaluation(
        {
            "id": str(uuid.uuid4()),
            "kbId": payload.kb_id,
            "query": result["query"],
            "answer": result["answer"],
            "relevanceScore": result["scores"]["relevanceScore"],
            "faithfulnessScore": result["scores"]["faithfulnessScore"],
            "llmScore": result["scores"]["llmScore"],
            "cannotAnswer": result["cannotAnswer"],
            "failureReason": None,
        }
    )

    # -------------------------------------------------------------------------
    # 第六阶段：记录日志
    # -------------------------------------------------------------------------
    # 计算 Token 使用总量
    prompt_tokens, completion_tokens, total_tokens = _token_usage_totals(result["timings"].get("llmUsage", {}))

    # 记录各阶段的 LLM 调用日志（用于成本分析和审计）
    _record_rag_llm_usage(
        request_id=request_id,
        kb_id=payload.kb_id,
        identity=identity,
        llm_usage=result["timings"].get("llmUsage", {}),
        latency_ms=result["timings"]["latencyMs"],
        pipeline_domain=pipeline_domain,
    )

    # 记录 RAG 查询日志（用于查询分析和统计）
    append_rag_query_log(
        RagQueryLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            kb_id=payload.kb_id,
            query=payload.query,
            answer=result["answer"],
            identity=identity,
            cannot_answer=result["cannotAnswer"],
            relevance_score=result["scores"]["relevanceScore"],
            faithfulness_score=result["scores"]["faithfulnessScore"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=result["timings"]["latencyMs"]["total"],
        )
    )

    return result


# =============================================================================
# 辅助转换函数
# =============================================================================


def _timings_to_payload(latency_ms: dict) -> dict:
    """
    将耗时数据转换为前端友好的格式。

    将各阶段的毫秒级耗时数据转换为符合前端约定的 JSON 结构，
    包含总体耗时、检索细分耗时、LLM 使用统计等。

    Args:
        latency_ms: 原始耗时数据字典，键为阶段名，值为毫秒数

    Returns:
        转换后的耗时数据结构：
            - latencyMs: 各阶段耗时（retrieval/rerank/generate/score/build/total）
            - retrievalBreakdownMs: 检索阶段细分耗时
            - llmUsage: LLM Token 使用统计
            - shortCircuit: 是否触发短路（如命中缓存）
    """
    return {
        # 各主要阶段的耗时（毫秒）
        "latencyMs": {
            "retrieval": int(latency_ms.get("retrieval", 0) or 0),
            "rerank": int(latency_ms.get("rerank", 0) or 0),
            "generate": int(latency_ms.get("generate", 0) or 0),
            "score": int(latency_ms.get("score", 0) or 0),
            "build": int(latency_ms.get("build", 0) or 0),
            "total": int(latency_ms.get("total", 0) or 0),
        },
        # 检索阶段的细分耗时（稠密检索、稀疏检索、融合等）
        "retrievalBreakdownMs": _retrieval_breakdown_to_payload(
            latency_ms.get("retrieval_breakdown", {})
        ),
        # LLM Token 使用统计
        "llmUsage": _llm_usage_to_payload(latency_ms.get("llm_usage", {})),
        # 是否触发短路（如命中缓存直接返回）
        "shortCircuit": bool(latency_ms.get("short_circuit", False)),
    }


def _retrieval_breakdown_to_payload(value: object) -> dict:
    """
    将检索阶段细分耗时转换为前端格式。

    处理检索阶段的各个子步骤耗时，如向量检索、稀疏检索、RRF 融合等。
    同时处理特殊的 short_circuit 标志。

    Args:
        value: 原始细分耗时数据

    Returns:
        转换后的细分耗时字典，键名转换为驼峰命名
    """
    if not isinstance(value, dict):
        return {}
    payload: dict[str, int | bool] = {}
    for key, raw in value.items():
        # 特殊处理 short_circuit 标志
        if key == "short_circuit":
            payload["shortCircuit"] = bool(raw)
        else:
            # 其他键名转换为驼峰命名
            payload[_snake_to_camel(str(key))] = int(raw or 0)
    return payload


def _llm_usage_to_payload(value: object) -> dict:
    """
    将 LLM Token 使用统计转换为前端格式。

    提取各阶段的 Prompt Token、Completion Token 和总 Token 数，
    键名转换为驼峰命名。

    Args:
        value: 原始 Token 使用数据

    Returns:
        转换后的 Token 使用统计字典
    """
    if not isinstance(value, dict):
        return {}
    return {
        _snake_to_camel(str(key)): int(raw or 0)
        for key, raw in value.items()
        # 只保留数值类型，排除布尔值
        if isinstance(raw, (int, float)) and not isinstance(raw, bool)
    }


def _snake_to_camel(value: str) -> str:
    """
    将蛇形命名（snake_case）转换为驼峰命名（camelCase）。

    Args:
        value: 蛇形命名字符串

    Returns:
        驼峰命名字符串

    Examples:
        >>> _snake_to_camel("retrieval_breakdown")
        'retrievalBreakdown'
        >>> _snake_to_camel("llm_usage")
        'llmUsage'
    """
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _attach_trace_timings(trace: list[dict], latency_ms: dict) -> None:
    """
    为执行追踪记录添加各阶段耗时。

    直接修改传入的 trace 列表，为每个追踪项添加 latencyMs 字段。

    Args:
        trace: 执行追踪列表
        latency_ms: 各阶段耗时数据

    Note:
        此函数会原地修改 trace 列表
    """
    for item in trace:
        key = item.get("key", "")
        item["latencyMs"] = int(latency_ms.get(key, 0) or 0)


# =============================================================================
# Graph RAG 查询服务
# =============================================================================


def run_graph_rag_query(
    payload: GraphQueryRequest,
    identity: IdentityContext | None = None,
    request_id: str | None = None,
    pipeline_domain: str = "graph_rag",
) -> dict:
    """
    执行 Graph RAG（图谱增强检索）查询。

    Graph RAG 是标准 RAG 的扩展，通过知识图谱增强检索和推理能力。
    适用于需要复杂推理、多跳查询、关系发现的场景。

    与标准 RAG 的区别：
        - 标准RAG：向量检索 → 重排 → 生成
        - Graph RAG：意图识别 → 图谱遍历 → 结构化推理 → 结果返回

    典型应用场景：
        - 多跳问答："A和B的共同作者有哪些？"
        - 关系发现："X公司的高管之前在哪些公司任职？"
        - 路径查询："从概念A到概念B的推导路径是什么？"

    Args:
        payload: Graph RAG 查询请求参数
            - query: 用户查询文本
            - kb_id: 知识库 ID
            - top_k: 返回的候选数量上限
            - min_score: 候选最低分数阈值
            - explain: 是否返回解释信息
            - intent: 可选的意图覆盖（跳过自动意图识别）
        identity: 用户身份上下文，用于权限校验
        request_id: 请求唯一标识，不传则自动生成 UUID
        pipeline_domain: 管道域标识，用于日志分类，默认 "graph_rag"

    Returns:
        包含 Graph RAG 查询结果的字典，结构如下：
            - requestId: 请求唯一标识
            - query: 原始查询文本
            - kbId: 知识库 ID
            - intent: 识别或指定的用户意图
            - intentSource: 意图识别来源（规则/LLM）
            - results: 图谱查询结果列表
            - stats: 统计信息

    Raises:
        ValueError: 知识库不存在或用户无权访问

    Example:
        >>> from backend.schemas.requests import GraphQueryRequest
        >>> payload = GraphQueryRequest(
        ...     query="AI领域有哪些华人学者？",
        ...     kb_id="kb_001",
        ...     top_k=10,
        ...     explain=True,
        ... )
        >>> result = run_graph_rag_query(payload)
        >>> print(result["intent"])
        'entity_query'
    """
    # 权限校验
    _assert_kb_access(payload.kb_id, identity)
    request_id = request_id or str(uuid.uuid4())

    # 执行 Graph RAG 管道
    result = run_graph_rag_pipeline(
        query=payload.query,
        kb_id=payload.kb_id,
        top_k=payload.top_k,
        min_score=payload.min_score,
        explain=payload.explain,
        intent=payload.intent,
    )

    # 构建响应
    response = {
        "requestId": request_id,
        "query": payload.query,
        "kbId": payload.kb_id,
        "intent": result["intent"],
        "intentSource": result["intent_source"],
        "results": result["results"],
        "stats": result["stats"],
    }

    # 记录查询日志
    append_rag_query_log(
        RagQueryLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            kb_id=payload.kb_id,
            query=payload.query,
            identity=identity,
            total_tokens=0,
            latency_ms=0,
        )
    )
    return response


# =============================================================================
# LLM 调用日志记录
# =============================================================================


def _record_rag_llm_usage(
    *,
    request_id: str,
    kb_id: str,
    identity: IdentityContext | None,
    llm_usage: object,
    latency_ms: dict,
    pipeline_domain: str,
) -> None:
    """
    记录 RAG 管道中各阶段的 LLM 调用日志。

    RAG 管道可能在多个阶段调用 LLM：
        1. 重排阶段的 LLM 检查（use_llm_check=True）
        2. 答案生成阶段（必须）
        3. 评分阶段的 LLM 打分（use_llm_score=True）

    此函数分别记录每个阶段的 Token 使用量和耗时，
    用于成本分析和性能优化。

    Args:
        request_id: 请求唯一标识
        kb_id: 知识库 ID
        identity: 用户身份上下文
        llm_usage: 各阶段的 Token 使用统计
        latency_ms: 各阶段耗时数据
        pipeline_domain: 管道域标识
    """
    if not isinstance(llm_usage, dict):
        return

    # 定义需要记录的 LLM 调用阶段
    # (token前缀, 管道域, 管道阶段, 功能名称, 耗时键)
    for item in (
        ("rerankLlmCheck", pipeline_domain, "rerank", "重排 LLM 检查", "rerank"),
        ("generate", pipeline_domain, "generation", "问答生成 LLM", "generate"),
        ("score", "evaluation", "evaluation", "评测打分 LLM", "score"),
    ):
        prefix, domain, stage, feature_name, latency_key = item
        _record_rag_llm_usage_prefix(
            request_id=request_id,
            kb_id=kb_id,
            identity=identity,
            llm_usage=llm_usage,
            latency_ms=latency_ms,
            token_prefix=prefix,
            pipeline_domain=domain,
            pipeline_stage=stage,
            feature_name=feature_name,
            latency_key=latency_key,
        )


def _record_rag_llm_usage_prefix(
    *,
    request_id: str,
    kb_id: str,
    identity: IdentityContext | None,
    llm_usage: dict,
    latency_ms: dict,
    token_prefix: str,
    pipeline_domain: str,
    pipeline_stage: str,
    feature_name: str,
    latency_key: str,
) -> None:
    """
    记录单个 LLM 调用的日志。

    从 llm_usage 字典中提取指定前缀的 Token 使用统计，
    并记录到 LLM 调用日志表中。

    Args:
        request_id: 请求唯一标识
        kb_id: 知识库 ID
        identity: 用户身份上下文
        llm_usage: Token 使用统计字典
        latency_ms: 各阶段耗时数据
        token_prefix: Token 统计键前缀（如 "generate"、"score"）
        pipeline_domain: 管道域标识
        pipeline_stage: 管道阶段名称
        feature_name: 功能名称（用于日志展示）
        latency_key: 对应的耗时键名
    """
    # 提取 Token 使用量
    prompt_tokens = int(llm_usage.get(f"{token_prefix}PromptTokens", 0) or 0)
    completion_tokens = int(llm_usage.get(f"{token_prefix}CompletionTokens", 0) or 0)
    total_tokens = int(llm_usage.get(f"{token_prefix}TotalTokens", 0) or 0)

    # 如果没有 Token 使用，则跳过记录
    if total_tokens <= 0 and prompt_tokens + completion_tokens <= 0:
        return

    # 确保 total_tokens 有值
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens

    # 记录 LLM 调用日志
    append_llm_call_log(
        LlmCallLogRecord(
            request_id=request_id,
            pipeline_domain=pipeline_domain,
            pipeline_stage=pipeline_stage,
            feature_name=feature_name,
            provider=_runtime_str("RAG_LLM_BASE_URL", "openai-compatible"),
            model_name=_runtime_str("RAG_LLM_MODEL", "qwen-max"),
            kb_id=kb_id,
            identity=identity,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int(latency_ms.get(latency_key, 0) or 0),
        )
    )


def _runtime_str(key: str, default: str) -> str:
    """
    从运行时配置中获取字符串值。

    尝试从运行时设置中解析配置值，失败时返回默认值。

    Args:
        key: 配置键名
        default: 默认值

    Returns:
        配置值或默认值
    """
    try:
        value, _source = resolve_runtime_setting(key)
    except Exception:
        value = default
    return str(value or default)


# =============================================================================
# 权限校验
# =============================================================================


def _assert_kb_access(kb_id: str, identity: IdentityContext | None = None) -> None:
    """
    断言用户有权访问指定知识库。

    如果用户身份为 None 或未启用权限校验（enforce_access=False），
    则跳过检查。否则验证知识库是否存在且用户有权访问。

    Args:
        kb_id: 知识库 ID
        identity: 用户身份上下文

    Raises:
        ValueError: 知识库不存在或用户无权访问
    """
    if identity is None or not identity.enforce_access:
        return
    if get_knowledge_base(kb_id, identity) is None:
        raise ValueError(f"Knowledge base '{kb_id}' not found or not accessible")


# =============================================================================
# Token 使用统计
# =============================================================================


def _token_usage_totals(llm_usage: object) -> tuple[int, int, int]:
    """
    计算所有 LLM 调用的 Token 使用总量。

    从 llm_usage 字典中汇总所有阶段的 Prompt Token、
    Completion Token 和总 Token 数。

    Args:
        llm_usage: LLM Token 使用统计字典

    Returns:
        (prompt_tokens, completion_tokens, total_tokens) 三元组
    """
    if not isinstance(llm_usage, dict):
        return 0, 0, 0

    # 汇总所有阶段的 Prompt Token
    prompt = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("PromptTokens"))

    # 汇总所有阶段的 Completion Token
    completion = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("CompletionTokens"))

    # 汇总所有阶段的总 Token
    total = sum(int(value or 0) for key, value in llm_usage.items() if str(key).endswith("TotalTokens"))

    # 如果 total 未记录，则使用 prompt + completion
    if total <= 0:
        total = prompt + completion

    return prompt, completion, total


# =============================================================================
# 数据转换函数
# =============================================================================


def _citation_to_payload(citation: dict) -> dict:
    """
    将引用数据转换为前端友好的格式。

    引用数据标注答案中每个引用标记的来源信息，
    用于在前端展示答案来源和跳转链接。

    Args:
        citation: 原始引用数据字典

    Returns:
        转换后的引用数据，包含：
            - index: 引用索引（对应答案中的 [1][2]...）
            - source: 来源文件路径
            - documentName: 文档名称（用于展示）
            - documentId: 文档 ID
            - page: 页码
            - chunkIndex: 切片索引
            - location: 位置描述
            - snippet: 引用片段文本
            - chunkId: 切片 ID
    """
    chunk_index = citation.get("chunk_index", None)
    return {
        "index": int(citation.get("index", 0) or 0),
        "source": citation.get("source", ""),
        "documentName": citation.get("document_name", citation.get("source", "")),
        "documentId": citation.get("document_id", ""),
        "page": int(citation.get("page", 0) or 0),
        "chunkIndex": int(chunk_index) if chunk_index is not None else None,
        "location": citation.get("location", ""),
        "snippet": citation.get("snippet", ""),
        "chunkId": citation.get("chunk_id", ""),
    }


def _candidate_to_payload(item: dict) -> dict:
    """
    将候选块数据转换为前端友好的格式。

    候选块是检索和重排阶段产出的核心数据，包含文本内容、
    来源信息、分数、层级关系等。

    Args:
        item: 原始候选块数据字典

    Returns:
        转换后的候选块数据，包含：
            - id: 块 ID
            - source: 来源文件路径
            - documentName: 文档名称
            - documentId: 文档 ID
            - page: 页码
            - chunkIndex: 切片索引
            - location: 位置描述（格式化为 "P.X · #Y"）
            - layer: 层级（parent/child/enhanced）
            - score: 最终分数
            - denseScore: 稠密检索分数
            - rerankScore: 重排分数
            - channel: 召回渠道（rrf）
            - title: 标题
            - content: 文本内容
            - contextWindow: 扩展的上下文窗口
            - isImageChunk: 是否为图片块
            - imagePath: 图片路径
            - imageUrl: 图片 URL（前端可访问）
            - relatedIds: 关联块 ID 列表
            - bestChildId: 最佳子块 ID（父子层级时使用）
            - matchedBy: 匹配方式列表
            - matchedEnhancedId: 匹配的增强块 ID
    """
    image_path = item.get("image_path", "") or None
    return {
        "id": item.get("id", ""),
        "source": item.get("source", ""),
        "documentName": item.get("document_name", item.get("source", "")),
        "documentId": item.get("document_id", ""),
        "page": int(item.get("page", 0) or 0),
        "chunkIndex": int(item.get("chunk_index", 0) or 0),
        "location": item.get("location") or _format_candidate_location(item),
        "layer": item.get("layer", "child"),
        "score": float(item.get("score", 0.0) or 0.0),
        "denseScore": float(item.get("dense_score", 0.0) or 0.0),
        "rerankScore": float(item.get("rerank_score", item.get("score", 0.0)) or 0.0),
        "channel": "rrf",
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "contextWindow": item.get("context_window", item.get("content", "")),
        "isImageChunk": bool(item.get("is_image_chunk", False)),
        "imagePath": image_path,
        "imageUrl": _image_asset_url(image_path),
        "relatedIds": item.get("related_ids", []),
        "bestChildId": item.get("best_child_id", ""),
        "matchedBy": item.get("matched_by", []),
        "matchedEnhancedId": item.get("matched_enhanced_id", ""),
    }


def _format_candidate_location(item: dict) -> str:
    """
    格式化候选块的位置描述。

    将页码和切片索引格式化为用户友好的位置字符串，
    如 "P.5 · #3" 表示第 5 页第 3 个切片。

    Args:
        item: 候选块数据字典

    Returns:
        格式化的位置字符串
    """
    return f"P.{int(item.get('page', 0) or 0)} · #{int(item.get('chunk_index', 0) or 0) + 1}"
