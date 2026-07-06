"""
RAG 适配器模块

本模块实现了将 core.rag 领域能力适配到 backend 服务层的适配器模式。

## 适配器模式说明

适配器模式（Adapter Pattern）是一种结构型设计模式，它允许不兼容的接口之间能够协同工作。
在本项目中：

- **被适配者（Adaptee）**: core.rag 模块中的各个组件
  - HybridRetriever: 混合检索器（向量 + 关键词）
  - GraphRetriever: 图检索器（知识图谱）
  - ParentChildReranker: 父子切片重排序器
  - RAGGenerator: RAG 答案生成器
  - RAGScorer: RAG 答案评分器

- **目标接口（Target）**: backend 服务层需要的简洁接口
  - 简单的函数调用接口
  - 统一的数据格式
  - 完整的性能指标

- **适配器（Adapter）**: 本模块提供的函数
  - run_rag_pipeline(): 标准 RAG 流水线
  - run_graph_rag_pipeline(): 图 RAG 流水线

## 适配器职责

1. **接口转换**: 将 core 模块的复杂 API 转换为 backend 简洁的函数接口
2. **数据编排**: 协调多个 core 组件的调用顺序和数据流转
3. **指标收集**: 统一收集各阶段的性能指标（延迟、LLM 使用量）
4. **结果聚合**: 将各组件输出聚合成 backend 需要的格式

## 数据流转

```
backend (HTTP 请求)
    ↓
run_rag_pipeline()  # 适配器入口
    ├─→ HybridRetriever.retrieve()      # 检索候选文档
    ├─→ ParentChildReranker.rerank()    # 重排序
    ├─→ RAGGenerator.generate()         # 生成答案
    └─→ RAGScorer.score()               # 评分
    ↓
(candidates, reranked, answer, scores)  # 统一返回格式
    ↓
backend (HTTP 响应)
```
"""
from __future__ import annotations

import time

from core.rag.generator import RAGGenerator
from core.rag.graph_retriever import GraphRetriever
from core.rag.reranker import ParentChildReranker
from core.rag.retriever import HybridRetriever
from core.rag.scorer import RAGScorer


def _elapsed_ms(started_at: float) -> int:
    """
    计算从指定时间点到现在经过的毫秒数。

    这是一个内部辅助函数，用于精确测量各阶段的执行时间。
    使用 time.perf_counter() 获取高精度时间戳，适合性能基准测试。

    Args:
        started_at: 起始时间戳（由 time.perf_counter() 返回）

    Returns:
        int: 经过的毫秒数（整数）

    Example:
        >>> start = time.perf_counter()
        >>> # ... 执行某些操作 ...
        >>> elapsed = _elapsed_ms(start)  # 返回毫秒数
    """
    return int((time.perf_counter() - started_at) * 1000)


def _merge_usage(*values: dict | None) -> dict[str, int]:
    """
    合并多个 LLM 使用量统计字典。

    在 RAG 流水线中，多个组件（reranker、generator、scorer）可能都会调用 LLM，
    本函数将它们的使用量统计合并为一个汇总字典，便于监控和分析。

    Args:
        *values: 任意数量的使用量字典（可能为 None）
                每个字典的格式如：{"input_tokens": 100, "output_tokens": 50}

    Returns:
        dict[str, int]: 合并后的使用量统计，格式如：
            {
                "input_tokens": 300,    # 所有输入 token 之和
                "output_tokens": 150,   # 所有输出 token 之和
            }

    Notes:
        - 自动跳过 None 值和非字典类型
        - 自动跳过布尔值（某些组件可能返回 bool 类型的标记）
        - 只合并 int/float 类型的数值
    """
    merged: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, raw in value.items():
            # 跳过布尔值，只处理数值类型
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                merged[str(key)] = int(merged.get(str(key), 0) or 0) + int(raw or 0)
    return merged


