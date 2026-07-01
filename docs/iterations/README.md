# 版本迭代总览

本页用于快速说明 `wisewe-rag-simple` 是如何从早期 PDF 验证脚本演进到当前“知识库系统 + Graph RAG + 控制台”的。详细 phase 仍以 `.planning/**` 为准，这里只保留对团队最有价值的摘要。

## 版本主线

| 版本 | 状态 | 主题 | 主要实现点 | 参考文档 |
|------|------|------|-----------|---------|
| v1.0 | 已归档 | PDF 最短链路验证 | PDF 解析、清洗、切片、embedding、导出最小闭环 | `.planning/milestones/v1.0-phases/` |
| v2.0 | 已完成 | 云端解析 + 向量存储 + RAG + 控制台起步 | MinerU 云解析、pgvector、知识库/文档表、RAG API、前端基础控制台 | `.planning/milestones/v2.0-snapshot-2026-05-29/` |
| v3.0 | 已完成 | 前端重构 | 控制台信息架构重组，单库工作台、入库、问答、评测、设置等页面重构 | [v3.0-frontend-redesign.md](./v3.0-frontend-redesign.md) |
| v3.1 | 已完成 | 前后端真实联调与遗留修复 | 剩余页面接入真实 API，真实 MinerU 管道接入，错误展示和任务链路收口 | `.planning/workstreams/v3.1/` |
| v4.0 | 已完成 | 生产就绪 | Redis 任务持久化、GitHub Actions CI/CD、运行链路更贴近真实环境 | `.planning/workstreams/v4.0/SUMMARY-08.md` |
| v5.0 | 当前范围已完成 | Graph RAG 演进 | typed relations、entities / entity_mentions、流程链、因果链、`/api/rag/graph-query`、离线 benchmark 框架 | `.planning/reports/MILESTONE_SUMMARY-v5.0.md` |
| v6.0 | 进行中 | 可控入库与知识工作台完善 | 可控入库、配置收口、文档 / 知识库图谱预览、Parser Provider 切换、MinerU / Document Mind 大文件分片解析、三层切片性能优化已推进；10-09 向量化性能优化已规划待执行 | `.planning/STATE.md` |

## 各版本摘要

### v1.0：验证“能不能跑通”

目标是把 PDF 文档加工成可用知识片段，验证解析、清洗、切片、向量化和导出链路。

主要落点：

- 早期 PDF 解析与块模型
- 规则清洗与切片策略试验
- embedding 与 CSV / 初始输出能力

### v2.0：把验证链路变成系统骨架

目标是从“离线实验”升级到“真实可调用系统”。

主要落点：

- MinerU 云解析接入
- PostgreSQL + pgvector 存储落地
- 知识库、文档、切片基本表结构
- `/api/rag/query` 相关 RAG 能力
- Next.js 控制台初版

### v3.0 / v3.1：把控制台和真实 API 接起来

目标是让控制台不再只是静态演示界面，而是真正可操作的工作台。

主要落点：

- 页面结构重组
- 单库上下文工作台
- 入库任务追踪
- 问答页、评测页、设置页接真实 API
- 真实 MinerU 管道接入与错误展示收口

### v4.0：补齐工程化底座

目标是解决“能跑”和“可持续运行”之间的差距。

主要落点：

- Redis 任务状态持久化
- GitHub Actions CI / Docker build
- 更稳定的运行与回归验证路径

### v5.0：把向量 RAG 推进到 Graph RAG

目标是从“高质量切片 + 向量检索”升级到“带关系层、实体层、解释路径和离线评测的 Graph RAG”。

主要落点：

- `chunk_relations` typed relation 底座
- `kg_triples`
- `entities`、`entity_mentions`、`mentions`
- `semantic_similar` / `duplicate_of`
- `next_step` / `prev_step` / `cause_of` / `effect_of`
- `/api/rag/graph-query`
- `core/eval/*` 与 `data/eval/textbook-qa.jsonl`

### v6.0：把控制面补齐

当前重点不是再开新故事，而是把已经具备的底层链路做成“可控、可调、可验收”的主线闭环。

当前已完成：

- 可控入库与切片确认
- 设置覆盖层与关键参数可见性
- 文档详情知识图谱预览与关系图例增强
- 知识库级图谱预览
- MinerU 云端大文件分片解析
- 解析 provider 显式切换与阿里 Document Mind POC
- Document Mind 大文件分片解析
- 三层切片严格等价性能优化与热路径优化

当前待推进：

- 10-09 向量化性能优化
- 10-07 在线 RAG 召回性能优化
- 规则配置中心
- 完整图谱工作台

## 如何使用本目录

- 想快速理解项目经历过哪些阶段：先读本页。
- 想看某次大改动的详细背景：进入对应版本文档或 `.planning/` 归档。
- 想知道“现在正在做什么”：看 `.planning/STATE.md` 和 `.planning/ROADMAP.md`。
