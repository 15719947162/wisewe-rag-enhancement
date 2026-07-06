"""
图谱增强检索器 - 传统检索 + 知识图谱的"混合增强版"

【核心思想】
传统检索（向量+关键词）像"精准打击"：找到字面/语义匹配的片段
知识图谱扩展像"顺藤摸瓜"：通过实体关系找到相关片段

这个模块把两者结合起来：
1. 先用传统方法找到最相关的片段（种子）
2. 再用图谱扩展找到"种子的朋友圈"
3. 最后合并、去重、排序

【检索流程图】
用户问题
    ↓
意图识别（是查概念、查流程、还是查数据？）
    ↓
多路召回（向量 + BM25 + 实体检索）
    ↓
RRF 融合（多路结果合并）
    ↓
图谱扩展（顺藤摸瓜找相关片段）
    ↓
填充内容（从数据库加载完整片段）
    ↓
构建上下文（给 LLM 用的格式化文本）
"""

from __future__ import annotations

import time
from typing import Any

from core.rag.graph_expander import graph_expand
from core.rag.intent_router import classify_intent
from core.rag.retriever import (
    _entity_retrieve,
    _fetch_chunks_by_ids,
    _coarse_filter,
    _dense_retrieve,
    _expand_related,
    _rrf_merge,
    _sparse_retrieve,
)
from core.embedding.client import embed_query_cached, embed_texts


