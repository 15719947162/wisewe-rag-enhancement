# 离线入库链路

本文档描述当前仓库里“文档如何进入知识库”的真实实现，不再沿用早期 `src/` 时代的目录口径。

## 定义

离线入库链路负责把原始 PDF / 文档加工为可检索、可解释、可用于 RAG 与 Graph RAG 的知识底座。

当前目标包括：

1. 把文档送入 MinerU 云解析链路
2. 对大体量 PDF 做体检、按页拆分、并发云解析和全局页码合并
3. 清洗、切片、质量过滤并生成向量
4. 写入知识库、关系层、实体层与离线评测可用的数据结构

## 当前入口

### 控制台 / API 入口

- `POST /api/ingestion/upload`
- `GET /api/ingestion/tasks/{task_id}`
- `GET /api/ingestion/stream/{task_id}`
- `POST /api/ingestion/tasks/{task_id}/retry`
- `GET /api/documents/{document_id}/export.csv`

对应编排：

- `backend/routes/ingestion.py`
- `backend/services/ingestion_service.py`
- `backend/routes/knowledge_bases.py`
- `backend/services/document_export_service.py`

### CLI 入口

- `python backend/cli.py --pdf ...`

### 兼容入口

- `python main.py --pdf ...`

## 当前服务端阶段视图

控制台里的真实任务阶段为：

```text
upload
  -> parse
  -> clean
  -> chunk
  -> quality
  -> embedding
  -> export
```

说明：

- `embedding` 阶段除了生成向量，还会补语义边、流程链和因果链
- `export` 阶段前会先做实体物化，再落库

## 实际处理链路

```text
PDF
  -> 保存到 data/uploads/
  -> PDF 体检
  -> 小文件：上传 OSS -> 302.ai MinerU 云解析
  -> 大文件：本地拆 shard -> 多 shard 上传 OSS -> 多 worker 302.ai MinerU 云解析
  -> 按原 PDF 全局页码合并 ContentBlock
  -> ContentBlock 规范化
  -> 规则清洗
  -> Chunk 切片
  -> 质量门控
  -> embedding
  -> semantic / procedure / causal relations
  -> entities / mentions 物化
  -> pgvector / chunk_relations / kg_triples / entity_mentions
```

## 关键实现位置

### 1. 文档接入与任务编排

- `backend/routes/ingestion.py`
- `backend/services/ingestion_service.py`
- `backend/services/task_store.py`

当前行为：

- 上传文件会先持久化到 `data/uploads/`
- 任务状态写入 Redis
- SSE 通过 `/api/ingestion/stream/{task_id}` 推送阶段变化和日志
- 实时日志流会持续到任务成功或失败，不再使用固定 600s 截断
- 任务长时间无新日志时，后端每 15s 发送 `heartbeat` 保持连接
- 如果浏览器断线重连，后端会从日志文件回放，前端按游标跳过已显示前缀，保证日志尽量完整且不重复刷屏

### 2. 云端解析

- `core/parser/mineru_parser.py`
- `core/parser/oss_uploader.py`

当前链路包含：

1. `parse_pdf()` 体检 PDF，读取页数、文件大小和前 N 页文本层
2. 小文件直接上传原 PDF 到 OSS，并提交 302.ai MinerU 解析任务
3. 大文件按 `pages_per_shard` 本地拆成多个 shard PDF
4. 每个 shard 独立上传 OSS、提交 MinerU、轮询结果并下载 ZIP
5. 合并 shard 内容块，执行 `global_page_idx = shard_start_page + local_page_idx`
6. 产出一份完整 `ContentBlock[]`

默认大文件分片配置：

```yaml
parser:
  cloud:
    timeout: 1800
    poll_interval: 3
    sharding:
      enabled: true
      min_pages: 120
      min_file_mb: 80
      pages_per_shard: 20
      max_concurrency: 4
      text_sample_pages: 5
```

重要约束：

