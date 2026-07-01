# RAG Performance And Graph Evolution Implementation Plan

**Goal:** 围绕教材级知识库，形成一条兼顾入库性能、召回稳定性和 Graph RAG 演进的分阶段技术路线。

**Architecture:** 当前项目已经具备 MinerU 云端解析、阿里 Document Mind POC、三层切片、pgvector/BM25 混合召回、Graph RAG 初版和控制台工作台。后续路线不推翻现有链路，而是在现有 v6.0 主线之上补齐解析分流、后台增强、Graph RAG 降级、召回路由和评测闭环。

**Tech Stack:** Python 后端、MinerU/可切换 Parser Provider、PyMuPDF/pdfplumber 本地解析、PostgreSQL/pgvector、Redis 任务状态、Next.js 控制台、Graph RAG 数据表 `entity_mentions` / `chunk_relations` / `kg_triples`。

---

## 1. 当前项目技术方向

本计划应服从当前项目方向：

- 项目已经不是单点 PDF RAG 验证，而是“教材知识库 + 可控入库 + 在线问答 + Graph RAG + 控制台”的完整系统。
- v6.0 主线重点是可控入库、配置控制面、规则/图谱工作台。
- 10-05 已完成 MinerU 大文件分片解析：PDF 体检、本地拆 shard、多 worker 云解析、全局页码合并。
- 10-06 已完成 Parser Provider 可切换：保留 302AI MinerU 默认 provider，新增阿里 Document Mind POC，通过 `PDF_PARSER_PROVIDER` 显式切换。
- 10-06b 已完成 Document Mind 大文件分片解析：超过 150MB 或 50 页时自动拆 shard，并发提交后按原始全局页码合并。
- 10-08 / 10-08b 已完成三层切片严格等价性能优化：入库前有序并发 enhanced、worker 内 client 复用、linker 去重热路径与 `chunkTimings` 扩展已经落地。
- 10-09 向量化性能优化已完成计划，尚未执行。
- v5.0 Graph RAG 已有初版：实体层、关系层、流程/因果链、Graph 检索与离线评测基础。
- 当前在线普通问答默认是混合 RAG：`media_ref` 短路、dense、BM25、RRF、`child + enhanced`、enhanced 折回 child、related 扩展、rerank、生成。

因此后续不是“重写系统”，而是把系统从同步批处理式入库，演进为：

```text
基础入库快速可用
-> 增强和图谱后台渐进补齐
-> 问答按问题类型自动路由
-> 统一评测和可观测性控制效果
```

## 2. 总体目标

### 2.1 性能目标

- 文字型 PDF 不再默认全量阻塞在云端 MinerU。
- 大文件解析失败后支持局部恢复，不需要整本教材重跑。
- 基础问答可用时间明显早于“全量 enhanced + 全量图谱完成”时间。
- 用户能在控制台看到真实阶段进度，而不是只看到一个长时间 pending/running。

### 2.2 质量目标

- 保留普通混合 RAG 作为默认稳定召回路径。
- Graph RAG 只在流程、因果、概念关系、跨章节综合问题中增强召回。
- Graph RAG 覆盖不足时自动降级，不假装图谱完整。
- 最终答案仍以 child 证据为主，保持页码、文档名、图表和引用可回溯。

### 2.3 演进目标

- Parser Provider 可替换，但 MinerU 继续作为默认可用路径。
- enhanced、实体、关系、三元组均支持异步、幂等、可重试、可版本化。
- 召回策略可评测、可解释、可灰度，而不是依赖感觉调参。

## 3. 推荐阶段拆分

```text
Phase A: 解析层性能与 Provider 路由（部分完成）
Phase B: 基础入库与增强异步化（严格等价并发增强已完成，ready_basic 后台模式未完成）
Phase C: Graph RAG 渐进构建与降级
Phase D: 问题意图路由与混合召回编排
Phase E: 评测、观测与控制台闭环
```

建议优先级：

```text
Phase A -> Phase B -> Phase E 基础观测
然后并行推进 Phase C / Phase D
最后补齐 Phase E 的 benchmark 和运营视图
```

## 4. Phase A: 解析层性能与 Provider 路由

### 4.1 目标

