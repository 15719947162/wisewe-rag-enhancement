# 外部治理能力对接契约

更新时间：2026-06-26

本文补齐 Phase 11 中依赖 AI 基座 SSO、外部调用方、安全网关或后续治理闭环的对接约定。它是目标契约和联调口径，不代表当前代码已经全部实现。

重要边界：AI 基座是知识库后台 SSO 与身份快照的事实源，但不是 OpenAPI / API Key 的唯一调用方。`/openapi/v1/*` 必须按通用外部调用方模型设计，既支持 AI 基座调用，也支持后续教务系统、内容平台、第三方应用、客户自有业务系统等其他系统接入。API Key、HMAC、IP 白名单、quota、审计和 SDK 示例不得写死为 AI 基座专用协议。

当前实现边界：

- 已实现：AI 基座 SSO launch / callback / exchange 与知识库本地短 session、当前用户刷新、HTTP delta 身份同步、用户及权限同步日志、本地只读身份快照、旧请求头联调兜底开关、本地 API Key 生命周期、通用调用方 app 模型、OpenAPI v1 Bearer / `X-API-Key` 鉴权、API Key 默认强签名、timestamp / nonce 防重放、body hash、API Key 级 IP 白名单、分钟请求上限 / 每日请求配额强制拦截、OpenAPI 鉴权失败脱敏审计、查询日志、审计日志、Token 小时 rollup、失效用户归属待转交标记与管理员转交接口。
- 尚未实现或未完整闭合：并发限制、月度配额、多语言正式 SDK 包、配置版本化 / 回滚、全量子资源权限收口、历史知识库归属迁移检查、身份同步失败重试、访问拒绝审计覆盖所有入口、AI 基座单点退出生产联调闭环。

## 对接矩阵

| 能力 | 依赖方 | 知识库侧目标责任 | 当前状态 |
| --- | --- | --- | --- |
| AI 基座 SSO | AI 基座租户端、统一登录服务 | 推荐接收一次性 `sso_code`，服务端换取身份摘要，建立短 session；如 AI 基座必须交付 JWT，则 JWT 只作为后端一次性交换 / 校验凭证，不作为知识库长期 session | RAG launch / callback / exchange / 本地短 session 已实现；JWKS 本地验签 fallback 仅作为后续兼容项 |
| 身份快照同步 | AI 基座 MySQL 或同步服务 | 只读同步租户、用户、角色、用户角色关系；不维护可写 IAM 主数据 | HTTP delta、当前用户刷新、同步日志和 SSO `superManager` 受控触发已实现；仍需补失败重试 |
| OpenAPI 强签名 | AI 基座、第三方系统、安全网关 | 对 `/openapi/v1/*` 校验 HMAC、timestamp、nonce、body hash 和 key 能力范围；调用方模型必须通用化，不绑定 AI 基座 | 已实现 HMAC 强签名、IP 白名单、认证失败审计和通用 app 模型；多语言正式 SDK 待补 |
| IP 白名单 | AI 基座、第三方系统、安全网关 | 按 API Key、调用方应用或租户配置 CIDR 白名单，拒绝非可信来源 | 已支持 API Key 级单 IP / CIDR |
| QPS / quota / 并发 | 配额服务、OpenAPI 网关或知识库后端 | 按 API Key、调用方应用、租户、知识库维度限制调用量和并发 | API Key 级 rpm / daily request limit 已强制拦截；并发限制、月度配额和更细报表未实现 |
| 审计日志 | 审计中心或知识库本地审计表 | 记录身份交换、签名失败、nonce 重放、IP 拒绝、导出、配置变更和 API Key 生命周期 | API Key / app 生命周期、OpenAPI 鉴权失败、配置变更、查询日志导出、退出回调等已写脱敏审计；访问拒绝与子资源操作仍未全覆盖 |
| 配置版本 / 回滚 | 配置中心 | 对全局、租户、知识库、API Key 和 Prompt Profile 变更做版本化、灰度、回滚和审计 | 未实现 |
| 失效用户接管 | AI 基座身份状态、租户管理员工作台 | 用户禁用、离职或移出租户后阻断访问，并支持管理员接管、转交、待认领和恢复 | 已支持失效归属人知识库标记为 `pending_transfer` 并通过管理员接口转交；批量待处理工作台和恢复流程未完整实现 |

