"""
重排序（Reranker）模块

## 什么是重排序？

重排序是 RAG（检索增强生成）系统中的关键步骤，用于对初始检索结果进行精细化排序。
在向量检索阶段，系统通过语义相似度找到可能相关的文档，但这种"第一遍检索"往往不够精确。
重排序模型会对查询和候选文档进行深度语义分析，重新评估相关性，提升最终结果的质量。

## 为什么需要重排序？

1. **向量检索的局限性**：向量相似度搜索基于整体语义表示，可能遗漏细节匹配，
   例如专业术语、特定实体或精确短语。

2. **召回 vs 精度的平衡**：向量检索擅长召回（Recall），找到大量潜在相关文档；
   重排序模型擅长精度（Precision），从候选中筛选出真正相关的文档。

3. **跨编码器优势**：重排序模型通常是跨编码器（Cross-Encoder），能同时看到查询和文档，
   理解它们之间的深层关系，而向量检索使用的是双编码器（Bi-Encoder），分别编码查询和文档。

## 本模块的重排序策略

本模块实现了"父子层级重排序"策略，专门针对分层切片的 RAG 系统：

1. **子层级优先重排序**：优先对细粒度的"子切片"进行重排序，
   因为子切片包含具体知识点，与查询的相关性更直接。

2. **父层级聚合**：将相关子切片的得分传递给对应的父切片（章节级别），
   这样既能定位到具体段落，又能提供完整的上下文。

3. **上下文窗口扩展**：对于父切片，围绕最相关的子切片构建上下文窗口，
   提供完整的前后文，避免信息碎片化。

4. **LLM 最终检查**（可选）：使用大语言模型对重排序结果进行二次验证，
   进一步过滤掉表面相关但实际无关的文档。

## 重排序流程

```
查询 + 候选文档
    ↓
子层级重排序（调用 Rerank API）
    ↓
父层级聚合（子切片得分 → 父切片）
    ↓
上下文窗口构建
    ↓
Top-K 筛选
    ↓
LLM 最终检查（可选）
    ↓
最终结果
```
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from core.http_client import create_httpx_client
from core.llm_usage import TokenUsage, extract_response_usage
from core.db.connection import get_db_connection


def _call_reranker_api(query: str, documents: list[str], model: str = "gte-rerank") -> list[float]:
    """
    调用重排序 API 获取文档相关性得分。

    ## 工作原理

    重排序模型（如阿里云 GTE-Rerank）采用跨编码器架构，将查询和每个文档
    作为一对输入，计算精确的相关性得分。与向量检索的余弦相似度不同，
    重排序得分反映的是"文档是否真的回答了问题"。

    ## 参数

    - query: 用户查询文本
    - documents: 待重排序的文档列表
    - model: 重排序模型名称，默认使用阿里云 GTE-Rerank

    ## 返回

    与 documents 顺序对应的相关性得分列表，得分范围通常为 [0, 1]，越高越相关。
    如果 API 调用失败，返回全零列表作为降级处理。

    ## 降级策略

    当以下情况发生时，返回零分列表：
    - 无候选文档
    - 缺少 API Key
    - API 调用异常
    - 响应格式错误
    - 得分数量与文档数量不匹配
    """
    if not documents:
        return []
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return [0.0] * len(documents)

    payload = {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False},
    }
    try:
        with create_httpx_client(timeout=30) as client:
            response = client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code != 200:
                return [0.0] * len(documents)
            body = response.json()
    except Exception:
        return [0.0] * len(documents)

    try:
        results = body["output"]["results"]
        scores = [float(item.get("relevance_score", 0.0) or 0.0) for item in results]
    except Exception:
        return [0.0] * len(documents)
    if len(scores) != len(documents):
        return [0.0] * len(documents)
    return scores


def _rerank_children(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    对子层级切片进行重排序。

    ## 设计思路

    在分层切片策略中，文档被切分为两个层级：
    - **父切片（parent）**：章节级别，提供完整的上下文框架
    - **子切片（child）**：段落/知识点级别，包含具体信息

    用户查询通常与子切片的相关性更强，因为：
    1. 子切片粒度更细，更容易找到精确匹配的知识点
    2. 子切片内容更聚焦，语义边界更清晰
    3. 父切片往往包含多个子主题，可能引入噪声

    因此，本函数优先对子切片调用重排序 API，子切片的得分会通过
    `_aggregate_to_parents()` 函数传递给对应的父切片。

    ## 处理流程

    1. 提取所有 layer="child" 的候选切片
    2. 调用重排序 API 计算子切片相关性得分
    3. 如果 API 失败，降级使用原始向量检索得分
    4. 非子切片保留原始得分
    5. 按得分降序排列所有候选

    ## 参数

    - query: 用户查询文本
    - candidates: 检索返回的候选切片列表

    ## 返回

    重排序后的候选列表，每个候选新增 `rerank_score` 字段。
    """
    if not candidates:
        return []

    reranked = [dict(candidate) for candidate in candidates]
    child_indices = [idx for idx, candidate in enumerate(reranked) if candidate.get("layer") == "child"]
    if not child_indices:
        for candidate in reranked:
            candidate["rerank_score"] = float(candidate.get("score", 0.0) or 0.0)
        return sorted(reranked, key=lambda item: item.get("rerank_score", 0.0), reverse=True)

    child_docs = [reranked[idx].get("content", "") for idx in child_indices]
    child_scores = _call_reranker_api(query, child_docs)
    fallback = not child_scores or max(child_scores) <= 0
    child_score_map = {
        child_index: float(reranked[child_index].get("score", 0.0) or 0.0)
        for child_index in child_indices
    }
    if not fallback:
        for child_index, rerank_score in zip(child_indices, child_scores):
            child_score_map[child_index] = float(rerank_score or 0.0)

    for idx, candidate in enumerate(reranked):
        if idx in child_score_map:
            candidate["rerank_score"] = child_score_map[idx]
        else:
            candidate["rerank_score"] = float(candidate.get("score", 0.0) or 0.0)
    return sorted(reranked, key=lambda item: item.get("rerank_score", 0.0), reverse=True)


