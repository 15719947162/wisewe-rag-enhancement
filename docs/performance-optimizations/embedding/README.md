# 向量化性能优化档案

本文归档向量化线的性能优化过程和技术演进。向量化线覆盖入库 confirm/finalize 阶段中的 embedding API、embedding key 池、语义 / 流程 / 因果后处理、实体物化和写库拆账，不把解析 provider 或切片增强并发计入向量化收益。

## 边界

向量化线负责：

- `embed_texts_with_metrics(texts)`。
- embedding batch 并发、重试和 key 池。
- `link_semantic()`、`detect_procedure_chunks()`、`link_procedure()`、`link_causal()`。
- `materialize_entities()` 和 `write_to_pgvector()` 的耗时拆账。

向量化线不负责：

- PDF 解析和分片。
- 三层切片 enhanced 生成。
- 在线召回策略本身的语义变更。

## 当前实现口径

```env
LLM_EMBEDDING_MODEL=text-embedding-v3
LLM_EMBEDDING_BATCH_SIZE=10
LLM_EMBEDDING_MAX_CONCURRENCY=10
LLM_EMBEDDING_MAX_RETRIES=2
LLM_EMBEDDING_API_KEY_POOL=<8 keys, redacted>
LLM_EMBEDDING_KEY_RETRIES=1
LLM_EMBEDDING_KEY_COOLDOWN_SECONDS=30
PGVECTOR_WRITE_PAGE_SIZE=500
PGVECTOR_WRITE_MODE=copy
LINKER_SEMANTIC_NUMPY_ENABLED=true
LINKER_SEMANTIC_BLOCK_SIZE=256
```

如果 `LLM_EMBEDDING_API_KEY_POOL` 为空，embedding 可以复用 `LLM_API_KEY_POOL`，但运行时 inflight、cooldown、retry 和 metrics 与切片增强线隔离。

`LINKER_SEMANTIC_NUMPY_ENABLED` 控制 `link_semantic()` 是否优先使用 numpy 分块矩阵相似度路径；未安装 numpy 或显式关闭时，自动回退到纯 Python 等价路径。`LINKER_SEMANTIC_BLOCK_SIZE` 控制每次矩阵计算的行块大小，用于平衡内存和速度。
`numpy` 已作为直接运行依赖声明，避免部署环境因缺少传递依赖而静默退回纯 Python 路径。

## 优化过程

| 时间 | 方案 / 事件 | 目标 | 判断 |
|---|---|---|---|
| 2026-06-09 | 10-09 向量化性能优化 | 入库 embedding 从串行 batch 改为有界并发，并保持返回顺序 | `embed_texts()` 返回类型和 chunk 顺序不变 |
| 2026-06-09 | query embedding TTL cache | 降低在线问答重复问题的 embedding 外呼 | 仅进程内 cache，不作为跨实例一致性能力 |
| 2026-06-09 | chunks 写库 `execute_values` | 降低 pgvector 写库固定开销 | 保留 fallback，不改 schema |
| 2026-06-11 | embedding 专用 key 池 | 将 key 池能力从切片增强扩展到 embedding | embedding 与 chunk key 池状态隔离 |
| 2026-06-11 | 后处理拆账指标 | 解释 `embedding.latencyMs` 远高于 `embeddingWallMs` 的原因 | 新增 `linkSemanticMs/linkProcedureMs/linkCausalMs` |
| 2026-06-11 | `Relation.weight` 微越界修复 | 修复 `link_semantic()` 计算 `1.0000000000000002` 导致 finalize 失败 | 只 clamp `1±1e-9` / `0±1e-9` 浮点误差，明显非法权重仍失败 |
| 2026-06-11 | `link_semantic()` 热路径优化 | 修复 3077 chunk 时 semantic 后处理占用 `251423ms` 的 O(n²) Python 热点 | 优先走 numpy 分块矩阵相似度；无 numpy 时走预计算范数、每 pair 只算一次的纯 Python 路径；保持 threshold/topk/skip_same_parent 和双向关系语义 |
| 2026-06-15 | relations/triples 批量写入 | 降低 export 中关系写入开销 | `execute_values` 后 export 从约 `35.5s` 降到约 `22.7s` |
| 2026-06-15 | `PGVECTOR_WRITE_MODE=copy` | 降低跨网络批量写入开销，并拆账 chunks/relations/triples | COPY run export `16578ms`，其中 chunks `15904ms`、relations `327ms` |
| 2026-06-15 | pgvector 写入路径微基准 | 拆分 COPY 传输、typed temp insert、真实 indexed chunks insert | 本地 DB 只明显降低 COPY 传输；indexed `chunks` insert 仍为数秒级，relations 低于 `0.5s` |

## 技术演进

