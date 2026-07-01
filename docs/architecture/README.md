# Architecture 文档

本目录用于解释“当前系统实际上是怎么组织的”，不是只保留零散问题分析。

## 当前架构分层

### 1. 接口与应用编排层

- `backend/app.py`：FastAPI 应用装配
- `backend/serve.py`：HTTP 服务入口，启动前会尝试自动补齐数据库 schema
- `backend/cli.py`：CLI 离线链路入口
- `backend/routes/`：控制台、入库、问答、评测、知识库、健康检查等接口
- `backend/services/`：HTTP 场景下的任务编排、控制台聚合、正式文档导出和运行时记录

### 2. 领域能力层

- `core/parser/`：MinerU 云解析、阿里 Document Mind POC、provider 选择、PDF 分片、OSS 上传、结构规范化
- `core/cleaner/`：规则清洗、LLM 清洗、质量门控
- `core/chunker/`：切片策略、hierarchical 有序并发增强、语义边、流程链、因果链
- `core/embedding/`：向量化调用
- `core/rag/`：向量检索、BM25、GraphRetriever、intent router、graph expander
- `core/kg/`：实体合并、定义生成、关系回写
- `core/output/`：CSV 导出、pgvector 写入、关系写入
- `core/db/`：连接、schema、初始化与知识库底层表
- `core/eval/`：离线 benchmark 数据集、指标与 runner

### 3. 前端控制台层

- `frontend/src/app/(console)/overview`：总览页
- `frontend/src/app/(console)/knowledge-bases`：知识库列表与单库工作台
- `frontend/src/app/(console)/ingestion`：入库任务页
- `frontend/src/app/(console)/query`：在线问答页
- `frontend/src/app/(console)/evaluation`：运行时评测页
- `frontend/src/app/(console)/settings`：控制台设置页

### 4. 数据与运行时产物

- `data/uploads/`：持久化上传文件
- `data/logs/`：任务日志
- `data/eval/`：离线 benchmark 数据集
- PostgreSQL / pgvector：知识库、文档、切片、关系、实体与三元组
- Redis：入库任务状态持久化

当前还补了一条“正式入库结果可回看”的导出路径：

- 路由：`GET /api/documents/{document_id}/export.csv`
- 服务：`backend/services/document_export_service.py`
- 数据来源：`documents`、`chunks`、`chunk_relations`、`kg_triples`

当前还包含图谱预览路径：

- 文档级：`GET /api/documents/{document_id}/graph`
- 知识库级：`GET /api/knowledge-bases/{kb_id}/graph`
- 前端复用：`frontend/src/components/knowledge-base/document-graph-view.tsx`

### 5. 规划与文档层

- `.planning/STATE.md`：当前主线状态
- `.planning/ROADMAP.md`：主线 milestone 演进
- `.planning/REQUIREMENTS.md`：需求边界
- `docs/iterations/`：对外更易读的迭代总览与历史文档

## 当前核心数据流

```text
文档上传
  -> parser provider selection
  -> MinerU / Document Mind 云解析
  -> large PDF sharding and global page merge
  -> ContentBlock
  -> 清洗
  -> hierarchical chunking + ordered parallel enhancement
  -> 质量门控
  -> embedding
  -> 实体物化 / 关系补链
  -> pgvector + chunk_relations + kg_triples + entity_mentions

用户提问
  -> dense / sparse / entity recall
  -> RRF
  -> graph expand
  -> context build
  -> answer / citations / scores
```

## 当前重要边界

- 运行时评分记录和离线 benchmark 已经分层。
- Graph RAG 代码位点已经存在，文档 / 知识库图谱预览已经落地；完整图谱工作台仍属于后续 phase。
- 控制台当前已经真实接后端，不再以 mock 成功回退作为主路径。
- 10-09 向量化性能优化已完成；当前性能结论以 [docs/performance-optimizations/](../performance-optimizations/README.md) 为准。

## 本目录当前内容

- [链路问题分析.md](../archive/architecture/链路问题分析.md)：历史问题定位与架构分析记录，已归档

如果需要了解版本演进，请优先阅读 [docs/iterations/README.md](../iterations/README.md)。
