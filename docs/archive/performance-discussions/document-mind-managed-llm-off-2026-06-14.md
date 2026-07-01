# Document Mind Managed LLM Off 2026-06-14

This note records the parse-only benchmark after hedged shard parsing did not improve Alibaba Document Mind wall time.

## Scope

- Parser-only benchmark.
- Same common textbook PDF:

```text
/app/data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf
```

- Stable scheduling profile retained:

```text
pages_per_shard=33
max_concurrency=4
max_inflight_per_key=1
key_probe_concurrency=1
target_waves=2
hedged_shard=false
```

## Tested Lever

Instead of adding more duplicate work, this run tested whether Document Mind managed LLM/VLM enhancement was the dominant provider-side tail:

```env
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
```

`visualLayoutInfo` remains enabled so image/layout evidence is still available.

## Results

Output file:

```text
data/results/document_mind_content_modes_benchmark.jsonl
```

The first three records in the file are invalid sandbox/Docker config permission failures and are excluded.

| Candidate | Managed LLM/VLM | Output | parseWallMs | shardWallMsMax | outputBlocks | imageBlocks | tableBlocks | textChars |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| `p33-c4-no-llm-layout` | off | `markdown,visualLayoutInfo` | 102131 | 74809 | 18500 | 475 | 81 | 477811 |
| `p33-c4-no-llm-markdown` | off | `markdown` | 119973 | 81621 | 18104 | 0 | 81 | 477811 |
| `p33-c4-llm-markdown` | on | `markdown` | 202658 | 156706 | 7975 | 0 | 79 | 541789 |
| `p33-c4-probe1` repeat | on | `markdown,visualLayoutInfo` | 381948 | 278378 | 8371 | 492 | 79 | 541724 |
| `p33-c4-no-llm-layout` repeat | off | `markdown,visualLayoutInfo` | 97082 | 53065 | 18500 | 475 | 81 | 477811 |

Earlier same-day baseline:

```text
p33-c4-probe1 parseWallMs=188911
```

## Conclusion

The strongest improvement is turning off Document Mind managed LLM/VLM enhancement while keeping `markdown,visualLayoutInfo`.

Current fast parse profile:

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY=1
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED=false
```

## Risk

The fast profile produces about 18.5k parse blocks instead of about 8.3k. This may shift work into local cleaning and chunking, so the next full-ingestion validation should watch:

- clean/chunk wall time
- final chunk count
- image/table evidence preservation
- representative RAG answer quality

Do not switch to `markdown` only as the default; it drops image evidence in this benchmark.

## Full-Ingestion Follow-Up

The 2026-06-15 full-ingestion benchmark validated this profile beyond parse-only runs. The best warm full run finished in `212885ms`; the COPY-mode run was API-rechecked as `success` with `parse=124088ms`, `chunk=71933ms`, `embedding=16527ms`, and `export=16578ms`.

See [Document Mind Full Ingestion 2026-06-15](./document-mind-full-ingestion-2026-06-15.md).
