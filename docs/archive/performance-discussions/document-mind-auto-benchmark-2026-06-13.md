# Document Mind Auto Benchmark 2026-06-13

This note records the parse-only auto benchmark for Alibaba Document Mind on the common textbook PDF.

## Method

- Hot update runtime settings through `PUT /api/console/settings`.
- Run the same `ali_document_mind` parser inside `wisewe-rag-backend`.
- Record parse-only metrics to `data/results/document_mind_parse_benchmark.jsonl`.
- Keep cleaning, chunking, embedding, database writes, and retrieval out of the comparison.

Input PDF:

```text
/app/data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf
```

Benchmark script:

```text
scripts/benchmark_document_mind_parse.ps1
```

## Results

| Candidate | pages_per_shard | max_concurrency | inflight/key | shard / worker | parseWallMs | shardWallMsMax | outputBlocks | throttle/retry/cooldown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `p33-c4` | 33 | 4 | 1 | 12 / 4 | 166860 | 150936 | 8371 | 0 / 0 / 0 |
| `p30-c4` | 30 | 4 | 1 | 14 / 4 | 184736 | 69221 | 8369 | 0 / 0 / 0 |
| `p36-c4` | 36 | 4 | 1 | 11 / 4 | 359430 | 300713 | 8372 | 0 / 0 / 0 |
| `p33-c5` | 33 | 5 | 1 | 12 / 5 | 437000 | 346358 | 8378 | 0 / 0 / 0 |

## Conclusion

`p33-c4` remains the best current parse setting:

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

`p30-c4` lowers the slowest shard but adds shards and scheduling waves, so total wall time is worse. `p36-c4` and `p33-c5` both show 300s-level provider-side long tails without explicit throttle, retry, or cooldown. The live server was hot-updated back to the stable `p33-c4` setting after the benchmark.
