# 知识库治理与开放能力实施方案（Claude 版）

版本：v0.1
日期：2026-06-17
状态：草案
适用系统：WiseWe RAG 教材 / 文档知识库系统
来源：Claude Code 基于上游 `docs/product/权限相关表.sql` 与现有项目链路独立产出

> 核心思路：知识库构建**本地 IAM + 单向数据同步**模式。AI 基座 MySQL 是上游事实源，知识库系统通过同步层把权限相关数据落地到自己的库，运行时**只读本地**做权限决策，不依赖基座 RPC 承担鉴权 SLA。基座变更通过 webhook + 定时增量 + 全量对账三层保证最终一致。
>
> 本方案与 `knowledge-base-governance-brd.md`（GPT 方案，硬边界为"知识库不维护任何权限/用户/角色"）路线不同，请并列阅读后定稿。

---

## 1. 总体架构

```
┌────────────────────────── AI 基座（MySQL，事实源） ────────────────────────┐
│  system_tenant / sys_user / sys_role / sys_user_role                     │
│  system_menu / sys_role_menu / system_tenant_package                     │
│  agent_permission / agent_access_address / digit_use_record              │
│  b_staff_basic / b_staff_group(_member) / b_staff_tag                    │
│  b_student_info / b_limit_setting(_detail) / b_user_session_record       │
└────────────┬──────────────────────────────────┬──────────────────────────┘
             │ webhook 推送                     │ 增量/全量拉取
             ▼                                  ▼
┌─────────────────────────── 知识库系统 ────────────────────────────────────┐
│ ┌─ Sync Layer（新增）───────────────────────────────────────────────────┐ │
│ │ webhook 接收器 │ 定时增量拉取 │ 全量对账器 │ 版本号/水位线/冲突解决   │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ ┌─ IAM Core（新增）─────────────────────────────────────────────────────┐ │
│ │ Identity（kb_user/kb_role/kb_user_role）                              │ │
│ │ Authorization（PolicyEngine：菜单/按钮/资源/数据范围）                │ │
│ │ Subject Profile（kb_staff/kb_student/kb_group/kb_tag — 影子表）       │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ ┌─ Edge ────────────────────────────────────────────────────────────────┐ │
│ │ /api/*       后台 API（基座 SSO Token → 本地 session）                │ │
│ │ /openapi/*   外部 API（自身 API Key + 可选 HMAC 签名）                │ │
│ │ /sync/*      同步入口（webhook 接收 + 内部回调）                      │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ ┌─ Domain（不动核心链路）──────────────────────────────────────────────┐ │
│ │ core/parser, core/chunker, core/embedding, core/rag, ingestion …      │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ ┌─ Governance（新增）──────────────────────────────────────────────────┐ │
│ │ api_key_service │ config_center │ audit_service │ usage_meter         │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│ ┌─ Storage ─ PostgreSQL（业务+治理）│ Redis（缓存+限流+任务）─────────┐ │
│ └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

依赖单向：`AI 基座 → 同步层 → 本地 IAM → 业务路由`，业务路由**永不**直接 RPC 基座。

---

## 2. 数据同步层（新增，方案核心）

### 2.1 同步通道（三层冗余）

| 通道 | 触发 | 延迟 | 用途 |
|---|---|---|---|
| **webhook 推送** | 基座写操作发生 | 秒级 | 关键状态变更：用户停用、角色权限变更、租户停用、API Key 绑定 |
| **定时增量拉取** | 5min cron | 分钟级 | 非关键数据：教职工/学生信息、分组、标签、菜单、套餐 |
| **每日全量对账** | 02:00 cron | 24h 兜底 | 版本号比对、漏同步修复、孤儿清理 |

任一通道失效都不会让数据陷入永久不一致。

### 2.2 增量识别策略

基座所有源表已有 `updated_time` 和 `deleted` 位。同步器维护 `kb_sync_watermark`：

```sql
kb_sync_watermark(
  table_name TEXT PK,
  last_synced_updated_time TIMESTAMP,
  last_synced_id BIGINT,
  last_full_sync_at TIMESTAMP,
  rows_in_last_run INT,
  status ENUM(idle, running, failed),
  error_message TEXT)
```

增量 SQL 模板（以 `sys_user` 为例）：

```sql
SELECT * FROM sys_user
WHERE updated_time >= :last_watermark
   OR (updated_time = :last_watermark AND id > :last_id)
