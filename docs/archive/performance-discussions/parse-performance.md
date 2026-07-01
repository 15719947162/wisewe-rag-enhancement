# 解析性能跟踪

本文单独跟踪离线入库中的 `parse` 阶段，避免解析、切片、向量化的耗时判断混在一起。

## 当前实现

解析入口在 `backend/services/ingestion_service.py`：

```text
upload/source ready
-> get_pdf_parser_provider()
-> parse_pdf(...)
-> blocks_preview
-> parse stage success
```

Provider 层由 `core/parser/provider.py` 选择，当前默认仍是 `mineru`，可通过 `PDF_PARSER_PROVIDER=ali_document_mind` 显式切到阿里 Document Mind 做 A/B 对比。MinerU 与 Document Mind 都已接入大文件分片解析，并复用 `core/parser/pdf_sharding.py` 的 PDF 体检、按页拆分、页码 offset 和 shard 合并逻辑。

本轮补了 `_ParseStageTracker`，它只消费现有 parser 日志，不改变解析 provider、分片规则或 `ContentBlock` 结果。

2026-06-11 追加 Document Mind 空结果保护：真实任务 `d8339f18-3115-4507-96af-5bd953708e46` 在 `shard #4 P121-160` 出现 `success` 但 `GetDocParserResult` 无 markdown / layout / page records 的异常。当前处理为：

- `ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT` 基线改为 `markdown,visualLayoutInfo`，避免只拿 markdown 时缺少结构兜底。
- 对同一 job 的空结果执行有限 `result fetch` 重试。
- 若仍为空，仅重新提交当前 shard 的 Document Mind job，不重跑已成功 shard。
- 每次空结果记录 payload 摘要：类型、顶层 key、markdown 字符数、layout record 数、page image 数。

2026-06-11 追加 Document Mind 多 AK/SK 解析并发：为 `PDF_PARSER_PROVIDER=ali_document_mind` 的分片解析增加 credential pool。每个 shard 会租用一个匿名凭证 alias（如 `dm-key-1`），并保证同一 shard 的 `submit -> poll -> result` 使用同一组 AK/SK；只有明确的限流类错误才触发 cooldown 与换 key 重试。有效 shard 并发现在受三者共同约束：

```text
min(shardCount, ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY, parseKeyPoolSize * ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY)
```

新增配置项：

