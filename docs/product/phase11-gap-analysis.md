# Phase 11 未实现与延后关键点清单

更新时间：2026-06-26

本文基于 `docs/product/knowledge-base-governance-brd.md`、`.planning/phases/11-*` 计划与总结、当前后端路由、`docs/product/AI基座SSO知识库对接文档.md` 和当前 `.env` 梳理 Phase 11 的真实落地状态。结论先说清楚：Phase 11 已经完成一轮“快速贯通 + 独立治理模块”落地，并在 2026-06-22 补齐 RAG 侧 AI 基座 SSO exchange + 本地短 session 闭环，2026-06-23 又补齐 SSO 真实路径配置、基础审计日志、入库链路 Token 明细补点和按链路过滤展示，2026-06-24 继续补齐小时级 token rollup、费用估算、quota 告警、趋势图展示、查询日志导出审计、当前用户刷新、HTTP 身份 delta 同步，以及清洗、质量审核、在线问答生成、OpenAPI、评测打分等可验证调用点的 `kb_llm_call_logs` 写入。2026-06-26 又补齐了真实 SSO `superManager` 触发后的 `http_delta` 全量同步复核、身份快照新鲜度强制窗口、AI 基座单点退出回调、失效用户归属待转交与管理员转交、OpenAPI 调用方 app 生命周期、API Key 分钟/日配额强制拦截和身份同步页全量明细 / 筛选 / 清空入口。但不能视为完整治理闭环。统一子资源权限收口、失败重试、访问拒绝审计全覆盖、托管解析 / Graph RAG 内部 LLM 的逐次采集、配置版本回滚、OpenAPI 并发 / 月度配额 / SDK 仍是后续硬化重点。

## 总体状态

