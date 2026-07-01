# MinerU Official Parser

本文记录 `PDF_PARSER_PROVIDER=mineru_official` 的官方 MinerU 精准解析接入方式。该渠道是独立 provider，不改变现有 `mineru`（302AI MinerU）和 `ali_document_mind`（Document Mind）的解析逻辑、分片参数和调度策略。

## 启用方式

```env
PDF_PARSER_PROVIDER=mineru_official
MINERU_OFFICIAL_API_BASE=https://mineru.net
MINERU_OFFICIAL_API_TOKEN=your-token
```

官方 MinerU 使用 Bearer Token 鉴权。`MINERU_OFFICIAL_API_TOKEN` 必须单独配置，不复用 `302AI_API_KEY`、`ALIYUN_DOCUMENT_MIND_*` 或 OSS 凭证。

当前官方 API 仍需要提交一个可访问的文件 URL，因此本项目会继续复用现有 OSS 上传能力：

```env
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_ENDPOINT=...
OSS_BUCKET=...
```

## 官方 API 形态

官方精准解析 API 的调用链路：

```text
POST /api/v4/extract/task
  Authorization: Bearer <MINERU_OFFICIAL_API_TOKEN>
  body.url = OSS signed URL
  body.model_version = pipeline | vlm | MinerU-HTML
  body.is_ocr / enable_formula / enable_table / language / extra_formats / no_cache / cache_tolerance

GET /api/v4/extract/task/{task_id}
  state = pending | running | converting | done | failed
  done -> data.full_zip_url
```

项目拿到 `full_zip_url` 后，沿用现有 MinerU ZIP 下载与 `content_list.json` 映射逻辑，转换成 `ContentBlock[]` 后进入清洗、切片、质量门控、向量化和入库。

## 已验证快档

2026-06-17 使用真实教材 `36.中医学 第9版.pdf` 验证官方 MinerU 快档，PDF 为 396 页、44.9 MB。当前推荐在线解析配置为：

```env
PDF_PARSER_PROVIDER=mineru_official
MINERU_OFFICIAL_MODEL_VERSION=pipeline
MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD=20
MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY=6
MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD=30
```

实测对比：

| 配置 | shard | 输出块 | parse 耗时 | 说明 |
| --- | ---: | ---: | ---: | --- |
| `vlm + 180页/片 + 并发2` | 3 | 7781 | 497166ms | 大 shard 长尾明显，最慢 shard 约 6 分钟 |
| `pipeline + 20页/片 + 并发6` | 20 | 7284 | 121773ms | 当前推荐快档，约 4.08 倍提速 |

本次快档日志：`data/logs/d30e57bd-a9ce-4724-819d-92429962ac78.log`。关键记录为 `20 shards, pages_per_shard=20, workers=6`、`model_version=pipeline`、`[parse] Done: 7284 blocks in 121773ms`。后续切片也正常完成，输出 2551 个切片，增强失败为 0。

注意：官方接口 `GET https://mineru.net/api/v4/tasks?page_no=1&page_size=20` 中的 `page_size=20` 是任务列表分页大小，不是 PDF 页切割参数。本项目的 20 页切割是本地 shard 策略，用来降低官方云端任务长尾。

代码内置默认值仍保持保守档，线上推荐通过控制台设置或 `.env` 显式配置上述快档。运行时设置优先级为 DB 控制台设置 > `.env` > 代码默认值；如果只改 `.env` 但控制台 DB 已保存旧值，实际运行会继续使用 DB 值。

## 官方渠道专用切割

官方精准解析限制单文件不超过 200 MB、200 页。为了给文件膨胀、页码统计和云端限制留余量，`mineru_official` 代码默认按下面保守策略拆分；生产推荐优先使用上面的已验证快档：

```env
MINERU_OFFICIAL_SHARDING_ENABLED=true
MINERU_OFFICIAL_SHARDING_MIN_FILE_MB=180
MINERU_OFFICIAL_SHARDING_MIN_PAGES=201
MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD=180
MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY=2
MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD=180
MINERU_OFFICIAL_SHARDING_TEXT_SAMPLE_PAGES=5
```

执行流程：

