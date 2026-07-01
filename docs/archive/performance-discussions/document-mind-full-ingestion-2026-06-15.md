# Document Mind Full Ingestion 2026-06-15

This note records the full-ingestion validation after turning off Document Mind managed LLM/VLM enhancement.

## Scope

- Live backend ingestion API.
- Same common textbook PDF:

```text
data/uploads/080ed361-f4f5-4be6-b134-99d53ecbd19a.pdf
```

- Isolated benchmark knowledge bases.
- No real AK/SK or model keys are recorded.

Fast parse profile:

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED=false
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY=1
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
```

## Results

Output file:

```text
data/results/document_mind_ingestion_benchmark.jsonl
```

| Run | Task | Result | Wall | Parse | Clean | Chunk | Quality | Embedding | Export | Chunks |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Managed-enhancement reference | `749ba15a-9f43-4adc-9a49-31059a482ebd` | success | n/a | `322214ms` | n/a | `75247ms` | n/a | `18709ms` | `290ms` | `3070` |
| No-LLM full baseline | `73343dfd-9534-4b1c-9a96-e2509b437dc6` | success | `262834ms` | `123655ms` | `95ms` | `67115ms` | `32ms` | `16373ms` | `35509ms` | `2989` |
| Relations/triples batched | `3ef92565-37b3-469d-8afa-5fec2985501b` | success | `232610ms` | `102300ms` | `206ms` | `76277ms` | `57ms` | `16471ms` | `22516ms` | `2989` |
| Warm values run | `d7d8f8a3-4557-4046-8bd6-e21587c79cf6` | success | `212885ms` | `84375ms` | `109ms` | `74471ms` | `26ms` | `16336ms` | `22710ms` | `2989` |
| COPY run, API rechecked | `5bfebd2d-7a8d-49b9-8de0-5765cde90b82` | success | about `236s` | `124088ms` | `113ms` | `71933ms` | `27ms` | `16527ms` | `16578ms` | `2985` |

The COPY run initially wrote an `ok=false` JSONL line because one polling request timed out. Backend API recheck confirmed the task was actually `success`.

## COPY Export Breakdown

Task `5bfebd2d-7a8d-49b9-8de0-5765cde90b82`:

| Metric | Value |
|---|---:|
| `pgvectorWriteMode` | `copy` |
| `pgvectorWriteMs` | `16559` |
| `pgvectorChunksWriteMs` | `15904` |
| `pgvectorChunkRows` | `2985` |
| `pgvectorRelationsWriteMs` | `327` |
| `pgvectorRelationRows` | `7037` |
| `pgvectorTriplesWriteMs` | `0` |
| `pgvectorTripleRows` | `0` |
| `pgvectorCommitMs` | `68` |

DB count recheck:

```text
kb=bench-p33-c4-no-llm-layout-full-20260615-082020-da45
chunks=2985
relations=7037
triples=0
```

## pgvector Write Path Microbenchmark

Output file:

```text
data/results/pgvector_write_path_benchmark.jsonl
```

The benchmark reuses the successful KB rows, re-maps UUIDs, inserts inside one transaction, and rolls back. It does not leave benchmark rows in the target database.

Payload:

```text
chunks=2985
relations=7037
chunkCsvBytes=41837219
```

| Scenario | COPY to temp | Typed temp insert | Actual indexed chunks insert | Relations insert | Total |
|---|---:|---:|---:|---:|---:|
| Remote target, host run | `3976-7366ms` | `900-944ms` | `3139-3436ms` | `148-185ms` | `13405-20742ms` |
| Remote target, backend container | `7888-8216ms` | `983-994ms` | `2600-3577ms` | `140-143ms` | `22233-22933ms` |
| Local target, remote source | `2032-2736ms` | `592-757ms` | `5341-6162ms` | `159-452ms` | `14782-18526ms` |

Interpretation:

- Relations/triples are not the remaining export bottleneck.
- DB locality reduces the COPY transfer part, especially for the about `41.8MB` chunk payload.
- Vector / tsvector cast into an unindexed typed temp table is not dominant by itself.
- The remaining cost is the real indexed `chunks` table insert, including WAL, FK checks, GIN/HNSW maintenance, and remote DB variability.

## Conclusion

The no-LLM profile is validated for full ingestion. The larger parse output, about `18.5k` blocks, does not make clean or chunk explode; clean remains negligible and chunk stays in the same minute-level range.

COPY mode reduced export from about `22.7s` to about `16.6s`. Relation/triple write is no longer the main write bottleneck; the main `chunks` table write dominates export because it sends and indexes about 3k 1024-dimensional vectors against pgvector.

Current bottleneck order:

1. Document Mind parse.
2. Three-layer enhanced chunking.
3. `chunks` main table pgvector write.
4. Embedding API wall time.

## Next Options

- Treat backend/pgvector colocation as a partial improvement: it helps COPY transfer, but indexed `chunks` insert remains visible.
- Evaluate index/load strategy for bulk chunk loads only if it can preserve online query correctness and operational simplicity.
- Prefer an async/basic-ingestion mode for large textbooks: make a basic searchable state available first, then defer enhanced chunks, relations, and expensive index-heavy writes.
- Avoid switching to `markdown` only by default; it drops image evidence.
