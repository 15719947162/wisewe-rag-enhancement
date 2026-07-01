# wisewe-rag-simple

围绕教材 / 培训文档构建的知识库与 RAG 系统。项目已经从早期 PDF 最短链路验证，演进为包含真实入库、在线问答、Next.js 控制台、Graph RAG 和离线评测基础框架的完整工程。

## 当前状态

- 当前主线里程碑：`v6.0`
- 当前最近完成 phase：`.planning/phases/10-08b-hierarchical-hotpath-equivalence/SUMMARY.md`
- 当前下一步候选：执行 `.planning/phases/10-09-embedding-performance-optimization/PLAN.md`，或继续推进 10-07 在线 RAG 召回性能优化
- 当前已完成范围：
  - 真实 MinerU 云解析与入库
  - PDF 解析 provider 显式切换：默认 MinerU，支持阿里 Document Mind POC
  - MinerU 与 Document Mind 大文件自动分片解析、并发云端任务与全局页码合并
  - PostgreSQL / pgvector 存储
  - 在线 RAG 问答
  - Next.js 控制台
  - Graph RAG 关系层、实体层、检索层
  - 文档级与知识库级图谱预览
  - 可控入库的切片草稿预览 / 编辑 / 删除 / 确认主链路
  - 控制台运行时配置覆盖、敏感字段脱敏与知识库默认切片策略编辑
  - hierarchical 三层切片入库前有序并发增强与热路径优化
  - 离线 benchmark 基础框架

文档归类入口见 [docs/document-map.md](docs/document-map.md)，完整技术链路见 [docs/pipeline/full-chain-technical-guide.md](docs/pipeline/full-chain-technical-guide.md)。版本演进速览见 [docs/iterations/README.md](docs/iterations/README.md)。

## 能力版图

```text
PDF / 文档
  -> parser provider: MinerU(default) / MinerU Official / Ali Document Mind(POC)
  -> OSS / provider cloud parsing
  -> large PDF sharding and global page merge
  -> 清洗
  -> 分层切片 + ordered parallel enhancement
  -> 质量门控
  -> embedding
  -> pgvector / chunk_relations / kg_triples / entity_mentions

query
  -> dense / BM25 / entity recall
  -> RRF
  -> graph expand
  -> context build
  -> answer / citations / scores
```

## Graph RAG 现状

当前仓库已经完成 v5.0 范围内的 Graph RAG 最小闭环，主要包括：

- `chunk_relations` typed relation 层
- `kg_triples`
- `entities` / `entity_mentions` / `mentions`
- `semantic_similar` / `duplicate_of`
- `next_step` / `prev_step` / `cause_of` / `effect_of`
- `/api/rag/graph-query`
- `core/eval/*` + `data/eval/textbook-qa.jsonl` + `/api/eval/reports`

评测分层说明：

- 运行时问答评分记录：`/api/console/evaluations`
- 离线 benchmark：`/api/eval/reports`

两条链路已经分层，不混用。

## 快速开始

### 方式一：Docker Compose

适合直接联调前后端与入库链路。

#### 1. 准备环境变量

复制 `.env.docker.example` 为 `.env`，至少补齐：

- 数据库连接：`DATABASE_URL` 或 `PGVECTOR_*`
- Redis：`REDIS_URL` 如需覆盖默认值
- 真实解析 / 向量化 / 问答：`302AI_*`、`OSS_*`、`LLM_*`

说明：

- `docker-compose.yml` 默认启动 `redis`、`backend`、`frontend`
- 数据库采用外部实例模式，不默认启动本地 PostgreSQL 容器

#### 2. 启动服务

```bash
docker compose up --build
```

