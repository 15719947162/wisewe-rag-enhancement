# Claude Code 多角色配置使用指南

> 历史参考：本文件保留为旧 Claude / ECC 工作流说明，不是当前 Codex 默认执行规范。当前仓库以 `AGENTS.md`、`todo.md`、`bug.md` 与 `.planning/STATE.md` 为准，并已将项目级 GSD 工作流统一收拢到主线 milestone / phase。

> 适用项目：wisewe-rag-simple  
> 配置版本：1.0.0  
> 插件依赖：ECC（Everything Claude Code）、GSD（Get Shit Done）、ui-ux-pro-max

---

## 一、配置结构总览

```
.claude/
├── agents/          # 8个专业角色（独立上下文的自主执行者）
├── commands/        # 4个工作流命令（注入到现有上下文的提示模板）
├── skills/          # 3个知识模块（可预加载的领域知识）
├── hooks/           # 2个事件钩子（自动触发的安全检查）
└── rules/           # 2个条件性规则（角色激活 + 工作流约束）
.mcp.json            # MCP 服务器配置（context7 + sequential-thinking）
```

---

## 二、角色（Agents）

共 8 个角色，存放于 `.claude/agents/`，每个角色在独立上下文中运行。

### 角色一览

| 角色 | 文件 | 模型 | 职责 |
|------|------|------|------|
| `leader` | `leader.md` | opus | 需求拆解、任务分配、GSD 编排 |
| `product-manager` | `product-manager.md` | sonnet | PRD 编写、用户故事、验收标准 |
| `ui-designer` | `ui-designer.md` | sonnet | 界面设计、组件规范、ui-ux-pro-max |
| `frontend-dev` | `frontend-dev.md` | sonnet | UI 实现、状态管理、前后端联调 |
| `backend-dev` | `backend-dev.md` | sonnet | FastAPI 实现、RAG 管道集成 |
| `db-expert` | `db-expert.md` | sonnet | pgvector Schema、索引、迁移 |
| `api-architect` | `api-architect.md` | sonnet | RESTful 设计、OpenAPI 规范 |
| `qa-tester` | `qa-tester.md` | sonnet | 测试策略、覆盖率、E2E |

### 自动激活触发词

角色会根据对话中的关键词自动激活，无需手动指定：

| 触发词 | 激活角色 |
|--------|---------|
| 新迭代、新功能、规划、kickoff | `leader` |
| 需求、PRD、用户故事、验收标准 | `product-manager` |
| 界面、UI、设计、组件、样式 | `ui-designer` |
| 前端、组件实现、页面、状态管理 | `frontend-dev` |
| FastAPI、后端、服务端、API实现 | `backend-dev` |
| 数据库、Schema、迁移、pgvector | `db-expert` |
| 接口、API设计、OpenAPI、路由 | `api-architect` |
| 测试、pytest、覆盖率、E2E | `qa-tester` |

### 手动指定角色

```
# 直接告诉 Claude 使用哪个角色
以 backend-dev 角色实现知识库创建接口

# 或通过 Agent 工具调用
使用 api-architect 角色设计检索接口
```

---

## 三、命令（Commands）

共 4 个工作流命令，存放于 `.claude/commands/`，通过 `/命令名` 调用。

### /kickoff — 启动迭代

适用于开始一个新功能或新迭代。

```
/kickoff 实现知识库管理功能，包括创建、列表、删除
```

**执行流程：**
1. `leader` 角色接收需求，评估复杂度
2. 判断功能关联性（强/弱/无），决定 GSD 执行策略
3. 输出完整迭代计划（角色分工 + 任务清单 + 验收标准）
4. **等待用户确认后才开始执行**

**关联性决策示例：**

```
# 强关联 → 同一 phase 统一执行
/kickoff 知识库CRUD + 权限管理（共享同一张表）

# 弱关联 → 多 phase 按依赖排序
/kickoff 知识库CRUD 和 文件上传（同模块但功能独立）

# 无关联 → 仍优先挂到同一主线 milestone，下拆独立 phase
/kickoff 知识库核心 和 前端UI（完全不同模块）
```

