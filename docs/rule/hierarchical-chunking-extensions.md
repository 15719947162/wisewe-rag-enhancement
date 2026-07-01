# 三层切片法扩展方案：关联关系与知识图谱

> 配套文档：`docs/rule/hierarchical-chunking.md`
> 围绕 parent / child / enhanced 三层结构，向「类型化关联 → 实体层 → 知识图谱 → Graph RAG」演进

---

## 现状盘点

```
纵向三层（载体）   parent → child → enhanced
横向关联         related_ids  ← 仅 3 条局部规则（引用号 / 邻近 / 同父）
```

当前在线问答召回口径：

```text
parent   结构导航层：默认不参与主召回，不作为最终 citation
child    证据层：参与主召回，是最终 evidence / citation 单元
enhanced 语义入口层：参与辅助召回，命中后折叠回对应 child
```

因此，向量检索不再理解为“只查 child”，而是：

```text
默认检索 child + enhanced
enhanced 命中 → enhanced.parent_id → child.id
最终答案引用 → child
parent 仅用于章节路径、上下文补全、结果分组和导航
```

四处短板：

1. **关联无类型、无权重、无来源** —— 不知道两个 chunk 是「互为引用」「互为对照」「包含关系」还是「顺序关系」
2. **关联是局部的** —— 跨章节、跨文档无法贯穿
3. **关联只在 chunk 之间** —— 没有第四类节点（实体 / 概念）来收敛知识
4. **enhanced 层只生成「摘要 + 问题」，没沉淀结构化知识** —— LLM 调用一次的产出未被复用

下面 6 个方向围绕这 4 处短板展开。

---

## 方向 1｜关联类型化：把 `related_ids` 升级为带语义的边

把 `related_ids: list[str]` 改造成：

```python
relations: list[Relation]

class Relation:
    target_id: str
    rel_type: Literal[
        "refers_to",      # 文本引用图/表
        "explains",       # 解释关系
        "contrasts",      # 对照关系
        "example_of",     # 举例
        "next_step",      # 操作链：下一步
        "prev_step",      # 操作链：上一步
        "cites",          # 引用外部规范/标准
        "duplicate_of",   # 高度重复
        "depends_on",     # 依赖关系
        "cause_of",       # 因果关系
        "co_occurs",      # 同章节共现
    ]
    weight: float        # 0~1 置信度
    source: Literal["rule", "embedding", "llm", "entity"]
    evidence: str        # 简要原文片段，便于审计
```

实施切口：

- `linker.py` 的三条规则各自打 `source="rule"`，分别对应 `refers_to` / `adjacent` / `sibling`
- 教材图/表编号引用应显式进入 `refers_to`：例如 `图1-3-3-6`、`如图1-3-3-6`、`表1-3-3-1`、`如表1-3-3-1`
- `Relation.evidence` 保留原始引用片段，便于解释“为什么这段文本和这张图/这张表相关”
- 新增 LLM / embedding 两类规则后，统一走同一个 Relation 通道
- **存储侧**：pgvector 加一张 `chunk_relations` 表（`src_id, dst_id, type, weight, source, evidence`），便于 Graph 查询

收益：检索时可按关系类型筛选邻居（「只要操作链邻居」「只要图表邻居」），rerank 时可加权。

图表引用的业务价值：

- 很多教材图片/表格本体只有 `图1-3-1`、`表1-2-1` 等短说明，单靠原文 embedding 召回效果弱
- 正文中常出现“见表...”“如图...”这样的证据链，直接关系答案和检验结果解释的正确性
- 将这些引用写成 `refers_to` 后，命中文本可以扩展到图/表，命中图/表增强描述也可以回到原始 child 证据

---

## 方向 2｜跨章节语义边：embedding 相似度补关联

linker 当前完全靠局部规则，全文范围内的「同主题分散内容」无法连。

做法：

1. 子级切完后，对所有 child 算 cosine 相似度矩阵；enhanced 只作为召回入口，不作为最终相似边证据
2. 阈值 + topK 双闸：相似度 > 0.85 且为对方 top10 → 加 `semantic_similar` 边，`weight = cos`
3. 相似度 > 0.95 → `duplicate_of` 边（用于检索去重）
4. 同 parent 内不重复加（已有 sibling 关系）

注意：教材里「概述—展开—总结」会自然命中此规则，能形成**主题链**。

---

## 方向 3｜实体层：在三层之外，加一个「实体节点」层

