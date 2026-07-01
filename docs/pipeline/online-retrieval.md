# 在线召回

## 术语规范

当前项目推荐使用以下术语：

1. `离线入库链路`
2. `在线检索增强问答链路`
3. `在线召回`

三者关系如下：

```text
RAG 系统
  -> 离线入库链路
  -> 在线检索增强问答链路
       -> 在线召回
       -> 融合 / 重排 / 图扩展
       -> 上下文构造
       -> 生成
       -> 引用对齐
       -> 评分
```

## 在线召回的定义

`在线召回` 指用户发起查询后，系统从知识库中快速取回候选切片的阶段。

在当前代码里，它不是整条问答链路，而是其中一个子阶段。

完整在线问答还会继续执行重排、上下文构造、答案生成、引用对齐和评分。召回阶段只负责把“可能相关的候选证据”尽快取出来。

## 当前实现位置

- `core/rag/retriever.py`
- `core/rag/graph_retriever.py`
- `backend/services/rag_service.py`
- `backend/adapters/rag_adapter.py`

## 普通 RAG 召回链路

普通问答入口是 `POST /api/rag/query`，后端调用顺序如下：

```text
backend/services/rag_service.py
  -> backend/adapters/rag_adapter.py
  -> HybridRetriever.retrieve()
  -> ParentChildReranker.rerank()
  -> RAGGenerator.generate()
  -> RAGScorer.score()
```

其中在线召回发生在 `HybridRetriever.retrieve()` 内部。

```text
query
  -> media_ref 直达判断
  -> query embedding
  -> dense recall
  -> sparse recall
  -> structured recall
  -> RRF merge
  -> enhanced 折回 child
  -> related_ids 扩展
  -> coarse filter
```

### 1. 图表编号直达召回：media_ref

当查询中出现教材图表编号时，普通 RAG 会优先走 `media_ref` 短路。

识别形态包括：

- `图1-3-3`
- `图 1-3-3`
- `图１－３－３`
- `表1-3-1`

技术细节：

- 入口函数：`_media_ref_retrieve()`
- 编号抽取：`_extract_media_ref_query()`
- 归一化：全角数字转半角，去掉空格和横线，只保留 `图/表 + 数字序列`
- 查询目标：只查 `chunks.layer = 'child'`
- 图片查询：要求 `chunks.is_image_chunk = TRUE`
- 表格查询：要求 `chunks.is_table_chunk = TRUE`
- 匹配字段：`chunks.title + chunks.content`
- 排序规则：标题命中优先，然后按页码和切片序号排序

命中后会跳过：

- query embedding
- BM25 构建
- RRF 融合
- rerank 外部 API
- LLM 答案生成

该规则用于“定位某个图/表”的导航型问题，目标是把类似“检索 图1-3-3”的耗时从分钟级降到数据库查询级。命中后由 `rag_adapter._build_media_ref_answer()` 直接生成定位答案、引用和候选。

### 2. 稠密召回：dense / pgvector

稠密召回用于语义相似匹配。

技术细节：

- 入口函数：`_dense_retrieve()`
- 查询向量：`embed_texts([query])[0]`
- 数据表：`chunks`
- 向量字段：`chunks.embedding`
- 距离算子：pgvector `<=>`
- 分数：`1 - (c.embedding <=> query_vec)`
- 默认层级：`child + enhanced`
- 默认数量：普通 RAG 内部先取 `top_n = 50`

SQL 逻辑核心：

```sql
SELECT ..., 1 - (c.embedding <=> %s::vector) AS score
FROM chunks c
WHERE c.kb_id = %s
  AND c.layer = ANY(%s)
ORDER BY c.embedding <=> %s::vector
LIMIT %s
```

召回结果会设置：

- `score = 向量相似分`
- `dense_score = score`
- `sources` 会在 RRF 阶段追加 `embedding`

### 3. 稀疏召回：sparse / BM25

稀疏召回用于关键词、编号、术语表述接近的匹配。

技术细节：

- 入口函数：`_sparse_retrieve()`
- 索引构建：`_build_bm25_index()`
- 缓存：`_bm25_cache[kb_id]`
- tokenizer：`_TOKEN_PATTERN = [A-Za-z0-9_]+|中文单字`
- 默认层级：`child + enhanced`
- 默认数量：普通 RAG 内部先取 `top_n = 50`

