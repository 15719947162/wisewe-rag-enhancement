# Codex 常用命令配方

> 目标：把本项目在 Codex + GSD 下的常见使用方式写成最短可执行配方。  
> 前置说明：本项目默认使用纯文本模式，不依赖 ECC，不依赖菜单式交互。

---

## 1. 小改动 / 文档更新 / 单点修复

适用场景：

- README、设计文档、配置说明更新
- 小范围 bugfix
- 单文件或少量文件修改

推荐：

```text
$gsd-quick --text
```

输入建议：

```text
$gsd-quick --text
任务：把 offline ingestion 页面文案统一成中文，并补一条设计规范
```

如果你直接在 Codex 对话中做，不走 GSD，也至少遵守：

1. 读 `AGENTS.md`
2. 读 `todo.md`
3. 读 `bug.md`
4. 修改代码 / 文档
5. 更新共享账本

---

## 2. Bug 排查

适用场景：

- 功能回归
- API 行为异常
- 前后端联调错误
- RAG 结果异常
- 环境问题

推荐：

```text
$gsd-debug --text
```

输入建议：

```text
$gsd-debug --text
问题：在线问答接口返回 200，但引用为空，怀疑是 rerank 后 chunk 映射丢失
```

配套动作：

- 如果确认是新问题，更新 `bug.md`
- 如果是已有问题修复，更新对应状态和修复说明

---

## 3. 新功能但范围还不清晰

适用场景：

- 你知道想做什么，但还没拆清楚
- 需要先澄清边界、依赖、phase 归属

推荐：

```text
$gsd-discuss-phase <phase> --text
```

示例：

```text
$gsd-discuss-phase 09 --text
```

适合继续接：

```text
$gsd-plan-phase 09 --text
```

---

## 4. 已有 phase 的正式规划

适用场景：

- 已经知道 phase 编号
- 需要生成清晰的执行计划

推荐：

```text
$gsd-plan-phase <phase> --text
```

常用变体：

```text
$gsd-plan-phase 07 --text
$gsd-plan-phase 07 --text --tdd
$gsd-plan-phase 07 --text --research
```

说明：

- `--tdd`：更适合测试先行的改动
- `--research`：更适合不确定方案或依赖新库 / 新机制的任务

---

## 5. 执行已有 phase

适用场景：

- phase 已经有计划文件
- 现在要开始落实

推荐：

```text
$gsd-execute-phase <phase> --text
```

示例：

```text
$gsd-execute-phase 08 --text
```

执行前最少应查看：

1. `.planning/STATE.md`
2. 对应 phase 的 `CONTEXT.md`
3. 对应 phase 的 `PLAN.md`

---

## 6. 主线推进

适用场景：

- 需要继续推进当前主线 milestone
- 需要决定下一阶段挂到哪个 milestone / phase
- 需要把历史 workstream 结果收拢回项目主线

推荐：

```text
直接更新主线文档：.planning/STATE.md / ROADMAP.md / REQUIREMENTS.md
```

使用原则：

- 强关联任务放同一主线 milestone / phase
- 弱关联任务优先放同一 milestone 下分 phase，而不是拆新 workstream
- 旧 `workstreams/**` 只作归档引用，不再作为默认新入口

如果只是需要规则参考，先看：

- `AGENTS.md`
- `docs/archive/agent/codex-role-mapping.md`

---

## 7. 验证交付结果

适用场景：

- phase 执行后要验收
- 想用 GSD 做 UAT / 结果检查

推荐：

```text
$gsd-verify-work --text
```

如果是 phase 级验证，也可以用：

```text
$gsd-verify-phase <phase> --text
```

---

## 8. 代码审查 / 文档更新

没有 ECC 时，建议组合使用：

- 直接让 Codex review 当前变更
- 需要补文档时使用：

```text
$gsd-docs-update --text
```

如果只是 repo 内局部说明更新，普通 Codex 对话通常更快。

---

## 9. 什么时候更新共享账本

更新 `todo.md`：

- 新任务值得跨会话追踪
- 任务状态从 `TODO` → `DOING` → `DONE`
- 存在 `BLOCKED`

更新 `bug.md`：

- 确认发现新问题
- 旧问题已修复
- 得出“这不是 bug，而是设计决策”

详细规则见：

- `docs/agent/agent-shared-docs.md`

---

## 10. 最小推荐工作流

### 简单任务

```text
读 AGENTS.md / todo.md / bug.md
→ 实施修改
→ 更新共享账本
```

### 正式 phase 任务

```text
$gsd-discuss-phase --text
→ $gsd-plan-phase --text
→ $gsd-execute-phase --text
→ $gsd-verify-work --text
```

### Bug 任务

```text
$gsd-debug --text
→ 修复
→ 更新 bug.md / todo.md
```