def _normalize_related_ids(value: Any) -> list[str]:
    """
    规范化 related_ids 字段为字符串列表。

    ## 背景

    related_ids 字段存储与当前切片关联的其他切片 ID，用于将文本切片与
    相邻的表格、图片切片关联起来。该字段可能以以下形式存储：
    - Python 列表：['id1', 'id2']
    - JSON 字符串：'["id1", "id2"]'
    - 单个字符串：'id1'

    本函数将这些格式统一转换为字符串列表，便于后续处理。

    ## 参数

    - value: 原始 related_ids 值，可能是列表、字符串或 None

    ## 返回

    规范化后的字符串列表，空值返回空列表。
    """
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    return []


def _aggregate_to_parents(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    """
    将子切片的重排序得分聚合到父切片。

    ## 为什么需要聚合？

    在分层切片策略中，检索和重排序主要针对子切片进行，但最终返回时可能需要父切片：
    1. **完整上下文**：父切片（章节）提供完整的论述背景，避免碎片化
    2. **结构导航**：用户可能需要知道答案在哪个章节
    3. **去重**：多个子切片可能属于同一父切片，聚合后去重

    ## 聚合策略

    对于每个有子切片命中的父切片：
    - 继承得分最高的子切片的 rerank_score
    - 设置 layer="parent" 标记父切片层级
    - 记录 best_child_id 标识哪个子切片最相关
    - 构建上下文窗口（在 `_window_reorder` 中完成）

    ## 参数

    - candidates: 重排序后的候选列表，包含子切片和非子切片
    - kb_id: 知识库 ID，用于查询数据库获取父切片详情

    ## 返回

    合并后的候选列表，包含：
    - 所有被聚合的父切片（从数据库查询完整信息）
    - 原始非子切片候选（如表格、图片切片）
    """
    if not candidates:
        return []

    children = [candidate for candidate in candidates if candidate.get("layer") == "child" and candidate.get("parent_id")]
    parent_ids = sorted({str(candidate["parent_id"]) for candidate in children if candidate.get("parent_id")})
    if not parent_ids:
        return candidates

    best_child_by_parent: dict[str, dict[str, Any]] = {}
    for child in children:
        parent_id = str(child["parent_id"])
        best_child = best_child_by_parent.get(parent_id)
        if not best_child or float(child.get("rerank_score", 0.0)) > float(best_child.get("rerank_score", 0.0)):
            best_child_by_parent[parent_id] = child

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, source, page, chunk_index, layer, parent_id, related_ids
                FROM chunks
                WHERE id::text = ANY(%s) AND kb_id = %s
                """,
                (parent_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    parents: list[dict[str, Any]] = []
    for row in rows:
        data = dict(zip(columns, row))
        parent_id = str(data.get("id", ""))
        best_child = best_child_by_parent.get(parent_id)
        if not best_child:
            continue
        parent_score = float(best_child.get("rerank_score", 0.0) or 0.0)
        parents.append(
            {
                "id": parent_id,
                "content": data.get("content", "") or "",
                "source": data.get("source", "") or "",
                "page": int(data.get("page", 0) or 0),
                "chunk_index": int(data.get("chunk_index", 0) or 0),
                "layer": "parent",
                "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
                "related_ids": _normalize_related_ids(data.get("related_ids")),
                "score": parent_score,
                "dense_score": float(best_child.get("dense_score", 0.0) or 0.0),
                "rerank_score": parent_score,
                "context_window": data.get("content", "") or "",
                "best_child_id": best_child.get("id", ""),
            }
        )

    merged: list[dict[str, Any]] = parents[:]
    for candidate in candidates:
        if candidate.get("layer") != "child" or not candidate.get("parent_id"):
            merged.append(dict(candidate))
    return sorted(merged, key=lambda item: item.get("rerank_score", 0.0), reverse=True)


def _window_reorder(
    candidates: list[dict[str, Any]],
    kb_id: str,
    window_size: int = 3,
) -> list[dict[str, Any]]:
    """
    为父切片构建上下文窗口。

    ## 什么是上下文窗口？

    当检索命中父切片时，用户看到的应该是一个完整的阅读单元，而不仅是切片片段。
    上下文窗口是以最相关子切片为中心，前后扩展一定数量子切片形成的文本段落。

    ## 为什么需要上下文窗口？

    1. **避免断章取义**：单独的子切片可能语义不完整
    2. **提供衔接信息**：前后文帮助理解核心内容
    3. **平衡精度与完整**：保留重排序的精准定位，同时提供足够上下文

    ## 示例

    假设章节有 10 个子切片，最相关的是第 5 个，窗口大小为 3：
    - 中心切片：子切片 5（得分最高）
    - 窗口范围：子切片 4、5、6（共 3 个）
    - 返回内容：子切片 4 + 5 + 6 的合并文本

    ## 参数

    - candidates: 候选切片列表
    - kb_id: 知识库 ID
    - window_size: 窗口大小（包含前后子切片的总数），默认 3

    ## 返回

    更新后的候选列表，父切片新增 `context_window` 字段包含窗口文本。
    非父切片的 `context_window` 直接设为其自身内容。
    """
    if not candidates:
        return []

    updated = [dict(candidate) for candidate in candidates]
    conn = get_db_connection()
    try:
        for candidate in updated:
            if candidate.get("layer") != "parent":
                candidate["context_window"] = candidate.get("content", "") or ""
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, chunk_index
                    FROM chunks
                    WHERE parent_id::text = %s AND kb_id = %s
                    ORDER BY chunk_index
                    """,
                    (candidate["id"], kb_id),
                )
                rows = cur.fetchall()
            if not rows:
                candidate["context_window"] = candidate.get("content", "") or ""
                continue

            center_index = 0
            best_child_id = str(candidate.get("best_child_id", ""))
            for idx, row in enumerate(rows):
                if str(row[0]) == best_child_id:
                    center_index = idx
                    break
            half_window = max(window_size // 2, 0)
            start = max(center_index - half_window, 0)
            end = min(start + window_size, len(rows))
            if end - start < window_size:
                start = max(end - window_size, 0)
            window_rows = rows[start:end]
            candidate["context_window"] = "\n\n".join(str(row[1] or "") for row in window_rows) or candidate.get("content", "")
    finally:
        conn.close()
    return updated


def _llm_final_check(query: str, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    使用 LLM 对重排序结果进行最终验证。

    ## 为什么需要 LLM 检查？

    重排序模型虽然比向量检索更精确，但仍然基于模式匹配和语义相似度，
    可能存在以下问题：
    1. **表面相关**：文档包含查询关键词，但实际不是答案
    2. **语义歧义**：多义词导致误判
    3. **领域知识缺失**：专业领域的相关性判断需要推理能力

    LLM 具备推理能力，能理解"问题-答案"的深层关系，
    可以过滤掉表面相关但实际无关的文档。

    ## 检查流程

    对每个候选切片：
    1. 构造提示词：询问内容是否与问题相关
    2. 调用 LLM 获取判断（只回答 yes 或 no）
    3. 如果回答包含 "no"，则过滤该候选

    ## 成本考虑

    LLM 检查会增加额外延迟和 Token 消耗，因此：
    - 仅在 `use_llm_check=True` 时启用
    - 只对 top-k 结果检查，而非全部候选
    - 使用低温度（temperature=0）确保判断稳定

    ## 参数

    - query: 用户查询文本
    - candidates: 重排序后的候选列表

    ## 返回

    元组 (filtered_candidates, token_metrics)：
    - filtered_candidates: 通过 LLM 检查的候选列表
    - token_metrics: Token 使用统计
    """
    if not candidates:
        return [], {}
    try:
        from core.rag.generator import _get_rag_llm_client, _get_rag_llm_model

        client = _get_rag_llm_client()
        model = _get_rag_llm_model()
    except Exception:
        return candidates, {}

    filtered: list[dict[str, Any]] = []
    token_usage = TokenUsage()
    try:
        from core.llm_config import resolve_llm_param
        system_prompt = resolve_llm_param("", "system_prompt", [])
        for candidate in candidates:
            prompt = (
                f"以下内容是否与问题相关？问题：{query}\n"
                f"内容：{(candidate.get('context_window') or candidate.get('content') or '')[:300]}\n"
                "只回答 yes 或 no"
            )
            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            token_usage.add_usage(extract_response_usage(response))
            text = (response.choices[0].message.content or "").strip().lower()
            if "no" not in text:
                filtered.append(candidate)
    except Exception:
        return candidates, token_usage.to_metrics("rerankLlmCheck")
    return filtered or candidates, token_usage.to_metrics("rerankLlmCheck")


class ParentChildReranker:
    """
    父子层级重排序器。

    ## 职责

    这是 RAG 检索管道中重排序阶段的主入口，负责：
    1. 对检索结果进行精细化重排序
    2. 处理分层切片的特殊逻辑
    3. 构建合理的上下文窗口
    4. 可选的 LLM 二次验证

    ## 使用方式

    ```python
    reranker = ParentChildReranker()
    results = reranker.rerank(
        query="如何配置切片策略？",
        candidates=retrieval_results,  # 向量检索返回的候选
        kb_id="kb_123",
        top_k=8,
        use_llm_check=True,  # 启用 LLM 最终检查
    )
    ```

    ## 设计原则

    1. **子优先**：子切片提供精确信息，优先重排序
    2. **父聚合**：父切片提供上下文，通过子切片得分推导
    3. **窗口化**：避免碎片化，提供完整阅读单元
    4. **可验证**：可选 LLM 检查，提升最终精度
    """

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        kb_id: str,
        top_k: int = 8,
        use_llm_check: bool = False,
    ) -> list[dict[str, Any]]:
        """
        执行完整的重排序流程。

        ## 处理流程

        ```
        1. 子切片重排序
           ↓
        2. 上下文窗口构建
           ↓
        3. Top-K 筛选
           ↓
        4. LLM 最终检查（可选）
        ```

        ## 参数

        - query: 用户查询文本
        - candidates: 向量检索返回的候选切片列表
        - kb_id: 知识库 ID，用于查询父切片详情
        - top_k: 返回的最大候选数量，默认 8
        - use_llm_check: 是否启用 LLM 最终检查，默认 False

        ## 返回

        重排序后的候选列表，按 rerank_score 降序排列。
        每个候选包含以下字段：
        - id: 切片 ID
        - content: 切片内容
        - rerank_score: 重排序得分
        - context_window: 上下文窗口文本
        - layer: 切片层级（parent/child）
        - 其他元数据字段

        ## 注意事项

        - 如果候选为空，返回空列表
        - LLM 检查结果存储在 `self.last_metrics` 中
        - 重排序失败时会降级使用原始向量检索得分
        """
        if not candidates:
            return []

        reranked = _rerank_children(query, candidates)
        # Final evidence stays at child granularity. Parent chunks are reserved for
        # structure/navigation rather than replacing child evidence in answer context.
        windowed = _window_reorder(reranked, kb_id)
        top_candidates = sorted(windowed, key=lambda item: item.get("rerank_score", 0.0), reverse=True)[:top_k]
        self.last_metrics: dict[str, int] = {}
        if use_llm_check:
            top_candidates, self.last_metrics = _llm_final_check(query, top_candidates)
        return top_candidates
