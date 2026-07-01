# AI 基座身份快照增量同步对接说明

本文档面向 AI 基座租户端开发，说明 WiseWe RAG 知识库需要 AI 基座提供哪些身份与权限数据同步能力，以及双方如何通过“受控增量同步（仅 AI 基座 SSO 的 `superManager` 可触发）+ 登录时兜底刷新”保证知识库后台访问判断及时、稳定、可审计。

本文只描述对接契约，不代表当前知识库代码已经完成全部生产治理闭环。当前 RAG 侧已具备正式 SSO session、当前用户刷新、HTTP delta 拉取、`last_sync_at/max_updated_at` 水位记录、用户及权限同步日志和 SSO `superManager` 受控触发入口；真实 SSO `superManager` 触发后的 `http_delta` 全量同步已完成复核，并成功入库 52 个租户、69278 个用户、227 个角色和 70755 条用户角色关系。当前仍需补齐失败重试、访问拒绝审计全覆盖、生产请求头兜底关闭验收和历史知识库归属迁移检查。

## 1. 背景与目标

AI 基座是租户、用户、角色和用户角色关系的事实源。知识库系统不是 IAM，不维护可写用户、角色、权限策略，也不保存 AI 基座密码、密码 hash、盐值、长期登录 token 或 JWT 原文。

知识库需要保存一份只读身份快照，用于：

1. 后台展示当前操作者、租户和角色摘要。
2. 服务端执行租户隔离。
3. 判断当前用户是否是租户管理员 / 知识库管理员。
4. 在 AI 基座短时不可用时，允许低风险读取按有限旧快照窗口降级。
5. 在日志和审计中记录 `tenant_id`、`user_id`、`identity_snapshot_version` 等追溯字段。

同步目标不是复制 AI 基座权限系统，而是把知识库访问判断需要的最小身份事实同步到本地。

## 2. 总体方案

推荐采用两个同步通道：

| 通道 | 触发方式 | 作用 | 要求 |
| --- | --- | --- | --- |
| 受控增量同步 | 由 AI 基座 SSO 登录且 `roleCodes` 包含 `superManager` 的当前身份触发；后台调度无当前授权上下文时只记录 `skipped` | 持续更新全量租户、用户、角色、用户角色关系变更 | 使用 `last_sync_at` 请求水位和 `max_updated_at` 响应水位；首次同步 `last_sync_at` 传空字符串，后续使用最近一次成功运行的 `max_updated_at` |
| 登录时兜底刷新 | 用户通过 SSO / JWT exchange 登录知识库时触发 | 确保当前登录用户的身份和角色不是旧数据 | 支持按 `tenant_id + user_id` 查询当前身份摘要 |

推荐流程：

```text
用户已登录 AI 基座
  -> 进入知识库
  -> 知识库通过 sso_code 或 JWT 一次性交换获得身份摘要
  -> 知识库按 tenant_id + user_id 做登录时兜底刷新
  -> 知识库更新当前用户相关身份快照
  -> 知识库创建自身短期 session
  -> 后续控制台 API 使用知识库 session + 本地身份快照做访问判断

受控增量同步：
  -> 校验当前知识库 session 来自 AI 基座 SSO，且 roleCodes 包含 superManager
  -> 读取最近一次成功运行的 max_updated_at
  -> 首次同步时 last_sync_at 为空字符串
  -> 调 AI 基座 delta 接口拉取变更
  -> upsert 本地只读快照并处理删除 / 停用 / 移除关系
  -> 全部处理成功后记录本轮 max_updated_at
  -> has_more=true 时继续下一页
```

## 3. `last_sync_at` 与 `max_updated_at` 是什么

`last_sync_at` 是知识库请求 AI 基座 delta 接口时携带的“上次成功同步到哪里”的时间水位；`max_updated_at` 是 AI 基座在响应中返回的本批数据最大更新时间水位。知识库只有在本轮租户、用户、角色、用户角色关系和删除事件都成功落库后，才会把本轮 `max_updated_at` 作为下一次请求的 `last_sync_at`。

水位格式统一使用标准格式：

```text
YYYY-MM-DD HH:mm:ss
```

首次同步时，知识库没有成功水位，必须请求：

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at=
```

后续同步时，知识库使用最近一次成功运行记录中的 `max_updated_at`：

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at=2026-06-22%2010:30:00
```