1. 从单路 embedding batch 演进为有界并发 batch，保持输出有序回填。
2. 从只有 stage 总耗时演进为 `embeddingWallMs / batchCount / retryCount`。
3. 从单 key 演进为 embedding 专用 key 池或复用 LLM key 池。
4. 从只看 embedding API 演进为后处理拆账：semantic / procedure / causal。
5. 从 chunks 写库普通批量演进为优先 `execute_values`。
6. 从 relation 写入直接信任浮点值演进为统一入口处理微小浮点边界误差。
7. 从 Python 层逐行两两 cosine 演进为可选 numpy 分块矩阵相似度；fallback 路径也预计算范数并避免重复计算同一 pair。
8. 从 chunks 单表批量优化扩展到 `chunk_relations` / `kg_triples` 的 `execute_values` 批量路径。
9. 从 `execute_values` 演进到可选 `COPY` 写入模式，使用临时表承接 CSV，再由 DB 侧 cast `search_vector` 和 `embedding`。
10. 从完整 export stage 拆到 pgvector 写入微基准，确认 locality 主要影响 COPY 传输，真实 indexed `chunks` insert 仍受索引/WAL/FK 维护约束。

## 关键指标

| 指标 | 用途 |
|---|---|
| `embeddingWallMs` | embedding API 真实墙钟耗时 |
| `embedding.latencyMs` | 控制台 stage 总耗时，包含后处理 |
| `batchCount` | 对齐 chunk 数和 batch size |
| `maxConcurrency` | 验证 embedding 并发档生效 |
| `retryCount` | 判断 provider 临时失败或限流 |
| `embeddingKeyThrottleCount` | 判断 key 池是否遇到限流 |
| `linkSemanticMs` | 语义相似关系构建耗时 |
| `linkProcedureMs` | 流程 / 步骤链接耗时 |
| `linkCausalMs` | 因果链接耗时 |
| `entityMaterializeMs` | 实体物化耗时 |
| `pgvectorWriteMs` | chunks / relations / triples 写库耗时 |
| `pgvectorWriteMode` | 当前写库模式：`values` 或 `copy` |
| `pgvectorChunksWriteMs` | chunks 主表写入耗时，包含 search vector 和 embedding cast / index 维护 |
| `pgvectorRelationsWriteMs` | chunk_relations 写入耗时 |
| `pgvectorTriplesWriteMs` | kg_triples 写入耗时 |

## 真实任务结论

任务 `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` 显示：

| 指标 | 值 |
|---|---:|
| `embedding.latencyMs` | 104651 |
| `embeddingWallMs` | 15285 |
| `batchSize` | 10 |
| `batchCount` | 189 |
| `maxConcurrency` | 8 |
| `retryCount` | 0 |

因此这轮不应判断为 embedding API 变慢；真正需要优化的是 embedding API 之后的 semantic / procedure / causal 后处理。

任务 `7f07b802-ab7c-4dee-ab15-32fa946b613f` 进一步验证：解析和切片都已完成，失败发生在 direct finalize 的 `link_semantic()`。该任务 `chunk_drafts` 仍保留 `3077` 条，部署修复后可直接 confirm，不需要重跑解析或切片。

任务 `0bc0372e-27cb-4c0c-8ba3-fdc9394f6e01` 显示 `embedding.latencyMs=275166`，但内部拆账为：

| 指标 | 值 |
|---|---:|
| `embeddingWallMs` | 23728 |
| `embeddingKeyPoolSize` | 5 |
| `batchCount` | 308 |
| `retryCount` | 0 |
| `embeddingKeyThrottleCount` | 0 |
| `linkSemanticMs` | 251423 |
| `linkProcedureMs` | 5 |
| `linkCausalMs` | 4 |

因此本轮瓶颈不是 embedding API，也不是多 key 未生效，而是 `link_semantic()` 在 child 数量增加后出现平方级后处理成本。合成 benchmark 中，600 个 256 维向量在旧式逐 pair Python cosine 下约 `12346ms`，numpy 分块路径约 `164ms`，用于验证热路径方向有效；真实教材仍以 stage metrics 为准。

任务 `5bfebd2d-7a8d-49b9-8de0-5765cde90b82` 在 `PGVECTOR_WRITE_MODE=copy` 下显示：

| 指标 | 值 |
|---|---:|
| `embedding.latencyMs` | 16527 |
| `embeddingWallMs` | 16287 |
| `batchCount` | 299 |
| `retryCount` | 0 |
| `pgvectorWriteMode` | `copy` |
| `pgvectorWriteMs` | 16559 |
| `pgvectorChunksWriteMs` | 15904 |
| `pgvectorChunkRows` | 2985 |
| `pgvectorRelationsWriteMs` | 327 |
| `pgvectorRelationRows` | 7037 |
| `pgvectorTriplesWriteMs` | 0 |

因此 COPY 已经把 relations/triples 写入压到非瓶颈；当前 export 的剩余耗时主要在远程 pgvector 上写入 `chunks` 主表、1024 维向量 cast 和索引维护。

pgvector 写入路径微基准进一步验证：

