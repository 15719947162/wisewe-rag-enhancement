"""
RAG 检索模块 - 混合检索与多路召回

本模块实现了 RAG（Retrieval-Augmented Generation）系统的核心检索功能，
采用混合检索策略，结合多种召回路径以提高检索质量和召回率。

## 向量检索原理

向量检索（密集检索）是将文本转换为高维向量（embeddings），然后在向量空间中
计算相似度的检索方式。核心思想是：语义相似的文本在向量空间中距离更近。

相似度计算：
- 余弦相似度：cos(a, b) = (a·b) / (||a|| * ||b||)
- 余弦距离：1 - cos(a, b)，范围 [0, 2]
- 欧氏距离：||a - b||₂
- PostgreSQL pgvector 使用余弦距离运算符 `<=>`

检索流程：
1. 查询向量化：使用相同的 embedding 模型将查询文本转为向量
2. 向量相似度计算：在向量空间中找到与查询向量最相近的 K 个向量
3. 返回对应的文本块作为检索结果

## 混合检索策略

本模块采用多路召回 + RRF 融合的策略：

1. **密集检索（Dense Retrieval）**：基于向量相似度
   - 使用 pgvector 的余弦距离进行 ANN（近似最近邻）检索
   - 语义理解能力强，能处理同义词和语义相关内容

2. **稀疏检索（Sparse Retrieval）**：基于 BM25
   - 传统关键词匹配，基于 TF-IDF 的改进算法
   - 精确匹配能力强，对专有名词和术语效果好

3. **结构化检索（Structured Retrieval）**：基于元数据过滤
   - 按文档来源、标题、页码范围等条件精确筛选
   - 适用于用户明确知道目标范围的情况

4. **媒体引用检索（Media Reference Retrieval）**：图表引用识别
   - 识别"图1"、"表2-3"等引用，直接定位图表切片
   - 跳过文本相似度计算，直接返回图表内容

5. **实体检索（Entity Retrieval）**：基于实体知识库
   - 匹配预定义的实体（术语、概念等）及其别名
   - 实体与切片的关联关系提供额外的召回路径

## RRF 融合算法

RRF（Reciprocal Rank Fusion）是一种简单高效的排序融合方法：
- RRF_score(d) = Σ (1 / (k + rank_i(d)))
- k 通常设为 60
- 对于每个召回通道，按排名位置计算贡献分数
- 最终按总分数排序

## 层级检索与折叠

支持三层切片层级：
- parent: 父级切片（章节级别），提供上下文
- child: 子级切片（知识点级别），精确召回
- enhanced: 增强切片（LLM 摘要），语义匹配

检索时优先使用 child + enhanced 层级，
增强切片命中后折叠回其对应的子级切片作为证据返回。

## 相关切片扩展

利用切片的 related_ids 字段扩展召回：
- 文本切片与相邻的图表切片建立关联
- 主召回命中文本切片时，自动补充关联的图表切片
- 提供更完整的上下文信息

## 性能优化

- 查询向量缓存：embed_query_cached() 避免重复向量化
- BM25 索引缓存：_bm25_cache 避免重复构建倒排索引
- 检索快照：预计算的检索缓存，加速高频查询
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from typing import Any

from core.db.connection import get_db_connection
from core.embedding.client import embed_query_cached, embed_texts
from core.rag.retrieval_snapshot import (
    expand_related_snapshot,
    fetch_retrieval_snapshot,
    fold_enhanced_snapshot,
)

# ============================================================================
# BM25 稀疏检索实现
# ============================================================================

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    # 当 rank_bm25 库未安装时，使用内置的简化实现
    # BM25 是 TF-IDF 的改进版，考虑了文档长度归一化
    class BM25Okapi:  # pragma: no cover - fallback only used when dependency is absent
        """
        BM25 算法的简化实现（备用）

        BM25 公式：
        score(D, Q) = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D|/avgdl))

        其中：
        - f(qi, D): 查询词 qi 在文档 D 中的词频
        - |D|: 文档 D 的长度
        - avgdl: 平均文档长度
        - k1: 词频饱和参数，通常为 1.2-2.0
        - b: 长度归一化参数，通常为 0.75
        - IDF(qi): 逆文档频率，log((N - n(qi) + 0.5) / (n(qi) + 0.5) + 1)
        """
        def __init__(self, corpus_tokens: list[list[str]]):
            """
            初始化 BM25 索引

            Args:
                corpus_tokens: 分词后的语料库，每个文档是一个词元列表
            """
            self.corpus_tokens = corpus_tokens
            # 每个文档的词频统计
            self.doc_freqs = [Counter(doc) for doc in corpus_tokens]
            # 每个文档的长度
            self.doc_lengths = [len(doc) for doc in corpus_tokens]
            # 平均文档长度，用于长度归一化
            self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
            # 文档频率：每个词出现在多少个文档中
            self.df: Counter[str] = Counter()
            for doc in corpus_tokens:
                for token in set(doc):
                    self.df[token] += 1
            # 语料库大小
            self.corpus_size = len(corpus_tokens)
            # BM25 参数
            self.k1 = 1.5  # 词频饱和参数
            self.b = 0.75  # 长度归一化参数

        def get_scores(self, query_tokens: list[str]) -> list[float]:
            """
            计算查询与所有文档的 BM25 相关性分数

            Args:
                query_tokens: 分词后的查询词元列表

            Returns:
                每个文档的 BM25 分数列表
            """
            scores: list[float] = []
            for index, freqs in enumerate(self.doc_freqs):
                doc_len = self.doc_lengths[index] or 1
                score = 0.0
                for token in query_tokens:
                    # 词频：查询词在文档中出现的次数
                    tf = freqs.get(token, 0)
                    if not tf:
                        continue
                    # 文档频率：包含该词的文档数量
                    df = self.df.get(token, 0)
                    # IDF 计算：使用 BM25 的 IDF 公式
                    # log((N - n + 0.5) / (n + 0.5) + 1)
                    idf = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)
                    # BM25 分数计算
                    # 分母考虑了文档长度归一化
                    denom = tf + self.k1 * (1 - self.b + self.b * doc_len / (self.avgdl or 1.0))
                    score += idf * (tf * (self.k1 + 1)) / denom
                scores.append(score)
            return scores


# ============================================================================
# 全局常量与缓存
# ============================================================================

# 分词正则：匹配英文单词/数字 或 中文字符
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")

# 图表引用正则：匹配"图1"、"表2-3"等格式
# 支持全角/半角数字、范围连字符
_REF_NUMBER_PATTERN = r"([0-9０-９]+(?:\s*[-－–—]\s*[0-9０-９]+){0,6})"
_MEDIA_REF_QUERY_PATTERN = re.compile(rf"(图|表)\s*{_REF_NUMBER_PATTERN}")

# 全角数字转半角数字的映射表
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# BM25 索引缓存：避免重复构建倒排索引
# 格式：{kb_id: (BM25索引, 文档列表)}
_bm25_cache: dict[str, tuple[BM25Okapi | None, list[dict[str, Any]]]] = {}

# 默认检索层级：使用子级和增强切片进行检索
# 父级切片通常用于提供上下文，不直接参与检索
_DEFAULT_RETRIEVAL_LAYERS = ("child", "enhanced")


# ============================================================================
# 辅助函数
# ============================================================================

def _snapshot_enabled() -> bool:
    """
    检查是否启用检索快照功能

    检索快照是预计算的检索缓存，可以加速高频查询。

    Returns:
        True 如果启用快照功能
    """
    value = os.environ.get("RAG_RETRIEVAL_SNAPSHOT", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _elapsed_ms(started_at: float) -> int:
    """
    计算经过的毫秒数

    Args:
        started_at: 起始时间戳（time.perf_counter()）

    Returns:
        经过的毫秒数
    """
    return int((time.perf_counter() - started_at) * 1000)


def _tokenize(text: str) -> list[str]:
    """
    分词函数：将文本分割为词元列表

    支持中英文混合文本：
    - 英文：按字母数字连续序列分割
    - 中文：每个字符作为一个词元

    Args:
        text: 待分词的文本

    Returns:
        词元列表

    示例:
        "Python编程很棒" -> ["python", "编", "程", "很", "棒"]
    """
    return _TOKEN_PATTERN.findall((text or "").lower())


def _normalize_media_ref(kind: str, number: str) -> str:
    """
    标准化图表引用编号

    将各种格式的图表引用统一为标准格式：
    - 全角数字转半角
    - 移除空格和连字符

    Args:
        kind: 引用类型（"图" 或 "表"）
        number: 引用编号字符串

    Returns:
        标准化后的引用字符串，如 "图1", "表23"

    示例:
        ("图", "１－２") -> "图12"
        ("表", " 3 - 5 ") -> "表35"
    """
    normalized_number = number.translate(_FULLWIDTH_DIGITS)
    normalized_number = re.sub(r"[\s\-－–—]", "", normalized_number)
    return f"{kind}{normalized_number}"


def _extract_media_ref_query(query: str) -> tuple[str, str] | None:
    """
    从查询中提取图表引用

    检测查询中是否包含图表引用（如"图1"、"表2-3"），
    如果存在则提取并标准化。

    Args:
        query: 用户查询文本

    Returns:
        如果找到引用，返回 (类型, 标准化引用) 元组；否则返回 None

    示例:
        "请看图１－２" -> ("图", "图12")
        "如表３所示" -> ("表", "表3")
        "这是一个问题" -> None
    """
    match = _MEDIA_REF_QUERY_PATTERN.search(query or "")
    if not match:
        return None
    kind = match.group(1)
    normalized = _normalize_media_ref(kind, match.group(2))
    if len(normalized) <= 1:
        return None
    return kind, normalized


def _vector_literal(values: list[float]) -> str:
    """
    将向量转换为 PostgreSQL 向量字面量字符串

    用于在 SQL 中使用 pgvector 的向量操作符。

    Args:
        values: 浮点数向量

    Returns:
        PostgreSQL 向量字面量，如 "[0.1,0.2,0.3]"

    示例:
        [0.1, 0.2, 0.3] -> "[0.10000000,0.20000000,0.30000000]"
    """
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _normalize_related_ids(value: Any) -> list[str]:
    """
    标准化 related_ids 字段

    将各种格式的 related_ids 统一为字符串列表：
    - None -> []
    - 列表 -> 过滤空值后的字符串列表
    - JSON 字符串 -> 解析后的字符串列表

    Args:
        value: 原始 related_ids 值

    Returns:
        标准化后的字符串列表
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