def run_rag_pipeline(
    query: str,
    kb_id: str,
    top_k: int,
    min_score: float,
    use_llm_check: bool,
    use_llm_score: bool,
) -> tuple[list[dict], list[dict], dict, dict]:
    """
    运行完整的 RAG（检索增强生成）流水线。

    这是 RAG 适配器的核心函数，协调 core.rag 模块中的多个组件完成：
    1. 混合检索（向量 + 关键词）
    2. 父子切片重排序
    3. LLM 答案生成
    4. 答案质量评分

    适配器职责：
    - 协调组件调用顺序
    - 收集性能指标
    - 统一返回数据格式

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID，用于过滤和隔离数据
        top_k: 返回的 top-k 文档数量
        min_score: 最小相关性分数阈值
        use_llm_check: 是否使用 LLM 进行相关性判断（在重排序阶段）
        use_llm_score: 是否使用 LLM 进行答案质量评分

    Returns:
        tuple[list[dict], list[dict], dict, dict]: 包含四个元素的元组
            - candidates: 检索阶段的候选文档列表，每个文档是一个字典
                格式: {
                    "id": str,           # 切片 ID
                    "content": str,      # 文档内容
                    "score": float,      # 检索分数
                    "source": str,       # 文档来源
                    "page": int,         # 页码
                    ...
                }
            - reranked: 重排序后的文档列表（已去重、重打分）
                格式与 candidates 类似，额外包含：
                - "rerank_score": float  # 重排序分数
                - "context_window": str  # 扩展上下文窗口
            - answer: LLM 生成的答案
                格式: {
                    "answer": str,           # 生成的答案文本
                    "citations": list[dict],  # 引用列表
                    "cannot_answer": bool,   # 是否无法回答
                    "llm_usage": dict,        # LLM 使用量统计
                }
            - scores: 答案质量评分和性能指标
                格式: {
                    "relevance_score": float,    # 相关性分数
                    "faithfulness_score": float,  # 忠实度分数
                    "llm_score": float | None,    # LLM 评分（可选）
                    "_latency_ms": {              # 性能指标
                        "retrieval": int,         # 检索耗时（毫秒）
                        "rerank": int,            # 重排序耗时
                        "generate": int,          # 生成耗时
                        "score": int,             # 评分耗时
                        "total": int,             # 总耗时
                        "short_circuit": bool,    # 是否短路
                        "retrieval_breakdown": dict, # 检索各子阶段耗时
                        "llm_usage": dict,         # LLM 使用量汇总
                    },
                }

    流程说明:
        标准流程：
            检索 → 重排序 → 生成 → 评分

        短路优化（媒体引用场景）：
            当检测到检索结果是媒体引用模式时，跳过重排序、生成、评分，
            直接构建结构化答案。这大幅降低了延迟。

    Example:
        >>> candidates, reranked, answer, scores = run_rag_pipeline(
        ...     query="什么是机器学习？",
        ...     kb_id="kb_001",
        ...     top_k=5,
        ...     min_score=0.5,
        ...     use_llm_check=True,
        ...     use_llm_score=True,
        ... )
        >>> print(answer["answer"])  # 打印生成的答案
        >>> print(scores["_latency_ms"]["total"])  # 打印总耗时
    """
    total_started_at = time.perf_counter()

    # 初始化流水线各阶段组件
    retriever = HybridRetriever()  # 混合检索器（向量 + 关键词）
    reranker = ParentChildReranker()  # 父子切片重排序器
    generator = RAGGenerator()  # RAG 答案生成器
    scorer = RAGScorer()  # 答案质量评分器

    # ========== 第一阶段：检索 ==========
    # 使用混合检索策略，结合向量相似度和关键词匹配
    retrieval_started_at = time.perf_counter()
    candidates = retriever.retrieve(
        query=query,
        kb_id=kb_id,
        top_k=max(top_k * 2, top_k),  # 检索更多候选，供重排序筛选
        min_score=min_score,
    )
    retrieval_ms = _elapsed_ms(retrieval_started_at)
    retrieval_breakdown_ms = getattr(retriever, "last_timings", {})

    # ========== 短路优化：媒体引用模式 ==========
    # 当检索结果是媒体引用（如图片、表格定位）时，跳过后续阶段直接返回
    # 这可以大幅降低延迟，适合"查找某图表在哪里"这类查询
    if candidates and candidates[0].get("retrieval_mode") == "media_ref":
        build_started_at = time.perf_counter()

        # 直接按分数排序并取 top-k
        direct = sorted(candidates, key=lambda item: item.get("score", 0.0), reverse=True)[:top_k]

        # 为每个结果添加重排序分数和上下文窗口
        for item in direct:
            item["rerank_score"] = float(item.get("score", 0.0) or 0.0)
            item["context_window"] = item.get("content", "") or ""

        # 构建媒体引用答案（结构化格式）
        answer = _build_media_ref_answer(query, direct)
        build_ms = _elapsed_ms(build_started_at)

        # 构建评分结果（媒体引用场景默认高分）
        scores = {
            "relevance_score": 1.0 if direct else 0.0,
            "faithfulness_score": 1.0 if direct else 0.0,
            "llm_score": None,
            "_latency_ms": {
                "retrieval": retrieval_ms,
                "rerank": 0,  # 短路跳过
                "generate": 0,  # 短路跳过
                "score": 0,  # 短路跳过
                "build": build_ms,
                "total": _elapsed_ms(total_started_at),
                "short_circuit": True,  # 标记短路优化
                "retrieval_breakdown": retrieval_breakdown_ms,
                "llm_usage": {},
            },
        }
        return candidates, direct, answer, scores

    # ========== 第二阶段：重排序 ==========
    # 使用父子切片策略重排序，提升文档相关性
    rerank_started_at = time.perf_counter()
    reranked = reranker.rerank(
        query=query,
        candidates=candidates,
        kb_id=kb_id,
        top_k=top_k,
        use_llm_check=use_llm_check,
    )
    rerank_ms = _elapsed_ms(rerank_started_at)

    # ========== 第三阶段：生成答案 ==========
    # 使用 LLM 基于重排序结果生成答案
    generate_started_at = time.perf_counter()
    answer = generator.generate(query, reranked)
    generate_ms = _elapsed_ms(generate_started_at)

    # ========== 第四阶段：评分 ==========
    # 对生成的答案进行质量评分（相关性、忠实度）
    score_started_at = time.perf_counter()
    scores = scorer.score(
        query=query,
        contexts=reranked,
        answer_dict=answer,
        use_llm_score=use_llm_score,
    )

    # ========== 聚合 LLM 使用量 ==========
    # 合并各阶段的 LLM token 使用统计
    llm_usage = _merge_usage(
        getattr(reranker, "last_metrics", {}),
        answer.get("llm_usage", {}),
        scores.get("llm_usage", {}),
    )

    # 添加性能指标到评分结果
    scores["_latency_ms"] = {
        "retrieval": retrieval_ms,
        "rerank": rerank_ms,
        "generate": generate_ms,
        "score": _elapsed_ms(score_started_at),
        "total": _elapsed_ms(total_started_at),
        "short_circuit": False,  # 标记完整流程
        "retrieval_breakdown": retrieval_breakdown_ms,
        "llm_usage": llm_usage,
    }

    return candidates, reranked, answer, scores


