# Bug & 问题追踪

> 记录已确认缺陷、阻塞、回归和设计性结论。
> 本文件允许 `human`、`codex`、`claude`、`gsd` 共享维护；保留可确认的历史 ID，不可靠乱码记录不做猜测还原。

---

## 状态说明

- `阻塞`：影响核心功能，必须优先处理。
- `待处理`：功能可用但仍有缺口。
- `已修复`：问题已解决并有验证记录。
- `设计结论`：经过确认的预期行为，不作为 bug 处理。

---

## 编码与文案

| # | 状态 | 问题描述 | 发现时间 | 修复说明 |
|---|------|---------|---------|---------|
| ENC-01 | 已修复 | 全项目存在多处可见中文乱码和测试/样例数据占位问号，影响控制台、共享账本和后端观测逻辑可读性。 | 2026-06-23 | 已完成 `frontend/src` 可见乱码清理；本轮继续清理后端解析进度识别、RAG enhanced 标记、生成器测试样例、评测样例数据、`todo.md` 历史占位行，并重建本文件为可读账本。 |
| ENC-02 | 设计结论 | PowerShell `Get-Content` 在当前环境下可能把 UTF-8 中文显示成乱码，但文件内容本身可能是正常 UTF-8。 | 2026-06-23 | 后续判断编码问题应以 UTF-8 读取、码点扫描和运行时页面显示为准，不仅凭终端肉眼显示判断。 |
| ENC-03 | 已修复 | AI 基座用户及权限同步门禁曾出现文档与实现口径不一致：旧表述误写为平台管理员也可触发 `sync-delta`，容易让联调和日志页按钮误判。 | 2026-06-25 | `backend/routes/identity.py`、日志页“立即同步”按钮与 Phase 11 / SSO / BRD / 11-01 planning 文档已统一收紧为仅 AI 基座 SSO 的 `superManager` 可触发；`backend/services/ai_base_sso_service.py` 已移除旧 `next_cursor` 水位回退，只保留 `last_sync_at/max_updated_at` 主合同；用户及权限同步日志数据源统一为 `kb_identity_sync_runs`；复核当前运行库仍无 `http_delta`，需真实同步后才会出现租户侧日志。 |

---

## 控制台界面

