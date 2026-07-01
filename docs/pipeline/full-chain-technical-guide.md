# WiseWe RAG 完整链路技术说明

本文档用于把当前项目的完整技术链路整理成后续需求设计、链路改造、性能优化和验收拆解时可直接引用的底稿。它描述当前实现事实，不替代 `.planning/**` 的阶段计划，也不记录每轮性能调参过程。

## 1. 总体链路

系统分为三条主链路：

```text
离线入库链路
  PDF / 文档
  -> 上传与任务创建
  -> 解析 provider 选择
  -> PDF 体检与分片
  -> 云解析与结果获取
  -> ContentBlock 规范化
  -> 清洗
  -> 三层切片与 enhanced 增强
  -> 切片草稿预览与确认
  -> 质量门控
  -> embedding
  -> semantic / procedure / causal 关系补链
  -> 实体物化
  -> pgvector / 关系 / 三元组写库

在线检索增强问答链路
  用户问题
  -> API 接收与参数归一
  -> media_ref 直达判断
  -> dense / BM25 / structured 召回
  -> RRF 融合
  -> enhanced 折回 child
  -> related 扩展
  -> rerank
  -> context 构造
  -> LLM 生成
  -> 引用对齐
  -> 运行时评分与记录

Graph RAG 结构化检索链路
  用户问题
  -> intent router
  -> dense / BM25 / entity 召回
  -> RRF 融合
  -> related 扩展
  -> chunk_relations / entity_mentions 图扩展
  -> hydrate
  -> structured results / stats / path
```

当前控制台同时承接知识库管理、文档详情、入库任务、问答工作台、评测记录、设置和图谱预览。

## 2. 核心模块版图

| 层 | 目录 / 模块 | 职责 |
| --- | --- | --- |
| HTTP 入口 | `backend/routes/` | 入库、知识库、文档、RAG、Graph RAG、评测、设置、健康检查等 API |
| 服务编排 | `backend/services/` | 入库任务编排、任务状态、草稿确认、控制台聚合、文档导出 |
| 适配层 | `backend/adapters/` | 把 HTTP 请求转换为 core 链路调用和响应 payload |
| 解析 | `core/parser/` | MinerU、官方 MinerU、Document Mind、OSS、PDF 分片、provider 选择 |
| 清洗 | `core/cleaner/` | block 级规则清洗、LLM 清洗、chunk 级质量门控 |
| 切片 | `core/chunker/` | 多策略切片、hierarchical 三层切片、enhanced、关系补链 |
| 向量化 | `core/embedding/` | OpenAI 兼容 embedding 调用、batch、并发、重试、metrics |
| RAG | `core/rag/` | 召回、重排、生成、评分、Graph RAG intent 和图扩展 |
| 知识图谱 | `core/kg/` | 实体抽取、实体合并、定义生成、mention 物化 |
| 输出 | `core/output/` | pgvector 写入、关系写入、三元组写入、CSV 导出 |
| 数据库 | `core/db/` | schema、连接、初始化兜底 |
| 前端 | `frontend/src/` | Next.js 控制台、知识库工作台、入库、问答、设置、图谱组件 |

## 3. 数据库与核心数据结构

| 表 / 结构 | 生成阶段 | 主要用途 |
| --- | --- | --- |
| `knowledge_bases` | 知识库创建 | 知识库隔离、默认切片策略 |
| `documents` | export | 文档元信息、文件 hash、源文件路径、解析 provider |
| `chunk_drafts` | chunk 后 | 可控入库草稿，支持预览、编辑、删除、合并、确认 |
| `chunks` | export | 在线召回主数据，含正文、层级、embedding、图片路径、页码 |
| `chunk_relations` | export | typed relation 图边，供图谱预览和 Graph RAG 扩展 |
| `kg_triples` | export | enhanced 抽取出的结构化三元组 |
| `entities` | export 前 | 实体层，供 Graph RAG 实体召回和图谱预览 |
| `entity_mentions` | export 前 | entity 到 chunk 的反向索引 |
| `data/uploads/` | upload | 持久化原始上传文件，支持任务恢复和重试 |
| `data/logs/` | 任务运行 | SSE 回放、排障、阶段日志 |
| `data/output/` | parse | 解析结果、图片等资产 |

核心中间模型：