def _build_media_ref_answer(query: str, contexts: list[dict]) -> dict:
    """
    构建媒体引用答案（用于图表、图片定位场景）。

    当用户的查询意图是定位某个图表、图片或表格在文档中的位置时，
    不需要生成自然语言答案，而是返回结构化的位置信息。

    这是一种特殊的数据转换逻辑：
    - 输入：检索到的媒体引用结果
    - 输出：结构化的定位答案（带引用）

    Args:
        query: 用户原始查询（如"第三章的销售增长图表在哪里"）
        contexts: 检索到的媒体引用结果列表
            格式: [{
                "document_name": str,  # 文档名称
                "source": str,         # 文档来源
                "page": int,           # 页码
                "chunk_index": int,    # 切片索引
                "matched_media_ref": str,  # 匹配的媒体引用标签
                "content": str,        # 文本内容
                "context_window": str, # 上下文窗口
                "document_id": str,    # 文档 ID
                "id": str,            # 切片 ID
            }]

    Returns:
        dict: 结构化答案
            格式: {
                "answer": str,           # 答案文本（如"已定位到销售增长图表，来源：年报.pdf，位置：P.15"）
                "citations": list[dict], # 引用列表（结构化位置信息）
                "cannot_answer": bool,   # 是否无法回答（contexts 为空时为 True）
            }

    Example:
        >>> answer = _build_media_ref_answer(
        ...     query="销售增长图表在哪里",
        ...     contexts=[{
        ...         "document_name": "年报.pdf",
        ...         "page": 15,
        ...         "matched_media_ref": "图3-1 销售增长趋势",
        ...     }]
        ... )
        >>> print(answer["answer"])
        # 输出: "已定位到 图3-1 销售增长趋势，来源：年报.pdf，位置：P.15。[1]"
    """
    # 无匹配结果，返回无法回答标记
    if not contexts:
        return {
            "answer": "根据现有文档无法回答该问题",
            "citations": [],
            "cannot_answer": True,
        }

    # 提取第一个匹配结果的元数据
    first = contexts[0]
    source = first.get("document_name") or first.get("source", "")
    page = int(first.get("page", 0) or 0)
    chunk_index = first.get("chunk_index", None)

    # 构建位置描述字符串
    # 格式: "P.页码" 或 "P.页码 · #切片索引"
    location = f"P.{page}" if chunk_index is None else f"P.{page} · #{int(chunk_index) + 1}"

    # 获取媒体引用标签（如"图3-1 销售增长趋势"）
    label = first.get("matched_media_ref") or query

    # 构建答案文本，格式: "已定位到 {标签}，来源：{文档}，位置：{位置}。[引用编号]"
    answer = f"已定位到 {label}，来源：{source}，位置：{location}。[1]"

    # 构建结构化引用对象
    citation = {
        "index": 1,  # 引用编号
        "source": source,
        "document_name": source,
        "document_id": first.get("document_id", ""),
        "page": page,
        "chunk_index": chunk_index,
        "location": location,
        "snippet": (first.get("context_window") or first.get("content") or "")[:100],  # 摘要前 100 字符
        "chunk_id": first.get("id", ""),
    }

    return {
        "answer": answer,
        "citations": [citation],
        "cannot_answer": False,
    }