说明：`last_sync_at/max_updated_at` 只用于服务端增量同步定位，不是用户身份、不是权限、不是登录 token，也不代表某个租户或用户的授权结果。知识库不能根据水位判断用户能否访问资源，访问判断仍必须基于知识库 session、本地身份快照和知识库业务归属规则。

例如：

```text
10:00 用户 A 新增
10:05 用户 B 禁用
10:08 用户 C 增加 superManager 角色
10:12 租户 D 停用
```

如果知识库最近一次成功同步记录保存：

```text
max_updated_at = 2026-06-22 10:05:00
```

下次请求：

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at=2026-06-22%2010:05:00
```

AI 基座应返回该时间之后的变更，并在响应里返回本批变更的 `max_updated_at`。知识库处理成功后把本地成功运行记录写入 `kb_identity_sync_runs`，下次自动取该 `max_updated_at`。如果同一秒内存在多条变更，AI 基座需要保证 `last_sync_at` 边界不会漏数据；知识库侧会通过主键幂等 upsert 降低重复返回的副作用。

水位约定：

| 类型 | 示例 | 说明 |
| --- | --- | --- |
| 请求水位 | `last_sync_at=2026-06-22 10:12:00` | 知识库发给 AI 基座，首次为空字符串 |
| 响应水位 | `"max_updated_at": "2026-06-22 10:30:00"` | AI 基座返回本批数据最大更新时间，知识库成功落库后保存 |
| 单条更新时间 | `updated_at=2026-06-22 10:20:00` | 租户、用户、角色、用户角色关系新增、修改、停用、删除时必须更新 |
| 增强排序键 | `change_id` / 全局版本号 | 可作为未来增强项，但当前正式合同主路径仍是 `last_sync_at/max_updated_at` |

不建议使用客户端自己拼接的不稳定分页页码，例如 `page=3`。增量同步需要在数据持续变化时仍能稳定恢复和重试。

如果 AI 基座只能提供秒级时间水位，建议服务端在 `last_sync_at` 边界采用“包含边界并稳定排序”或内部回看窗口，知识库侧通过租户、用户、角色、用户角色关系的主键幂等 upsert 去重。这样可以降低时间精度、并发提交、事务延迟或服务端时钟偏差导致漏同步的风险。

## 4. AI 基座需提供的接口

### 4.1 增量同步接口

```http
GET /ai/system/internal/identity/snapshot/delta?last_sync_at={lastSyncAt}
Authorization: Bearer <server_to_server_token>
```

这里的 `server_to_server_token` 是 AI 基座分配给知识库后端的服务端调用凭证，只允许后端保存和使用。它不等同于用户 JWT、SSO code、知识库 API Key 或浏览器 session，不能暴露给前端。

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `last_sync_at` | 首次可空字符串 | 上次成功同步后的最大更新时间水位，格式为 `YYYY-MM-DD HH:mm:ss`。首次同步传空字符串 |

响应示例：

```json
{
  "last_sync_at": "2026-06-22 10:00:00",
  "max_updated_at": "2026-06-22 10:30:00",
  "has_more": false,
  "snapshot_version": "identity_snapshot_20260622103000",
  "generated_at": "2026-06-22T10:30:00+08:00",
  "tenants": [
    {
      "tenant_id": "t_001",
      "tenant_name": "示例租户",
      "tenant_code": "demo",
      "status": "active",
      "changed_at": "2026-06-22T10:20:00+08:00",
      "updated_at": "2026-06-22T10:20:00+08:00"
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
      "changed_at": "2026-06-22T10:21:00+08:00",
      "updated_at": "2026-06-22T10:21:00+08:00"
    }
  ],
  "roles": [
    {
      "role_id": "r_001",
      "tenant_id": "t_001",
      "role_code": "superManager",
      "role_name": "超级管理员",
      "status": "active",
      "changed_at": "2026-06-22T10:22:00+08:00",
      "updated_at": "2026-06-22T10:22:00+08:00"
    }
  ],
  "user_roles": [
    {
      "relation_id": "ur_001",
      "tenant_id": "t_001",
      "user_id": "u_001",
      "role_id": "r_001",
      "status": "active",
      "changed_at": "2026-06-22T10:23:00+08:00",
      "updated_at": "2026-06-22T10:23:00+08:00"
    }
  ],
  "deleted": {
    "tenant_ids": [],
    "user_ids": [],
    "role_ids": [],
    "user_role_relation_ids": ["ur_099"]
  }
}
```

### 4.2 当前用户身份刷新接口

登录时兜底刷新建议提供一个按当前登录用户查询的接口。

```http
GET /internal/identity/snapshot/users/{user_id}?tenant_id={tenant_id}
Authorization: Bearer <server_to_server_token>
```

响应示例：

```json
{
  "snapshot_version": "identity_version_20260622103100",
  "generated_at": "2026-06-22T10:31:00+08:00",
  "tenant": {
    "tenant_id": "t_001",
    "tenant_name": "示例租户",
    "tenant_code": "demo",
    "status": "active",
    "updated_at": "2026-06-22T10:20:00+08:00"
  },
  "user": {
    "user_id": "u_001",
    "tenant_id": "t_001",
    "username": "zhangsan",
    "display_name": "张三",
    "mobile_masked": "138****0000",
    "email_masked": "z***@example.com",
    "status": "active",
    "updated_at": "2026-06-22T10:21:00+08:00"
  },
  "roles": [
    {
      "role_id": "r_001",
      "tenant_id": "t_001",
      "role_code": "superManager",
      "role_name": "超级管理员",
      "status": "active",
      "updated_at": "2026-06-22T10:22:00+08:00"
    }
  ],
  "user_roles": [
    {
      "relation_id": "ur_001",
      "tenant_id": "t_001",
      "user_id": "u_001",
      "role_id": "r_001",
      "status": "active",
      "updated_at": "2026-06-22T10:23:00+08:00"
    }
  ]
}
```

如果用户不存在、已禁用、已离职、已移出租户，接口应返回明确状态，而不是静默返回空数组。知识库会据此拒绝登录或拒绝敏感操作。

### 4.3 可选：状态探测接口

用于监控 AI 基座身份同步能力是否可用。

```http
GET /internal/identity/snapshot/health
```

响应示例：

```json
{
  "status": "ok",
  "latest_max_updated_at": "2026-06-22 10:30:00",
  "latest_change_at": "2026-06-22 10:30:00"
}
```

### 4.4 可选：事件通知 / webhook

webhook 不是第一期必需能力，但可以作为高敏变更加速通道，例如账号禁用、租户停用、角色移除。

```http
POST https://kb.example.com/internal/ai-base/identity-events
```

事件通知只作为“提醒知识库尽快拉取 delta”的信号，不建议把 webhook body 当作唯一事实源。知识库收到 webhook 后仍应回源调用 delta 或当前用户刷新接口。

## 5. 必须返回的变更类型

AI 基座增量接口必须覆盖以下变更：

| 变更 | 示例 | 知识库处理 |
| --- | --- | --- |
| 租户新增 / 更新 | 租户名称变更 | upsert 租户快照 |
| 租户停用 / 冻结 / 删除 | `status=disabled` 或 `deleted.tenant_ids` | 拒绝该租户新增 session 和敏感操作 |
| 用户新增 / 更新 | 昵称、脱敏手机号变更 | upsert 用户快照 |
| 用户禁用 / 离职 / 移出租户 | `status=disabled` 或 `deleted.user_ids` | 拒绝该用户登录和敏感操作 |
| 角色新增 / 更新 | 角色名称变更 | upsert 角色快照 |
| 角色停用 / 删除 | `status=disabled` 或 `deleted.role_ids` | 不再命中管理员判断 |
| 用户增加角色 | 新增 `user_roles` 记录 | 更新用户角色关系 |
| 用户移除角色 | `status=disabled` 或 `deleted.user_role_relation_ids` | 删除或停用本地关系，撤销管理员身份 |
| 角色编码变化 | `role_code` 改变 | 重新计算 `superManager` 命中 |

特别注意：用户角色关系被删除或移除时，不能只依赖“当前查询结果里没有这条关系”来推断。AI 基座必须提供以下至少一种机制：

1. 关系表软删除字段，例如 `deleted=true`。
2. 关系表状态字段，例如 `status=disabled`。
3. delta 响应中的 `deleted.user_role_relation_ids`。
4. 变更事件表中明确的 `user_role_removed` 事件。

否则知识库无法可靠发现“已经同步过的角色关系后来被移除”。

## 6. 字段规范

### 6.1 租户字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `tenant_id` | 是 | AI 基座租户唯一 ID，稳定不可复用 |
| `tenant_name` | 是 | 租户展示名称 |
| `tenant_code` | 否 | 租户编码 |
| `status` | 是 | `active` / `disabled` / `deleted` / `frozen` 等，需给出枚举 |
| `changed_at` / `updated_at` | 是 | AI 基座侧变更时间水位；新增、修改、停用、软删除时必须更新 |
| `version` | 可选 | 单条记录变更版本，第一期不强依赖 |

### 6.2 用户字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `user_id` | 是 | AI 基座用户唯一 ID，稳定不可复用 |
| `tenant_id` | 是 | 用户所属租户 |
| `username` | 是 | 登录名或账号名 |
| `display_name` | 否 | 展示名 |
| `mobile_masked` | 否 | 脱敏手机号，例如 `138****0000` |
| `email_masked` | 否 | 脱敏邮箱，例如 `z***@example.com` |
| `status` | 是 | `active` / `disabled` / `resigned` / `removed` / `deleted` |
| `changed_at` / `updated_at` | 是 | AI 基座侧变更时间水位；新增、修改、禁用、离职、移出租户、软删除时必须更新 |
| `version` | 可选 | 单条记录变更版本，第一期不强依赖 |

### 6.3 角色字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `role_id` | 是 | AI 基座角色唯一 ID |
| `tenant_id` | 推荐 | 租户级角色所属租户；全局角色可为空但需明确语义 |
| `role_code` | 是 | 角色编码，第一期知识库按 `superManager` 判断租户管理员 |
| `role_name` | 是 | 角色展示名称 |
| `status` | 是 | `active` / `disabled` / `deleted` |
| `changed_at` / `updated_at` | 是 | AI 基座侧变更时间水位；新增、修改、停用、删除时必须更新 |
| `version` | 可选 | 单条记录变更版本，第一期不强依赖 |

### 6.4 用户角色关系字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `relation_id` | 强烈推荐 | 用户角色关系唯一 ID，用于精确处理删除 |
| `tenant_id` | 是 | 关系所属租户 |
| `user_id` | 是 | 用户 ID |
| `role_id` | 是 | 角色 ID |
| `status` | 是 | `active` / `disabled` / `deleted` |
| `changed_at` / `updated_at` | 是 | AI 基座侧变更时间水位；授予角色、移除角色、停用关系、删除关系时必须更新 |
| `version` | 可选 | 单条关系变更版本，第一期不强依赖 |

如果 AI 基座当前没有 `relation_id`，知识库可以临时使用 `(tenant_id, user_id, role_id)` 作为复合键，但 AI 基座必须在关系移除时返回该复合键对应的删除 / 停用事件。

## 7. 变更检测规则

知识库判断已同步数据是否发生变化，依赖 AI 基座返回的变更标记。

第一期检测规则：

1. 以 `changed_at` / `updated_at` 作为增量同步水位。
2. 以业务主键或复合键执行幂等 upsert。
3. 以 `status` / `deleted` 显式状态处理停用、删除、离职、移出租户、角色移除。
4. 如后续 AI 基座具备 `change_id`、全局版本号或事件表，可升级为更稳定的内部排序机制，但不改变当前 `last_sync_at/max_updated_at` 主合同和知识库侧业务判断规则。

检测示例：

```text
本地已有：
user_id=u_001
status=active
source_changed_at=2026-06-22T10:00:00+08:00