降低云端 MinerU 对入库链路的阻塞，把解析层从单一云端路径改为“本地快速解析 + 云端重解析 + Provider POC”的可切换结构。

### 4.2 范围

保留当前 10-05 分片 MinerU 与 10-06b Document Mind 分片能力，同时新增：

- PDF 体检结果持久化或至少写入任务详情。
- 本地快速解析候选路径。
- 疑难页云端兜底策略。
- Provider 路由规则。
- Provider A/B 对比记录。

### 4.3 建议触达文件

- `core/parser/mineru_parser.py`
- `core/parser/provider.py`
- `core/parser/document_mind_parser.py`
- `backend/services/ingestion_service.py`
- `core/runtime_settings.py`
- `backend/services/console_service.py`
- `docs/pipeline/offline-ingestion-pipeline.md`
- `docs/pipeline/parser-provider-poc.md`
- `tests/test_cloud_parser.py`
- `tests/test_parser_provider.py`
- `tests/test_document_mind_parser.py`

### 4.4 任务清单

#### A1. 解析体检指标定义

定义 PDF 体检输出结构：

```text
page_count
file_size_mb
text_layer_ratio
image_page_ratio
table_candidate_ratio
scan_likelihood
recommended_parse_mode
```

建议体检判断：

- 文本层密度高：`local_text_first`
- 扫描概率高：`cloud_ocr`
- 图表密集：`cloud_layout_or_table_provider`
- 超大教材：`sharded_cloud`

验收：

- 同一 PDF 可输出稳定体检结果。
- 控制台任务详情能看到推荐解析模式。
- 体检结果不改变现有 MinerU 默认行为，先只作为观测和路由依据。

#### A2. 本地快速解析 POC

为文字型 PDF 增加本地解析路径，优先使用 PyMuPDF，必要时评估 pdfplumber。

本地快速解析第一版只承诺：

- 页码准确。
- 正文文本提取。
- 基础标题/段落块。
- 保留 `source_file`。

第一版不强求：

- 完整表格结构还原。
- 复杂阅读顺序完美。
- 图片切片完整落图。

验收：

- 文字型 PDF 不调用云端即可产出 `ContentBlock`。
- 输出能进入现有 clean/chunk/embedding/write DB 链路。
- 对扫描件自动拒绝本地快速解析，回退云端路径。

#### A3. MinerU shard 状态持久化设计

把当前内部 shard 执行单元升级为可恢复状态。

建议状态字段：

```text
document_id / task_id
shard_index
page_start
page_end
oss_object
cloud_task_id
status
result_url
local_output_dir
started_at
finished_at
error_message
retry_count
```

验收：

- 任一 shard 失败后能定位失败页码范围。
- 后续实现可只重试失败 shard。
- 前端进度可从“整个 parse 阶段”细化到 shard 级。

#### A4. Provider 路由策略

在已完成的 10-06 Provider 切换基础上，扩展路由概念：

```text
explicit provider:
  PDF_PARSER_PROVIDER=mineru | ali_document_mind | local_text

future auto strategy:
  PDF_PARSER_PROVIDER=auto
```

第一版建议不启用自动 fallback。自动路由只作为后续设计：

```text
if text_layer_ratio high:
  local_text
elif scan_likelihood high:
  mineru or document_mind
elif table_candidate_ratio high:
  table-capable provider
else:
  mineru
```

验收：

- 显式 provider 行为清晰。
- 日志和 task payload 记录实际 provider。
- POC 文档能比较不同 provider 的耗时与质量。

### 4.5 风险

- 本地解析速度快，但结构质量可能低于 MinerU。
- Provider 输出格式差异会增加 `ContentBlock` 映射成本。
- 自动路由过早上线会让问题归因复杂。

### 4.6 建议排期

- A1/A4：1-2 天。
- A2：2-4 天 POC。
- A3：2-3 天设计与实现。
- Provider A/B：持续补数据，不阻塞主链路。

## 5. Phase B: 基础入库与增强异步化

### 5.1 目标

把入库从“全量处理完成才可用”改为“基础入库先可问答，enhanced 和图谱后台补齐”。

### 5.2 当前问题与已完成部分

当前三层切片设计合理：