| 场景 | COPY 到临时表 | typed 临时表 insert | indexed chunks 主表 insert | relations insert |
|---|---:|---:|---:|---:|
| 远程 DB，宿主机运行 | `3976-7366ms` | `900-944ms` | `3139-3436ms` | `148-185ms` |
| 远程 DB，backend 容器运行 | `7888-8216ms` | `983-994ms` | `2600-3577ms` | `140-143ms` |
| 本地临时 pgvector，远程源库 | `2032-2736ms` | `592-757ms` | `5341-6162ms` | `159-452ms` |

该微基准使用真实 `2985` chunks / `7037` relations payload，并在事务末尾 `ROLLBACK`。结论是：同机/同地域部署可以减少约 `41.8MB` CSV payload 的传输开销，但无法单独解决 indexed chunks insert 的数秒级成本；typed cast 本身也不是主因。

## 当前结论

- 暂不优先继续提高 `LLM_EMBEDDING_MAX_CONCURRENCY`；当前 embedding API 墙钟约 `16s` 且无 retry/throttle。
- 如果 `embeddingWallMs` 低但 `embedding.latencyMs` 高，优先看 `linkSemanticMs/linkProcedureMs/linkCausalMs`。
- 如果 export 高但 `pgvectorRelationsWriteMs` 很低，说明 relations/triples 已不是瓶颈；下一步应看 `pgvectorChunksWriteMs`。
- 当前 COPY 模式已经是默认推荐档；继续压 export 需要索引维护策略、bulk load 取舍或产品层 async/basic-ingestion。DB locality 只作为部分改善，不应被视为完整解法。

## 回退规则

| 现象 | 处理 |
|---|---|
| `retryCount` 或 key throttle 上升 | 降低 `LLM_EMBEDDING_MAX_CONCURRENCY` |
| `embedding.latencyMs` 高但 `embeddingWallMs` 低 | 优先优化后处理，不提高 API 并发 |
| `linkSemanticMs` 随 chunk 数平方级上升 | 启用 numpy 分块矩阵路径；若需保守回退，设置 `LINKER_SEMANTIC_NUMPY_ENABLED=false` |
| `Relation.weight` 微越界 | 统一关系写入入口 clamp 微小浮点误差 |
| 明显非法 relation weight | 保持 Pydantic 失败，不吞异常 |
| COPY 模式兼容性异常 | 设置 `PGVECTOR_WRITE_MODE=values` 回退到 `execute_values` |
| `pgvectorChunksWriteMs` 成为主瓶颈 | 优先评估索引维护策略、bulk load 取舍或 async/basic-ingestion；DB locality 仅作为传输侧改善 |
| `pgvectorRelationsWriteMs` / `pgvectorTriplesWriteMs` 成为主瓶颈 | 再回到 relation/triple 批量路径优化 |

## 关联材料

- [历史向量化性能跟踪](../../archive/performance-discussions/embedding-performance.md)
- [Embedding Key Pool](../../archive/performance-discussions/embedding-key-pool.md)
- [入库性能三线跟踪](../../archive/performance-discussions/ingestion-performance-tracks.md)
- [离线入库链路](../../pipeline/offline-ingestion-pipeline.md)
## 2026-06-11 并发 10 验证档

真实任务 `962ab89d-d5dd-47e6-bd15-5a3b504d7eb8` 显示，向量化阶段 `latencyMs=22507`，其中 `embeddingWallMs=22132`，`linkSemanticMs=333`，`linkProcedureMs=15`，`linkCausalMs=16`，`pgvectorWriteMs=304`，`retryCount=0`，`embeddingKeyThrottleCount=0`。

结论：当前向量化瓶颈已经回到 embedding API 外呼墙钟，而不是语义链接或写库。因此本轮把 `LLM_EMBEDDING_MAX_CONCURRENCY` 从 `8` 小步提升到 `10`，并把 `LLM_EMBEDDING_BATCH_SIZE` / `LLM_EMBEDDING_MAX_CONCURRENCY` / `LLM_EMBEDDING_MAX_RETRIES` 纳入控制台运行时设置，方便后续按真实任务热更新验证。

回退规则：如果下一轮 `retryCount`、`embeddingKeyThrottleCount` 或 `embeddingKeyRetryCount` 上升，优先把 `LLM_EMBEDDING_MAX_CONCURRENCY` 回退到 `8`。

## 2026-06-11 8-Key Pool Verified

Real ingestion task `3a3c1fd0-a0a8-4fea-87eb-2f1c98de3f15` confirmed `LLM_EMBEDDING_API_KEY_POOL` aligned to the same 8-key pool used by chunk enhancement. The run reported `embeddingKeyPoolSize=8`, `embeddingWallMs=18561`, `retryCount=0`, `embeddingKeyThrottleCount=0`, and `embeddingKeyRetryCount=0`. Key pool parsing is capped at 20 unique keys.
