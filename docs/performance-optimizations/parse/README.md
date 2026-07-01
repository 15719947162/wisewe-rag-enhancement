# 解析性能优化档案

本文归档解析线的性能优化过程和技术演进。解析线只讨论 PDF / 文档解析、分片、provider 调用、结果获取、内容块转换和解析阶段观测，不把切片增强或向量化耗时计入解析收益。

面向汇报的连续过程版见：[Document Mind 解析优化过程](./document-mind-optimization-process.md)。

**结档状态：** 阿里 Document Mind 解析性能优化已于 `2026-06-17` 结档。当前封存档为 `33页/片 + 4 worker + 单 Key 并发 1 + probe1 + No-LLM + markdown,visualLayoutInfo + 4 组已验证 AK/SK`；后续不再继续围绕同一 provider 做常规调参。

## 边界

解析线负责：

- 保存或读取原始 PDF。
- PDF 体检、按页拆分和 shard 合并。
- MinerU / Document Mind provider 调用。
- result_url / markdown / visualLayoutInfo 获取与转换。
- 输出全局页码稳定的 `ContentBlock[]`。

解析线不负责：

- 三层切片 enhanced worker。
- embedding batch 或 semantic linker。
- pgvector 写库。

## 当前实现口径

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED=true
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB=150
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES=50
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED=false
ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST=false
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE=true
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
```

MinerU 与 Document Mind 复用 `core/parser/pdf_sharding.py` 的 PDF 体检、分片、页码 offset 和合并逻辑。入库侧通过 `_ParseStageTracker` 把 parser 日志映射为 parse progress 和 metrics。

## 优化过程

| 时间 | 事件 / 方案 | 目标 | 判断 |
|---|---|---|---|
| 2026-06-05 | MinerU 大文件分片解析 | 避免 100MB+ / 百页级 PDF 单任务拖慢或失败 | 已落地 PDF 体检、按页拆 shard、多 worker 云解析、全局页码合并 |
| 2026-06-08 | Parser Provider 可切换 | 保留 MinerU 默认，同时引入 Document Mind POC 做显式 A/B | 不做静默 fallback，避免质量和成本不可解释 |
| 2026-06-09 | Document Mind 大文件自动分片 | 规避 `DocSizeLimitError`，让 Document Mind 承接大教材 | 超过 150MB 或 50 页时分片，并按原始页码合并 |
| 2026-06-10 | 解析阶段进度与 metrics | 解决解析看似假死、缺少 shard 进度的问题 | 新增 `provider/shardCount/completedShards/pollCount/parseWallMs/outputBlocks` |
| 2026-06-11 | Document Mind 空结果保护 | 修复 shard success 但结果体无 markdown/layout/page records | 同 job 有限重拉，仍为空时仅重提当前 shard |
| 2026-06-12 | Document Mind 分片并发上限曾提升到 `100` | 随解析 key 池增加，验证更高 shard submit/poll/result 调度容量 | 真实任务显示高并发会放大 provider 隐式排队风险，当前已回到独立 `4 worker` 稳定档 |
| 2026-06-13 | 解析凭证池与 shard 并发上限解耦 | 保留 6 组 AK/SK 轮换能力，但避免新增 key 自动拉高解析 worker | 默认 `SHARDING_MAX_CONCURRENCY=4`、`PAGES_PER_SHARD=33`、`MAX_INFLIGHT_PER_KEY=1` |
| 2026-06-12 | 配置中心验证 `MAX_INFLIGHT_PER_KEY=1` 与页数档 | 排除同一 AK/SK 多 in-flight 的隐式排队，并寻找 4 组 AK/SK 下更稳的 shard 粒度 | `33页/片 + 4 worker + 单 Key 并发 1` 在任务 `080ed361-f4f5-4be6-b134-99d53ecbd19a` 跑到 `parseWallMs=147212`，优于 25 页档 `178903` 和 40 页长尾档 `347241` |
| 2026-06-14 | 关闭 Document Mind 托管 LLM/VLM | 避免 provider 侧增强长尾，同时保留 `markdown,visualLayoutInfo` 图文证据 | parse-only 降到约 `97-102s` |
| 2026-06-15 | No-LLM 完整入库验证 | 确认 18.5k parse blocks 不会把 clean/chunk 压垮 | 完整入库成功，最佳 warm run `212885ms`；COPY run parse `124088ms` |
| 2026-06-15 | 复杂度感知分片 + 重片优先调度 | 不缓存解析结果、不增加 worker，通过 shard 边界和提交顺序降低慢 shard 长尾 | 已落地代码与指标，但同教材 A/B 显示固定分片更快：fixed `100560ms`，weighted `136111ms`；默认保持关闭，仅作显式实验档 |
| 2026-06-15 | 快验证漏斗并入主线 | 避免每轮都跑完整云端 A/B，先用本地 dry-run 和 canary 页段收敛 | 新增 `scripts/plan_document_mind_shards.py`；228MB / 494 页教材 dry-run 约 `11.9-14.8s`，输出可直接传给 canary benchmark 的页段 |
| 2026-06-15 | 混合解析 dry-run 探针 | 判断是否能用本地 PyMuPDF 文本层绕开部分云端 OCR/layout | 已加入 `hybridParse` dry-run 字段；228MB / 494 页扫描教材本地文本候选 `0%`，47MB / 396 页常用教材候选约 `5.3%`，当前代表样本不适合优先投入混合解析 |
| 2026-06-16 | 状态响应结果短路 | 当 `QueryDocParserStatus` 的 success 响应已包含 markdown / 结构化内容时，跳过额外 `GetDocParserResult` 往返 | 已落地保守短路：只有状态响应能转换出非空主内容块才跳过 result fetch；纯页面图片 layout 仍继续拉取 result 并合并图片证据。不跨任务缓存、不复用历史解析结果 |
| 2026-06-16 | 轮询间隔 canary + 整本复核 | 直接验证 `POLL_INTERVAL=1/2/3` 是否能形成更优默认档，并复核当前稳定档 | 4 shard canary 受 provider 队列波动影响明显：`poll1=285671ms`、`poll2=241954ms`、`poll3=34765ms`，不下调默认间隔。整本复核当前稳定档 `parseWallMs=135405`、`18500 blocks / 475 image / 81 table`、throttle/retry/cooldown 为 0 |

## 技术演进

1. 从单 PDF 云解析演进为“本地体检 + 自动分片 + 多 worker 云解析”。
2. 从单一 MinerU provider 演进为 provider 层显式切换。
3. 从只看最终成功 / 失败演进为 parse stage progress 和 shard metrics。
4. 从整本失败后重跑演进到单 shard 异常可定点重拉 / 重提。
5. 从只拿 markdown 演进为 `markdown,visualLayoutInfo` 双输出兜底。
6. 从固定 40 页 shard 演进为自适应 shard 粒度：40 仍是上限，多 key 并发时按目标波次数收敛到更小 shard，降低 provider 长尾。
7. 从“小 shard + 4 worker”验证转向“更多解析 worker”验证：真实任务 `19979c30-f8bd-4d44-a33c-6e3aeb4b2ba6` 已确认 4 组 AK/SK 均衡分摊 16 个 shard，但 `parseWallMs=175023`，未优于上一轮 10 shard 的 `156727ms / 165829ms`。
8. 随解析 key 池继续增加，Document Mind 分片解析上限曾提升到 `ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=100`；真实任务复盘后确认该方向容易放大 provider 侧隐式排队，当前已回退为独立 `4 worker` 稳定档。
9. 真实验证显示同一 AK/SK 的 `MAX_INFLIGHT_PER_KEY=2` 会放大 provider 侧隐式排队长尾：任务 `4423d93c-15c0-4fca-968c-a15d850333af` 在 8 worker 下 `parseWallMs=293422`，且无显式限流/重试/冷却。因此当前推荐用真实 AK/SK 组数扩大 worker，而不是放大单 Key in-flight。
10. 4 组 AK/SK 下的当前推荐解析档为 `SHARDING_PAGES_PER_SHARD=33`、`SHARDING_MAX_CONCURRENCY=4`、`MAX_INFLIGHT_PER_KEY=1`。最新同教材复盘任务 `080ed361-f4f5-4be6-b134-99d53ecbd19a` 使用 12 shard / 4 worker，`parseWallMs=147212`、限流/重试/冷却均为 0；上一轮同档任务 `43e3e97f-4186-4874-9774-464bb9bd0e97` 为 `147618ms`，说明该档在重复运行中保持稳定。
11. 关闭 Document Mind 托管 LLM/VLM 后，parse-only 最快降到 `97082ms`，完整入库最佳 warm run 的 parse 为 `84375ms`，COPY run 为 `124088ms`。因此 No-LLM 档已不是只适用于 parse-only 的实验档，可以作为完整入库当前推荐口径。
12. 无缓存优化第一段已落地复杂度感知分片：`inspect_pdf(profile_pages=True)` 只在 Document Mind weighted sharding 开启时逐页提取轻量特征；`plan_weighted_page_ranges()` 保持 shard 数与页数上限，优先最小化最重 shard；提交阶段可按 weight 从高到低启动，最终仍按全局页码合并。真实同教材 A/B 结论是当前教材在 396 页 / 33 页每片下刚好形成 12 个满 shard，weighted 边界空间有限且增加约 `10s` inspection 开销，因此默认关闭。
13. `scripts/plan_document_mind_shards.py` 已补 `hybridParse` dry-run 画像，用保守阈值筛出“可本地文本解析”的页：默认要求页面有至少 `120` 个 PyMuPDF 文本字符、非疑似扫描页、且无图片/绘图对象。两本代表样本验证显示：大扫描教材 `0/494` 页可本地化；常用教材 `21/396` 页可本地化，预估云端页数只减少 `5.3%`。因此当前解析瓶颈不应优先押注混合解析，除非后续出现 born-digital 文本层占比明显更高的教材。

## 关键指标

| 指标 | 用途 |
|---|---|
| `provider` | 区分 MinerU / Document Mind |
| `shardCount` | 判断是否进入分片路径 |
| `completedShards` | 判断解析是否真在推进 |
| `pollCount` | 观察 provider 轮询长尾 |
| `parseWallMs` | 解析总墙钟耗时 |
| `outputBlocks` | 防止性能调参导致解析结果回退 |
| `effectivePagesPerShard` | 本次实际 shard 页数，用于确认自适应分片是否生效 |
| `parseKeyPoolSize * parseKeyMaxInflightPerKey` | 判断 key 池容量是否足够覆盖当前独立 shard 并发上限 |
| `parseWeightedShardingEnabled` / `parseHeavyShardFirstEnabled` | 确认本轮是否启用复杂度感知分片和重片优先调度 |
| `parseShardSaveGarbage` / `parseShardSaveDeflate` | Confirm local shard PDF save strategy. Default is `1 / true` for lower split overhead. |
| `resultFetchSkippedByStatus` | 状态响应已携带完整主内容时跳过单独 result 获取的次数。 |
| `statusConvertMs` / `statusConvertMsMax` | 尝试从状态响应直接转换内容的耗时。 |
| `parseShardWeightTotal/Max/Min/Avg` | 观察 shard 复杂度分布，定位是否仍存在明显重片 |
| `parseHeaviestShardIndex` / `parseHeaviestShardPages` | 标记最重 shard 的编号和页数，便于和 `shardWallMsMax` 对照 |
| `submitWallMs` / `pollWallMs` / `resultFetchMs` / `convertMs` / `mergeShardMs` | 解析内部耗时拆账 |
| `pollWallMsMax` / `shardWallMsMax` | 判断最慢 shard 是否仍拖慢总解析 |

## 当前结论

- Document Mind 分片解析已经可用，但 provider 成功状态下仍可能短暂返回空结果。
- 空结果不是切片假死，也不是向量化问题，应归入解析 provider 结果获取稳定性。
- 当前结论是：多 AK/SK 已经生效，但同一 AK/SK 的并发 in-flight 不应放大；4 组 AK/SK 下，`33页/片 + 4 worker + 单 Key 并发 1 + No-LLM + markdown,visualLayoutInfo` 是当前最优已验证解析档。
- No-LLM 完整入库已验证，18.5k parse blocks 没有导致 clean/chunk 失控。
- 当前推荐继续使用固定分片稳定档：`33页/片 + 4 worker + 单 Key 并发 1 + probe1 + No-LLM + markdown,visualLayoutInfo`。
- 复杂度感知分片和重片优先已实现，但同教材 A/B 不优于固定分片，默认关闭，仅在明显复杂度不均、扫描页/图表页集中或页数不能整除固定 shard 的教材上显式验证。
- 若 Document Mind 的状态响应已经携带 markdown / 结构化主内容，解析器会在单次任务内直接转换该响应并跳过额外 result fetch；这只是省掉当前 job 的一次 API 往返，不是解析结果缓存。
- 最新整本复核显示 `resultFetchSkippedByStatus=0`，说明当前 Document Mind 响应仍主要需要 `GetDocParserResult`；T-102 短路保留为无害兜底，不作为主要收益来源。
- `ALIYUN_DOCUMENT_MIND_POLL_INTERVAL=1/2/3` canary 没有证明更密集轮询能改善整体墙钟，反而暴露 provider 队列波动远大于轮询检测延迟；默认保持 `3s`。
- 后续收敛默认走“本地 dry-run -> canary 页段 -> 完整 parse-only A/B”的漏斗；只有 dry-run 或 canary 出现明确正信号，才启动完整 Document Mind A/B。
- 若解析时长仍不可接受，下一步应评估替代解析 provider、文档质量/证据保留取舍，或产品层 basic-ready 流程。不建议继续通过同一 AK/SK 多 in-flight 堆并发。

## 快验证流程

目标是把每轮实验的反馈时间从“完整云端解析一整本教材”前移到“本地秒级/十几秒级画像 + 少量页段 canary”。该流程不缓存解析结果，也不复用历史 `ContentBlock[]` 或历史 shard 结果；canary 和 full A/B 仍会真实调用 Document Mind。

### 1. 本地 dry-run

```powershell
python scripts/plan_document_mind_shards.py `
  --pdf-path data/uploads/0dcadf4f-1d9b-4ed3-b565-0b438a4fe14a.pdf `
  --pages-per-shard 33 `
  --top-n 4
```

