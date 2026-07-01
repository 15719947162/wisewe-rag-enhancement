# 文档归类地图

本文档是当前项目文档入口和归档口径的总索引。后续新增文档时，优先先判断它属于哪一类，再放入对应目录。

## 推荐阅读路径

1. [README.md](../README.md)：项目定位、启动方式、主要接口。
2. [pipeline/full-chain-technical-guide.md](./pipeline/full-chain-technical-guide.md)：完整离线入库、在线 RAG、Graph RAG 技术链路。
3. [pipeline/README.md](./pipeline/README.md)：链路文档入口。
4. [architecture/README.md](./architecture/README.md)：系统分层、模块边界和核心数据流。
5. [performance-optimizations/README.md](./performance-optimizations/README.md)：解析、切片、向量化、在线召回性能优化档案。
6. [iterations/README.md](./iterations/README.md)：版本演进摘要。
7. `.planning/STATE.md` 与 `.planning/ROADMAP.md`：当前主线状态与规划事实源。

## 目录归类

| 目录 | 用途 | 当前代表文档 |
| --- | --- | --- |
| `docs/agent/` | Codex、GSD、共享账本文档协作规则 | [agent/codex-onboarding.md](./agent/codex-onboarding.md)、[agent/agent-shared-docs.md](./agent/agent-shared-docs.md) |
| `docs/architecture/` | 当前系统结构、模块边界、历史问题分析 | [architecture/README.md](./architecture/README.md) |
| `docs/design/` | 控制台设计系统、页面规范、可见中文文案规范 | [design/README.md](./design/README.md) |
| `docs/eval/` | 离线 benchmark 与运行时评分边界 | [eval/README.md](./eval/README.md) |
| `docs/iterations/` | 版本演进摘要与单次迭代说明 | [iterations/README.md](./iterations/README.md) |
| `docs/performance-optimizations/` | 已结论化的性能优化档案 | [performance-optimizations/README.md](./performance-optimizations/README.md) |
| `docs/archive/performance-discussions/` | 性能讨论过程、专题历史材料、早期验证记录 | [archive/performance-discussions/README.md](./archive/performance-discussions/README.md) |
| `docs/pipeline/` | 当前稳定链路如何工作 | [pipeline/full-chain-technical-guide.md](./pipeline/full-chain-technical-guide.md) |
| `docs/product/` | 产品参数、验收口径、对外交付表达 | [product/README.md](./product/README.md) |
| `docs/api/` | 当前后端 HTTP API 参考、认证方式、请求 / 响应约定和示例 | [api/reference.md](./api/reference.md) |
| `docs/research/` | 外部资料、调研结果、供应商/工具研究 | [research/README.md](./research/README.md) |
| `docs/rule/` | 清洗、切片、增强抽取、定位、Graph RAG 规则边界 | [rule/chunking-rules.md](./rule/chunking-rules.md) |
| `docs/archive/` | 已迁移、旧入口或仅作历史参考的文档 | [archive/README.md](./archive/README.md) |

## `.planning/` 与 `docs/` 的边界

| 类型 | 放置位置 | 原则 |
| --- | --- | --- |
| 当前主线状态、phase 计划、验收结论 | `.planning/**` | 事实源，优先保持 GSD 结构 |
| 面向团队阅读的链路说明 | `docs/pipeline/` | 从当前实现出发，避免混入调参历史 |
| 性能实验、参数验证、回退规则 | `docs/performance-optimizations/` | 必须说明基线、指标、结论和回退 |
| 需求、产品参数、招标表达 | `docs/product/` | 面向交付与验收，不替代实现文档 |
| 过期入口、历史参考、旧目录口径 | `docs/archive/` 或 `.planning/milestones/` | 保留来源，不强行改写成当前事实 |

## 当前重要入口

- 完整链路：[pipeline/full-chain-technical-guide.md](./pipeline/full-chain-technical-guide.md)
- 离线入库：[pipeline/offline-ingestion-pipeline.md](./pipeline/offline-ingestion-pipeline.md)
- 在线问答：[pipeline/online-rag-pipeline.md](./pipeline/online-rag-pipeline.md)
- 在线召回：[pipeline/online-retrieval.md](./pipeline/online-retrieval.md)
- 解析 provider：[pipeline/parser-provider-poc.md](./pipeline/parser-provider-poc.md)
- 官方 MinerU：[pipeline/mineru-official-parser.md](./pipeline/mineru-official-parser.md)
- Document Mind 分片：[pipeline/document-mind-sharding.md](./pipeline/document-mind-sharding.md)
- 三层切片最终方案：[pipeline/three-layer-chunking-final-solution.md](./pipeline/three-layer-chunking-final-solution.md)
- 文档图谱预览：[pipeline/document-graph-preview.md](./pipeline/document-graph-preview.md)
- 知识库治理 BRD：[product/knowledge-base-governance-brd.md](./product/knowledge-base-governance-brd.md)
- Phase 11 缺口清单：[product/phase11-gap-analysis.md](./product/phase11-gap-analysis.md)
- API 参考：[api/reference.md](./api/reference.md)
- AI 基座 SSO 对接：[product/ai-base-sso-integration-guide.md](./product/ai-base-sso-integration-guide.md)
- 外部治理对接契约：[product/external-governance-integration-contract.md](./product/external-governance-integration-contract.md)
- 产品技术参数：[product/technical-bid-parameters.md](./product/technical-bid-parameters.md)

## 新文档放置判断

- 解释“系统现在怎么跑”：放 `docs/pipeline/` 或 `docs/architecture/`。
- 解释“规则为什么这么判”：放 `docs/rule/`。
- 解释“某次优化怎么验证”：放 `docs/performance-optimizations/`。
- 解释“版本怎么演进”：放 `docs/iterations/` 或 `.planning/milestones/`。
- 解释“对外怎么描述能力”：放 `docs/product/`。
- 只是旧入口或历史讨论：放 `docs/archive/`，并链接当前入口；旧目录最多保留 `README.md` 跳转页。
