# 在线召回性能优化归档

本文归档普通 `/api/rag/query` 在线召回链路的性能优化。它只覆盖“用户发起问题后取回候选切片”的阶段，不把 rerank、generate、score 或 Graph RAG hop 扩展计入本线收益。

## 边界

本线负责：

- 普通 RAG 候选召回
- pgvector dense 查询
- DB 稀疏文本候选
- enhanced 命中折回 child
- `related_ids` 扩展
- `timings.retrievalBreakdownMs` 中的召回拆账

本线不负责：

- Graph RAG 的 `chunk_relations` / `entity_mentions` 多跳扩展
- rerank、LLM 生成、LLM 评分外呼
- 离线入库解析、切片增强或 embedding finalize 后处理

## 当前实现口径

默认开启：

```env
RAG_RETRIEVAL_SNAPSHOT=true
```

回退方式：

```env
RAG_RETRIEVAL_SNAPSHOT=false
```

默认开启时，普通 RAG 非 `media_ref`、非结构化 filters 路径执行：

```text
query
  -> query embedding
  -> retrieval snapshot SQL
  -> Python enhanced fold
  -> Python related expand
  -> coarse filter
  -> rerank / generate / score
```

`media_ref` 图表编号直达仍在 query embedding 之前短路，不进入 snapshot。

## 2026-06-11：10-07 retrieval snapshot

### 背景

此前普通 RAG 热路径包含多段数据库访问：

- dense pgvector 查询
- BM25 冷启动时拉取全库 child/enhanced 切片
- enhanced 命中后回查 child
- related 扩展回查

这会把一次在线问答放大成多次 PG 访问，并且 BM25 冷启动会随知识库规模增长。

### 方案

新增 `core/rag/retrieval_snapshot.py`：

- `fetch_retrieval_snapshot()`：用一次 CTE 查询拿齐 dense、sparse、fold child、related 所需候选和文档元数据
- `snapshot_row_to_candidate()`：统一候选 payload
- `fold_enhanced_snapshot()`：在内存中把 enhanced 命中折回 child
- `expand_related_snapshot()`：在内存中使用 snapshot 已取回的 related rows

新增 `chunks.search_text`、`chunks.search_vector` 和 GIN 索引；入库写 `chunks` 时同步写入 search text 和 `to_tsvector('simple', search_text)`，替代在线拉全库构建 BM25 的默认热路径。

### 不改变

- 不改变 chunk、citation、图片、表格候选 payload 语义
- 不改变 `media_ref` 图表编号直达规则
- 不重写 Graph RAG
- 不把最终排序策略全部塞入 SQL，SQL 只负责候选快照，融合仍在 Python

## 关键指标

| 指标 | 用途 |
|---|---|
| `retrievalBreakdownMs.snapshot` | 一次候选快照 SQL 耗时 |
| `retrievalBreakdownMs.fold` | enhanced 折回 child 的内存处理耗时 |
| `retrievalBreakdownMs.related` | related 内存扩展耗时 |
| `retrievalBreakdownMs.snapshotFallback` | snapshot 异常后是否回退旧链路 |
| `retrievalBreakdownMs.shortCircuit` | 是否命中 `media_ref` 短路 |

## 回退规则

| 现象 | 处理 |
|---|---|
| snapshot SQL 在真实库上异常 | 设置 `RAG_RETRIEVAL_SNAPSHOT=false` 回退旧链路 |
| 中文稀疏召回质量不足 | 保留 dense 与 rerank 兜底，后续再评估中文分词或专用搜索索引 |
| Graph RAG 慢 | 不归入本次优化，另开 Graph RAG N+1 查询收敛 |
| 结构化 filters 查询 | 继续走旧链路，避免未覆盖语义回退 |

## 验证

已覆盖：

```bash
pytest tests/test_retrieval_snapshot.py tests/test_rag_retriever.py tests/test_pgvector_writer.py tests/test_api_console.py tests/test_runtime_settings.py -q
```

结果：

```text
30 passed
```

后续真实教材验证应重点观察：

- `retrievalBreakdownMs.snapshot`
- `retrievalBreakdownMs.snapshotFallback`
- 候选数量与引用是否保持稳定
- 首次查询是否不再出现 BM25 冷启动全库拉取