这是从「文档结构」迈向「知识图谱」的关键一步。

### 数据建模

```
parent (章节)
   ↓ contains
child (知识点) ←→ mentions ←→ Entity (实体节点)
   ↓ has_enhanced                    ↑
enhanced (摘要)                      │
                              EntityType (Concept / Procedure /
                                         Equipment / Standard /
                                         Quantity / Person / Time)
```

`Entity` 是新引入的第四类节点：

```python
class Entity:
    id: str
    name: str                    # 规范名
    aliases: list[str]           # 别名 / 缩写（如 "GB/T 28001" / "OHSAS"）
    type: EntityType
    definition: str | None       # 取自首次出现的 child 或 LLM 总结
    source_chunks: list[str]     # 提到该实体的所有 child id
    embedding: list[float] | None  # 用于实体消歧
```

### 抽取流程

子级生成后追加一步 `extract_entities(child)`：

- 教材类用提示工程足够：让 LLM 输出 `{name, type, aliases, role_in_text}` JSON
- 同名跨 chunk 自动合并到同一 Entity（先按字面匹配，再用 embedding 消歧 > 0.9 视为同实体）
- Entity 反向连回所有 source_chunks → **同实体的不同 chunk 自动互联**（强力补充跨章节关联）

收益：

- 检索时 `query → entity` 精确匹配 → `entity.source_chunks` 召回（解决向量检索对术语 / 型号 / 标准号不敏感的痛点）
- 同实体跨章节贯穿，天然解决「如上所述 / 详见」指代

---

## 方向 4｜三元组抽取：把 enhanced 的 LLM 调用产出「再榨一次」

目前 enhanced 调一次 LLM 只换一段摘要文本，性价比偏低。在同一次调用里**追加结构化输出**，零额外成本拿到知识图谱原料。

改造 enhanced 的 Prompt，让 LLM 同时返回：

```json
{
  "summary": "...",
  "questions": ["...", "...", "..."],
  "entities": [{"name": "应急预案", "type": "Concept"}],
  "triples": [
    {"s": "应急预案", "p": "包含", "o": "应急响应流程", "confidence": 0.9},
    {"s": "应急预案", "p": "依据", "o": "GB/T 29639", "confidence": 0.85}
  ]
}
```

落库：

- triples 入 `kg_triples` 表
- entities 写回 Entity 节点
- summary / questions 仍按原方式存 `enhanced.enhanced_text`

为什么有用：

- enhanced 层从「文本辅助检索」升级为「知识图谱构建器」
- 三元组可独立做 Graph RAG，也可与向量召回融合（GraphRAG 的核心做法）
- **无新增 LLM 调用，仅调整提示词**

---

## 方向 5｜流程 / 因果链：把「操作类」切片串成有向链

规则文档里已经识别「步骤 / 操作 / 流程 / 方法」关键词并放宽切片长度，但**没有把步骤之间显式连起来**。教材里操作类内容占比很高，这是高价值低成本的扩展。

做法：

1. 在 `hierarchical.py` 的「操作类」判定后打标 `is_procedure_chunk=True`
2. 新增 `procedure_linker.py`：在同一 parent 下的 procedure chunks 之间
   - 用正则识别「第一步 / Step 1 / ① / 1) / 首先 / 接着 / 最后」等序号或时序词
   - 串成 `next_step` / `prev_step` 双向边
3. 检索「如何做 X」类问题时，命中链头后**整链召回**（沿 `next_step` 走到底）

同理可扩展因果链：识别「因为 / 由于 / 导致 / 所以 / 因此」建立 `cause_of` / `effect_of`。

---

## 方向 6｜检索侧：从向量 RAG 升级为 Graph RAG

光有关系网不查询等于零。检索流程改造：

```
query
 ├─→ embedding 召回 top-k child         （现状）
 ├─→ entity 精确 / 模糊匹配 → entity.source_chunks
 ├─→ BM25 关键词召回                     （术语 / 型号场景）
 └─→ 合并去重 → seed chunks
        ↓
        图扩展：沿 relations 走 1~2 跳
        - rel_type = explains / example_of：必扩展
        - rel_type = next_step：操作类问题必扩展
        - rel_type = mentions(entity)：跨章节聚合
        ↓
        rerank（cross-encoder 或 LLM）
        ↓
        组装上下文（parent 标题链 + 命中 child + 邻居 + 实体定义）
```

加一个**意图路由**：

