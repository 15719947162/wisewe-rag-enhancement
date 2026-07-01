# API 参考

更新时间：2026-06-26

本文记录当前 FastAPI 后端实际暴露的 HTTP API。默认开发地址为 `http://localhost:8000`，生产部署时以网关或服务实际域名为准。前端 Next.js 控制台通常通过这些后端接口完成知识库、入库、问答、图谱、日志和配置操作。

## 认证与身份

当前存在两类身份方式：

| 场景 | 方式 | 说明 |
| --- | --- | --- |
| 控制台正式登录 | HttpOnly `kb_session` cookie | RAG 侧通过 `/api/auth/ai-base/launch` 发起 AI 基座 SSO，callback / exchange 成功后建立知识库本地短 session。敏感操作会按 `KB_IDENTITY_SNAPSHOT_MAX_AGE_SECONDS` 校验身份快照新鲜度。 |
| 控制台 / 内部联调兜底 | `X-KB-Tenant-Id`、`X-KB-User-Id` | 本地开发和历史联调用的临时身份上下文，可通过 `KB_LEGACY_HEADER_AUTH_ENABLED=false` 在生产关闭。旧请求头身份不能触发身份 delta 入库。 |
| OpenAPI 外部调用 | `Authorization: Bearer <api_key>` 或 `X-API-Key: <api_key>`，生产 Key 默认叠加 `X-KB-Timestamp`、`X-KB-Nonce`、`X-KB-Body-SHA256`、`X-KB-Signature` | 适用于 `/openapi/v1/*`。API Key 是知识库面向多类外部调用方的通用技术凭证，AI 基座只是可能调用方之一；后续第三方系统也应复用同一调用方 / app / API Key 模型。API Key 明文格式为 `wwkb_{key_id}_{secret}`，只在创建或轮换时返回一次，服务端只保存 SHA-256 hash；新建 Key 默认要求 HMAC 强签名。 |

尚未完整闭合的生产安全能力：并发限制、月度配额、多语言 SDK 包、全量子资源权限收口、配置版本 / 回滚、身份同步失败重试和访问拒绝审计全覆盖。SSO、本地短 session、HMAC 强签名、timestamp、nonce 防重放、body hash、API Key 级 IP 白名单、rpm / daily request limit 和 OpenAPI 鉴权失败审计已完成后端闭环；目标对接契约见 [external-governance-integration-contract.md](../product/external-governance-integration-contract.md)，SSO/JWT 细则见 [ai-base-sso-integration-guide.md](../product/ai-base-sso-integration-guide.md)。

## 通用响应与错误

内部 `/api/*` 接口多数直接返回业务 JSON，错误通常为 FastAPI 标准结构：

```json
{"detail": "error message"}
```

OpenAPI `/openapi/v1/*` 使用稳定包装：

```json
{
  "requestId": "7b2c...",
  "data": {}
}
```

OpenAPI 错误结构：

```json
{
  "requestId": "7b2c...",
  "error": {
    "code": "API_KEY_REQUIRED",
    "message": "OpenAPI authentication is required",
    "details": {}
  }
}
```

常见 OpenAPI 错误码：`KB_ID_REQUIRED`、`API_KEY_REQUIRED`、`INVALID_API_KEY`、`API_KEY_DISABLED`、`API_KEY_EXPIRED`、`KB_BINDING_DENIED`、`CAPABILITY_DENIED`、`SIGNATURE_REQUIRED`、`INVALID_SIGNATURE`、`BODY_HASH_MISMATCH`、`TIMESTAMP_EXPIRED`、`INVALID_TIMESTAMP`、`NONCE_REPLAYED`、`IP_NOT_ALLOWED`、`KB_NOT_FOUND`、`VALIDATION_ERROR`、`OPENAPI_QUERY_FAILED`、`OPENAPI_GRAPH_QUERY_FAILED`。

## 健康检查

| 方法 | 路径 | 用途 | 认证 |
| --- | --- | --- | --- |
| GET | `/api/health` | 返回后端健康状态 | 无 |

## 身份、SSO 与同步

