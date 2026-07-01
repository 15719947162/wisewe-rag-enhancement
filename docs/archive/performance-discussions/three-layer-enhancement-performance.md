# 三层切片增强性能与知识图谱演进

## 背景

当前项目的三层切片口径是：

```text
parent / child / enhanced
```

其中：

- `child` 是最终问答引用和证据回溯的主证据单元。
- `parent` 用于章节结构、上下文导航和结构分组。
- `enhanced` 用于提高召回可见性，命中后需要折回对应 `child` 作为最终证据。

当前在线召回默认检索 `child + enhanced`，但最终引用优先回到 `child`。这个设计方向是合理的，但如果 `enhanced` 生成、向量化、实体抽取、关系抽取全部同步阻塞入库，会让大教材入库耗时被明显放大。

## 当前性能问题

三层增强慢，主要慢在 `enhanced` 和图谱前置处理，而不是 `parent / child` 切片本身。

典型耗时来源：

1. 每个 `child` 都调用 LLM 生成 enhanced 文本。
2. enhanced 生成后还需要 embedding。
3. 如果同步抽取实体、关系、三元组，会继续放大耗时。
4. 大教材 chunk 数量多，LLM 调用量近似随 chunk 数线性增长。
5. 同步失败会阻塞整本文档进入可用状态。

### 大切片量下的额外约束

如果一本书已经产生数千个切片，例如 7890 个 child / media / table 级切片，仅仅把 enhanced 从同步改成异步还不够。全量逐条异步增强只会把“入库慢”转移成“后台队列长期堆积”，并可能带来：

- LLM / VL / embedding 接口限流。
- token 成本线性放大。
- 后台任务长时间占用 worker，影响后续文档。
- 增强完成时间不可预测，用户看不到哪些能力已经可用。
- 失败重试时重复生成，进一步放大耗时和成本。

因此大切片量场景的目标不应是“把所有 child 尽快全量增强完”，而应是“文档先进入可问答状态，再按优先级持续补齐最有价值的增强和图谱信息”。

## 推荐原则

核心原则是：

```text
基础入库先可用，增强和图谱后台补齐。
```

也就是把链路拆成：

```text
必须同步完成：
PDF 解析 -> 清洗 -> parent/child 切片 -> child embedding -> 入库可检索

可以异步补全：
enhanced 生成 -> enhanced embedding -> entity/relation 抽取 -> graph 边补全 -> 质量评分
```

这样文档可以先进入基础问答状态，再逐步升级为增强问答和 Graph RAG 状态。

## 推荐状态模型

文档级状态可以拆成：

```text
document.status:
  ready_basic
  enhancing
  ready_enhanced
  enhance_failed

document.graph_status:
  pending
  building
  partial
  ready
  failed
```

切片级状态可以拆成：

```text
chunk.enhance_status:
  pending
  ready
  failed

chunk.graph_status:
  pending
  ready
  failed
```

在线召回应支持降级：

```text
ready_basic:
  检索 child

ready_enhanced:
  检索 child + enhanced

graph_building / graph_partial:
  Graph RAG 返回 graphCoverage 或 graphStatus，并降级使用普通混合召回
```

## 优化方案

### 方案一：增强异步化

基础入库完成后，文档立即可检索、可问答。后台增强任务继续生成 enhanced、embedding 和相关元数据。

推荐链路：

```text
parse
-> clean
-> chunk(parent/child)
-> child embedding
-> write DB
-> ready_basic

background enhancement job:
-> select child chunks
-> lightweight enhanced
-> batch LLM enhanced
-> enhanced embedding
-> write enhanced chunks
-> update enhance status
```

异步增强必须配合有限并发与队列限流。推荐第一版采用固定 worker 池，而不是按 chunk 数直接并发：

```text
text enhanced workers: 3-6
table enhanced workers: 1-2
image / VL enhanced workers: 1
embedding batch workers: 1-2
```

每个 worker 从优先级队列领取 batch，按 document / provider / model 维度做限流，避免一个 7890 切片的大书长期独占后台资源。

### 当前已落地的严格等价第一阶段

10-08 第一阶段先落地 `parallel_ordered`，用于解决“切片阶段被逐条 enhanced 外呼阻塞”的主瓶颈：

