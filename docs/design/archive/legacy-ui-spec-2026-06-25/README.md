# WiseWe RAG Console UI 设计规范

本文档集已归档为历史 UI 规范与细节补充。现行实现入口以 `../../previews/wisewe-rag-console-ui-preview.html` 和 `../../system/MASTER.md` 为准。

- 离线建库链路
- 在线检索增强问答链路
- 知识库资产管理
- 评测与问题诊断
- 系统配置与参数管理

## 文档结构

- [00-设计总纲](./00-设计总纲.md)
- [01-Design-Tokens](./01-Design-Tokens.md)
- [02-布局与信息架构](./02-布局与信息架构.md)
- [03-核心组件规范](./03-核心组件规范.md)
- [04-页面模板规范](./04-页面模板规范.md)
- [05-图表与数据表达规范](./05-图表与数据表达规范.md)
- [06-状态、交互与可用性规范](./06-状态、交互与可用性规范.md)
- [07-页面线框与链路映射](./07-页面线框与链路映射.md)
- [08-用量报表样板规范](./08-用量报表样板规范.md)

## 关联文档

- [设计系统 Master](../system/MASTER.md)
- [离线建库链路](../../pipeline/offline-ingestion-pipeline.md)
- [在线检索增强问答链路](../../pipeline/online-rag-pipeline.md)
- [在线召回](../../pipeline/online-retrieval.md)
- [分层切片规则](../../rule/hierarchical-chunking.md)
- [清洗规则与质量过滤](../../rule/cleaner-rules.md)

## 使用方式

1. 做全局 UI 设计时，先读 `../../previews/wisewe-rag-console-ui-preview.html` 与 `../../system/MASTER.md`。
2. 本目录仅用于查找历史页面、图表、状态描述的细节，不得覆盖现行 Master。
3. 如果本目录内容与现行 Master 冲突，以现行 Master 和预览稿为准。
