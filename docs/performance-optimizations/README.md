# 性能优化档案

本目录作为性能优化的独立归档入口，和 `docs/pipeline/`、`docs/rule/` 做职责隔离。

- `docs/pipeline/`：描述当前稳定链路如何工作。
- `docs/rule/`：描述切片、清洗、召回等规则边界。
- `docs/performance-optimizations/`：记录性能瓶颈、实验过程、技术演进、回退规则和真实任务数据。

历史讨论材料已归档到 `docs/archive/performance-discussions/`，新的性能结论优先沉淀到本目录。

## 优化线

| 优化线 | 档案 | 关注点 | 不混入的内容 |
|---|---|---|---|
| 解析 | [parse/README.md](./parse/README.md) | 云解析、PDF 分片、provider 稳定性、结果获取与解析进度 | 不评价三层增强或 embedding API |
| 切片 | [chunk/README.md](./chunk/README.md) | 三层切片增强、并发调度、key 池、严格等价优化 | 不承接解析下载，也不评价向量化后处理 |
| 向量化 | [embedding/README.md](./embedding/README.md) | embedding batch、key 池、semantic/procedure/causal 后处理、写库拆账 | 不评价解析 provider 或切片增强并发 |
| 在线召回 | [retrieval/README.md](./retrieval/README.md) | 普通 RAG 候选快照、PG 查询收敛、DB 稀疏检索、enhanced/related 内存处理 | 不评估 rerank、LLM 生成、Graph RAG hop 扩展 |

## 当前基线

| 线 | 当前结论 | 下一步 |
|---|---|---|
| 解析 | MinerU / Document Mind 都已支持大文件分片；Document Mind 当前封存档为 `33页/片 + 4 worker + 单 Key 并发 1 + probe1`，并关闭托管 `LLM/VLM` 增强、保留 `markdown,visualLayoutInfo`；完整入库已验证 18.5k parse blocks 可被 clean/chunk 承接 | 阿里 Document Mind 解析性能优化已于 `2026-06-17` 结档；后续不再围绕同一 provider 做常规调参，仅在 provider 行为变化、新文档类型、可复现回退或明确证据取舍时重启 |
| 切片 | 三层增强在 `16/3/4 + 100`、8 key 池下完整入库稳定，`enhanceFailures/throttle/retry=0`，但仍需约 `67-76s` | 下一步若要大幅提速，评估 basic-ready + enhanced 异步补强，而不是继续盲目加并发 |
| 向量化 / 写库 | embedding API 稳定约 `16s`；`PGVECTOR_WRITE_MODE=copy` 已将 export 降至约 `16.6s`；写入微基准显示同机 pgvector 主要降低 COPY 传输，indexed `chunks` insert 仍为数秒级 | 优先评估索引维护策略、bulk load 取舍或 basic-ready + async write；后端与 pgvector 同地域/同机部署只作为部分改善，relations/triples 暂不再作为优化重点 |
| 在线召回 | 普通 RAG 默认使用一次 `retrieval snapshot` 查询收敛 dense/sparse/fold/related 所需候选，`media_ref` 仍短路 | 用真实教材观察 `retrievalBreakdownMs.snapshot` 与 `snapshotFallback`，Graph RAG 另行优化 |

## 归档规则

每条优化线都必须记录：

1. 优化目标和不改变的边界。
2. 真实任务或可复现基线。
3. 每轮尝试、指标变化和判断。
4. 技术方案如何演进。
5. 当前推荐档位和回退规则。

不要把一个阶段的耗时下降归功到另一个阶段。例如：切片增强已经完成，但任务最终失败在 `link_semantic()`，应归入向量化 / finalize 后处理线。

## 历史材料

- [解析性能跟踪](../archive/performance-discussions/parse-performance.md)
- [Document Mind 完整入库 No-LLM 验证](../archive/performance-discussions/document-mind-full-ingestion-2026-06-15.md)
- [Document Mind 关闭托管 LLM/VLM 增强验证](../archive/performance-discussions/document-mind-managed-llm-off-2026-06-14.md)
- [Document Mind 慢 key 评分与投机重发验证](../archive/performance-discussions/document-mind-slow-key-scoring-2026-06-14.md)
- [切片性能跟踪](../archive/performance-discussions/chunk-performance.md)
- [三层切片增强优化追踪](../archive/performance-discussions/chunk-enhancement-optimization-tracker.md)
- [三层切片优化过程归档](../archive/performance-discussions/three-layer-chunking-optimization-archive.md)
- [向量化性能跟踪](../archive/performance-discussions/embedding-performance.md)
- [Embedding Key Pool](../archive/performance-discussions/embedding-key-pool.md)
- [入库性能三线跟踪](../archive/performance-discussions/ingestion-performance-tracks.md)
- [在线召回性能优化归档](./retrieval/README.md)