ORDER BY updated_time, id
LIMIT 1000;
```

### 2.3 同步表清单（基于上游 SQL 一一映射）

| 上游表 | 本地映射 | 同步频率 | 备注 |
|---|---|---|---|
| `system_tenant` | `kb_tenant` | webhook + 5min | 状态变化必须实时 |
| `system_tenant_package` | `kb_tenant_package` | 5min | 含 `menu_ids` JSON |
| `sys_user` | `kb_user` | webhook + 5min | 不存密码哈希 |
| `sys_role` | `kb_role` | webhook + 5min | 含 `data_scope` |
| `sys_user_role` | `kb_user_role` | webhook + 5min | 多对多关系表 |
| `system_menu` | `kb_menu` | 30min | 菜单变更频率低 |
| `sys_role_menu` | `kb_role_menu` | webhook + 5min | 角色权限关键 |
| `agent_permission` | `kb_resource_permission`（结构对齐） | 仅参考结构，知识库自建 | 知识库资源权限本地维护 |
| `agent_access_address` | `kb_access_address`（影子） | 5min | API 调用源参考 |
| `b_staff_basic` | `kb_staff_basic` | 15min | 影子表，用于范围权限判断 |
| `b_staff_group / member` | `kb_staff_group(_member)` | 15min | 教职工分组范围 |
| `b_staff_tag` | `kb_staff_tag` | 15min | 标签范围 |
| `b_student_info` | `kb_student_info` | 15min | 年级/专业/班级范围 |
| `b_limit_setting / detail` | `kb_limit_setting(_detail)` | webhook + 5min | 额度规则 |
| `b_user_session_record` | 不同步 | — | 知识库自有 RAG 会话日志 |

**关键决策**：知识库**只同步上游数据**到本地影子表，**不反向写**任何数据回基座。本地新增的"知识库专属权限"（如知识库级 `staff_scope/student_scope`）写入 `kb_resource_permission`，独立于上游。

### 2.4 同步幂等与冲突

- 主键沿用上游 `id`（bigint），本地 `kb_*` 表用同一 ID，确保跨系统可关联。
- `UPSERT ON CONFLICT (id) DO UPDATE`，永远以上游 `updated_time` 较大者为准。
- 删除采用软删：上游 `deleted=1` → 本地 `deleted_at = now()`，运行时查询过滤。
- 同步失败不影响在线服务：本地有最后一次成功的快照可用。

### 2.5 同步可观测

- `/console/sync` 页面：每张表的水位线、最近同步耗时、行数、失败原因、手动重跑按钮。
- Prometheus 指标：`sync_lag_seconds{table=...}`、`sync_rows_total`、`sync_errors_total`。
- 告警：水位线落后 > 30min、连续 3 次失败、全量对账发现偏差。

---

## 3. 本地 IAM 模型

### 3.1 身份与角色（影子+扩展）

```sql
kb_user(
  id BIGINT PK,             -- 同上游 sys_user.id
  tenant_id BIGINT,
  user_name VARCHAR(30),
  nick_name VARCHAR(30),
  status TINYINT,           -- 0 正常 1 停用
  user_type TINYINT,
  source_updated_time TIMESTAMP,  -- 上游水位
  synced_at TIMESTAMP,
  deleted_at TIMESTAMP NULL,
  -- 知识库扩展（上游无的字段）
  last_login_kb_at TIMESTAMP NULL,
  preference JSONB)

kb_role(
  id BIGINT PK,
  tenant_id BIGINT,
  name VARCHAR(30),
  code VARCHAR(100),
  data_scope CHAR(1),       -- 1全部 2自定 3本部门 4本部门及以下
  status CHAR(1),
  source_updated_time TIMESTAMP,
  synced_at TIMESTAMP,
  deleted_at TIMESTAMP NULL)

kb_user_role(user_id, role_id, tenant_id, source_updated_time, synced_at)

kb_menu(id, name, permission, type, parent_id, full_id_path, ...)
kb_role_menu(role_id, menu_id, tenant_id, source_updated_time, synced_at)
```

### 3.2 资源权限（本地自建，对齐 `agent_permission` 风格）

**这是本方案与"完全只读基座"路线的最大区别**：知识库内部资源（KB、API Key、配置档案）的授权，由知识库**本地维护**，可在管理后台配置，不依赖上游。

```sql
kb_resource_permission(
  id BIGSERIAL PK,
  tenant_id BIGINT,
  resource_type VARCHAR(32),   -- knowledge_base / api_key / config_profile
  resource_id BIGINT,
  open_to_all BIT,             -- 1 开放全员 0 指定范围
  staff_allowed ENUM('DENY','ALL','SPECIFIC'),
  student_allowed ENUM('DENY','ALL','SPECIFIC'),
  staff_scope JSONB,           -- {tags:[],groups:[],department:[]}
  student_scope JSONB,         -- {major:[],grade:[],class:[]}
  role_codes JSONB,            -- ["kb_admin","kb_viewer"] 角色码白名单
  user_ids JSONB,              -- 显式用户白名单（少用）
  created_by, updated_by,
  created_at, updated_at, deleted)