```env
ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL=
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_RETRIES=1
ALIYUN_DOCUMENT_MIND_KEY_COOLDOWN_SECONDS=60
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

2026-06-12 追加解析长尾优化：真实任务 `f6a4348f-4a12-4000-9d4f-5397138d9917` 与 `f4532b5a-3b4a-40ef-9d25-886e67d3c0dd` 显示同为 40 页的 shard 耗时差异明显，慢 shard 可达到约 `109s - 110s`，而快 shard 约 `26s - 30s`。这说明当前瓶颈主要在 Document Mind provider 的单 shard 处理长尾，不在清洗、切片、向量化，也不是多 AK/SK 没有生效。

因此 `ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=40` 现在表示“每片页数上限”。当页数较多且 key 池容量允许时，解析器会按 `parseSchedulingCapacity * ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES` 估算目标 shard 数，并把实际 `effectivePagesPerShard` 收敛到不低于 `ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD`。例如 396 页、4 并发、目标 4 波次时，实际会从 40 页/片收敛到约 25 页/片，shard 数从 10 个增至约 16 个，以减少单个 40 页 shard 拖住 worker 的长尾。

2026-06-12 真实复盘显示“小 shard + 4 worker”没有带来解析提速：任务 `19979c30-f8bd-4d44-a33c-6e3aeb4b2ba6` 使用 4 组 AK/SK、16 个 shard、`effectivePagesPerShard=25`、`parseWorkerCount=4`，各 key 调用均为 `4/4/4/4`，`parseKeyThrottleCount/retry/cooldown=0`，但 `parseWallMs=175023`。对比同一文件上一轮 10 shard 的 `871a693e-8326-4975-9eca-b3d30fa4a1e7` 为 `156727ms`、`f4532b5a-3b4a-40ef-9d25-886e67d3c0dd` 为 `165829ms`，以及更早 `f6a4348f-4a12-4000-9d4f-5397138d9917` 的 `139366ms`，本轮没有提升。

当前判断：多 AK/SK 已真实分摊请求，但解析墙钟仍受总 shard worker 限制和 provider 波动影响；继续单纯缩小每片页数不是优先方向。曾随解析 key 池增加把验证档提升到 `ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=100`、`ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=2`、`ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2`，用于隔离验证解析并发容量收益；真实任务复盘后已确认该方向会放大 provider 隐式排队风险。

2026-06-12 继续复盘后确认，`MAX_INFLIGHT_PER_KEY=2` 会放大 Document Mind provider 侧隐式排队风险：任务 `4423d93c-15c0-4fca-968c-a15d850333af` 使用 16 个 shard、`parseWorkerCount=8`，但 `parseWallMs=293422`，其中 `shard 004 P76-100` 从 14:55:41 到 15:00:14，单片约 273s，且没有显式限流、重试、失败或冷却。因此当前解析-only 验证应回到“一组 AK/SK 同时只跑一个 Document Mind job”：`ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1`。

2026-06-12 16:15 - 16:45 的配置中心快速验证进一步收敛出当前推荐解析档：

| 任务 | 每片页数 | shard / worker | 单 Key 并发 | `parseWallMs` | `shardWallMsMax` | 结论 |
|---|---:|---:|---:|---:|---:|---|
| `4725b55b-faf4-49cc-9c28-9397fc48cb01` | 40 | 10 / 4 | 1 | 347241 | 338385 | 40 页 shard 仍可能出现极端长尾，`shard 002 P41-80` 使用 `dm-key-2` 单片拖到约 338s |
| `8dc4ff70-7068-499c-8600-f1e56b268953` | 25 | 16 / 4 | 1 | 178903 | 57696 | 小 shard 消除了极端长尾，但 16 个 shard 需要 4 波调度，墙钟仍受波次数影响 |
| `43e3e97f-4186-4874-9774-464bb9bd0e97` | 33 | 12 / 4 | 1 | 147618 | 67264 | 12 个 shard 刚好 3 波，未出现显式限流/重试/冷却，首次收敛到当前推荐档 |
| `080ed361-f4f5-4be6-b134-99d53ecbd19a` | 33 | 12 / 4 | 1 | 147212 | 58364 | 同教材重复运行继续保持 12 个 shard / 3 波，无限流/重试/冷却，是当前最新最优档 |

当前解析线建议保持：

```env
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

这组配置只优化解析阶段，不调整清洗、切片、质检、向量化或入库后逻辑。后续若增加真实 AK/SK 组数，可以在保持 `MAX_INFLIGHT_PER_KEY=1` 的前提下，把 `SHARDING_MAX_CONCURRENCY` 提升到物理凭证组数；不要通过同一 AK/SK 的 `MAX_INFLIGHT_PER_KEY=2` 来扩大解析并发。

2026-06-13 复盘 6 组 AK/SK 后进一步确认，新增凭证不应自动拉高 shard worker。对比截图中 4 组配置 `parseWallMs=147212` 与 6 组配置 `parseWallMs=322214`，两者都没有显式限流/重试/冷却，但 6 组下 `dm-key-4`、`dm-key-6` 出现明显长尾，说明更高并发更可能触发 provider 侧排队。当前代码与配置已改为：`CREDENTIAL_POOL` 只表示可轮换凭证池，`SHARDING_MAX_CONCURRENCY` 独立控制解析 worker，默认和本地运行档均保持 `4`。

## 已有观测字段

`parse` stage 的 `metrics` 当前包含：

