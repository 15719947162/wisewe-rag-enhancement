# Docs 导航

`docs/` 用来承接项目级说明、链路文档、设计规范和历史迭代记录。这里不再假设仓库仍停留在早期 PDF 验证阶段，当前口径以“完整知识库系统 + Graph RAG 演进 + v6.0 主线实施中”为准。

## 当前实现快照

- v5.0 Graph RAG 当前范围已完成：typed relations、实体层、流程/因果链、Graph 检索与离线 benchmark 基础框架。
- v6.0 已完成可控入库、配置收口、文档 / 知识库图谱预览、MinerU / Document Mind 解析 provider 切换与大文件分片解析。
- 三层切片性能优化已推进到 10-08b：入库前有序并发 enhanced、worker 内 LLM client 复用、linker 去重热路径优化与 `chunkTimings` 观测字段扩展。
- 10-09 向量化性能优化已完成；性能优化档案已从链路文档中拆出，按解析、切片、向量化三线独立归档。

## 推荐阅读顺序

1. 根目录 [README.md](../README.md)
2. [document-map.md](./document-map.md)
3. [pipeline/full-chain-technical-guide.md](./pipeline/full-chain-technical-guide.md)
4. [pipeline/README.md](./pipeline/README.md)
5. [iterations/README.md](./iterations/README.md)
6. [performance-optimizations/README.md](./performance-optimizations/README.md)
7. [architecture/README.md](./architecture/README.md)
8. `.planning/ROADMAP.md` 与 `.planning/STATE.md`

## 目录说明

- `agent/`：Codex / GSD / 共享账本协作约定
- `architecture/`：当前系统结构、问题分析、架构判断
- `archive/`：旧入口、历史参考、早期性能讨论和已迁移文档说明
- `design/`：控制台设计系统与 UI 规范
- `eval/`：离线 benchmark 与评测边界说明
- `iterations/`：版本演进摘要与单次迭代文档
- `performance-optimizations/`：解析、切片、向量化三线性能优化档案
- `performance-discussions/`：早期性能讨论旧入口；正文已归档到 `archive/performance-discussions/`
- `pipeline/`：离线入库、在线 RAG、召回和规则链路说明
- `product/`：产品参数、验收口径和对外交付表达
- `research/`：外部调研与历史分析材料
- `rule/`：切片、清洗、Graph RAG 设计规则与定位分析

## 维护约定

- 顶层 `README.md` 负责“项目是什么、怎么运行、当前做到哪一步”。
- `docs/document-map.md` 负责“文档应该去哪里找、后续新增文档应放哪里”。
- `docs/iterations/` 负责“项目是怎么一步步演进到今天的”。
- `docs/pipeline/` 和 `docs/eval/` 负责“系统链路是如何工作的”。
- `docs/performance-optimizations/` 负责“性能瓶颈如何被发现、优化和回退”。
- `docs/product/` 负责“面向产品、验收和招标的能力表达”。
- `.planning/**` 仍是主线规划与状态事实源，`docs/` 负责把这些事实整理成更适合阅读的说明文档。