| 领域 | 已落地 | 快速贯通 / 临时方案 | 未实现 / 延后 | 风险 | 建议优先级 |
| --- | --- | --- | --- | --- | --- |
| 11-01 身份与访问底座 | 本地只读身份快照表、AI 基座 MySQL 样本同步、`X-KB-Tenant-Id` / `X-KB-User-Id` 联调身份上下文、`role_code=superManager` 租户管理员判断；RAG 侧 `/api/auth/ai-base/launch`、`/callback`、`/exchange`、`/api/auth/session`、`/api/auth/logout`；`kb_auth_sessions` 本地短 session；`kb_sso_used_credentials` 一次性凭证防重放；HttpOnly `kb_session` cookie；租户端真实 `launch / exchange / user snapshot / delta` 路径已进入服务端配置和控制台设置，`launch` 已带 `client_id`，`exchange` 已解包 `Result.data`；RAG launch 统一负责生成随机 `state` 与 RAG cookie；当前用户刷新和 HTTP `delta` 同步已支持服务端调用、`max_updated_at` / `snapshot_version` 落库和 `deleted` 数组处理；真实 SSO `superManager` 已触发 `http_delta` 并完成 52 租户、69278 用户、227 角色、70755 用户角色入库复核；高风险操作已接入身份快照新鲜度强制窗口；AI 基座服务端 logout callback 可撤销本地短 session；身份同步页支持全量用户明细、筛选、立即同步、查看日志和二次确认清空本地快照 | 旧请求头身份作为开发 / 联调兜底保留，可通过开关关闭；JWT 兼容模式通过后端 exchange 换本地 session，不保存 JWT 原文；身份 delta 只允许 AI 基座 SSO 的 `superManager` 触发，后台调度若没有当前 SSO `superManager` 上下文只记录 `skipped` | 身份同步失败重试、统一访问判定中间件、访问拒绝审计全覆盖、生产环境请求头兜底关闭验收；若后续启用 JWT 兼容主路径，再补 RAG 侧本地 JWKS 公钥验签 fallback 和 `jti` 吊销查询 | 登录、同步、当前用户刷新、新鲜度、退出回调已经具备后端闭环；风险集中在失败重试、兜底关闭验收和子资源访问判断尚未全覆盖 | P0 |
| 11-02 知识库业务闭环 | `knowledge_bases` 增加租户、创建人、当前归属人、软删除字段；知识库 CRUD 服务端过滤；新增知识库 ID 改为 24 位 hex；失效用户仍为当前归属人或历史创建人时会标记 `owner_status='pending_transfer'` / `owner_invalid_reason`，管理员可通过 `POST /api/knowledge-bases/{kb_id}/transfer-owner` 转交同租户 active 用户并写审计 | 旧匿名路径继续兼容；带身份头启用治理判断；转交接口已覆盖最小接管闭环 | 历史库正式迁移脚本、批量待处理工作台、恢复流程、文档/入库任务/图谱/评测等全部子资源权限收口、更多操作审计 | 子资源入口若未逐一收口，可能绕过知识库级治理；失效用户接管已有后端能力但缺少完整工作台 | P0 |
| 11-03 RAG / Graph RAG / 评测治理 | `/api/rag/query`、`/api/rag/graph-query` 在召回前校验知识库可访问性；软删除库不可召回 | 匿名路径兼容历史控制台；有身份上下文时先过 KB 守门 | 文档详情、知识库图谱、文档图谱、CSV 导出、评测记录的完整权限裁剪；评测记录按租户/归属过滤 | 证据回溯和导出入口可能暴露跨租户或已删除资源元数据 | P0 |
| 11-04 OpenAPI 与 API Key | `/openapi/v1/rag/query`、`/openapi/v1/rag/graph-query`；`/openapi/v1/knowledge-bases`、`/openapi/v1/ingestion/options`、`/openapi/v1/ingestion/tasks/{task_id}`、`/openapi/v1/ingestion/upload`；上传接口支持切片策略、教材类型、教材排版、解析管道请求元数据、自动确认和清洗 / 质检 append 提示词；统一 `requestId` 和错误格式；Bearer / `X-API-Key` 鉴权；API Key hash 存储、创建、禁用、轮换、软删除、过期、知识库绑定、能力范围校验；`requireSignature`、`allowedIps`、HMAC 强签名、timestamp、nonce 防重放、body hash、强制签名入口、API Key 级 IP 白名单；API Key 级 `rpmLimit` / `dailyRequestLimit` 已通过 `kb_api_key_usage_windows` 强制拦截；通用调用方 app 列表 / 创建 / 更新 / 删除已接入控制台；签名失败 / IP 拒绝 / 超限等 OpenAPI 认证失败已写脱敏审计日志；API Key 和 app 生命周期已写脱敏审计日志；已有 HMAC 示例脚本 | API Key 明文只在创建/轮换响应返回一次；默认能力为 `rag.query` / `rag.graph_query`；新建 Key 默认要求强签名；单 Key 最多绑定 20 个知识库；AI 基座只是当前可能调用方之一 | 并发阈值、月度配额、更细 app 使用报表、多语言正式 SDK 包、上传 multipart 完整原始 body 签名口径，以及解析管道单次真实覆盖 | OpenAPI 强验证、app 生命周期、分钟/日配额强制闭环已落地；生产级并发治理、月度配额、报表和 SDK 仍不完整；不能把 OpenAPI / API Key 写死为 AI 基座专用能力 | P0 |
| 11-05 配置中心治理 | 配置项返回 `configScope`、`effectiveMode`、`governance` 元数据；敏感掩码值回传时不误保存；保存失败显式 503；配置保存成功已写脱敏审计日志 | 先按 `hot` / `gray` / `ops` 标记生效边界 | 全局 / 知识库 / API Key 三层配置模型、Prompt Profile、版本化、灰度、回滚、权限与签名/IP 联动 | 当前更像“配置可见性、防误写与基础审计”，不是完整配置治理 | P1 |
| 11-06 日志、监控、用量 | `kb_rag_query_logs` 脱敏查询日志；RAG / Graph RAG 返回 `requestId`；日志中心列表、筛选、CSV 导出；查询日志 CSV 导出已写 `query_logs.export` 脱敏审计事件；`kb_llm_call_logs` 模型调用明细表；Token 统计页按 `pipeline_domain` / stage 展示 `pipelineStages` 和 `llmCalls`；入库 chunk / embedding / clean / quality 阶段已写入模型调用明细；在线问答 generation / rerank LLM check / evaluation score 可按真实 token metrics 写入明细；OpenAPI 普通问答显式归入 `openapi` domain；`kb_token_usage_hourly` 小时级 rollup、费用估算、quota 告警 payload 和前端趋势图 / 治理摘要已落地 | 日志写入 best-effort，不阻断主查询；查询日志导出只包含脱敏字段；在线 RAG 仍保留 `kb_rag_query_logs` 兜底；费用估算默认按环境变量费率计算，未配置时为 0；quota 当前只告警不强制拦截；部分链路仍以阶段汇总方式写入明细 | 托管解析 provider 的真实 token / 费用采集、Graph RAG 内部 LLM 逐次采集、API Key 维度强制阈值 / QPS / 并发限制、日志保留自动清理、短期敏感原文调试留痕开关 | 已有日志、基础审计、小时 rollup、费用估算和告警展示可追踪，但留存治理、强制 quota 和未暴露 token metrics 的调用点仍不完整 | P1 |