def _row_to_candidate(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    """
    将数据库查询结果行转换为检索候选对象

    将 SQL 查询的列名和行数据组合成结构化的候选字典，
    统一字段类型和默认值。

    Args:
        columns: 列名列表
        row: 行数据元组

    Returns:
        检索候选字典，包含：
        - id: 切片 ID
        - content: 切片内容
        - source: 来源文档
        - document_id: 文档 ID
        - document_name: 文档名称
        - page: 页码
        - layer: 切片层级
        - parent_id: 父级 ID
        - related_ids: 关联切片 ID 列表
        - score: 相关性分数
        - dense_score: 向量检索分数
        - 其他可选字段

    Note:
        这是所有检索路径的统一输出格式，便于后续处理和融合。
    """
    data = dict(zip(columns, row))
    candidate = {
        "id": str(data.get("id", "")),
        "content": data.get("content", "") or "",
        "source": data.get("source", "") or "",
        "document_id": str(data["document_id"]) if data.get("document_id") else "",
        "document_name": data.get("document_name", "") or data.get("filename", "") or data.get("source", "") or "",
        "page": int(data.get("page", 0) or 0),
        "layer": data.get("layer", "") or "",
        "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
        "related_ids": _normalize_related_ids(data.get("related_ids")),
        "score": float(data.get("score", 0.0) or 0.0),
        "dense_score": float(data.get("dense_score", data.get("score", 0.0)) or 0.0),
        "image_path": data.get("image_path", "") or None,
    }
    if "chunk_index" in data:
        candidate["chunk_index"] = int(data.get("chunk_index", 0) or 0)
    if "title" in data:
        candidate["title"] = data.get("title") or ""
    if "is_table_chunk" in data:
        candidate["is_table_chunk"] = bool(data.get("is_table_chunk", False))
    if "is_image_chunk" in data:
        candidate["is_image_chunk"] = bool(data.get("is_image_chunk", False))
    return candidate


# ============================================================================
# 核心检索函数
# ============================================================================

def _dense_retrieve(query_vec: list[float], kb_id: str, top_n: int = 50) -> list[dict[str, Any]]:
    """
    密集检索（向量检索）

    使用 pgvector 在向量空间中进行近似最近邻检索。
    通过计算查询向量与所有切片向量的余弦距离，
    返回距离最小的 top_n 个切片。

    ## 向量检索原理

    1. **向量化**：文本通过 embedding 模型转换为高维向量
    2. **相似度计算**：计算查询向量与数据库向量的距离
    3. **排序返回**：按距离从小到大排序，返回最近的切片

    ## pgvector 相似度计算

    PostgreSQL pgvector 使用余弦距离运算符 `<=>`：
    - 余弦距离 = 1 - 余弦相似度
    - 余弦相似度 = (a·b) / (||a|| * ||b||)
    - 距离范围 [0, 2]，越小越相似

    Args:
        query_vec: 查询向量
        kb_id: 知识库 ID
        top_n: 返回数量，默认 50

    Returns:
        检索候选列表，每个候选包含：
        - score: 相似度分数 (1 - 余弦距离)
        - dense_score: 与 score 相同，标记为向量检索分数
        - 其他切片元数据

    SQL 说明:
        使用 pgvector 的余弦距离运算符 <=>
        1 - (embedding <=> query_vec) 将距离转换为相似度分数
    """
    conn = get_db_connection()
    vector_literal = _vector_literal(query_vec)
    try:
        with conn.cursor() as cur:
            # 使用 pgvector 的余弦距离运算符 <=>
            # 1 - (embedding <=> query_vec) 将距离转换为相似度分数
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s AND c.layer = ANY(%s)
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (vector_literal, kb_id, list(_DEFAULT_RETRIEVAL_LAYERS), vector_literal, top_n),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    candidates = [_row_to_candidate(columns, row) for row in rows]
    # 标记向量检索分数
    for candidate in candidates:
        candidate["dense_score"] = candidate["score"]
    return candidates


def _build_bm25_index(kb_id: str) -> tuple[BM25Okapi | None, list[dict[str, Any]]]:
    """
    构建 BM25 倒排索引

    从数据库加载指定知识库的所有切片内容，
    构建用于 BM25 检索的倒排索引。

    BM25 索引包含：
    - 词频统计（每个词在每个文档中出现的次数）
    - 文档频率（每个词出现在多少个文档中）
    - 文档长度统计（用于长度归一化）

    Args:
        kb_id: 知识库 ID

    Returns:
        (BM25索引对象, 文档列表) 元组
        如果知识库为空，返回 (None, [])
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 加载所有 child 和 enhanced 层级的切片
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s AND c.layer = ANY(%s)
                ORDER BY c.chunk_index
                """,
                (kb_id, list(_DEFAULT_RETRIEVAL_LAYERS)),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    corpus_docs = [_row_to_candidate(columns, row) for row in rows]
    if not corpus_docs:
        return None, []

    # 对每个文档进行分词
    corpus_tokens = [_tokenize(doc["content"]) for doc in corpus_docs]
    return BM25Okapi(corpus_tokens), corpus_docs


def _sparse_retrieve(query: str, kb_id: str, top_n: int = 50) -> list[dict[str, Any]]:
    """
    稀疏检索（BM25 关键词检索）

    使用 BM25 算法进行关键词匹配检索。
    BM25 是 TF-IDF 的改进版本，考虑了文档长度归一化。

    ## BM25 相比 TF-IDF 的改进

    1. **词频饱和**：词频增长到一定程度后收益递减
    2. **长度归一化**：长文档不会因为词多就得分高
    3. **IDF 平滑**：避免罕见词权重过高

    ## 检索流程

    1. 检查缓存中是否已有 BM25 索引
    2. 如果没有，构建索引并缓存
    3. 对查询进行分词
    4. 计算每个文档的 BM25 分数
    5. 归一化分数到 [0, 1] 范围
    6. 返回 top_n 个最高分文档

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID
        top_n: 返回数量，默认 50

    Returns:
        检索候选列表，分数已归一化到 [0, 1]
    """
    # 从缓存获取或构建 BM25 索引
    index, corpus_docs = _bm25_cache.get(kb_id) or _build_bm25_index(kb_id)
    _bm25_cache[kb_id] = (index, corpus_docs)
    if not corpus_docs or index is None:
        return []

    # 计算 BM25 分数
    scores = [float(score) for score in index.get_scores(_tokenize(query))]
    if not scores:
        return []
    max_score = max(scores)
    if max_score <= 0:
        return []

    # 按分数降序排序，取 top_n
    ranked_indices = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:top_n]
    results: list[dict[str, Any]] = []
    for idx in ranked_indices:
        # 归一化分数到 [0, 1]
        score = scores[idx] / max_score
        if score <= 0:
            continue
        candidate = dict(corpus_docs[idx])
        candidate["score"] = float(score)
        candidate["dense_score"] = 0.0  # 标记为非向量检索
        results.append(candidate)
    return results