| 方法 | 路径 | 用途 | 认证 |
| --- | --- | --- | --- |
| GET | `/api/auth/ai-base/config` | 返回 AI 基座 SSO 配置摘要、RAG launch URL、legacy header fallback 状态 | 无 |
| GET | `/api/auth/ai-base/launch` | 由 RAG 生成随机 state/cookie 后 302 到 AI 基座 SSO | 浏览器 |
| GET | `/api/auth/ai-base/callback` | 接收 AI 基座 code/state，exchange 身份摘要并写入本地短 session | 浏览器 |
| POST | `/api/auth/ai-base/exchange` | 用一次性 code 或 JWT 换取本地短 session | 后端 / 受控前端 |
| GET | `/api/auth/session` | 返回当前知识库本地 session 身份摘要 | `kb_session` |
| POST | `/api/auth/logout` | 清理知识库本地 session | `kb_session` 可选 |
| POST | `/api/auth/ai-base/refresh-current-user` | 刷新当前登录用户身份快照 | 已登录身份 |
| POST | `/api/identity/sync-delta` | 拉取 AI 基座租户、用户、角色、用户角色和删除事件 delta | 仅 AI 基座 SSO `superManager` |
| DELETE | `/api/identity/snapshot-data` | 移除本地身份快照与同步运行记录，写入审计；前端有二次确认 | superManager |
| GET | `/api/identity/sync-status` | 查看身份同步调度、水位和最近状态 | superManager |
| POST | `/api/auth/ai-base/logout-callback` | AI 基座服务端回调，按租户或用户撤销本地短 session | `X-Client-Id` / `X-Client-Secret` |
| GET | `/api/identity/snapshot-users` | 返回本地 AI 基座身份快照明细，用于身份与权限同步页 | superManager |

`GET /api/identity/snapshot-users` 会按请求 `limit` 返回本地快照中的用户明细，包含租户、用户、原始角色、RAG 角色和同步时间。前端 `/identity-monitor` 默认按最近成功同步的用户数量拉取全量，并在页面内按租户名称、用户名称、原始角色和同步时间筛选。

## 知识库与文档

| 方法 | 路径 | 用途 | 参数 / 请求体 | 认证 |
| --- | --- | --- | --- | --- |
| GET | `/api/knowledge-bases` | 列出知识库 | 无 | 可选临时身份头 |
| POST | `/api/knowledge-bases` | 创建知识库 | JSON：`name`、`description`、`strategy` | 可选临时身份头 |
| PUT | `/api/knowledge-bases/{kb_id}` | 更新知识库名称、描述和默认切片策略 | JSON：`name`、`description`、`strategy` | 可选临时身份头 |
| DELETE | `/api/knowledge-bases/{kb_id}` | 软删除知识库 | 路径参数 `kb_id` | 可选临时身份头 |
| POST | `/api/knowledge-bases/{kb_id}/transfer-owner` | 将失效归属人的知识库转交给同租户 active 用户 | JSON：`newOwnerUserId` | 管理员 |
| GET | `/api/knowledge-bases/{kb_id}/graph` | 获取知识库级图谱预览 | 路径参数 `kb_id` | 当前未完整接入临时身份裁剪 |
| GET | `/api/documents` | 列出文档 | Query：`kb_id` 可选 | 当前未完整接入临时身份裁剪 |
| GET | `/api/documents/{document_id}` | 文档详情与切片 | 路径参数 `document_id` | 当前未完整接入临时身份裁剪 |
| GET | `/api/documents/{document_id}/graph` | 文档级图谱预览 | 路径参数 `document_id` | 当前未完整接入临时身份裁剪 |
| DELETE | `/api/documents/{document_id}` | 删除文档 | 路径参数 `document_id` | 当前未完整接入临时身份裁剪 |
| GET | `/api/documents/{document_id}/export.csv` | 导出文档切片 / 关系 CSV | 路径参数 `document_id` | 当前未完整接入临时身份裁剪 |
| GET | `/api/documents/{document_id}/source` | 下载源 PDF；OSS 文档返回 302 跳转 | 路径参数 `document_id` | 当前未完整接入临时身份裁剪 |

创建知识库示例：

```bash
curl -X POST http://localhost:8000/api/knowledge-bases \
  -H "Content-Type: application/json" \
  -H "X-KB-Tenant-Id: 1" \
  -H "X-KB-User-Id: 1" \
  -d "{\"name\":\"中医教材知识库\",\"description\":\"教材与讲义\",\"strategy\":\"hierarchical\"}"
```

新建知识库 ID 为不暴露业务语义的 24 位小写 hex；历史 ID 和 `default` 保留兼容。

## 入库与解析