## 已完成但容易被误解的点

- 菜单已按 BRD 功能域重组，并补了 `identity-monitor`、`api-keys`、`openapi`、`logs`、`usage` 等治理入口；其中“身份与权限同步”入口现在只作为身份 / 访问治理状态页和同步日志跳转，不再沿用“SSO 延后、本地快照登录、请求头解析”为主的旧说明；但入口存在不等于对应后端治理能力完整实现。
- 知识库 ID 已从名称/拼音派生改为不暴露业务语义的 24 位小写 hex，例如 `6a30fe65b0b256647e733f4b`；历史 ID 和 `default` 兼容保留。
- API Key 生命周期和 OpenAPI 强验证最小闭环已落地，不再只是 OpenAPI 路由壳；新建 Key 默认要求 HMAC 强签名，并校验 timestamp、nonce、body hash 和 IP 白名单。当前控制台 OpenAPI 文档页已按 AI 基座用户端场景整理为“API 总表 + 单接口详情”，普通 RAG / Graph RAG、知识库列表、上传入库、入库任务、入库可选项、清洗提示词追加和质检提示词追加均已完成最小后端接口。QPS、配额、并发强制、SDK 示例、解析管道单次真实覆盖和 multipart 完整原始 body 签名口径仍未完成。
- OpenAPI / API Key 是通用第三方接入能力，不是 AI 基座专用能力；后续第三方系统应复用同一 app/API Key/能力范围/安全策略模型接入。
- 日志中心筛选与 CSV 导出已落地，查询日志 CSV 导出已写入 `query_logs.export` 脱敏审计事件；但导出授权仍依赖当前临时身份上下文，尚未形成正式 SSO session 下的完整子资源授权边界。
- AI 基座采用 JWT 登录机制时，推荐仍由 AI 基座内部校验 JWT 后给知识库发放一次性 `sso_code`；如必须让知识库消费 JWT，也只能作为后端一次性交换 / 校验凭证，交换后转为知识库本地短 session。当前 RAG 侧已实现后端 exchange、一次性凭证指纹防重放和本地 session 闭环；JWKS 本地验签 fallback、`jti` 实时吊销查询和完整审计仍未落地。
- 租户端真实 SSO 路径、`launch client_id`、RAG 侧随机 `state` / cookie 校验、`exchange Result.data` 解包、当前用户刷新和 HTTP `delta` 同步已经落地并有回归测试；仍未闭合的是在 SSO `superManager` 授权边界下的真实同步运行记录、失败重试、快照新鲜度强制窗口、访问拒绝审计和单点退出回调。

## 后续优先级 backlog

### P0：生产安全与租户隔离闭环

