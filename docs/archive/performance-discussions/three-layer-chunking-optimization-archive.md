# 三层切片优化过程归档

本文按时间线归档三层切片性能优化过程。它用于复盘“为什么这样调”，不是当前运行方案的唯一入口；当前技术方案见 [三层切片最终技术方案](../../pipeline/three-layer-chunking-final-solution.md)。

## 归档边界

本轮优化只处理三层切片中的 enhanced 外呼吞吐，严格保持以下不变：

- parent / child / enhanced 三层结构不变
- enhanced 触发条件不变
- prompt、模型、温度等生成参数不变
- 每个 enhanced 任务仍独立生成
- worker 完成顺序不影响最终 slot 合并顺序
- 最终 chunk 数、层级语义、引用证据边界不变
- 不记录真实 API key

解析、切片、向量化按三条独立优化线跟踪。不要把解析或向量化的耗时变化误判为切片优化收益。

## 下载机制边界

“下载”属于解析 / 资产准备阶段，而不是 enhanced worker 的职责：

- 原始 PDF 先保存到 `data/uploads/`。
- MinerU 路径会上传 OSS、轮询 `result_url`，再下载结果 ZIP 并提取 `ContentBlock` 与图片资源。
- Document Mind 路径会通过 OpenAPI 获取 markdown / visualLayoutInfo 结果，并转换为 `ContentBlock`。
- 三层切片只消费合并后的 `ContentBlock[]`。
- 图片增强只有在 `image_path` 指向本地可读文件且 VL 配置可用时才读取图片 base64；否则使用图片说明文字走文本 fallback。

因此，结果下载、远端图片 URL 转本地资产、重试和超时都应优先放在解析/资产标准化阶段，不应混进并发增强 worker。

## 优化时间线

| 日期 | 方案 / 档位 | 任务 ID | `chunkMs` | `enhanceWallMs` | 判断 |
|---|---|---|---:|---:|---|
| 2026-06-09 | 原始串行/低并发基线 | `c170ad27-c8c0-4ad5-be9f-0cd2f1608d33` | 7774156 | - | 切片成为全链路绝对瓶颈。 |
| 2026-06-09 | `parallel_ordered` 初版 | `7eb31499-c306-4899-836d-db9b63d59f48` | 982313 | 981948 | 有序并发可行，但 enhanced 外呼仍接近 16 分钟。 |
| 2026-06-10 | 动态借用 hard cap `18` | `5bc9f5f4-c66c-4f37-a4f6-b225a4bad022` | 249148 | 248692 | 动态借用能显著压缩空闲池浪费。 |
| 2026-06-10 | `16/3/4 + 22` | `508c6fe3-f5d6-4a0c-8192-98dfbabbaaec` | 205305 | 204951 | 单 key 下继续提速，失败为 0。 |
| 2026-06-11 | `22` + 5 key 池 | `2da514c3-8e70-403c-b0d9-786505e56c0d` | 217279 | 216913 | key 池生效，但 cap 不变时不保证提速。 |
| 2026-06-11 | `26` + 5 key 池 | `f6a4348f-4a12-4000-9d4f-5397138d9917` | 175656 | 175282 | 无 throttle/retry/failure，可继续阶梯验证。 |
| 2026-06-11 | `34` + 5 key 池 | `bc79f5ef-4673-4bc5-943a-225b316bc463` | 144412 | 143985 | 继续稳定下降。 |
| 2026-06-11 | `42` + 5 key 池 | `617c207a-0d9c-4e46-8736-a5f0a7050aad` | 120517 | 120164 | 继续稳定下降。 |
| 2026-06-11 | `50` + 5 key 池 | `0317ee49-b378-47f9-b1e5-e733a6661388` | 96101 | 95695 | 继续稳定下降。 |
| 2026-06-11 | `60` + 5 key 池 | `64cc88d6-c222-4793-bb86-bd41af8ffc24` | 80165 | 79579 | 从 `50` 到 `60` 后仍无 throttle/retry/failure。 |
| 2026-06-11 | `70` + 5 key 池 | `7571b640-6d0b-4bdb-8f83-f90b6902fb9f` | 70329 | 69365 | 当前最佳已验证档；peak 达到 70，失败/限流/重试均为 0。 |

## 关键技术演进

1. 入库前有序并发增强：将 enhanced 外呼从逐条等待改成并发执行，但按原 slot 顺序合并。
2. 动态借用调度：text/table/image 有各自软上限，空闲容量可被仍有积压的类型借用。
3. worker 内 client 复用：减少几百到上千次 OpenAI 兼容 client / 连接池重复创建成本。
4. 多 API key 池：`LLM_API_KEY_POOL` 分摊 text/table/fragment；`VL_API_KEY_POOL` 分摊 image/VL。仅明确限流错误触发冷却和换 key 重试。
5. 匿名 per-key 指标：记录 `llm-key-N.calls/successes/failures/throttles/totalMs`，不落真实 key。
6. 运行时热更新：通过控制台 settings DB override 调整 `HIERARCHICAL_ENHANCE_MAX_CONCURRENCY`，无需每次重建镜像。
7. 三线拆账：解析、切片、向量化分别记录指标，避免阶段归因混淆。

## 当前结论

`70` 是当前最佳已验证切片档：

```text
enhanceMaxConcurrency=70
enhancePeakConcurrency=70
enhanceWallMs=69365
enhanceTasks=957
enhanceFailures=0
enhanceKeyThrottleCount=0
enhanceKeyRetryCount=0
enhanceKeyCooldownCount=0
perKeyCalls=187/188/176/186/195
```

不建议继续直接把切片并发推到 `80/90`。最新任务里切片已降到 `70329ms`，而解析为 `152181ms`，embedding stage 为 `104651ms`。其中 embedding API 墙钟只有 `15285ms`，更需要先拆解和优化 `link_semantic / procedure / causal` 等后处理。

## 回退规则

出现以下任一情况，优先回退到 `60`：

- `enhanceFailures` 不再为 0
- `enhanceKeyThrottleCount` 或 `enhanceKeyRetryCount` 持续上升
- `enhanceWallMs` 无收益且 provider 长尾明显加重
- 单个匿名 key 出现明显错误集中或耗时异常

若 `60` 仍不稳，再按 `50 -> 42 -> 34 -> 26 -> 22 -> 18` 回退。

## 关联文档

- [三层切片最终技术方案](../../pipeline/three-layer-chunking-final-solution.md)
- [三层切片增强优化追踪](./chunk-enhancement-optimization-tracker.md)
- [切片性能跟踪](./chunk-performance.md)
- [入库性能三线跟踪](./ingestion-performance-tracks.md)
- [三层切片规则](../../rule/hierarchical-chunking.md)
