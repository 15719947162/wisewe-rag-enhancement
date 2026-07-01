# AI 基座 SSO 知识库对接文档

## 1. 文档目的

本文档用于指导知识库系统对接当前 AI 基座已经实现的 SSO 能力。

本文档以当前代码实际实现为准，覆盖以下能力：

- 浏览器从 RAG launch 跳转到 AI 基座 SSO，再由 AI 基座回调知识库
- 知识库后端使用一次性 `sso_code` 换取身份摘要
- 知识库按当前用户刷新身份快照
- 知识库定时拉取租户、用户、角色、用户角色关系增量

本文档不包含知识库本地 session 的具体代码实现，但会明确知识库侧必须完成的处理规则。

## 2. 对接范围

当前 AI 基座已经提供以下接口：

- `GET /sso`
- `POST /ai/system/internal/sso/exchange`
- `GET /ai/system/internal/identity/snapshot/users/{userId}?tenant_id={tenantId}`
- `GET /ai/system/internal/identity/snapshot/delta?last_sync_at={lastSyncAt}`

当前阶段采用一次性 `sso_code` 模式，不做 JWT 对接模式。

## 3. 前置准备

知识库对接前，需要先在 AI 基座配置一个知识库专用客户端，复用 `system_oauth2_client` 表。

客户端至少需要满足：

- `clientId`：知识库客户端标识
- `secret`：知识库后端调用 AI 基座内部接口使用的密钥
- `redirectUris`：包含知识库回调地址
- `authorizedGrantTypes`：必须包含 `ai_base_sso_code`
- `status`：启用

注意：

- `authorizedGrantTypes` 配置值是 `ai_base_sso_code`
- 但 `/exchange` 请求体里的 `grant_type` 当前实现固定传 `authorization_code`
- 如果客户端配置了 `ipWhitelist`，需要放行“知识库后端服务”的出口 IP
- `client_secret` 只能保存在知识库后端，不能下发到浏览器或前端代码

## 4. 总体流程

### 4.1 登录主流程

```text
用户已登录 AI 基座
  -> 点击进入知识库
  -> 浏览器先跳转到 RAG /api/auth/ai-base/launch
  -> RAG 生成随机 state，写入 RAG HttpOnly cookie
  -> RAG 302 到 AI 基座 /sso?client_id=...&redirect_uri=...&state=...
  -> AI 基座校验当前登录态、客户端、redirect_uri、用户和租户状态
  -> AI 基座生成一次性 sso_code
  -> AI 基座 302 回 RAG callback，并原样带上 code/state
  -> RAG 校验 query state 与 RAG cookie 中的 state 一致
  -> 知识库后端调用 /ai/system/internal/sso/exchange
  -> AI 基座返回身份摘要
  -> 知识库落本地身份快照并创建自己的本地 session
  -> 用户进入知识库控制台
```

### 4.2 增量同步流程

```text
知识库后台定时任务
  -> 读取本地上次成功处理的 last_sync_at
  -> 调用 /ai/system/internal/identity/snapshot/delta
  -> 处理 tenants/users/roles/user_roles/deleted
  -> 记录本次返回的 max_updated_at
  -> 下一次继续使用 max_updated_at 作为 last_sync_at
```

## 5. 接口契约

### 5.1 跳转入口

```http
GET /sso?client_id={clientId}&redirect_uri={redirectUri}&state={state}
```

说明：

- 该接口由浏览器访问，但应由 RAG `/api/auth/ai-base/launch` 302 跳转过来
- 用户必须已经登录 AI 基座
- `state` 必须由 RAG launch 生成并写入 RAG HttpOnly cookie，用于 callback 校验
- 成功时返回 `302`

AI 基座页面上的“进入知识库”按钮也应直接把浏览器导向 RAG `/api/auth/ai-base/launch`。AI 基座作为入口页没有问题，但不应由 AI 基座后端先向 RAG 预取 `state`，也不应把 `state` 固定为业务页面名。

成功后的跳转示例：

```text
https://kb.example.com/api/auth/ai-base/callback?code=8d6...&state=trace-123
```

处理规则：

- 校验当前 AI 基座登录态
- 校验客户端具备 `ai_base_sso_code` 能力
- 校验 `redirect_uri` 必须在客户端白名单中
- 原样透传 RAG 传入的 `state`
- 校验当前租户、用户、角色状态
- 生成一次性 `sso_code`

当前实现约束：

- `sso_code` 有效期为 90 秒
- `sso_code` 只允许使用一次
- Redis 中只保存 `code` 的 hash 关联记录，不保存明文

### 5.2 服务端换取身份摘要

```http
POST /ai/system/internal/sso/exchange
Content-Type: application/json
```

请求体：

