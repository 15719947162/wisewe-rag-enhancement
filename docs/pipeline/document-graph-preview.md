# 文档详情知识图谱预览

文档详情知识图谱预览是入库后的质量检查视图，用来查看单个文档范围内已经写库的切片、实体、关系和三元组。它服务于人工验收和 Graph RAG 数据基础检查，不参与在线召回排序，也不改变问答链路行为。

## 入口

- 前端入口：文档详情弹窗中的“知识图谱”视图
- 后端接口：`GET /api/documents/{document_id}/graph`
- 渲染组件：`frontend/src/components/knowledge-base/document-graph-view.tsx`

## 数据来源

该视图只读取当前 `document_id` 对应的数据：

- `chunks`：生成 chunk 节点，区分文本、图片、表格切片
- `chunk_relations`：生成 chunk 到 chunk 的关系边
- `entity_mentions` + `entities`：生成实体节点和 mentions 边
- `kg_triples`：生成三元组 term 节点、triple 边和 source 边

## 关系视图

前端按用户理解把关系拆成三个视图：

- **切片关系**：默认视图，用于检查文本、图片、表格切片之间的结构连接。只展示 `adjacent`、`sibling`、`refers_to`。
- **实体关系**：用于检查实体抽取与三元组沉淀。只展示 `mentions`、`triple`、`triple_source`。
- **全部关系**：用于排查图谱数据完整性，展示当前文档内所有节点和关系。

切片关系回答“文本和文本之间是否有关联、文本是否引用图表”；实体关系回答“文本提到了哪些实体、抽取出了哪些结构化知识”。两者都是同一个文档图谱 payload 的不同前端过滤视图，不改变后端数据。

## 图例

节点图例：

- 灰色圆圈：文本切片
- 蓝色方块：图片切片
- 橙色方块：表格切片
- 青绿色圆点：实体或三元组术语

边图例：

- `adjacent` / 相邻切片：文档顺序上的前后连接
- `sibling` / 同级切片：同一结构层级下的切片连接
- `refers_to` / 引用指向：正文、图、表或其他切片之间的引用关系
- `mentions` / 提到实体：切片提到了某个实体
- `triple` / 三元组关系：结构化三元组中的主语到宾语关系
- `triple_source` / 三元组来源：三元组术语回溯到来源切片

## Payload 形态

接口返回稳定的三段结构：

```json
{
  "documentId": "doc-1",
  "nodes": [],
  "edges": [],
  "stats": {
    "nodeCount": 0,
    "edgeCount": 0,
    "chunkCount": 0,
    "entityCount": 0,
    "tripleCount": 0,
    "truncated": false
  }
}
```

## 边界规则

- 只展示当前文档内图谱，不做跨文档扩展。
- 默认最多返回 100 个节点，超限时 `stats.truncated=true`。
- 超限后只保留两端节点都已展示的边，避免前端出现悬空边。
- 空图谱返回 200 和空数组，由前端展示空状态。
- 该视图不触发 embedding、BM25、rerank、LLM 或 Graph RAG 扩展。

## 与在线问答链路的关系

文档图谱预览复用离线入库写入的关系数据，但它不是在线 RAG 的召回阶段。在线问答仍由 `/api/rag/query` 与 `/api/rag/graph-query` 负责，图谱预览只帮助检查这些接口未来可能使用的数据基础是否完整。