def _structured_retrieve(
    query: str,
    kb_id: str,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    结构化检索（元数据过滤）

    根据元数据条件（如来源、标题、页码等）精确筛选切片。
    不依赖文本相似度，而是基于结构化字段匹配。

    ## 支持的过滤条件

    - source: 文档来源（匹配 source 字段或 filename）
    - title_like: 标题模糊匹配（LIKE 查询）
    - is_table: 是否为表格切片
    - page_range: 页码范围 [start, end]

    ## 适用场景

    - 用户明确知道要找哪个文档的内容
    - 筛选特定类型的切片（如表格）
    - 定位特定页码范围的内容

    Args:
        query: 用户查询文本（当前未使用）
        kb_id: 知识库 ID
        filters: 过滤条件字典

    Returns:
        检索候选列表，所有候选分数为 1.0（精确匹配）
    """
    del query  # 结构化检索不使用文本查询
    if not filters:
        return []

    # 动态构建 WHERE 子句
    where_clauses = ["c.kb_id = %s"]
    params: list[Any] = [kb_id]
    if filters.get("source"):
        where_clauses.append("(c.source = %s OR d.filename = %s)")
        params.append(filters["source"])
        params.append(filters["source"])
    if filters.get("title_like"):
        where_clauses.append("c.title LIKE %s")
        params.append(f"%{filters['title_like']}%")
    if filters.get("is_table"):
        where_clauses.append("c.is_table_chunk = TRUE")
    if filters.get("page_range") and len(filters["page_range"]) == 2:
        start_page, end_page = filters["page_range"]
        where_clauses.append("c.page BETWEEN %s AND %s")
        params.extend([start_page, end_page])

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE {" AND ".join(where_clauses)}
                  AND c.layer = ANY(%s)
                ORDER BY c.page, c.chunk_index
                """,
                tuple([*params, list(_DEFAULT_RETRIEVAL_LAYERS)]),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    # 所有精确匹配的候选分数为 1.0
    results = [_row_to_candidate(columns, row) for row in rows]
    for candidate in results:
        candidate["score"] = 1.0
        candidate["dense_score"] = 0.0
    return results


def _media_ref_retrieve(query: str, kb_id: str, top_n: int = 20) -> list[dict[str, Any]]:
    """
    媒体引用检索（图表定位）

    识别查询中的图表引用（如"图1"、"表2-3"），
    直接定位并返回对应的图表切片。

    ## 工作原理

    1. 使用正则表达式提取查询中的图表引用
    2. 根据引用类型（图/表）筛选对应切片
    3. 在标题和内容中匹配引用编号
    4. 按匹配度排序返回

    ## 适用场景

    - 用户明确引用了文档中的图表
    - 需要快速定位特定图表
    - 跳过文本相似度计算，直接返回图表

    ## 匹配优先级

    1. 标题完全匹配（优先级最高）
    2. 标题部分匹配
    3. 内容匹配

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID
        top_n: 返回数量，默认 20

    Returns:
        检索候选列表，标记为 media_ref 检索模式
        如果查询中没有图表引用，返回空列表
    """
    media_ref = _extract_media_ref_query(query)
    if media_ref is None:
        return []

    kind, normalized_ref = media_ref
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 根据"图"或"表"筛选对应类型的切片
            # 在标题和内容中匹配引用编号
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.kb_id = %s
                  AND c.layer = 'child'
                  AND (
                    (%s = '图' AND c.is_image_chunk = TRUE)
                    OR (%s = '表' AND c.is_table_chunk = TRUE)
                  )
                  AND regexp_replace(
                    COALESCE(c.title, '') || ' ' || COALESCE(c.content, ''),
                    '[[:space:]\\-－–—]',
                    '',
                    'g'
                  ) ILIKE %s
                ORDER BY
                  CASE
                    WHEN regexp_replace(COALESCE(c.title, ''), '[[:space:]\\-－–—]', '', 'g') ILIKE %s THEN 0
                    ELSE 1
                  END,
                  c.page,
                  c.chunk_index
                LIMIT %s
                """,
                (kb_id, kind, kind, f"%{normalized_ref}%", f"%{normalized_ref}%", top_n),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    results = [_row_to_candidate(columns, row) for row in rows]
    # 标记为媒体引用检索
    for index, candidate in enumerate(results):
        candidate["score"] = max(1.0 - index * 0.02, 0.7)  # 按排序位置降权
        candidate["dense_score"] = 0.0
        candidate["rerank_score"] = candidate["score"]
        candidate["sources"] = ["media_ref"]
        candidate["matched_by"] = ["media_ref"]
        candidate["retrieval_mode"] = "media_ref"
        candidate["matched_media_ref"] = normalized_ref
    return results


def _fetch_chunks_by_ids(chunk_ids: list[str], kb_id: str) -> list[dict[str, Any]]:
    """
    根据 ID 列表批量获取切片

    用于相关切片扩展和实体检索中获取关联切片。

    Args:
        chunk_ids: 切片 ID 列表
        kb_id: 知识库 ID

    Returns:
        切片候选列表
    """
    if not chunk_ids:
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.title,
                       c.is_table_chunk, c.is_image_chunk, c.image_path, c.chunk_index
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.id::text = ANY(%s) AND c.kb_id = %s
                """,
                (chunk_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    return [_row_to_candidate(columns, row) for row in rows]


def _entity_match_score(query: str, name: str, aliases: list[str], emb_score: float) -> float:
    """
    计算实体匹配分数

    结合精确匹配、子串匹配、词元重叠和向量相似度，
    计算查询与实体的综合匹配分数。

    ## 匹配策略

    1. **精确匹配**：查询与实体名称完全一致 -> 1.0
    2. **包含匹配**：实体名称包含查询或反之 -> 0.92
    3. **词元重叠**：查询与实体共享词元 -> 0.4 + 0.4 * overlap
    4. **向量相似度**：embedding 相似度作为保底 -> emb_score * 0.85

    Args:
        query: 用户查询文本
        name: 实体名称
        aliases: 实体别名列表
        emb_score: 向量相似度分数

    Returns:
        综合匹配分数，范围 [0, 1]
    """
    normalized_query = query.strip().lower()
    query_tokens = set(_tokenize(query))
    best = 0.0

    # 检查实体名称和所有别名
    for candidate in [name, *aliases]:
        normalized = candidate.strip().lower()
        if not normalized:
            continue
        # 精确匹配
        if normalized == normalized_query:
            best = max(best, 1.0)
            continue
        # 包含匹配
        if normalized in normalized_query or normalized_query in normalized:
            best = max(best, 0.92)
            continue

        # 词元重叠匹配
        candidate_tokens = set(_tokenize(candidate))
        if query_tokens and candidate_tokens:
            overlap = len(query_tokens & candidate_tokens) / max(len(candidate_tokens), 1)
            if overlap > 0:
                best = max(best, 0.4 + 0.4 * overlap)

    # 保底使用向量相似度
    return max(best, emb_score * 0.85)


def _entity_retrieve(
    query: str,
    kb_id: str,
    query_vec: list[float] | None = None,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    实体检索

    基于预定义的实体知识库进行检索。
    实体可以是术语、概念、人名、地名等，并可以有别名。

    ## 工作流程

    1. 从数据库查询实体，使用向量相似度排序（如果有查询向量）
    2. 对每个实体计算综合匹配分数（精确匹配 + 向量相似度）
    3. 筛选分数 >= 0.35 的实体
    4. 查询实体关联的切片（通过 entity_mentions 表）
    5. 返回关联切片作为检索结果

    ## 适用场景

    - 用户查询包含专业术语或概念
    - 需要基于知识图谱增强检索
    - 处理同义词和别名

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID
        query_vec: 查询向量（可选，用于向量相似度排序）
        top_n: 返回数量，默认 20

    Returns:
        检索候选列表，每个候选包含关联的实体信息
    """
    if not query.strip():
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 查询实体，优先使用向量相似度排序
            if query_vec:
                vector_literal = _vector_literal(query_vec)
                cur.execute(
                    """
                    SELECT id::text, name, aliases, type, definition,
                           1 - (embedding <=> %s::vector) AS emb_score
                    FROM entities
                    WHERE kb_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector_literal, kb_id, vector_literal, max(top_n * 2, 20)),
                )
            else:
                cur.execute(
                    """
                    SELECT id::text, name, aliases, type, definition, 0.0 AS emb_score
                    FROM entities
                    WHERE kb_id = %s
                    LIMIT %s
                    """,
                    (kb_id, max(top_n * 2, 20)),
                )
            entity_rows = cur.fetchall()

            # 计算实体的综合匹配分数
            ranked_entities: list[tuple[str, float, dict[str, Any]]] = []
            for entity_id, name, aliases, entity_type, definition, emb_score in entity_rows:
                alias_list = aliases if isinstance(aliases, list) else []
                score = _entity_match_score(query, name, alias_list, float(emb_score or 0.0))
                if score < 0.35:  # 过滤低分实体
                    continue
                ranked_entities.append(
                    (
                        entity_id,
                        score,
                        {
                            "id": entity_id,
                            "name": name,
                            "aliases": alias_list,
                            "type": entity_type,
                            "definition": definition or name,
                        },
                    )
                )

            # 按分数排序，取 top_n
            ranked_entities.sort(key=lambda item: item[1], reverse=True)
            selected = ranked_entities[:top_n]
            if not selected:
                return []

            # 查询实体关联的切片
            entity_ids = [entity_id for entity_id, _score, _entity in selected]
            cur.execute(
                """
                SELECT entity_id::text, chunk_id::text
                FROM entity_mentions
                WHERE kb_id = %s AND entity_id::text = ANY(%s)
                """,
                (kb_id, entity_ids),
            )
            mention_rows = cur.fetchall()
    finally:
        conn.close()

    # 建立 实体ID -> 实体信息 的映射
    entity_meta = {entity_id: entity for entity_id, _score, entity in selected}
    entity_scores = {entity_id: score for entity_id, score, _entity in selected}

    # 建立 切片ID -> (最高分实体, 实体信息) 的映射
    chunk_to_entity: dict[str, tuple[float, dict[str, Any]]] = {}
    for entity_id, chunk_id in mention_rows:
        score = entity_scores.get(entity_id, 0.0)
        entity = entity_meta.get(entity_id)
        if not entity:
            continue
        current = chunk_to_entity.get(chunk_id)
        # 如果切片关联多个实体，保留分数最高的
        if current is None or score > current[0]:
            chunk_to_entity[chunk_id] = (score, entity)

    # 获取切片详情
    chunks = _fetch_chunks_by_ids(list(chunk_to_entity.keys()), kb_id)
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        score, entity = chunk_to_entity.get(chunk["id"], (0.0, None))
        if entity is None:
            continue
        candidate = dict(chunk)
        candidate["score"] = score
        candidate["dense_score"] = 0.0
        candidate["entity"] = entity  # 附加实体信息
        results.append(candidate)
    return sorted(results, key=lambda item: item.get("score", 0.0), reverse=True)[:top_n]


# ============================================================================
# 融合与后处理函数
# ============================================================================

def _rrf_merge(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    structured: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """
    RRF（Reciprocal Rank Fusion）融合算法

    将多个召回通道的结果融合为一个统一的排序列表。
    RRF 是一种简单高效的排序融合方法，不需要训练。

    ## RRF 公式

    RRF_score(d) = Σ (1 / (k + rank_i(d)))

    其中：
    - d: 文档
    - rank_i(d): 文档 d 在第 i 个通道中的排名
    - k: 平滑参数，通常为 60

    ## 工作原理

    1. 对每个召回通道，按排名计算每个文档的贡献分数
    2. 排名越靠前，贡献分数越高
    3. 将所有通道的分数相加得到总分
    4. 按总分降序排列

    ## 优点

    - 不需要分数归一化
    - 对异常值不敏感
    - 计算简单高效

    Args:
        dense: 密集检索结果列表
        sparse: 稀疏检索结果列表
        structured: 结构化检索结果列表
        k: RRF 平滑参数，默认 60

    Returns:
        融合后的候选列表，按 RRF 分数降序排列
    """
    merged: dict[str, dict[str, Any]] = {}
    channels = (
        ("embedding", dense),
        ("bm25", sparse),
        ("entity", structured),
    )

    for channel, candidates in channels:
        for rank, candidate in enumerate(candidates, start=1):
            # 使用 setdefault 确保每个文档只出现一次
            entry = merged.setdefault(candidate["id"], dict(candidate))
            # 累加 RRF 分数
            entry["score"] = float(entry.get("score", 0.0)) + 1.0 / (k + rank)
            # 保留最大的向量检索分数
            entry["dense_score"] = max(
                float(entry.get("dense_score", 0.0) or 0.0),
                float(candidate.get("dense_score", 0.0) or 0.0),
            )
            # 记录召回来源
            sources = entry.setdefault("sources", [])
            if channel not in sources:
                sources.append(channel)

    return sorted(merged.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def _score_for_folded_child(candidate: dict[str, Any]) -> float:
    """
    计算折叠后的子切片分数

    当增强切片命中时，需要折叠回对应的子切片。
    这个函数计算折叠后的分数，对增强切片进行适当的降权。

    ## 降权策略

    - 非增强切片：保持原分数
    - 增强切片（图表描述/摘要）：× 0.95
    - 增强切片（其他）：× 0.85

    Args:
        candidate: 候选字典

    Returns:
        折叠后的分数
    """
    score = float(candidate.get("score", 0.0) or 0.0)
    if candidate.get("layer") != "enhanced":
        return score
    content = candidate.get("content", "") or ""
    # 图表描述和表格摘要降权较少
    if "[图片描述]" in content or "[表格摘要]" in content:
        return score * 0.95
    return score * 0.85


def _fold_enhanced_to_children(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    """
    将增强切片折叠回子切片

    增强切片是由 LLM 生成的摘要，用于语义匹配。
    检索时使用增强切片进行匹配，但返回给用户的是原始子切片。

    ## 工作原理

    1. 识别所有增强切片候选
    2. 根据 parent_id 获取对应的子切片
    3. 将增强切片的分数和匹配信息转移给子切片
    4. 记录 matched_enhanced_id 和 matched_enhanced_text

    ## 为什么折叠

    - 增强切片用于提高召回质量（语义更清晰）
    - 子切片用于提供原始证据（内容更可信）
    - 折叠后用户看到的是原始内容，但享受增强召回的好处

    Args:
        candidates: 检索候选列表
        kb_id: 知识库 ID

    Returns:
        折叠后的候选列表
    """
    if not candidates:
        return []

    # 收集需要获取的子切片 ID
    child_ids = [
        str(candidate["parent_id"])
        for candidate in candidates
        if candidate.get("layer") == "enhanced" and candidate.get("parent_id")
    ]
    # 批量获取子切片
    child_by_id = {child["id"]: child for child in _fetch_chunks_by_ids(sorted(set(child_ids)), kb_id)}

    folded: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        current = dict(candidate)
        folded_score = _score_for_folded_child(candidate)
        matched_by = list(current.get("matched_by", []))

        # 如果是增强切片，折叠到子切片
        if candidate.get("layer") == "enhanced" and candidate.get("parent_id"):
            child = child_by_id.get(str(candidate["parent_id"]))
            if not child:
                continue
            current = dict(child)
            current["score"] = folded_score
            current["dense_score"] = float(candidate.get("dense_score", 0.0) or 0.0)
            # 记录匹配的增强切片信息
            current["matched_enhanced_id"] = candidate.get("id", "")
            current["matched_enhanced_text"] = candidate.get("content", "")
            matched_by.append("enhanced")
        else:
            current["score"] = folded_score
            matched_by.append(str(candidate.get("layer") or "chunk"))

        current["matched_by"] = sorted(set(item for item in matched_by if item))

        # 去重：同一子切片可能被多个增强切片匹配，保留最高分
        existing = folded.get(current["id"])
        if not existing or float(current.get("score", 0.0)) > float(existing.get("score", 0.0)):
            folded[current["id"]] = current
            continue
        # 合并匹配信息
        existing["score"] = max(float(existing.get("score", 0.0)), float(current.get("score", 0.0)))
        existing["dense_score"] = max(float(existing.get("dense_score", 0.0)), float(current.get("dense_score", 0.0)))
        existing["matched_by"] = sorted(set(existing.get("matched_by", []) + current.get("matched_by", [])))

    return sorted(folded.values(), key=lambda item: item.get("score", 0.0), reverse=True)


def _expand_related(candidates: list[dict[str, Any]], kb_id: str) -> list[dict[str, Any]]:
    """
    扩展相关切片

    利用切片的 related_ids 字段，将关联的切片补充到结果中。
    通常用于将图表切片与文本切片关联。

    ## 工作原理

    1. 从候选切片中提取 related_ids
    2. 去重后批量查询关联切片
    3. 将关联切片追加到结果列表
    4. 关联切片的分数设为固定值 0.3

    ## 适用场景

    - 主召回命中文本切片时，补充关联的图表
    - 提供更完整的上下文信息
    - 支持"图X如上所述"等引用关系

    Args:
        candidates: 检索候选列表
        kb_id: 知识库 ID

    Returns:
        扩展后的候选列表（包含原始候选和相关切片）
    """
    if not candidates:
        return []

    existing_ids = {candidate["id"] for candidate in candidates}
    related_ids: list[str] = []
    for candidate in candidates:
        for related_id in candidate.get("related_ids", []):
            if related_id and related_id not in existing_ids and related_id not in related_ids:
                related_ids.append(related_id)
    if not related_ids:
        return candidates

    # 批量查询关联切片
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.content, c.source, c.document_id, d.filename AS document_name,
                       c.page, c.layer, c.parent_id, c.related_ids, c.chunk_index,
                       c.title, c.is_table_chunk, c.is_image_chunk, c.image_path
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.id::text = ANY(%s) AND c.kb_id = %s
                """,
                (related_ids, kb_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    # 追加关联切片
    expanded = list(candidates)
    for row in rows:
        candidate = _row_to_candidate(columns, row)
        if candidate["id"] in existing_ids:
            continue
        candidate["score"] = 0.3  # 相关切片固定分数
        candidate["dense_score"] = 0.0
        expanded.append(candidate)
        existing_ids.add(candidate["id"])
    return expanded


def _coarse_filter(
    candidates: list[dict[str, Any]],
    min_score: float = 0.3,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    粗过滤：筛选低分候选并限制数量

    对最终结果进行简单的阈值过滤和数量限制。

    Args:
        candidates: 候选列表
        min_score: 最低分数阈值，默认 0.3
        top_n: 返回数量，默认 20

    Returns:
        过滤后的候选列表
    """
    filtered = [candidate for candidate in candidates if float(candidate.get("score", 0.0)) >= min_score]
    return sorted(filtered, key=lambda item: item.get("score", 0.0), reverse=True)[:top_n]


# ============================================================================
# 混合检索器类
# ============================================================================

class HybridRetriever:
    """
    混合检索器

    整合多种检索策略，提供统一的检索接口。

    ## 检索流程

    1. **媒体引用检测**：如果查询包含图表引用，直接返回图表切片
    2. **查询向量化**：将查询文本转换为向量
    3. **快照检查**：如果启用快照且命中缓存，直接返回缓存结果
    4. **多路召回**：
       - 密集检索（向量）
       - 稀疏检索（BM25）
       - 结构化检索（过滤）
    5. **RRF 融合**：合并多路结果
    6. **层级折叠**：将增强切片折叠回子切片
    7. **相关扩展**：补充关联切片
    8. **粗过滤**：筛选低分结果

    ## 性能监控

    每次检索都会记录各阶段耗时到 `last_timings` 字典中，
    用于性能分析和优化。

    Attributes:
        last_timings: 最近一次检索的耗时统计
    """

    def __init__(self) -> None:
        """初始化混合检索器"""
        self.last_timings: dict[str, int | bool] = {}

    def retrieve(
        self,
        query: str,
        kb_id: str,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        """
        执行混合检索

        Args:
            query: 用户查询文本
            kb_id: 知识库 ID
            top_k: 返回数量，默认 20
            filters: 过滤条件（可选）
            min_score: 最低分数阈值，默认 0.3

        Returns:
            检索结果列表，每个结果包含：
            - id: 切片 ID
            - content: 切片内容
            - score: 相关性分数
            - sources: 召回来源列表
            - matched_by: 匹配方式列表
            - 其他元数据字段
        """
        # 初始化耗时统计
        timings: dict[str, int | bool] = {"short_circuit": False}
        started_at = time.perf_counter()

        # 1. 媒体引用检测（快速路径）
        media_ref_started_at = time.perf_counter()
        media_ref_hits = _media_ref_retrieve(query, kb_id, top_n=top_k)
        timings["media_ref"] = _elapsed_ms(media_ref_started_at)
        if media_ref_hits:
            # 媒体引用命中，直接返回，跳过其他检索
            related_started_at = time.perf_counter()
            expanded = _expand_related(media_ref_hits, kb_id)
            timings["related"] = _elapsed_ms(related_started_at)
            filter_started_at = time.perf_counter()
            result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
            timings["filter"] = _elapsed_ms(filter_started_at)
            timings["total"] = _elapsed_ms(started_at)
            timings["short_circuit"] = True
            self.last_timings = timings
            return result

        # 2. 查询向量化
        embedding_started_at = time.perf_counter()
        query_vec, query_embedding_cache_hit = embed_query_cached(query)
        timings["embedding"] = _elapsed_ms(embedding_started_at)
        timings["query_embedding_cache_hit"] = query_embedding_cache_hit

        # 3. 检查检索快照（可选优化路径）
        if _snapshot_enabled() and not filters:
            try:
                snapshot_started_at = time.perf_counter()
                snapshot_candidates, snapshot_by_id = fetch_retrieval_snapshot(
                    query=query,
                    query_vec=query_vec,
                    kb_id=kb_id,
                    dense_limit=50,
                    sparse_limit=50,
                    related_limit=200,
                )
                timings["snapshot"] = _elapsed_ms(snapshot_started_at)
                timings["dense"] = 0
                timings["sparse"] = 0
                timings["structured"] = 0
                timings["fusion"] = 0
                fold_started_at = time.perf_counter()
                folded = fold_enhanced_snapshot(snapshot_candidates, snapshot_by_id)
                timings["fold"] = _elapsed_ms(fold_started_at)
                related_started_at = time.perf_counter()
                expanded = expand_related_snapshot(folded, snapshot_by_id)
                timings["related"] = _elapsed_ms(related_started_at)
                filter_started_at = time.perf_counter()
                result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
                timings["filter"] = _elapsed_ms(filter_started_at)
                timings["total"] = _elapsed_ms(started_at)
                self.last_timings = timings
                return result
            except Exception as exc:
                # 快照失败，回退到标准检索流程
                timings["snapshot_error"] = True
                timings["snapshot"] = int(timings.get("snapshot", 0) or 0)
                timings["snapshot_fallback"] = True

        # 4. 多路召回
        # 4.1 密集检索（向量）
        dense_started_at = time.perf_counter()
        dense = _dense_retrieve(query_vec, kb_id, 50)
        timings["dense"] = _elapsed_ms(dense_started_at)

        # 4.2 稀疏检索（BM25）
        sparse_started_at = time.perf_counter()
        sparse = _sparse_retrieve(query, kb_id, 50)
        timings["sparse"] = _elapsed_ms(sparse_started_at)

        # 4.3 结构化检索（过滤）
        structured_started_at = time.perf_counter()
        structured = _structured_retrieve(query, kb_id, filters)
        timings["structured"] = _elapsed_ms(structured_started_at)

        # 5. RRF 融合
        fusion_started_at = time.perf_counter()
        merged = _rrf_merge(dense, sparse, structured)
        timings["fusion"] = _elapsed_ms(fusion_started_at)

        # 6. 层级折叠
        fold_started_at = time.perf_counter()
        folded = _fold_enhanced_to_children(merged, kb_id)
        timings["fold"] = _elapsed_ms(fold_started_at)

        # 7. 相关扩展
        related_started_at = time.perf_counter()
        expanded = _expand_related(folded, kb_id)
        timings["related"] = _elapsed_ms(related_started_at)

        # 8. 粗过滤
        filter_started_at = time.perf_counter()
        result = _coarse_filter(expanded, min_score=min_score, top_n=top_k)
        timings["filter"] = _elapsed_ms(filter_started_at)

        timings["total"] = _elapsed_ms(started_at)
        self.last_timings = timings
        return result