```json
{
  "client_id": "kb-client",
  "client_secret": "kb-secret",
  "grant_type": "authorization_code",
  "code": "8d6b7d9d...",
  "redirect_uri": "https://kb.example.com/sso/callback"
}
```

字段说明：

- `client_id`：知识库客户端标识
- `client_secret`：知识库后端密钥
- `grant_type`：当前必须传 `authorization_code`
- `code`：AI 基座刚签发的一次性 `sso_code`
- `redirect_uri`：必须与 `launch` 时传入的一致

返回体示例：

```json
{
  "success": true,
  "code": 200,
  "msg": "操作成功",
  "data": {
    "tenant": {
      "tenant_id": "1",
      "tenant_name": "示例租户",
      "status": "active"
    },
    "user": {
      "user_id": "1001",
      "username": "zhangsan",
      "display_name": "张三",
      "mobile_masked": "138****0000",
      "email_masked": "z***@example.com",
      "status": "active"
    },
    "roles": [
      {
        "role_id": "2001",
        "tenant_id": "1",
        "role_code": "superManager",
        "role_name": "超级管理员",
        "status": "active",
        "updated_at": "2026-06-22T10:02:00+08:00"
      }
    ],
    "user_roles": [
      {
        "user_id": "1001",
        "role_id": "2001",
        "tenant_id": "1",
        "status": "active",
        "updated_at": "2026-06-22T10:03:00+08:00"
      }
    ],
    "snapshot_version": "20260622100500",
    "issued_at": "2026-06-22T10:05:00+08:00",
    "expires_in": 300
  },
  "timestamp": 1782093900000
}
```

重要说明：

- 返回的是 `Result` 包装，业务数据位于 `data`
- `tenant_id`、`user_id`、`role_id` 当前实现都是“数字转字符串”
- 手机号、邮箱只返回脱敏值
- `snapshot_version` 当前格式是 `yyyyMMddHHmmss`

### 5.3 当前用户身份刷新

```http
GET /ai/system/internal/identity/snapshot/users/{userId}?tenant_id={tenantId}
X-Client-Id: kb-client
X-Client-Secret: kb-secret
```

示例：

```http
GET /ai/system/internal/identity/snapshot/users/1001?tenant_id=1
X-Client-Id: kb-client
X-Client-Secret: kb-secret
```

返回体结构与 `/ai/system/internal/sso/exchange` 完全一致。

建议用法：

- 登录成功后，如知识库侧有统一“当前用户刷新”流程，可直接调用该接口覆盖本地快照
- 一期最小闭环下，也可以直接使用 `/exchange` 返回体完成登录