1. 正式 AI 基座 SSO 硬化：真实接口路径、`launch client_id`、RAG 侧随机 `state` / cookie 校验、`Result.data` 解包、HTTP `delta` 拉取、`last_sync_at/max_updated_at` 增量同步、登录时兜底刷新、SSO `superManager` 受控触发、用户及权限同步日志、真实 `http_delta` 全量同步复核、快照新鲜度强制窗口和 AI 基座单点退出回调已接入并验证；下一步补齐失败重试、统一访问判定、访问拒绝审计全覆盖、生产环境关闭旧请求头兜底验收和历史知识库归属迁移检查。`jti` 吊销查询仅在后续重新启用 JWT 兼容主路径时进入实现范围。
2. 统一访问判定：把知识库、文档、任务、图谱、评测、导出、日志、配置、API Key 管理统一接入后端访问判定，而不是只在部分路由点状调用。
3. OpenAPI 强安全：HMAC 签名、timestamp、nonce 防重放、body hash、IP 白名单、强制签名入口、认证失败脱敏审计、AI 基座用户端入库类开放接口、通用调用方 app 模型、API Key 分钟请求上限和每日请求配额强制拦截已完成后端最小闭环；下一步补并发限制、月度配额、更细 app 使用报表、SDK 示例、上传 multipart 完整原始 body 签名口径和解析管道单次真实覆盖。对接契约见 [external-governance-integration-contract.md](./external-governance-integration-contract.md)。
4. 高危操作审计：API Key 创建 / 更新 / 轮换 / 删除、配置变更和查询日志 CSV 导出已写脱敏审计记录；知识库删除/恢复、文档删除、文档 CSV 导出、访问拒绝等仍需补审计。
5. 子资源权限补齐：文档详情、源文件下载、文档 CSV、知识库图谱、文档图谱、入库任务 SSE、草稿编辑/合并/确认、评测记录全部按所属知识库裁剪。
6. 身份快照初始化与迁移收口：历史知识库 `tenant_id`、`owner_user_id` 和失效用户接管/转交路径补齐后，才能让正式 SSO 真正替代临时请求头身份。

### P1：治理报表、用量与配置闭环

1. 扩展 `kb_llm_call_logs` 采集面：表结构、写入 helper、Token 统计 API 和入库 chunk / embedding / clean / quality、在线问答 generation / rerank LLM check、OpenAPI 普通问答、评测打分等可验证调用点已落地；仍需继续补托管解析 provider、Graph RAG 内部 LLM 以及其他未暴露 token metrics 的逐次采集。
2. `kb_token_usage_hourly` 已建表并接入增量 upsert / 可重建 rollup，按租户、知识库、API Key、pipeline domain、stage 做小时级聚合；后续需要补日 / 月聚合、留存策略和大数据量性能验证。
3. Token 统计 API 和前端已返回 / 展示费用估算、小时趋势和 quota 告警；API Key 已支持分钟请求上限和每日请求配额强制拦截，仍需增加并发限制、月度 token / 调用量配额、强制阈值策略和禁用策略。
4. 配置中心补全 Prompt Profile、版本化、灰度发布、回滚和权限 / 签名 / IP 联动；配置变更基础审计已落地。
5. 补失效用户的知识库接管、转交、待认领和恢复流程。
6. 日志保留、导出文件 24 小时清理、冷存储和短期敏感原文调试开关。

### SSO 后续对接切片

1. `launch / exchange / user snapshot / delta` 四个路径已进入服务端配置和控制台设置分组，且默认值已对齐租户端当前文档；仍建议在联调期由 AI 基座租户端和知识库双方一起确认最终网关前缀与回调域名，避免后期代理层重定向。
2. `exchange` 响应已经按 `Result.data` 解包，后续仍需按租户端真实错误码补更细粒度映射和联调验证。
3. `delta` 同步已实现 HTTP 拉取、`max_updated_at` 水位记录和 `deleted` 数组处理；下一步需要补齐 SSO `superManager` 真实触发后的运行记录复核、失败重试策略和快照新鲜度强制窗口。
4. 登录成功后仍建议补“当前用户刷新”接口与本地快照新鲜度检查，便于后台敏感操作在身份过旧时拒绝。
5. 若后续仍保留 JWT 兼容模式，再补 JWKS 本地验签 fallback 与 `jti` 吊销查询；如果租户端只保留 `sso_code` broker，则可把 JWT 支持降级为文档说明，不进入主线实现。
6. 在 SSO 真正切到生产态前，先把访问拒绝审计、退出回调和历史知识库归属迁移做完，否则只能算“可登录但不可完全治理”。

### P2：体验与对外交付完善

1. 控制台 API 文档页已补“API 总表 + 单接口详情 + curl 示例 + 成功/错误响应 + 强签名判断规则”；后续仍需补 SDK 片段和生产联调 checklist。
2. 告警中心补错误聚合、API Key 超阈、LLM 调用失败、日志写入失败和导出风险提示。
3. 补运行手册：如何从临时请求头身份切到正式 SSO，如何在 code broker 与 JWT 一次性交换两种对接形态之间选择，如何从基础 API Key 升级到强签名模式。目标协议已先沉淀到 [external-governance-integration-contract.md](./external-governance-integration-contract.md) 和 [ai-base-sso-integration-guide.md](./ai-base-sso-integration-guide.md)，实现前需再补实际部署、密钥轮换、JWKS 缓存刷新和网关配置步骤。