## AI 基座 SSO 契约

详细流程以 [ai-base-sso-integration-guide.md](./ai-base-sso-integration-guide.md) 为准；本文只固化跨系统契约。

### 跳转与回调

AI 基座从租户端发起跳转：

```text
GET /login/sso/callback?sso_code=<one_time_code>&state=<opaque_state>
```

知识库侧要求：

- `sso_code` 必须一次性使用，短有效期，建议 60-180 秒。
- `state` 必须由知识库侧预先生成并绑定浏览器临时态，回调时校验，防止 CSRF 和跨租户跳转串用。
- `sso_code` 只能由知识库后端拿去换身份摘要，前端不得直接请求 AI 基座换取用户信息。
- 回调成功后由知识库后端建立短 session；临时 `X-KB-Tenant-Id` / `X-KB-User-Id` 只保留为开发和联调兼容路径。

如果 AI 基座采用 JWT 认证机制，推荐仍由 AI 基座在租户端内部校验 JWT 后生成一次性 `sso_code`。若短期内必须把 JWT 交给知识库，则必须满足：

- JWT 只能通过 HTTPS POST、后端 callback 或一次性 exchange 接口交给知识库后端，禁止 URL query。
- 知识库后端校验 JWT 的 `iss`、`aud`、`exp`、`nbf`、`iat`、`kid`、签名算法白名单、签名和 `jti`。
- JWT 交换成功后立即创建知识库本地短 session，并丢弃 JWT 原文。
- 后续控制台 API 使用知识库 session，不透传 AI 基座 JWT。
- `/openapi/v1/*` 继续使用 API Key / HMAC 强签名，不接受 AI 基座 JWT，避免与外部系统 Bearer API Key 混淆。

### 身份交换响应

AI 基座返回的身份摘要建议包含：

```json
{
  "tenantId": "1",
  "userId": "10001",
  "userStatus": "active",
  "roles": [
    {"roleCode": "superManager", "roleName": "租户管理员"}
  ],
  "issuedAt": "2026-06-21T10:00:00Z",
  "expiresAt": "2026-06-21T10:03:00Z"
}
```

知识库侧不接收密码、密码 hash、盐值、手机号明文、邮箱明文或 AI 基座长期 token。手机号和邮箱如需展示或审计，只允许使用脱敏快照。

JWT 兼容模式下，知识库侧也不保存 JWT 原文、refresh token 或 AI 基座长期登录 token；如需审计，只保存 `jti`、`kid`、issuer、audience、subject、tenantId 和 token hash 指纹。

### 会话与退出

- 知识库 session 只存最小身份上下文：`tenant_id`、`user_id`、管理员标记、身份快照版本和过期时间。
- session 过期、用户禁用、租户禁用或快照刷新发现用户已离开租户时，必须拒绝继续访问。
- 退出登录时清理知识库 session；如 AI 基座支持单点退出，后续可接入统一 logout 回调。

## OpenAPI 强签名契约

当前 `/openapi/v1/*` 已支持 Bearer / `X-API-Key` 取出一次性明文 API Key，并在 `requireSignature=true` 时强制校验 HMAC、timestamp、nonce、body hash 和 IP 白名单。下一阶段生产目标是进一步引入 key id / app id 派生机制，减少跨系统链路反复传输真实明文 key。

OpenAPI 调用方模型必须保持开放：

- AI 基座只是可接入的调用方之一，不是 API Key 协议的唯一主体。
- `X-WW-App-Id` / `app_id` 表示外部调用方应用或系统 ID，可以是 AI 基座应用，也可以是其他第三方系统应用。
- API Key 绑定的是知识库资源、能力范围、租户边界、调用方应用和安全策略，不绑定 AI 基座登录 JWT。
- 后续新增第三方系统时，应通过新增 app/API Key/白名单/quota 配置接入，不应新增一套与 AI 基座耦合的 OpenAPI 协议。
- 审计日志应记录通用 `caller_app_id`、`api_key_id`、`tenant_id`、`kb_id`、`capability`、`requestId`，而不是只记录 AI 基座专用字段。