| 字段 | 含义 |
|---|---|
| `provider` | 本次解析使用的 provider，例如 `mineru` / `ali_document_mind` |
| `shardCount` | 识别到的 shard 总数；未分片时为 1 或 0 |
| `completedShards` | 已完成并合并的 shard 数，按 shard id 去重 |
| `pollCount` | 解析 provider 轮询次数 |
| `parseWallMs` | 入库侧观察到的 parse 阶段总墙钟耗时 |
| `outputBlocks` | 解析输出的内容块数量 |
| `parseKeyPoolSize` | Document Mind AK/SK 池容量；仅 `ali_document_mind` provider 暴露 |
| `parseKeyMaxInflightPerKey` | 每组 AK/SK 允许同时处理的 shard 数 |
| `parseKeyThrottleCount` | 明确限流类错误次数 |
| `parseKeyRetryCount` | 因限流切换凭证重试次数 |
| `parseKeyCooldownCount` | 被放入 cooldown 的凭证次数 |
| `parseKey.dm-key-N.calls/successes/failures/throttles/totalMs` | 匿名 per-key 分布指标，不记录真实 AK/SK |
| `inspectMs` | PDF 体检耗时 |
| `splitMs` | 本地 PDF 拆 shard 耗时 |
| `configuredPagesPerShard` | 配置的每片页数上限 |
| `effectivePagesPerShard` | 本次实际使用的每片页数 |
| `parseSchedulingCapacity` | 用于估算 shard 波次的解析调度容量 |
| `parseShardCount` | 实际生成的 shard 数 |
| `parseWorkerCount` | 实际 ThreadPool worker 数 |
| `submitWallMs` / `submitWallMsMax` | Document Mind submit 累计耗时 / 单 shard 最大耗时 |
| `pollWallMs` / `pollWallMsMax` | Document Mind poll 累计耗时 / 单 shard 最大耗时 |
| `resultFetchMs` / `resultFetchMsMax` | Document Mind result 拉取累计耗时 / 单 shard 最大耗时 |
| `convertMs` / `convertMsMax` | result 转 `ContentBlock[]` 累计耗时 / 单 shard 最大耗时 |
| `mergeShardMs` / `mergeShardMsMax` | shard 页码 offset 与最终合并耗时 |
| `shardWallMs` / `shardWallMsMax` | shard 端到端累计耗时 / 最慢 shard 耗时 |

阶段进度来自 parser 日志关键事件：

- PDF 体检 / inspection：约 5%
- 命中分片或单任务路径：约 10% - 12%
- submit / task_id：约 20% - 28%
- poll：约 35%
- shard 输出 / 合并：按 `completedShards / shardCount` 推进到约 82%
- result_url / result：约 82%
- 转换结果：约 88%
- stage success：100%

## 下一轮重点看

真实教材跑完后，优先记录：

- `parseWallMs`
- `provider`
- `shardCount`
- `completedShards`
- `pollCount`
- `outputBlocks`
- `effectivePagesPerShard`
- `parseWorkerCount`
- `pollWallMsMax`
- `shardWallMsMax`
- `parseKeyPoolSize`
- `parseKeyThrottleCount`
- `parseKeyRetryCount`
- `parseKeyCooldownCount`
- 各 `parseKey.dm-key-N.calls/successes/failures/throttles/totalMs` 是否均衡
- 失败日志中的 provider 错误码、超时点、是否集中在 submit / poll / result download / convert

同教材重复运行已经确认 `33页/片 + 4 worker + 单 Key 并发 1` 稳定优于 `25页/片` 和 `40页/片`，最新 `parseWallMs` 维持在约 147s 档，同时 `parseKeyThrottleCount / retry / failure / cooldown` 仍为 0。当前可直接将 33 页档作为 4 组 AK/SK 的解析推荐档；若后续增加真实 AK/SK 组数，再在保持 `MAX_INFLIGHT_PER_KEY=1` 的前提下提升 `SHARDING_MAX_CONCURRENCY`。

## 可优化空间

在不改变解析结果的前提下，优先考虑这些低风险方向：

1. 解析日志结构化：让 provider 直接发事件对象，而不是靠字符串推断进度。
2. 分片耗时明细：当前已记录 submit / poll / result fetch / convert / merge / shard wall 的累计与最大耗时；若还不够，再补 per-shard 明细列表。
3. 分片并发自适应：当前 Document Mind shard 并发受 key 池容量约束，先通过更均衡的小 shard 降低长尾，不盲目把单 key inflight 放大。
4. 下载与转换重试：仅针对网络瞬断和临时 5xx 做有限重试，避免吞掉 provider 业务错误。
5. 结果体积观测：记录 result zip / markdown / visualLayoutInfo 大小，判断慢点是否在云端解析还是结果传输/转换。
6. Document Mind 空结果观测：跟踪 `ALIYUN_DOCUMENT_MIND_RESULT_FETCH_RETRIES` 和 `ALIYUN_DOCUMENT_MIND_EMPTY_RESULT_RETRIES` 是否被触发；若频繁触发，优先降低 shard 并发或缩小每片页数。

## 不改变的边界

- 不改变默认 provider。
- 不改变 MinerU / Document Mind 的输出语义。
- 不改变 PDF 分片阈值、页码 offset 和 shard 合并顺序；自适应策略只改变每片页数上限内的 shard 粒度。
- 不引入解析完成前的半成品入库。
- 不把 provider fallback 做成静默行为。
