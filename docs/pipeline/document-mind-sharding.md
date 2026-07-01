# Document Mind large-PDF sharding

This note records the 10-06b behavior for `PDF_PARSER_PROVIDER=ali_document_mind`.

The common PDF sharding infrastructure lives in `core/parser/pdf_sharding.py`. Both MinerU and
Document Mind use the same `PdfInspection`, `PdfShard`, `inspect_pdf()`, `split_pdf_to_shards()`,
`offset_shard_blocks()`, and `merge_shard_records()` helpers, so page ranges and global page
offset behavior stay consistent across providers.

## Trigger

Document Mind sharding is enabled by default. A PDF is parsed through shard tasks when either
condition is true:

- `file_size > 150 MB`
- `page_count > 50`

Default config:

```env
ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED=true
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB=150
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES=50
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES=5
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1
ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE=true
```

`ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD` is the upper bound. When there are many pages and enough parse key capacity, the parser estimates target shards with `parseSchedulingCapacity * ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES` and lowers `effectivePagesPerShard` no lower than `ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD`. This keeps the same page offset and merge semantics while reducing long-tail 40-page shards.

Current validation profile keeps shard worker concurrency independent from the credential pool size. Extra Document Mind credentials remain available for rotation and fallback, but effective parse concurrency is still capped by `shardCount`, `ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY`, and `parseKeyPoolSize * ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY`.

Document Mind shards use lightweight local PDF save cleanup by default:
`ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=1` with deflate enabled. This reduces
the pre-provider split cost while preserving page ranges and merge semantics.
Set `ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE=4` when investigating PDF cleanup
compatibility.

## Flow

```text
PDF
  -> inspect pages, size, and sampled text
  -> split local PDF into page shards when threshold is hit
  -> submit shard Document Mind OpenAPI tasks concurrently
  -> reuse successful status payload when it already contains primary parse content
  -> parse each shard into ContentBlock[]
  -> page_idx = shard.start_page + local_page_idx
  -> source_file = original uploaded filename
  -> merge all shards
  -> continue clean/chunk/quality/embedding/export/database
```

When `QueryDocParserStatus` returns `success` with markdown or structured primary
content already present, the parser converts that status payload directly and skips
the extra `GetDocParserResult` call for that job. Pure layout/image evidence does
not trigger the shortcut; the parser still fetches the result payload and merges
status images afterward. This is an in-flight response shortcut, not cross-task
parse-result caching.

If a PDF exceeds the size threshold but has relatively few pages, the parser reduces the effective
`pages_per_shard` from the configured value based on average MB/page. This avoids creating a shard
that is still above the Document Mind size limit whenever the document has more than one page.

## Merge invariants

- `ContentBlock.page_idx` is the original PDF global 0-based page index.
- User-facing page display remains `page_idx + 1`.
- `ContentBlock.source_file` is the original upload filename, never a shard filename.
- Shard output directories are isolated under `document_mind_shards/shard_NNN/`.
- One failed shard fails the whole parse task with the shard number and original page range.