### 请求头

生产强签名请求必须包含：

| Header | 说明 |
| --- | --- |
| `X-WW-App-Id` | 外部调用方应用 ID 或系统 ID |
| `X-WW-Key-Id` | API Key ID，不是 API Key 明文 |
| `X-WW-Timestamp` | Unix 秒或 ISO 8601 时间戳，服务端默认允许 300 秒时钟偏差 |
| `X-WW-Nonce` | 调用方生成的随机串，同一 key 在有效窗口内不得重复 |
| `X-WW-Body-SHA256` | 原始请求体 SHA-256 摘要；空 body 使用空字符串 SHA-256 |
| `X-WW-Signature` | HMAC 签名结果 |
| `X-WW-Signature-Version` | 可选，默认 `v1` |

### 规范化字符串

签名输入固定为：

```text
{METHOD}
{PATH_WITH_QUERY}
{TIMESTAMP}
{NONCE}
{BODY_SHA256}
```

示例：

```text
POST
/openapi/v1/rag/query
1782036000
01J1D7Z3ZV2V6N8M9K0Q1R2S3T
4f8b42c2c7...
```

签名算法：

```text
signature = HMAC-SHA256(<api_key_secret>, canonical_string)
```

编码建议统一为 lowercase hex；如果外部网关要求 base64，必须通过 `X-WW-Signature-Version` 明确区分。

### 校验顺序

服务端目标校验顺序：

1. 校验必要 header 是否存在。
2. 根据 `X-WW-Key-Id` 找到 API Key 元数据和 hash / secret 派生材料。
3. 校验 key 状态、过期时间、绑定知识库、能力范围和租户边界。
4. 校验 timestamp 窗口，默认允许 300 秒偏差。
5. 校验 body hash 与原始 body 完全一致。
6. 校验 nonce 未被同一 key 使用过。
7. 校验 HMAC 签名。
8. 写入 nonce 防重放缓存，建议保留 10-30 分钟。
9. 继续执行业务请求。

签名失败、body hash 不一致、nonce 重放和 IP 拒绝都必须写脱敏审计记录。

### 错误码

强签名相关错误码目标如下：

| 错误码 | 触发条件 |
| --- | --- |
| `SIGNATURE_REQUIRED` | 缺少强签名必要 header |
| `SIGNATURE_INVALID` | HMAC 校验失败或签名版本不支持 |
| `TIMESTAMP_EXPIRED` | timestamp 超出允许窗口 |
| `NONCE_REPLAYED` | 同一 key 的 nonce 在有效窗口内重复 |
| `BODY_HASH_MISMATCH` | 请求体摘要和 header 不一致 |
| `IP_NOT_ALLOWED` | 来源 IP 不在白名单 |
| `QUOTA_EXCEEDED` | 日 / 月 / 总调用量或 token 配额耗尽 |
| `RATE_LIMITED` | QPS 超阈值 |
| `CONCURRENCY_LIMITED` | 并发请求数超阈值 |

错误响应沿用当前 OpenAPI 格式：

```json
{
  "requestId": "7b2c...",
  "error": {
    "code": "SIGNATURE_INVALID",
    "message": "OpenAPI signature verification failed",
    "details": {}
  }
}
```

## IP 白名单契约

- 白名单可以绑定到 API Key、外部应用或租户，支持单 IP 和 CIDR。
- 当某个 key 配置了白名单时，默认拒绝不在白名单内的来源。
- 后端只能信任可信代理注入的来源 IP，例如由网关清洗后的 `X-Forwarded-For`；不能直接信任调用方自带的任意 IP 头。
- 审计日志记录脱敏后的来源 IP、key_id、tenant_id、kb_id、requestId 和拒绝原因，不记录 API Key 明文或签名 secret。

## 配额与限流契约

配额目标维度：

- `tenant_id`
- `kb_id`
- `api_key_id`
- `capability`
- `pipeline_domain`

限制类型：

- QPS
- 并发请求数
- 每日 / 每月调用量
- 每日 / 每月 token 用量
- 单次请求最大 `query` 字符数、`top_k`、文件大小和导出行数