输出写入 `data/results/document_mind_shard_plan_dry_run.jsonl`，同时打印 fixed / weighted 两套 shard 规划、复杂度分布、`canaryPageRangesArg`。

本次主线 dry-run 结果：

- PDF：`228.03MB` / `494` 页 / 疑似扫描型。
- 耗时：约 `11.9-14.8s`。
- fixed 与 weighted 的 `weightMax` 均为 `825`，`balanceRatio=1.002`，说明该教材在当前轻量画像下基本均匀重。
- 建议 canary 页段：`33-65,1-33,66-98,99-131`。
- `hybridParse` 显示本地文本候选 `0/494` 页，`estimatedCloudPageReductionPct=0.0`，推荐 `keep_cloud_full`。

常用教材对照 dry-run：

- PDF：`44.87MB` / `396` 页 / 前几页有文本层，主体仍以扫描页为主。
- `hybridParse` 显示本地文本候选 `21/396` 页，预估云端页数减少约 `5.3%`。
- 结论：对当前代表样本，混合解析不能显著缩短云端解析墙钟；后续仅在本地文本候选页占比达到约 `20%` 以上时再跑 hybrid canary，达到约 `50%` 以上时再考虑接入正式混合 parser。

### 2. canary 页段解析

当 dry-run 发现 weighted 可能降低最重 shard，或需要验证 provider 侧长尾时，再跑小样本 canary：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\benchmark_document_mind_parse.ps1 `
  -OutputJsonl data/results/document_mind_parse_canary_benchmark.jsonl `
  -CandidateNames p33-c4-no-llm-layout-fixed,p33-c4-no-llm-layout-weighted `
  -PageRanges "33-65,1-33,66-98,99-131"
```