- shard 只是解析执行单元，不会写成多篇文档。
- `source_file` 始终保留原始上传文件名。
- `page_idx` 在合并时修正为原教材全局页码，前端展示仍是 `page_idx + 1`。
- 图片输出目录按 shard 隔离，避免 `images/` 文件名覆盖。
- 清洗、切片、图表引用关系、实体物化和写库都发生在合并后的完整 `ContentBlock[]` 之后，因此跨 shard 的文本/图表关系仍可在后续阶段构建。

Document Mind provider 额外支持多 AK/SK credential pool：

```env
ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL=ak1:sk1,ak2:sk2
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_RETRIES=1
ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS=60
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

约束：

- 同一个 shard 的 `submit -> poll -> result` 使用同一个 `dm-key-N`。
- 只有明确限流类错误触发换 key 重试，普通业务错误和空结果保护仍按原错误语义处理。
- 实际 shard 并发受 `shardCount`、`ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY`、`parseKeyPoolSize * ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY` 共同限制。
- `ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD` 是每片页数上限；页数多且 key 池容量足够时，会自动收敛 `effectivePagesPerShard`，减少单个大 shard 的 provider 处理长尾。
- 当前解析验证档保持 `SHARDING_MAX_CONCURRENCY=4`、`MAX_INFLIGHT_PER_KEY=1`；新增 AK/SK 只扩大可轮换凭证池，不再隐式提高 shard worker 上限。
- parse stage metrics 会记录匿名 `parseKey.*` 指标，用于判断多 AK/SK 是否真实分摊请求。

### 3. 清洗

- `core/cleaner/`

当前默认会走规则清洗；CLI 可按参数启用 LLM 清洗。

### 4. 切片

- `core/chunker/`

当前项目已经包含多种切片策略，并在分层切片基础上承接 Graph RAG 所需的 enhanced 输出。

hierarchical 三层切片当前默认使用 `HIERARCHICAL_ENHANCE_MODE=parallel_ordered`：

```text
基础切片 parent/child/table/image
  -> 规划 enhanced slots
  -> text/fragment/table/image 动态借用式有限并发增强
  -> 按原 slot 顺序合并
  -> link_related_chunks()
```

这仍然是“入库前完整三层结果”模式，不改变 parent / child / enhanced 原则，也不跳过任何原本会触发的 enhanced。需要完全回退旧式逐条增强时，可设置 `HIERARCHICAL_ENHANCE_MODE=serial`。

当前低风险验证配置：

| 配置 | 当前值 | 作用 |
|---|---:|---|
| `HIERARCHICAL_TEXT_ENHANCE_WORKERS` | 16 | 普通文本与片段增强共用的文本增强软上限 |
| `HIERARCHICAL_TABLE_ENHANCE_WORKERS` | 3 | 表格摘要增强软上限 |
| `HIERARCHICAL_IMAGE_ENHANCE_WORKERS` | 4 | 图片/VL 描述增强软上限 |
| `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY` | 70 | 单进程 enhanced 外呼全局硬上限 |
| `HIERARCHICAL_REUSE_LLM_CLIENTS` | true | 每个 worker 线程内复用同一 provider/base_url 的 OpenAI 兼容客户端 |

当前最大同时 enhanced 外呼数由 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70` 控制。三类 worker 值是软上限：调度器优先保障 text/table/image 的独立并发预算，但当某类任务提前完成或不存在时，空闲容量可以被剩余类型借用。worker 完成顺序不会改变最终切片顺序；最终仍按预留 slot 合并后，再进入关系构建、质检、向量化和写库。客户端复用只减少重复 client/连接池初始化，不改变 prompt、请求参数或输出解析；如遇 provider 兼容问题可设为 `false` 回退。

切片阶段会在任务状态中记录 `chunkTimings`，主要字段包括：