def run_graph_rag_pipeline(
    query: str,
    kb_id: str,
    top_k: int,
    min_score: float,
    explain: bool,
    intent: str | None,
) -> dict:
    """
    运行图 RAG（Graph RAG）流水线。

    图 RAG 是一种基于知识图谱的检索增强生成方法。与传统的向量检索不同，
    它利用知识图谱的结构化信息来提升检索的准确性和可解释性。

    ## 适配器职责

    本函数作为 GraphRetriever 与 backend 服务之间的适配器：
    - 将 HTTP 请求参数映射到 GraphRetriever 的调用参数
    - 提供简洁的函数接口，隐藏 core 模块的复杂性
    - 返回统一格式的结果

    ## 与标准 RAG 的区别

    | 特性 | 标准 RAG | 图 RAG |
    |------|---------|--------|
    | 检索方式 | 向量相似度 + 关键词 | 知识图谱遍历 |
    | 结构化程度 | 低（文本切片） | 高（实体、关系） |
    | 可解释性 | 较低 | 较高（可展示推理路径） |
    | 适用场景 | 通用问答 | 复杂关系推理 |

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID
        top_k: 返回的 top-k 结果数量
        min_score: 最小相关性分数阈值
        explain: 是否返回推理路径解释（用于可解释性）
        intent: 用户意图标注（可选，用于优化检索策略）
            - None: 自动检测意图
            - str: 显式指定意图（如"查找实体"、"关系推理"）

    Returns:
        dict: 图 RAG 检索结果
            格式: {
                "results": list[dict],  # 检索结果列表
                "graph_context": dict,  # 图谱上下文信息
                "explanation": str | None,  # 推理路径解释（explain=True 时）
                ...
            }

    Example:
        >>> result = run_graph_rag_pipeline(
        ...     query="张三和李四有什么关系？",
        ...     kb_id="kb_001",
        ...     top_k=5,
        ...     min_score=0.5,
        ...     explain=True,
        ...     intent="关系推理",
        ... )
        >>> print(result["explanation"])
        # 输出: "张三 --[同事]--> 李四"
    """
    # 初始化图检索器
    retriever = GraphRetriever()

    # 执行图谱检索
    # GraphRetriever 内部会：
    # 1. 解析查询中的实体
    # 2. 在图谱中定位实体节点
    # 3. 遍历相关路径
    # 4. 聚合路径上的信息
    return retriever.retrieve(
        query=query,
        kb_id=kb_id,
        top_k=top_k,
        min_score=min_score,
        explain=explain,
        intent=intent,
    )
