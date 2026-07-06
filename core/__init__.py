"""
Core - 核心领域能力库

本包是 RAG 系统的核心能力层，提供 PDF 解析、内容清洗、切片、向量化、RAG 检索等关键能力。
核心原则：**不依赖任何 HTTP 框架**，可以独立运行和测试。

## 设计理念

```
core/ 就像一个"工具箱"，每个子模块是一类工具：
- parser/ 是"扫描仪"，把 PDF 变成结构化内容
- cleaner/ 是"过滤器"，清洗掉无效内容
- chunker/ 是"切割刀"，把长文本切成小段
- embedding/ 是"编码器"，把文本变成向量
- rag/ 是"问答机"，检索知识并生成答案
```

这些工具可以：
1. 被 HTTP 服务（backend/）调用
2. 被命令行工具（main.py, backend/cli.py）调用
3. 被测试脚本独立测试
4. 被其他项目复用

## 子模块概览

| 模块 | 职责 | 核心类型 |
|------|------|----------|
| models/ | 领域模型定义 | ContentBlock, Chunk |
| parser/ | PDF 解析（MinerU 云端 API） | parse_pdf(), upload_pdf_to_oss() |
| cleaner/ | 内容清洗管道 | clean_blocks(), QualityGate |
| chunker/ | 切片策略（6 种） | get_strategy(), ChunkingStrategy |
| embedding/ | 文本向量化 | embed_texts() |
| rag/ | RAG 检索生成 | HybridRetriever, RAGGenerator |
| db/ | 数据库操作 | pgvector 写入、知识库管理 |
| output/ | 结果输出 | CSV 导出、pgvector 写入 |
| prompts/ | LLM 提示词模板 | 清洗/切片/RAG 用的提示词 |

## 数据流转

```
PDF 文件
    ↓ parser 解析
ContentBlock 列表
    ↓ cleaner 清洗
ContentBlock 列表（干净）
    ↓ chunker 切片
Chunk 列表
    ↓ embedding 向量化
向量列表
    ↓ output 存储
数据库/CSV 文件
```

## 快速开始

```python
from core.parser import parse_pdf
from core.cleaner import clean_blocks
from core.chunker import get_strategy

# 解析 PDF
blocks = parse_pdf("data/input/sample.pdf", "data/output/")

# 清洗内容
clean_result = clean_blocks(blocks)
print(f"移除了 {clean_result.removed_count} 个无效块")

# 切片
chunker = get_strategy("paragraph")
chunks = chunker.chunk(clean_result.blocks)
print(f"切出了 {len(chunks)} 个知识片段")
```
"""

# core/ 是一个聚合包，各子模块独立导入
# 这里不统一导出，避免循环导入和不必要的依赖
__all__: list[str] = []