class GraphRetriever:
    """
    图谱增强检索器 - 传统检索 + 图谱扩展的混合方案

    就像找资料：
    1. 先用搜索引擎找最相关的（向量+BM25）
    2. 再看看这些资料的"参考文献"（图谱扩展）
    3. 最后整理成报告（构建上下文）
    """

    def retrieve(
        self,
        query: str,
        kb_id: str,
        top_k: int = 5,
        min_score: float = 0.3,
        explain: bool = False,
        intent: str | None = None,
    ) -> dict[str, Any]:
        """
        执行完整的图谱增强检索流程

        Args:
            query: 用户问题
            kb_id: 知识库 ID
            top_k: 最终返回多少个结果
            min_score: 最低相关性分数阈值
            explain: 是否返回详细路径（用于调试/解释）
            intent: 可选的意图覆盖（不自动识别）

        Returns:
            检索结果字典，包含：
            - intent: 识别出的意图
            - results: 最终的片段列表（带分数、实体、路径等）
            - context: 格式化后的上下文文本（直接喂给 LLM）
            - stats: 各阶段统计（召回数量、耗时等）
        """
        started_at = time.perf_counter()

        # 第一步：识别意图
        # 意图决定了后续图谱扩展时，哪些关系更重要
        resolved_intent, source = classify_intent(query) if not intent else (intent, "override")

        # 第二步：多路召回（向量 + BM25 + 实体）
        # 这是传统检索的部分
        recall_started = time.perf_counter()
        query_vec, query_embedding_cache_hit = embed_query_cached(query)
        dense = _dense_retrieve(query_vec, kb_id, 20)  # 向量检索
        sparse = _sparse_retrieve(query, kb_id, 20)  # BM25 关键词检索
        entity_hits = _entity_retrieve(query, kb_id, query_vec if resolved_intent != "data" else None, 20)  # 实体检索
        merged = _rrf_merge(dense, sparse, entity_hits)  # RRF 融合

        # 第三步：扩展相关片段（表格、图片等）
        expanded_related = _expand_related(merged, kb_id)

        # 第四步：粗筛
        # 保留分数 >= min_score 的前 top_k*2 个
        seed_top = _coarse_filter(expanded_related, min_score=min_score, top_n=max(top_k * 2, 10))
        recall_elapsed = int((time.perf_counter() - recall_started) * 1000)

        # 第五步：图谱扩展（顺藤摸瓜）
        # 从种子片段出发，沿实体关系找到更多相关片段
        expand_started = time.perf_counter()
        expanded_graph = graph_expand([item["id"] for item in seed_top], kb_id, resolved_intent, max_hops=2, max_neighbors=50)
        expand_elapsed = int((time.perf_counter() - expand_started) * 1000)

        # 第六步：合并去重
        # 图谱扩展的结果和种子结果合并
        by_id = {item["id"]: dict(item) for item in seed_top}
        for item in expanded_graph:
            entry = by_id.get(item["id"], {"id": item["id"], "score": 0.0})
            # 取较高的分数
            entry["score"] = max(float(entry.get("score", 0.0)), float(item["score"]))
            if explain:
                entry["path"] = item["path"]
            entry["channel"] = item.get("channel", "graph_expand")
            sources = entry.setdefault("sources", [])
            if "graph_expand" not in sources:
                sources.append("graph_expand")
            by_id[item["id"]] = entry

        # 第七步：最终排序，取 top_k
        ranked = sorted(by_id.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)[:top_k]

        # 第八步：填充内容
        # 从数据库加载完整片段内容和关联实体
        build_started = time.perf_counter()
        ranked = self._hydrate_results(ranked, kb_id, explain)

        # 第九步：构建上下文
        # 把检索结果格式化成给 LLM 用的文本
        context = self._build_context(ranked, explain)
        build_elapsed = int((time.perf_counter() - build_started) * 1000)
        total_elapsed = int((time.perf_counter() - started_at) * 1000)

        return {
            "intent": resolved_intent,
            "intent_source": source,
            "results": ranked,
            "context": context,
            "stats": {
                "recall_counts": {"embedding": len(dense), "bm25": len(sparse), "entity": len(entity_hits)},
                "after_fusion": len(merged),
                "after_expand": len(expanded_graph),
                "after_dedupe": len(ranked),
                "latency_ms": {
                    "recall": recall_elapsed,
                    "expand": expand_elapsed,
                    "build": build_elapsed,
                    "total": total_elapsed,
                },
                "query_embedding_cache_hit": query_embedding_cache_hit,
            },
        }

    def _hydrate_results(self, ranked: list[dict[str, Any]], kb_id: str, explain: bool) -> list[dict[str, Any]]:
        """
        填充结果：从数据库加载完整内容和关联实体

        之前的检索只拿到了片段 ID 和分数
        这里要把完整内容、来源、页码、实体等信息都加载出来

        Args:
            ranked: 排序后的候选列表
            kb_id: 知识库 ID
            explain: 是否包含路径信息

        Returns:
            填充后的完整结果列表
        """
        chunk_ids = [item["id"] for item in ranked]
        chunks = _fetch_chunks_by_ids(chunk_ids, kb_id)
        chunk_map = {chunk["id"]: chunk for chunk in chunks}
        entity_map = self._load_entities_for_chunks(chunk_ids, kb_id)

        hydrated: list[dict[str, Any]] = []
        for item in ranked:
            base = chunk_map.get(item["id"])
            if base is None:
                continue
            result = dict(base)
            result["score"] = float(item.get("score", 0.0) or 0.0)
            result["rank"] = len(hydrated) + 1  # 排名（从 1 开始）
            result["source"] = item.get("sources", item.get("channel", "graph_expand"))
            result["entities"] = entity_map.get(base["id"], [])  # 关联的实体列表
            if explain and item.get("path"):
                result["path"] = item["path"]  # 图谱路径（调试用）
            hydrated.append(result)
        return hydrated

    def _load_entities_for_chunks(self, chunk_ids: list[str], kb_id: str) -> dict[str, list[dict[str, Any]]]:
        """
        为片段加载关联的实体

        实体就是片段中提到的"关键词"，比如人名、地名、概念名
        比如片段提到"张三在北京开发了系统A"
        则实体有：张三（人名）、北京（地名）、系统A（产品名）

        Args:
            chunk_ids: 片段 ID 列表
            kb_id: 知识库 ID

        Returns:
            字典：chunk_id → 实体列表
        """
        if not chunk_ids:
            return {}

        from core.db.connection import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # 联表查询：片段 → 实体提及 → 实体详情
                cur.execute(
                    """
                    SELECT em.chunk_id::text, e.name, e.type, e.definition
                    FROM entity_mentions em
                    JOIN entities e ON e.id = em.entity_id
                    WHERE em.kb_id = %s AND em.chunk_id::text = ANY(%s)
                    ORDER BY e.name
                    """,
                    (kb_id, chunk_ids),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        # 按片段 ID 分组
        by_chunk: dict[str, list[dict[str, Any]]] = {}
        for chunk_id, name, entity_type, definition in rows:
            by_chunk.setdefault(chunk_id, []).append(
                {
                    "name": name,
                    "type": entity_type,
                    "definition": definition or name,
                }
            )
        return by_chunk

    def _build_context(self, hits: list[dict[str, Any]], explain: bool) -> str:
        """
        构建上下文：把检索结果格式化成给 LLM 用的文本

        就像写报告：
        - 每个片段一个章节
        - 标注来源（哪个文档哪一页）
        - 列出相关实体
        - 如果开启 explain，还显示图谱路径

        Args:
            hits: 检索结果列表
            explain: 是否包含路径信息

        Returns:
            格式化后的上下文文本
        """
        blocks: list[str] = []
        for hit in hits:
            # 格式化实体列表
            entity_lines = "\n".join(
                f"- {entity['name']}：{entity['definition']}" for entity in hit.get("entities", [])
            )
            # 片段主体：来源 + 页码 + 内容
            block = f"## {hit.get('source', '')} 第 {hit.get('page', 0)} 页\n{hit.get('content', '')}"

            # 添加实体信息
            if entity_lines:
                block += f"\n\n相关实体：\n{entity_lines}"

            # 添加图谱路径（调试用）
            if explain and hit.get("path"):
                path = " → ".join(step["rel_type"] for step in hit["path"])
                block += f"\n\n路径：{path}"

            blocks.append(block)

        # 用分隔线连接各片段
        return "\n\n---\n\n".join(blocks)