- `chunkBaseMs`：基础 parent / child / table / image 切片耗时
- `enhanceWallMs`：并发 enhanced 阶段墙钟耗时
- `enhanceTextMs` / `enhanceFragmentMs` / `enhanceTableMs` / `enhanceImageMs`：各类增强任务累计耗时
- `enhanceTasks` / `enhanceTextTasks` / `enhanceFragmentTasks` / `enhanceTableTasks` / `enhanceImageTasks`：增强任务数量
- `enhanceFailures`：无输出且带错误的增强任务数量
- `enhanceClientReuse`：是否启用线程内客户端复用，`1` 表示启用
- `enhanceScheduler`：是否启用动态借用式调度器，`1` 表示启用
- `enhanceMaxConcurrency` / `enhancePeakConcurrency`：配置的全局并发上限与本次实际峰值并发
- `enhanceTextWorkers` / `enhanceTableWorkers` / `enhanceImageWorkers`：本次运行读取到的各类型 worker 软上限
- `mergeChunksMs`：按 slot 合并耗时
- `linkRelationsMs`：`link_related_chunks()` 关系构建耗时

### 5. 质量门控

- `core/cleaner/quality_gate.py`

目标是过滤掉明显不适合作为知识单元的切片。

### 6. 向量化与关系补链

- `core/embedding/client.py`
- `core/chunker/semantic_linker.py`
- `core/chunker/procedure_linker.py`
- `core/chunker/causal_linker.py`

当前在这一阶段会完成：

- chunk embedding
- `semantic_similar` / `duplicate_of` 等语义边
- `next_step` / `prev_step`
- `cause_of` / `effect_of`

### 7. 实体物化与写库

- `core/kg/extraction_pipeline.py`
- `core/output/entity_writer.py`
- `core/output/pgvector_writer.py`
- `core/db/schema.py`

当前会写入的核心结构包括：

- `knowledge_bases`
- `documents`
- `chunks`
- `chunk_relations`
- `kg_triples`
- `entities`
- `entity_mentions`

正式入库后的文档 CSV 导出也基于这组结构：

- 以 `documents.id` 作为导出入口
- 逐行输出 `chunks`
- 将 `chunk_relations` 与 `kg_triples` 以 JSON 字符串附着到每行
- 面向人工验收时统一按 1-based 页码输出

## 数据库初始化说明

当前有两条自动兜底路径：

- `backend/serve.py` 启动时会尝试 `ensure_db_schema()`
- `core/output/pgvector_writer.py` 写库前也会调用 `ensure_db_schema()`

因此 `python -m core.db.init_db` 或 `docker compose --profile tools run --rm db-init` 现在更适合作为：

- 手工 bootstrap
- 独立排障
- 显式初始化工具

而不是唯一的建表入口。

## 当前产物

离线入库链路的主要输出包括：

1. `ContentBlock` 列表
2. `Chunk` 列表
3. 向量数据
4. 关系边与三元组
5. 实体与实体反向索引
6. PostgreSQL / pgvector 中的知识库数据
7. 面向控制台与人工验收的正式文档 CSV 导出

## 与在线链路的边界

离线入库链路在“知识被写入并可被检索”时结束。

后续的：

- 用户提问
- 检索融合
- 图扩展
- 答案生成
- 评分与引用回溯

属于 [在线检索增强问答链路](./online-rag-pipeline.md)。
## 10-09 Embedding Performance Notes

The ingestion embedding stage keeps the public `embed_texts(texts)` behavior
unchanged while running embedding batches with bounded concurrency.

Current validation environment values:

```env
LLM_EMBEDDING_BATCH_SIZE=10
LLM_EMBEDDING_MAX_CONCURRENCY=10
LLM_EMBEDDING_MAX_RETRIES=2
PGVECTOR_WRITE_PAGE_SIZE=500
```

Important behavior:

- `LLM_EMBEDDING_BATCH_SIZE` remains 10 by default to stay within DashScope-compatible limits.
- `LLM_EMBEDDING_MAX_CONCURRENCY=10` lets multiple embedding batches run in parallel, but the final embedding list is merged back in the original chunk order.
- `LLM_EMBEDDING_MAX_RETRIES` retries a failed batch without restarting the whole document embedding run.
- The ingestion task `embedding` stage exposes metrics including batch size, batch count, max concurrency, retry count, and embedding wall time.
- Chunk export uses `execute_values` for `chunks` inserts when psycopg2 extras are available, with `executemany` retained as fallback.