| 方法 | 路径 | 用途 | 参数 / 请求体 | 认证 |
| --- | --- | --- | --- | --- |
| GET | `/api/ingestion/tasks` | 列出入库任务 | Query：`kb_id` 可选 | 无 |
| POST | `/api/parse/preview` | 解析预览 | JSON：`pdf_path` 可选 | 无 |
| POST | `/api/ingestion/upload` | 上传 PDF 并创建入库任务 | multipart：`file`；Query：`kb_id`、`strategy`、`subject_type`、`layout_type`、`auto_confirm` | 无 |
| GET | `/api/ingestion/tasks/{task_id}` | 获取任务详情 | 路径参数 `task_id` | 无 |
| DELETE | `/api/ingestion/tasks/{task_id}` | 删除任务；运行中任务可能返回 409 | 路径参数 `task_id` | 无 |
| GET | `/api/ingestion/stream/{task_id}` | SSE 订阅任务进度 | 路径参数 `task_id` | 无 |
| POST | `/api/ingestion/tasks/{task_id}/retry` | 重试失败任务 | 路径参数 `task_id` | 无 |
| GET | `/api/ingestion/chunks/preview/{task_id}` | 查看草稿切片 | 路径参数 `task_id` | 无 |
| PUT | `/api/ingestion/chunks/{draft_id}` | 编辑草稿切片 | JSON：`content` | 无 |
| DELETE | `/api/ingestion/chunks/{draft_id}` | 删除草稿切片 | 路径参数 `draft_id` | 无 |
| POST | `/api/ingestion/chunks/merge` | 合并草稿切片 | JSON：`task_id`、`draft_ids` | 无 |
| POST | `/api/ingestion/chunks/confirm/{task_id}` | 确认草稿并正式入库 | 路径参数 `task_id` | 无 |

上传文档示例：

```bash
curl -X POST "http://localhost:8000/api/ingestion/upload?kb_id=6a30fe65b0b256647e733f4b&strategy=hierarchical&auto_confirm=false" \
  -F "file=@教材.pdf"
```

上传限制：仅支持 `.pdf`，单文件最大 500MB。`/api/ingestion/stream/{task_id}` 的响应类型为 `text/event-stream`。

## RAG 与 Graph RAG

| 方法 | 路径 | 用途 | 请求体 | 认证 |
| --- | --- | --- | --- | --- |
| POST | `/api/rag/query` | 普通 RAG 问答 | `query`、`kb_id`、`top_k`、`min_score`、`use_llm_check`、`use_llm_score` | 可选临时身份头 |
| POST | `/api/rag/graph-query` | Graph RAG 问答 | `query`、`kb_id`、`top_k`、`min_score`、`explain`、`intent` | 可选临时身份头 |

普通 RAG 示例：

```bash
curl -X POST http://localhost:8000/api/rag/query \
  -H "Content-Type: application/json" \
  -H "X-KB-Tenant-Id: 1" \
  -H "X-KB-User-Id: 1" \
  -d "{\"query\":\"细胞膜的主要功能是什么？\",\"kb_id\":\"6a30fe65b0b256647e733f4b\",\"top_k\":8,\"min_score\":0.3}"
```

`top_k` 范围为 1-20，`min_score` 范围为 0-1。带临时身份头时，后端会在召回前校验知识库访问权限。

## OpenAPI v1

OpenAPI v1 是知识库对外开放接口，不是 AI 基座专用接口。当前 AI 基座可以使用 API Key 调用，后续其他第三方系统也应通过独立 `app_id` / API Key / 能力范围 / IP 白名单 / quota 接入。不要把 AI 基座 JWT 当作 OpenAPI Bearer，也不要把 OpenAPI 字段命名或鉴权流程写死为 AI 基座专用。

### AI 基座用户端开放 API 场景表

| API 名称 | API 用途 | 方法 | 路径 | 能力标识 | 强签名要求 | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 查询知识库列表 | 按本人、租户或管理员视角选择可用知识库 | GET | `/openapi/v1/knowledge-bases` | `kb.list` | 生产建议强签名 | 已开放 |
| 上传文件并创建入库任务 | 上传教材 PDF，并选择切片策略、教材类型、教材排版和解析管道 | POST | `/openapi/v1/ingestion/upload` | `ingestion.upload` | 必须强签名 | 已开放 |
| 查询入库任务详情 | 查询解析、清洗、切片、质检、向量化和写库进度 | GET | `/openapi/v1/ingestion/tasks/{task_id}` | `ingestion.read` | 生产建议强签名 | 已开放 |
| 查询入库可选项 | 给 AI 基座用户端下拉框提供切片策略、教材类型、排版和解析管道枚举 | GET | `/openapi/v1/ingestion/options` | `ingestion.options` | 按 Key 策略 | 已开放 |
| 普通 RAG 查询 | 基于指定知识库执行普通问答 | POST | `/openapi/v1/rag/query` | `rag.query` | 生产建议强签名；若 Key 要求签名则强制 | 已开放 |
| Graph RAG 查询 | 基于指定知识库执行图谱增强问答 | POST | `/openapi/v1/rag/graph-query` | `rag.graph_query` | 生产建议强签名；若 Key 要求签名则强制 | 已开放 |
| 清洗提示词追加 | 在系统默认清洗提示词基础上追加用户侧清洗要求 | POST | `/openapi/v1/ingestion/upload` | `ingestion.clean_prompt.append` | 必须强签名 | 已开放 |
| 质检提示词追加 | 在系统默认质检提示词基础上追加用户侧质检重点 | POST | `/openapi/v1/ingestion/upload` | `ingestion.quality_prompt.append` | 必须强签名 | 已开放 |

