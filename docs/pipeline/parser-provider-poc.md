# Parser Provider POC 对比记录

本文用于记录 302AI MinerU 与阿里 Document Mind 的同源 PDF 解析对比。

## 配置切换

MinerU：

```env
PDF_PARSER_PROVIDER=mineru
```

阿里 Document Mind：

```env
PDF_PARSER_PROVIDER=ali_document_mind
ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID=...
ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET=...
ALIYUN_DOCUMENT_MIND_ENDPOINT=docmind-api.cn-hangzhou.aliyuncs.com
ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT=markdown,visualLayoutInfo
ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT=false
ALIYUN_DOCUMENT_MIND_ENHANCEMENT_MODE=VLM
ALIYUN_DOCUMENT_MIND_LAYOUT_STEP_SIZE=3000
```

## Provider selection boundary

Document Mind is introduced as a document parsing / RAG pre-processing provider only.
Do not use Alibaba hosted RAG knowledge base or model training in this POC.
MinerU remains the default provider, and Document Mind is enabled only through
explicit configuration for A/B comparison.

## Document Mind sharded large-PDF parsing

When `PDF_PARSER_PROVIDER=ali_document_mind`, large PDFs now use the same execution shape as
the MinerU large-file path:

```text
PDF inspection
  -> if file > 150 MB or pages > 50
  -> split local PDF by page shard
  -> submit shard Document Mind jobs concurrently
  -> add shard.start_page to each local ContentBlock.page_idx
  -> force source_file back to the original upload filename
  -> merge ContentBlock[] and continue clean/chunk/quality/embedding/export
```

Default Document Mind sharding config:

```env
ALIYUN_DOCUMENT_MIND_SHARDING_ENABLED=true
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_FILE_MB=150
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES=50
ALIYUN_DOCUMENT_MIND_SHARDING_PAGES_PER_SHARD=33
ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY=4
ALIYUN_DOCUMENT_MIND_MAX_INFLIGHT_PER_KEY=1
ALIYUN_DOCUMENT_MIND_SHARDING_MIN_PAGES_PER_SHARD=20
ALIYUN_DOCUMENT_MIND_SHARDING_TARGET_WAVES=2
ALIYUN_DOCUMENT_MIND_SHARDING_TEXT_SAMPLE_PAGES=5
```

For files that exceed the size threshold but have relatively few pages, the parser reduces the
effective `pages_per_shard` from the configured value according to average MB/page. This prevents
a single shard from remaining above the Document Mind size limit. Concurrency is capped by the
actual shard count, the independent shard concurrency setting, and parse key capacity. Extra credentials should not implicitly raise `ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY`.

Important merge rules:

- `ContentBlock.page_idx` remains the original PDF global 0-based page index after merge.
- User-visible page numbers are still `page_idx + 1`.
- `ContentBlock.source_file` remains the original uploaded filename, not the shard filename.
- A failed shard fails the whole parse task, and the error includes the shard number and original
  page range such as `shard #2 P41-80`.

## Document Mind OpenAPI boundary

Document Mind provider uses the official Alibaba Cloud OpenAPI SDK:

1. `SubmitDocParserJobAdvance` uploads and submits the local PDF.
2. `QueryDocParserStatus` polls the parser job until success or failure.
3. `GetDocParserResult` reads the parser result and converts it to `ContentBlock`.

This project does not use local mock parser results in the ingestion runtime. Removed legacy
`ALIYUN_DOCUMENT_MIND_MOCK_RESULT_PATH`, `ALIYUN_DOCUMENT_MIND_SUBMIT_URL`,
`ALIYUN_DOCUMENT_MIND_QUERY_URL`, and `ALIYUN_DOCUMENT_MIND_RESULT_URL` config keys; they were
temporary POC shims and are not production configuration.

## 测试样本

| 样本 | 文件 | 类型 | 页数 | 备注 |
| --- | --- | --- | --- | --- |
| S1 | 待填 | 小 PDF | 待填 | 快速回归 |
| S2 | 待填 | 含表格教材 | 待填 | 表格与标题层级 |
| S3 | 待填 | 扫描版大 PDF | 待填 | 稳定性与耗时 |

## 对比指标

| 指标 | MinerU | Document Mind | 备注 |
| --- | --- | --- | --- |
| 是否成功解析 | 待测 | 待测 | 记录失败原因 |
| 总耗时 | 待测 | 待测 | 从 parse stage 开始到结束 |
| 总 block 数 | 待测 | 待测 | `ContentBlock` 数量 |
| 页码准确性 | 待测 | 待测 | 抽样检查 |
| 标题层级 | 待测 | 待测 | 章节结构是否保留 |
| 表格保真 | 待测 | 待测 | Markdown / HTML / 文本化质量 |
| 图片 / 图表可追溯 | 待测 | 待测 | 是否有可展示图片路径 |
| 清洗后保留率 | 待测 | 待测 | clean 后 block 保留比例 |
| 切片数量 | 待测 | 待测 | chunk 数量 |
| RAG 证据可读性 | 待测 | 待测 | 引用内容是否可解释 |

## 结论模板

```text
样本：
推荐 provider：
主要原因：
不可接受问题：
是否可作为默认 provider：
后续动作：
```
