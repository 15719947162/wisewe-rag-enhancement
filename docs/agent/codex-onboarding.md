# Codex 协作快速入门

本文面向第一次参与 `wisewe-rag-simple` 的 Codex / 人类协作者，用来快速判断从哪里读、怎么动手、结束时同步什么。它不替代 `AGENTS.md`，只做一页式入口。

## 先读什么

进入仓库后，按下面顺序读取：

1. `AGENTS.md`：项目级约束、GSD 入口、共享账本规则。
2. `todo.md`：当前可见任务、最近完成项和后续 backlog。
3. `bug.md`：已确认缺陷、设计结论和历史高风险点。
4. `.planning/STATE.md`：当前主线 milestone、phase 和最近状态。
5. 与任务直接相关的代码、文档或 phase summary。

如果只是回答问题或做轻量探索，可以先读相关文件，不修改任何内容。若用户明确要求实现、修复或继续执行，再进入编辑和验证。

## 怎么选任务入口

优先避免和主线正在推进的高风险工作重叠：

| 场景 | 推荐入口 |
| --- | --- |
| 小文档、单点修复、低风险补丁 | GSD quick 等价流程 |
| 明确 bug 或回归 | GSD debug 等价流程 |
| 已指定 phase 的计划执行 | 读取对应 `CONTEXT.md` / `PLAN.md` / `SUMMARY.md` 后执行 |
| 不确定需求边界 | 先讨论或写最小方案，不直接大改 |

当前项目主线集中在 v7.0 知识库治理与开放能力；如果任务涉及 SSO、OpenAPI、API Key、权限、日志审计、Token 统计，应先确认是否已经由主线或其他会话推进，避免重复实现。

## 编辑原则

- 只改和当前任务直接相关的文件。
- 不重排共享账本历史 ID，不删除别人未确认关闭的条目。
- `.claude/` 和历史 archive 只作参考，除非用户明确要求，不把它们当作当前运行前提。
- 文档中不要把未实现能力写成已上线；用“已开放”“最小开放”“规划待接入”“后续硬化”区分真实状态。
- 前端可见文案默认使用简体中文，并遵守 `docs/design/previews/wisewe-rag-console-ui-preview.html` 与 `docs/design/system/MASTER.md`。

## 验证和收尾

按改动风险选择验证：

| 改动类型 | 建议验证 |
| --- | --- |
| Python 后端 | `python -m py_compile ...`，再跑相关 `pytest` |
| 前端页面 / 组件 | `cd frontend && npm run typecheck` |
| 文档-only | 检查链接、状态口径和共享账本是否一致 |
| API / 权限 / 审计 | 同时补成功、失败、越权和错误码测试 |

结束前同步：

1. `todo.md`：值得跨会话追踪的完成项或后续项。
2. `bug.md`：只在发现真实缺陷、阻塞或设计性结论时更新。
3. `.planning/**`：仅当任务影响主线 phase 状态、用户要求 GSD 同步，或已有 quick/phase summary 需要补记录时更新。

## 常见误区

- 不要把 AI 基座 JWT 当作 OpenAPI Bearer；OpenAPI Bearer 是知识库 API Key。
- 不要把 `user_id` / `role_code` 当成可信授权依据，它们最多是过滤提示。
- 不要因为某个页面有入口就认为后端治理已完整闭环。
- 不要把历史乱码显示直接当作文件损坏；在 Windows 终端下应优先用 UTF-8 读取或运行验证确认。