默认端口：

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`

#### 3. 查看日志

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

#### 4. 数据库初始化说明

当前后端在启动时会调用 `core.db.init_db.ensure_db_schema()`，写库路径 `core.output.pgvector_writer.write_to_pgvector()` 也会再次确保 schema 存在。

这意味着：

- 常规启动或入库时，系统会自动尝试补齐 `knowledge_bases`、`documents`、`chunks`、`chunk_relations`、`kg_triples`、`entities`、`entity_mentions` 等表
- `db-init` 不再是唯一入口，更适合作为首次排障、手工补库或独立初始化工具

如需手动执行：

```bash
docker compose --profile tools run --rm db-init
```

#### 5. 运行 CLI

```bash
docker compose run --rm cli --pdf data/input/sample.pdf --strategy semantic --clean
docker compose run --rm cli --pdf data/input/sample.pdf --strategy all --clean --clean-llm
```

### 方式二：本地 Python + 前端开发

#### 1. 安装依赖

```bash
pip install -r requirements.txt
cd frontend && npm install
```

#### 2. 配置环境变量

复制 `.env.example` 为 `.env`，按需填写：

```bash
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://your-service/v1
LLM_EMBEDDING_MODEL=text-embedding-v3
DATABASE_URL=postgresql://...
302AI_API_KEY=...
OSS_ENDPOINT=...
```

#### 3. 启动后端

```bash
python backend/serve.py
```

或：

```bash
uvicorn backend.app:app --reload
```

说明：

- `python backend/serve.py` 会在启动前尝试自动补齐数据库 schema
- 直接运行 `uvicorn backend.app:app --reload` 不会经过这层包装；若数据库尚未初始化，建议先跑一次 `backend/serve.py` 或手工执行 `db-init`

#### 4. 启动前端

```bash
cd frontend
npm run dev
```

确保同时设置：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

#### 5. 运行 CLI 离线链路

```bash
python backend/cli.py --pdf data/input/sample.pdf --strategy semantic --clean
python backend/cli.py --pdf data/input/sample.pdf --strategy all --clean --clean-llm
```

#### 6. 兼容旧入口

`main.py` 仍保留为兼容层：

```bash
python main.py --pdf data/input/sample.pdf --strategy all --clean --clean-llm
python main.py --serve-api
```

## 当前主要接口

### 知识库与文档

- `GET /api/knowledge-bases`
- `POST /api/knowledge-bases`
- `PUT /api/knowledge-bases/{kb_id}`
- `DELETE /api/knowledge-bases/{kb_id}`
- `GET /api/knowledge-bases/{kb_id}/graph`
- `GET /api/documents?kb_id=...`
- `GET /api/documents/{document_id}/graph`
- `GET /api/documents/{document_id}/export.csv`

说明：

- `GET /api/documents/{document_id}/export.csv` 用于导出“正式入库后”的文档切片结果，而不是待确认草稿。
- 导出内容基于 `documents`、`chunks`、`chunk_relations`、`kg_triples` 组装，适合验收最终落库成果。
- 当前控制台已在单库工作台的“文档列表”中提供对应下载按钮。

### 入库

- `POST /api/ingestion/upload`
- `GET /api/ingestion/tasks/{task_id}`
- `GET /api/ingestion/stream/{task_id}`
- `POST /api/ingestion/tasks/{task_id}/retry`

### 问答与评测

- `POST /api/rag/query`
- `POST /api/rag/graph-query`
- `GET /api/console/evaluations`
- `GET /api/eval/reports`

### 控制台聚合接口

- `GET /api/console/overview-metrics`
- `GET /api/console/alerts`
- `GET /api/console/queue`
- `GET /api/console/settings`
- `PUT /api/console/settings`
- `GET /api/dashboard/stats`

## 项目结构

```text
wisewe-rag-simple/
├── backend/                  HTTP 接口、服务编排、适配层
├── core/                     领域能力库（parser / cleaner / chunker / rag / kg / db / eval）
├── frontend/                 Next.js 控制台
├── data/                     上传文件、日志、输出产物、评测数据
├── docs/                     文档导航、链路、架构、性能、产品、设计规范
├── tests/                    pytest 测试
├── .planning/                主线 roadmap / state / requirements / phase 文档
├── main.py                   兼容入口
├── config.yaml               配置文件
├── .env.example              本地环境变量模板
└── .env.docker.example       Docker 环境变量模板
```

## 推荐阅读

1. [docs/document-map.md](docs/document-map.md)
2. [docs/pipeline/full-chain-technical-guide.md](docs/pipeline/full-chain-technical-guide.md)
3. [docs/pipeline/README.md](docs/pipeline/README.md)
4. [docs/iterations/README.md](docs/iterations/README.md)
5. [docs/performance-optimizations/README.md](docs/performance-optimizations/README.md)
6. [docs/architecture/README.md](docs/architecture/README.md)
7. [.planning/STATE.md](.planning/STATE.md)

## 说明

- 控制台设置页已具备运行时配置查看、部分可编辑覆盖和敏感字段脱敏；规则中心仍在后续 phase 规划内。
- Graph RAG 基础设施已经落地；文档级 / 知识库级图谱预览已完成，完整图谱工作台仍在后续 phase 规划内。
- 如果你只想快速理解项目经历过哪些阶段，优先看 [docs/iterations/README.md](docs/iterations/README.md)。