| 模型 | 来源 | 说明 |
| --- | --- | --- |
| `ContentBlock` | 解析阶段 | 解析 provider 统一输出，保留页码、类型、文本、表格、图片路径 |
| `Chunk` | 切片阶段 | 入库和召回的核心知识单元，支持 `parent`、`child`、`enhanced` |
| `Entity` | 实体物化 | 实体名、别名、类型、定义、source chunk |
| RAG candidate | 在线召回 | 召回候选，含分数、来源、文档名、页码、图片 URL、matched enhanced 信息 |

## 4. 离线入库链路

### 4.1 上传与任务创建

| 项 | 当前实现 |
| --- | --- |
| API | `POST /api/ingestion/upload` |
| 代码 | `backend/routes/ingestion.py`、`backend/services/ingestion_service.py` |
| 输入 | PDF 文件、`kb_id`、切片策略、学科类型、版式类型等 |
| 输出 | `task_id`、任务状态、持久化源文件 |
| 状态存储 | `backend/services/task_store.py`，Redis 优先，内存降级 |
| 文件落点 | `data/uploads/{task_id}.pdf` 或原扩展名 |

关键行为：

- 上传后立即创建任务，并把源文件落盘。
- 任务 payload 保存 `kb_id`、`filename`、`strategy`、`source_path`、`stages`。
- 控制台通过轮询和 SSE 查看任务状态。
- 删除任务时会尝试清理草稿、上传文件和相关临时产物。

需求挂点：

- 多文件批量上传：扩展 upload API 与任务队列聚合。
- 断点续跑：围绕 `source_path`、阶段产物和 task 状态增加阶段级恢复。
- 租户隔离：在 `kb_id` 外补租户字段和权限过滤。

### 4.2 阶段状态与可观测性

当前任务阶段固定为：

```text
upload -> parse -> clean -> chunk -> quality -> embedding -> export
```

代码常量：

- `backend/services/ingestion_service.py`
- `STAGE_KEYS = ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"]`

每个 stage 主要字段：

| 字段 | 说明 |
| --- | --- |
| `status` | `pending`、`running`、`success`、`failed` |
| `message` | 当前阶段人类可读进度 |
| `progress` | 0 到 100 的阶段进度 |
| `latency_ms` | 阶段耗时 |
| `input_count` / `output_count` | 输入输出数量 |
| `metrics` | 阶段特有指标 |

SSE 行为：

- API：`GET /api/ingestion/stream/{task_id}`
- 任务未结束时发送日志和状态快照。
- 长时间无日志时发送 heartbeat。
- 浏览器重连时按日志文件回放，前端用游标去重。

### 4.3 Parser Provider 选择

| provider | 配置值 | 代码 | 说明 |
| --- | --- | --- | --- |
| 302AI MinerU | `PDF_PARSER_PROVIDER=mineru` | `core/parser/mineru_parser.py` | 当前默认解析通道，复用 OSS URL 与 ZIP 结果转换 |
| 官方 MinerU | `PDF_PARSER_PROVIDER=mineru_official` | `core/parser/mineru_official_parser.py` | 官方精准解析 API，独立 token 与 `MINERU_OFFICIAL_*` 配置 |
| 阿里 Document Mind | `PDF_PARSER_PROVIDER=ali_document_mind` | `core/parser/document_mind_parser.py` | OpenAPI SDK 接入，支持 markdown 与 visualLayoutInfo |

统一入口：

- `core/parser/provider.py`
- `get_pdf_parser_provider()`
- `parse_pdf()`

重要边界：

- provider 必须显式切换，不做隐式 fallback。
- 三条解析通道各自有独立分片配置。
- 解析输出必须统一为 `ContentBlock[]`，后续链路不直接依赖 provider 原始 JSON。

### 4.4 PDF 体检与分片

| 项 | 当前实现 |
| --- | --- |
| 代码 | `core/parser/pdf_sharding.py` |
| 体检 | 页数、文件大小、文本层采样、疑似扫描特征、可选 per-page profile |
| 固定分片 | `split_pdf_to_shards()` |
| 实验分片 | `split_pdf_to_weighted_shards()`，默认仅实验档 |
| 合并 | `merge_shard_records()` 恢复全局页码 |

通用页码规则：

```text
global_page_idx = shard_start_page + local_page_idx
用户可见页码 = global_page_idx + 1
```

