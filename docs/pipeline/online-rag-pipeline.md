# 在线检索增强问答链路

本文档描述当前仓库里“知识库建好之后，系统如何回答问题”的真实链路。

## 定义

在线检索增强问答链路负责：

1. 接收用户问题
2. 从知识库中取回候选切片
3. 做融合、重排或图扩展
4. 生成答案、引用和评分

它依赖离线入库链路提供的 `chunks`、关系边、实体层和向量底座。

文档详情里的知识图谱预览属于入库质量检查视图，只读取当前文档已经写库的 `chunks`、`chunk_relations`、`entity_mentions` 和 `kg_triples`。该视图不参与在线召回、重排、生成或 Graph RAG 扩展，也不改变 `/api/rag/query` 与 `/api/rag/graph-query` 的行为。

## 当前入口

### 向量 / 混合 RAG

- `POST /api/rag/query`
- `backend/services/rag_service.py`
- `backend/adapters/rag_adapter.py`

### Graph RAG

- `POST /api/rag/graph-query`
- `core/rag/graph_retriever.py`

### 前端工作台

- `frontend/src/app/(console)/query/page.tsx`
- `frontend/src/app/(console)/knowledge-bases/[kbId]/query/page.tsx`

## 标准阶段

```text
用户问题
  -> 查询理解
  -> 在线召回
  -> 融合 / 重排 / 图扩展
  -> 上下文构造
  -> 答案生成
  -> 引用对齐
  -> 评分
  -> 响应输出
```

## 当前代码实现

### A. 普通 RAG 路径

```text
query
  -> HybridRetriever
  -> ParentChildReranker
  -> RAGGenerator
  -> RAGScorer
```

其中 `图/表 + 编号` 这类定位查询会走轻量短路：

```text
图1-3-3 / 表1-3-1
  -> media_ref 直达召回
  -> 直接返回候选、引用和定位答案
```

该短路只用于图表编号定位。命中后不调用 embedding、BM25、rerank API 或 LLM 生成，避免冷启动 BM25 或外部模型调用把单次定位查询拖到分钟级。

核心模块：

- `core/rag/retriever.py`
- `core/rag/reranker.py`
- `core/rag/generator.py`
- `core/rag/scorer.py`

### B. Graph RAG 路径

```text
query
  -> intent router
  -> dense / sparse / entity recall
  -> RRF
  -> related expand
  -> graph expand
  -> context build
  -> structured results
```

核心模块：

- `core/rag/intent_router.py`
- `core/rag/graph_retriever.py`
- `core/rag/graph_expander.py`

## 详细阶段说明

### 1. 查询输入

当前常见参数包括：

- `kb_id`
- `top_k`
- `min_score`
- `use_llm_check`
- `use_llm_score`
- `explain`
- `intent`

### 2. 在线召回

普通 RAG 与 Graph RAG 都会从召回开始，但召回通道不同。

当前主要通道包括：

- 图表编号直达召回：`media_ref`
- 稠密召回：pgvector
- 稀疏召回：BM25
- 实体召回：`entity_mentions`
- related 扩展：基于 `related_ids`
- RRF 融合

### 3. 重排与图扩展

普通 RAG 路径：

- `ParentChildReranker` 对候选进行父子重排
- 生成 `context_window`

Graph RAG 路径：

- `intent_router` 先判断意图
- `graph_expand()` 沿 `chunk_relations` 和实体层做受控扩展
- 结果可附带 explain path

### 4. 生成与引用

- `RAGGenerator` 负责答案生成
- 返回中包含 `answer`、`citations`、`cannotAnswer`

Graph RAG 当前返回以结构化结果为主，重点是：

- `intent`
- `intentSource`
- `results`
- `stats`

### 5. 评分

普通 RAG 路径会产生运行时评分记录，主要包括：

- `relevanceScore`
- `faithfulnessScore`
- 可选 `llmScore`

这些结果会被写入本地评测记录，供：

- `GET /api/console/evaluations`

查询与展示。

## 当前边界

- 运行时评分记录不等于离线 benchmark。
- 普通 RAG 与 Graph RAG 两条路径都已经存在，但前端图谱工作台仍在后续 phase。
- 当前控制台问答页主要展示运行时链路；离线 benchmark 通过独立 API 触发。

## 与离线链路的边界

在线链路从“用户发起问题”开始，到“答案 / 结果 / 评分返回”结束。

它不负责：

- 文档解析
- 清洗
- 切片生成
- 向量建库
- 关系写库

这些都属于 [离线入库链路](./offline-ingestion-pipeline.md)。