| # | 状态 | 问题描述 | 发现时间 | 修复说明 |
|---|------|---------|---------|---------|
| UI-20 | 已修复 | 在线 RAG 评测中忠实度容易持续显示 `0.0`：当 LLM 答案漏写 `[n]` 引用编号时，即使召回上下文有效也会被记为无引用。 | 2026-06-11 | `core/rag/generator.py` 在 LLM 输出无引用但已有高置信上下文时，复用 extractive fallback 生成带引用编号的答案；已补 `tests/test_generator.py` 回归。 |
| UI-21 | 已修复 | parse stage 的 `shardCount` / `completedShards` 容易被 `396 pages`、`40 pages per shard` 以及无 `shard_ref` 的 `merge complete` 日志污染，导致控制台进度和统计误判。 | 2026-06-11 | `backend/services/ingestion_service.py` 新增 `_extract_declared_shard_count()`，只读取紧邻 `shards` 的声明数量，跳过 `pages per shard`；无 `shard_ref` 的 done 日志不再盲目累加 `completedShards`。 |
| UI-22 | 已修复 | 同一超级管理员在系统总览能看到全局知识库数量，但知识库管理显示 0；总览统计和知识库列表身份过滤口径不一致，且历史 `tenant_id IS NULL` 知识库被租户管理员误过滤。 | 2026-06-22 | 总览统计传入当前身份并与知识库列表保持同一口径；租户管理员可见同租户及历史未绑定租户知识库，普通用户仍限制到 owner。 |
| UI-23 | 已修复 | 知识库已有单库知识图谱功能，但控制台缺少全局入口，用户只能在深层单库路径中发现图谱能力。 | 2026-06-22 | 在“知识库管理”下新增“知识图谱”菜单与移动端入口，新增全局知识图谱入口页并跳转到各知识库原有 `/graph` 图谱页。 |
| UI-24 | 已修复 | 控制台出现大面积中文乱码，影响全局导航、知识库工作台、系统总览、按钮、指标卡和说明文案。 | 2026-06-23 | 已完成 `frontend/src` 可见乱码整体清理；`frontend npm run typecheck` 通过，且扫描未发现 Unicode 替换符或私用区字符残留。 |
| UI-25 | 已修复 | 新建知识库已改为 24 位 hex 随机 ID 后，进入单库文档、入库、问答、评测等子页可能显示 `This page couldn’t load`；根因是单库动态 layout / 页面仍在服务端拉取全量知识库列表并用旧 helper 反查路由 ID，接口失败会拖垮整个页面渲染。 | 2026-06-23 | 单库 layout、总览、文档、入库、问答、评测、图谱页均改为直接 `decodeKbId(routeKbId)`；总览和图谱页对文档 / 评测 / 图谱接口失败降级为空数据；删除旧 `resolveKnowledgeBaseId` helper，并补 hex ID 过滤回归测试。 |
| UI-26 | 已修复 | 控制台从 `http://192.168.2.208:3000` 请求 `http://192.168.2.208:8000/api/*` 时被 CORS 预检拦截，报 `x-kb-user-id is not allowed`；实际探测发现宿主机 `8000` 返回 C-Lodop 打印服务页面，不是 RAG 后端，同时后端 CORS 头缺少显式身份头白名单。 | 2026-06-23 | 后端 CORS 改为显式允许 `X-KB-Tenant-Id` / `X-KB-User-Id` / `X-API-Key` 等请求头并支持 `KB_CORS_ALLOW_HEADERS` 覆盖；Docker 后端对外端口改为 `8001`，前端 `NEXT_PUBLIC_API_BASE_URL` 和 SSO 回调同步到 `http://192.168.2.208:8001`；已重建 backend/frontend 容器并验证 `192.168.2.208:8001` OPTIONS 预检通过。 |
| UI-27 | 已修复 | 入库管理产品化二次验收发现第 1 条仍有遗漏：全局上传请求期间还未显示“文件正在上传中...”遮罩，单库入库工作台仍通过 SSE `log` 事件累积并展示“实时日志”。 | 2026-06-23 | 全局 `/ingestion` 上传请求期间新增固定遮罩；单库 `IngestionWorkspace` 移除 `logLines` 状态、SSE `log` 监听和“实时日志”面板，仅保留阶段进度和友好状态；同时将单库 SSE 默认后端端口从 `8000` 对齐为 `8001`。验证 `frontend npm run typecheck`、`frontend npm run build` 通过，并扫描 `frontend/src` 无 `实时日志` / `logLines` 残留。 |
| UI-28 | 已修复 | 在线 RAG snapshot 召回分数尺度错误导致默认 `min_score=0.3` 过滤真实 dense/BM25 文本命中，只剩 related 图片候选；同时 LLM 生成失败时即使已有高置信上下文也会拒答，忠实度和 LLM 评分展示容易表现为空。 | 2026-06-24 | `core/rag/retrieval_snapshot.py` 保留 dense/BM25 相关性量级并将 RRF 作为加成，related 图片降权且同页去重；`core/rag/generator.py` 在高置信上下文下提供带引用的抽取式兜底；`core/rag/scorer.py` 修正忠实度为有效引用占比；前端问答请求启用可选 LLM 评分并对未评分显示“未评分”。已验证真实知识库 `7cbc7f0b2d7449188ae71b48` 查询“针灸学的发展”Top 8 均为文本、`cannotAnswer=false`、忠实度 `1.0`。 |
| UI-29 | 已修复 | “身份与权限”菜单仍沿用旧的本地快照 / 请求头主口径说明，用户及权限同步成功后应从同步日志接口复核运行记录，容易让菜单入口和真实同步入口混淆。 | 2026-06-25 | 左侧菜单改为“身份与权限同步”，页面说明改为 SSO + 用户及权限同步日志 + 访问治理状态；同步日志默认读取最近 100 条，接口口径为 `GET /api/console/identity-sync-logs?limit=100`。 |
| UI-30 | 已修复 | “身份与权限同步”入口只显示说明型占位内容，没有展示用户及权限明细表；同时 AI 基座 delta 若返回 camelCase / `xxxList` 字段，可能导致租户计数正常但用户、角色、用户角色计数为 0。 | 2026-06-25 | `/identity-monitor` 已改为真实表格，展示租户、用户、原始角色、RAG 角色、同步方式、同步时间；身份快照接口补充 `roleNames`、`ragRole`、`syncedAt`；delta 落库兼容 `tenantList/userList/roleList/userRoles` 和 camelCase 删除事件，已补回归测试。 |
| UI-31 | 已修复 | 真实 SSO `superManager` 触发 `http_delta` 后，AI 基座 delta 首轮观测一度只有 tenants，users、roles、user_roles 为空，导致页面用户和角色计数看起来“不对”。 | 2026-06-26 | 已按 AI 基座租户端约定清空身份快照后使用首次水位 `2000-01-01 00:00:00` 重新同步，并将 delta HTTP timeout 放大到 300 秒；本次成功拉取并入库 tenants=52、users=69278、roles=227、user_roles=70755，原始 payload 为 users=69278、roles=227、user_roles=70805、deleted=91。此前为空/不完整主要是全量同步耗时超过先前等待窗口导致的观测偏差；保留脱敏结构诊断日志、接口 `diagnostics` 和 `kb_identity_sync_runs.source_schema` 摘要用于后续排查。 |

