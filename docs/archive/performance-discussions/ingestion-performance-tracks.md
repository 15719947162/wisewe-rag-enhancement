# 入库性能三线追踪

本文用于把入库链路的性能优化拆成三条独立追踪线：解析、切片、向量化。三条线可以共用同一次真实教材任务数据，但调参、回退和验收应分别判断，避免把某一段的提速误认为整条链路稳定。

## 基线任务

当前参考任务：

```text
task_id=080ed361-f4f5-4be6-b134-99d53ecbd19a
file=36.中医学 第9版.pdf
provider=ali_document_mind
strategy=hierarchical
```

### 阶段耗时

| 阶段 | 耗时 | 观察结论 |
|---|---:|---|
| parse | 147212ms | Document Mind 分片解析已稳定，`33页/片 + 4 worker + 单 Key 并发 1` 是当前最新最优已验证档 |
| clean | 70ms | 非瓶颈 |
| chunk | 86015ms | 主要耗时仍在增强外呼，不属于解析线 |
| quality | 33ms | 非瓶颈 |
| embedding | 17801ms | 目前不是主瓶颈 |
| export | 316ms | 非瓶颈 |

### 切片细分

> 这部分只为完整记录入库链路，不作为解析线调参依据。

| 指标 | 值 |
|---|---:|
| `chunkBaseMs` | 810 |
| `enhanceWallMs` | 85115 |
| `enhanceTasks` | 1566 |
| `enhanceTextTasks` | 867 |
| `enhanceFragmentTasks` | 128 |
| `enhanceTableTasks` | 79 |
| `enhanceImageTasks` | 492 |
| `enhanceFailures` | 0 |
| `enhancePeakConcurrency` | 100 |

## 2026-06-15 完整入库验证

这轮验证的目标是确认 `ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false` 的快速解析档是否只在 parse-only 中好看，还是可以被完整入库链路承接。

| Run | Task | Result | Wall | Parse | Clean | Chunk | Embedding | Export | Chunks |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No-LLM full baseline | `73343dfd-9534-4b1c-9a96-e2509b437dc6` | success | `262834ms` | `123655ms` | `95ms` | `67115ms` | `16373ms` | `35509ms` | `2989` |
| Relations/triples batched | `3ef92565-37b3-469d-8afa-5fec2985501b` | success | `232610ms` | `102300ms` | `206ms` | `76277ms` | `16471ms` | `22516ms` | `2989` |
| Warm values run | `d7d8f8a3-4557-4046-8bd6-e21587c79cf6` | success | `212885ms` | `84375ms` | `109ms` | `74471ms` | `16336ms` | `22710ms` | `2989` |
| COPY run, API rechecked | `5bfebd2d-7a8d-49b9-8de0-5765cde90b82` | success | about `236s` | `124088ms` | `113ms` | `71933ms` | `16527ms` | `16578ms` | `2985` |

COPY run 的 export 拆账：

| 指标 | 值 |
|---|---:|
| `pgvectorWriteMode` | `copy` |
| `pgvectorWriteMs` | `16559` |
| `pgvectorChunksWriteMs` | `15904` |
| `pgvectorChunkRows` | `2985` |
| `pgvectorRelationsWriteMs` | `327` |
| `pgvectorRelationRows` | `7037` |
| `pgvectorTriplesWriteMs` | `0` |
| `pgvectorCommitMs` | `68` |

DB 复核 `bench-p33-c4-no-llm-layout-full-20260615-082020-da45`：`chunks=2985`、`relations=7037`、`triples=0`。

结论：

- 关闭 Document Mind 托管 LLM/VLM 已通过完整入库验证；18.5k parse blocks 没有让 clean/chunk 失控。
- COPY 写库把 export 从约 `22.7s` 降到约 `16.6s`。
- relations/triples 不再是写库瓶颈；剩余 export 主要耗在远程 pgvector 的 `chunks` 主表写入、1024 维向量 cast 与索引维护。
- 当前真实瓶颈顺序是 parse、chunk、chunks 写库、embedding。

### pgvector 写入路径微基准

输出文件：

```text
data/results/pgvector_write_path_benchmark.jsonl
```

验证方式：

- 读取成功 benchmark KB `bench-p33-c4-no-llm-layout-full-20260615-082020-da45` 的真实 `chunks/chunk_relations`。
- 重新映射 UUID，避免冲突。
- 在一个事务内写入 benchmark KB / document / chunks / relations。
- 末尾 `ROLLBACK`，不留下 benchmark 数据。

共同 payload：`chunks=2985`、`relations=7037`、`chunkCsvBytes=41837219`。

| 场景 | COPY 到临时表 | typed 临时表 insert | indexed chunks 主表 insert | relations insert | total |
|---|---:|---:|---:|---:|---:|
| 远程 DB，宿主机运行 | `3976-7366ms` | `900-944ms` | `3139-3436ms` | `148-185ms` | `13405-20742ms` |
| 远程 DB，backend 容器运行 | `7888-8216ms` | `983-994ms` | `2600-3577ms` | `140-143ms` | `22233-22933ms` |
| 本地临时 pgvector，远程源库 | `2032-2736ms` | `592-757ms` | `5341-6162ms` | `159-452ms` | `14782-18526ms` |