## 建议的下一步切片

如果继续按“不依赖 AI 基座 SSO 的独立模块”推进，建议先做：

1. 子资源权限收口：文档、任务、图谱、导出、评测这些已有 API 的 KB 访问裁剪。
2. API Key 强安全第二段：在已补 HMAC、timestamp、nonce、body hash、IP 白名单、认证失败审计、通用 app 模型、分钟请求上限和每日请求配额强制拦截的基础上，继续补并发限制、月度配额、更细 app 使用报表、SDK 示例和 multipart 完整原始 body 签名口径。
3. 日志用量第二段：在已补小时级 rollup、费用估算、quota 告警、图表展示和导出审计的基础上，继续接入托管解析 provider、Graph RAG 内部 LLM 调用点，并补日 / 月聚合、留存策略、API Key 并发限制和月度配额。

如果要进入生产登录闭环，则应优先回到 11-01，完成正式 SSO code broker 或 JWT 一次性交换兼容、后端 session 和身份快照新鲜度判断，再继续扩大治理面。

## 2026-06-23 更新：Token 明细化统计状态

11-06 的 Token 统计已完成一轮结构性补强，但仍不是完整计费闭环：

- 已新增 `kb_llm_call_logs`，用于承接真实模型调用明细。
- `/api/console/token-usage` 已升级为按功能环节返回 `pipelineStages` 和 `llmCalls`，并保留 `kb_rag_query_logs` 作为在线 RAG 总量兜底。
- 前端 `/usage` 已改成按环节展开，明确展示解析、清洗、切片、质量审核、向量化、重排、问答生成、评测等链路的明细采集状态。
- 平台超级管理员可查看所有租户、所有知识库、所有环节的消耗明细；普通已登录身份默认按当前租户过滤。
- 仍待完成：清洗、质量审核、在线问答生成、OpenAPI、评测等可取得 token metrics 的调用点，以及小时级 rollup、费用折算、quota 告警、图表统计和导出审计已在后续切片补齐；当前仍需继续补托管解析 provider、Graph RAG 内部 LLM 等尚未暴露 token metrics 的逐次采集，并建设日 / 月聚合、留存策略和更细治理报表。

因此，P1 backlog 中 `kb_llm_call_logs` 的状态应理解为“数据表与 API 契约已落地，完整采集链路待接入”，而不是已经具备完整成本核算能力。

## 2026-06-24 更新：Token rollup、费用与审计状态

本轮已完成并通过回归验证的闭合项：

- `kb_token_usage_hourly` 已接入 `append_llm_call_log()` 同事务增量 upsert，并提供 `refresh_token_usage_hourly()` 从 `kb_llm_call_logs` 重建指定时间窗口。
- `/api/console/token-usage` 已返回 `hourlyUsage`、`costSummary`、`quota`、`quotaAlerts`，前端 `/usage` 已展示小时趋势、费用估算、quota 状态和告警。
- 费用估算通过 `KB_TOKEN_COST_PER_1K_PROMPT`、`KB_TOKEN_COST_PER_1K_COMPLETION`、`KB_TOKEN_COST_PER_1K_TOTAL` 和 `KB_TOKEN_COST_CURRENCY` 配置；未配置时费用为 0，不影响调用。
- quota 当前只做统计和告警，沿用 BRD 中“第一期不强制拦截”的边界。
- 清洗、质量审核、在线问答 generation / rerank LLM check、OpenAPI 普通问答和评测打分等可从现有 metrics 取到 token 的调用点已接入 `kb_llm_call_logs`；Graph RAG 查询本身仍只写 query log，除非后续内部链路暴露真实 LLM token metrics。
- 查询日志 CSV 导出已写入 `query_logs.export` 脱敏审计事件。

验证记录：