AI 基座 delta 返回：
user_id=u_001
status=disabled
changed_at=2026-06-22T10:30:00+08:00

知识库处理：
发现 changed_at 进入本轮同步窗口
-> 更新本地用户快照为 disabled
-> 后续拒绝该用户新建 session
-> 敏感操作直接拒绝
```

使用 `changed_at` / `updated_at` 做增量水位时，AI 基座需要保证：

1. 所有身份相关表的 `changed_at` / `updated_at` 在新增、修改、停用、软删除、角色授予、角色移除时都会更新。
2. 同一秒内多条变更不会因为分页或时间精度丢失。若无法提供稳定排序，AI 基座侧应在 `last_sync_at` 边界包含等值记录并保证响应的 `max_updated_at` 可继续推进，知识库侧通过幂等 upsert 去重。
3. 传给 delta 接口和响应中的同步水位统一为 `YYYY-MM-DD HH:mm:ss`。
4. 用户角色关系移除必须能通过关系状态变化、软删除、`deleted.user_role_relation_ids` 或可查询到的移除记录体现；否则单纯按时间同步无法发现“曾经同步过但后来被删除”的关系。

## 8. 知识库侧落库方式

知识库本地只读快照建议表：

| 表 | 说明 |
| --- | --- |
| `kb_identity_tenants` | AI 基座租户快照 |
| `kb_identity_users` | AI 基座用户快照 |
| `kb_identity_roles` | AI 基座角色快照 |
| `kb_identity_user_roles` | AI 基座用户角色关系快照 |
| `kb_identity_sync_runs` | 同步运行记录、`last_sync_at`、`max_updated_at`、数量、耗时、失败原因 |

每条快照建议保存：

| 字段 | 说明 |
| --- | --- |
| `source_changed_at` / `source_updated_at` | AI 基座源数据变更时间 |
| `source_version` | AI 基座源数据版本，可选 |
| `synced_at` | 知识库同步落库时间 |
| `snapshot_version` | 本次同步快照版本，可由同步时间生成 |
| `raw_status` | AI 基座原始状态值 |
| `normalized_status` | 知识库归一化后的 `active` / `disabled` 等状态 |

知识库落库必须是幂等的。重复消费同一页 delta 不应产生重复角色关系或错误状态。

## 9. 同步事务与重试

推荐知识库按页处理：

```text
begin transaction
  upsert tenants
  upsert users
  upsert roles
  upsert user_roles
  apply deleted ids
  insert sync_run success
  record max_updated_at for next last_sync_at