```text
parent / child / enhanced
```

10-08 / 10-08b 已经先落地严格等价口径下的性能优化：

- `HIERARCHICAL_ENHANCE_MODE=parallel_ordered`：基础切片后规划 enhanced slot，text/table/image 分池有限并发执行，再按原顺序合并。
- `HIERARCHICAL_REUSE_LLM_CLIENTS=true`：worker 线程内复用同一 provider/base_url 的 OpenAI 兼容 client。
- `link_related_chunks()` relation 去重热路径优化。
- `chunkTimings` 扩展：记录 enhanced 墙钟耗时、任务数、失败数、client 复用标记和 `linkRelationsMs`。

仍未完成的性能方向主要在：

- enhanced LLM 生成。
- enhanced embedding。
- 实体/关系/三元组抽取。
- 大教材 chunk 数量导致线性放大。

如果一本书已经产生数千个切片，例如 7890 个 child / table / image 级切片，Phase B 不能只做“全量异步并行”。全量逐条异步增强仍会形成很长后台队列，并可能触发 LLM / VL / embedding 限流、成本放大和 worker 长时间被单本文档占用。Phase B 的默认方向应是：

```text
基础入库先可用
-> 后台有限并发增强
-> P0/P1 高价值切片优先
-> 查询反馈与评测失败驱动 P2/P3 补强
```

### 5.3 建议触达文件

- `core/chunker/`
- `core/output/pgvector_writer.py`
- `backend/services/ingestion_service.py`
- `backend/services/task_store.py`
- `core/rag/retriever.py`
- `core/rag/graph_expander.py`
- `core/rag/graph_retriever.py`
- `core/db/schema.py`
- `tests/test_chunker.py`
- `tests/test_pgvector_writer.py`
- `tests/test_rag_retriever.py`
- `tests/test_backend_app.py`

### 5.4 任务清单

#### B1. 入库状态拆分

新增或约定文档级状态：

```text
ready_basic
enhancing
ready_enhanced
enhance_failed
```

图谱状态：

```text
graph_pending
graph_building
graph_partial
graph_ready
graph_failed
```

验收：

- 文档基础切片和 child embedding 完成后即可进入 `ready_basic`。
- `ready_basic` 文档允许普通问答。
- enhanced 未完成时，召回自动只检索 child。

#### B2. enhanced 生成后台任务

把 enhanced 生成和 enhanced embedding 从主入库链路移出。

建议后台任务：

```text
select pending child chunks
-> lightweight enhanced
-> batch LLM enhanced
-> enhanced embedding
-> write enhanced chunks
-> update document enhance_status
```

验收：

- 后台任务失败不影响基础问答。
- 可重试失败 chunk。
- enhanced 写入后普通召回自动升级到 `child + enhanced`。

#### B2a. 后台增强队列、限流与批处理

后台增强不能按 chunk 数直接并发。建议设计 document 级任务 + batch 级子任务：

```text
enhance_job(document_id)
  -> build priority batches
  -> limited workers consume batches
  -> write enhanced / entity / triple / relation idempotently
```

推荐并发上限：

```text
text enhanced workers: 3-6
table enhanced workers: 1-2
image / VL enhanced workers: 1
embedding batch workers: 1-2
```

每批建议 5-10 个 child，避免 JSON 返回过长导致解析失败或漏项。batch 状态建议包含：

```text
batch_id
document_id
priority
chunk_ids
status
retry_count
provider
model
prompt_version
started_at
finished_at
error_message
```

验收：

- 一本 7890 切片级别的大书不会独占所有增强 worker。
- 单个 batch 失败后只重试失败 batch / chunk。
- 增强写库、实体写库、三元组写库和关系写库具备幂等键。
- 控制台或任务日志能看到增强总数、完成数、失败数和当前优先级。

#### B3. 轻量增强同步化

同步阶段只做不依赖 LLM 的轻量增强：

- 标题路径。
- 页码。
- 章节层级。
- 图表编号。
- 邻接 chunk id。
- 文档名和 chunk index。

验收：

- 这些字段进入 child metadata 或 related metadata。
- 不显著增加入库耗时。

#### B4. 重型增强按需化

不要全量 child 默认重增强。优先增强：

