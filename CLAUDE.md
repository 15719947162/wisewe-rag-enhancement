# CLAUDE.md

本文件为 Claude Code 在此仓库中工作时提供指引。

## 项目简介

PDF → RAG 知识库构建管道，用于对比不同切片策略的效果。完整链路：MinerU 云端解析 → 规则/LLM 清洗 → 切片（6 种策略）→ 质量门控 → 向量化 → CSV 导出。核心目标是策略对比，非生产级规模。

## 运行方式

**HTTP 服务（前后端联调）：**
```bash
python backend/serve.py
uvicorn backend.app:app --reload
```

**CLI（批量/脚本运行）：**
```bash
# Mock 模式（无需 MinerU 或 API Key）
python backend/cli.py --pdf data/input/sample.pdf --strategy all --mock

# 真实解析 + Mock 向量化
python backend/cli.py --pdf data/input/sample.pdf --strategy fixed_length --mock-embedding

# 完整真实运行
python backend/cli.py --pdf data/input/sample.pdf --strategy all --clean
```

**兼容旧入口（legacy）：**
```bash
python main.py --pdf data/input/sample.pdf --strategy all --mock
python main.py --serve-api
```

**测试：**
```bash
python -m pytest tests/
# 运行单个测试文件
python -m pytest tests/test_chunker.py
# backend smoke tests
python -m pytest tests/test_backend_app.py tests/test_api_console.py -q
```

**验证解析输出：**
```bash
python scripts/verify_parse.py
```

## 环境配置

将 `.env.example` 复制为 `.env` 并填写 API Key。Embedding 客户端按以下优先级解析密钥：
1. 函数参数显式传入
2. `LLM_API_KEY` / `LLM_BASE_URL`（通用）
3. `DASHSCOPE_API_KEY` → 自动设置 DashScope base URL
4. `OPENAI_API_KEY` → 默认 OpenAI base URL

相关环境变量：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_EMBEDDING_MODEL`、`LLM_EMBEDDING_BATCH_SIZE`、`DASHSCOPE_API_KEY`、`LLM_CLEANER_MODEL`、`LLM_CLEANER_API_KEY`、`302AI_API_KEY`、`302AI_API_BASE`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_BUCKET`、`PGVECTOR_HOST`、`PGVECTOR_PORT`、`PGVECTOR_DB`、`PGVECTOR_USER`、`PGVECTOR_PASSWORD`、`RAG_LLM_MODEL`。

前端联调变量（`frontend/.env.local`）：`NEXT_PUBLIC_DATA_MODE`（`mock`/`live`）、`NEXT_PUBLIC_API_BASE_URL`。

PDF 解析使用 302.ai MinerU 云端 API，无需本地模型文件或安装 `magic-pdf`。管道将 PDF 上传至阿里云 OSS，提交解析任务，轮询完成后下载包含 `*_content_list.json` 和 `images/` 图片的结果 ZIP，解压到 `data/output/`。

## 架构

### 目录职责

```
backend/          HTTP 服务应用层（routes / schemas / services / adapters）
core/             领域能力库（parser / chunker / cleaner / embedding / rag / db / output）
frontend/         Next.js 前端（独立，支持 mock/live 双模式）
tests/            pytest 测试套件
scripts/          工具脚本（verify_parse.py）
data/input/       输入 PDF
data/output/      运行时生成物（gitignored）
data/results/     实验对比存档
docs/             文档（architecture / design / research / rules）
ml-models/        ML 模型文件（gitignored）
```

依赖方向：`backend → core`，`frontend → backend`（HTTP）。

### 数据流

```
PDF 文件
  → upload_pdf_to_oss()    # core/parser/mineru_parser.py → OSS 签名 URL
  → parse_pdf_from_url()   # core/parser/mineru_parser.py → list[ContentBlock]
  → clean_blocks()         # core/cleaner/__init__.py → CleanResult
  → strategy.chunk()       # core/chunker/<strategy>.py → list[Chunk]
  → apply_quality_gate()   # core/cleaner/quality_gate.py → QualityGateResult
  → embed_texts()          # core/embedding/client.py → list[list[float]]
  → write_knowledge_csv()  # core/output/csv_writer.py → CSV 文件
  → write_to_pgvector()    # core/output/pgvector_writer.py → pgvector 写入
```

### 核心模型（`core/models/content_block.py`）

- `ContentBlock` — 解析器输出。关键字段：`type`（BlockType 枚举）、`text`、`page_idx`、`is_table`、`table_html`、`image_path`、`bbox`。
- `Chunk` — 切片策略输出。关键字段：`content`、`source`、`page`、`chunk_index`、`strategy`、`layer`（`parent`/`child`/`enhanced`）、`parent_id`、`related_ids`、`enhanced_text`。

### 切片策略（`core/chunker/`）

所有策略继承 `ChunkingStrategy`（ABC），通过 `@register_strategy` 装饰器注册。使用 `get_strategy(name, **params)` 获取实例。

| 策略 | 文件 | 说明 |
|---|---|---|
| `fixed_length` | `fixed_length.py` | 按字符数硬切，带重叠 |
| `paragraph` | `paragraph.py` | 自然段落边界，合并短段，拆分长段 |
| `semantic` | `semantic.py` | 按 MinerU `text_level` 标题层级分组 |
| `separator` | `separator.py` | 按可配置标点列表切分 |
| `llm` | `llm_chunker.py` | LLM 判断语义边界 |
| `hierarchical` | `hierarchical.py` | 三层：父级（章节）→ 子级（知识点）→ 增强（LLM 摘要） |

`linker.py` 中的 `link_related_chunks()` 后处理切片，填充 `related_ids`（将文本切片与相邻表格/图片切片关联）。

### 清洗管道（`core/cleaner/`）

`clean_blocks()` 先执行规则清洗，再可选 LLM 清洗。默认规则：`RemoveEmptyBlocks`、`RemoveShortBlocks(min_chars=2)`、`RemovePunctuation(threshold=0.8)`、`RemoveCopyrightAds`。图片块在所有规则中均豁免，不会被误删。每条规则实现 `CleanerRule.apply(blocks) → CleanResult`。

质量门控（`quality_gate.py`）在切片后执行：按标点比例过滤，可选 LLM 评分（1–5 分）。

### 解析器（`core/parser/`）

`mineru_parser.py` 提供三个公共入口：

- `upload_pdf_to_oss(pdf_path, log_fn, original_name) -> str` — 通过 `oss_uploader.py` 将 PDF 上传至阿里云 OSS，返回签名 URL。
- `parse_pdf_from_url(pdf_url, pdf_path, output_dir, log_fn) -> list[ContentBlock]` — 向 302.ai 提交 MinerU 任务（`_submit_task`），轮询至 `SUCCESS`（`_poll_task`），下载结果 ZIP（`_download_zip`），提取 `*_content_list.json` 并将 `images/*` 写入 `output_dir`（`_extract_and_map`），最终映射为 `ContentBlock` 列表（`_convert_content_list`）。
- `parse_pdf(pdf_path, output_dir, log_fn, original_name) -> list[ContentBlock]` — 便捷包装，依次调用上述两个函数。

`column_reorder.py` 对多栏布局重排块顺序（`two_col_lr`、`two_col_rl`、`three_col`）。

### 配置

`config.yaml` 设置 `parser.mode`、`parser.cloud.*`（parse_method、version、timeout、poll_interval）、`parser.oss.*`（prefix、url_expiry）、`embedding.*`、`output.*`。通过 `core/config.py:load_config()` 加载。`.env` 在运行时提供 API Key（302AI、OSS、Embedding、pgvector）。