文档身份规则：

```text
source_file = 用户原始上传文件名
documents 表仍只写一篇原文档
shard 只是解析执行单元，不进入 documents 表
```

当前推荐解析档位：

| provider | 稳定档 |
| --- | --- |
| 302AI MinerU | `parser.cloud.sharding.pages_per_shard=20`，`max_concurrency=4`，触发阈值约 `120 页` 或 `80 MB` |
| 官方 MinerU | 已验证快档 `pipeline + 20 页/片 + 6 worker + 30 MB/片上限` |
| Document Mind | 已封存档 `33 页/片 + 4 worker + 单 key 并发 1 + probe1 + No-LLM + markdown,visualLayoutInfo` |

### 4.5 云解析与结果转换

解析阶段输入：

```text
本地 PDF 路径
原始文件名
provider 配置
OSS 配置或 Document Mind AK/SK
```

解析阶段输出：

```text
ContentBlock[]
parse metrics
解析日志
图片 / 表格 / 页面视觉证据路径或 URL
```

关键字段：

| 字段 | 说明 |
| --- | --- |
| `text` / `content` | 文本内容或图表占位说明 |
| `page` / `page_idx` | 0-based 全局页码 |
| `block_type` | 文本、标题、图片、表格等 |
| `is_table` / `is_image` | 表格或图片标记 |
| `image_path` | 本地路径、远程 URL 或 data URL |
| `table_html` | 表格结构化内容 |
| `source_file` | 原始文件名 |

解析 metrics 关注：

- `provider`
- `shardCount`
- `completedShards`
- `pollCount`
- `parseWallMs`
- `outputBlocks`
- `outputImageBlocks`
- `outputTableBlocks`
- Document Mind 的匿名 `parseKey.*`
- 官方 MinerU 的 shard 数、worker 数、model_version

### 4.6 清洗

| 项 | 当前实现 |
| --- | --- |
| 阶段 | `clean` |
| 代码 | `core/cleaner/` |
| 默认 | 规则清洗 |
| 可选 | CLI 可启用 LLM 清洗 |
| 输入 | `ContentBlock[]` |
| 输出 | 清洗后的 `ContentBlock[]`、removed reasons、metrics |

当前职责：

- 去除空块、低价值格式噪声、版权广告、明显异常块。
- 表格块、图片块需要谨慎豁免，避免把无纯文本但有证据价值的内容删掉。
- 清洗阶段只处理 block 级噪声，不负责判断最终 chunk 是否值得入库。

需求挂点：

- 教材页眉页脚规则中心。
- 学科定制清洗规则。
- 清洗前后 diff 可视化。

### 4.7 三层切片

| 项 | 当前实现 |
| --- | --- |
| 阶段 | `chunk` |
| 代码 | `core/chunker/`、`core/chunker/hierarchical.py` |
| 默认策略 | `hierarchical` |
| 可选策略 | `llm`、`paragraph`、`semantic`、`separator`、`fixed_length` |
| 输入 | 清洗后的 `ContentBlock[]` |
| 输出 | `Chunk[]` |

三层结构：

| 层级 | 作用 | 是否默认召回 | 是否最终引用 |
| --- | --- | ---: | ---: |
| `parent` | 章节、标题、较大结构容器 | 否 | 否 |
| `child` | 正式证据粒度，承载正文、图片、表格、页码 | 是 | 是 |
| `enhanced` | LLM/VL 生成的摘要、问题化表达、图片描述、表格摘要、片段补全 | 是 | 否，命中后折回 child |

当前 enhanced 类型：

- 普通文本增强
- 上下文依赖片段增强
- 表格摘要
- 图片描述

当前默认增强模式：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

执行规则：