```text
chunk_base(parent/child/table/image)
  -> 为每个原本会增强的 child/table/image 预留 enhanced slot
  -> text/fragment/table/image 动态借用式有限并发执行
  -> worker 完成顺序不参与最终排序
  -> 按原 slot 顺序合并 chunks
  -> link_related_chunks()
```

该模式不启用 `ready_basic`，也不做选择性跳过 enhanced；正式进入质量门控、向量化和入库前，仍等待完整三层切片结果。回退方式：

```env
HIERARCHICAL_ENHANCE_MODE=serial
```

当前验证配置：

```env
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

实际调度池不是四类各自独立，而是三组：

| 调度池 | 包含任务 | 当前 worker 软上限 |
|---|---|---:|
| text | 普通文本增强 + 片段增强 | 16 |
| table | 表格摘要增强 | 3 |
| image | 图片/VL 描述增强 | 4 |

当前最大同时 enhanced 外呼数由 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70` 控制。三类 worker 是软上限：调度器优先按 text/table/image 预算派发任务，但当某类任务提前完成或不存在时，空闲容量可以被其他仍有积压的任务类型借用。例如图片增强提前完成后，空出来的全局并发名额可以继续服务文本或表格增强，避免固定池空转。如果 provider 限流明显，可优先回退到上一稳定验证档 `16/3/4 + 60`、`16/3/4 + 50`、`16/3/4 + 42`、`16/3/4 + 34`、`16/3/4 + 26`、`16/3/4 + 22` 或 `12/3/4 + 18`。

10-08b 后续热路径优化继续保持严格等价：每个 worker 线程默认复用同一 `api_key + base_url` 的 OpenAI 兼容客户端，避免几千个 enhanced 请求反复创建 client/连接池；`link_related_chunks()` 内部改用 relation key set 做 O(1) 去重，但仍按原规则顺序生成 `refers_to / adjacent / sibling / enhanced inheritance`。这两项只优化执行成本，不改变切片原则或最终关系语义。

切片阶段已暴露 breakdown：

```text
chunkBaseMs       基础 parent / child / table / image 切片
enhanceWallMs     并发 enhanced 墙钟耗时
enhanceTextMs     普通文本增强累计耗时
enhanceFragmentMs 片段增强累计耗时
enhanceTableMs    表格增强累计耗时
enhanceImageMs    图片增强累计耗时
enhanceTasks      增强任务总数
enhanceFailures   无输出且带错误的增强任务数
enhanceClientReuse 是否启用线程内客户端复用，1 表示启用
enhanceScheduler  是否启用动态借用式调度器，1 表示启用
enhanceMaxConcurrency 配置的全局 enhanced 外呼上限
enhancePeakConcurrency 本次运行实际峰值并发
enhanceTextWorkers / enhanceTableWorkers / enhanceImageWorkers 各类型 worker 软上限
mergeChunksMs     按 slot 合并耗时
linkRelationsMs   图文/表格引用关系构建耗时
```

本阶段暂不默认启用 enhanced cache。原因是严格等价口径下，cache 会让“重复入库/重试”复用旧增强输出，而旧串行逻辑会重新调用模型生成；除非 cache 被作为显式可选模式并带 prompt/model/provider 版本 key，否则容易和“结果不变”的验收边界混淆。

### 方案二：按需增强

不要默认所有 `child` 都做重增强。优先增强：

- 标题、定义、结论、公式说明
- 图表附近文字
- 表格 chunk
- 流程、因果、步骤类内容
- 用户问过但召回差的问题相关区域
- 被多次引用的热点章节

普通连续正文可以先只做轻量增强，或者延后处理。

建议把 selection policy 显式化为可观察规则，例如：

```text
P0: table / image / fragment / 图表引用附近 chunk
P1: 标题、定义、结论、流程、因果、步骤类 chunk
P2: 用户问过但召回差、评测失败、被多次引用的 chunk
P3: 普通连续正文，按需或低峰补跑
```

对 7890 切片级别的大书，第一轮后台增强可以只处理 P0/P1，先得到可感知收益；P2/P3 由查询反馈、评测失败或低峰任务逐步触发。

### 方案三：轻重增强分层

轻量 enhanced 可以同步或快速完成：