BM25 原始分会按当前知识库本次结果的最大值归一化：

```text
normalized_score = bm25_score / max_score
```

召回结果会设置：

- `score = normalized_score`
- `dense_score = 0.0`
- `sources` 会在 RRF 阶段追加 `bm25`

注意：如果知识库没有切片、BM25 全部为 0，或依赖不可用，稀疏召回会返回空数组。当前代码已有空库和 numpy-like scores 的回归保护。

### 4. 结构化召回：structured filters

普通 RAG 中的结构化召回不是 Graph RAG 的实体召回。它只根据明确过滤条件查 `chunks`。

技术细节：

- 入口函数：`_structured_retrieve()`
- 触发条件：调用方传入 `filters`
- 支持过滤：
  - `source`
  - `title_like`
  - `is_table`
  - `page_range`
- 默认层级：`child + enhanced`
- 排序：`page, chunk_index`

命中结果会设置：

- `score = 1.0`
- `dense_score = 0.0`

当前 `/api/rag/query` 的常规请求没有把 filters 传入 `HybridRetriever.retrieve()`，所以控制台里“结构化”计数通常是 `0`。这是当前实现口径，不代表系统没有结构化数据。

### 5. RRF 融合

RRF 是融合算法，不是独立的数据源。它把多个召回通道的排序结果合并成一个候选集合。

技术细节：

- 入口函数：`_rrf_merge()`
- 默认参数：`k = 60`
- 当前普通 RAG 融合输入：
  - `embedding`
  - `bm25`
  - `entity`，这里实际承载 structured 结果

融合公式：

```text
candidate.score += 1 / (k + rank)
```

同一个 `chunk.id` 被多个通道命中时会合并为一条：

- `score` 累加 RRF 分
- `dense_score` 取各通道最大值
- `sources` 记录命中过的通道名

RRF 之后的分数已经不是原始向量分或 BM25 分，而是排序融合分。

### 6. enhanced 折回 child

当前三层切片口径是：召回可以检索 `child + enhanced`，但最终证据优先回到 `child`。

技术细节：

- 入口函数：`_fold_enhanced_to_children()`
- 如果命中 `enhanced` 且存在 `parent_id`，会查回对应 child
- 最终候选使用 child 的正文、页码、图片路径和文档信息
- 同时保留：
  - `matched_enhanced_id`
  - `matched_enhanced_text`
  - `matched_by`

折回分数规则：

- 普通 enhanced：乘以 `0.85`
- 包含图片描述或表格摘要的 enhanced：乘以 `0.95`
- child 原生命中：保持原分

这样做的目的：让 enhanced 负责提高可召回性，但答案引用仍落在原始教材切片上。

### 7. 关联扩展：related_ids

普通 RAG 的关联扩展来自 `chunks.related_ids` 字段，不是 `chunk_relations` 图扩展。

技术细节：

- 入口函数：`_expand_related()`
- 遍历当前候选的 `related_ids`
- 查回尚未出现过的关联 chunk
- 给扩展候选固定分：

```text
score = 0.3
dense_score = 0.0
```

关联扩展常用于补回相邻、引用或由入库阶段写入的轻量关系候选。扩展结果仍会经过粗过滤和 top_k 截断。

### 8. 粗过滤：coarse filter

召回最终会进入 `_coarse_filter()`。

技术细节：

- 默认 `min_score = 0.3`
- 按 `score` 倒序
- 截断到 `top_n`

普通 RAG 在 `run_rag_pipeline()` 中会先请求 `max(top_k * 2, top_k)` 个候选，随后再交给重排器选出最终 top_k。

## 控制台召回通道计数口径

当前 `/api/rag/query` 返回的 `recallChannels` 在 `backend/services/rag_service.py` 中计算：

```python
recall_channels = [
    {"channel": "dense", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) > 0)},
    {"channel": "sparse", "count": sum(1 for c in candidates if c.get("dense_score", 0.0) == 0)},
    {"channel": "structured", "count": 0},
    {"channel": "rrf", "count": len(candidates)},
    {"channel": "related", "count": sum(1 for c in candidates if c.get("score", 0.0) <= 0.3)},
]
```