```

完全复用基座 `agent_permission` 的语义模型，让产品理解一致。新增字段 `role_codes` 支持 RBAC，`user_ids` 处理特殊情况。

### 3.3 PolicyEngine（鉴权决策引擎）

`core/iam/policy_engine.py`：纯函数 + 本地查询，不出网络。

```python
def can(actor: Identity, action: str, resource: Resource) -> Decision:
    # 1. 租户边界（最先判定）
    if actor.tenant_id != resource.tenant_id and not actor.is_platform_admin:
        return Decision.deny("CROSS_TENANT")

    # 2. 用户/租户存活
    if actor.status != 0 or tenant_status(actor.tenant_id) != 1:
        return Decision.deny("USER_OR_TENANT_DISABLED")

    # 3. 菜单/按钮权限（基于 kb_role_menu）
    required_perm = action_to_permission(action)  # e.g. "kb:doc:upload"
    if required_perm and not has_menu_permission(actor, required_perm):
        return Decision.deny("MISSING_MENU_PERMISSION")

    # 4. 资源权限（基于 kb_resource_permission）
    perm = load_resource_permission(resource.type, resource.id)
    if perm.open_to_all:
        return Decision.allow("OPEN_TO_ALL")
    if matched_role(actor, perm.role_codes):
        return Decision.allow("ROLE_MATCH")
    if actor.id in perm.user_ids:
        return Decision.allow("EXPLICIT_USER")
    if matched_staff_scope(actor, perm.staff_scope):
        return Decision.allow("STAFF_SCOPE_MATCH")
    if matched_student_scope(actor, perm.student_scope):
        return Decision.allow("STUDENT_SCOPE_MATCH")

    return Decision.deny("NO_RULE_MATCHED")
```

性能：所有数据本地化 + Redis 二级缓存（TTL 30s）；p95 < 5ms。

### 3.4 KbAuthGuard（FastAPI 依赖）

```python
@router.post("/api/knowledge-bases/{kb_id}/documents/upload",
    dependencies=[Depends(KbAuthGuard("knowledge_base", "document.upload"))])