---

## 解析与入库

| # | 状态 | 问题描述 | 发现时间 | 修复说明 |
|---|------|---------|---------|---------|
| P-09 | 已修复 | Document Mind 最新一次解析变差：LLM 增强仍为关闭，但 Docker backend 仍使用 6-key pool；4 worker 稳态下继续探测/使用未知慢 key，导致慢 key 进入解析路径。 | 2026-06-16 | `core/parser/document_mind_parser.py` 新增 `parseKeyActiveTarget` 调度目标；sharded parse 按实际 worker 数设置 active target，目标满足后停止探未知 key；`.env` 收敛到当前 4 个已验证 Document Mind key。 |
| P-10 | 已修复 | 入库链路三层切片增强 / embedding 已发生 LLM 或模型调用，但 Token 统计没有体现；用户反馈知识库 `7cbc7f0b2d7449188ae71b48` 在 2026-06-23 13:50 左右使用了 LLM 增强却未统计。 | 2026-06-23 | Token 统计改为优先从 `kb_llm_call_logs` 聚合并支持 `pipeline_domain` 过滤；入库 chunk / embedding 阶段完成后会按阶段汇总写入 LLM 调用明细，包含 provider、model、tokens、latency、kb_id 和 request_id；已补 console/token usage 回归测试。历史未采集明细的任务需另做回填才会出现逐次调用记录。 |
| P-11 | 已修复 | 配置中心展示了清洗模型和提示词，但入库清洗阶段实际调用 `clean_blocks(blocks)`，默认 `use_llm=False`，因此只走规则清洗，不会产生清洗 Token；质量审核模型也只展示系统提示词，缺少启用开关、模型、base_url、api_key 和评分阈值配置。 | 2026-06-26 | 已新增 `LLM_CLEANER_ENABLED`、`LLM_QUALITY_GATE_ENABLED`、`LLM_QUALITY_GATE_MODEL`、`LLM_QUALITY_GATE_BASE_URL`、`LLM_QUALITY_GATE_API_KEY`、`LLM_QUALITY_GATE_MIN_SCORE` 配置；入库清洗 / 质检阶段现在从配置中心读取开关和模型参数，默认仍为关闭以避免意外 Token 消耗，开启后写入 `kb_llm_call_logs`；配置中心补齐质量审核模型字段，前端治理菜单按 `superManager` 过滤。验证：`pytest tests/test_api_console.py tests/test_backend_app.py tests/test_runtime_settings.py -q` 77 passed，`frontend npm run typecheck` 通过。 |

---

## 历史记录说明

2026-06-23 之前，本文件中大量早期条目已经发生 UTF-8/GBK 错解并混入私用区字符，无法从现有文本可靠恢复原意。为避免继续传播错误信息，本轮不逐条猜测还原旧记录，只保留近期可从代码、测试和共享账本中确认的条目。需要追溯更早问题时，请以 git 历史、相关 phase `SUMMARY.md`、`todo.md` 和代码提交为准。

---

## 统计

| 类别 | 总数 | 已修复 | 待处理 | 设计结论 |
|------|------|--------|--------|---------|
| 编码与文案 | 3 | 2 | 0 | 1 |
| 控制台界面 | 11 | 11 | 0 | 0 |
| 解析与入库 | 2 | 2 | 0 | 0 |
| **合计** | **16** | **15** | **0** | **1** |

---

*最后更新：2026-06-26*
## 2026-06-25 补充

| # | 状态 | 问题描述 | 发现时间 | 修复说明 |
|---|------|---------|---------|---------|
| ENV-01 | 已修复 | Claude Code / Cursor / VSCode 在 Windows + Git Bash 环境下曾误命中 Microsoft Store 的 `python3` 占位程序，导致 `UserPromptSubmit hook error`。 | 2026-06-25 | 已在项目根新增 `python3.cmd` 指向 `C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python311\\python.exe`，并在 `.claude/settings.json`、`.vscode/settings.json`、`.cursor/settings.json` 中收口 Python 3.11 路径与 UTF-8 环境变量；若 IDE 未立即生效，先重载工作区并重开终端。 |