`-PageRanges` 会在容器内临时抽取这些页组成 canary PDF，再按候选配置真实调用 Document Mind。记录里会保留 `sourcePdfPath` 和 `canaryPageRanges`，便于和 dry-run 页段对应。

### 3. 完整 A/B 门槛

满足以下任一条件才跑完整 parse-only A/B：

- dry-run 显示 weighted 的 `weightMax` 至少降低约 `10%`。
- dry-run 显示 fixed / weighted 的 heaviest 页段差异明显，且页段集中在扫描页、图表页或大图片区域。
- canary 的 `parseWallMs` 或 `shardWallMsMax` 比固定分片改善约 `10%`，且输出块数、图片块、表格块不回退。

若 dry-run 像本次一样显示 `weightMax` 不降、`balanceRatio` 已接近 `1.0`，默认不再启动完整 weighted A/B，直接保留固定分片稳定档。

## 无缓存后续方案

用户已明确要求后续不使用解析结果缓存，因此本节只保留“每次真实调用解析 provider”的优化方向。解析结果缓存、跨任务复用 `ContentBlock[]`、复用历史 shard 结果均不作为后续方案。

### 优先级 1：复杂度感知分片 + 重片优先调度

目标是在不增加 worker、不增加单 Key in-flight、不改变输出格式的前提下，降低最慢 shard 拖住整本教材的概率。