强签名判断口径：

- 上传文件、提示词追加、后续批量导出或任何写操作必须强签名，因为这些接口会改变知识库内容、影响入库结果或携带大体积请求体。
- 查询类接口生产建议强签名；当 API Key 的 `requireSignature=true` 时，后端已经强制校验签名。
- `user_id` / `role_code` 只能作为查询过滤或代查提示，不能作为可信授权依据；最终权限必须以后端本地身份快照、SSO session 或可信签名上下文为准。
- OpenAPI 不直接接受 AI 基座 JWT 作为 Bearer；Bearer 位置放知识库 API Key。

当 API Key 的 `requireSignature=true` 时，请求必须同时携带：

- `X-KB-Timestamp`：Unix 秒或 ISO 8601 时间戳，默认 5 分钟窗口。
- `X-KB-Nonce`：同一 API Key 在 10 分钟内不可重复。
- `X-KB-Body-SHA256`：原始请求体的 SHA-256 lowercase hex。当前 multipart 上传接口按 `file` 字节内容计算，调用方需对上传文件 bytes 计算该值。
- `X-KB-Signature`：`HMAC-SHA256(plain_api_key, METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + BODY_SHA256)`。

OpenAPI 请求 schema 禁止未知字段，`query` 最大 4000 字符，`kb_id` 必填。示例：

```bash
curl -X POST http://localhost:8000/openapi/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer wwkb_ak_example_secret" \
  -d "{\"query\":\"解释神经元突触传递过程\",\"kb_id\":\"6a30fe65b0b256647e733f4b\",\"top_k\":8,\"min_score\":0.3}"
```

成功响应：

```json
{
  "requestId": "7b2c...",
  "data": {
    "requestId": "7b2c...",
    "answer": "...",
    "citations": [],
    "candidates": []
  }
}
```

### 已开放接口参数

`GET /openapi/v1/knowledge-bases`

| 参数 | 位置 | 类型 | 说明 |
| --- | --- | --- | --- |
| `scope` | query | string | 可选，`mine` / `tenant` / `all`，默认 `mine` |
| `user_id` | query | string | 可选，仅管理员代查时使用 |
| `role_code` | query | string | 可选，只作过滤提示，不直接授权 |
| `page` | query | integer | 可选，默认 1 |
| `page_size` | query | integer | 可选，默认 20，最大 100 |

`POST /openapi/v1/ingestion/upload`

| 参数 | 位置 | 类型 | 说明 |
| --- | --- | --- | --- |
| `file` | form-data | file | 必填，仅支持 PDF，沿用系统 500MB 上限 |
| `kb_id` | form-data | string | 必填，目标知识库 ID |
| `chunk_strategy` | form-data | string | 可选，`hierarchical` / `semantic` / `paragraph` / `fixed_length` / `separator` / `llm` |
| `subject_type` | form-data | string | 可选，默认 `general`，必须遵循 RAG 已支持教材类型 |
| `layout_type` | form-data | string | 可选，默认 `single_column`，必须遵循 RAG 已支持排版类型 |
| `parser_provider` | form-data | string | 可选，`mineru` / `mineru_official` / `ali_document_mind`；当前仅记录为 OpenAPI 请求元数据，真实解析管道仍以运行时 `PDF_PARSER_PROVIDER` 为准 |
| `auto_confirm` | form-data | boolean | 可选，默认 `false` |
| `cleaning_prompt_mode` | form-data | string | 可选，第一期仅允许 `append` |
| `cleaning_prompt_content` | form-data | string | 可选，追加清洗要求，最长 2000 字符；需 API Key 同时具备 `ingestion.clean_prompt.append` |
| `quality_prompt_mode` | form-data | string | 可选，第一期仅允许 `append` |
| `quality_prompt_content` | form-data | string | 可选，追加质检要求，最长 2000 字符；需 API Key 同时具备 `ingestion.quality_prompt.append` |

