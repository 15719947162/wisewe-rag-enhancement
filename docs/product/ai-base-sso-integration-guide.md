# AI 基座租户端 SSO 对接帮助文档

本文档说明 AI 基座租户端如何为 WiseWe RAG 知识库提供 SSO 登录能力。目标是让知识库系统消费 AI 基座的身份事实，同时不接触 AI 基座密码、长期登录 token 或可写权限主数据。

> 2026-06-22 补充：AI 基座采用 JWT 认证机制时，推荐仍由 AI 基座用自身 JWT 校验当前登录态，再向知识库发放一次性 `sso_code`。如联调形态必须让知识库消费 AI 基座 JWT，JWT 只能作为后端一次性交换凭证或服务端校验凭证；知识库不得把 AI 基座 JWT 当成自身长期 session，也不得在前端 URL、localStorage 或日志中保存 JWT 原文。
>
> 2026-06-24 对齐：当前联调入口统一由 RAG `/api/auth/ai-base/launch` 发起。无论入口按钮在 RAG 登录页还是 AI 基座页面，浏览器都应先进入 RAG launch，由 RAG 生成随机 `state`、写入 RAG HttpOnly cookie，再 302 到 AI 基座租户端 `/sso`。AI 基座页面只是入口页，不直接代替 RAG 发起 OAuth / SSO 事务。

## 1. 设计原则

1. AI 基座是租户、用户、角色、用户角色关系的身份事实源。
2. 知识库系统不是 IAM，不维护可写用户、角色、权限策略或用户角色关系。
3. 推荐模式下，知识库后台登录只接收一次性 `sso_code`，再由知识库后端服务端换取身份摘要。
4. JWT 兼容模式下，AI 基座 JWT 只允许作为知识库后端一次性交换 / 校验凭证，不能作为知识库长期登录态。
5. 浏览器 URL 中不得携带用户资料、角色列表、手机号、邮箱、AI 基座 JWT、AI 基座登录 token 或长期凭证。
6. 知识库只创建自身短期后台 session；该 session 只在知识库后台有效。
7. AI 基座账号禁用、离职、移出租户或租户停用时，SSO 换取身份摘要必须拒绝。

## 2. 推荐交互流程

### 2.1 推荐模式：JWT 内部认证 + 一次性 sso_code

该模式最适合生产登录闭环。AI 基座可以继续采用 JWT 作为自身租户端登录机制，但跨系统跳转给知识库的凭证仍是一次性 `sso_code`。

```text
用户点击“AI 基座登录”或从 AI 基座页面点击“进入知识库”
  -> 浏览器访问 RAG /api/auth/ai-base/launch
  -> RAG 生成随机 state，写入 RAG HttpOnly state cookie
  -> RAG 302 到 AI 基座 /sso?client_id=rag-client&redirect_uri=...&state=...
  -> AI 基座用自身 JWT / session 校验当前用户、租户、redirect_uri 和 client_id
  -> AI 基座生成一次性 sso_code，并原样透传 state
  -> 浏览器 302 回 RAG /api/auth/ai-base/callback?code=...&state=...
  -> RAG 校验 state 与 RAG cookie 一致
  -> 知识库后端用 sso_code 向 AI 基座服务端换身份摘要
  -> 知识库刷新当前用户、租户、角色和用户角色关系只读快照
  -> 知识库创建短期后台 session
  -> 用户进入知识库控制台
```

该模式下：

- JWT 不离开 AI 基座认证边界，知识库不需要解析 AI 基座 JWT。
- 浏览器 URL 只出现短期、一次性、无用户资料的 `sso_code` 和 `state`。
- `state` 的生成方和校验方都是 RAG；`state` 不是固定业务跳转标识，也不由 AI 基座后端向 RAG 预取。
- AI 基座页面上的“进入知识库”按钮应把浏览器导向 RAG `/api/auth/ai-base/launch`，可附加 `next=/knowledge-bases` 等 RAG 本地安全路径。
- code 的重放、过期、redirect_uri 绑定和服务端到服务端鉴权仍按本文后续规则执行。

### 2.2 兼容模式：AI 基座 JWT 一次性交换

仅当 AI 基座暂时无法提供 `sso_code` broker 时，允许采用 JWT 兼容模式。该模式下 JWT 只能被知识库后端消费一次，用于换取或构造身份摘要，并立即转成本地短期 session。