---

### /sprint — 执行任务

根据 `todo.md` 中的待办任务，自动选择角色并行执行。

```
/sprint
```

**执行逻辑：**
- 读取 `todo.md` 中状态为待办的任务
- 按任务类型自动匹配角色
- 独立任务并行启动，依赖任务串行等待
- 完成后更新 `todo.md` 状态，发现 Bug 记录到 `bug.md`

**任务类型与角色映射：**

```
PRD / 需求文档    → product-manager
UI 设计 / 组件规范 → ui-designer
API 设计 / 接口文档 → api-architect
数据库 Schema     → db-expert
前端实现          → frontend-dev
后端实现          → backend-dev
测试编写          → qa-tester
```

---

### /review-all — 全面审查

提交代码前执行多维度并行审查。

```
/review-all
```

**并行激活的审查角色：**

| 审查维度 | 使用工具 |
|---------|---------|
| 代码质量 | `ecc:code-reviewer` |
| 安全漏洞 | `gsd-security-auditor` |
| 测试覆盖 | `qa-tester` |
| FastAPI 规范 | `ecc:fastapi-reviewer` |
| 数据库质量 | `ecc:database-reviewer` |

**阻塞合并的条件：**
- 存在任何 CRITICAL 安全问题
- 测试覆盖率 < 80%
- 构建失败

---

### /deploy-check — 部署前检查

部署前执行完整的环境和质量验证。

```
/deploy-check
```

**检查项：**
1. 测试通过且覆盖率 ≥ 80%
2. 所有必需环境变量已配置（`.env`）
3. 数据库迁移脚本无待执行项
4. 无硬编码密钥
5. API 健康检查通过

---

## 四、知识模块（Skills）

共 3 个知识模块，存放于 `.claude/skills/`，在相关任务时自动预加载。

### rag-pipeline

**预加载**：是（所有 RAG 相关任务自动加载）

包含内容：
- 6 种切片策略的适用场景对比
- 向量检索阈值和 Top-K 调优建议
- 质量评估指标定义
- 常见问题排查表

### ui-design-system

**预加载**：否（UI 相关任务时按需加载）

包含内容：
- 项目颜色令牌和间距系统
- 状态指示器、进度条等核心组件规范
- ui-ux-pro-max 命令使用方式

### api-design

**预加载**：否（API 设计任务时按需加载）

包含内容：
- FastAPI 项目目录结构规范
- 完整错误码命名空间
- 异步任务（长时操作）设计模式

---

## 五、事件钩子（Hooks）

### on-task-complete.sh

**触发时机**：Write / Edit 工具修改 `.py` 文件后

**行为**：提示更新 `todo.md` 中对应任务状态

### pre-commit-check.sh

**触发时机**：Bash 工具执行 `git commit` 时

**行为**：
- 扫描 `src/` 中是否存在硬编码 API Key（`sk-` 前缀）
- 检查 `.env` 是否被意外加入暂存区（发现则**阻止提交**）

---

## 六、MCP 服务器

配置文件：`.mcp.json`

| 服务器 | 用途 |
|--------|------|
| `context7` | 获取 FastAPI、pgvector、SQLAlchemy 等库的最新文档 |
| `sequential-thinking` | 复杂架构决策和调试时的分步推理 |

**使用示例：**

```
# context7 自动触发（询问库用法时）
pgvector 的 HNSW 索引参数怎么配置？

# sequential-thinking 手动触发
thinkhard: 如何设计支持多策略并行对比的检索架构？
```

---

## 七、典型工作流

### 场景 A：开发新功能（完整流程）

```
# 第一步：启动迭代规划
/kickoff 实现向量检索接口，支持多策略对比

# Leader 输出计划，确认后继续

# 第二步：并行执行各角色任务
/sprint

# 第三步：提交前全面审查
/review-all

# 第四步：修复审查发现的问题后部署检查
/deploy-check
```

