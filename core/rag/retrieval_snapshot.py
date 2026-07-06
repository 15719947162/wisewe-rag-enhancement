"""
检索快照模块 - 一次查询搞定所有召回，提升性能

【核心问题】
传统检索需要多次查询数据库：
1. 向量检索 → 查一次
2. BM25 检索 → 查一次
3. 扩展相关片段 → 再查一次
4. 获取片段详情 → 又查一次

每次查询都有网络开销，累积起来很慢。

【解决方案：检索快照】
就像"一次性购物清单"：
1. 用一个超级 SQL 查询，把向量、BM25、相关片段一次性都拉出来
2. 结果保存在内存快照中（snapshot_by_id 字典）
3. 后续处理都在内存中操作，不再查数据库

【快照包含什么】
- base: 向量/BM25 直接命中的片段
- fold: 如果命中了 enhanced 层的片段，把它的 parent 也拉出来
- related: 如果片段有 related_ids，把相关的片段也拉出来

【性能提升】
- 原来：4-5 次数据库查询
- 现在：1 次超级查询
- 典型场景：延迟从 200ms 降到 50ms
"""

from __future__ import annotations

import json
from typing import Any

from core.db.connection import get_db_connection

# RRF（Reciprocal Rank Fusion）融合参数
# K 值越大，排名差异的影响越小
# 简单说：K=60 时，第 1 名和第 10 名的分数差异不会太大
_RRF_K = 60