已实现：

1. `inspect_pdf(..., profile_pages=True)` 在 weighted sharding 开启时提取 per-page 文本字符数、文本块数、图片数、绘图对象数和疑似扫描页。
2. `plan_weighted_page_ranges()` 保持固定页数分片的 shard 数量，使用连续页段动态规划尽量降低最重 shard。
3. `split_pdf_to_weighted_shards()` 写出带 `weight` 的 PDF shard；profile 不完整时回退固定页数分片。
4. `parse_pdf_sharded()` 支持 `ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED=true` 与 `ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST=true`，提交顺序按 shard weight 从高到低；真实同教材 A/B 后默认保持关闭。
5. 合并顺序仍按全局页码和 shard order，不改变 `ContentBlock[]` 语义，也不复用任何历史解析结果。

真实 A/B：

| 候选 | parseWallMs | shardWallMsMax | inspectMs | outputBlocks / image / table | 结论 |
|---|---:|---:|---:|---|---|
| `p33-c4-no-llm-layout-fixed` | `100560` | `84993` | `992` | `18500 / 475 / 81` | 当前更快，保持默认 |
| `p33-c4-no-llm-layout-weighted` | `136111` | `82171` | `10119` | `18500 / 475 / 81` | 最慢 shard 略降，但总墙钟更差，默认关闭 |