---

### 场景 B：单角色快速任务

```
# 直接描述任务，触发词自动激活对应角色

# 触发 api-architect
设计知识库检索接口，支持 top_k 和 threshold 参数

# 触发 db-expert
为 chunks 表的 embedding 列添加 HNSW 索引

# 触发 qa-tester
为 FixedLengthStrategy 编写单元测试，覆盖边界条件
```

---

### 场景 C：多模块并行开发

```
# Leader 判断为无关联，但仍收拢到主线 milestone
/kickoff 同时开发知识库管理后端 和 前端上传界面

# Leader 自动执行：
/gsd:phase add "知识库后端" --text
/gsd:phase add "前端上传" --text
# 两个 phase 仍可并行推进，但状态统一回收到主线 milestone
```

---

### 场景 D：Bug 修复

```
# 描述 Bug，触发对应角色
[bug] 检索接口返回结果相似度分数始终为 0

# 自动触发：backend-dev 排查 + db-expert 检查 SQL
# Bug 记录到 bug.md，修复后更新状态
```

---

## 八、ECC / GSD 集成说明

### ECC 工具映射

| 场景 | ECC 工具 |
|------|---------|
| 代码审查 | `ecc:code-reviewer` |
| 安全审计 | `ecc:security-reviewer` |
| FastAPI 审查 | `ecc:fastapi-reviewer` |
| 数据库审查 | `ecc:database-reviewer` |
| 构建错误修复 | `ecc:build-error-resolver` |
| E2E 测试 | `ecc:e2e-runner` |
| 架构设计 | `ecc:code-architect` |

### GSD 工具映射

| 场景 | GSD 工具 |
|------|---------|
| 生成执行计划 | `gsd-planner` |
| 验证交付物 | `gsd-verifier` |
| 安全扫描 | `gsd-security-auditor` |
| UI 质量检查 | `gsd-ui-checker` |
| 前后端契约验证 | `gsd-integration-checker` |
| 阶段边界处理 | `gsd-phase-boundary` |

### GSD Phase 生命周期

每个 GSD phase 走完整流程：

```
discuss → plan → execute → verify → [phase-boundary]
```

对应命令：

```
/gsd:discuss-phase  # 讨论阶段目标
/gsd:plan-phase     # 生成执行计划
/gsd:execute-phase  # 执行任务
/gsd:verify-phase   # 验证交付物
```

---

## 九、文档维护规范

### todo.md 格式

```markdown
## 迭代 N：[功能名称]
**状态**：进行中
**开始时间**：YYYY-MM-DD

### 任务清单
- [ ] 🔄 Task 1（角色，预估）
- [x] ✅ Task 2（角色）已完成

---
## 统计
- 总迭代数：N
- 已完成功能：功能1、功能2
- 当前进行中：功能N
```

### bug.md 格式

```markdown
## Bug #N：[描述]
- **发现时间**：YYYY-MM-DD
- **影响范围**：模块名
- **状态**：待修复 / 修复中 / 已修复

---
## 统计
- Bug 总数：N
- 待修复：N  修复中：N  已修复：N
```

---

## 十、注意事项

1. **GateGuard 钩子**：首次执行 Bash 命令前需声明操作意图，这是 ECC 的安全机制，正常现象。

2. **leader 使用 opus 模型**：编排决策需要更强推理能力，其余角色使用 sonnet 以控制成本。

3. **/kickoff 必须等待确认**：Leader 输出计划后不会自动执行，需要用户明确确认后才开始 /sprint。

4. **测试覆盖率硬性要求**：`/review-all` 和 `/deploy-check` 均强制要求 ≥ 80%，低于此值会阻塞流程。

5. **ui-ux-pro-max 集成**：`ui-designer` 角色内置了 ui-ux-pro-max 插件调用规范，需确保该插件已安装（`/plugin install ui-ux-pro-max`）。