```text
用户已登录 AI 基座租户端
  -> 点击“知识库”
  -> AI 基座校验当前用户、租户、跳转地址
  -> AI 基座将短期 JWT 通过安全方式交给知识库后端 callback / exchange
  -> 知识库后端校验 JWT 签名、issuer、audience、有效期、jti 和用户状态
  -> 知识库刷新当前用户、租户、角色和用户角色关系只读快照
  -> 知识库创建短期后台 session
  -> 知识库丢弃 JWT 原文，仅保留必要审计指纹
  -> 用户进入知识库控制台
```

JWT 传递方式按推荐程度排序：

1. AI 基座仍生成一次性 `sso_code`，知识库后端用 code 换摘要。
2. AI 基座使用 `form_post` 将短期 JWT POST 到知识库后端 callback。
3. AI 基座前端短暂调用知识库 `/api/auth/ai-base/exchange`，使用 `Authorization: Bearer <jwt>` 或 POST body 提交 JWT，知识库后端立即交换成本地 session。
4. 禁止通过 URL query 携带 JWT，例如 `?token=<jwt>`。

JWT 兼容模式不改变知识库的会话边界：后续控制台 API 仍使用知识库本地 session，不透传 AI 基座 JWT。

## 3. AI 基座需要提供的能力

### 3.1 SSO 浏览器入口

RAG launch 负责发起一次性登录事务，AI 基座租户端提供实际 SSO 认证入口。

```http
GET /sso?client_id=rag-client&redirect_uri={redirect_uri}&state={state}
```

AI 基座处理要求：

- 校验当前用户已登录；若 AI 基座采用 JWT，应先校验自身 JWT / session。
- 校验用户状态有效。
- 校验租户状态有效。
- 校验 `client_id` 为已登记的知识库应用。
- 校验 `redirect_uri` 在知识库应用白名单内。
- 原样透传 RAG 提供的 `state`，不得改写为固定业务值。
- 生成一次性 `sso_code`。
- 服务端保存 `code_hash`、`tenant_id`、`user_id`、`client_id`、`redirect_uri`、`expires_at`、`used=false`。
- 302 跳转到知识库 callback。

AI 基座页面作为入口页时，不建议后端调用 RAG “取 state”。后端拿到的 `state` 无法自然写入用户浏览器里的 RAG cookie，容易引入跨域 cookie 或前端中转复杂度。推荐做法是入口按钮直接让浏览器访问 RAG `/api/auth/ai-base/launch`。

示例跳转：

```text
https://kb.example.com/auth/ai-base/callback?code=one_time_code&state=opaque_state
```

`sso_code` 建议：

- 随机强度不少于 128 bit。
- 有效期 60-120 秒。
- 只能使用一次。
- 服务端只存 hash，不存明文。
- 绑定 `client_id`、`redirect_uri`、`tenant_id`、`user_id`。

如果 AI 基座内部登录态是 JWT，`sso_code` 生成事件应关联 JWT 的 `sub`、`tenant_id`、`jti` 或登录会话 ID，但不得把 JWT 原文写入 code 记录或审计日志。

### 3.2 SSO 凭证换身份摘要接口

该接口只允许知识库后端服务端调用，或由知识库后端 callback 在服务端侧消费。浏览器不得直接调用 AI 基座换取用户资料。

```http
POST /internal/sso/exchange
Content-Type: application/json
```

#### 3.2.1 code 模式请求示例

字段说明：

- `client_id`：AI 基座为知识库系统分配的应用标识，例如 `wisewe-kb`。
- `client_secret`：AI 基座为该 `client_id` 分配的服务端应用密钥，只用于知识库后端调用 AI 基座 `/internal/sso/exchange` 时证明“调用方确实是知识库服务端”。它不是用户密码、不是 AI 基座 JWT、不是知识库 session，也不能下发到浏览器、前端代码、移动端或第三方系统。
- 生产环境可从明文 `client_secret` 提交升级为 HMAC 签名或 mTLS；即使使用 HMAC，`client_secret` 也只作为服务端签名密钥，不进入 URL、日志或导出文件。

```json
{
  "client_id": "wisewe-kb",
  "client_secret": "***",
  "grant_type": "authorization_code",
  "code": "one_time_code",
  "redirect_uri": "https://kb.example.com/auth/ai-base/callback"
}
```

#### 3.2.2 JWT 模式请求示例