`GET /openapi/v1/ingestion/options`

返回包含 `chunkStrategies`、`subjectTypes`、`layoutTypes`、`parserProviders`。解析管道覆盖当前 RAG 支持的 `mineru`、`mineru_official`、`ali_document_mind`，并对每个 provider 返回 `available` 与不可用原因，避免 AI 基座用户端下拉框选到当前环境缺少密钥的管道。

## 控制台

| 方法 | 路径 | 用途 | 参数 / 请求体 | 认证 |
| --- | --- | --- | --- | --- |
| GET | `/api/console/overview-metrics` | 控制台概览指标 | 无 | 无 |
| GET | `/api/console/alerts` | 告警摘要 | 无 | 无 |
| GET | `/api/console/queue` | 队列摘要 | 无 | 无 |
| GET | `/api/console/evaluations` | 评测记录 | Query：`kb_id` 可选 | 无 |
| GET | `/api/console/query-logs` | 脱敏 RAG 查询日志 | Query：`kb_id`、`request_id`、`actor_id`、`api_key_id`、`pipeline_domain`、`start_at`、`end_at`、`limit` | 可选临时身份头 |
| GET | `/api/console/query-logs/export.csv` | 按筛选导出脱敏查询日志 CSV | 同上，`limit` 最大 100000 | 可选临时身份头 |
| GET | `/api/console/audit-logs` | 脱敏审计日志 | Query：`actor_id`、`action`、`resource_type`、`resource_id`、`request_id`、`kb_id`、`outcome`、`start_at`、`end_at`、`limit` | 可选身份 |
| GET | `/api/console/identity-sync-logs` | 用户及权限同步运行记录 | Query：`limit`，默认 100，最大 200 | superManager |
| GET | `/api/console/ingestion-tasks` | 控制台入库任务台账 | Query：`keyword`、`status`、`strategy`、`page`、`page_size` | 可选身份 |
| GET | `/api/console/ingestion-logs/latest` | 最新一次入库日志摘要 | Query：`kb_id`、`max_lines` | 可选身份 |
| POST | `/api/console/ingestion-tasks/{task_id}/backfill-llm-usage` | 回填入库任务 LLM 用量 | 路径参数 `task_id` | 管理员 + 新鲜身份快照 |
| GET | `/api/console/token-usage` | 基于 `kb_llm_call_logs` / 小时 rollup 的 token 用量、费用估算和 quota 告警 | Query：`limit`、`pipeline_domain` | 可选身份 |
| GET | `/api/console/api-keys` | 列出 API Key | 无 | 管理员 |
| GET | `/api/console/openapi-apps` | 列出外部调用方 app | 无 | 管理员 |
| POST | `/api/console/openapi-apps` | 创建外部调用方 app | JSON：`name`、`note` | 管理员 + 新鲜身份快照 |
| PATCH | `/api/console/openapi-apps/{app_id}` | 更新 app 名称、状态或备注 | JSON：`name`、`status`、`note` | 管理员 + 新鲜身份快照 |
| DELETE | `/api/console/openapi-apps/{app_id}` | 禁用 / 删除外部调用方 app | 路径参数 `app_id` | 管理员 + 新鲜身份快照 |
| GET | `/api/console/settings` | 配置中心项目列表 | 无 | 无 |
| PUT | `/api/console/settings` | 保存配置项 | JSON 键值对象 | 管理员 + 新鲜身份快照 |

日志导出示例：

```bash
curl -L "http://localhost:8000/api/console/query-logs/export.csv?kb_id=6a30fe65b0b256647e733f4b&limit=1000" \
  -H "X-KB-Tenant-Id: 1" \
  -H "X-KB-User-Id: 1" \
  -o query-logs.csv
```

导出只包含 `kb_rag_query_logs` 中的脱敏字段，不导出完整 query、answer、prompt、文档正文、provider 原始响应或 API Key 明文。查询日志导出会写入 `query_logs.export` 脱敏审计；导出文件自动清理仍未实现。

## API Key 管理

API Key 管理面向通用外部调用方。创建 API Key 时应保留调用方名称、调用方类型、应用 ID、绑定知识库、能力范围、有效期、IP 白名单、quota 等扩展空间；当前最小实现不应理解为 AI 基座专属 Key。

