# Document Mind Slow Key Scoring 2026-06-14

This note records the follow-up optimization after adding more AK/SK pairs made Alibaba Document Mind parsing slower.

## Scope

- Parser only: Alibaba Document Mind credential scheduling.
- Same common textbook PDF:

```text
/app/data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf
```

- No changes to cleaning, chunking, embedding, database writes, or retrieval.
- Metrics expose only anonymous `dm-key-N` aliases.

## Implementation

The credential pool now tracks successful parse latency per anonymous key:

- `parseKey.dm-key-N.lastMs`
- `parseKey.dm-key-N.avgMs`
- `parseKeyUnknownProbeConcurrency`

Scheduling chooses by:

```text
inflight -> latency score -> rotation offset
```

It also keeps process-local latency history keyed by an internal hash of AK/SK, so a backend process can remember slow keys across parse tasks without exposing the secret. Cold-start probing is capped by `ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY`; the current stable value is `1`.

## Results

Output file:

```text
data/results/document_mind_slow_key_scoring_benchmark.jsonl
```

Invalid records in that JSONL:

- one sandbox/Docker config permission failure before escalation
- one PowerShell candidate list binding failure

Valid parse-only records:

| Candidate | pages | workers | inflight/key | probe | parseWallMs | shardWallMsMax | outputBlocks | throttle/retry/cooldown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `p33-c4` before probe cap | 33 | 4 | 1 | n/a | 318819 | 280879 | 8350 | 0 / 0 / 0 |
| `p33-c4` conservative cold start | 33 | 4 | 1 | 2 | 286658 | 277921 | 8372 | 0 / 0 / 0 |
| `p33-c4-probe1` | 33 | 4 | 1 | 1 | 192911 | 184406 | 8371 | 0 / 0 / 0 |
| `p33-c4-probe2` | 33 | 4 | 1 | 2 | 362534 | 353833 | 8372 | 0 / 0 / 0 |
| `p33-c4-probe3` | 33 | 4 | 1 | 3 | 346032 | 279693 | 8382 | 0 / 0 / 0 |
| `p33-c4` final baseline | 33 | 4 | 1 | 1 | 208043 | 157302 | 8372 | 0 / 0 / 0 |

## Conclusion

`probe=1` is the best verified setting in this run. Increasing probe concurrency to `2` or `3` reintroduced provider-side long tails even though there was no explicit throttle, retry, cooldown, or failure.

Current stable parser setting:

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY=1
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

## Follow-up: Hedging

Hedged shard parsing was implemented behind an off-by-default switch and benchmarked on the same textbook PDF:

```text
data/results/document_mind_hedged_shards_benchmark.jsonl
```

Results:

| Candidate | parseWallMs | Hedge attempts | Extra submissions | Hedge wins |
|---|---:|---:|---:|---:|
| `p33-c4-probe1` | 188911 | 0 | 0 | 0 |
| `p33-c4-hedge90` | 224780 | 1 | 1 | 1 |
| `p33-c4-hedge75` | 228932 | 2 | 1 | 1 |

Conclusion: hedging made the parse slower in this run and remains disabled by default.

The next effective lever was turning off Document Mind managed LLM/VLM enhancement while keeping `markdown,visualLayoutInfo`; see [Document Mind Managed LLM Off 2026-06-14](./document-mind-managed-llm-off-2026-06-14.md).