1. 先生成基础 `parent / child / table / image`。
2. 为需要增强的 child 预留 enhanced slot。
3. text、table、image worker 以软上限调度。
4. 空闲容量可被仍有积压的类型借用。
5. 全局并发不超过 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY`。
6. worker 可乱序完成，但最终按 slot 顺序合并，保证结果稳定。
7. 之后执行 `link_related_chunks()`。

切片 metrics：

- `chunkBaseMs`
- `enhanceWallMs`
- `enhanceTextMs`
- `enhanceFragmentMs`
- `enhanceTableMs`
- `enhanceImageMs`
- `enhanceTasks`
- `enhanceFailures`
- `enhanceMaxConcurrency`
- `enhancePeakConcurrency`
- `enhanceClientReuse`
- `enhanceScheduler`
- `mergeChunksMs`
- `linkRelationsMs`

### 4.8 图文、表格和轻量关系补链

| 项 | 当前实现 |
| --- | --- |
| 代码 | `core/chunker/linker.py`、`link_related_chunks()` |
| 产物 | `chunk.related_ids`、`chunk.relations` |
| 后续用途 | 普通 RAG related 扩展、图谱预览、写入 `chunk_relations` |

当前关系示例：

- 图表编号引用：正文中的“见图 1-3-3”指向图片 chunk。
- 图片 / 表格与附近正文：`adjacent`。
- 同一 parent 下的媒体块与文本块：`sibling`。
- enhanced 继承或关联对应 child。

注意：

- 这是入库阶段的轻量关系补链。
- Graph RAG 的图扩展主要读取写库后的 `chunk_relations` 和 `entity_mentions`。

### 4.9 草稿预览与确认

| 项 | 当前实现 |
| --- | --- |
| 代码 | `backend/services/chunk_draft_service.py` |
| 表 | `chunk_drafts` |
| API | `/api/ingestion/chunks/preview/{task_id}`、`PUT /api/ingestion/chunks/{draft_id}`、`DELETE /api/ingestion/chunks/{draft_id}`、`POST /api/ingestion/chunks/merge`、`POST /api/ingestion/chunks/confirm/{task_id}` |

链路：

```text
chunk 完成
  -> save_chunk_drafts()
  -> 控制台预览、编辑、删除、合并
  -> 用户确认
  -> load_confirmable_chunks()
  -> quality / embedding / export
```

当前保留字段包括：

- `content`
- `page`
- `strategy`
- `layer`
- `title`
- `parent_id`
- `related_ids`
- `is_table_chunk`
- `is_image_chunk`
- `image_path`
- `enhanced_text`
- `extracted_entities`
- `extracted_triples`
- `relations`

需求挂点：

- 多人协同审核。
- 草稿版本历史。
- 按规则批量删除低价值草稿。
- 图片、表格、正文联动预览。

### 4.10 质量门控

| 项 | 当前实现 |
| --- | --- |
| 阶段 | `quality` |
| 代码 | `core/cleaner/quality_gate.py` |
| 输入 | 确认后的 `Chunk[]` |
| 输出 | passed chunks、discarded count、quality breakdown |

职责：

- 在 embedding 前过滤明显无效的 chunk。
- 避免浪费向量化和写库成本。
- 维护图片、表格等特殊证据的保留逻辑。

边界：

- 清洗是 block 级格式噪声处理。
- 质量门控是 chunk 级知识价值判断。

### 4.11 向量化与后处理关系

| 项 | 当前实现 |
| --- | --- |
| 阶段 | `embedding` |
| 代码 | `core/embedding/client.py` |
| 调用 | `embed_texts_with_metrics()` |
| 输入 | passed chunks 的 `content` |
| 输出 | embeddings、embedding metrics |

当前稳定参数：

```env
LLM_EMBEDDING_BATCH_SIZE=10
LLM_EMBEDDING_MAX_CONCURRENCY=10
LLM_EMBEDDING_MAX_RETRIES=2
```

向量化后同阶段还会补：

| 关系 | 代码 | 产物 |
| --- | --- | --- |
| 语义相似 / 重复 | `core/chunker/semantic_linker.py` | `semantic_similar`、`duplicate_of` |
| 流程关系 | `core/chunker/procedure_linker.py` | `next_step`、`prev_step` |
| 因果关系 | `core/chunker/causal_linker.py` | `cause_of`、`effect_of` |

metrics 关注：

- batch size
- batch count
- max concurrency
- retry count
- embedding wall time
- `linkSemanticMs`
- `linkProcedureMs`
- `linkCausalMs`

### 4.12 实体物化

| 项 | 当前实现 |
| --- | --- |
| 阶段 | export 前半段 |
| 代码 | `core/kg/extraction_pipeline.py`、`core/output/entity_writer.py` |
| 输入 | passed chunks |
| 输出 | `entities`、`entity_mentions` |

链路：

```text
passed chunks
  -> materialize_entities()
  -> entity merge / definition
  -> write_entities()
  -> entities
  -> entity_mentions
