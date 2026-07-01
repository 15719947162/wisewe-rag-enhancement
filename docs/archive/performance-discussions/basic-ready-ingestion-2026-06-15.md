# Basic-Ready Ingestion 2026-06-15

## 背景

2026-06-15 的完整入库与 pgvector 写入微基准已经确认：

- 继续增加 Document Mind 同 key in-flight 没有收益，甚至会变慢。
- hedged shard 没有收益，默认应保持关闭。
- `PGVECTOR_WRITE_MODE=copy` 后，relations/triples 和 vector cast 不是剩余主因。
- 当前端到端耗时的主要压力仍在 parse、hierarchical enhancement chunk，以及少量 indexed chunks 写入。

因此本轮不再继续把重点放在关系/triple 入库微调，而是转向降低用户等待时间：先让知识库进入基础可检索状态，再为后续 enhanced / relation / heavy write 异步补强留出路径。

## 本轮实现

新增运行时开关：

```env
INGESTION_READY_MODE=full
```

取值：

| Value | 行为 |
|---|---|
| `full` | 默认值，保持现有完整 hierarchical enhanced 入库链路。 |
| `basic` | 仅对 `hierarchical` 策略生效，切片阶段传入 `enable_enhanced=False`，跳过 enhanced chunks。 |
| `basic_ready` / `ready_basic` | `basic` 的兼容别名，metrics 中统一记为 `basic`。 |

开启 basic-ready 后，入库仍会继续执行：

- parse
- clean
- basic parent / child / table / image chunk
- `link_related_chunks()`
- quality gate
- embedding
- semantic / procedure / causal linking
- entity materialization
- pgvector write

跳过的是 hierarchical enhanced layer 的 LLM/VL 增强任务。

## 可观测字段

`chunkTimings` 会新增：

| Field | Meaning |
|---|---|
| `readyMode` | 实际生效模式，`full` 或 `basic`。 |
| `requestedReadyMode` | 用户配置解析后的请求模式。 |
| `readyModeSource` | `env` / `db` / `code` 等来源。 |
| `enhancementSkipped` | `1` 表示 basic-ready 跳过 enhanced，`0` 表示完整模式。 |

任务 payload 也会暴露：

```json
{
  "ingestionReadyMode": "basic"
}
```

## 预期收益

本切片的收益点不是缩短 Document Mind parse 本身，而是压缩“可搜索可问答”的等待时间。

在最近同教材完整入库中，hierarchical enhanced chunk 约 `67-76s`。basic-ready 模式预计能先跳过这部分增强等待，让基础 chunks 更快进入 embedding/export。实际收益需要用同一教材跑 A/B：

```text
full:  parse + clean + chunk(full) + embedding + export
basic: parse + clean + chunk(basic) + embedding + export
```

对比指标：

- `taskWallMs`
- `parse.latencyMs`
- `chunk.latencyMs`
- `chunkTimings.readyMode`
- `chunkTimings.enhancementSkipped`
- `chunkTimings.enhanceTasks`
- `embedding.latencyMs`
- `export.latencyMs`
- final chunks count
- RAG smoke query hit quality

## 风险与边界

- `basic` 会减少 enhanced chunks，可能影响复杂概念解释、图表摘要和长文片段召回质量。
- child/table/image chunks 仍在，基础检索和引用链路应可用。
- 本轮没有实现后台 enhanced 队列，因此 basic-ready 不是“最终完整质量模式”。
- 默认仍是 `full`，不会改变现有正式入库结果。

## 下一步

1. 用同一常用教材跑 `full` 与 `basic` A/B，记录基础可检索完成时间。
2. 若 basic-ready 可接受，再实现后台增强补强队列：
   - enhanced chunks 生成；
   - enhanced embedding；
   - relation / index 增量写入；
   - 前端展示“基础可用 / 增强中 / 完整可用”。
3. 若 basic-ready 质量不可接受，再评估按章节或按热点页优先增强，而不是一次性全量增强。