```

Guard 流程：
1. 解析 `Authorization: Bearer <session>`，从 Redis/`kb_session` 还原 `Identity`。
2. 拼 `Resource(type, id from path)`。
3. 调 `PolicyEngine.can()`，缓存结果到 Redis（key 含 actor+action+resource）。
4. 失败：写 `kb_authz_logs`，返回 403。
5. 成功：注入 `request.state.identity / decision`，给后续日志中间件用。

### 3.5 SSO 与登录态

- 用户从 AI 基座登录后，基座颁发 token；知识库系统通过 `/api/auth/sso/exchange` 用此 token 换本地 session。
- 交换流程：先调用基座一次（仅在交换时点）→ 获得 `actor_id / tenant_id` → 查本地 `kb_user` 验证状态 → 写 Redis `kb_session:{token}`，TTL 8h。
- 此后所有 `/api/*` 请求只查 Redis session，**不**再回基座，符合"运行时不依赖基座"原则。

---

## 4. 对外 OpenAPI 与 API Key

### 4.1 路由结构

```
/openapi/v1/knowledge-bases
/openapi/v1/knowledge-bases/{kb_id}
/openapi/v1/knowledge-bases/{kb_id}/query
/openapi/v1/knowledge-bases/{kb_id}/graph-query
/openapi/v1/knowledge-bases/{kb_id}/documents
/openapi/v1/knowledge-bases/{kb_id}/documents/upload
/openapi/v1/documents/{document_id}
/openapi/v1/tasks/{task_id}
/openapi/v1/knowledge-bases/{kb_id}/graph
/openapi/v1/usage
```

与 `/api/*` 完全独立的路由模块、完全独立的中间件栈。

### 4.2 API Key 数据模型

```sql
kb_api_key(
  id BIGSERIAL PK,
  key_id VARCHAR(32) UNIQUE,           -- 公开部分
  key_hash VARCHAR(128),               -- Argon2id(secret)
  key_prefix VARCHAR(8), key_suffix4 CHAR(4),
  tenant_id BIGINT,
  bound_user_id BIGINT,                -- 绑定到本地用户（关键：API Key 携带身份）
  bound_role_codes JSONB,              -- 或绑定角色组
  name VARCHAR(64),
  status ENUM(enabled, disabled, rotating),
  require_signature BOOL DEFAULT FALSE,
  ip_allowlist CIDR[],
  qps_limit INT, concurrency_limit INT,
  daily_quota INT, total_quota BIGINT,
  total_used BIGINT DEFAULT 0,
  expires_at TIMESTAMP,
  rotation_grace_until TIMESTAMP NULL,
  previous_key_hash VARCHAR(128) NULL,
  last_used_at TIMESTAMP,
  risk_action ENUM(allow, deny, downgrade),
  created_by, updated_by, created_at, updated_at, deleted)

kb_api_key_capability(api_key_id, capability VARCHAR(64))
kb_api_key_binding(api_key_id, kb_id, tenant_id)
```

**关键设计**：API Key 必须 `bound_user_id` 或 `bound_role_codes` 至少有一个，让外部调用也能复用本地 PolicyEngine——把 API Key 视为"佩戴某身份的访客令牌"，统一鉴权口径。

### 4.3 认证链（每请求顺序执行）

1. **解析凭证**：Bearer 模式 `Authorization: Bearer <key_id>.<key_secret>`；签名模式读 `X-KB-*` 头。
2. **存活校验**：`kb_api_key`，`status=enabled`，未过期，`Argon2id(secret) == hash`（含轮换宽限期支持双 hash）。
3. **签名校验**（`require_signature=true` 或命中默认强签名接口）：
   - `|now - timestamp| ≤ 300s`
   - Redis `kb:nonce:{key_id}:{nonce}` SETNX，TTL=600s
   - `body_hash == sha256(raw_body)`
   - `signature == HMAC-SHA256(secret, METHOD + PATH + ts + nonce + body_hash)`
4. **IP 白名单**：CIDR 匹配。
5. **能力码**：当前接口能力码 ∈ `kb_api_key_capability`。
6. **租户/KB 绑定**：`kb_id` ∈ `kb_api_key_binding`，`tenant_id` 一致。
7. **本地租户/KB 状态**：`kb_tenant.status=1` 且 `knowledge_bases.status='enabled'`（这里直接读本地，无 30s 滞后问题）。
8. **PolicyEngine**：以 `bound_user_id` 或虚拟用户 + `bound_role_codes` 为身份，复用 `can(actor, action, resource)`。
9. **限流额度**（Redis）：QPS 滑窗 + 并发 INCR/DECR + 日额度计数 + 总额度 incr。
10. **schema 校验**：Pydantic `extra="forbid"`，长度/字段数/文件大小硬限。
11. **风险扫描**：query 命中 prompt-injection 特征 → `risk_flag` 落日志，按 `risk_action` 处置。

**亮点**：第 7 步直接读本地，比"调基座"快 100 倍且无 SLA 风险；本地数据来自 webhook + 5min 同步，最坏 5min 滞后，可接受。

### 4.4 系统级强签名接口

无视 API Key `require_signature` 配置，强制签名：
- `POST /openapi/v1/knowledge-bases/{kb_id}/documents/upload`
- `POST /openapi/v1/knowledge-bases/{kb_id}/graph-query`
- 任何 `/openapi/v1/*/export` 类接口
- 任何 `/openapi/v1/*/batch` 类接口

### 4.5 错误码

完整 19 个错误码（INVALID_API_KEY / API_KEY_DISABLED / API_KEY_EXPIRED / SIGNATURE_REQUIRED / SIGNATURE_INVALID / REQUEST_EXPIRED / NONCE_REPLAYED / BODY_HASH_MISMATCH / IP_NOT_ALLOWED / RATE_LIMITED / QUOTA_EXCEEDED / PAYLOAD_TOO_LARGE / REQUEST_SCHEMA_INVALID / KB_ACCESS_DENIED / KB_NOT_FOUND / DOCUMENT_NOT_FOUND / TASK_NOT_FOUND / PROVIDER_UNAVAILABLE / INTERNAL_ERROR）。HTTP status 标准映射 401/403/413/422/429/503。

---

## 5. 前端管理后台

### 5.1 信息架构

```
现有：总览 / 知识库 / 入库任务 / 在线问答 / 评测
新增（IAM 与运营）：
  ├─ 用户中心
  │   ├─ 用户管理（来自基座，只读 + 知识库扩展属性）
  │   ├─ 角色管理（来自基座，知识库可定义资源授权）
  │   └─ 数据范围（教职工/学生范围浏览）
  ├─ API Key 管理
  ├─ 配置中心
  ├─ 日志中心
  ├─ 同步中心（新增：基座同步状态、水位线、手动重跑、对账结果）
  └─ 鉴权监控
```

### 5.2 关键页面要点

- **用户管理**：列表展示 `kb_user`（同步自基座），可见但不可编辑账号信息；可编辑"知识库扩展属性"（偏好、最近知识库等）。
- **角色管理**：左侧基座角色树，右侧"该角色对知识库资源的授权"——点击角色 → 显示其在 `kb_resource_permission` 中的所有规则。
- **资源授权抽屉**：在每个知识库详情页有"权限"标签页，等价于基座 `agent_permission` 的配置 UI（开放全员 / 教职工范围 / 学生范围 / 角色白名单 / 用户白名单），写入 `kb_resource_permission`。
- **API Key 管理**：列表（前缀+后四位）、创建抽屉（命名/绑定 KB/绑定用户或角色/能力多选/IP/限流/过期/签名开关）、明文一次性、调用量趋势 30 天、最近 100 条日志、复制 curl/JS 示例。
- **配置中心**：4 层 scope tab，左侧分组树，敏感字段 `••••••`，diff 预览。
- **日志中心**：统一筛选 + 虚拟滚动 + 详情抽屉。
- **同步中心**：每张 `kb_*` 影子表的水位线、最近同步耗时、行数、失败重跑、全量对账结果。
- **鉴权监控**：可用性 / p95 / allow:deny / reasonCode 分布。

### 5.3 工程化

- HTTP 拦截器：401 → 跳基座登录；403 → 跳无权限页（不重定向，避免循环）。
- `useMenus()`：调 `/api/me/menus`，5min 前端 cache。
- `<PermissionGate code="kb:doc:upload">`：未授权不渲染（仅 UX，后端必再校验）。

---

## 6. 配置中心

### 6.1 数据模型

```sql
kb_config_profile(
  id UUID PK,
  scope ENUM(global, tenant, kb, api_key),
  scope_id BIGINT NULL, name TEXT, description TEXT,
  status, version INT,
  created_by, created_at, updated_by, updated_at)

kb_config_value(
  id UUID PK, profile_id FK, group_code TEXT, key TEXT,
  value JSONB, value_type ENUM(string,number,bool,json,secret),
  is_secret BOOL, secret_cipher BYTEA NULL,
  hot_reload BOOL, version INT,
  UNIQUE(profile_id, group_code, key))

kb_config_change_log(
  id, profile_id, key, old_hash, new_hash,
  actor_id, action ENUM(create,update,delete,rollback),
  client_ip, request_id, created_at)
```

### 6.2 解析顺序

`api_key > kb > tenant > global > env_default`，按 key 维度逐层 merge。

### 6.3 加密

密钥字段 AES-256-GCM 加密，KEK 来自 `KB_CONFIG_KEK`（环境变量 / Vault），明文绝不落 DB、不入日志、读 API 默认脱敏。保存空字符串不清原值。

### 6.4 分组（基于现有项目特性）

| 分组 | 示例 key |
|---|---|
| 解析 | `PDF_PARSER_PROVIDER`、`MINERU_OFFICIAL_*`、`ALIYUN_DOCUMENT_MIND_*` |
| 切片 | `HIERARCHICAL_*`、`INGESTION_READY_MODE` |
| 向量 | `LLM_EMBEDDING_MODEL`、`LLM_EMBEDDING_BATCH_SIZE`、`LLM_EMBEDDING_API_KEY_POOL` |
| 问答 | `RAG_LLM_MODEL`、`RAG_RETRIEVAL_SNAPSHOT` |
| Graph RAG | `GRAPH_RAG_*` |
| 安全 | API Key 默认过期、IP 白名单默认策略 |
| 日志 | 保留天数、脱敏策略 |

### 6.5 与现有 `console_settings` 关系

替换：迁移脚本一次性把现有 `console_settings` 落到 `kb_config_profile(scope=global)` + `kb_config_value`，保留旧表 1 个版本作回退兜底。`core/runtime_settings.py:apply_runtime_env_overrides()` 接管层换成 `ConfigCenter.reload()`，热更新行为不变。

### 6.6 入库任务快照

`tasks` 表加 `config_snapshot JSONB`，任务创建时把生效配置 snapshot 进去，便于复现历史性能（与现有 T-077~T-095 优化档可关联）。

---

## 7. 日志管理

### 7.1 日志分类（7 类）

| 表 | 说明 | 保留 |
|---|---|---|
| `kb_audit_log` | 关键管理操作（KB CRUD、API Key CRUD、配置变更/回滚、删除文档、确认入库、导出日志） | 365d |
| `kb_api_call_log` | `/openapi/v1/*` 请求-响应、状态、耗时、错误码、字节、风险标签 | 180d |
| `kb_rag_query_log` | 问题（hash）、答案摘要、引用数、score、tokens | 180d |
| `kb_ingestion_event_log` | 入库 7 阶段（upload/parse/clean/chunk/quality/embedding/export）耗时与指标 | 180d |
| `kb_authz_log` | PolicyEngine 决策日志（allow/deny + reason_code + matched_rule） | 180d |
| `kb_sync_log` | 数据同步事件、水位线、错误 | 90d |
| `kb_config_change_log` | 配置变更（见 §6.1） | 365d |
| `kb_usage_daily` | 日维度聚合（tenant/kb/api_key × calls/tokens/errors） | 730d |

### 7.2 写入路径

FastAPI 中间件 → `asyncio.Queue` → 后台 worker 批量 `execute_values`（500/批，1s flush）→ 写库失败本地 NDJSON `data/logs/audit/` + 告警。**永不阻塞主链路**，不影响 RAG 返回。

### 7.3 分区策略

`kb_api_call_log` / `kb_rag_query_log` / `kb_ingestion_event_log` / `kb_authz_log` 按月 RANGE 分区，`pg_partman` 自动维护，过期分区直接 drop（比 delete 快 100 倍）。

### 7.4 强制审计

`@audit("knowledge_base.delete")` 装饰器在以下操作上必须出现，CI 静态校验覆盖率：

> 创建/修改/删除知识库、API Key 创建/禁用/删除/轮换、配置修改/回滚、删除文档、确认入库、导出日志。

### 7.5 日志中心查询

支持按：tenant / kb / actor_id / api_key_id / request_id / task_id / decision_id / 时间段 / 类型 / status_code / risk_flag 多维筛选。

---

## 8. 数据库设计总览

**库结构**：与现有 pgvector 同库，新建 schema：

```
public.*               业务表（不变 + 加 tenant_id 字段）
kb_iam.*               身份权限：kb_user / kb_role / kb_user_role / kb_menu / kb_role_menu
kb_subject.*           主体影子：kb_tenant / kb_tenant_package / kb_staff_* / kb_student_* / kb_limit_*
kb_governance.*        治理：kb_api_key* / kb_config_* / kb_resource_permission
kb_logs.*              日志：所有 *_log 表（独立 schema 便于授权和清理）
kb_sync.*              同步：kb_sync_watermark / kb_sync_run_history
```

业务表扩展（alembic 迁移，零停机：先加列允许 NULL → 回填 → 改 NOT NULL → 上索引）：

```sql
ALTER TABLE knowledge_bases  ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0,
                             ADD COLUMN status VARCHAR(16) DEFAULT 'enabled',
                             ADD COLUMN config_profile_id UUID,
                             ADD COLUMN created_by BIGINT,
                             ADD COLUMN updated_by BIGINT;
ALTER TABLE documents        ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0,
                             ADD COLUMN uploaded_by BIGINT,
                             ADD COLUMN deleted_by BIGINT,
                             ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE chunks           ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE chunk_relations  ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE kg_triples       ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0;
ALTER TABLE entities         ADD COLUMN tenant_id BIGINT NOT NULL DEFAULT 0;
CREATE INDEX ON chunks(tenant_id, document_id);
```

**租户强隔离**：`TenantFilter` FastAPI 依赖把 `tenant_id` 注入所有 query；CI lint（grep）禁止裸 SQL `FROM (chunks|documents|...)` 不带 `tenant_id`。

---

## 9. 代码组织

```
backend/
├── auth/                       新增
│   ├── sso.py                  基座 token 交换
│   ├── session.py              本地 session 存取
│   ├── guard.py                KbAuthGuard（FastAPI dep）
│   ├── api_key.py              ApiKeyAuthenticator + 签名校验
│   └── decorators.py           @audit / @require_capability
├── iam/                        新增
│   ├── policy_engine.py        本地决策核心
│   ├── identity.py             Identity / Resource / Decision 数据类
│   └── scope_matcher.py        staff_scope / student_scope 匹配
├── sync/                       新增
│   ├── source_client.py        基座 MySQL 连接（只读）
│   ├── webhook.py              webhook 接收器
│   ├── puller.py               定时增量拉取
│   ├── reconciler.py           全量对账
│   └── tables/                 各表同步器（user.py / role.py / staff.py / ...）
├── routes/
│   ├── auth.py                 新增（/api/auth/sso/exchange、/api/me/*）
│   ├── sync.py                 新增（/sync/webhook、/api/sync/*）
│   ├── iam.py                  新增（/api/users、/api/roles、/api/permissions）
│   └── openapi/v1/             新增（10 端点）
├── services/
│   ├── api_key_service.py      新增
│   ├── config_service.py       新增（接管 console_settings）
│   ├── audit_service.py        新增
│   ├── usage_service.py        新增
│   └── sync_service.py         新增
├── middleware/                 新增
│   ├── request_id.py
│   ├── audit_log.py            异步日志写入
│   ├── tenant_filter.py        强制 tenant_id
│   └── rate_limit.py
core/
└── governance/                 新增（领域无关纯能力）
    ├── crypto.py               HMAC / AES-GCM
    └── risk_filter.py          prompt-injection 检测
db/
├── migrations/                 alembic
└── partitions/                 pg_partman 配置
```

依赖方向：`backend → core/governance` 单向；`backend/sync` 单向只读基座 MySQL；`backend/iam` 只读本地 PG。

---

## 10. 关键流程

### 10.1 用户访问后台

```
基座登录 → 拿到 base_token
↓
浏览器进入知识库后台 → POST /api/auth/sso/exchange { base_token }
↓
后端调基座一次校验 token → 拿到 actor_id, tenant_id
↓
查 kb_user / kb_user_role / kb_role_menu → 组装 Identity
↓
写 Redis kb_session:{kb_token} (TTL 8h)，返回 kb_token
↓
后续所有 /api/* 请求只查 Redis session，PolicyEngine 本地决策
```

### 10.2 创建知识库 + 配置权限

```
用户点击创建 → KbAuthGuard("knowledge_base","create") allow
→ 写 knowledge_bases (tenant_id, created_by)
→ 写 kb_resource_permission (默认 open_to_all=0, role_codes=[当前用户主角色])
→ 写 kb_audit_log
→ 返回详情
（可在权限标签页继续调整范围）
```

### 10.3 外部 API Key 调用问答

```
请求带 Bearer/签名头 → ApiKeyAuthenticator 11 步校验
→ 第 8 步用 bound_user_id 装载 Identity，PolicyEngine.can(query)
→ 限流额度通过 → schema 校验 → 风险扫描
→ 进入 RAG 主链路（不动）
→ 返回结果 + 异步写 kb_api_call_log + kb_rag_query_log + 用量计数
```

### 10.4 基座用户停用同步

```
基座 sys_user.status 改 1 → 触发 webhook → POST /sync/webhook { table:"sys_user", id:... }
→ 同步器拉该用户最新行 → UPSERT kb_user (status=1)
→ 主动删除该用户在 Redis 的所有 kb_session:* 与 PolicyEngine 缓存
→ 写 kb_sync_log
（最坏情况 webhook 漏，5min 后定时拉取兜底，24h 后全量对账兜底）
```

### 10.5 配置变更

```
PUT /api/config/{key} → KbAuthGuard("config","update") allow
→ schema 校验 → secret 加密
→ 写 kb_config_value (新版本号)
→ ConfigCenter.reload() 刷新进程内缓存
→ 写 kb_config_change_log
→ 返回成功（不可热更新 key 返回 RESTART_REQUIRED）
```

---

## 11. 实施路线（4 期，独立可交付）

| 期 | 周期 | 交付 | 阻塞门 |
|---|---|---|---|
| **P0 同步层 + IAM 基础** | 3 周 | sync 框架 / 8 张影子表初始化 / kb_user/kb_role/kb_user_role/kb_menu/kb_role_menu / SSO 交换 / KbAuthGuard / 现有 `/api/*` 全接入 | 基座连通；同步水位线 < 5min；后台所有页面鉴权通过 |
| **P1 资源权限 + OpenAPI + API Key** | 3 周 | kb_resource_permission / 资源授权页 / 10 个 OpenAPI / API Key 11 步认证链 / 限流额度 / 调用日志 | API Key 全部安全断言通过（签名/重放/IP/能力/绑定/限流） |
| **P2 配置中心** | 2 周 | 4 层 scope / 加密 / 变更日志 / 回滚 / `console_settings` 迁移 / 任务 config_snapshot | 现有热更新无回退；密钥读 API 全脱敏 |
| **P3 日志中心 + 监控** | 2 周 | 7 类日志 / 月分区 / 异步写 / 中心页 / 鉴权监控 / 同步中心 / 用量统计 / 导出 | 写日志失败不阻塞 RAG；CI `@audit` 覆盖 100% |

并行策略：P0 完成后，P1/P2/P3 可并行（独立目录、独立表、独立路由）。

---

## 12. 非功能与性能预算

| 指标 | 目标 | 落地手段 |
|---|---|---|
| 后台 API 鉴权 p95 | < 5ms | 本地 PG + Redis 30s 缓存，无网络 RPC |
| API Key 校验 p95 | < 30ms | 内存 LRU（key_id → hash, ttl=10s）+ Argon2id only on miss |
| 知识库列表 p95 | < 500ms | tenant_id 索引 + 列表缓存 |
| 同步水位线 | < 5min | webhook 秒级 + 5min cron 兜底 |
| 全量对账漂移 | 0 | 02:00 跑全量比对，自动补齐 |
| 日志写入对主链路 | 0 阻塞 | asyncio queue + 本地 NDJSON 兜底 |
| 跨租户泄露 | 0 | TenantFilter + CI lint |

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 基座 webhook 漏推 | 数据滞后 | 5min 定时拉取兜底 + 02:00 全量对账 |
| 基座库压力 | 同步影响线上 | 只读从库；增量批 1000 行；夜间跑全量 |
| 本地 IAM 与基座规则不一致 | 决策错误 | 同步幂等；冲突以基座 `updated_time` 较大者为准；对账自愈 |
| 上游表结构变更 | 同步报错 | 同步器对未知字段宽容（落 raw JSONB），告警人工介入 |
| API Key 泄露 | 外部滥用 | hash 存储、IP 白名单、限流、立即禁用、轮换、签名模式、风险扫描 |
| `kb_resource_permission` 误配 | 越权 | 操作必走 `@audit`；提供"模拟某用户访问"调试工具；变更前预览影响范围 |
| 跨租户数据泄露 | 严重事故 | 业务表全加 `tenant_id`；TenantFilter 依赖；CI 静态校验 |
| 配置误改影响线上 | 解析/问答异常 | 变更预览 diff、审计、回滚、按 scope 灰度 |
| pgvector 同库性能 | 治理表抢资源 | 治理表小（< 千万），独立 schema 便于将来分库；日志表分区可独立移走 |
| 同步初始化耗时 | 首次部署慢 | 全量初始化提供 `--parallel N` 并行；分表加载，不互相阻塞 |

---

## 14. 与上游 SQL 的字段映射速查

| 基座源 | 关键字段 | 知识库本地用法 |
|---|---|---|
| `system_tenant` | `id, code, status, package_id` | 租户判活；查 `package_id` 决定可见菜单 |
| `system_tenant_package.menu_ids` | JSON 菜单 ID 数组 | 与 `kb_role_menu` 取交集得到最终可见菜单 |
| `sys_user` | `id, tenant_id, status, user_type` | Identity 主键；status≠0 立即吊销 session |
| `sys_role` | `id, code, data_scope, status` | RBAC 角色；`data_scope` 进 PolicyEngine |
| `sys_user_role` | `user_id, role_id` | Identity 角色集 |
| `system_menu` | `permission`（如 `kb:doc:upload`） | 按钮/菜单可见性判断 |
| `sys_role_menu` | `role_id, menu_id` | 角色菜单关系 |
| `agent_permission` | `staff_scope, student_scope` JSON | 直接复刻到 `kb_resource_permission`，语义一致 |
| `b_staff_basic.tags` | `;` 分隔 | scope 匹配 `tags:[]` |
| `b_staff_group_member` | `group_id, staff_id` | scope 匹配 `groups:[]` |
| `b_student_info` | `grade_name, major_name, class_name` | scope 匹配 `grade/major/class` |
| `b_limit_setting(_detail)` | `day_max_message, all_max_message` | 用户级额度，叠加 API Key 技术额度 |
| `agent_access_address.day_max_message` | 访问地址级额度 | 参考语义，本地按 API Key 实现 |

---

## 15. 与 BRD（GPT 方案）路线对照

本方案为独立路线，与 `knowledge-base-governance-brd.md` 并存，最终由产品/架构方决策选其一或合并：

| 维度 | BRD（GPT） | 本方案（Claude） |
|---|---|---|
| IAM 归属 | 完全只读基座，知识库不维护任何用户/角色/权限 | 本地 IAM + 单向同步影子表，知识库可定义资源授权 |
| 鉴权运行时 | 每次调基座 `/authz` | 本地 PolicyEngine，不出网 |
| 数据范围授权 | 由基座下发结果 | 本地 `kb_resource_permission` 自主配置 |
| 基座依赖 | 强（每次调用） | 弱（仅 SSO 交换 + 同步） |
| 性能 | 受基座 SLA 影响 | 本地决策 p95 < 5ms |
| 上游变更 SLA | 实时 | 秒~5min（webhook+cron） |
| 后台页面 | 无用户/角色/权限页 | 有用户/角色/资源授权页 |

适用判断：
- 若基座可承担鉴权 SLA 且强一致是硬要求 → 选 BRD。
- 若强一致非必须、追求性能与可用性、希望知识库自治运营 → 选本方案。

---

落档说明：本文件由 Claude Code 独立产出，与 `knowledge-base-governance-brd.md` 并列保存，互不修改。后续若决策合并，再产出 `knowledge-base-governance-final.md` 收口。

