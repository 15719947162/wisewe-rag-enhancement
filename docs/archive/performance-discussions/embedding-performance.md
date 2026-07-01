# 向量化性能跟踪

本文单独跟踪离线入库中的 `embedding` 阶段，以及在线查询中的 query embedding 热路径。

## 当前实现

入库向量化在 `backend/services/ingestion_service.py` 的 confirm/finalize 链路中执行：

```text
quality passed chunks
-> embed_texts_with_metrics(texts)
-> link_semantic(passed, embeddings)
-> write_to_pgvector(...)
```

`core/embedding/client.py` 当前提供：

- `embed_texts_with_metrics()`
- `EmbeddingRun`
- batch 有序合并
- batch 级有限重试
- 入库 embedding 有界并发
- query embedding 进程内 TTL cache

`embed_texts(texts)` 保持原返回类型，内部委托新实现。

## 当前配置口径

```env
LLM_EMBEDDING_MODEL=text-embedding-v3
LLM_EMBEDDING_BATCH_SIZE=10
LLM_EMBEDDING_MAX_CONCURRENCY=10
LLM_EMBEDDING_MAX_RETRIES=2
LLM_EMBEDDING_API_KEY_POOL=
LLM_EMBEDDING_KEY_RETRIES=1
LLM_EMBEDDING_KEY_COOLDOWN_SECONDS=30
RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS=1800
RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE=512
PGVECTOR_WRITE_PAGE_SIZE=500
```

说明：

- batch size 当前固定为 10，符合 DashScope `text-embedding-v3` 常见上限。
- max concurrency 控制同时外呼 batch 数。
- retry 只覆盖 batch 级临时失败，不改变输入顺序和输出顺序。
- query embedding cache 只在当前 Python 进程内生效。

## 已有观测字段

`embedding` stage 的 `metrics` 当前包含：

| 字段 | 含义 |
|---|---|
| `batchSize` | 每批文本数量 |
| `batchCount` | batch 总数 |
| `maxConcurrency` | 本次实际使用的最大并发 |
| `retryCount` | batch 级重试总次数 |
| `embeddingWallMs` | embedding API 调用阶段墙钟耗时 |

入库 stage 还会记录：

- `latencyMs`
- `inputCount`
- `outputCount`

注意：`embeddingWallMs` 只覆盖 embedding API 阶段，`embedding.latencyMs` 还包含 `link_semantic()` 等后续处理。

2026-06-11 最新真实任务 `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` 进一步确认了这个口径差异：

| 指标 | 值 |
|---|---:|
| `embedding.latencyMs` | 104651 |
| `embeddingWallMs` | 15285 |
| `batchSize` | 10 |
| `batchCount` | 189 |
| `maxConcurrency` | 8 |
| `retryCount` | 0 |

因此这轮不应判断为 embedding API 变慢；真正需要拆账的是 `embed_texts_with_metrics()` 之后的 `link_semantic()`、`detect_procedure_chunks()`、`link_procedure()` 和 `link_causal()`。

2026-06-11 真实任务 `7f07b802-ab7c-4dee-ab15-32fa946b613f` 验证了上述后处理路径还需要边界保护：切片阶段已成功输出 `3077` 个切片，增强墙钟 `112575ms`，但 direct finalize 在 `link_semantic()` 生成 `Relation(weight=1.0000000000000002)` 时触发 `weight <= 1` 校验失败。根因是 cosine 相似度在相同 / 近相同向量上出现微小浮点越界，不是 embedding API 失败。当前修复是在 `core/chunker/relation_utils.py` 的统一关系写入入口中，只把 `1±1e-9` / `0±1e-9` 这种浮点误差压回合法范围，明显非法权重仍保留 Pydantic 校验失败。

恢复口径：该任务失败发生在实体物化和 pgvector 写入前，未形成半截入库；`chunk_drafts` 仍保留 `3077` 条可确认草稿。部署修复后可直接调用 `/api/ingestion/chunks/confirm/7f07b802-ab7c-4dee-ab15-32fa946b613f` 重新执行 quality / embedding / finalize，不需要重跑 Document Mind 解析或三层切片。

## 下一轮重点看

真实教材跑完后，优先记录：

- `embedding.latencyMs`
- `embedding.metrics.embeddingWallMs`
- `embedding.metrics.batchCount`
- `embedding.metrics.maxConcurrency`
- `embedding.metrics.retryCount`
- export / pgvector 写库耗时是否超过 embedding 本身

如果 `retryCount` 明显上升，说明并发可能已经超过 provider 稳定区间，应优先回退 `LLM_EMBEDDING_MAX_CONCURRENCY`。

## 可优化空间

在不改变向量模型和 schema 的前提下，候选方向：

1. 分离 embedding API 与语义/规则链接耗时：把 `linkSemanticMs`、`linkProcedureMs`、`linkCausalMs` 单独写入 stage metrics。
2. 写库批量化继续扩展：当前 chunks 已优先使用 `execute_values`，如 export 慢再扩展到 `chunk_relations` / `kg_triples`。
3. query embedding cache 观测：增加命中率字段，用于判断是否值得做 Redis 级 cache。
4. batch 自适应退避：当 429 / timeout 增加时，对后续 batch 降低并发。
5. 空文本与重复文本去重：必须保证 chunk 顺序和 embedding 回填位置不变。

## 不改变的边界

- 不改变 embedding model。
- 不改变 `chunks.embedding vector(1024)` schema。
- 不改变 chunk id、顺序、layer、parent_id、related_ids。
- 不改变 `media_ref` 图表编号直达召回短路规则。
- 不把 query embedding cache 作为跨实例一致性能力。
