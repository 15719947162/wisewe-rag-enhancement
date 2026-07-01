# 归档：旧 UI 交互标准

> 归档日期：2026-06-25  
> 现行入口：`docs/design/previews/wisewe-rag-console-ui-preview.html` + `docs/design/system/MASTER.md`

以下内容已从 active 设计入口中退场：

| 原路径 | 处理方式 | 说明 |
| --- | --- | --- |
| `docs/design/ui/**` | 已迁移到 `docs/design/archive/legacy-ui-spec-2026-06-25/**` | 早期 UI 规范与 v3 页面规范，仅保留追溯价值 |
| `docs/design/ui-interaction-standard.md` | 已从 active 目录删除 | 旧交互标准被预览稿和 Master 取代 |
| `docs/design/product-ui-redesign-plan.md` | 已从 active 目录删除 | 旧改版计划不再作为实现依据 |

归档原因：

- 旧规范过度强调早期卡片化、旧页面模板和旧交互套路。
- 现行控制台已统一到浅灰蓝 canvas、286px 左侧导航、76px 顶栏、8px 面板、多业务域色和数据密集工作台语言。
- 继续保留 active 入口会让后续实现回退到旧方案。

后续规则：

- 新 UI 任务不得引用 `docs/design/ui/**` 作为实现依据。
- 需要追溯历史决策时，只能从 archive 读取，并以 Master 和预览稿为准覆盖。