```

用途：

- Graph RAG 实体召回。
- 文档 / 知识库图谱预览。
- 后续实体浏览、概念页、知识点目录。

### 4.13 写入 pgvector 与图谱表

| 项 | 当前实现 |
| --- | --- |
| 阶段 | `export` |
| 代码 | `core/output/pgvector_writer.py` |
| 输入 | passed chunks、embeddings、source metadata |
| 输出 | documents、chunks、chunk_relations、kg_triples |

当前写入内容：

- `documents`
- `chunks`
- `chunk_relations`
- `kg_triples`
- `entities`
- `entity_mentions`

写库策略：

- 写库前调用 `ensure_db_schema()` 兜底。
- 文档按 `kb_id + file_hash` 去重。
- 重复文档会刷新文件名、chunk count 和 source metadata。
- chunk 写入可使用 `PGVECTOR_WRITE_MODE=copy`。
- relations 和 triples 也支持批量 / COPY 路径。

metrics 关注：

- `entityMaterializeMs`
- `pgvectorWriteMs`
- `pgvectorChunksWriteMs`
- `pgvectorRelationsWriteMs`
- `pgvectorTriplesWriteMs`

## 5. 在线普通 RAG 链路

### 5.1 API 与适配层

| 项 | 当前实现 |
| --- | --- |
| API | `POST /api/rag/query` |
| route | `backend/routes/rag.py` |
| service | `backend/services/rag_service.py` |
| adapter | `backend/adapters/rag_adapter.py` |
| core | `core/rag/retriever.py`、`reranker.py`、`generator.py`、`scorer.py` |

标准调用：

```text
run_rag_query()
  -> run_rag_pipeline()
  -> HybridRetriever.retrieve()
  -> ParentChildReranker.rerank()
  -> RAGGenerator.generate()
  -> RAGScorer.score()
  -> append_evaluation()
```

### 5.2 查询参数

常见输入：

- `kb_id`
- `query`
- `top_k`
- `min_score`
- `use_llm_check`
- `use_llm_score`
- 可扩展 filters

输出核心字段：

- `answer`
- `citations`
- `candidates`
- `contextWindow`
- `scores`
- `recallChannels`
- `trace`

### 5.3 media_ref 图表编号直达

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._media_ref_retrieve()`、`rag_adapter._build_media_ref_answer()` |
| 识别 | `图1-3-3`、`图 1-3-3`、全角数字、不同横线、`表1-3-1` |
| 查询 | 只查 child 层图片或表格 chunk |
| 命中后跳过 | embedding、BM25、RRF、rerank、LLM 生成 |

用途：

- 处理“检索图 1-3-3”“表 2-1 在哪里”这类导航问题。
- 把图表定位查询压到数据库查询级。

### 5.4 dense 召回

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._dense_retrieve()` |
| 向量 | `embed_texts([query])`，支持进程内 TTL cache |
| 表 | `chunks` |
| 算子 | pgvector `<=>` |
| 层级 | 默认 `child + enhanced` |

默认查询口径：

```sql
WHERE c.kb_id = %s
  AND c.layer = ANY(%s)
ORDER BY c.embedding <=> %s::vector
```

缓存配置：

```env
RAG_QUERY_EMBEDDING_CACHE_TTL_SECONDS=1800
RAG_QUERY_EMBEDDING_CACHE_MAX_SIZE=512
```

### 5.5 BM25 稀疏召回

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._sparse_retrieve()` |
| 索引 | 进程内 BM25 cache |
| tokenizer | 英文数字词、中文单字 |
| 层级 | `child + enhanced` |
| 分数 | 当前结果内归一化 |

用途：

- 术语、编号、标题、原文短语召回。
- 弥补纯向量召回对精确词不敏感的问题。

### 5.6 structured 召回

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._structured_retrieve()` |
| 支持过滤 | `source`、`title_like`、`is_table`、`page_range` |
| 当前边界 | 常规控制台请求暂未传 filters，因此 structured 计数通常为 0 |

需求挂点：

- 高级检索面板。
- 按文档、章节、页码、图表类型过滤。
- 教材目录导航联动问答。

### 5.7 RRF 融合

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._rrf_merge()` |
| 输入 | dense、BM25、structured |
| 公式 | `score += 1 / (k + rank)`，默认 `k=60` |
| 合并键 | `chunk.id` |

