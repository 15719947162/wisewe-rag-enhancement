# 三层切片增强优化追踪

本文用于连续记录三层切片 LLM 增强的优化尝试、真实教材任务结果和回退判断。解析、切片、向量化仍按三条独立优化线跟踪；本文只讨论 `chunk` 阶段，尤其是 enhanced 外呼墙钟耗时。

## 固定约束

所有纳入本文的严格等价优化必须保持：

- parent / child / enhanced 三层切片原则不变
- 增强触发条件、prompt、模型和生成参数不变
- 每个 text、fragment、table、image 任务仍独立调用原增强逻辑
- 最终结果仍按原 slot 顺序合并
- relation 类型、构建语义和在线召回边界不变
- 不记录或暴露真实 API key

允许变化的范围主要是：任务调度方式、worker/client 复用、全局并发预算、同源 API key 分摊、限流重试和观测指标。

## 当前结论

- 已完成真实任务验证的最佳档是 `text/table/image=16/3/4`、全局并发 `70`、LLM key 池大小 `5`。
- 任务 `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` 的 `chunkMs=70329`、`enhanceWallMs=69365`，峰值并发 `70`，无限流、重试和增强失败。
- 相比 `60` 并发任务，增强墙钟耗时继续下降约 `12.8%`；相比 18 并发动态调度任务下降约 `72.1%`。
- 多 key 池已经真实分摊请求，但在全局并发上限不变且没有发生限流时，单独启用 key 池不会自然带来明显提速。它的主要价值是提供更高并发的容量基础和限流隔离。
- `70` 档已完成真实任务验证：它不改变 prompt、模型参数、触发条件、slot 合并顺序或最终 chunk。后续不建议继续盲目加压到更高并发；当前主链路瓶颈已经转向解析与 embedding stage 内部后处理。

## 优化过程

下表中的 `chunkMs` 来自任务阶段指标或既有截图记录，`enhanceWallMs` 同时用任务日志末尾的“增强墙钟耗时”校验。早期任务尚未具备全部观测字段，因此缺失项记为 `-`。

| 日期 | 尝试 / 方案 | 任务 ID | 文件 | `chunkMs` | `enhanceWallMs` | 并发 / key 池 | 结果与判断 |
|---|---|---|---|---:|---:|---|---|
| 2026-06-09 | 原始串行/低并发基线 | `c170ad27-c8c0-4ad5-be9f-0cd2f1608d33` | `25.神经病学 第9版.pdf` | 7774156 | - | 早期实现 | 切片成为全链路绝对瓶颈；当时还没有详细 `chunkTimings`。因文件不同，只作为问题基线，不与后续中医学任务做严格同比。 |
| 2026-06-09 | 第一轮 `parallel_ordered` 有序并发 | `7eb31499-c306-4899-836d-db9b63d59f48` | `36.中医学 第9版.pdf` | 982313 | 981948 | 早期并发档 | 相比原始瓶颈大幅改善，同时验证有序并发方案可行，但增强外呼仍接近 16 分钟。 |
| 2026-06-09 | 早期并发复测 | `af6df146-6086-4f3d-9f12-325865a8c4ec` | `36.中医学 第9版.pdf` | - | 926416 | 早期并发档 | 属于此前一次尝试，墙钟耗时仍在 15 分钟级，说明仅有初步并发还不够。 |
| 2026-06-09 | 提高增强并发 | `a0456bf4-9bdc-452b-a31a-d4c5896f22a7` | `36.中医学 第9版.pdf` | 592001 | 591708 | 提升 worker | 继续下降到约 9.9 分钟，证明主要收益来自压缩 enhanced 外呼长尾。 |
| 2026-06-10 | 并发档继续调优 | `c8692c4d-2b02-4a51-a6bc-96de77318cce` | `36.中医学 第9版.pdf` | 344186 | 343938 | 递增验证档 | 进入约 5.7 分钟区间。 |
| 2026-06-10 | 相邻配置复测 | `15488b41-3fa3-46e4-a616-d48d398821b8` | `36.中医学 第9版.pdf` | 365357 | 364990 | 递增验证档 | 与上一轮处于同一量级，表明 provider 波动会造成几十秒偏差，单次结果不能单独决定档位。 |
| 2026-06-10 | 动态借用调度，hard cap `18` | `5bc9f5f4-c66c-4f37-a4f6-b225a4bad022` | `36.中医学 第9版.pdf` | 249148 | 248692 | peak `18`，单 key | `958` 个增强任务、失败 `0`；动态调度可把空闲池容量借给仍有积压的任务类型，效果明确。 |
| 2026-06-10 | `16/3/4 + 22` | `508c6fe3-f5d6-4a0c-8192-98dfbabbaaec` | `36.中医学 第9版.pdf` | 205305 | 204951 | peak `22`，单 key | 继续提速，失败 `0`；22 并发在当轮 provider 条件下稳定。 |
| 2026-06-11 | `22` + LLM key 池 | `2da514c3-8e70-403c-b0d9-786505e56c0d` | `36.中医学 第9版.pdf` | 217279 | 216913 | peak `22`，pool `5` | key 池生效，但 throttle/retry/failure 均为 `0`；并发 cap 未提高时没有稳定提速。 |
| 2026-06-11 | `22` + 匿名 per-key 观测 | `cb4b3b9b-5e63-4eb1-9e73-43d8083b27ab` | `36.中医学 第9版.pdf` | 221422 | 221164 | peak `22`，pool `5` | 每个 key 调用数约为 `194/186/185/182/185`，分布均匀；throttle/retry/failure 均为 `0`。 |
| 2026-06-11 | `26` + LLM key 池 | `f6a4348f-4a12-4000-9d4f-5397138d9917` | `36.中医学 第9版.pdf` | 175656 | 175282 | peak `26`，pool `5` | per-key 调用约 `189/197/178/182/186`，无限流、重试或失败。 |
| 2026-06-11 | `34` + LLM key 池 | `bc79f5ef-4673-4bc5-943a-225b316bc463` | `36.中医学 第9版.pdf` | 144412 | 143985 | peak `34`，pool `5` | 此前最佳已验证档；per-key 调用约 `186/189/189/186/182`，throttle/retry/failure 均为 `0`。 |
| 2026-06-11 | `42` + LLM key 池 | `617c207a-0d9c-4e46-8736-a5f0a7050aad` | `36.中医学 第9版.pdf` | 120517 | 120164 | peak `42`，pool `5` | 此前最佳已验证档；per-key 调用约 `184/186/180/190/192`，throttle/retry/failure 均为 `0`。 |
| 2026-06-11 | `50` + LLM key 池 | `0317ee49-b378-47f9-b1e5-e733a6661388` | `36.中医学 第9版.pdf` | 96101 | 95695 | peak `50`，pool `5` | 此前最佳已验证档；per-key 调用约 `188/180/184/190/190`，throttle/retry/failure 均为 `0`。 |
| 2026-06-11 | `60` + LLM key 池 | `64cc88d6-c222-4793-bb86-bd41af8ffc24` | `36.中医学 第9版.pdf` | 80165 | 79579 | peak `60`，pool `5` | 此前最佳已验证档；per-key 调用约 `194/179/181/193/188`，throttle/retry/failure 均为 `0`。 |
| 2026-06-11 | `70` + LLM key 池 | `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` | `36.中医学 第9版.pdf` | 70329 | 69365 | peak `70`，pool `5` | 当前最佳已验证档；per-key 调用约 `187/188/176/186/195`，throttle/retry/failure 均为 `0`。 |