- 标题、定义、结论。
- 图表附近 chunk。
- 表格 chunk。
- 流程、因果、步骤类段落。
- 高频召回或用户问过的区域。
- 评测失败问题相关 chunk。

验收：

- 有明确 selection policy。
- 后台任务能按优先级处理。
- 普通正文可延后增强。

建议 selection policy 第一版显式拆成：

```text
P0: table / image / fragment / 图表引用附近 chunk
P1: 标题、定义、结论、流程、因果、步骤类 chunk
P2: 用户问过但召回差、评测失败、被多次引用的 chunk
P3: 普通连续正文，按需或低峰补跑
```

第一轮后台增强优先完成 P0/P1，先让用户感知 enhanced 收益；P2/P3 作为持续补强队列，不阻塞文档进入 `ready_basic`。

#### B5. enhanced 缓存与版本

缓存键建议：

```text
document_hash
chunk_content_hash
enhance_prompt_version
model_name
strategy
```

验收：

- 相同输入不重复调用 LLM。
- prompt 或模型升级时可以重新生成。
- 可追踪 enhanced 来源版本。

#### B6. 查询反馈驱动补强

基础问答可用后，用真实查询和评测结果驱动后续增强：

- 召回为空或置信度低的问题，反向定位相关章节并提升增强优先级。
- rerank 前后分歧大的候选，标记为需要 enhanced 或关系抽取。
- 评测失败的问题，把 gold / expected evidence 附近 chunk 放入 P2 队列。
- 高频访问章节优先补图谱，低频普通正文延后到低峰任务。

验收：

- 增强队列可以从查询日志 / 评测失败记录追加任务。
- 同一 chunk 已有相同版本 enhanced 时不重复入队。
- 后续增强预算优先投入实际影响问答质量的位置。

### 5.5 风险

- 状态过多会增加前后端理解成本。
- 后台任务如果不可观测，会变成隐藏失败。
- enhanced 延后后，用户早期问答质量可能低于全量增强完成后。
- 如果只做“全量异步并行”而没有优先级和限流，后台任务仍可能在大书上长期拥堵。
- selection policy 会改变“所有 child 立即拥有 enhanced”的旧假设，需要在文档状态和召回降级里明确表达。

### 5.6 建议排期

- B1：1-2 天。
- B2/B2a：4-6 天。
- B3/B4：2-3 天。
- B5：2 天。
- B6：2-3 天，可在查询日志和评测链路稳定后补做。

## 6. Phase C: Graph RAG 渐进构建与降级

### 6.1 目标

让 Graph RAG 成为可增量构建、可降级、可解释的增强召回路径，而不是阻塞入库的同步环节。

### 6.2 设计原则

```text
Graph RAG 不替代 Hybrid RAG。
Hybrid RAG 保底准确率。
Graph RAG 补复杂关系召回。
```

### 6.3 建议触达文件

- `core/rag/intent_router.py`
- `core/rag/graph_retriever.py`
- `core/rag/graph_expander.py`
- `backend/services/rag_service.py`
- `backend/routes/rag.py`
- `backend/adapters/rag_adapter.py`
- `backend/services/document_graph_service.py`
- `backend/adapters/kb_adapter.py`
- `frontend/src/components/knowledge-base/document-graph-view.tsx`
- `tests/test_document_graph_service.py`
- `tests/test_rag_retriever.py`
- `tests/test_backend_app.py`

### 6.4 任务清单

#### C1. 图谱构建任务拆分

后台图谱流水线：

```text
entity extraction
-> relation extraction
-> kg triple normalization
-> graph metadata/index update
-> graph quality check
```

每一步具备：

- 独立状态。
- 独立重试。
- 幂等写入。
- 版本标记。
- 耗时记录。

验收：

- 某一步失败不影响基础问答。
- 可单独重跑 relation extraction。
- 可查询图谱构建进度。

#### C2. 图谱边幂等键

建议关系去重键：

```text
document_id
chunk_id
extractor_version
relation_type
source_entity
target_entity
evidence_hash
```

验收：

- 重试不会无限插入重复边。
- 新版本抽取可与旧版本区分。
- 可按版本回滚或重建。

#### C3. Graph RAG 降级语义