融合后：

- 同一 chunk 多路命中会合并。
- `sources` 记录命中通道。
- `dense_score` 保留向量通道痕迹。

### 5.8 enhanced 折回 child

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._fold_enhanced_to_children()` |
| 输入 | 召回候选中可能包含 enhanced |
| 输出 | 最终证据优先回到对应 child |

规则：

- enhanced 只提高可召回性。
- 最终 citation 使用 child 的原文、页码、图片路径和文档信息。
- 响应保留 `matchedEnhancedId`、`matchedEnhancedText`、`matchedBy`。

### 5.9 related 扩展

| 项 | 当前实现 |
| --- | --- |
| 代码 | `HybridRetriever._expand_related()` |
| 数据 | `chunks.related_ids` |
| 分数 | 固定低分，当前约 `0.3` |

用途：

- 正文命中时带出相关图片、表格。
- 图片或表格命中时带出说明正文。
- 这是普通 RAG 的轻量关系扩展，不等于 Graph RAG 图遍历。

### 5.10 重排、上下文与生成

| 阶段 | 代码 | 说明 |
| --- | --- | --- |
| rerank | `core/rag/reranker.py` | `ParentChildReranker`，可调用外部 rerank API |
| context | `backend/adapters/rag_adapter.py`、`core/rag/generator.py` | 构造带编号的上下文窗口 |
| generate | `core/rag/generator.py` | OpenAI 兼容 Chat Completion |
| citation | `core/rag/generator.py` / adapter | 将 `[1]` 映射到真实 context |
| score | `core/rag/scorer.py` | relevance、faithfulness、可选 LLM score |

引用字段：

- `documentName`
- `documentId`
- `page`
- `chunkIndex`
- `location`
- `snippet`
- `chunkId`

前端候选字段：

- `score`
- `denseScore`
- `rerankScore`
- `documentName`
- `page`
- `chunkIndex`
- `location`
- `isImageChunk`
- `imageUrl`
- `matchedBy`
- `matchedEnhancedId`

### 5.11 运行时评测记录

| 项 | 当前实现 |
| --- | --- |
| 入口 | RAG 查询完成后 |
| 查询 | `GET /api/console/evaluations` |
| 内容 | query、answer、relevanceScore、faithfulnessScore、llmScore、cannotAnswer |
| 边界 | 运行时评分记录，不等于离线 benchmark |

## 6. Graph RAG 链路

### 6.1 API 与核心流程

| 项 | 当前实现 |
| --- | --- |
| API | `POST /api/rag/graph-query` |
| service | `backend/services/rag_service.py` |
| adapter | `backend/adapters/rag_adapter.py` |
| core | `core/rag/graph_retriever.py`、`graph_expander.py`、`intent_router.py` |

流程：

```text
query
  -> classify_intent()
  -> query embedding
  -> dense recall
  -> BM25 recall
  -> entity recall
  -> RRF merge
  -> related_ids expand
  -> seed coarse filter
  -> graph_expand()
  -> hydrate chunks / entities
  -> structured context
