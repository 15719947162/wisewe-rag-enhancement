# 三层切片最终技术方案

本文沉淀当前三层切片的最终落地方案。历史调优过程与性能演进见 [切片性能优化档案](../performance-optimizations/chunk/README.md)。

## 目标

在不改变 parent / child / enhanced 三层结果语义的前提下，把大教材入库中的切片增强阶段从分钟级长尾压缩到可控范围，并保证后续 RAG / Graph RAG 仍能回溯到原文、图片、表格和关系证据。

## 总体链路

```text
PDF
  -> 保存到 data/uploads/
  -> 解析 provider 处理与结果下载 / 获取
  -> 合并为完整 ContentBlock[]
  -> 基础三层切片 parent / child / table / image
  -> 规划 enhanced slots
  -> text / fragment / table / image 并发增强
  -> 按原 slot 顺序合并 enhanced
  -> link_related_chunks()
  -> quality
  -> embedding / semantic / procedure / causal
  -> entity materialize
  -> pgvector / relations / triples 写库
```

## 下载与资产准备边界

下载机制统一放在解析/资产准备阶段：

| 路径 | 下载 / 获取行为 | 三层切片看到的输入 |
|---|---|---|
| MinerU | 上传 OSS，轮询 `result_url`，下载结果 ZIP，提取文本、表格、图片路径 | 合并后的 `ContentBlock[]`，图片通常是本地 `image_path` |
| Document Mind | 调用 OpenAPI 获取 markdown / visualLayoutInfo / 页面图片信息 | 转换后的 `ContentBlock[]`，图片可能是本地路径、远程 URL 或 data URL |

三层切片阶段不负责下载 provider 原始结果，也不在增强 worker 中临时下载远程图片。当前图片增强规则是：

- 若 `image_path` 是本地可读文件，且配置了 VL 模型与 VL key，则读取 base64 调用 VL。
- 若本地图片不可读、`image_path` 是远程 URL / data URL，或 VL 不可用，则基于图片 OCR 说明文字走文本 LLM fallback。
- 如果后续要把远程图片 URL 下载成本地文件，应新增“解析后资产标准化”步骤，而不是放进并发增强 worker。

这样做的好处是：下载重试、provider 错误、图片资产缺失和 enhanced 外呼限流可以分别观测和回退。

## 三层结构

| 层级 | 来源 | 是否参与默认召回 | 是否作为最终 citation |
|---|---|---:|---:|
| `parent` | 标题 / 章节 | 否 | 否 |
| `child` | 文本、表格、图片基础证据 | 是 | 是 |
| `enhanced` | LLM 生成的摘要、问题、图片描述、表格摘要、片段补全 | 是 | 否，命中后折叠回 child |

核心规则：

- parent 是章节上下文容器。
- child 是最终证据入口。
- enhanced 是召回辅助，不直接作为事实引用。
- enhanced 的 `parent_id` 指向对应 child，不指向章节 parent。
- enhanced 命中后必须回查 child，并按 child 去重。

## Enhanced 类型

| 类型 | 触发条件 | 输出前缀 | 调度池 |
|---|---|---|---|
| 普通文本增强 | 普通 child，内容长度达到阈值，且不是 fragment | `[LLM增强]` | text |
| 片段增强 | 文本过短或含“如上所述/见图/参见”等上下文依赖词 | `[片段增强]` | text |
| 表格摘要 | `is_table_chunk=True` 且内容达到阈值 | `[表格摘要]` | table |
| 图片描述 | `is_image_chunk=True` | `[图片描述]` | image |

所有 enhanced 仍按原 prompt、原模型参数、原输出解析逻辑独立生成。

## 并发执行方案

当前默认模式：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

执行方式：