Graph query 响应增加状态语义：

```text
graphStatus
graphCoverage
relationCount
pathCount
fallbackUsed
```

降级规则：

```text
graph_ready:
  graph expand + hybrid candidates

graph_partial:
  limited graph expand + hybrid fallback

graph_pending / graph_failed:
  hybrid fallback only
```

验收：

- 图谱未完成时不返回误导性的完整 Graph RAG 结果。
- 前端能显示当前是图谱增强还是普通召回降级。

#### C4. 图谱扩展边界

按问题类型限制图扩展：

```text
定义型:
  0-1 hop

流程型:
  next_step / prev_step

因果型:
  cause_of / effect_of

概念关系型:
  part_of / belongs_to / similar_to / prerequisite
```

验收：

- Graph RAG 不对所有问题无差别扩展。
- 简单事实题不会被图谱噪声明显干扰。
- 流程/因果题能返回路径证据。

### 6.5 风险

- 图谱抽取质量低时，Graph RAG 会召回相关但不直接回答的内容。
- 关系类型过多会增加规则维护成本。
- 图谱覆盖状态如果不透明，用户会误以为结果完整。

### 6.6 建议排期

- C1/C2：3-5 天。
- C3：2-3 天。
- C4：2-4 天，依赖评测集。

## 7. Phase D: 问题意图路由与混合召回编排

### 7.1 目标

让系统根据问题类型选择合适召回策略：

```text
定义 / 原文 / 图表定位 / 单点知识:
  Hybrid RAG

流程 / 因果 / 概念关系 / 跨章节综合:
  Hybrid RAG + Graph RAG

图表编号定位:
  media_ref 短路
```

### 7.2 建议触达文件

- `core/rag/intent_router.py`
- `core/rag/retriever.py`
- `core/rag/graph_retriever.py`
- `core/rag/reranker.py`
- `backend/adapters/rag_adapter.py`
- `backend/services/rag_service.py`
- `docs/pipeline/online-retrieval.md`
- `docs/pipeline/online-rag-pipeline.md`
- `tests/test_rag_retriever.py`
- `tests/test_rag_adapter.py`

### 7.3 任务清单

#### D1. 问题类型分类

建议问题类型：

```text
media_location
definition
fact_lookup
chapter_summary
process
causal
comparison
concept_relation
cross_chapter_synthesis
unknown
```

第一版优先规则分类，LLM 分类可后置。

验收：

- 图/表编号稳定命中 `media_location`。
- “是什么/定义”稳定命中 `definition`。
- “为什么/导致”稳定命中 `causal`。
- “步骤/过程/流程”稳定命中 `process`。

#### D2. 召回策略矩阵

建立策略表：

| 问题类型 | 主召回 | Graph 扩展 | rerank | 备注 |
| --- | --- | --- | --- | --- |
| media_location | media_ref | 否 | 否 | 直接返回定位 |
| definition | dense + BM25 | 默认否 | 是 | 避免图谱扩散 |
| fact_lookup | dense + BM25 | 默认否 | 是 | 传统 RAG 保底 |
| process | dense + BM25 | 是 | 是 | 沿流程边 |
| causal | dense + BM25 | 是 | 是 | 沿因果边 |
| concept_relation | dense + BM25 | 是 | 是 | 限制 hop |
| cross_chapter_synthesis | dense + BM25 | 是 | 是 | 增加覆盖但控噪 |

验收：

- 每类问题有明确策略。
- Graph RAG 不再无差别触发。
- 所有非短路结果最终经过统一 rerank/scorer。

#### D3. 候选合并协议

候选来源统一标记：

```text
sources:
  embedding
  bm25
  structured
  related
  enhanced
  graph_entity
  graph_relation
  media_ref
```

验收：

- 前端和日志能解释候选来自哪里。
- RRF/rerank 前后保留 source 信息。
- Graph 扩展候选不会覆盖原始 child 证据。

#### D4. 引用校验

最终答案引用仍优先落在 child：

```text
enhanced hit -> child citation
graph hit -> evidence child citation
media_ref hit -> image/table child citation
```

验收：

- citations 中有文档名、页码、chunk index。
- enhanced 和 graph 只作为召回/扩展线索，不直接替代教材证据。

