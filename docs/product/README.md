# 产品文档

本目录归档面向产品、验收、招标和对外交付表达的文档。这里不承接代码实现计划，也不记录性能调参过程。

## 当前文档

- [knowledge-base-governance-brd.md](./knowledge-base-governance-brd.md)：知识库治理、对外 API、前端管理后台、配置中心与日志管理的业务需求文档，明确知识库系统不维护可写用户、角色和权限主数据；身份数据第一期采用受控增量同步（仅 AI 基座 SSO 的 `superManager` 可触发）+ 登录时兜底刷新，仅同步 AI 基座租户、用户、角色、用户角色关系只读快照，用于展示、审计、租户隔离和管理员身份判断，并通过用户及权限同步日志复核运行记录；租户管理员 / 知识库管理员第一期优先按 `role_code=superManager` 判断，未命中则按普通用户的知识库当前业务归属权限处理，平台管理员角色独立确认；首次上线前必须完成 AI 基座身份快照初始化和历史知识库 `tenant_id`、`owner_user_id` 补齐；知识库访问按同租户、当前业务归属人可见可管、租户管理员本租户全量可管、平台管理员全局可管执行，第一期不做普通用户共享；用户禁用、离职或移出租户后不能继续访问，历史知识库不自动删除，由租户管理员接管、转移当前业务归属人或删除，`created_by` 保留审计，`owner_user_id` 表示当前归属；删除按所属知识库归属判断，知识库、文档、入库任务默认软删除，子资源立即对查询和召回不可见，日志、审计和 token 统计保留脱敏记录；API Key 调用知识库资源必须指定 `kb_id`，单个 API Key 默认最多绑定 20 个知识库，查询、上传、入库等能力按显式能力范围授权；生产环境默认强签名 + IP 白名单 + nonce 防重放，其他系统不得传输真实 API Key 明文；配置中心全局配置仅平台管理员可改，租户主数据只读同步，知识库配置可由当前业务归属人或管理员修改，API Key 配置仅管理员可改；文档已按功能域、数据库设计、系统级凭证安全、6 个迭代规划和链路归属 token 统计展开。
- [phase11-gap-analysis.md](./phase11-gap-analysis.md)：Phase 11 当前真实落地状态与延后关键点清单，区分已落地、快速贯通、未实现 / 延后、风险和建议优先级；已补充真实 SSO `superManager` 触发 `http_delta` 全量同步复核、身份同步页全量明细 / 筛选 / 清空入口、OpenAPI app 生命周期、API Key rpm / daily 配额强制拦截和脱敏审计状态，避免把历史待办或最小切片误认为当前生产治理边界。
- [ai-base-sso-integration-guide.md](./ai-base-sso-integration-guide.md)：AI 基座租户端 SSO 对接帮助文档，说明推荐的一次性 `sso_code` 跳转、AI 基座 JWT 一次性交换兼容模式、服务端换身份摘要、知识库本地短 session、JWT/JWKS 校验、身份快照增量同步、`role_code=superManager` 管理员识别、安全约束、错误码、日志审计和最小联调清单。
- [ai-base-identity-delta-sync-guide.md](./ai-base-identity-delta-sync-guide.md)：面向 AI 基座租户端开发的身份快照增量同步对接说明，细化 `last_sync_at/max_updated_at` 水位语义、首次空字符串、SSO `superManager` 触发边界、`GET /api/console/identity-sync-logs?limit=100` 同步日志复核、登录时兜底刷新、变更检测、删除 / 停用 / 用户角色移除事件、字段规范、快照新鲜度、重试、错误码和联调验收清单。
- [external-governance-integration-contract.md](./external-governance-integration-contract.md)：Phase 11 延后生产治理能力的横向对接契约，覆盖正式 SSO、OpenAPI HMAC 强签名、timestamp / nonce / body hash、IP 白名单、quota、审计、配置版本回滚和失效用户接管；该文档是目标契约，不代表当前代码已全部实现。
- [technical-bid-parameters.md](./technical-bid-parameters.md)：教材知识库与 RAG 产品技术招标参数，面向能力说明、验收项和产品亮点表述。

## 维护边界

- 技术实现细节以 `docs/pipeline/`、`docs/rule/` 和 `docs/architecture/` 为准。
- 性能基线和实验过程以 `docs/performance-optimizations/` 为准。
- 规划状态以 `.planning/STATE.md`、`.planning/ROADMAP.md` 和 phase 文档为准。