触发限制时，OpenAPI 返回 `RATE_LIMITED`、`QUOTA_EXCEEDED` 或 `CONCURRENCY_LIMITED`，并在 `details` 中返回可安全公开的限制类型、窗口和重试建议。

## 审计与日志契约

必须写审计的事件：

- SSO code exchange 成功 / 失败。
- state 校验失败、code 过期、code 重放。
- API Key 创建、轮换、禁用、删除、绑定知识库变化、能力范围变化。
- OpenAPI 签名失败、timestamp 过期、nonce 重放、body hash 不匹配、IP 拒绝。
- 知识库删除 / 恢复、文档删除、源文件下载、CSV 导出。
- 配置变更、Prompt Profile 发布、灰度、回滚。
- 失效用户知识库接管、转交、待认领、恢复。

日志安全要求：

- 永不记录 API Key 明文、签名 secret、模型 Key、AK/SK、SSO code、session token、用户密码、密码 hash 和盐值。
- 默认不记录完整 query、answer、prompt、文档正文或 provider 原始响应。
- 如需短期敏感原文调试，必须由管理员显式开启，默认 7-14 天清理，全程写审计。

## 配置版本与回滚契约

目标配置对象：

- 全局运行配置。
- 租户级治理配置。
- 知识库级问答、入库、召回、日志配置。
- API Key 级签名、IP、quota 和能力配置。
- Prompt Profile 与模型参数版本。

每次变更必须生成版本记录：

```json
{
  "configScope": "knowledge_base",
  "targetId": "6a30fe65b0b256647e733f4b",
  "version": 12,
  "changeType": "update",
  "changedBy": "10001",
  "changedAt": "2026-06-21T10:00:00Z",
  "diffSummary": {
    "topK": {"from": 8, "to": 10}
  }
}
```

敏感配置值只允许保存密文或外部 secret 引用；版本 diff 中只能展示掩码、hash、fingerprint 或引用 ID。

## 失效用户接管契约

当 AI 基座快照显示用户禁用、离职或移出租户：

- 该用户不能继续登录或调用知识库资源。
- 其历史知识库不自动删除。
- `created_by` 保留原创建人用于审计。
- `owner_user_id` 表示当前业务归属人，可由租户管理员接管或转交。
- 无法自动归属的历史知识库进入待认领状态，普通用户不可见，仅租户管理员和平台管理员可处理。

目标操作包括：

- 接管为租户管理员本人。
- 转交给同租户有效用户。
- 恢复到原用户，前提是 AI 基座确认用户重新有效且仍在租户内。
- 删除或归档知识库，保留脱敏审计和用量统计。

## 最小联调清单

SSO：

- 正常 code exchange 成功并建立知识库 session。
- 正常 JWT exchange 成功并建立知识库 session。
- 过期 code 被拒绝。
- 重放 code 被拒绝。
- 过期 JWT 被拒绝。
- JWT `aud` / `iss` 不匹配被拒绝。
- JWT `kid` 不存在或算法不在白名单时被拒绝。
- 一次性交换 JWT 的 `jti` 重放被拒绝。
- state 不匹配被拒绝。
- 用户禁用或移出租户后 session 失效。

强签名：

- 正确签名请求成功。
- 缺少签名返回 `SIGNATURE_REQUIRED`。
- 篡改 body 返回 `BODY_HASH_MISMATCH`。
- 篡改签名返回 `SIGNATURE_INVALID`。
- timestamp 超窗返回 `TIMESTAMP_EXPIRED`。
- nonce 重放返回 `NONCE_REPLAYED`。
- 非白名单 IP 返回 `IP_NOT_ALLOWED`。

审计：

- 上述失败场景均有脱敏审计记录。
- 审计记录不包含 API Key 明文、secret、SSO code、session token 或完整 prompt。

## 与现有文档关系

- BRD 来源：[knowledge-base-governance-brd.md](./knowledge-base-governance-brd.md)
- Phase 11 缺口清单：[phase11-gap-analysis.md](./phase11-gap-analysis.md)
- SSO 详细流程：[ai-base-sso-integration-guide.md](./ai-base-sso-integration-guide.md)
- 当前实际 API：[../api/reference.md](../api/reference.md)