如果 AI 基座采用 JWT 一次性交换，可以使用显式 grant type：

```json
{
  "client_id": "wisewe-kb",
  "client_secret": "***",
  "grant_type": "jwt",
  "jwt": "***",
  "redirect_uri": "https://kb.example.com/auth/ai-base/callback"
}
```

也可以采用 OAuth 2.0 Token Exchange 风格：

```json
{
  "client_id": "wisewe-kb",
  "client_secret": "***",
  "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
  "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
  "subject_token": "***",
  "redirect_uri": "https://kb.example.com/auth/ai-base/callback"
}
```

JWT 模式要求：

- JWT 必须短有效期，建议 5-15 分钟以内；如果只用于一次性交换，建议 60-180 秒。
- JWT 必须以 HTTPS POST 或后端服务端方式传递，禁止 query string。
- JWT 原文不得落库、入日志、进入导出文件或保存在浏览器长期存储。
- 成功交换后，知识库只保存本地 session 和 JWT 指纹 / `jti` 等审计关联字段。

#### 3.2.3 响应示例

响应示例：

```json
{
  "tenant": {
    "tenant_id": "t_001",
    "tenant_name": "示例租户",
    "status": "active"
  },
  "user": {
    "user_id": "u_001",
    "username": "zhangsan",
    "display_name": "张三",
    "mobile_masked": "138****0000",
    "email_masked": "z***@example.com",
    "status": "active"
  },
  "roles": [
    {
      "role_id": "r_001",
      "role_code": "superManager",
      "role_name": "超级管理员",
      "status": "active"
    }
  ],
  "user_roles": [
    {
      "user_id": "u_001",
      "role_id": "r_001",
      "tenant_id": "t_001",
      "status": "active"
    }
  ],
  "snapshot_version": "20260618120000",
  "issued_at": "2026-06-18T12:00:00+08:00",
  "expires_in": 300
}
```

接口处理要求：

- 校验 `client_id` 和 `client_secret`，也可升级为 HMAC 或 mTLS。
- code 模式下，校验 `code` 存在、未过期、未使用、未被吊销。
- JWT 模式下，校验 JWT 签名、issuer、audience、生效时间、过期时间、算法、`kid`、`jti` 和吊销状态。
- 校验 `redirect_uri` 与发起登录或生成凭证时一致。
- 校验用户、租户、角色状态。
- code 模式成功后立即将 code 标记为已使用。
- JWT 一次性交换模式成功后应记录 `jti` 已使用，防止重放。
- 返回身份摘要，不返回 AI 基座长期 token、refresh token 或 JWT 原文。

### 3.3 JWT 公钥与元数据接口

若采用 JWT 兼容模式，AI 基座必须提供稳定的公钥发现机制，建议采用 JWKS。

```http
GET /.well-known/jwks.json
```

JWKS 示例：

```json
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "ai-base-2026-06",
      "use": "sig",
      "alg": "RS256",
      "n": "...",
      "e": "AQAB"
    }
  ]
}
```

要求：

- 每个 JWT header 必须带 `kid`。
- 知识库按 `kid` 选择公钥并缓存 JWKS；缓存需支持过期刷新。
- 密钥轮换期间，新旧公钥需要并存一个过渡窗口。
- 不允许 `alg=none`。
- 不允许知识库根据 token header 动态接受任意算法；必须使用算法白名单。
- 推荐算法：`RS256` 或 `ES256`。如使用对称算法，必须明确 secret 分发、轮换和隔离方案，生产不优先推荐。

JWT 推荐声明：

```json
{
  "iss": "https://ai-base.example.com",
  "aud": "wisewe-kb",
  "sub": "u_001",
  "tenant_id": "t_001",
  "role_codes": ["superManager"],
  "jti": "01J1D7Z3ZV2V6N8M9K0Q1R2S3T",
  "iat": 1782036000,
  "nbf": 1782036000,
  "exp": 1782036600
}
```

JWT 中不建议放入：

- 手机号明文、邮箱明文、身份证号等个人敏感信息。
- 密码、密码 hash、盐值。
- 长期 refresh token。
- 完整菜单权限、可写权限策略或大量组织树。
- 与知识库无关的业务数据。

### 3.4 身份快照增量同步接口

知识库需要受控同步租户、用户、角色、用户角色关系只读快照。