```

### 6.2 意图路由

常见意图：

- `concept`
- `procedure`
- `data`
- `visual`
- `general`

意图影响：

- 召回策略权重。
- 图扩展关系优先级。
- explain path 的解释重点。

### 6.3 实体召回

| 项 | 当前实现 |
| --- | --- |
| 代码 | `GraphRetriever._entity_retrieve()` |
| 数据 | `entities`、`entity_mentions` |
| 匹配 | 名称、别名、token overlap、可选 entity embedding |
| 输出 | 与实体相关的 chunk seeds |

当 intent 为 `data` 时，当前路径会关闭 entity embedding，只做名称和别名匹配。

### 6.4 图扩展

| 项 | 当前实现 |
| --- | --- |
| 代码 | `core/rag/graph_expander.py` |
| 边 | `chunk_relations` |
| 回跳 | `entity_mentions` |
| 默认最大跳数 | `max_hops=2` |
| 默认邻居上限 | `max_neighbors=50` |

分数组成：

```text
path_weight * relation_weight * hop_decay * intent_relation_priority
```

关系优先级示例：

| intent | 高优先关系 |
| --- | --- |
| `concept` | `mentions`、`sibling`、`semantic_similar` |
| `procedure` | `next_step`、`prev_step`、`sibling` |
| `data` | `refers_to`、`sibling` |
| `visual` | `refers_to`、`adjacent` |
| `general` | `adjacent`、`sibling`、`semantic_similar` |

### 6.5 Graph RAG 输出

当前 Graph RAG 更偏结构化检索，不是普通 RAG 的自然语言生成路径。

输出重点：

- `intent`
- `intentSource`
- `results`
- `stats`
- explain path

统计字段：

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

## 7. 图谱预览链路

图谱预览是入库后的质量检查视图，不参与在线召回排序、Graph RAG 扩展或答案生成。

| 视图 | API | 代码 |
| --- | --- | --- |
| 文档级图谱 | `GET /api/documents/{document_id}/graph` | `backend/routes/knowledge_bases.py`、`backend/adapters/kb_adapter.py` |
| 知识库级图谱 | `GET /api/knowledge-bases/{kb_id}/graph` | `backend/routes/knowledge_bases.py`、`backend/adapters/kb_adapter.py` |
| 前端组件 | 控制台文档详情 / 单库总览 | `frontend/src/components/knowledge-base/document-graph-view.tsx` |

读取数据：

- `chunks`
- `chunk_relations`
- `entities`
- `entity_mentions`
- `kg_triples`

用途：

- 检查图表、正文、表格关系是否建立。
- 检查 entity 和 triple 是否可见。
- 辅助判断 Graph RAG 的底层数据是否完整。

## 8. 控制台链路

| 页面 / 功能 | 目录 | 主要 API |
| --- | --- | --- |
| 总览 | `frontend/src/app/(console)/overview` | overview metrics、alerts、queue |
| 知识库列表 | `frontend/src/app/(console)/knowledge-bases` | knowledge-bases CRUD |
| 单库工作台 | `frontend/src/app/(console)/knowledge-bases/[kbId]` | documents、graph、ingestion、query |
| 全局入库 | `frontend/src/app/(console)/ingestion` | ingestion upload、tasks、stream |
| 问答 | `frontend/src/app/(console)/query` | `/api/rag/query` |
| 评测 | `frontend/src/app/(console)/evaluation` | console evaluations、eval reports |
| 设置 | `frontend/src/app/(console)/settings` | `/api/console/settings` |

关键前端边界：

- 主要可见文案保持简体中文。
- 控制台设置中的敏感字段脱敏展示。
- 当前运行时设置优先级：DB override > `.env` > 代码默认值。
- 入库任务依赖 SSE 与轮询共同更新。

## 9. 配置与运行时覆盖

配置来源：

| 来源 | 说明 |
| --- | --- |
| `.env` / `.env.example` | 本地运行基线 |
| `.env.docker.example` | Docker 运行基线 |
| `config.yaml` | 旧配置和部分默认结构 |
| DB runtime settings | 控制台设置保存后的运行时覆盖 |
| 代码默认值 | 最后兜底 |

关键配置族：

| 类别 | 代表变量 |
| --- | --- |
| 解析 provider | `PDF_PARSER_PROVIDER` |
| 302AI MinerU | `302AI_API_KEY`、`parser.cloud.sharding.*` |
| 官方 MinerU | `MINERU_OFFICIAL_API_TOKEN`、`MINERU_OFFICIAL_MODEL_VERSION`、`MINERU_OFFICIAL_SHARDING_*` |
| Document Mind | `ALIYUN_DOCUMENT_MIND_*`、`ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL` |
| OSS | `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_BUCKET` |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` |
| 增强 key 池 | `LLM_API_KEY_POOL`、`VL_API_KEY_POOL` |
| 三层增强 | `HIERARCHICAL_*` |
| Embedding | `LLM_EMBEDDING_*` |
| 写库 | `PGVECTOR_WRITE_MODE`、`PGVECTOR_WRITE_PAGE_SIZE` |
| 在线召回 | `RAG_QUERY_EMBEDDING_CACHE_*` |

安全边界：

- API Key、AK/SK、OSS 密钥等不得明文返回前端。
- 控制台设置以 `sensitive`、`hasValue` 和脱敏值表达。
- metrics 中 key 只记录匿名编号。

