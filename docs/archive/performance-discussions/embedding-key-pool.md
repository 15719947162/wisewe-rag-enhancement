# Embedding Key Pool

本文件记录 2026-06-11 的向量化 key 池方案。

## 决策

- `LLM_EMBEDDING_API_KEY_POOL` 有值时，embedding 优先使用该专用 key 池。
- `LLM_EMBEDDING_API_KEY_POOL` 为空时，embedding 复用 `LLM_API_KEY_POOL`。
- embedding 与三层切片增强可以共享同一批 key 值，但运行时状态隔离：
  - inflight 独立
  - throttle 独立
  - cooldown 独立
  - retry 独立
- 本轮不改变 embedding model、维度、chunk 顺序、schema 或召回策略。

## 配置

```env
LLM_EMBEDDING_API_KEY_POOL=
LLM_EMBEDDING_KEY_RETRIES=1
LLM_EMBEDDING_KEY_COOLDOWN_SECONDS=30
```

`LLM_EMBEDDING_API_KEY_POOL` 支持逗号、分号、空白分隔，也支持 JSON 数组。真实 key 不会写入 metrics。

## 指标

`embedding` stage metrics 新增：

| 字段 | 含义 |
|---|---|
| `embeddingKeyPoolSize` | embedding key 池大小 |
| `embeddingKeyThrottleCount` | embedding key 池观察到的限流次数 |
| `embeddingKeyRetryCount` | 限流后换 key 重试次数 |
| `embeddingKeyCooldownCount` | key 进入冷却的次数 |
| `embeddingKey.embedding-key-N.calls` | 匿名 key 调用次数 |
| `embeddingKey.embedding-key-N.successes` | 匿名 key 成功次数 |
| `embeddingKey.embedding-key-N.failures` | 匿名 key 失败次数 |
| `embeddingKey.embedding-key-N.throttles` | 匿名 key 限流次数 |
| `embeddingKey.embedding-key-N.totalMs` | 匿名 key 累计调用耗时 |

同一轮还补充向量化后处理拆账：

| 字段 | 含义 |
|---|---|
| `linkSemanticMs` | embedding 后语义相似关系构建耗时 |
| `linkProcedureMs` | 流程 / 步骤关系检测与链接耗时 |
| `linkCausalMs` | 因果关系链接耗时 |

`export` stage metrics 新增：

| 字段 | 含义 |
|---|---|
| `entityMaterializeMs` | 实体物化耗时 |
| `pgvectorWriteMs` | chunks / relations / triples 写库耗时 |

## 验收口径

- 若 `embeddingWallMs` 高、`retryCount=0` 且 `embeddingKeyThrottleCount=0`，可再考虑提高 `LLM_EMBEDDING_MAX_CONCURRENCY`。
- 若 `embeddingKeyThrottleCount` 或 `embeddingKeyRetryCount` 上升，优先降低并发或缩小 key 池中的异常 key。
- 若 `embedding.latencyMs` 远高于 `embeddingWallMs`，优先优化 `linkSemanticMs`、`linkProcedureMs`、`linkCausalMs`，不要误判为 embedding API 慢。
