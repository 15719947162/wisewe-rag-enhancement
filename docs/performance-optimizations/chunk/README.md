# 切片性能优化档案

本文归档切片线的性能优化过程和技术演进。切片线只讨论三层切片中的基础切片、LLM / VL enhanced、关系构建和严格等价调度，不把解析下载、provider 结果获取、embedding API 或 pgvector 写库计入切片收益。

## 固定边界

所有已接受的切片性能优化必须保持：

- parent / child / enhanced 三层原则不变。
- enhanced 触发条件、prompt、模型参数不变。
- 每个 enhanced 任务仍按原逻辑独立生成。
- worker 完成顺序不影响最终 slot 合并顺序。
- child 仍是最终 citation，enhanced 命中后折回 child。
- 不记录真实 API key。

允许优化的范围是调度方式、worker/client 复用、并发预算、同源 key 池、限流重试和观测指标。

## 当前已验证档

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=100
HIERARCHICAL_REUSE_LLM_CLIENTS=true
LLM_API_KEY_POOL=<8 keys, redacted>
VL_API_KEY_POOL=<8 keys, redacted>
HIERARCHICAL_ENHANCE_KEY_RETRIES=1
HIERARCHICAL_KEY_COOLDOWN_SECONDS=30
```

2026-06-11 validation: real ingestion task `3a3c1fd0-a0a8-4fea-87eb-2f1c98de3f15` confirmed `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=100` with an 8-key pool. Key pool parsing is capped at 20 unique keys. The run reached `enhancePeakConcurrency=100` with zero throttles/retries/failures.

最新稳定判断：

```text
task_id=3a3c1fd0-a0a8-4fea-87eb-2f1c98de3f15
enhanceMaxConcurrency=100
enhancePeakConcurrency=100
enhanceWallMs=76975
enhanceTasks=1566
enhanceFailures=0
enhanceKeyThrottleCount=0
enhanceKeyRetryCount=0
enhanceLlmKeyPoolSize=8
perKeyCalls=187/189/192/181/200/196/183/183
```

## 优化过程

| 时间 | 方案 / 档位 | 代表任务 | `chunkMs` | `enhanceWallMs` | 判断 |
|---|---|---|---:|---:|---|
| 2026-06-09 | 原始串行 / 低并发基线 | `c170ad27-c8c0-4ad5-be9f-0cd2f1608d33` | 7774156 | - | 切片成为全链路绝对瓶颈 |
| 2026-06-09 | `parallel_ordered` 初版 | `7eb31499-c306-4899-836d-db9b63d59f48` | 982313 | 981948 | 有序并发可行，但 enhanced 仍接近 16 分钟 |
| 2026-06-10 | 动态借用 hard cap `18` | `5bc9f5f4-c66c-4f37-a4f6-b225a4bad022` | 249148 | 248692 | 空闲池容量可被积压类型借用，收益明显 |
| 2026-06-10 | `16/3/4 + 22` | `508c6fe3-f5d6-4a0c-8192-98dfbabbaaec` | 205305 | 204951 | 单 key 下继续提速且失败为 0 |
| 2026-06-11 | `22` + 5 key 池 | `2da514c3-8e70-403c-b0d9-786505e56c0d` | 217279 | 216913 | key 池生效，但 cap 不变时不保证提速 |
| 2026-06-11 | `26` + 5 key 池 | `f6a4348f-4a12-4000-9d4f-5397138d9917` | 175656 | 175282 | 无限流 / 重试 / 失败，可继续阶梯验证 |
| 2026-06-11 | `34 -> 42 -> 50 -> 60` | 多轮中医学任务 | 144412 -> 80165 | 143985 -> 79579 | 真实峰值逐步提高，失败和限流仍为 0 |
| 2026-06-11 | `70` + 5 key 池 | `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` | 70329 | 69365 | 当时最佳已验证档 |
| 2026-06-11 | `100` + 8 key 池 | `3a3c1fd0-a0a8-4fea-87eb-2f1c98de3f15` | 77797 | 76975 | 已验证可打满 100 并发，0 限流 / 重试 / 失败；该任务规模为 1566 个增强任务，不能和 70 档小任务直接按墙钟等量比较 |

更完整逐轮记录见 [三层切片增强优化追踪](../../archive/performance-discussions/chunk-enhancement-optimization-tracker.md)。

## 技术演进

1. 入库前有序并发增强：从逐条 enhanced 等待，演进为并发执行、按原 slot 顺序合并。
2. 子阶段拆账：补 `chunkBaseMs / enhanceWallMs / mergeChunksMs / linkRelationsMs`。
3. 任务类型拆账：补 text / fragment / table / image 任务数与累计耗时。
4. 动态借用调度：text/table/image 使用软上限，全局 hard cap 控制同时外呼。
5. worker 内 client 复用：减少大量 OpenAI 兼容 client / 连接池重复创建。
6. 多 API key 池：`LLM_API_KEY_POOL` 分摊 text/table/fragment，`VL_API_KEY_POOL` 分摊 image/VL。
7. 匿名 per-key 指标：验证 key 是否真实分摊，不泄露真实密钥。
8. 热更新：通过控制台 settings DB override 调整并发档，避免每轮重新构建镜像。

## 关键指标

| 指标 | 用途 |
|---|---|
| `chunkBaseMs` | 验证基础切片是否仍非瓶颈 |
| `enhanceWallMs` | 本线核心优化指标 |
| `enhancePeakConcurrency` | 验证并发档真实生效 |
| `enhanceFailures` | 判断 provider 或输出解析是否恶化 |
| `enhanceKeyThrottleCount` | 判断 key 池是否遭遇限流 |
| `enhanceKeyRetryCount` | 判断是否发生限流后换 key |
| `enhanceLlmKey.llm-key-N.calls` | 验证匿名 key 分布是否均匀 |

## 当前结论

`100` + 8 key 是当前最新已验证切片档，可保持作为下一轮真实任务默认档。最新任务中切片线约 `78s`，但端到端瓶颈已经转到解析阶段；向量化约 `19s`，不是当前主瓶颈。后续不建议继续盲推切片 cap，优先观察解析 shard 长尾和 provider 排队。

本轮只改变 enhanced 外呼并发预算，不改变切片结果。`text/table/image=16/3/4` 是软上限，`HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=100` 是单进程全局硬上限；动态调度器仍按原 slot 顺序合并结果。

当前 `100` 档已完成真实任务验证，用 `3a3c1fd0-a0a8-4fea-87eb-2f1c98de3f15` 上的 8-key 池打满且 0 限流 / 0 重试 / 0 失败。增强多 key 池仍只属于切片增强线，不影响解析线和向量化线。

## 回退规则

| 现象 | 处理 |
|---|---|
| `enhanceFailures` 上升 | 先回退全局 cap 到 `70`，仍不稳再回退 `60` |
| throttle / retry / cooldown 持续上升 | 回退 `70 -> 60`，必要时 `50 -> 42 -> 34 -> 26 -> 22` |
| 单 key 错误集中 | 移除或冷却异常 key，不扩大总并发 |
| 图片/VL 长尾明显 | 优先保持 image=4 或回退到 3 |
| `enhancePeakConcurrency` 打不满 | 先检查全局 cap，再检查任务积压量和 provider 长尾 |

## 相关文档

- [三层切片最终技术方案](../../pipeline/three-layer-chunking-final-solution.md)
- [切片性能讨论](../../archive/performance-discussions/chunk-performance.md)
- [三层切片增强优化追踪](../../archive/performance-discussions/chunk-enhancement-optimization-tracker.md)
- [三层切片优化过程归档](../../archive/performance-discussions/three-layer-chunking-optimization-archive.md)
- [三层切片规则](../../rule/hierarchical-chunking.md)