- 标题路径拼接
- 页码 / 章节 / 小节上下文
- 图表编号
- 专有名词关键词
- 邻接 chunk 简短上下文

重型 enhanced 放到后台：

- LLM 摘要
- 查询改写式增强
- 图表语义描述
- 术语解释
- 因果 / 流程关系抽取

### 方案四：批量生成与缓存

增强生成应尽量批量化：

```text
N 个 child -> 一次 LLM 调用 -> 返回 N 个 enhanced
```

建议每批 5-10 个 child，避免批量过大导致 JSON 解析失败或漏项。

批量生成需要可恢复的 batch 状态，而不是只记录 document 级状态：

```text
enhance_batch:
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

失败重试时只重跑失败 batch 或失败 chunk。写入 enhanced、entity、triple、relation 时要保持幂等，避免重试插入重复数据。

增强结果也应缓存。缓存键可以包含：

```text
document_hash
chunk_content_hash
enhance_prompt_version
model_name
strategy
```

只要输入和策略不变，就不要重复生成。

### 方案五：查询反馈驱动补强

当基础问答已经可用后，可以把真实查询和评测结果作为增强调度信号：

- 召回为空或置信度低的问题，反向定位相关章节并提升增强优先级。
- rerank 前后分歧大的候选，标记为需要 enhanced 或关系抽取。
- 评测失败的问题，把 gold / expected evidence 附近 chunk 放入 P2 队列。
- 高频访问章节优先补图谱，低频普通正文延后到低峰任务。

这样增强预算会优先花在实际影响问答质量的位置，而不是平均铺满整本书。

## 对知识图谱演进的影响

三层增强异步化对知识图谱方向是正向影响，但前提是异步任务要设计成“图谱可增量构建”，而不是简单地把 enhanced 延后。

### 正向影响

1. 入库更快，图谱可以渐进生成

   文档先进入 `ready_basic`，马上可检索。实体、关系、三元组和图谱边在后台逐步补齐。

2. 图谱质量更容易提升

   同步链路通常会为了速度压缩 prompt、减少校验。异步后可以给实体抽取、关系抽取、图表语义理解更多时间，也可以做二次校验。

3. 支持热点优先建图

   不必一开始把整本教材全量图谱化。可以优先处理：

   - 用户问过的章节
   - 召回频繁的 chunk
   - 标题、定义、图表、流程段
   - 评测失败问题相关区域

4. 支持多版本图谱

   后台增强可以记录 `prompt_version`、`model`、`extraction_strategy`。未来关系抽取策略升级时，可以增量重跑，不必重新入库整本 PDF。

### 风险点

1. 图谱短时间内不完整

   文档刚入库时，Graph RAG 结果可能弱于普通 RAG。因此 API 和前端需要明确暴露 `graph_status` 或 `graphCoverage`。

2. 在线召回必须支持降级

   如果图谱没建完，`/api/rag/graph-query` 不能假装完整。更合理的行为是：

   ```text
   graph incomplete -> child/enhanced hybrid recall -> 返回 graphStatus 提示
   ```

3. 关系边需要幂等更新

   异步任务可能重复跑、失败重试、局部重建，所以 `entity_mentions`、`chunk_relations`、`kg_triples` 应按稳定键去重或覆盖，例如：

   ```text
   document_id + chunk_id + extractor_version + relation_key
   ```

4. enhanced 与 graph 职责要分清

   `enhanced` 的主要目标是提高召回；`kg_triples` 和 `chunk_relations` 的目标是结构推理和路径解释。不要把所有结构信息都塞进 enhanced 文本，否则未来图谱会退化成不可维护的“文本影子”。

## 推荐演进链路

建议未来后台任务拆成可独立重试的流水线：

```text
基础入库完成
-> lightweight enhanced
-> entity extraction
-> relation extraction
-> kg triple normalization
-> graph index / graph retrieval metadata
-> graph quality check
```

每一步都应具备：

- 独立状态
- 独立重试
- 幂等写入
- 版本标记
- 可观测耗时

## 初步结论

三层增强异步化不会削弱知识图谱，反而是走向知识图谱的必要前提。

如果坚持同步全量 enhanced 和同步全量建图，教材越大，系统越像一个慢速批处理系统。更好的方向是让文档先可用，再通过后台任务持续补齐 enhanced、实体、关系和图谱能力。
