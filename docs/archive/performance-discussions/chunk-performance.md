# 切片性能跟踪

## 2026-06-11: 匿名 key 分布观测

为验证 `LLM_API_KEY_POOL` / `VL_API_KEY_POOL` 是否真的分摊增强请求，`chunkTimings` 现在追加匿名 per-key 扁平指标，不记录真实 key。

字段示例：

```text
enhanceLlmKey.llm-key-1.calls
enhanceLlmKey.llm-key-1.successes
enhanceLlmKey.llm-key-1.failures
enhanceLlmKey.llm-key-1.throttles
enhanceLlmKey.llm-key-1.totalMs
enhanceVlKey.vl-key-1.calls
```

这些字段只用于观测分布、限流和累计耗时；不改变增强并发上限、prompt、模型参数、slot 合并顺序或最终切片结果。

本文单独跟踪离线入库中的 `chunk` 阶段，重点是三层切片在保持结果等价前提下的性能空间。

## 当前实现

三层切片主逻辑在 `core/chunker/hierarchical.py`，入库侧由 `backend/services/ingestion_service.py` 调用：

```text
cleaned blocks
-> chunk_base(parent / child / table / image)
-> enhanced task plan
-> parallel_ordered enhanced
-> slot order merge
-> link_related_chunks()
```

10-08 已把 enhanced 生成改为入库前有序并发；10-08b 增加 worker 线程内 OpenAI 兼容 client 复用，并优化 `link_related_chunks()` 的 relation key 去重；260610 dynamic quick 又把固定池升级为动态借用调度。

当前仍坚持严格等价口径：不改变 prompt、触发条件、模型参数、slot 合并顺序、parent/child/enhanced 原则和最终 chunk 结果。

## 当前配置口径

环境变量当前用于控制增强阶段：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

说明：

- text 池承接普通文本与 fragment enhanced。
- table / image 单独限流，避免表格摘要和 VL 图片描述互相拖垮。
- soft cap 允许动态借用，但全局 hard cap 仍限制最大同时外呼。
- 最终结果仍按原 slot 顺序合并。

## 当前新增：增强多 key 池

方案状态：已实现最小闭环。目标是在不改变三层切片原则和结果的前提下，用多个同源 `api_key` 分摊 provider 配额与限流长尾，而不是继续单纯提高全局并发。

配置口径：

```env
# 文本 / 表格 / fragment 增强 key 池；未配置时继续使用 LLM_API_KEY。
LLM_API_KEY_POOL=

# 图片 / VL 增强 key 池；未配置时继续使用 VL_API_KEY；VL 不可用时回退 LLM_API_KEY_POOL。
VL_API_KEY_POOL=

# 仅限限流错误换 key 重试，普通业务/解析错误不跨 key 盲重。
HIERARCHICAL_ENHANCE_KEY_RETRIES=1
HIERARCHICAL_KEY_COOLDOWN_SECONDS=30
```

验证边界：

- 所有 key 必须使用同一 `base_url`、同一模型、同一 prompt、同一生成参数和同一解析逻辑。
- 2026-06-11 已验证 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=26`、`34`、`42`、`50`、`60` 与 `70` 在 5 key 池下能继续压低切片阶段，且 `throttles/retry/failures=0`；当前最佳已验证档为 `70`，不建议在解析和向量化后处理尚未拆账前继续盲目扩大。
- 只有 `429 / rate limit / throttle / quota` 等明确限流错误才触发 key 冷却和换 key 重试；业务错误、普通模型输出错误和解析错误不跨 key 盲目重试。
- 当前指标只记录池大小、限流、冷却和重试次数，不记录真实 key。

新增观测字段：

| 字段 | 含义 |
|---|---|
| `enhanceLlmKeyPoolSize` | 文本 / 表格 / fragment 增强 key 池大小 |
| `enhanceVlKeyPoolSize` | 图片 / VL 增强 key 池大小 |
| `enhanceKeyPoolSize` | 去重后的总 key 数 |
| `enhanceKeyThrottleCount` | 限流错误次数 |
| `enhanceKeyRetryCount` | 限流后换 key 重试次数 |
| `enhanceKeyCooldownCount` | key 冷却次数 |

## 已有观测字段

`chunk` stage 的 `metrics` / `chunkTimings` 当前包含：

| 字段 | 含义 |
|---|---|
| `chunkBaseMs` | 基础 parent / child / table / image 切片耗时 |
| `enhanceWallMs` | enhanced 并发阶段墙钟耗时 |
| `enhanceTextMs` | 文本增强累计耗时 |
| `enhanceFragmentMs` | fragment 增强累计耗时 |
| `enhanceTableMs` | 表格增强累计耗时 |
| `enhanceImageMs` | 图片/VL 增强累计耗时 |
| `mergeChunksMs` | enhanced slot 合并耗时 |
| `linkRelationsMs` | 图文/表格引用关系构建耗时 |
| `enhanceTasks` | enhanced 任务总数 |
| `enhanceFailures` | enhanced 失败数 |
| `enhanceClientReuse` | 是否启用 worker 内 client 复用 |
| `enhanceScheduler` | 调度器类型 |
| `enhanceMaxConcurrency` | 全局 hard cap |
| `enhancePeakConcurrency` | 真实峰值并发 |
| `enhanceLlmKeyPoolSize` | 文本 / 表格 / fragment 增强 key 池大小 |
| `enhanceVlKeyPoolSize` | 图片 / VL 增强 key 池大小 |
| `enhanceKeyPoolSize` | 去重后的总 key 数 |
| `enhanceKeyThrottleCount` | 限流错误次数 |
| `enhanceKeyRetryCount` | 限流后换 key 重试次数 |
| `enhanceKeyCooldownCount` | key 冷却次数 |

## 下一轮重点看

真实教材跑完后，优先比较：

- `chunk.latencyMs`
- `chunkTimings.enhanceWallMs`
- `chunkTimings.enhancePeakConcurrency`
- `chunkTimings.enhanceFailures`
- `chunkTimings.enhanceImageMs`
- `chunkTimings.linkRelationsMs`
- `enhanceTasks` 与 text/table/image/fragment 分布

如果 `enhancePeakConcurrency` 长期达不到 hard cap，说明任务类型分布或 provider 响应时间限制了收益；如果 `enhanceFailures` 增加，优先降 `image` 或总 hard cap，而不是继续扩大并发。`70` 档已跑满且稳定后，下一步优先看解析线与 embedding stage 内部后处理，而不是继续加切片并发。

## 可优化空间

仍在严格等价边界内的候选优化：

1. 更细的增强任务分桶统计：按文本、表格、图片、fragment 输出平均耗时和 P95。
2. provider 限流感知：失败集中在 429 / timeout 时，自动降低下一批并发或增加 backoff。
3. 图文关系构建继续热路径化：如果 `linkRelationsMs` 再次升高，继续按 parent 局部索引优化。
4. 大 parent 局部拆解：只优化 relation 构建的数据结构，不改变 relation 类型和顺序。
5. 可选 enhanced cache：必须显式开启，并把 prompt/model/provider/base_url/version 纳入 cache key。

## 暂不进入本轮的方向

- `ready_basic` 后台渐进增强。
- batch prompt 合并多个 chunk。
- 改 prompt 或改 enhanced 内容协议。
- 改 parent / child / enhanced 的召回边界。
- 按“看起来更快”牺牲最终 chunk 等价性。