因此控制台显示需要按以下口径理解：

| 通道 | 当前计数含义 | 注意事项 |
| --- | --- | --- |
| 稠密 | 最终候选中 `dense_score > 0` 的数量 | 通常表示被向量通道命中过 |
| 稀疏 | 最终候选中 `dense_score == 0` 的数量 | 不等于纯 BM25，可能包含 structured 或 related |
| 结构化 | 当前固定为 `0` | 常规接口暂未传 filters |
| RRF | 召回器返回给重排前的候选总数 | 是融合后总数，不是独立召回源 |
| 关联 | 最终候选中 `score <= 0.3` 的数量 | 近似表示 related 扩展项，受 min_score/top_k 影响 |

示例：

```text
稠密      6
稀疏      10
结构化    0
RRF       16
关联      0
```

这个结果表示：当前返回给重排前的候选总数是 16，其中 6 个有向量命中痕迹，10 个没有向量分。结构化过滤没有启用，最终结果里也没有低分 related 扩展项。它不表示系统实际只执行了 16 次 RRF，也不表示 BM25 原始命中一定只有 10 条。

## Graph RAG 召回链路

Graph RAG 入口是 `POST /api/rag/graph-query`，后端调用 `GraphRetriever.retrieve()`。

```text
query
  -> intent router
  -> query embedding
  -> dense recall
  -> sparse recall
  -> entity recall
  -> RRF merge
  -> related_ids expand
  -> seed coarse filter
  -> graph_expand
  -> hydrate chunks/entities
  -> build structured context
```

### 1. 意图识别

Graph RAG 会先识别问题意图。

- 入口：`classify_intent()`
- 支持外部传入 `intent` override
- 常见意图：
  - `concept`
  - `procedure`
  - `data`
  - `visual`
  - `general`

不同意图会影响后续图扩展时的关系权重。

### 2. Graph RAG 的实体召回

Graph RAG 会使用真正的实体召回。

技术细节：

- 入口函数：`_entity_retrieve()`
- 数据表：
  - `entities`
  - `entity_mentions`
- 匹配依据：
  - 实体名
  - aliases
  - token overlap
  - 可选 entity embedding
- 默认阈值：`score >= 0.35`

命中实体后，会通过 `entity_mentions` 找到提到该实体的 chunk，再把 chunk 作为候选返回。

当意图是 `data` 时，当前代码会把 entity embedding 检索关闭，只做名称和别名匹配：

```python
_entity_retrieve(query, kb_id, query_vec if resolved_intent != "data" else None, 20)
```

### 3. Graph RAG 的图扩展

Graph RAG 的图扩展来自 `chunk_relations` 和 `entity_mentions`，不是普通 RAG 的 `related_ids`。

技术细节：

- 入口函数：`graph_expand()`
- 默认最大跳数：`max_hops = 2`
- 默认最大邻居数：`max_neighbors = 50`
- 关系表：`chunk_relations`
- 实体回跳表：`entity_mentions`

扩展分数：

```text
score = path_weight * relation_weight * hop_decay * intent_relation_priority
```

当前实现里 hop 衰减为：

```text
0.6 ** hop
```

不同意图下关系优先级不同，例如：

| intent | 高优先关系 |
| --- | --- |
| concept | `mentions`、`sibling`、`semantic_similar` |
| procedure | `next_step`、`prev_step`、`sibling` |
| data | `refers_to`、`sibling` |
| visual | `refers_to`、`adjacent` |
| general | `adjacent`、`sibling`、`semantic_similar` |

如果遇到 `mentions` 关系，扩展器会继续通过 `entity_mentions` 找到提到该实体的 chunk，并把这条路径记录到 explain path。

### 4. Graph RAG 返回统计

Graph RAG 返回 `stats`，包含更接近内部真实阶段的统计：

- `recall_counts.embedding`
- `recall_counts.bm25`
- `recall_counts.entity`
- `after_fusion`
- `after_expand`
- `after_dedupe`
- `latency_ms.recall`
- `latency_ms.expand`
- `latency_ms.build`
- `latency_ms.total`