## 观测能力演进

优化过程不仅提高了并发，也逐步补齐了判断“是否真的更快”的证据：

1. 早期只有 `chunk.latencyMs`，无法区分基础切片、LLM 增强和关系构建。
2. 增加 `chunkBaseMs`、`enhanceWallMs`、各任务类型累计耗时、`mergeChunksMs` 和 `linkRelationsMs`。
3. 增加 `enhanceTasks`、各类型任务数、`enhanceFailures` 和 `enhanceClientReuse`。
4. 动态调度阶段增加 `enhanceScheduler`、`enhanceMaxConcurrency` 和 `enhancePeakConcurrency`。
5. 多 key 阶段增加 key 池大小、限流、冷却和换 key 重试计数。
6. per-key 阶段增加匿名 `calls/successes/failures/throttles/totalMs`，用于证明请求是否均匀分摊。

## 每轮复盘口径

后续每次真实教材任务至少记录：

```text
task_id=
file=
chunkMs=
chunkBaseMs=
enhanceWallMs=
enhanceTasks=
enhanceMaxConcurrency=
enhancePeakConcurrency=
enhanceFailures=

enhanceLlmKeyPoolSize=
enhanceVlKeyPoolSize=
enhanceKeyThrottleCount=
enhanceKeyRetryCount=
enhanceKeyCooldownCount=
perKeyCalls=

outputChunks=
decision=keep / retry / raise / rollback
```

比较时优先选择同一 PDF、相近的解析内容块数和增强任务数。provider 延迟存在自然波动，建议至少看相邻两次结果或同时比较 `enhanceWallMs`、失败/限流和真实峰值并发，不只看单次 `chunkMs`。

## 70 档结果与回退

`70` 档已满足以下条件，可认为本轮继续优化有效：

- `enhanceMaxConcurrency=70`，且 `enhancePeakConcurrency` 明显高于 `60`
- `enhanceWallMs` 相比 60 档有稳定下降
- `enhanceFailures=0`
- `enhanceKeyThrottleCount` 和 `enhanceKeyRetryCount` 没有明显上升
- per-key 调用仍大致均匀，没有单 key 集中失败或耗时异常

当前建议保持 `70`，不要继续直接扩大到更高并发。理由是本次任务中切片已经降到 `70329ms`，而解析为 `152181ms`、embedding stage 为 `104651ms`；继续压切片的边际收益已经小于拆解和优化后续阶段。

出现以下任一情况，优先回退到已经验证稳定的 `60`；若仍不稳，再回退到 `50`、`42`、`34` 或 `26`：

- 墙钟耗时没有收益或明显回退
- throttle、retry、cooldown 或 failure 开始持续增加
- provider 长尾加重，真实 peak 长期达不到配置上限
- 单 key 错误或总耗时明显偏离其他 key

## 关联文档

- [切片性能跟踪](./chunk-performance.md)
- [入库性能三线跟踪](./ingestion-performance-tracks.md)
- [三层切片增强性能与知识图谱演进](./three-layer-enhancement-performance.md)
- [三层切片规则](../../rule/hierarchical-chunking.md)
- [离线入库链路](../../pipeline/offline-ingestion-pipeline.md)
