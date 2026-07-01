# Hierarchical 异步增强方案存档

> 状态：方案存档，部分已落地。10-08 已实现“入库前有序并发增强 + 严格顺序合并 + 切片子阶段耗时观测”，10-08b 已补 enhanced worker 内 LLM client 复用与 linker 去重热路径优化；`ready_basic` 后台渐进补强、enhanced cache、batch 状态和查询反馈补强仍是后续显式模式。
> 背景：当前 hierarchical 三层切片会在切片过程中逐块调用 LLM / VL 做 enhanced chunk、实体和三元组增强。大文档入库时，这部分模型调用会让“切片阶段”长时间阻塞，用户难以判断进度，也难以区分基础切片慢还是增强慢。

## 目标

在保持最终入库切片结果尽量等价于当前实现的前提下，把切片阶段拆成更可观测、可并发、可重试的流程。

核心目标：

1. 基础 parent / child / table / image 切片快速产出。
2. enhanced chunk、entities、triples 仍按当前逻辑生成。
3. 大书先进入基础可问答状态，再通过后台任务渐进补齐 enhanced / entities / triples / graph。
4. 对需要严格复现旧结果的场景，保留“入库前等待增强完成”的兼容模式。
5. 用户能看到增强进度，而不是只看到“切片中”。

## 当前问题

当前大致链路是：

```text
parse
  -> clean
  -> hierarchical chunk:
       parent / child 切片
       逐个 child 调 LLM 增强
       逐个 table 调 LLM 摘要
       逐个 image 调 VL / LLM 描述
  -> quality
  -> embedding
  -> export
```

慢点主要来自：

- 文本 child enhanced 串行调用 LLM。
- 表格 enhanced 调用 LLM，复杂表格耗时更长。
- 图片 enhanced 调用 VL 模型或文本 fallback，通常比文本更慢。
- 切片阶段内部没有细粒度任务状态，用户无法看到当前处理到哪一页、哪一个切片。
- 当一本书产生数千个切片时，逐条异步增强也会形成很长的后台队列，成本和限流风险仍然不可控。

## 推荐方案

推荐默认采用“基础先可用，增强后台补齐”：

```text
parse
  -> clean
  -> chunk_base(parent/child/table/image)
  -> child embedding
  -> write DB
  -> ready_basic
  -> background enhance / graph jobs
```

如果暂时不改前端阶段结构，也可以仍保留现有 `chunk` 阶段，但在阶段 message / task log 中细分：

```text
基础切片完成：246 个基础切片
增强中：83/246，当前 P.35 #84
增强完成：生成 246 个 enhanced chunk
关系构建完成
```

对要求最终 chunk 集合与当前同步 hierarchical 完全一致的离线导出或基准评测，可以启用兼容模式：

```text
chunk_base
  -> chunk_enhance
  -> quality
  -> embedding
  -> export
```

兼容模式会等增强完成后再正式入库，速度较慢，但结果更接近旧链路。

### 阶段 1：基础切片

只做不依赖模型调用的结构化切片：

- parent 标题块
- child 文本块
- table child
- image child
- parent-child 归属
- chunk_index、page、source、title 等基础字段

输出基础 chunks，并保存为草稿或任务内存状态。

### 阶段 2：异步增强

后台增强基础 chunks：

- 对普通文本 child 生成 enhanced summary / retrieval questions。
- 对 fragment child 生成上下文补全。
- 对 table child 生成表格摘要和术语解释。
- 对 image child 生成图片描述。
- 解析 enhanced 输出中的 entities / triples。

增强完成后，把 enhanced chunks 合并回基础 chunks，得到与当前 hierarchical 同步实现一致的完整 chunk 集合。

### 阶段 3：基础入库与渐进补齐

默认推荐基础切片完成后先正式入库，让文档进入 `ready_basic`：

推荐顺序：

```text
基础切片完成
  -> child quality / child embedding / write DB
  -> 文档进入 ready_basic，可普通问答
  -> 后台增强任务继续生成 enhanced / entity / triple / relation
  -> enhanced embedding 后增量写库
  -> 文档逐步进入 ready_enhanced / graph_partial / graph_ready
```

这会引入半增强状态，因此必须明确数据语义：

- `ready_basic` 只承诺 child 检索和基础问答。
- `ready_enhanced` 才承诺 `child + enhanced` 检索。
- `graph_partial / graph_ready` 分别表示图谱局部可用 / 图谱完成。
- 在线召回必须按状态降级，不能假装 enhanced 或 graph 已完整。