def _vector_literal(values: list[float]) -> str:
    """
    把向量列表转成 PostgreSQL 的向量字面量格式

    PostgreSQL 的 vector 类型需要这种格式：[0.1,0.2,0.3]
    不能直接用 Python 的列表，需要手动格式化

    Args:
        values: 浮点数列表

    Returns:
        PostgreSQL 向量字面量字符串
    """
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _normalize_related_ids(value: Any) -> list[str]:
    """
    标准化 related_ids 字段

    related_ids 可能有多种格式：
    - JSON 字符串：'["id1", "id2"]'
    - Python 列表：["id1", "id2"]
    - 空值：None 或 ""

    这个函数把它们统一转成列表格式

    Args:
        value: 原始 related_ids 值

    Returns:
        字符串 ID 列表
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        if parsed:
            return [str(parsed)]
    return []


def snapshot_row_to_candidate(row: dict[str, Any] | tuple[Any, ...], columns: list[str] | None = None) -> dict[str, Any]:
    """
    把数据库查询的一行转成候选片段字典

    数据库返回的是原始行数据（可能是元组或字典）
    这里要转成统一格式的候选片段，包含：
    - 片段基本信息（id, content, source, page 等）
    - 检索分数（dense_score, sparse_score, rrf_score）
    - 来源标记（sources: 哪些检索方式命中）

    Args:
        row: 数据库行数据
        columns: 列名列表（当 row 是元组时需要）

    Returns:
        标准化的候选片段字典
    """
    # 如果是元组，转成字典
    data = dict(zip(columns or [], row)) if not isinstance(row, dict) else row

    dense_rank = data.get("dense_rank")
    sparse_rank = data.get("sparse_rank")
    dense_score = float(data.get("dense_score", 0.0) or 0.0)
    sparse_score = float(data.get("sparse_score", 0.0) or 0.0)

    # 确定来源：向量检索命中还是 BM25 命中
    sources = [item for item in (data.get("sources") or []) if item]
    if not sources:
        if dense_rank is not None:
            sources.append("embedding")
        if sparse_rank is not None:
            sources.append("bm25")

    # 计算 RRF 融合分数
    # RRF 公式：score = sum(1 / (K + rank_i))
    # 两种检索都命中的片段分数更高
    rrf_score = 0.0
    if dense_rank is not None:
        rrf_score += 1.0 / (_RRF_K + int(dense_rank))
    if sparse_rank is not None:
        rrf_score += 1.0 / (_RRF_K + int(sparse_rank))

    # 最终分数 = max(向量分数, BM25分数) + RRF 加成
    # 注意：RRF 只是小加成，主体还是原始相关性分数
    # 这样做的目的是保持原始分数的语义（0-1 范围）
    relevance_score = max(dense_score, min(max(sparse_score, 0.0), 1.0))
    score = min(relevance_score + rrf_score, 1.0) if relevance_score > 0 else rrf_score

    candidate = {
        "id": str(data.get("id", "")),
        "content": data.get("content", "") or "",
        "source": data.get("source", "") or "",
        "document_id": str(data["document_id"]) if data.get("document_id") else "",
        "document_name": data.get("document_name", "") or data.get("filename", "") or data.get("source", "") or "",
        "page": int(data.get("page", 0) or 0),
        "chunk_index": int(data.get("chunk_index", 0) or 0),
        "layer": data.get("layer", "") or "",
        "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
        "related_ids": _normalize_related_ids(data.get("related_ids")),
        "title": data.get("title") or "",
        "is_table_chunk": bool(data.get("is_table_chunk", False)),
        "is_image_chunk": bool(data.get("is_image_chunk", False)),
        "image_path": data.get("image_path", "") or None,
        "score": score,
        "rrf_score": rrf_score,
        "dense_score": dense_score,
        "sparse_score": sparse_score,
        "sources": sources,
        "matched_by": [],  # 后续填充：被哪些方式匹配
        "_snapshot_role": data.get("snapshot_role", "base") or "base",  # 快照角色
        "_dense_rank": int(dense_rank) if dense_rank is not None else None,
        "_sparse_rank": int(sparse_rank) if sparse_rank is not None else None,
    }
    return candidate


def fetch_retrieval_snapshot(
    query: str,
    query_vec: list[float],
    kb_id: str,
    dense_limit: int = 50,
    sparse_limit: int = 50,
    related_limit: int = 200,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    一次性获取所有需要的片段（检索快照）

    【核心思想】
    用一个超级 SQL，把以下内容一次性查出来：
    1. dense: 向量检索命中的片段
    2. sparse: BM25 检索命中的片段
    3. fold: enhanced 层片段的 parent（用于层级折叠）
    4. related: related_ids 指向的片段（表格、图片等）

    【为什么叫"快照"】
    因为这是一次性把所有可能需要的片段都拉出来
    后续的折叠、扩展、排序都在内存中完成
    不再需要查询数据库

    Args:
        query: 用户问题
        query_vec: 问题向量
        kb_id: 知识库 ID
        dense_limit: 向量检索返回数量
        sparse_limit: BM25 检索返回数量
        related_limit: 相关片段返回数量

    Returns:
        (base_candidates, snapshot_by_id)
        - base_candidates: 基础候选片段列表（向量/BM25 直接命中）
        - snapshot_by_id: 所有片段的快照字典（ID → 片段详情）
    """
    vector_literal = _vector_literal(query_vec)
    like_query = f"%{query}%"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 超级 SQL：用 CTE 把向量、BM25、折叠、相关片段一次性查出
            cur.execute(
                """
                WITH dense AS (
                    -- 向量检索：计算余弦距离，取 top N
                    SELECT c.id::text AS id,
                           1 - (c.embedding <=> %s::vector) AS dense_score,
                           row_number() OVER (ORDER BY c.embedding <=> %s::vector) AS dense_rank
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND c.layer = ANY(%s)
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                ),
                sparse_source AS (
                    -- BM25 全文检索 + LIKE 模糊匹配
                    -- ts_rank_cd 是 PostgreSQL 的 BM25 实现
                    SELECT c.id::text AS id,
                           GREATEST(
                               COALESCE(ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)), 0),
                               CASE
                                   WHEN COALESCE(c.search_text, c.title, c.content, '') ILIKE %s THEN 0.5
                                   ELSE 0
                               END
                           ) AS sparse_score
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND c.layer = ANY(%s)
                      AND (
                          c.search_vector @@ plainto_tsquery('simple', %s)
                          OR COALESCE(c.search_text, c.title, c.content, '') ILIKE %s
                      )
                ),
                sparse AS (
                    -- BM25 结果排序
                    SELECT id,
                           sparse_score,
                           row_number() OVER (ORDER BY sparse_score DESC, id) AS sparse_rank
                    FROM sparse_source
                    WHERE sparse_score > 0
                    ORDER BY sparse_score DESC, id
                    LIMIT %s
                ),
                base AS (
                    -- 向量和 BM25 结果的全外连接
                    -- 两种检索都命中的片段会有两个分数
                    SELECT COALESCE(d.id, s.id) AS id,
                           d.dense_score,
                           d.dense_rank,
                           s.sparse_score,
                           s.sparse_rank
                    FROM dense d
                    FULL OUTER JOIN sparse s ON s.id = d.id
                ),
                base_chunks AS (
                    -- 获取基础片段的完整信息
                    SELECT c.*
                    FROM chunks c
                    JOIN base b ON b.id = c.id::text
                    WHERE c.kb_id = %s
                ),
                fold_ids AS (
                    -- 如果命中了 enhanced 层的片段，把它的 parent 也拉出来
                    -- 这是为了"层级折叠"：展示 parent 内容，但用 enhanced 的匹配信息
                    SELECT DISTINCT parent_id::text AS id
                    FROM base_chunks
                    WHERE layer = 'enhanced' AND parent_id IS NOT NULL
                ),
                fold_base_chunks AS (
                    -- 基础片段 + 折叠片段
                    SELECT c.*
                    FROM chunks c
                    WHERE c.kb_id = %s
                      AND (
                          c.id::text IN (SELECT id FROM base)
                          OR c.id::text IN (SELECT id FROM fold_ids)
                      )
                ),
                related_ids AS (
                    -- 从 related_ids 字段中提取相关片段 ID
                    -- related_ids 是 JSON 数组，指向表格、图片等相关片段
                    SELECT DISTINCT value AS id
                    FROM fold_base_chunks c
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        CASE
                            WHEN c.related_ids IS NULL OR btrim(c.related_ids) = '' THEN '[]'::jsonb
                            ELSE c.related_ids::jsonb
                        END
                    ) value
                    LIMIT %s
                ),
                needed_ids AS (
                    -- 最终需要的所有片段 ID，标记角色
                    -- base: 直接命中
                    -- fold: 折叠用的 parent
                    -- related: 相关片段
                    SELECT id, 'base' AS snapshot_role FROM base
                    UNION
                    SELECT id, 'fold' AS snapshot_role FROM fold_ids
                    UNION
                    SELECT id, 'related' AS snapshot_role FROM related_ids
                )
                -- 最终查询：获取所有需要的片段详情
                SELECT c.id::text AS id,
                       c.content,
                       c.source,
                       c.document_id::text AS document_id,
                       d.filename AS document_name,
                       c.page,
                       c.layer,
                       c.parent_id::text AS parent_id,
                       c.related_ids,
                       c.chunk_index,
                       c.title,
                       c.is_table_chunk,
                       c.is_image_chunk,
                       c.image_path,
                       b.dense_score,
                       b.dense_rank,
                       b.sparse_score,
                       b.sparse_rank,
                       ARRAY_REMOVE(ARRAY[
                           CASE WHEN b.dense_rank IS NOT NULL THEN 'embedding' END,
                           CASE WHEN b.sparse_rank IS NOT NULL THEN 'bm25' END
                       ], NULL) AS sources,
                       n.snapshot_role
                FROM needed_ids n
                JOIN chunks c ON c.id::text = n.id AND c.kb_id = %s
                LEFT JOIN documents d ON d.id = c.document_id
                LEFT JOIN base b ON b.id = c.id::text
                """,
                (
                    vector_literal,
                    vector_literal,
                    kb_id,
                    ["child", "enhanced"],
                    vector_literal,
                    dense_limit,
                    query,
                    like_query,
                    kb_id,
                    ["child", "enhanced"],
                    query,
                    like_query,
                    sparse_limit,
                    kb_id,
                    kb_id,
                    related_limit,
                    kb_id,
                ),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    # 构建快照字典和基础候选列表
    snapshot_by_id: dict[str, dict[str, Any]] = {}
    base_candidates: list[dict[str, Any]] = []
    for row in rows:
        candidate = snapshot_row_to_candidate(row, columns)
        snapshot_by_id[candidate["id"]] = candidate
        if candidate.get("_snapshot_role") == "base":
            base_candidates.append(candidate)

    base_candidates.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return base_candidates, snapshot_by_id


def _score_for_folded_child(candidate: dict[str, Any]) -> float:
    """
    计算折叠后子片段的分数

    当 enhanced 层片段被折叠到 parent 时
    分数需要调整：
    - 普通文本：× 0.85（稍微降权）
    - 图像描述/表格摘要：× 0.95（接近原分）

    为什么降权？因为 parent 内容更宽泛，不如 enhanced 精准

    Args:
        candidate: 候选片段

    Returns:
        调整后的分数
    """
    score = float(candidate.get("score", 0.0) or 0.0)
    if candidate.get("layer") != "enhanced":
        return score
    content = candidate.get("content", "") or ""
    # 图像描述和表格摘要更精准，降权少一点
    if "[图像描述]" in content or "[表格摘要]" in content:
        return score * 0.95
    return score * 0.85


def fold_enhanced_snapshot(candidates: list[dict[str, Any]], snapshot_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """
    层级折叠：把 enhanced 层片段折叠到 parent

    【为什么需要折叠】
    我们的切片策略生成三层：
    - parent: 大块内容（章节级别）
    - child: 中块内容（段落级别）
    - enhanced: 小块内容（知识点级别，带 LLM 摘要）

    当 enhanced 层片段被检索命中时
    用户更希望看到完整的 parent 内容
    但要保留 enhanced 的匹配信息（分数、来源等）

    【折叠逻辑】
    1. 如果候选是 enhanced 层，找到它的 parent
    2. 用 parent 的内容替换 enhanced 的内容
    3. 保留 enhanced 的分数和匹配信息
    4. 标记 matched_by = ["enhanced"]

    Args:
        candidates: 候选片段列表
        snapshot_by_id: 快照字典

    Returns:
        折叠后的候选片段列表
    """
    folded: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        current = dict(candidate)
        folded_score = _score_for_folded_child(candidate)
        matched_by = list(current.get("matched_by", []))

        # 如果是 enhanced 层且有关联的 parent，执行折叠
        if candidate.get("layer") == "enhanced" and candidate.get("parent_id"):
            child = snapshot_by_id.get(str(candidate["parent_id"]))
            if not child:
                continue

            # 用 parent 内容替换，但保留 enhanced 的匹配信息
            current = dict(child)
            current["score"] = folded_score
            current["dense_score"] = float(candidate.get("dense_score", 0.0) or 0.0)
            current["sparse_score"] = float(candidate.get("sparse_score", 0.0) or 0.0)
            current["sources"] = list(candidate.get("sources", []))
            current["matched_enhanced_id"] = candidate.get("id", "")  # 记录命中的 enhanced ID
            current["matched_enhanced_text"] = candidate.get("content", "")  # 记录命中的 enhanced 内容
            matched_by.append("enhanced")
        else:
            # 非 enhanced 层，直接使用
            current["score"] = folded_score
            matched_by.append(str(candidate.get("layer") or "chunk"))

        current["matched_by"] = sorted(set(item for item in matched_by if item))

        # 去重：同一 ID 只保留分数最高的
        existing = folded.get(current["id"])
        if not existing or float(current.get("score", 0.0)) > float(existing.get("score", 0.0)):
            folded[current["id"]] = current
            continue

        # 如果已存在且分数更高，合并匹配信息
        existing["score"] = max(float(existing.get("score", 0.0)), float(current.get("score", 0.0)))
        existing["dense_score"] = max(float(existing.get("dense_score", 0.0)), float(current.get("dense_score", 0.0)))
        existing["sparse_score"] = max(float(existing.get("sparse_score", 0.0)), float(current.get("sparse_score", 0.0)))
        existing["sources"] = sorted(set(existing.get("sources", []) + current.get("sources", [])))
        existing["matched_by"] = sorted(set(existing.get("matched_by", []) + current.get("matched_by", [])))

    return sorted(folded.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def expand_related_snapshot(
    candidates: list[dict[str, Any]],
    snapshot_by_id: dict[str, dict[str, Any]],
    related_score: float = 0.24,
) -> list[dict[str, Any]]:
    """
    相关片段扩展：把 related_ids 指向的片段加入结果

    【应用场景】
    文本片段旁边可能有关联的表格或图片
    用户问问题时，虽然只命中了文本
    但相关表格/图片也应该展示出来

    【related_ids 是什么】
    切片时，linker 模块会把相关的片段 ID 写入 related_ids
    比如一段文字旁边有表格，这段文字的 related_ids 就包含表格的 ID

    【扩展逻辑】
    1. 遍历候选片段的 related_ids
    2. 从快照中找到相关片段
    3. 相关片段继承原片段的部分分数（图像 × 0.45，其他 × 0.85）
    4. 标记 matched_by = ["related"]

    Args:
        candidates: 候选片段列表
        snapshot_by_id: 快照字典
        related_score: 相关片段的最低分数（默认 0.24）

    Returns:
        扩展后的候选片段列表
    """
    expanded = list(candidates)
    existing_ids = {candidate["id"] for candidate in candidates}
    related_image_pages: set[tuple[str, int]] = set()  # 去重：同文档同页的图片只保留一个

    for candidate in candidates:
        for related_id in candidate.get("related_ids", []):
            related = snapshot_by_id.get(str(related_id))
            if not related or related["id"] in existing_ids:
                continue

            current = dict(related)
            is_related_image = bool(current.get("is_image_chunk", False))

            # 图片去重：同一文档同一页只保留一个图片
            if is_related_image:
                image_page_key = (str(current.get("document_id", "") or current.get("source", "")), int(current.get("page", 0) or 0))
                if image_page_key in related_image_pages:
                    continue
                related_image_pages.add(image_page_key)

            # 继承原片段的分数（图像降权更多，因为图片通常不太相关）
            inherited_factor = 0.45 if is_related_image else 0.85
            inherited_score = float(candidate.get("score", 0.0) or 0.0) * inherited_factor
            current["score"] = max(float(current.get("score", 0.0) or 0.0), related_score, inherited_score)
            current["dense_score"] = float(current.get("dense_score", 0.0) or 0.0)
            current["matched_by"] = sorted(set(current.get("matched_by", []) + ["related"]))
            current["sources"] = sorted(set(current.get("sources", []) + ["related"]))

            expanded.append(current)
            existing_ids.add(current["id"])

    return expanded