commit
```

如果任一步失败：

```text
rollback
insert sync_run failed
保留旧 last_sync_at
下次仍从旧 last_sync_at 重试
```

要求：

1. 只有整批成功处理后，才能把响应中的 `max_updated_at` 作为下一次 `last_sync_at`。
2. 失败后重试同一 `last_sync_at`，AI 基座应返回同一批或兼容的后续变更。
3. AI 基座 delta 接口应支持短期重复请求，不得因为重复拉取导致源侧状态改变。
4. 如果某一批数据过大或异常，AI 基座应返回明确错误码，便于双方排查或拆分处理。

## 10. 快照新鲜度与降级策略

知识库会根据本地快照的新鲜度决定是否允许访问。

建议策略：

| 场景 | 快照状态 | 处理 |
| --- | --- | --- |
| 控制台普通读取 | 5 分钟内 | 允许 |
| 控制台普通读取 | 5-10 分钟 | 可降级允许，记录 `identity_snapshot_stale=true` |
| 控制台普通读取 | 超过 10 分钟 | 拒绝或要求刷新 |
| 新增、修改、删除、导出、配置变更、API Key 管理 | 快照过旧或刷新失败 | 拒绝 |
| 用户 / 租户 / 角色状态不明确 | 任意操作 | 拒绝 |
| 用户禁用、离职、移出租户 | 任意操作 | 拒绝登录和敏感操作 |
| 租户停用、冻结、删除 | 任意操作 | 拒绝 |

默认旧快照窗口建议 10 分钟，可配置范围建议 5-15 分钟。

## 11. 管理员判断规则

第一期知识库租户管理员 / 知识库管理员按 AI 基座角色编码判断：

```text
role_code = superManager
role_status = active
user_role.status = active
tenant.status = active
user.status = active
```

命中上述条件时，该用户可管理本租户知识库。未命中时，按普通用户处理，只能访问本人作为当前业务归属人的知识库。

平台管理员不从 `superManager` 自动推断，需 AI 基座单独确认角色编码或后续走独立配置。

如果 AI 基座调整 `superManager` 语义、编码或作用范围，需要提前通知知识库侧，并通过同步变更刷新本地快照。

## 12. 安全要求

1. 同步接口只允许知识库后端服务端调用，不允许浏览器直接调用。
2. 生产建议使用 HMAC 签名、mTLS 或内网专线鉴权。
3. 所有接口必须走 HTTPS 或内网可信链路。
4. 不返回密码、密码 hash、盐值、登录 token、refresh token、JWT 原文。
5. 手机号、邮箱默认只返回脱敏值。
6. 日志不得记录 `client_secret`、SSO code 明文、JWT 原文或服务端同步 token。
7. delta 响应中不应包含菜单权限、按钮权限、学生、教职工、分组、标签等知识库当前不消费的数据。

服务端调用签名示例：

```text
X-Client-Id: wisewe-kb
X-Timestamp: 2026-06-22T10:30:00+08:00
X-Nonce: random_nonce
X-Signature: hmac_sha256(client_secret, method + path + timestamp + nonce + body_hash)
```

这里的 `client_secret` 是 AI 基座为知识库服务端应用分配的客户端密钥，只用于服务端到服务端鉴权或签名。它不是用户密码、不是 AI 基座 JWT、不是知识库后台 session，也不是 OpenAPI 调用的 API Key。该密钥只能保存在知识库后端密钥配置或密钥管理系统中，不得写入前端代码、URL query、localStorage、日志、埋点或导出文件。

## 13. 错误码建议

| 错误码 | 含义 |
| --- | --- |
| `IDENTITY_LAST_SYNC_AT_INVALID` | `last_sync_at` 格式非法或已过期 |
| `IDENTITY_LAST_SYNC_AT_TOO_OLD` | `last_sync_at` 太旧，需重新初始化全量快照 |
| `IDENTITY_SOURCE_UNAVAILABLE` | AI 基座身份源暂不可用 |
| `IDENTITY_SNAPSHOT_BUILDING` | AI 基座快照正在生成，稍后重试 |
| `IDENTITY_CLIENT_UNAUTHORIZED` | 知识库服务端鉴权失败 |
| `IDENTITY_TENANT_NOT_FOUND` | 租户不存在 |
| `IDENTITY_USER_NOT_FOUND` | 用户不存在 |
| `IDENTITY_USER_DISABLED` | 用户禁用、离职或移出租户 |
| `IDENTITY_TENANT_DISABLED` | 租户停用、冻结或删除 |
| `IDENTITY_RATE_LIMITED` | 同步接口调用过快 |

## 14. 联调验收清单

AI 基座与知识库联调时至少覆盖：

1. 首次 `last_sync_at=` 空字符串能拉到初始化快照。
2. 非空 `last_sync_at` 只返回该水位之后的变更，且响应包含 `max_updated_at`。
3. `has_more=true` 时知识库可连续翻页直到完成。
4. 同一 `last_sync_at` 重试不会漏数据或产生副作用。
5. 用户新增后能同步到知识库。
6. 用户禁用后，知识库拒绝该用户登录和敏感操作。
7. 租户停用后，知识库拒绝该租户新增 session 和敏感操作。
8. 用户增加 `superManager` 角色后，知识库识别为租户管理员。
9. 用户移除 `superManager` 角色后，知识库撤销租户管理员身份。
10. 角色停用后，知识库不再按该角色授权。
11. 用户角色关系物理删除或软删除时，delta 能返回明确删除 / 停用事件。
12. 登录时当前用户刷新能返回最新用户、租户、角色和用户角色关系。
13. AI 基座同步接口不可用时，知识库对敏感操作默认拒绝。
14. 日志中不出现密码、JWT 原文、SSO code 明文、同步鉴权 secret。
15. `last_sync_at` 太旧时返回明确错误，双方可触发重新初始化流程。

## 15. 当前快速贯通状态

当前仓库已经有身份快照与同步链路：

1. `scripts/sync_ai_base_identity.py` 从 AI 基座 MySQL 样本同步 `system_tenant`、`sys_user`、`sys_role`、`sys_user_role`。
2. `core/db/identity.py` 从本地快照解析 `tenant_id/user_id`，并按 `role_code=superManager` 判断租户管理员。
3. RAG 侧已具备 AI 基座 SSO 本地短 session，正式控制台 API 优先使用 `kb_session` cookie；`X-KB-Tenant-Id` / `X-KB-User-Id` 仅作为开发 / 联调兜底。
4. `POST /api/identity/sync-delta` 已实现 HTTP delta 拉取与落库，但只允许 AI 基座 SSO session 且 `roleCodes` 包含 `superManager` 的当前身份触发。
5. 日志管理已新增“用户及权限同步”页签，同步成功后通过 `GET /api/console/identity-sync-logs?limit=100` 读取 `kb_identity_sync_runs`，展示租户、用户、角色、用户角色、删除事件数量、`last_sync_at`、`max_updated_at`、快照版本、状态和失败原因。

当前运行库已由真实 AI 基座 SSO `superManager` 触发过 `http_delta` 并复核新增同步日志；身份同步页可查看全量用户明细、筛选、立即同步、查看日志，并可经二次确认清空本地身份快照与同步运行记录。生产化仍需补齐失败重试、访问拒绝审计全覆盖、生产请求头兜底关闭验收和历史知识库归属迁移检查。