如果不接受半增强状态，才使用“入库前等待增强完成”的兼容模式。

## 并发策略

增强阶段应按类型设置不同并发上限，不按 chunk 数直接并发：

```text
text enhanced: 3-6 并发
table enhanced: 1-2 并发
image / VL enhanced: 1 并发
embedding batch: 1-2 并发
```

当前低风险验证配置：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=22
```

调度分组如下：

| 调度池 | 包含任务 | 当前 worker 软上限 |
|---|---|---:|
| text | 普通文本增强 + 片段增强 | 16 |
| table | 表格摘要增强 | 3 |
| image | 图片/VL 描述增强 | 4 |

当前验证最大同时 enhanced 外呼数由 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=22` 控制。各调度池 worker 是软上限，调度器优先按 text/table/image 预算派发任务；当某类任务提前完成或不存在时，空闲容量可以被其他仍有积压的任务类型借用。该实现仍是入库前兼容模式：基础切片后规划 enhanced slot，动态有限并发执行 enhanced，最后按原 slot 顺序合并；不启用 `ready_basic`、不跳过 enhanced、也不使用多 child batch prompt。

### 增强多 key 池验证方案

方案状态：已实现最小闭环。多 key 池只用于扩大同一 provider、同一模型配置下的吞吐配额，不改变 enhanced 任务生成、prompt、模型参数、解析逻辑或 slot 合并顺序。

配置：

```env
# 文本、表格、fragment 增强使用；未配置时继续使用 LLM_API_KEY。
LLM_API_KEY_POOL=

# 图片/VL 增强使用；未配置时继续使用 VL_API_KEY；VL 不可用时回退 LLM_API_KEY_POOL。
VL_API_KEY_POOL=

HIERARCHICAL_ENHANCE_KEY_RETRIES=1
HIERARCHICAL_KEY_COOLDOWN_SECONDS=30
```

限流处理：

