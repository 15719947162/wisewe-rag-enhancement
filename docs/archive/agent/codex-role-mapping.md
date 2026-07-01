# Claude 角色到 Codex 映射

> 目标：保留 `docs/archive/agent/claude-config-guide.md` 中的角色分工思想，但改写成适合 Codex + GSD 的任务分类映射。  
> 结论先行：Codex 不需要真的复制 8 个 agent；只需要保留这些“职责视角”。

---

## 一、总体原则

在 Claude 方案中，角色是运行时实体；在 Codex 方案中，角色更适合作为：

- 任务分类
- 阅读顺序
- 输出重点
- 验收视角

因此本文件不是要新建 8 个 Codex agent，而是告诉你：

- 当前任务更像哪个角色
- 应优先读哪些文件
- 输出应该偏向什么

---

## 二、角色映射表

| 原角色 | 在 Codex 中的含义 | 优先读取 | 主要产出 |
|--------|-------------------|---------|---------|
| `leader` | 任务编排 / phase 路由 / 风险识别 | `AGENTS.md`、`todo.md`、`.planning/STATE.md`、`ROADMAP.md` | 任务拆解、执行顺序、共享账本更新 |
| `product-manager` | 需求澄清 / 用户故事 / 验收标准 | `REQUIREMENTS.md`、相关 PRD、`todo.md` | 功能边界、验收条件、文档化需求 |
| `ui-designer` | 设计规则 / 交互策略 / 组件规范 | `docs/design/previews/**`、`docs/design/system/**` | 中文 UI 规范、布局和交互说明 |
| `frontend-dev` | 页面实现 / 状态管理 / 联调 | `frontend/src/**`、设计规范、相关 API 文档 | 前端代码、页面文案、交互实现 |
| `backend-dev` | API 实现 / 管道集成 / 服务逻辑 | `src/**`、`tests/**`、相关 phase 文档 | 服务端代码、业务逻辑、接口实现 |
| `db-expert` | Schema / 索引 / SQL / 迁移 | `src/db/**`、初始化脚本、phase 设计文档 | 数据模型、查询、索引策略 |
| `api-architect` | 契约设计 / 响应结构 / 错误码 | API 相关代码、`docs/api/`、需求文档 | 接口定义、命名规范、契约约束 |
| `qa-tester` | 测试设计 / 覆盖率 / 回归风险 | `tests/**`、`bug.md`、相关实现代码 | 测试用例、验证步骤、风险结论 |

---

## 三、任务触发词映射

下面这些词可以帮助你在 Codex 里快速切换“工作视角”。

### `leader` 视角

触发词：

- 新功能
- 新迭代
- kickoff
- phase
- roadmap
- 主线 milestone
- 怎么拆

优先动作：

1. 判断是 quick / debug / phase / 主线 milestone
2. 看 `todo.md`
3. 看 `.planning/STATE.md`
4. 输出拆分和路径，而不是立刻写实现细节

### `product-manager` 视角

触发词：

- 需求
- PRD
- 用户故事
- 验收标准
- 要支持什么

优先动作：

1. 澄清 Must / Should / Out of Scope
2. 写清验收标准
3. 再进入计划或实现

### `ui-designer` 视角

触发词：

- UI
- 设计
- 页面
- 组件
- 布局
- 文案

优先动作：

1. 先看 `docs/design/previews/wisewe-rag-console-ui-preview.html` 与 `docs/design/system/MASTER.md`
2. 确认中文输出约束
3. 再讨论视觉与交互

### `frontend-dev` 视角

触发词：

- 前端
- Next.js
- 页面实现
- 状态管理
- 联调

优先动作：

1. 看设计规范
2. 看 `frontend/src/**`
3. 看相关接口
4. 再落代码

### `backend-dev` 视角

触发词：

- FastAPI
- 后端
- API 实现
- RAG 管道
- 服务端

优先动作：

1. 看 `src/**`
2. 看测试和 phase 文档
3. 明确输入输出和异常路径

### `db-expert` 视角

触发词：

- 数据库
- Schema
- pgvector
- 索引
- SQL

优先动作：

1. 看数据模型和现有脚本
2. 先审查查询路径
3. 再动表结构和索引

### `api-architect` 视角

触发词：

- 接口设计
- OpenAPI
- 错误码
- 响应格式
- 契约

优先动作：

1. 明确资源模型
2. 定义请求响应结构
3. 再交给实现层

### `qa-tester` 视角

触发词：

- 测试
- 覆盖率
- 回归
- 验证
- review

优先动作：

1. 优先找风险点
2. 优先列失败路径
3. 优先确认已有 bug 是否会复现

---

## 四、Codex 下的实际使用方式

### 简单做法

直接在自然语言里带上视角：

```text
从 backend-dev + qa-tester 视角检查这个 RAG 接口的回归风险
```

```text
从 ui-designer 视角把 offline ingestion 页面规范补成中文
```

### 正式做法

如果任务会跨 phase 或跨模块，优先走 GSD：

```text
$gsd-discuss-phase <phase> --text
$gsd-plan-phase <phase> --text
```

然后在实现时，把本文件当作“阅读和关注点路由表”。

补充约定：

- 项目级推进默认挂主线 milestone
- 历史 `workstreams/**` 只作归档参考，不再作为新的默认拆分维度

---

## 五、和共享账本的关系

无论采用哪个角色视角，最终都应回到统一落点：

- 任务状态进 `todo.md`
- 缺陷 / 设计结论进 `bug.md`
- phase 级长期状态进 `.planning/**`

这比在不同代理生态中分别维护一套状态更稳定。