这套统计和普通 RAG 控制台的 `recallChannels` 不是同一个口径。

## 普通 RAG 与 Graph RAG 的边界

| 项目 | 普通 RAG `/api/rag/query` | Graph RAG `/api/rag/graph-query` |
| --- | --- | --- |
| 主召回器 | `HybridRetriever` | `GraphRetriever` |
| 稠密召回 | 有 | 有 |
| BM25 召回 | 有 | 有 |
| 结构化 filters | 有函数，常规接口暂未传入 | 不使用该函数 |
| 实体召回 | 不在普通路径使用 | 使用 `entities` + `entity_mentions` |
| related 扩展 | 使用 `chunks.related_ids` | RRF 后也会使用 |
| 图扩展 | 不使用 | 使用 `chunk_relations` + 实体层 |
| 重排 | `ParentChildReranker` | 当前返回结构化结果，不走普通 reranker |
| 生成 | `RAGGenerator` | 当前以结构化结果为主 |
| 典型用途 | 常规问答、证据引用 | 关系追踪、流程/因果/实体关联查询 |

文档详情里的知识图谱预览也是读取 `chunks`、`chunk_relations`、`entity_mentions` 和 `kg_triples`，但它是入库质量检查视图，不参与在线召回排序、Graph RAG 扩展或答案生成。

## 数据依赖

| 数据 | 来源阶段 | 在线用途 |
| --- | --- | --- |
| `chunks.content` | 离线切片 | BM25、答案上下文、引用片段 |
| `chunks.embedding` | 向量化入库 | pgvector 稠密召回 |
| `chunks.layer` | 分层切片 | 限制默认召回层为 `child + enhanced` |
| `chunks.parent_id` | 分层切片 | enhanced 折回 child |
| `chunks.related_ids` | 入库增强/关系写入 | 普通 related 扩展 |
| `chunks.title` | 解析/切片 | 图表编号直达匹配 |
| `chunks.is_image_chunk` | 切片 | 图编号直达 |
| `chunks.is_table_chunk` | 切片 | 表编号直达 |
| `chunks.image_path` | 解析/切片 | 候选图片展示 |
| `documents.filename` | 文档入库 | 引用和候选来源展示 |
| `entities` | enhanced 抽取 | Graph RAG 实体召回 |
| `entity_mentions` | enhanced 抽取 | 实体到 chunk 回跳 |
| `chunk_relations` | 关系写库 | Graph RAG 图扩展 |

## 已知口径与后续改进点

当前普通 RAG 的 `recallChannels` 是面向控制台展示的轻量统计，存在几个明确边界：

- `sparse` 目前按 `dense_score == 0` 统计，不是严格的 BM25-only 统计。
- `structured` 当前固定为 `0`，没有反映 `_structured_retrieve()` 的潜在能力。
- `related` 目前用 `score <= 0.3` 近似判断，可能受 `min_score` 和 top_k 截断影响。
- RRF 计数是融合后候选总数，不是独立召回通道命中数。

如果后续要让控制台显示更精确，可以在候选对象中显式保留每条候选的 `sources` / `matched_by`，并在 `rag_service.py` 中按来源集合统计：

- `embedding`
- `bm25`
- `structured`
- `media_ref`
- `related`
- `enhanced`
- `graph_expand`

## 正式文档入口

如果要看完整在线链路，请阅读：

- [在线检索增强问答链路](./online-rag-pipeline.md)

如果要看入库链路，请阅读：

- [离线入库链路](./offline-ingestion-pipeline.md)
## 10-09 Query Embedding Cache

普通 RAG 与 Graph RAG 的 query embedding 现在通过进程内 TTL cache 复用结果。

默认环境变量：

```env
RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS=1800
RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE=512
```

行为边界：

- `media_ref` 图表编号直达召回仍在 query embedding 之前短路，因此命中时不会调用 embedding。
- cache key 包含 normalized query、embedding model、dimensions 与 provider/base_url marker。
- TTL 设为 `0` 可关闭缓存。
- 第一阶段 cache 仅在当前 Python 进程内生效，多实例部署之间不共享。
- 普通 RAG retrieval timings 会记录 `query_embedding_cache_hit`。