预期收益：

- 对包含大量图表、扫描页或复杂版面的教材，降低 `shardWallMsMax`。
- 让慢 shard 更早开始，减少最后一波只剩重片的等待。
- 保留当前稳定并发档，避免再次触发 Document Mind provider 隐式排队。

验证：

| 指标 | 通过标准 |
|---|---|
| `parseWallMs` | 同教材 2-3 次重复运行的中位数优于当前 baseline |
| `shardWallMsMax` | 明显下降，或最慢 shard 不再集中在最后一波 |
| `outputBlocks/outputImageBlocks/outputTableBlocks` | 不显著回退 |
| `parseKeyThrottleCount/retry/cooldown` | 保持 0 或不高于 baseline |
| 页码连续性 | 分片合并后 `page_idx` 不乱序、不重复异常 |

### 优先级 2：超长 shard 的 split-on-timeout 兜底

默认关闭，仅作为极端长尾保护。它不是 hedged shard，不重复提交同一个 shard；只有当某个 shard 明确超时或失败时，才把该 shard 二分后重跑。

适用场景：

- 单 shard 超过硬阈值并失败。
- provider 返回临时 5xx / 超时，但非业务错误。
- 同教材经常出现某个页段 300s+ 长尾。

边界：

- 不在正常运行中提前复制任务。
- 不吞掉 provider 业务错误。
- 必须记录 `splitOnTimeoutCount`、`splitShardOriginalPages`、`splitShardRetryPages` 和额外 provider 请求次数。

### 优先级 3：显式纯文本快档

默认仍保持 `markdown,visualLayoutInfo`，因为它保留图片/layout 证据。`markdown` only 只能作为用户明确接受“丢图片证据、换更快解析”的显式快档。

验证要求：

- 标注 `parseEvidenceMode=text_only`。
- RAG smoke query 必须覆盖图表引用类问题。
- 若图表问题召回质量明显下降，不进入默认档。

### 优先级 4：Provider 替换复验

如果 Document Mind 首次真实解析仍无法接受，应重新做 provider 级 A/B，而不是继续堆当前 provider 的并发。

候选：

- 当前 Document Mind No-LLM layout 档。
- MinerU 分片档。
- 后续可接入的新 OCR/layout provider。

要求：

- 不做静默 fallback。
- 同教材同问题集比较解析时长、图片/表格证据保留、最终 RAG 引用质量。
- provider 切换必须是显式配置或产品档位。

## 回退规则