```text
用户上传一本 PDF
  -> inspect_pdf 读取页数和文件大小
  -> 仅当 provider=mineru_official 且达到阈值时本地拆分 PDF
  -> 每个 shard 单独上传 OSS
  -> 每个 shard 单独提交官方 MinerU task
  -> 下载每个 shard 的 full_zip_url
  -> 恢复全局 page_idx，source_file 固定为用户原始文件名
  -> 合并 ContentBlock[] 后继续后续入库链路
```

边界规则：

- `mineru_official` 的切割阈值只读取 `MINERU_OFFICIAL_SHARDING_*`。
- `mineru` 的 302AI MinerU 配置仍读取 `parser.cloud.sharding.*`，不受官方变量影响。
- `ali_document_mind` 仍读取 `ALIYUN_DOCUMENT_MIND_SHARDING_*`，不受官方变量影响。
- shard 失败会让整次解析失败，错误信息包含 shard 编号和原始页码范围，方便定位重试。

## 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINERU_OFFICIAL_API_BASE` | `https://mineru.net` | 官方 API base URL |
| `MINERU_OFFICIAL_API_TOKEN` | 空 | 官方 MinerU API token，敏感字段 |
| `MINERU_OFFICIAL_MODEL_VERSION` | `vlm` | 官方模型版本，可改为 `pipeline` 或 `MinerU-HTML` |
| `MINERU_OFFICIAL_TIMEOUT` | `1800` | 单 task 轮询超时秒数 |
| `MINERU_OFFICIAL_POLL_INTERVAL` | `3` | 初始轮询间隔秒数 |
| `MINERU_OFFICIAL_ENABLE_FORMULA` | `true` | 是否解析公式 |
| `MINERU_OFFICIAL_ENABLE_TABLE` | `true` | 是否解析表格 |
| `MINERU_OFFICIAL_LANGUAGE` | `ch` | 文档语言 |
| `MINERU_OFFICIAL_IS_OCR` | `false` | 是否强制 OCR |
| `MINERU_OFFICIAL_EXTRA_FORMATS` | 空 | 逗号分隔的额外格式 |
| `MINERU_OFFICIAL_NO_CACHE` | `false` | 是否禁用官方缓存 |
| `MINERU_OFFICIAL_CACHE_TOLERANCE` | `900` | 官方缓存容忍秒数 |
| `MINERU_OFFICIAL_SUBMIT_RETRY_ATTEMPTS` | `3` | 提交网络异常重试次数 |
| `MINERU_OFFICIAL_POLL_RETRY_ATTEMPTS` | `5` | 轮询网络异常重试次数 |
| `MINERU_OFFICIAL_SHARDING_ENABLED` | `true` | 是否启用官方渠道专用拆分 |
| `MINERU_OFFICIAL_SHARDING_MIN_FILE_MB` | `180` | 文件大小达到该值触发拆分 |
| `MINERU_OFFICIAL_SHARDING_MIN_PAGES` | `201` | 页数达到该值触发拆分 |
| `MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD` | `180` | 每个 shard 页数上限 |
| `MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY` | `2` | 官方 shard 解析最大并发 |
| `MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD` | `180` | 按平均 MB/页收敛页数上限 |
| `MINERU_OFFICIAL_SHARDING_TEXT_SAMPLE_PAGES` | `5` | PDF 体检时抽样文本页数 |

推荐运行值：

| 变量 | 推荐值 | 说明 |
| --- | --- | --- |
| `MINERU_OFFICIAL_MODEL_VERSION` | `pipeline` | 当前真实教材验证最快的官方模型档 |
| `MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD` | `20` | 降低单个官方 task 长尾 |
| `MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY` | `6` | 20 页 shard 下的已验证并发 |
| `MINERU_OFFICIAL_SHARDING_MAX_FILE_MB_PER_SHARD` | `30` | 防止高密度 PDF 单 shard 文件过大 |

## 记录与排查

解析日志会输出 PDF 体检结果、单 task / shard task 选择、每个 shard 的编号和原始页码范围、task_id、轮询状态、输出块数量，以及合并后的总 `ContentBlock` 数。

这些日志会被入库任务的 parse stage tracker 消费，用于控制台展示解析渠道、shard 总数、已完成 shard、轮询次数和输出块数。