## 10. 观测指标总表

| 阶段 | 关键指标 |
| --- | --- |
| upload | 文件大小、源文件路径、准备耗时 |
| parse | provider、shardCount、completedShards、pollCount、parseWallMs、outputBlocks、outputImageBlocks、outputTableBlocks |
| clean | 输入块、输出块、移除原因、清洗耗时 |
| chunk | chunkBaseMs、enhanceWallMs、enhanceTasks、enhanceFailures、enhancePeakConcurrency、linkRelationsMs |
| quality | 输入 chunk、通过 chunk、丢弃数、质量 breakdown |
| embedding | batch size、batch count、max concurrency、retry count、embedding wall time、linkSemanticMs、linkProcedureMs、linkCausalMs |
| export | entityMaterializeMs、pgvectorWriteMs、chunks/relations/triples write ms |
| retrieval | dense、sparse、structured、RRF、related、query embedding cache hit |
| rerank | rerank latency、fallback 情况 |
| generation | LLM token usage、answer、citations、cannotAnswer |
| Graph RAG | recall_counts、after_fusion、after_expand、latency_ms |

## 11. 离线 benchmark 与运行时评分边界

| 类型 | 入口 | 用途 |
| --- | --- | --- |
| 运行时评分记录 | `/api/console/evaluations` | 记录真实问答操作的 relevance、faithfulness、llmScore 等 |
| 离线 benchmark | `/api/eval/reports`、`core/eval/*` | 用数据集比较策略，计算 recall@5、MRR、nDCG 等 |

注意：

- 运行时评分不等于正式评测。
- 离线 benchmark 当前是基础框架，真实人工标注数据集仍需补齐。

## 12. 后续需求结合建议

新增需求时，建议先定位它作用在哪个链路节点：

| 需求类型 | 优先查看 |
| --- | --- |
| 新解析 provider | `core/parser/provider.py`、`docs/pipeline/parser-provider-poc.md`、本文件 4.3 到 4.5 |
| 大文件解析优化 | `core/parser/pdf_sharding.py`、`docs/performance-optimizations/parse/README.md` |
| 清洗规则中心 | `core/cleaner/`、`docs/rule/cleaner-rules.md` |
| 切片策略调整 | `core/chunker/`、`docs/pipeline/three-layer-chunking-final-solution.md` |
| 入库审核增强 | `backend/services/chunk_draft_service.py`、`chunk_drafts` |
| 向量化优化 | `core/embedding/client.py`、`docs/performance-optimizations/embedding/README.md` |
| pgvector 写库优化 | `core/output/pgvector_writer.py`、`docs/performance-optimizations/embedding/README.md` |
| 普通 RAG 召回改造 | `core/rag/retriever.py`、`docs/pipeline/online-retrieval.md` |
| Graph RAG 扩展 | `core/rag/graph_retriever.py`、`core/rag/graph_expander.py`、`chunk_relations`、`entities` |
| 答案可信度 | `core/rag/generator.py`、`core/rag/scorer.py` |
| 图谱工作台 | `backend/adapters/kb_adapter.py`、`document-graph-view.tsx` |
| 控制台设置 | `core/runtime_settings.py`、`backend/services/console_service.py` |

需求落地时建议同步确认：

1. 是否改变输入输出模型。
2. 是否需要新增数据库字段或迁移。
3. 是否影响 `ContentBlock`、`Chunk`、`Entity` 或 RAG candidate payload。
4. 是否需要新增 stage metrics。
5. 是否影响控制台中文文案。
6. 是否需要补 `docs/pipeline/` 当前链路说明。
7. 是否属于性能实验，若是则归档到 `docs/performance-optimizations/`。

## 13. 相关文档

- [离线入库链路](./offline-ingestion-pipeline.md)
- [在线检索增强问答链路](./online-rag-pipeline.md)
- [在线召回](./online-retrieval.md)
- [解析 provider 对比](./parser-provider-poc.md)
- [官方 MinerU provider](./mineru-official-parser.md)
- [Document Mind 分片解析](./document-mind-sharding.md)
- [三层切片最终技术方案](./three-layer-chunking-final-solution.md)
- [文档图谱预览](./document-graph-preview.md)
- [性能优化档案](../performance-optimizations/README.md)
- [文档归类地图](../document-map.md)