### 5.4 增量同步接口

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at={lastSyncAt}
X-Client-Id: kb-client
X-Client-Secret: kb-secret
```

参数说明：

- `last_sync_at`：可空，首次同步传空字符串；后续传上次成功处理后的 `max_updated_at`，格式统一为 `YYYY-MM-DD HH:mm:ss`

返回体示例：

```json
{
  "success": true,
  "code": 200,
  "msg": "操作成功",
  "data": {
    "snapshot_version": "20260622101500",
    "generated_at": "2026-06-22T10:15:00+08:00",
    "max_updated_at": "2026-06-22 10:10:00",
    "tenants": [
      {
        "tenant_id": "1",
        "tenant_name": "示例租户",
        "status": "active",
        "updated_at": "2026-06-22T10:00:00+08:00"
      }
    ],
    "users": [
      {
        "user_id": "1001",
        "tenant_id": "1",
        "username": "zhangsan",
        "display_name": "张三",
        "mobile_masked": "138****0000",
        "email_masked": "z***@example.com",
        "status": "active",
        "updated_at": "2026-06-22T10:01:00+08:00"
      }
    ],
    "roles": [
      {
        "role_id": "2001",
        "tenant_id": "1",
        "role_code": "superManager",
        "role_name": "超级管理员",
        "status": "active",
        "updated_at": "2026-06-22T10:02:00+08:00"
      }
    ],
    "user_roles": [
      {
        "user_id": "1001",
        "role_id": "2001",
        "tenant_id": "1",
        "status": "active",
        "updated_at": "2026-06-22T10:03:00+08:00"
      }
    ],
    "deleted": [
      {
        "entity_type": "user_role",
        "entity_id": "3001",
        "tenant_id": "1",
        "updated_at": "2026-06-22T10:04:00+08:00"
      }
    ]
  },
  "timestamp": 1782094500000
}
```

当前实现说明：

- `last_sync_at` 使用时间水位，首次可空
- 查询语义是 `updated_at >= last_sync_at`
- `max_updated_at` 可直接作为下一次同步的 `last_sync_at`
- `last_sync_at` 与 `max_updated_at` 的对接水位格式统一为 `YYYY-MM-DD HH:mm:ss`；RAG 侧会把 ISO_OFFSET_DATE_TIME 兼容转换为该格式后再请求和落库
- `generated_at` 与各对象的 `updated_at` 可继续使用 ISO_OFFSET_DATE_TIME 格式
- `deleted` 当前是“数组”，不是按 `tenant_ids/user_ids/role_ids` 分组的对象
- `deleted.entity_type` 当前可能取值：`tenant`、`user`、`role`、`user_role`

## 6. 知识库侧处理规则

### 6.1 登录换取成功后的处理

知识库收到身份摘要后，应立即执行：

1. upsert 本地租户快照
2. upsert 本地用户快照
3. upsert 本地角色快照
4. upsert 本地用户角色关系快照
5. 基于身份摘要创建知识库自己的本地 session

知识库本地 session 至少应包含：

- `tenant_id`
- `user_id`
- `role_codes`
- `identity_snapshot_version`
- `auth_source=ai_base_sso_code`

禁止事项：

- 不保存 AI 基座密码
- 不保存 `client_secret`
- 不保存 `sso_code` 明文
- 不把 AI 基座身份摘要当可写主数据

### 6.2 管理员判断规则

当前阶段统一按以下规则处理：

- 命中 `role_code=superManager`，可识别为租户管理员
- 其他平台级管理员逻辑，由知识库自己单独配置，不从 `superManager` 自动推断

### 6.3 增量同步处理规则

知识库处理 `delta` 时建议按以下顺序：

1. 处理 `tenants`
2. 处理 `users`
3. 处理 `roles`
4. 处理 `user_roles`
5. 再处理 `deleted`
6. 整批事务成功后，记录 `max_updated_at`

状态字段当前统一按以下值处理：

- `active`
- `disabled`
- `deleted`
- `unknown`

建议规则：

- 主数组中的 `status` 是主依据
- `deleted` 作为补充删除事件处理
- 尤其是 `user_roles`，建议优先按 `user_id + role_id + tenant_id` 这组关系做幂等 upsert
- 如果主数组里 `user_role.status=deleted`，应删除或停用本地关系

## 7. 错误码

当前对接最常见的错误码如下：

| 错误码 | 含义 |
| --- | --- |
| `SSO_CLIENT_NOT_ALLOWED` | 客户端不存在、未启用、未配置 `ai_base_sso_code`，或 IP 白名单不匹配 |
| `SSO_CLIENT_AUTH_FAILED` | `client_secret` 校验失败 |
| `SSO_REDIRECT_URI_MISMATCH` | `redirect_uri` 与客户端白名单或 code 绑定值不匹配 |
| `SSO_CODE_INVALID` | `code` 格式非法或内容不可解析 |
| `SSO_CODE_EXPIRED` | `code` 已过期 |
| `SSO_CODE_REPLAYED` | `code` 已被使用 |
| `SSO_USER_DISABLED` | 用户不可用 |
| `SSO_TENANT_DISABLED` | 租户已停用 |
| `SSO_TENANT_UNAVAILABLE` | 租户不存在或状态不可用 |
| `SSO_ROLE_UNAVAILABLE` | 角色不存在、已删除或状态不可用 |
| `INVALID_PARAMETER` | 例如 `/exchange` 的 `grant_type` 不是 `authorization_code` |

## 8. 联调检查清单

知识库联调时建议至少验证以下场景：

1. 正常登录时，浏览器能从 `launch` 成功跳回知识库 callback
2. callback 中 `state` 不匹配时，知识库拒绝登录
3. `/exchange` 成功返回身份摘要并创建知识库本地 session
4. 同一个 `code` 第二次调用 `/exchange` 返回 `SSO_CODE_REPLAYED`
5. 过期 `code` 调用 `/exchange` 返回 `SSO_CODE_EXPIRED`
6. `redirect_uri` 不一致时返回 `SSO_REDIRECT_URI_MISMATCH`
7. 用户禁用时，`launch` 或 `/exchange` 返回用户不可用错误
8. 租户停用时，`launch` 或 `/exchange` 返回租户不可用错误
9. `delta` 首次空游标能拉到全量数据
10. `delta` 使用上一次 `max_updated_at` 能继续拉增量
11. `delta` 中 `status=deleted` 的数据能正确反映到知识库本地快照
12. `superManager` 角色能被知识库识别为租户管理员

## 9. 当前实现注意事项

为了避免对接方按旧设计稿理解接口，以下几点请按当前实现处理：

- `/exchange` 的 `grant_type` 当前必须传 `authorization_code`
- 客户端授权类型配置仍然必须是 `ai_base_sso_code`
- 内部数据接口成功响应统一走 `Result<>`
- `deleted` 当前返回数组，不是分组对象
- ID 字段当前是字符串化后的数字，不是类似 `u_001`、`t_001` 这样的编码
- `launch` 仍然保持 302 跳转，不使用 `Result<>` 包装