| 现象 | 处理 |
|---|---|
| shard 空结果偶发 | 保持当前配置，依赖同 job 重拉和单 shard 重提 |
| 空结果频繁出现 | 降低 Document Mind shard 并发或缩小 pages per shard |
| 单 Key 并发放大后 `parseWallMs` 上升或出现长尾 | 回退到 `MAX_INFLIGHT_PER_KEY=1` |
| `33页/片` 出现单 shard 超过 120s 的 provider 长尾 | 回退到 `25页/片`，优先隔离复杂页段 |
| 后续增加真实 AK/SK 组数 | 保持 `MAX_INFLIGHT_PER_KEY=1`，把 `SHARDING_MAX_CONCURRENCY` 提升到物理凭证组数 |
| provider 业务错误明确失败 | 不静默 fallback，记录错误并失败 |
| 分片合并页码异常 | 回退到上一稳定 parser/sharding 配置，优先修合并逻辑 |

## 关联材料

- [历史解析性能跟踪](../../archive/performance-discussions/parse-performance.md)
- [Document Mind 关闭托管 LLM/VLM 增强验证](../../archive/performance-discussions/document-mind-managed-llm-off-2026-06-14.md)
- [Document Mind 完整入库 No-LLM 验证](../../archive/performance-discussions/document-mind-full-ingestion-2026-06-15.md)
- [Document Mind 慢 key 评分与投机重发验证](../../archive/performance-discussions/document-mind-slow-key-scoring-2026-06-14.md)
- [MinerU 云端解析性能瓶颈](../../archive/performance-discussions/mineru-cloud-performance.md)
- [Document Mind 分片链路](../../pipeline/document-mind-sharding.md)
- [Parser Provider POC](../../pipeline/parser-provider-poc.md)

## 2026-06-16 local shard save update

Document Mind sharding now defaults local shard PDF saves to
`ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1` and
`ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE=true`. This does not cache parse
results and does not change page ranges, cloud requests, page offsets, or block
merge order. It only lowers the local `splitMs` paid before every real provider
run.

On the 47MB / 396 page benchmark PDF, a local microbenchmark showed
`garbage=4,deflate=true` at about `782ms` for 12 shards and
`garbage=1,deflate=true` at about `282ms`, with total shard bytes staying around
`47.29MB`. If a compatibility investigation needs maximum PDF cleanup, set
`ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=4`.

## 2026-06-16 scheduler regression fix

The latest slow run was not caused by Document Mind managed LLM/VLM being
enabled again. Runtime verification showed
`ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false`. The regression came from the
Docker backend still running with a 6-key Document Mind pool while the stable
parse profile only needs 4 workers. Unknown or slow keys could still enter a
steady-state parse run, and the observed slow run included `dm-key-5` with much
higher latency while `dm-key-6` was unused.

The credential pool now tracks `parseKeyActiveTarget`. Sharded parsing sets this
target to the actual worker count. Cold start can still discover enough keys to
fill the active target, but after that target is met, the scheduler stops
probing additional unknown keys and keeps choosing from known-latency keys. The
local `.env` has also been narrowed to the 4 verified Document Mind credentials
for the current 4-worker profile.

The backend was rebuilt with:

```powershell
docker compose up -d --build backend
```

Container verification after rebuild:

```text
pool_count=4
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY=1
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
```

Real parse-only verification on
`/app/data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf` is recorded in
`data/results/document_mind_scheduler_4key_benchmark.jsonl`. The valid run:

```text
candidate=p33-c4-no-llm-layout-fixed
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

The first two records in that JSONL are Docker CLI permission/config failures
from the sandboxed shell and did not run the parser. The third record is the
valid benchmark run.

## 2026-06-17 Document Mind closure

Alibaba Document Mind parser performance optimization is now archived. The
final accepted profile is the 4-worker / 4-key No-LLM layout profile verified
above:

```text
33 pages per shard
4 parser workers
1 in-flight job per key
KEY_PROBE_CONCURRENCY=1
LLM_ENHANCEMENT=false
OUTPUT_FORMAT=markdown,visualLayoutInfo
weighted/heavy-first/hedged shard disabled by default
```

No further routine tuning will be done on this provider path. Reopen only for a
clear provider behavior change, a new document class that invalidates the
current benchmark assumptions, a reproducible regression, an explicit evidence
quality trade-off, or a fresh provider-level A/B.