详细增量同步契约、`last_sync_at/max_updated_at` 水位定义、删除 / 停用 / 角色移除检测、登录时兜底刷新、重试和验收清单见 [ai-base-identity-delta-sync-guide.md](./ai-base-identity-delta-sync-guide.md)。本节只保留 SSO 文档内的最小接口摘要。

`last_sync_at` 字段说明：

- `last_sync_at` 是知识库发给 AI 基座的“同步水位 / 书签”，表示知识库已经成功处理到哪个变更位置。
- `last_sync_at` 不是权限字段、不是用户身份、不是 token，也不参与登录态判断。
- 首次同步时 `last_sync_at` 可以为空字符串；AI 基座应从初始化水位或全量快照起点返回数据。
- 后续同步时，知识库把上次成功运行记录中的 `max_updated_at` 作为下一次请求的 `last_sync_at`，AI 基座只返回该位置之后的变更。
- 第一期可约定 `last_sync_at` 为 AI 基座身份数据的变更时间水位，例如 `changed_at` 或 `updated_at`；`change_id` / 版本号属于增强项，不作为第一期强依赖。
- 如果只能提供单一时间水位，知识库侧会启用回看窗口并通过主键幂等 upsert 去重；AI 基座必须确保新增、修改、停用、软删除、角色授予、角色移除都会更新该变更时间。
- 如能提供稳定排序键，推荐使用 `changed_at + source_id` 作为内部排序辅助，避免同一时间点多条变更漏同步；不建议使用普通分页页码如 `page=3` 作为增量同步依据。
- 只有当知识库整批处理成功并落库后，才能把本地 `max_updated_at` 更新为下一次请求的 `last_sync_at`；失败时保留旧水位，下次重试。

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at={lastSyncAt}
```

响应示例：

```json
{
  "last_sync_at": "2026-06-18 12:00:00",
  "max_updated_at": "2026-06-18 12:30:00",
  "has_more": false,
  "tenants": [
    {
      "tenant_id": "t_001",
      "tenant_name": "示例租户",
      "status": "active",
      "updated_at": "2026-06-18T12:00:00+08:00"
    }
  ],
  "users": [
    {
      "user_id": "u_001",
      "tenant_id": "t_001",
      "username": "zhangsan",
      "display_name": "张三",
      "mobile_masked": "138****0000",
      "email_masked": "z***@example.com",
      "status": "active",
      "updated_at": "2026-06-18T12:00:00+08:00"
    }
  ],
  "roles": [
    {
      "role_id": "r_001",
      "tenant_id": "t_001",
      "role_code": "superManager",
      "role_name": "超级管理员",
      "status": "active",
      "updated_at": "2026-06-18T12:00:00+08:00"
    }
  ],
  "user_roles": [
    {
      "tenant_id": "t_001",
      "user_id": "u_001",
      "role_id": "r_001",
      "status": "active",
      "updated_at": "2026-06-18T12:00:00+08:00"
    }
  ]
}
```

同步要求：

- 支持 `last_sync_at/max_updated_at` 时间水位增量同步。
- 返回删除、停用、禁用、离职、移出租户等状态变化。
- 不返回密码、密码 hash、盐值、登录凭证或 session token。
- 手机号、邮箱等个人信息只返回脱敏值，除非后续有明确合规授权。

## 4. 知识库侧处理规则

知识库收到身份摘要后：

1. 刷新当前租户、用户、角色、用户角色关系只读快照。
2. 按 `tenant_id` 建立租户隔离上下文。
3. 按 `role_code=superManager` 判断第一期租户管理员 / 知识库管理员。
4. 按独立配置判断平台管理员角色，不从 `superManager` 自动推断。
5. 创建知识库自身短期后台 session。
6. 后续后台 API 仍必须服务端执行租户隔离与当前业务归属人 / 管理员访问判断。
7. JWT 模式下，JWT 校验通过只代表 token 可信，不代表用户当前仍可访问知识库；知识库仍需结合身份摘要、实时状态接口或本地身份快照新鲜度判断用户 / 租户 / 角色状态。

知识库不得：

- 保存 AI 基座密码、密码 hash、盐值。
- 保存 AI 基座长期登录 token、refresh token 或 JWT 原文。
- 把 AI 基座身份摘要当成可写用户主数据。
- 在前端 URL 或本地存储中保存完整身份资料或角色列表。

### 4.1 知识库本地 session 规则

无论采用 code 模式还是 JWT 模式，知识库都必须创建自身短期后台 session。该 session 是知识库控制台和后台 API 的唯一生产登录态。

建议 session 保存字段：

| 字段 | 说明 |
| --- | --- |
| `session_id` | 知识库本地会话 ID |
| `tenant_id` | AI 基座租户 ID |
| `user_id` | AI 基座用户 ID |
| `role_codes` | 当前有效角色编码，最少包含管理员判断所需角色 |
| `is_tenant_admin` | 是否命中第一期租户管理员规则 |
| `identity_snapshot_version` | 登录时使用的身份快照版本 |
| `auth_source` | `ai_base_sso_code` 或 `ai_base_jwt` |
| `credential_fingerprint` | `sso_code` hash ID、JWT `jti` 或 JWT hash 指纹，不保存原文 |
| `issued_at` | session 签发时间 |
| `expires_at` | session 过期时间 |
| `last_seen_at` | 最近访问时间 |

session 要求：

- 建议使用 `HttpOnly`、`Secure`、`SameSite=Lax` 或更严格的 cookie。
- 默认短有效期，例如 2-8 小时；敏感操作可要求重新校验。
- session 续期不得延长 AI 基座已失效用户的访问。
- 默认“退出知识库”只清理知识库本地 session，不清理 AI 基座租户端登录态；退出后跳转到知识库 `/login?logged_out=1`，页面只提供“使用 AI 基座重新登录”入口。
- 如业务需要“退出 AI 基座账号 / 退出全部系统”，必须作为独立操作接入 AI 基座统一 logout endpoint，不得混同为知识库默认退出。
- 知识库登录页不得提供自有账号密码登录；正式入口统一走 AI 基座 SSO，本地身份快照入口仅作为开发 / 联调兜底。
- 开发联调用的 `X-KB-Tenant-Id` / `X-KB-User-Id` 只能作为非生产兼容路径，生产默认关闭。

### 4.2 JWT 与 OpenAPI Bearer 的边界

AI 基座 JWT 只用于后台登录 / SSO 身份，不用于 `/openapi/v1/*` 外部系统调用。

OpenAPI / API Key 是知识库面向多类外部调用方的通用开放能力，不能绑定死为 AI 基座专用能力。AI 基座可以作为一个外部调用方使用 OpenAPI，但后续还需要支持教务系统、内容平台、第三方应用、客户自有业务系统等其他调用方。因此 API Key、`app_id`、能力范围、IP 白名单、quota 和审计字段都应按“调用方应用 / 外部系统”建模，而不是按“AI 基座 JWT 用户身份”建模。

当前 OpenAPI 使用 `Authorization: Bearer <api_key>` 或 `X-API-Key` 作为 API Key 凭证。该 Bearer 与 AI 基座 JWT 必须分开：

- `/api/auth/ai-base/*` 或 SSO callback 消费 AI 基座 JWT / code。
- `/openapi/v1/*` 消费知识库 API Key 或后续 HMAC 强签名，调用方可以是 AI 基座，也可以是其他第三方系统。
- 控制台后续 API 消费知识库本地 session。
- 不允许 `/openapi/v1/*` 在未设计用户委托模型前接受 AI 基座 JWT。
- 不允许把 API Key 的表结构、错误码、签名字段、quota 字段命名成 AI 基座专用概念；应使用 `app_id`、`client_id`、`caller_id`、`api_key_id`、`tenant_id`、`kb_id`、`capability` 等通用字段。

## 5. 安全约束

### 5.1 redirect_uri 白名单

AI 基座必须为知识库应用配置可信回调地址白名单。

示例：

```text
https://kb.example.com/auth/ai-base/callback
https://kb-staging.example.com/auth/ai-base/callback
```

禁止使用任意 `redirect_uri`，避免开放重定向。

### 5.2 state 校验

RAG `/api/auth/ai-base/launch` 发起跳转前生成随机 `state`，写入 RAG HttpOnly cookie，callback 时校验 query 中的 `state` 与 cookie 内保存的 `state` 一致。`state` 应与知识库侧登录尝试绑定，防止 CSRF 和登录串号。

`state` 不是业务页面名，也不是外部系统约定的固定值。联调时出现的 `state=knowledge-base-home` 只能视为一次临时随机样例，不能写入环境变量、白名单映射或后端固定配置。

### 5.3 code 防重放

`sso_code` 必须：

- 短有效期。
- 一次性使用。
- 服务端保存 hash。
- 成功换取身份摘要后立即置为已使用。
- 过期、已使用或不存在时拒绝。

### 5.4 服务端到服务端鉴权

`/internal/sso/exchange` 至少使用 `client_secret` 鉴权。生产建议使用 HMAC 签名或 mTLS。

签名模式可参考：

```text
X-Client-Id: wisewe-kb
X-Timestamp: 2026-06-18T12:00:00+08:00
X-Nonce: random_nonce
X-Signature: hmac_sha256(client_secret, method + path + timestamp + nonce + body_hash)
```

### 5.5 状态拒绝规则

以下场景必须拒绝：

- 用户禁用。
- 用户离职。
- 用户移出租户。
- 租户停用、冻结、删除或状态不明确。
- 角色状态停用。
- `sso_code` 过期、已使用或与 `redirect_uri` 不匹配。
- JWT 过期、尚未生效、签名无效、`iss` 不可信、`aud` 不匹配、`kid` 不存在、`jti` 已重放或已吊销。

### 5.6 JWT 校验规则

JWT 兼容模式必须执行以下校验：

| 校验项 | 要求 |
| --- | --- |
| `alg` | 必须在白名单内，例如 `RS256` / `ES256`；禁止 `none` |
| `kid` | 必须存在，并能在 AI 基座 JWKS 中找到 |
| 签名 | 必须使用 `kid` 对应公钥校验通过 |
| `iss` | 必须等于 AI 基座约定 issuer |
| `aud` | 必须包含知识库应用 ID，例如 `wisewe-kb` |
| `exp` | 必须未过期 |
| `nbf` | 必须已生效 |
| `iat` | 不得晚于当前时间太多，且不得超过允许最大 token 年龄 |
| `jti` | JWT 用作一次性交换凭证时必须存在，并校验未重放 |
| `sub` | 必须存在，并映射为 AI 基座用户 ID |
| `tenant_id` | 必须存在，并映射为 AI 基座租户 ID |

时间校验允许少量时钟偏差，建议 30-60 秒。超过偏差窗口必须拒绝。

JWT 校验通过后，还必须执行用户 / 租户 / 角色状态校验。状态来源可以是：

1. AI 基座 exchange / introspection 返回的实时身份摘要。
2. 登录时兜底刷新后的身份快照。
3. 新鲜度满足要求的本地身份快照。

如果本地身份快照超过允许新鲜度窗口，知识库应强制刷新；刷新失败时按安全策略拒绝或降级，并写入审计。生产建议默认拒绝后台登录。

### 5.7 JWT 传递与存储限制

- 禁止在 URL query、fragment、Referer 可见位置传递 JWT。
- 禁止把 AI 基座 JWT 存入 `localStorage`、`sessionStorage` 或 IndexedDB 作为长期登录态。
- 禁止在日志、异常、埋点、导出文件、浏览器控制台中输出 JWT 原文。
- 如必须记录关联信息，只允许记录 `jti`、`kid`、issuer、audience、subject、租户 ID、签名 key 版本、JWT hash 指纹和错误码。
- JWT 只允许在交换请求的瞬时内存中存在；交换成功后立即丢弃原文。

## 6. 建议错误码

| 错误码 | 含义 |
| --- | --- |
| `INVALID_CODE` | code 不存在或格式非法 |
| `CODE_EXPIRED` | code 已过期 |
| `CODE_REPLAYED` | code 已被使用 |
| `REDIRECT_URI_MISMATCH` | redirect_uri 与生成 code 时不一致 |
| `CLIENT_NOT_ALLOWED` | client_id 无效或未授权 |
| `CLIENT_AUTH_FAILED` | client_secret、HMAC 或 mTLS 校验失败 |
| `USER_DISABLED` | 用户禁用、离职或移出租户 |
| `TENANT_DISABLED` | 租户停用、冻结或删除 |
| `TENANT_UNAVAILABLE` | 租户状态不明确或不可用 |
| `ROLE_UNAVAILABLE` | 角色状态不可用 |
| `SIGNATURE_INVALID` | 服务端调用签名无效 |
| `RATE_LIMITED` | SSO 接口调用过快 |
| `JWT_INVALID` | JWT 格式、签名或必要声明非法 |
| `JWT_EXPIRED` | JWT 已过期 |
| `JWT_NOT_ACTIVE` | JWT 尚未生效 |
| `JWT_AUDIENCE_MISMATCH` | JWT audience 不包含知识库应用 |
| `JWT_ISSUER_MISMATCH` | JWT issuer 非可信 AI 基座 |
| `JWT_KEY_NOT_FOUND` | JWT header 中的 `kid` 找不到可用公钥 |
| `JWT_ALGORITHM_NOT_ALLOWED` | JWT 使用了未允许的签名算法 |
| `JWT_REVOKED` | JWT 已被吊销 |
| `JWT_REPLAYED` | 一次性交换 JWT 的 `jti` 被重复使用 |
| `IDENTITY_SNAPSHOT_STALE` | 身份快照超过允许新鲜度且刷新失败 |

## 7. 日志与审计

AI 基座建议记录：

- `code` 生成事件：clientId、tenantId、userId、redirectUri、expiresAt、requestId。
- `code` 换取事件：clientId、tenantId、userId、成功 / 失败、失败错误码、requestId。
- JWT 交换事件：clientId、tenantId、userId、`iss`、`aud`、`sub`、`kid`、`jti`、成功 / 失败、失败错误码、requestId。
- 重放、过期、redirect_uri 不匹配、客户端鉴权失败等安全事件。

日志不得记录：

- `sso_code` 明文。
- `client_secret`。
- AI 基座登录 token、JWT 原文或 refresh token。
- 用户密码、密码 hash、盐值。

知识库侧建议记录：

- SSO callback 收到时间、state 校验结果、凭证模式、requestId。
- 身份交换成功 / 失败、失败错误码、tenantId、userId、auth_source。
- JWT 模式下记录 `kid`、`jti`、issuer、audience、token hash 指纹，不记录 JWT 原文。
- 本地 session 创建、续期、过期、退出、强制失效。
- 身份快照刷新结果、快照版本、快照年龄、是否 stale。

## 8. 最小联调清单

- 正常用户从 AI 基座进入知识库成功。
- `state` 不匹配时知识库拒绝。
- `redirect_uri` 不在白名单时 AI 基座拒绝。
- `sso_code` 第二次使用返回 `CODE_REPLAYED`。
- `sso_code` 过期返回 `CODE_EXPIRED`。
- JWT 正常交换成功并创建知识库 session。
- JWT 过期返回 `JWT_EXPIRED`。
- JWT `aud` 不匹配返回 `JWT_AUDIENCE_MISMATCH`。
- JWT `iss` 不可信返回 `JWT_ISSUER_MISMATCH`。
- JWT 签名 key 轮换期间，新旧 `kid` 均按预期处理。
- JWT `jti` 第二次交换返回 `JWT_REPLAYED`。
- JWT 签名算法不在白名单时返回 `JWT_ALGORITHM_NOT_ALLOWED`。
- 用户禁用后返回 `USER_DISABLED`。
- 租户停用后返回 `TENANT_DISABLED` 或 `TENANT_UNAVAILABLE`。
- 用户禁用但 JWT 未过期时，知识库仍拒绝登录或拒绝续期。
- 身份快照过期且刷新失败时，生产策略下拒绝登录并返回 `IDENTITY_SNAPSHOT_STALE`。
- `role_code=superManager` 用户进入知识库后被识别为本租户管理员。
- 非 `superManager` 用户只获得普通用户身份。
- 点击“退出知识库”后，`kb_session` 被清理，访问控制台重新进入 `/login?logged_out=1`，AI 基座租户端登录态不受影响。
- 如接入“退出 AI 基座账号”，该入口必须跳转 AI 基座统一 logout，并最终回到 AI 基座登录页或租户门户页。

## 9. 与知识库治理 BRD 的关系

本文档是 `knowledge-base-governance-brd.md` 中“AI 基座身份接入与访问判断”的接口化帮助说明。BRD 是业务边界事实源；本文档用于 AI 基座租户端和知识库系统进行 SSO 联调与接口评审。

## 10. 当前实现状态

2026-06-18 已按“SSO 延后、先跑通身份快照”的方式完成最小同步链路：

- 新增知识库本地只读快照表 `kb_identity_tenants`、`kb_identity_users`、`kb_identity_roles`、`kb_identity_user_roles`。
- 新增 `scripts/sync_ai_base_identity.py`，从 AI 基座 MySQL 只同步 `system_tenant`、`sys_user`、`sys_role`、`sys_user_role`。
- 同步脚本默认限制 `1-5` 条配套样本，手机号和邮箱写入前脱敏，不查询或保存 `sys_user.password`。
- 已从 `192.168.2.212:3306` 的 `tellyes_ai` schema 同步样本到本地 PostgreSQL：1 个租户、4 个用户、1 个角色、4 条用户角色关系。

2026-06-22 RAG 侧已补齐正式 SSO 最小闭环：

- 新增 `/api/auth/ai-base/launch`，生成知识库侧 `state` 并跳转 AI 基座 SSO launch。
- 新增 `/api/auth/ai-base/callback`，用 `sso_code` 调 AI 基座 `/internal/sso/exchange`，校验 state 后创建知识库本地 session。
- 新增 `/api/auth/ai-base/exchange`，支持 AI 基座短期 JWT 一次性交换兼容模式；JWT 原文不写入本地存储。
- 新增 `/api/auth/session` 和 `/api/auth/logout`，控制台通过 HttpOnly `kb_session` cookie 读取和清理知识库本地 session。
- 控制台默认退出语义为“退出知识库”：仅调用 `/api/auth/logout` 清理知识库本地 session 并进入 `/login?logged_out=1`；不默认触发 AI 基座全局退出。
- 新增 `kb_auth_sessions` 和 `kb_sso_used_credentials`，分别保存本地短 session 与一次性 SSO 凭证指纹，防止 code / JWT 重放。
- 成功 exchange 后会把 AI 基座返回的租户 / 用户 / 角色 / 用户角色关系摘要 upsert 到本地只读身份快照，再按 `role_code=superManager` 解析租户管理员身份。
- 旧 `X-KB-Tenant-Id` / `X-KB-User-Id` 只作为开发 / 联调兜底保留；正式控制台 API 优先使用 `kb_session` cookie。

2026-06-24 结合真实联调入口再次收口：

- AI 基座页面上的“进入知识库”入口应把浏览器导向 RAG `/api/auth/ai-base/launch`；RAG launch 生成随机 `state`、写 cookie 后，再 302 到 AI 基座 `http://192.168.2.169:8090/sso?...&state=...`。
- `AI_BASE_SSO_LAUNCH_BASE_URL` / `AI_BASE_SSO_LAUNCH_PATH` 只表示 RAG launch 跳向的 AI 基座浏览器 SSO 端点，例如 `http://192.168.2.169:8090` + `/sso`；`AI_BASE_SSO_BASE_URL` 继续表示 RAG 后端调用 exchange / snapshot / delta 的服务端内部接口根地址。
- 当前 `.env` 已配置 AI 基座服务端内部接口根地址、浏览器 SSO 根地址、`rag-client`、后端 8001 callback、控制台 base URL、4 小时本地 session TTL、后端 8001 端口和允许携带 cookie 的控制台 CORS origin。
- 租户端 `/sso` 接口要求带 `client_id`、`redirect_uri`、`state`；RAG launch 已带 `client_id`，且 `state` 由 RAG 生成并在 callback 校验。
- 租户端 exchange 成功响应使用 `Result<>` 包装，业务身份摘要位于 `data`；RAG exchange 已支持 `Result.data` 解包和 `success/code/msg` 错误映射。
- RAG 已新增当前用户身份刷新和 HTTP delta 同步入口，按服务端配置调用 `/ai/system/internal/identity/snapshot/users/{userId}?tenant_id=` 与 `/ai/system/internal/identity/snapshot/delta?last_sync_at=...`，首次同步 `last_sync_at` 传空，后续使用最近一次成功的 `max_updated_at`，水位统一格式化为 `YYYY-MM-DD HH:mm:ss`，保存 `max_updated_at` / `snapshot_version` 并处理 `deleted` 数组。
- 租户端当前阶段明确“不做 JWT 对接模式”；知识库已有 JWT exchange 兼容入口可保留为后续可选能力，但本轮生产联调主路径应收敛到 `sso_code`。

当前仍未实现或未完成对齐：HTTP delta 定时任务编排、身份快照新鲜度强制窗口、生产环境请求头兜底收口、完整访问拒绝审计、AI 基座单点退出回调；若未来重新启用 JWT 兼容模式，再补 RAG 侧直接 JWKS 本地验签 fallback。