- `python -m py_compile core/db/query_logs.py backend/services/rag_service.py backend/services/ingestion_service.py backend/services/console_service.py backend/routes/console.py backend/routes/openapi_v1.py`
- `pytest tests/test_query_logs.py tests/test_api_console.py tests/test_backend_app.py tests/test_openapi_v1.py tests/test_api_keys.py tests/test_identity_access.py -q`：97 passed
- `frontend npm run typecheck`：通过

仍未闭合：

- 托管解析 provider 的真实 token / 费用明细尚未验证，不能标记完成。
- Graph RAG 内部如果后续接入 LLM rerank / generation / scoring，需要在对应 metrics 暴露后再写逐次明细；当前仅保留 Graph RAG query log。
- 日 / 月聚合、日志保留自动清理、API Key 维度并发限制、月度配额和更细使用报表仍是后续治理项；API Key 分钟请求上限和每日请求配额已在后续切片强制拦截。

## 2026-06-24 更新：AI 基座 SSO 放行后复核

本轮按 AI 基座已放行后的运行时状态复核 11-01 遗留项，结论如下：

- RAG 登录页 `/login?next=%2Fevaluation` 的“打开 AI 基座 SSO”入口仍指向 RAG `/api/auth/ai-base/launch`，由 RAG 生成随机 `state` 并写入 `kb_sso_state` HttpOnly cookie，再 302 到 AI 基座租户端 `/sso`。
- 后端日志已出现完整成功链路：`/api/auth/ai-base/launch` 302、`/api/auth/ai-base/callback?code=...&state=...` 302、`/api/auth/session` 200，后续知识库、概览、入库、评测、设置、API Key、日志等控制台接口返回 200。
- `kb_auth_sessions` 已写入多条 `auth_source=ai_base_sso_authorization_code` 的短 session，最新记录包含 `tenant_id=1`、`user_id=100`、`username=admin0001`、`display_name=管理员`、`tenant_name=中教智汇` 和 AI 基座返回的 `identity_snapshot_version`。
- callback 创建 session 时会调用 AI 基座 exchange，并把 exchange 返回的当前身份摘要落入本地只读身份快照；因此“SSO 认证通过并建立知识库本地 session”可以按当前联调口径标记闭合。
- 运行时日志未出现 `POST /api/auth/ai-base/refresh-current-user` 或 `POST /api/identity/sync-delta`；前端 API client 也没有这两个接口的自动调用方。
- `kb_identity_sync_runs` 当前仍只有 2026-06-18 的 `mysql_bootstrap` 记录，没有 `sync_mode=http_delta` 的成功或失败运行记录；这说明 HTTP delta 接口能力已实现，但尚未由真实 AI 基座 SSO `superManager` 通过日志页入口或 `POST /api/identity/sync-delta` 实际触发。后台 scheduler 在无当前授权上下文时只记录 / 返回 `skipped`，不能绕过该门禁直接入库。

据此，Phase 11 的身份链路状态应拆开理解：

- 已闭合：正式 SSO launch / callback / exchange / 本地 session、当前登录用户摘要落快照。
- 2026-06-26 复核更新：真实 SSO `superManager` 已触发 `http_delta` 全量同步并成功入库，当前用户刷新、HTTP delta 拉取、水位记录、受控触发入口、用户及权限同步日志、快照新鲜度强制窗口和 AI 基座单点退出回调已形成后端闭环。
- 仍未闭合：身份同步失败重试、访问拒绝审计全覆盖、生产环境请求头兜底关闭验收、历史知识库归属迁移检查，以及依赖正式身份的子资源权限全覆盖。

## 2026-06-24 更新：身份同步调度与请求头兜底开关

本轮在前述复核基础上补齐了一个后端最小治理切片：

- 新增 `KB_LEGACY_HEADER_AUTH_ENABLED` runtime/env 开关，默认 `true` 兼容本地开发和历史联调；生产切正式 SSO 后可设为 `false`，仅携带 `X-KB-Tenant-Id` / `X-KB-User-Id` 的请求会被 401 拒绝，不再继续读取本地身份快照。
- 新增 `AI_BASE_IDENTITY_SYNC_ENABLED`、`AI_BASE_IDENTITY_SYNC_INTERVAL_SECONDS`、`AI_BASE_IDENTITY_SYNC_RUN_ON_STARTUP`，用于让后端暴露可选调度状态；按最新权限边界，后台任务无当前 SSO 超级管理员上下文时只记录 `skipped` 状态，不直接执行五类 delta 入库。
- 新增管理员只读接口 `GET /api/identity/sync-status`，返回调度是否启用、是否运行、间隔、最近水位、最近一次运行状态和错误信息，便于判断 HTTP delta 是否真的进入运行闭环。
- `/api/auth/ai-base/config` 的 `legacyHeaderFallback` 现在反映真实开关值；控制台设置分组和 env 模板也暴露了上述开关。