| 查询类型 | 检索策略 |
|---|---|
| 概念查询 | 父级链 + 实体定义 + 一跳邻居 |
| 操作查询 | procedure 链全召回 |
| 数据查询 | table chunk 优先 + table 增强摘要 |
| 视觉查询 | image chunk + image 增强描述 |

---

## 优先级与实施路径

| 优先级 | 扩展项 | 工作量 | 与现有代码的接口 |
|---|---|---|---|
| P0 | 关系类型化（方向 1） | 1~2 天 | 改 `Chunk.related_ids` → `relations`，linker 三条规则各打类型 |
| P0 | enhanced Prompt 加 entities / triples 输出（方向 4） | 1 天 | 改 `_make_text_enhanced` 的 Prompt + JSON 解析 |
| P0 | 跨章节 embedding 相似度边（方向 2） | 0.5 天 | 在 linker 末尾加一步全局矩阵计算 |
| P1 | Entity 节点 + source_chunks 反向连接（方向 3） | 3~5 天 | 新增 `core/kg/entity.py`，从 triples 物化 |
| P1 | procedure 链（方向 5） | 1 天 | 新增 `procedure_linker.py` |
| P1 | Graph RAG 检索（方向 6） | 3~5 天 | 改 `core/rag` 检索流程，加 BM25 + 图扩展 |
| P2 | 概念层（ontology）/ GraphRAG 社区检测 / HippoRAG | 视调研 | 架构级，需要 PoC |

---

## 统一心智模型

把整个系统看成 **4 层 × 2 类边** 的异构图：

```
节点层：
  L1 parent      章节骨架
  L2 child       知识点 / 图片 / 表格（最终证据入口）
  L3 enhanced    检索辅助文本（可被召回，但必须回指 child）
  L4 entity      跨章节 / 跨文档收敛点  ← 新增

边类型：
  结构边（structural）：contains / has_enhanced            ← 三层切片自带
  语义边（semantic）：refers_to / explains / next_step /
                     mentions / similar_to / cause_of    ← 扩展重点
```

三层切片解决「**怎么把 PDF 拆成可检索的颗粒**」，关联与图谱解决「**这些颗粒怎么连成一张能被推理的网**」。两者正交，叠加之后才是完整的 RAG 知识底座。

---

## 在线召回与引用展示补充

### 召回折叠

enhanced 层可以提升召回，尤其是图片描述、表格摘要和问题改写。但 enhanced 是模型生成内容，不应作为最终事实证据。因此召回后必须做折叠：

```text
命中 enhanced
  ↓
读取 enhanced.parent_id
  ↓
定位对应 child
  ↓
候选列表与答案引用展示 child
  ↓
enhanced 仅保留为 matched_by / matched_enhanced_id / 命中解释
```

同一个 child 被多个 enhanced 命中时，应合并为一个候选，并合并分数与命中来源。

### 引用来源

召回测试、在线问答和 citation 展示应优先使用规范文档名：

```text
documentName = documents.filename
location = P.{page} · #{chunkIndex + 1}
```

示例：

```text
正确：带表格的教材片段.pdf P.1 · #3
错误：0bbf82c7-1057-4007-98e8-78d74c5a6522.pdf P.1
```

这样可以区分“上传落盘文件名”和“用户理解的教材文档名”，避免召回测试时误判来源。

---

## 数据模型增量汇总

为方便后续 PLAN 落地，集中列出新增 / 修改的核心数据结构。

### 修改：`Chunk`

```python
# 旧
related_ids: list[str]

# 新
relations: list[Relation]
is_procedure_chunk: bool      # 操作类标记
procedure_order: int | None   # 在 procedure 链中的序号
```

### 新增：`Relation`

```python
class Relation:
    target_id: str
    rel_type: str
    weight: float
    source: str
    evidence: str
```

### 新增：`Entity`

```python
class Entity:
    id: str
    name: str
    aliases: list[str]
    type: EntityType
    definition: str | None
    source_chunks: list[str]
    embedding: list[float] | None
```

### 新增：`Triple`

```python
class Triple:
    s: str            # 主语 entity name
    p: str            # 谓语
    o: str            # 宾语 entity name
    confidence: float
    source_chunk: str # 来源 child id
```

### 数据库新增表

| 表名 | 用途 |
|---|---|
| `chunk_relations` | 存储类型化关联边 |
| `entities` | 实体节点（含 embedding 用于消歧） |
| `entity_mentions` | entity ↔ chunk 多对多映射 |
| `kg_triples` | 三元组 |
