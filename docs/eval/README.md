# Graph RAG 评测说明

## 评测分层

当前仓库有两类“评测/评分”能力，职责不同：

- 运行时评分记录：`POST /api/rag/query` 后写入本地评测记录，可通过 `GET /api/console/evaluations` 查看
- 离线 benchmark：`GET /api/eval/reports` 驱动 `core/eval/*` 对数据集做策略对比

这两类能力已经分层，不应混写为同一套体系。

## 当前已落地能力

- 数据集 schema：`core/eval/dataset.py`
- 基础指标：`core/eval/metrics.py`
- Runner：`core/eval/runner.py`
- API：`GET /api/eval/reports`
- 示例数据集：`data/eval/textbook-qa.jsonl`

## 数据集格式

每行一个 JSON 对象：

```json
{
  "id": "q001",
  "kb_id": "default",
  "query": "什么是应急预案？",
  "intent": "concept",
  "ground_truth_chunks": ["chunk-id-a"],
  "ground_truth_answer": "标准答案",
  "cross_section": false,
  "tags": ["应急管理"],
  "notes": "人工备注"
}
```

注意：

- 当前示例里的 `chunk-demo-*` 仍是占位值，正式评测前必须替换成真实入库后的 chunk id。
- `cross_section=true` 预留给跨章节召回指标，不代表该指标已经完成正式验收。

## 调用方式

```http
GET /api/eval/reports?dataset_path=data/eval/textbook-qa.jsonl&strategies=baseline_vector,graph_full
```

返回重点：

- `records`：参与评测的问题数
- `strategies`：本次运行的策略列表
- `summary`：至少包含 `recallAt5`、`mrr`、`ndcgAt5`

## 当前边界

v5.0 已经完成“最小可用离线评测闭环”，但还没有完成以下增强项：

- 50 到 100 条真实人工标注数据集
- Graph RAG Recall@5 与跨章节召回率的正式验收
- RAGAS 接入
- hard-case 挖掘
- CSV / Markdown 报告导出
- 前端独立离线 benchmark 报告页

因此当前准确说法应为：

- “离线评测基础框架已落地，可承接真实数据集与后续指标扩展”

而不是：

- “完整评测体系已经全部完成”