因此，身份 HTTP delta 已从“接口能力已实现但没有编排入口”推进为“有后端受控触发入口与状态观测”。仍未闭合的是：真实环境中由 SSO 超级管理员触发后的运行记录复核、快照新鲜度强制窗口、访问拒绝审计、AI 基座单点退出回调，以及依赖正式身份的全量子资源权限收口。

## 2026-06-24 更新：用户及权限同步日志与触发边界

本轮按联调确认继续收口身份 delta 同步：

- `last_sync_at` 首次同步允许为空；后续使用本地最近一次成功运行记录中的 `max_updated_at`。传给 AI 基座 delta 接口以及写回 `kb_identity_sync_runs` 的格式统一为 `YYYY-MM-DD HH:mm:ss`。
- 日志管理新增“用户及权限同步”页签，同步成功后刷新 `GET /api/console/identity-sync-logs?limit=100`，数据源为 `kb_identity_sync_runs`，展示每次同步的租户、用户、角色、用户角色、删除事件数量、`last_sync_at`、`max_updated_at`、快照版本、状态和失败原因。
- 日志页提供“立即同步”入口，用于触发 `POST /api/identity/sync-delta` 后刷新同步日志。
- 五类 delta 入库动作只允许 SSO session 来源且当前身份命中 `role_code=superManager` 时执行；平台管理员身份不再作为用户及权限同步触发条件，旧 `X-KB-Tenant-Id` / `X-KB-User-Id` 请求头即使能解析出管理员身份，也不能触发五类 delta 入库。

如果日志页看不到租户侧同步数据，优先排查两点：一是 `POST /api/identity/sync-delta` 是否由 AI 基座 SSO `superManager` 成功触发；二是 AI 基座 delta 返回体中的 `tenants/users/roles/user_roles/deleted` 是否为空。后台 scheduler 无当前授权上下文时只会 `skipped`，不代表租户侧同步已运行。RAG 侧已对这五类数组做幂等 upsert / 删除状态处理，并把每轮计数落到同步日志。

## 2026-06-25 更新：用户及权限同步复核

本轮按“租户侧是否真的产生同步数据”复核运行库与代码边界：

- 当前运行库 `kb_identity_sync_runs` 仍只有 1 条 `mysql_bootstrap`，没有 `sync_mode=http_delta` 的成功或失败记录；本地快照表已有 `1` 个租户、`4` 个用户、`11` 个角色和 `14` 条用户角色关系，这些来自历史初始化，不代表 AI 基座 delta 已跑通。
- 后端日志能看到日志页读取 `GET /api/console/identity-sync-logs?limit=100`，但未看到 `POST /api/identity/sync-delta`；因此日志页为空或没有租户侧增量数据时，首要原因是尚未由 SSO `superManager` 触发一次真实同步。
- `POST /api/identity/sync-delta` 已收紧为仅允许 AI 基座 SSO session 且 `roleCodes` 包含 `superManager` 的身份触发；平台管理员、普通用户和旧请求头身份都不能执行五类 delta 入库。
- 日志页“用户及权限同步”页签继续通过 `GET /api/console/identity-sync-logs?limit=100` 读取 `kb_identity_sync_runs`；“立即同步”按钮按当前身份禁用，只有 AI 基座 SSO 登录的 `superManager` 可触发。
- 左侧“身份与权限同步”菜单已同步到当前 SSO + 用户及权限同步日志口径，只作为身份 / 访问治理状态入口；五类 delta 入库触发仍只在日志页按钮或 `POST /api/identity/sync-delta` 执行。
- `last_sync_at` 调用规则保持：首次没有成功水位时传空字符串，后续自动取最近一次成功运行的 `max_updated_at`；传给 AI 基座和落库展示统一格式为 `YYYY-MM-DD HH:mm:ss`。