### 7.4 风险

- intent router 误判会导致策略错误。
- Graph 扩展和 related 扩展同时开启时可能候选过宽。
- 前端召回通道计数需要重新解释。

### 7.5 建议排期

- D1/D2：2-3 天。
- D3/D4：2-4 天。
- 与 Phase E 的评测集联动调参。

## 8. Phase E: 评测、观测与控制台闭环

### 8.1 目标

用真实指标决定策略有效性，避免只凭主观体验判断“更快/更准”。

### 8.2 建议触达文件

- `backend/services/console_service.py`
- `backend/routes/console.py`
- `backend/services/rag_service.py`
- `docs/eval/`
- `docs/pipeline/online-retrieval.md`
- `docs/pipeline/online-rag-pipeline.md`
- `frontend/src/app/(console)/settings/page.tsx`
- `frontend/src/app/(console)/knowledge-bases/[kbId]/query/page.tsx`
- `tests/test_api_console.py`
- `tests/test_runtime_settings.py`
- `tests/test_backend_app.py`

### 8.3 任务清单

#### E1. 入库性能指标

每个任务记录：

```text
upload_ms
pdf_inspect_ms
parse_submit_ms
parse_poll_ms
parse_download_ms
parse_merge_ms
clean_ms
chunk_ms
child_embedding_ms
write_db_ms
enhance_ms
graph_build_ms
total_to_ready_basic_ms
total_to_ready_enhanced_ms
total_to_graph_ready_ms
```

验收：

- 能区分“基础可用时间”和“增强完成时间”。
- 能定位 MinerU、embedding、enhanced、graph 哪个阶段慢。

#### E2. 召回质量评测集

建立教材问题集：

```text
definition
fact_lookup
media_location
table_lookup
process
causal
concept_relation
cross_chapter_synthesis
cannot_answer
```

每条记录至少包含：

```text
question
expected_answer
expected_citation
expected_page
question_type
allowed_relation_types
```

验收：

- 普通 RAG 和 Graph RAG 可在同一问题集上对比。
- 能看出 Graph RAG 对复杂问题的收益和对简单问题的噪声。

#### E3. 指标定义

建议指标：

```text
recall@k
citation_hit_rate
answer_faithfulness
cannot_answer_accuracy
media_ref_latency
graph_path_precision
graph_coverage
rerank_selected_source_distribution
```

验收：

- 不只看答案文本，也看引用是否命中正确页码/图表。
- Graph RAG 单独评估路径质量。

#### E4. 控制台展示

控制台应展示：

- 当前 parser provider。
- 当前解析模式。
- 基础入库状态。
- enhanced 状态。
- graph 状态。
- shard 进度。
- 召回通道来源。
- 是否使用 Graph fallback。

验收：

- 用户能理解“文档已可问答，但图谱仍在构建”。
- 用户能理解某次问答是普通 RAG、Graph RAG，还是降级结果。

### 8.4 风险

- 指标太多会让控制台复杂。
- 没有真实教材样本时，评测结果不可靠。
- Graph RAG 的收益必须分问题类型看，不能只看平均分。

### 8.5 建议排期

- E1：2-3 天。
- E2/E3：3-5 天初版。
- E4：3-5 天，视前端展示范围而定。

## 9. 推荐里程碑方案

### Milestone 1: 解析阻塞缓解

目标：让入库不再完全受 MinerU 云端阻塞。

包含：

- A1 解析体检。
- A2 本地快速解析 POC。
- A4 Provider 路由继续接 10-06。
- E1 基础耗时观测。

退出标准：

- 文字型 PDF 可本地快速解析进入基础问答。
- 扫描件仍稳定走 MinerU。
- 任务详情能看到各阶段耗时。

### Milestone 2: 基础可用与增强解耦

目标：文档先可问答，enhanced 和 graph 后台补齐。

包含：

- B1 状态拆分。
- B2 enhanced 后台任务。
- B2a 后台增强队列、限流与批处理。
- B3 轻量增强。
- B4 重型增强按需化。
- B5 enhanced 缓存与版本。
- B6 查询反馈驱动补强。
- C1 图谱构建任务拆分设计。

退出标准：