1. 基础切片先生成 parent / child / table / image。
2. 对每个需要 enhanced 的 child 预留 slot。
3. 调度器按 text/table/image 软上限派发任务。
4. 当某类任务提前结束或不存在时，其空闲容量可被其他任务类型借用。
5. 全局同时 enhanced 外呼不超过 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY`。
6. worker 可乱序完成，但最终按预留 slot 顺序合并。
7. 所有 enhanced 完成后再进入 `link_related_chunks()`、quality、embedding 和写库。

回退到旧式串行：

```env
HIERARCHICAL_ENHANCE_MODE=serial
```

## Key 池方案

切片增强 key 池：

```env
LLM_API_KEY_POOL=
VL_API_KEY_POOL=
HIERARCHICAL_ENHANCE_KEY_RETRIES=1
HIERARCHICAL_KEY_COOLDOWN_SECONDS=30
```

规则：

- `LLM_API_KEY_POOL` 用于 text / fragment / table。
- `VL_API_KEY_POOL` 用于 image / VL；未配置 VL 或本地图片不可读时，图片描述回退到文本 LLM key 池。
- 所有 key 必须同 base_url、同模型、同 prompt、同参数。
- 仅 `429 / rate limit / throttle / quota` 等明确限流错误触发冷却和换 key 重试。
- 普通业务错误、输出解析错误、内容为空不跨 key 盲重。
- 指标只记录匿名 `llm-key-N` / `vl-key-N`，不记录真实 key。

## 热更新方案

运行时配置优先级以控制台 settings DB override 为准。调整 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY` 等运行时配置时，优先使用控制台 settings API，不需要每次重建镜像。

注意：

- 容器 `printenv` 可能仍显示旧 `.env` 值。
- 判断真实生效状态时，以 `/api/console/settings` 的 `source=db` 和下一轮任务 metrics 为准。
- `.env`、`.env.example`、`.env.docker.example` 作为基线文档同步，不代表已经覆盖 DB override。

## 观测指标

切片阶段必须记录：

```text
chunkBaseMs
enhanceWallMs
enhanceTextMs
enhanceFragmentMs
enhanceTableMs
enhanceImageMs
enhanceTasks
enhanceTextTasks
enhanceFragmentTasks
enhanceTableTasks
enhanceImageTasks
enhanceFailures
enhanceClientReuse
enhanceScheduler
enhanceMaxConcurrency
enhancePeakConcurrency
enhanceTextWorkers
enhanceTableWorkers
enhanceImageWorkers
enhanceLlmKeyPoolSize
enhanceVlKeyPoolSize
enhanceKeyPoolSize
enhanceKeyThrottleCount
enhanceKeyRetryCount
enhanceKeyCooldownCount
enhanceLlmKey.llm-key-N.calls/successes/failures/throttles/totalMs
mergeChunksMs
linkRelationsMs
```

最新已验证档：

```text
task_id=7571b640-6d0b-4bdb-8f83-f90b6902fb9f
enhanceMaxConcurrency=70
enhancePeakConcurrency=70
enhanceWallMs=69365
enhanceTasks=957
enhanceFailures=0
enhanceKeyThrottleCount=0
enhanceKeyRetryCount=0
perKeyCalls=187/188/176/186/195
```

## 回退策略

优先保持 `16/3/4 + 70`。出现以下情况时回退：

| 现象 | 处理 |
|---|---|
| failure / throttle / retry 持续上升 | 先回退 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=60` |
| 图片/VL 明显长尾 | 保持总 cap，先评估 image worker 从 `4` 回退到 `3` |
| 单个 key 错误集中 | 冷却或移除异常 key，不扩大总并发 |
| `enhancePeakConcurrency` 长期达不到 cap | 不继续加 cap，先看任务类型分布和 provider 长尾 |
| 切片已不是主瓶颈 | 转向解析线或向量化后处理线 |

## 不做的事

- 不启用 `ready_basic` 半增强入库作为默认模式。
- 不改变 enhanced prompt 或模型参数来追求性能。
- 不把 remote image 下载放进 enhanced worker。
- 不把 enhanced 作为最终 citation。
- 不暴露真实 API key。
- 不把解析、切片、向量化三条线的指标混为一个结论。

## 后续优化入口

当前切片线已到 `70` 稳定档，下一步优先级：

1. 解析线：继续观察 provider / shard 长尾，而不是盲目加 shard 并发。
2. 向量化线：优先拆解和优化 `link_semantic / procedure / causal` 后处理，外部 embedding API 不是当前主瓶颈。
3. 资产线：如需要 VL 直接看远程图片，应在解析后资产标准化阶段下载远程图片并落本地路径。

## 关联文档

- [切片性能优化档案](../performance-optimizations/chunk/README.md)
- [三层切片规则](../rule/hierarchical-chunking.md)
- [离线入库链路](./offline-ingestion-pipeline.md)
- [性能优化档案总入口](../performance-optimizations/README.md)
