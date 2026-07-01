# AGENTS.md

<!-- GSD:project-start source:.planning/PROJECT.md -->
## Project

`wisewe-rag-simple` 是一个围绕教材 / 文档知识库构建与 RAG 验证的项目，当前范围已经从最初的 CLI 技术验证扩展到完整链路：

- PDF / 文档解析
- 清洗与分块
- 向量化与存储
- 在线检索与问答
- 前端控制台与证据回溯

当前仓库同时保留：

- `src/` 中的 Python / RAG 主链路
- `frontend/` 中的 Next.js 控制台
- `.planning/` 中的 GSD 规划与阶段上下文

`.claude/` 和 `CLAUDE.md` 保留为历史参考，不作为 Codex 的运行前提。
<!-- GSD:project-end -->

<!-- GSD:stack-start source:README.md -->
## Technology Stack

- 后端 / 核心链路：Python
- 文档解析：MinerU 云端解析 + OSS 上传
- 向量化 / 检索：Embedding API + pgvector
- 前端：Next.js
- 规划体系：GSD（`.planning/`）
- 项目级 Codex 指令：`AGENTS.md` + `.codex/skills/`
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:repo -->
## Conventions

- 默认对用户使用简体中文，除非用户明确要求其他语言。
- 在本仓库工作时，优先把 `.claude/` 视为“历史方案参考”，不要把 ECC、Claude Agent、Claude Hooks 当成 Codex 的必需能力。
- 开始实现前，优先读取与任务直接相关的共享文档：
  - `todo.md`
  - `bug.md`
  - `docs/agent/agent-shared-docs.md`
  - `.planning/STATE.md`
- 如果用户明确指定 phase，或点名某个历史 milestone / workstream，再补读对应的 `CONTEXT.md`、`PLAN.md`、`SUMMARY.md`。
- 控制台 UI 规范以 `docs/design/previews/wisewe-rag-console-ui-preview.html` 与 `docs/design/system/MASTER.md` 为准，可见文案输出必须以简体中文为主。
- 不要因为切换到 Codex 就新造一套和仓库脱节的流程；优先复用现有 GSD 产物与项目上下文。

### 共享账本规则

- `todo.md` 是跨运行时共享的任务账本，允许人类、Codex、Claude、GSD 更新。
- `bug.md` 是跨运行时共享的问题账本，发现真实缺陷、阻塞或设计性结论时更新。
- 更新共享账本时：
  - 只改和当前任务直接相关的条目
  - 保留历史 ID，不随意重排旧记录
  - 不删除他人尚未确认关闭的信息
- `.planning/**` 默认仍由 GSD 主导；直接手改只限于用户明确要求或你在补全 GSD 约定文档时。
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:manual -->
## Architecture

建议按下面的层次理解仓库：

1. `src/`：解析、清洗、分块、向量化、检索、生成等核心能力
2. `frontend/`：控制台界面、Mock 数据、交互工作台
3. `docs/design/`：界面规范源文档与历史归档
4. `.planning/`：GSD 阶段计划、上下文、路线图、状态
5. `todo.md` / `bug.md`：跨代理共享账本

如果任务同时涉及代码、规划和共享文档，优先顺序是：

1. 保证实际代码和行为正确
2. 更新共享账本
3. 再同步 `.planning/` 或补充说明文档
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

| Skill | Description | Path |
| --- | --- | --- |
| `wisewe-rag-workflow` | WiseWe RAG 仓库专用工作流技能，定义共享账本、GSD 路由、最小读取顺序和 Codex 约束。 | `.codex/skills/wisewe-rag-workflow/SKILL.md` |
| `ui-ux-pro-max` | 现有项目级 UI / UX 设计技能，用于高质量前端与控制台界面任务。 | `.codex/skills/ui-ux-pro-max/SKILL.md` |
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

本项目在 Codex 下优先采用 `AGENTS.md + GSD + 共享账本` 的组合，不依赖 ECC。

### 推荐入口

- 小改动 / 文档更新 / 单点修复：`$gsd-quick --text`
- Bug 排查：`$gsd-debug --text`
- 已有 phase 的计划执行：`$gsd-execute-phase <phase> --text`
- 新 phase 讨论与规划：
  - `$gsd-discuss-phase <phase> --text`
  - `$gsd-plan-phase <phase> --text`
- 主线推进与状态收口：优先直接更新 `.planning/STATE.md`、`.planning/ROADMAP.md`、相关 milestone / phase 文档

### 参考手册

- 常用命令配方：`docs/codex-command-recipes.md`
- 旧 Claude 角色到 Codex 的职责映射：`docs/codex-role-mapping.md`
- 审查与发布前检查：`docs/codex-review-playbook.md`

### 在普通 Codex 对话中的等价执行

如果当前不是通过 GSD 命令启动，而是直接在 Codex 对话里处理任务，也要遵守同样纪律：

1. 先读共享账本和相关上下文
2. 再实施代码或文档修改
3. 结束时同步 `todo.md` / `bug.md`
4. 涉及主线 milestone / phase 目标变化时，再补 `.planning/` 文档

### 主线优先

本仓库的 GSD 工作流现统一收拢到项目主线：

- 新任务默认挂到主线 milestone / phase
- `workstreams/**` 只作为历史归档与引用材料
- 不再把“新建并行 workstream”作为默认组织方式

### 文本模式

本项目默认启用 `workflow.text_mode: true`，原因是 Codex 不依赖 TUI 弹窗或 AskUserQuestion 菜单，所有 GSD 交互统一退化为纯文本编号选择。
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

- 偏好简洁直接的沟通
- 偏好中文输出
- 偏好共享账本可追踪、少隐藏状态
- 不希望项目运行依赖只存在于某个单一代理生态中的插件
<!-- GSD:profile-end -->
