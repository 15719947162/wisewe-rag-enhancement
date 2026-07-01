# Codex + GSD 项目配置指南

> 参考来源：`docs/archive/agent/claude-config-guide.md`  
> 适用项目：`wisewe-rag-simple`  
> 配置目标：在 **不依赖 ECC** 的前提下，为 Codex 建立一套稳定、可提交、可共享、可长期维护的项目内配置。

---

## 一、为什么不直接照搬 Claude 配置

`docs/archive/agent/claude-config-guide.md` 的核心思路没有问题，但它默认依赖以下能力：

- ECC 提供的专项审查器
- Claude 风格的 agent / command / hook 组织方式
- 某些菜单式交互体验

Codex 在这个仓库里更适合的方案是：

1. 用 `AGENTS.md` 作为项目级主指令入口
2. 用 GSD 作为阶段规划 / 执行引擎
3. 用项目内 `.codex/skills/` 放仓库专用技能
4. 用 `todo.md`、`bug.md` 作为跨代理共享账本
5. 用 `workflow.text_mode: true` 解决非菜单式交互

这套方案的目标不是“在 Codex 里模拟 Claude”，而是“让 Codex 在这个仓库里原生工作得顺手”。

---

## 二、最终配置结构

```text
AGENTS.md
.codex/
├── README.md
└── skills/
    └── wisewe-rag-workflow/
        └── SKILL.md
.planning/
└── config.json
docs/
├── README.md
└── agent/
    ├── agent-shared-docs.md
    ├── codex-command-recipes.md
    ├── codex-config-guide.md
    └── codex-review-playbook.md
docs/archive/
└── agent/
    ├── claude-config-guide.md
    └── codex-role-mapping.md
todo.md
bug.md
```

说明：

- `AGENTS.md`：Codex 项目级主指令
- `.codex/skills/wisewe-rag-workflow/SKILL.md`：仓库专用工作流技能
- `.planning/config.json`：GSD 的 Codex 友好配置
- `todo.md` / `bug.md`：跨代理共享账本
- `docs/agent/agent-shared-docs.md`：共享文档契约

---

## 三、核心设计决策

### 1. 不依赖 ECC

本方案不再假设存在：

- `ecc:code-reviewer`
- `ecc:fastapi-reviewer`
- `ecc:database-reviewer`
- `ecc:e2e-runner`

替代方式：

- 代码质量：Codex 自身代码审查能力 + GSD 审查工作流
- 文档与计划：`.planning/**`
- 前端 / 设计：项目级技能 `ui-ux-pro-max`
- 技术文档：`context7`

### 2. 以 `AGENTS.md` 为中心，而不是 `.claude/commands/`

Codex + GSD 的组合更适合“项目主说明 + workflow 路由”，不适合把大量行为硬塞进自定义命令目录。

因此：

- `AGENTS.md` 负责项目上下文、共享文档、工作流约束
- GSD 命令负责 phase / quick / debug / verify
- 项目技能负责仓库专用知识

### 3. 强制启用 GSD 文本模式

Codex 不应该依赖菜单弹窗式交互，因此启用：

- `workflow.text_mode: true`

效果：

- GSD 在 Codex 下统一改成纯文本编号选择
- 不再依赖 AskUserQuestion / TUI 弹窗

### 4. 共享少量高价值文档

不建议共享太多文档，否则容易变成多套状态相互污染。

本方案只建议把以下内容视为共享写入面：

- `todo.md`
- `bug.md`

谨慎共享：

- `.planning/STATE.md`
- `.planning/ROADMAP.md`
- `.planning/REQUIREMENTS.md`

### 5. `.claude/` 保留但降级为参考

现有 `.claude/` 不删除，因为其中仍有：

- 历史角色划分
- 工作流示例
- 设计与命名参考

但在 Codex 方案里，它不再是执行入口。

---

## 四、GSD 配置建议

当前项目已改成对 Codex 更友好的配置，重点如下：

- `response_language: "Chinese"`
- `workflow.text_mode: true`
- `workflow.plan_check: true`
- `workflow.verifier: true`
- `workflow.use_worktrees: false`
- `manager.flags.* = "--text"`

理由：

- 中文输出统一
- 文本模式兼容 Codex
- 保留 GSD 的规划和验证能力
- 关闭 worktree，减少 Windows + 单人开发下的复杂度

---

## 五、Claude 配置到 Codex 配置的映射

| Claude 方案 | Codex 方案 |
|-------------|------------|
| `.claude/agents/*` | `AGENTS.md` 中的任务分类 + GSD 路由 |
| `.claude/commands/*` | 直接使用 GSD 命令 |
| ECC 专项插件 | Codex 自身能力 + GSD + context7 |
| Claude Hooks | 不作为项目必需项，保留全局 hooks 即可 |
| Claude Skills | `.codex/skills/` 项目技能 |
| `todo.md` / `bug.md` | 保留并升级为跨代理共享账本 |

---

## 六、推荐使用方式

### 场景 A：小改动 / 单点修复

推荐：

```text
$gsd-quick --text
```

或者直接在 Codex 对话里做，但仍需：

1. 读 `todo.md`
2. 读 `bug.md`
3. 做修改
4. 更新共享账本

### 场景 B：Bug 排查

推荐：

```text
$gsd-debug --text
```

适合：

- 环境问题
- 回归问题
- 前后端联调异常
- RAG 结果异常

### 场景 C：已有 phase 的功能实现

推荐：

```text
$gsd-execute-phase <phase> --text
```

在执行前先看：

1. `.planning/STATE.md`
2. 对应 phase 的 `CONTEXT.md`
3. 对应 phase 的 `PLAN.md`

### 场景 D：新功能 / 新阶段

推荐：

```text
$gsd-discuss-phase <phase> --text
$gsd-plan-phase <phase> --text
```

需要用户参与明确范围时，GSD 文本模式会直接给编号式问题，不依赖菜单。

---

## 七、共享文档策略

详见：`docs/agent/agent-shared-docs.md`

### `todo.md`

适合记录：

- 当前活动任务
- backlog
- block
- 最近完成

### `bug.md`

适合记录：

- 确认缺陷
- 环境阻塞
- 已修复问题
- 设计性结论

### 共享原则

1. 只共享少量高价值文档
2. 只更新和当前任务直接相关的条目
3. 保留历史，不做大规模重写

---

## 八、结论

这套 Codex 配置的核心不是“复制 Claude Code 的表面结构”，而是把它真正迁移成适合 Codex 的四层：

1. `AGENTS.md` 管项目行为
2. GSD 管阶段和执行
3. `.codex/skills/` 管仓库专用知识
4. `todo.md` / `bug.md` 管跨代理共享状态

如果后续你要继续补齐“像 Claude `/kickoff`、`/review-all` 那样的 Codex 使用手册”，可以在这套基础上继续加一份 `docs/agent/codex-command-recipes.md`，但当前这版已经能作为完整、可用、可提交的 Codex 项目配置。