| 方法 | 路径 | 用途 | 请求体 / 参数 | 认证 |
| --- | --- | --- | --- | --- |
| GET | `/api/console/api-keys` | 列出 API Key | 无 | 可选临时身份头；带身份时要求管理员 |
| POST | `/api/console/api-keys` | 创建 API Key | JSON：`appId`、`name`、`kbIds`、`capabilities`、`requireSignature`、`allowedIps`、`rpmLimit`、`dailyRequestLimit`、`note`、`expiresAt` | 管理员 + 新鲜身份快照 |
| PATCH | `/api/console/api-keys/{key_id}` | 更新名称、状态、绑定知识库、能力、签名、IP、配额、备注、过期时间 | JSON：可选字段；`status` 为 `active` 或 `disabled` | 管理员 + 新鲜身份快照 |
| POST | `/api/console/api-keys/{key_id}/rotate` | 轮换 secret，返回一次性明文 | 路径参数 `key_id` | 管理员 + 新鲜身份快照 |
| DELETE | `/api/console/api-keys/{key_id}` | 软删除 API Key | 路径参数 `key_id` | 管理员 + 新鲜身份快照 |

创建 API Key 示例：

```bash
curl -X POST http://localhost:8000/api/console/api-keys \
  -H "Content-Type: application/json" \
  -H "X-KB-Tenant-Id: 1" \
  -H "X-KB-User-Id: 1" \
  -d "{\"name\":\"外部问答服务\",\"kbIds\":[\"6a30fe65b0b256647e733f4b\"],\"capabilities\":[\"rag.query\",\"rag.graph_query\"],\"note\":\"联调用\",\"expiresAt\":null}"
```

轮换 API Key 示例：

```bash
curl -X POST http://localhost:8000/api/console/api-keys/ak_example/rotate \
  -H "X-KB-Tenant-Id: 1" \
  -H "X-KB-User-Id: 1"
```

创建和轮换响应会返回一次性 `apiKey` 明文；后续列表只返回前缀、后缀、状态、绑定、签名/IP/配额和时间等元数据。`rpmLimit` 与 `dailyRequestLimit` 当前已在鉴权路径强制拦截，超限返回 `RATE_LIMITED` 或 `QUOTA_EXCEEDED`。

## 评测、仪表盘与静态资源

| 方法 | 路径 | 用途 | 认证 |
| --- | --- | --- | --- |
| GET | `/api/eval/reports` | 离线评测报告列表 | 无 |
| GET | `/api/dashboard/stats` | 仪表盘统计 | 无 |
| GET | `/api/assets/output/**` | 解析输出图片等静态资源 | 无 |

## 已知文档边界

1. 本文记录当前代码已暴露的接口，不等同于 BRD 目标状态。
2. 标记为“当前未完整接入临时身份裁剪”的接口，是 Phase 11 后续子资源权限收口重点。
3. OpenAPI v1 已支持 API Key 鉴权和强验证闭环：HMAC 签名、timestamp、nonce、防重放、body hash、API Key 级 IP 白名单、分钟请求上限、每日请求配额和鉴权失败脱敏审计；并发拦截、月度配额和 SDK 包仍在延后 backlog。

## 2026-06-22 补充：Token 用量明细接口

`GET /api/console/token-usage` 当前返回面向成本治理的环节级结构：

- `source`：目标明细源，当前为 `kb_llm_call_logs`。
- `fallbackSource`：兼容兜底源，当前为 `kb_rag_query_logs`。
- `scope`：`all_tenants` / `tenant` / `unscoped`，用于说明本次统计的数据范围。
- `detailAvailable`：是否已有真实模型调用明细。
- `pipelineStages`：按功能环节聚合的 Token 用量，覆盖解析、清洗、切片、质量审核、向量化、重排、问答生成、评测等环节。
- `llmCalls`：最近模型调用明细，每条包含供应商、模型名称、模型版本、输入 Token、输出 Token、总 Token、延迟、状态、请求 ID、知识库和 API Key。
- `chartReady`：图表统计开关标记；本期固定为 `false`，后续趋势图和环节对比图不在本次实现范围。

权限口径：平台超级管理员可查看所有租户、所有知识库、所有环节的消耗明细；普通已登录身份默认只查看当前租户范围；未登录请求保留历史兼容口径。当前接口不会返回 prompt 原文、answer 原文、文档正文、provider 原始响应或 API Key 明文。