结论：

- 本地 pgvector 能明显缩短 COPY 传输，但不能把 indexed chunks insert 消掉。
- `embedding_text::vector` 与 `to_tsvector(...)` 写入无索引 typed 临时表约 `0.6-1.0s`，不是主因。
- 当前 export 继续优化的重点应转向索引维护策略、bulk load 取舍或 basic-ready 异步模式，而不是继续压 relations/triples。

## 解析线

目标：降低大文件云端解析等待和超时风险，同时保持解析结果页码、内容块顺序和 `source_file` 语义稳定。

当前推荐口径：

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED=true
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB=150
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES=50
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
```

观察指标：

| 指标 | 用途 |
|---|---|
| parse 阶段总耗时 | 判断 Document Mind 是否仍是入库主瓶颈 |
| shard 数量、页数、完成顺序 | 判断分片是否均衡，是否存在长尾 shard |
| provider 错误码和重试 | 判断是否碰到限流、任务失败或上游波动 |
| 合并后 `page_idx` 连续性 | 验证分片合并没有破坏全局页码 |
| 内容块数量和图片块数量 | 防止调参导致解析结果回退 |

当前结论：

- `MAX_INFLIGHT_PER_KEY=2` 会放大 Document Mind provider 侧隐式排队风险。
- 4 组 AK/SK 下，`33页/片 + 4 worker + 单 Key 并发 1` 是当前最优已验证档。
- 如果后续增加真实 AK/SK 组数，优先扩大物理凭证池，不要通过同一 Key 的多 in-flight 去堆并发。

## 切片线

目标：在严格遵守 parent / child / enhanced 三层切片原则、触发条件、prompt、模型参数、slot 合并顺序和最终 chunk 结果不变的前提下，继续压缩 enhanced 外呼墙钟。

当前参考口径：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=100
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

观察指标：

| 指标 | 用途 |
|---|---|
| `chunkBaseMs` | 确认基础切片本身不是瓶颈 |
| `enhanceWallMs` | 本轮切片优化核心指标 |
| `enhancePeakConcurrency` | 验证并发档是否真正生效 |
| `enhanceFailures` | 判断是否触发 provider 限流或请求失败 |
| `enhanceTextMs` / `enhanceTableMs` / `enhanceImageMs` | 判断长尾主要来自文本、表格还是图片/VL |
| `enhanceTasks` 及分类任务数 | 观察不同教材之间的负载是否均衡 |
| `linkRelationsMs` | 防止增强提速后关系构建反而成为新瓶颈 |

## 向量化线

目标：保持 embedding 模型、维度、chunk 顺序和写库结果不变，只优化 batch 并发、重试和写库批量路径。

当前参考口径：

```env
LLM_EMBEDDING_BATCH_SIZE=10
LLM_EMBEDDING_MAX_CONCURRENCY=10
LLM_EMBEDDING_MAX_RETRIES=2
PGVECTOR_WRITE_PAGE_SIZE=500
PGVECTOR_WRITE_MODE=copy
```

观察指标：

| 指标 | 用途 |
|---|---|
| `embeddingWallMs` | embedding client 内部真实墙钟 |
| `batchCount` | 观察 batch size 是否合理 |
| `maxConcurrency` | 验证并发档是否生效 |
| `retryCount` | 判断是否发生临时失败重试 |
| 写库耗时 / export 耗时 | 判断是否转移到 pgvector 写入阶段 |
| `pgvectorChunksWriteMs` | 判断 chunks 主表写入、vector cast 和索引维护是否成为 export 瓶颈 |
| `pgvectorRelationsWriteMs` / `pgvectorTriplesWriteMs` | 判断关系 / 三元组写入是否仍值得继续优化 |

当前结论：

- 当前 embedding API 不是第一瓶颈，稳定在约 `16s`。
- COPY 后 export 仍约 `16.6s`，但主要耗时已集中在 `chunks` 主表写入，不是 relations/triples。
- 若后续继续压 export，优先评估 DB locality、无索引 staging load 或 async/basic-ingestion 模式，而不是继续优化 relations/triples。

## 复盘模板

每次真实教材跑完后，补入：

```text
task_id=
parseMs=
chunkMs=
embeddingMs=

chunkBaseMs=
enhanceWallMs=
enhancePeakConcurrency=
enhanceFailures=

embeddingWallMs=
batchCount=
maxConcurrency=
retryCount=

pgvectorWriteMode=
pgvectorChunksWriteMs=
pgvectorRelationsWriteMs=
pgvectorTriplesWriteMs=

decision=parse hold / parse adjust / chunk hold / embedding hold
```

## 关联材料

- [解析性能跟踪](parse-performance.md)
- [解析性能优化档案](../../performance-optimizations/parse/README.md)
- [切片性能优化档案](../../performance-optimizations/chunk/README.md)
- [向量化性能优化档案](../../performance-optimizations/embedding/README.md)
- [Document Mind 完整入库 No-LLM 验证](document-mind-full-ingestion-2026-06-15.md)
