# Document Mind 解析优化过程

本文把 Document Mind 解析优化整理成一条连续过程，方便对内复盘或对客户说明。这里的“解析优化”只覆盖 PDF 体检、分片、Document Mind 提交/轮询/结果获取、内容块转换和解析阶段观测；不把后续清洗、三层切片、embedding、pgvector 写库的收益混到解析收益里。

## 一句话结论

当前最优已验证档不是“盲目加并发”，而是：

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY=1
ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED=false
ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED=false
ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST=false
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE=true
```

最新有效复核结果：

```text
parseWallMs=84042
parseKeyPoolSize=4
parseKeyActiveTarget=4
parseKeyThrottleCount=0
parseKeyRetryCount=0
parseKeyCooldownCount=0
parseProviderManagedLlmEnabled=0
outputBlocks=18500
outputImageBlocks=475
outputTableBlocks=81
```

## 结档状态

状态：**已结档**  
结档日期：`2026-06-17`

阿里 Document Mind 解析性能优化到此为止。当前档位已经完成 parse-only、完整入库、慢 key 回归修复和 4 组 AK/SK 稳定性复核；后续不再继续围绕同一 provider 做常规调参、盲目加并发、投机重发、轮询频率压缩或复杂度分片默认化。

最终封存档：

```text
33页/片 + 4 worker + 单 Key 并发 1 + probe1
No-LLM
markdown,visualLayoutInfo
4 组已验证 Document Mind AK/SK
weighted/heavy-first/hedged shard 默认关闭
```

封存原因：

- 4 组 AK/SK 已覆盖当前 4 worker 并发需求，6 组反而曾让未知慢 key 混入稳定路径。
- 关闭 Document Mind 托管 LLM/VLM 是主要有效收益点，已通过完整入库验证。
- weighted sharding、hedged shard、更密集 poll、混合本地解析在代表样本上没有形成可采纳收益。
- 当前剩余端到端耗时已经不应继续归因到 Document Mind provider 单点，后续若要改善整体体验，应从产品流程、basic-ready、替代 provider 或后续阶段异步化另开课题。

## 优化主线

### 1. 先把问题拆清楚

最早的问题不是“系统慢”这么笼统，而是入库链路里 parse 阶段长期占大头。我们先把链路拆成：

- parse：PDF 解析、分片、云端 provider 调用、结果转换。
- chunk：三层切片和 enhanced 外呼。
- embedding：向量化。
- export：pgvector 写库。

这样做的目的，是避免把切片或写库的耗时误认为 Document Mind 解析问题。后续所有结论都按阶段拆账。

### 2. 引入 Document Mind provider，但保持显式切换

系统先保留 MinerU 默认链路，再新增 `PDF_PARSER_PROVIDER=ali_document_mind` 作为显式解析 provider。这样可以做同文档 A/B，不做静默 fallback，避免客户问“这次到底是谁解析的”时解释不清。

Document Mind 接入后，补齐了：

- `markdown` 输出转换。
- `visualLayoutInfo` 图文版面证据转换。
- 图片、表格、页码、原文件来源透传。
- 解析失败、业务错误、空结果的明确异常。

### 3. 解决大文件限制：自动分片

Document Mind 对大文件和页数有限制，真实教材经常超过单次解析舒适区。因此加了大文件分片：

```text
PDF -> 按页拆成多个 shard -> 多 worker 调 Document Mind -> 按原始页码合并
```

默认触发条件：

- 文件大于 `150MB`
- 或页数超过 `50`

合并时恢复全局页码，保证后续证据回溯仍然指向原 PDF 页码。

### 4. 给解析阶段补进度和指标

解析慢时，如果控制台只显示 running，用户会以为卡死。因此增加 parse stage 观测：

- `provider`
- `shardCount`
- `completedShards`
- `pollCount`
- `parseWallMs`
- `outputBlocks`
- `parseKey.*`
- `submit/poll/resultFetch/convert/merge` 内部耗时

这一步的价值是让后续调参有依据：到底慢在提交、轮询、结果获取，还是某个 shard 长尾。

### 5. 验证分片粒度和并发：不是越多越快

最初直觉是：页数拆小一点、worker 多一点，应该更快。实际验证不是这样。

代表性结果：

| 档位 | 结果 |
|---|---:|
| `33页/片 + 4 worker + 单 Key 并发 1` | `166860ms` |
| `30页/片 + 4 worker + 单 Key 并发 1` | `184736ms` |
| `36页/片 + 4 worker + 单 Key 并发 1` | `359430ms` |
| `33页/片 + 5 worker + 单 Key 并发 1` | `437000ms` |

结论：

- `33页/片 + 4 worker` 最稳。
- 拆得更小会增加 shard 数和调度波次，不一定更快。
- worker 提高到 5 反而触发 provider 侧隐式排队长尾。
- 单个 AK/SK 同时跑多个 in-flight 也会放大长尾风险。

因此当前策略是：用真实 AK/SK 组数支撑 worker，不靠单 Key 堆并发。

### 6. 多 AK/SK 凭证池：从“多 key”改成“好 key”

增加 AK/SK 后，曾经出现“key 变多但解析更慢”的情况。原因不是限流，而是部分 key 或 provider 路径有长尾。

因此凭证池做了几件事：

- 同一个 shard 的 `submit -> poll -> result` 使用同一个匿名 key。
- 只暴露 `dm-key-N`，不暴露真实 AK/SK。
- 记录每个 key 的 `lastMs / avgMs`。
- 调度时优先选择 inflight 少、历史耗时低的 key。
- 冷启动时限制未知 key 探测并发。

验证后，`KEY_PROBE_CONCURRENCY=1` 最稳：

| 档位 | parseWallMs |
|---|---:|
| `probe1` | `192911ms` |
| `probe2` | `362534ms` |
| `probe3` | `346032ms` |

后续又修复了一次回归：Docker backend 仍带 6-key pool，但 4 worker 稳态只需要 4 个活跃 key，导致未知慢 key 混入。现在新增 `parseKeyActiveTarget`，worker 目标满足后不再继续探未知 key；本地 `.env` 也收敛到 4 个已验证 key。

### 7. 尝试投机重发：验证后关闭

针对慢 shard，做过 hedged shard 投机重发：当某个 shard 过慢时，重复提交一份，谁先回来用谁。

结果：

| 档位 | parseWallMs |
|---|---:|
| baseline | `188911ms` |
| hedge90 | `224780ms` |
| hedge75 | `228932ms` |

结论：投机重发增加了 provider 请求量，但没有降低总墙钟，默认关闭，只保留为极端长尾诊断开关。

### 8. 关闭 Document Mind 托管 LLM/VLM 增强

这是解析阶段最有效的一次优化。之前 Document Mind 托管增强会在 provider 侧做额外 LLM/VLM 处理，导致长尾明显。

调整为：

```env
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
```

注意不是只要 `markdown`，而是保留 `visualLayoutInfo`，避免图表和版面证据丢失。

parse-only 验证：

| 档位 | parseWallMs | 输出 |
|---|---:|---|
| No-LLM + layout | `102131ms`，repeat `97082ms` | `18500 blocks / 475 images / 81 tables` |
| No-LLM + markdown only | `119973ms` | 图片证据丢失 |
| LLM + markdown | `202658ms` | blocks 明显更少 |
| LLM + layout repeat | `381948ms` | provider 长尾明显 |

结论：关闭托管 LLM/VLM，并保留 `markdown,visualLayoutInfo`，是当前推荐档。

### 9. 用完整入库验证 No-LLM 不是“只在解析阶段好看”

关闭托管增强后，parse blocks 从约 `8.3k` 增加到 `18.5k`，风险是后续 clean/chunk 被压垮。因此跑完整入库验证。

代表性结果：

| Run | Wall | Parse | Chunk | Embedding | Export | Chunks |
|---|---:|---:|---:|---:|---:|---:|
| No-LLM full baseline | `262834ms` | `123655ms` | `67115ms` | `16373ms` | `35509ms` | `2989` |
| Warm values run | `212885ms` | `84375ms` | `74471ms` | `16336ms` | `22710ms` | `2989` |
| COPY run | about `236s` | `124088ms` | `71933ms` | `16527ms` | `16578ms` | `2985` |

结论：

- `18.5k` parse blocks 没有压垮 clean/chunk。
- clean 仍是百毫秒级。
- chunk 仍主要耗在三层 enhanced，不是解析输出不可承接。
- No-LLM 可以作为完整入库推荐档。

### 10. 压缩本地分片保存开销

Document Mind 每次真实解析前都要先生成 shard PDF。这里不缓存解析结果，只优化临时 shard PDF 的保存策略：

```env
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE=true
```

微基准：

| 策略 | 12 个 shard 保存耗时 |
|---|---:|
| `garbage=4,deflate=true` | 约 `782ms` |
| `garbage=1,deflate=true` | 约 `282ms` |

收益不如关闭托管增强大，但属于低风险稳定收益，不改变页码、云端请求和结果合并顺序。

### 11. 尝试复杂度感知分片：实现了，但默认不启用

为了不靠加并发，又尝试让系统识别每页复杂度，把重页分散到不同 shard，并优先提交重 shard。

实现包括：

- `inspect_pdf(profile_pages=True)` 提取每页文本、图片、绘图对象、疑似扫描页等轻量特征。
- `plan_weighted_page_ranges()` 规划 weighted shard。
- `HEAVY_SHARD_FIRST` 支持先提交重 shard。
- 合并仍按全局页码，不改变输出语义。

真实 A/B：

| 档位 | parseWallMs | shardWallMsMax | inspectMs | 输出 |
|---|---:|---:|---:|---|
| fixed | `100560` | `84993` | `992` | `18500 / 475 / 81` |
| weighted-heavy-first | `136111` | `82171` | `10119` | `18500 / 475 / 81` |

结论：weighted 让最慢 shard 略降，但总墙钟更差，主要因为当前教材分片已经比较均匀，且逐页 inspect 增加约 `10s` 开销。因此默认关闭，只作为显式实验档。

### 12. 建立快验证漏斗，减少无效完整 A/B

后续不再每个想法都直接跑整本云端 A/B，而是：

```text
本地 dry-run -> 少量页段 canary -> 完整 parse-only A/B -> 完整入库验证
```

本地 dry-run 不调用 Document Mind，只分析 PDF 页面特征，输出：

- fixed / weighted 分片规划
- shard 复杂度分布
- 建议 canary 页段
- hybridParse 本地文本候选比例

当前代表样本：

| PDF | dry-run 结论 |
|---|---|
| 228MB / 494 页扫描教材 | 本地文本候选 `0/494`，继续全量云端解析 |
| 47MB / 396 页常用教材 | 本地文本候选 `21/396`，约 `5.3%`，暂不值得做混合解析 |

结论：混合解析目前不是优先方向，除非后续教材 born-digital 文本层占比明显更高。

### 13. 状态响应短路：只做单次任务内的保守省往返

有些 Document Mind 状态响应里已经带了 markdown 或结构化结果。现在做了保守短路：

- 如果 `QueryDocParserStatus` 的 success 响应能转换出非空主内容，跳过额外 `GetDocParserResult`。
- 如果状态响应只有页面图片 layout 或内容不完整，仍然拉取 result。
- 不跨任务缓存，不复用历史 `ContentBlock[]`，不复用历史 shard 结果。

最新整本复核里 `resultFetchSkippedByStatus=0`，说明当前主要收益不来自这里；它更多是无害兜底。

### 14. 轮询间隔验证：默认不下调

验证过 `POLL_INTERVAL=1/2/3`。4 shard canary 结果受 provider 队列波动影响非常明显：

```text
poll1=285671ms
poll2=241954ms
poll3=34765ms
```

结论：轮询更密集不能稳定改善墙钟，provider 队列波动远大于轮询检测延迟，默认保持 `3s`。

## 最终采纳与未采纳

| 方案 | 状态 | 原因 |
|---|---|---|
| 大文件自动分片 | 采纳 | 解决 Document Mind 大文件限制和长任务风险 |
| `33页/片 + 4 worker + 单 Key 1 inflight` | 采纳 | 多轮真实教材验证最稳 |
| 多 AK/SK 凭证池 | 采纳 | 支撑 shard 并发，但不自动放大 worker |
| 慢 key 评分 / active target | 采纳 | 避免未知慢 key 混入稳定路径 |
| 关闭托管 LLM/VLM | 采纳 | 最大幅度降低 provider 长尾 |
| `markdown,visualLayoutInfo` | 采纳 | 兼顾速度和图片/layout 证据 |
| shard 保存 `garbage=1` | 采纳 | 降低本地拆片开销，不改变解析语义 |
| 状态响应短路 | 采纳为兜底 | 有机会省一次 result fetch，但不是主收益 |
| hedged shard 投机重发 | 默认关闭 | A/B 显示更慢 |
| weighted sharding / heavy-first | 默认关闭 | 当前代表教材总墙钟更差，仅保留实验档 |
| `markdown` only | 不作为默认 | 会丢图片证据 |
| 单 Key 多 in-flight | 不采纳 | 会放大 provider 隐式排队 |
| 更密集 poll | 不采纳 | 没有稳定收益 |
| 混合本地/云端解析 | 暂不采纳 | 当前代表样本文本层占比过低 |

## 对客户的口径

可以这样解释：

我们没有简单地把解析并发调大，而是按真实教材逐轮验证了“哪里慢、为什么慢、怎么调才稳定”。最后发现 Document Mind 的主要耗时来自云端 provider 的处理长尾，盲目增加 worker、单 Key 并发或投机重发都会让长尾更严重。当前采用的是更稳的组合：大文件先分片，4 个 worker 并行，每个 key 同时只处理一个任务，避开历史慢 key，关闭 Document Mind 托管 LLM/VLM 增强，同时保留 markdown 和视觉版面信息，确保图片、表格、页码证据不丢。

这套优化不是只看解析阶段数字，也做了完整入库验证：关闭托管增强后输出块变多，但后续清洗、切片和入库可以承接，没有造成链路失控。因此当前方案是“速度、稳定性、证据完整性”三者平衡后的已验证档。

## 封存后的重启规则

这条优化线默认不再继续推进。只有出现以下情况之一，才重新打开 Document Mind 解析优化：

- 阿里 Document Mind provider 行为或计费/性能策略发生明确变化。
- 新教材类型和当前代表样本明显不同，例如 born-digital 文本层占比显著提高。
- 当前封存档出现可复现回退，例如 `parseWallMs` 连续多次明显劣化且不是上游波动。
- 产品明确接受证据取舍，例如允许纯文本快档牺牲图片/layout 证据。
- 决定进行新的 provider 级 A/B，而不是继续微调现有 Document Mind 档。

若重新打开，仍按这个漏斗判断：

1. 先本地 dry-run，看 PDF 是否真的存在复杂度不均或可本地解析页。
2. dry-run 有正信号，再跑少量页段 canary。
3. canary 至少改善约 `10%`，且输出块、图片块、表格块不回退，再跑完整 parse-only A/B。
4. parse-only 成立后，再跑完整入库，确认 clean/chunk/embedding/export 都能承接。

如果解析时长仍不可接受，优先评估替代 provider、证据保留取舍，或产品层 basic-ready 流程；不建议继续通过单 Key 多 in-flight 或盲目加 worker 堆并发。