- `ready_basic` 后可直接问答。
- enhanced 失败不导致整本文档不可用。
- Graph 状态可见。
- 7890 切片级别的大书不会全量异步盲跑，而是按优先级、batch、有限 worker 渐进补强。

### Milestone 3: Graph RAG 受控增强

目标：Graph RAG 只在合适问题中扩展，并能降级。

包含：

- C2 幂等关系边。
- C3 Graph 降级。
- C4 图谱扩展边界。
- D1/D2 intent router 与策略矩阵。

退出标准：

- 定义题默认不被 Graph 过度扩展。
- 流程/因果题能返回路径证据。
- 图谱未完成时自动 fallback。

### Milestone 4: 质量评测闭环

目标：用问题集和指标驱动调参。

包含：

- E2 教材问题评测集。
- E3 指标定义。
- D3/D4 候选合并与引用校验。
- E4 控制台展示。

退出标准：

- 普通 RAG、Graph RAG、混合策略可对比。
- 每次策略调整能看到准确率、引用命中率和耗时变化。

## 10. 不建议第一阶段做的事

- 不建议直接废弃 MinerU。
- 不建议第一版启用自动 fallback，因为会混淆 Provider A/B 结果。
- 不建议让 Graph RAG 替代普通 RAG。
- 不建议把所有 enhanced、实体、关系抽取都同步塞回入库主链路。
- 不建议一开始做完整 UI 大改，先把状态和指标打通。
- 不建议只看平均准确率，教材场景必须分问题类型看。

## 11. 关键技术决策

### 11.1 Parser Provider 决策

默认：

```text
PDF_PARSER_PROVIDER=mineru
```

短期：

```text
PDF_PARSER_PROVIDER=local_text
PDF_PARSER_PROVIDER=ali_document_mind
```

中期：

```text
PDF_PARSER_PROVIDER=auto
```

但 `auto` 必须在 A/B 数据充分后再启用。

### 11.2 入库状态决策

基础状态和增强状态必须分离：

```text
ready_basic != ready_enhanced != graph_ready
```

否则系统会继续被最慢的增强/图谱阶段拖住。

### 11.3 召回策略决策

默认策略：

```text
Hybrid RAG first
Graph RAG when useful
Reranker/scorer final
Child citation always
```

### 11.4 Graph RAG 决策

Graph RAG 的目标不是提升所有问题准确率，而是提升：

- 多跳问题完整性。
- 因果/流程解释。
- 概念关系召回。
- 跨章节组织能力。

简单事实问题仍由传统混合 RAG 保底。

## 12. 验证矩阵

| 维度 | 需要验证的问题 |
| --- | --- |
| 解析性能 | 文字型 PDF 是否明显快于 MinerU 全量解析 |
| 解析质量 | 本地解析是否保留足够页码、标题、正文证据 |
| 大文件恢复 | 单个 shard 失败是否可定位、可重试 |
| 基础可用 | ready_basic 是否能正常问答 |
| enhanced 收益 | enhanced 完成后 recall@k 是否提升 |
| Graph 收益 | 流程/因果/关系题是否比 Hybrid RAG 更完整 |
| Graph 风险 | 定义题是否被 Graph 扩展干扰 |
| 引用质量 | 最终 citations 是否落在正确 child/page |
| 用户体验 | 控制台是否能解释当前状态和降级行为 |

## 13. 建议下一步

当前主线已经完成其中一部分路线，并继续保留两个待推进方向：

```text
10-07 在线 RAG 召回性能优化（planned）
10-09 向量化性能优化（planned）
```

建议下一步优先在 10-07 与 10-09 中二选一：

- 如果先压在线问答耗时，执行 10-07，解决 `/api/rag/query` 热路径里的多次查库、BM25 冷启动和 enhanced/related 回查问题。
- 如果先压入库后半段耗时，执行 10-09，为入库 `embed_texts()` 增加有界并发 batch，并为 query embedding 增加进程内 TTL cache。

10-08 的严格等价优化已经完成；`ready_basic`、后台增强优先级队列、batch LLM、enhanced cache、查询反馈补强和图谱渐进补齐仍应作为后续显式模式规划，避免和严格等价入库路径混在一起。