- 文本、表格、fragment 从 `LLM_API_KEY_POOL` 取 key。
- 图片优先从 `VL_API_KEY_POOL` 取 key；VL 不可用并回退文本模型时，改从 `LLM_API_KEY_POOL` 取 key。
- 仅 `429 / rate limit / throttle / quota` 等明确限流错误触发 key 冷却和换 key 重试。
- 普通业务错误、模型输出协议错误、解析错误不跨 key 盲目重跑。
- 第一轮保持 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=22` 不变，先验证同并发下 key 池是否降低限流和长尾，再决定是否提高全局并发。

观测字段：

```text
enhanceLlmKeyPoolSize
enhanceVlKeyPoolSize
enhanceKeyPoolSize
enhanceKeyThrottleCount
enhanceKeyRetryCount
enhanceKeyCooldownCount
```

安全边界：

- 所有 key 必须使用相同 `base_url`、模型、prompt、temperature、max_tokens 和输出解析逻辑。
- 日志与 metrics 不得记录真实密钥；当前只记录池大小、限流、冷却和重试次数。
- 多 key 池是切片增强线的独立验证项，不和解析多 key、embedding 并发调参混在同一轮判断。

原因：

- 文本增强最适合并发。
- 表格 prompt 较长，接口耗时和 token 成本更高。
- 图片/VL 请求最容易慢，也更容易触发模型限流。

增强任务需要支持：

- retry / backoff
- 单 chunk 失败记录
- 总体失败阈值
- 任务取消
- 进度日志
- provider / model 维度限流
- 单文档最大并发占用，避免一本大书独占后台 worker

对 7890 个切片这类大书，后台增强应通过优先级队列处理，而不是全量平铺：

```text
P0: table / image / fragment / 图表引用附近 chunk
P1: 标题、定义、结论、流程、因果、步骤类 chunk
P2: 用户问过但召回差、评测失败、被多次引用的 chunk
P3: 普通连续正文，按需或低峰补跑
```

第一轮优先完成 P0/P1，让用户尽快感知 enhanced 收益；P2/P3 由查询反馈、评测失败或低峰任务触发。

## 缓存策略

建议后续增加 enhanced cache，缓存 key 可由以下字段组成：

```text
hash(chunk.content)
chunk.type
enhance prompt version
model
base_url/provider
```

收益：

- 任务重试时不用重复增强已完成 chunk。
- 同一教材重复入库时可以复用结果。
- 大文件失败后恢复成本更低。

批量增强还需要 batch 级状态：

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

失败重试时只重跑失败 batch 或失败 chunk，写入 enhanced、entity、triple、relation 时保持幂等。

## 选择性增强

默认不再建议对所有 child 做重型 LLM enhanced。推荐分层：

- 必须优先增强：
  - table child
  - image child
  - fragment child
  - 定义、流程、公式、图表引用相关 child
- 可延后增强：
  - 已经足够完整的长文本 child
  - 低价值噪音段落
  - 很少被检索或从未被用户访问的普通正文

这样会改变“每个可增强 child 都立刻拥有 enhanced”的旧假设，但更适合教材级大文档和在线系统。

## 查询反馈驱动补强

基础问答可用后，可以用真实查询反推增强优先级：

- 召回为空或置信度低的问题，提升相关章节增强优先级。
- rerank 前后分歧大的候选，标记为需要 enhanced 或关系抽取。
- 评测失败的问题，把期望证据附近 chunk 放入 P2 队列。
- 高频访问章节优先补图谱，低频普通正文延后到低峰任务。

## 方案对比

| 方案 | 最终结果一致性 | 用户可见速度 | 总耗时 | 实现复杂度 | 风险 |
| --- | --- | --- | --- | --- | --- |
| 继续同步增强 | 高 | 慢 | 慢 | 低 | 切片阶段黑盒、易超时 |
| 只做基础切片，跳过增强 | 低 | 快 | 快 | 低 | Graph RAG / enhanced 检索质量下降 |
| 先基础入库，后台优先级补增强 | 中 | 快 | 中 | 高 | 需要状态、降级和幂等补写 |
| 入库前异步并发增强 | 高 | 中-快 | 中-快 | 中 | 需要任务状态和失败策略 |

推荐默认选择：**先基础入库，后台优先级补增强**。

保留可选兼容模式：**入库前异步并发增强**，用于离线基准、导出一致性或暂时不希望处理半增强状态的场景。

## 建议落地顺序

1. 为 hierarchical 增加 `enable_enhanced=false` 的基础切片路径验证，确认基础切片耗时。
2. 在入库服务中拆出 `chunk_base` 和 `chunk_enhance` 两个内部步骤。
3. 增加 `ready_basic / enhancing / ready_enhanced / enhance_failed` 状态。
4. 普通召回按状态降级：`ready_basic` 只查 child，`ready_enhanced` 查 child + enhanced。
5. 增强阶段增加进度日志：总数、已完成数、当前页码、当前 chunk 类型、失败数。
6. 对文本增强加有限并发，对表格和图片增强限流。
7. 增加 priority selection policy，先处理 P0/P1。
8. 增加 enhanced cache 和 batch 状态，支持失败重试与幂等补写。
9. 后续再做查询反馈驱动补强。
10. 在严格等价兼容模式中验证增强多 key 池：默认激活 4 组，按 `enhanceTasks` 动态拉到 6/8/10 组，第一轮保持总并发不变。

## 状态边界

本方案是技术方案存档，其中一部分已经作为严格等价模式落地：

- 已修改 `core/chunker/hierarchical.py`，支持 `HIERARCHICAL_ENHANCE_MODE=parallel_ordered`。
- 已在入库任务状态中记录 `chunkTimings`，包含 `enhanceWallMs`、增强任务数、失败数、client 复用标记和 `linkRelationsMs` 等字段。
- 已支持 `HIERARCHICAL_REUSE_LLM_CLIENTS=true`，在 worker 线程内复用同一 provider/base_url 的 OpenAI 兼容 client。
- 已优化 `link_related_chunks()` relation 去重热路径。
- 已将固定 text/table/image 池升级为动态借用式调度；当前验证档为 `16/3/4` 软上限，`HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=22` 全局硬上限，并记录 `enhanceScheduler / enhanceMaxConcurrency / enhancePeakConcurrency / enhance*Workers` 指标。
- 已归档增强多 key 池验证方案：默认激活 4 组、最多 10 组，按 `enhanceTasks` 动态拉取，保持严格等价边界。

仍未实现的部分：

- 尚未新增 `chunk_base` / `chunk_enhance` 阶段。
- 尚未改变正式入库数据结构。
- 尚未实现 `ready_basic / ready_enhanced` 状态。
- 尚未实现后台增强队列、优先级调度、batch 状态或 enhanced cache。
- 尚未实现增强多 key 池、key 冷却、限流换 key 重试和对应 metrics。
