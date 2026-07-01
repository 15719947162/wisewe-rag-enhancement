# Design 文档

本目录存放 WiseWe RAG Console 的现行 UI 规范、视觉预览和历史归档。

## 现行入口

后续 UI 设计、评审和前端实现只读取下面两个入口：

- [previews/wisewe-rag-console-ui-preview.html](./previews/wisewe-rag-console-ui-preview.html)：唯一视觉基准
- [system/MASTER.md](./system/MASTER.md)：项目级 UI 规范与实现约束

页面级差异只允许写入 `system/pages/`，且不得覆盖 Master 中的全局规则。

## 子目录

- [`system/`](./system/)：现行设计系统主文档与页面级规则
- [`previews/`](./previews/)：现行视觉预览稿
- [`archive/`](./archive/)：已废弃或迁出的旧 UI 交互标准

## 已退场内容

- `docs/design/ui/**` 已迁移到 `docs/design/archive/legacy-ui-spec-2026-06-25/`
- `docs/design/ui-interaction-standard.md` 与 `docs/design/product-ui-redesign-plan.md` 不再作为 active 文件保留

详见 [archive/2026-06-25-retired-ui-standards.md](./archive/2026-06-25-retired-ui-standards.md)。
