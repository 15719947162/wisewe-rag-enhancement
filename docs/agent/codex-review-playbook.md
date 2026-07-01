# Codex 审查手册

> 目标：替代 Claude/ECC 语境下的 `/review-all`。  
> 适用：Codex + GSD，无 ECC 插件前提。

---

## 一、设计原则

在本项目里，“审查”不再绑定某个专用插件，而是拆成 4 个可组合层次：

1. Codex 直接代码审查
2. GSD phase / work 验证
3. 安全专项复核
4. UI / 质量专项复核

这样做的好处是：

- 不依赖 ECC
- 可按任务类型选择深度
- 能和 `.planning/`、`todo.md`、`bug.md` 一起工作

---

## 二、最常用的 3 种审查模式

### 模式 A：轻量审查

适用场景：

- 小改动
- 局部文档
- 单个 bugfix
- 单个前端页面修复

做法：

1. 直接让 Codex review 当前改动
2. 重点看：
   - 行为回归
   - 空值 / 边界条件
   - 文案和设计约束
   - 是否需要补测试

推荐提示词：

```text
请 review 当前改动，优先找 bug、回归风险、缺失测试，不要做泛泛总结。
```

### 模式 B：标准 phase 审查

适用场景：

- 一个 phase 已完成
- 需要正式验证交付结果

推荐组合：

```text
$gsd-verify-work <phase> --text
$gsd-code-review <phase> --depth=standard
```

作用分工：

- `verify-work`：偏用户视角 / 验收视角
- `code-review`：偏代码风险 / 结构风险 / 测试缺口

### 模式 C：发布前审查

适用场景：

- 准备 ship
- 准备合并较大改动
- 准备给别人接手

推荐组合：

```text
$gsd-code-review <phase> --depth=deep
$gsd-secure-phase <phase>
$gsd-validate-phase <phase>
$gsd-ui-review <phase>
$gsd-ship <phase>
```

不是每次都要全跑。推荐按任务类型裁剪：

- 后端重构：优先 `code-review + secure-phase + validate-phase`
- 前端大改：优先 `code-review + ui-review + verify-work`
- AI / RAG 改动：优先 `code-review + validate-phase + verify-work`

---

## 三、Codex 直接 review 的标准提示词

如果你不想先走 GSD，直接在 Codex 里审查，建议统一用这种风格：

```text
请 review 这次改动。
要求：
1. 先列 findings，按严重度排序
2. 优先关注 bug、行为回归、安全风险、缺失测试
3. 每条问题给出文件和行号
4. 如果没有明显问题，明确说 no findings，并说明残余风险
```

这和当前项目里的 Codex 行为风格是一致的。

---

## 四、不同任务类型的审查重点

### 1. 前端 / UI

优先检查：

- 是否遵守 `docs/design/previews/wisewe-rag-console-ui-preview.html` 与 `docs/design/system/MASTER.md`
- 可见文案是否为简体中文
- 状态是否只靠颜色表达
- 空状态 / 错误状态 / loading 状态是否完整
- 响应式和布局是否被破坏

推荐组合：

```text
$gsd-code-review <phase> --depth=standard
$gsd-ui-review <phase>
```

### 2. 后端 / API

优先检查：

- 路由层、schema 层、service 层职责是否混乱
- 输入校验是否缺失
- 错误路径是否稳定
- 前端 live mode 契约是否被破坏
- 新结构是否引入导入循环

推荐组合：

```text
$gsd-code-review <phase> --depth=deep
$gsd-secure-phase <phase>
```

### 3. 数据库 / pgvector

优先检查：

- Schema 变更是否兼容现有数据
- 索引策略是否合理
- 查询是否存在明显性能问题
- 多知识库隔离是否被破坏

推荐组合：

```text
$gsd-code-review <phase> --depth=deep
$gsd-validate-phase <phase>
```

### 4. RAG / AI 行为

优先检查：

- 检索结果与引用是否一致
- 生成答案是否有脱离上下文的风险
- 评分信息是否可解释
- 回退路径是否覆盖

推荐组合：

```text
$gsd-verify-work <phase> --text
$gsd-validate-phase <phase>
```

---

## 五、发现问题后怎么落地

### 轻量问题

- 直接修
- 更新 `todo.md`

### 明确缺陷

- 记录到 `bug.md`
- 标状态
- 如果修复了，补修复说明

### 规划级问题

比如：

- phase 目标漂移
- 路线图不再准确
- 需求边界变化

这类问题再去补：

- `.planning/STATE.md`
- `.planning/ROADMAP.md`
- `.planning/REQUIREMENTS.md`

---

## 六、推荐默认组合

如果你不想想太多，默认这样用：

### 小任务

```text
Codex 直接 review
```

### 一个 phase 做完

```text
$gsd-verify-work <phase> --text
$gsd-code-review <phase> --depth=standard
```

### 准备发版 / 合并大改

```text
$gsd-code-review <phase> --depth=deep
$gsd-secure-phase <phase>
$gsd-validate-phase <phase>
```

如果是前端主导改动，再加：

```text
$gsd-ui-review <phase>
```
