# 三层切片法（Hierarchical Chunking）规则说明

> 对应实现：`core/chunker/hierarchical.py`、`core/chunker/linker.py`

---

## 层级结构

```
父级 (parent)   ← 章节标题，上下文容器，默认不参与在线问答主召回
  └── 子级 (child)   ← 知识点块 / 图片 / 表格，最终证据与引用单元
        └── 增强 (enhanced)   ← LLM 生成摘要+问题，参与辅助召回，命中后回指 child
```

---

## 第一层：父级 (parent)

**触发条件**：遇到 `BlockType.TITLE` 类型的块。

**规则**：
- 每遇到标题，立即 flush 当前文本缓冲区（生成子级），再创建新的父级块
- 父级内容 = 标题文本本身
- 父级**不生成**增强块
- 后续所有子级的 `parent_id` 指向此父级的 `id`

**作用**：提供章节上下文，在 UI 中展示树形结构，并用于召回后的上下文补全、结果分组和详情导航。

**在线召回口径**：
- parent 默认**不参与在线问答主向量召回**
- parent 不作为最终答案证据引用
- 命中 child 后，可通过 `parent_id` 回溯 parent，用于展示章节路径、标题链和同章节扩展
- 如后续支持“章节概览 / 目录导航 / 本章讲了什么”等意图，可在专门检索模式中启用 parent

---

## 第二层：子级 (child)

### 文本子级

**触发条件**：普通文本块（`BlockType.TEXT`）。

**规则**：
- 文本块先进缓冲区，遇到标题/表格/图片时统一 flush
- flush 时将缓冲区所有文本合并（`\n` 连接），再按 `child_max_chars`（默认 800）切分
- **操作类内容特殊处理**：若文本含"步骤/操作/流程/方法/过程/程序/要点/注意事项/procedure/step"等关键词，`max_chars` 自动翻倍（上限 1000），避免操作步骤被截断
- 切分优先按句子边界（从右向左找）：`。 ； ！ ？ .\n \n\n \n`，找不到才硬切

### 表格子级

**触发条件**：`BlockType.TABLE`。

**规则**：
- 遇到即立即 flush 缓冲区，然后直接生成一个子级块
- `is_table_chunk = True`
- 内容优先用 `table_html`，其次用 `text`

### 图片子级

**触发条件**：`BlockType.IMAGE`。

**规则**：
- 遇到即立即 flush 缓冲区，然后直接生成一个子级块
- `is_image_chunk = True`
- 内容用 `block.text`（OCR 说明文字），无则填 `[图片 第N页]`
- `image_path` 记录本地图片路径

### 不生成增强块的条件

- 内容少于 20 字符（表格/图片）或 50 字符（文本）

---

## 第三层：增强 (enhanced)

**触发条件**：子级生成后，若 `enable_enhanced=True` 则按子级类型分发到对应增强函数。

增强块统一属性：
- `layer = "enhanced"`
- `parent_id` = 对应**子级**的 `id`（增强块的 parent 是子级，不是父级标题）
- `enhanced_text` 存储 LLM 原始输出（不含前缀标签）
- `token_cost` 记录本次 LLM 消耗的 token 数

**在线召回口径**：
- enhanced 可以参与默认 RAG 召回，尤其用于图片描述、表格摘要、术语改写和潜在问题匹配
- enhanced 命中后必须回指到对应 child：`enhanced.parent_id -> child.id`
- 最终 evidence / citation 以 child 为准，enhanced 只作为召回辅助、排序辅助和命中解释
- 同一个 child 被多个 enhanced 命中时，应合并为一个 child 证据，避免候选列表重复膨胀

### 增强类型一：文本增强 `[LLM增强]`

**触发条件**：普通文本子级，内容 ≥ 50 字符，且不满足片段增强条件。

**LLM Prompt**：
```
你是一个教材知识点摘要助手。请为以下内容生成一段增强检索文本，
包含：1) 一句话摘要；2) 3个可能的检索问题。

所属章节：{title}
原文内容：{content[:800]}

请直接输出增强文本（不要标题、不要编号前缀）：
```

### 增强类型二：图片描述 `[图片描述]`

**触发条件**：`is_image_chunk=True` 且 `enable_image_enhanced=True`。

**处理逻辑**：
1. 若配置了 VL 模型（`VL_MODEL` 环境变量）且存在 `image_path`，读取图片 base64 调用视觉语言模型
2. 否则 fallback：将 `block.text`（OCR 说明文字）作为 alt_text，调用文本 LLM 推断描述

**VL 模型 Prompt**：
```
这是教材《{title}》中的一张图片。请完成以下任务：
1) 用2-3句话描述图片的主要内容；
2) 说明该图片在教学中的作用或意义；
3) 列出图片中出现的关键概念或标注（如有）。
请直接输出描述，不要加标题或编号前缀。
```

**Fallback 文本 Prompt**：
```
你是一个教材内容助手。以下是教材《{title}》中一张图片的说明文字：
图片说明：{alt_text}

请根据上下文推断该图片可能展示的内容，并生成：
1) 图片内容的推断描述（2-3句）；
2) 该图片在教学中的可能作用；
3) 2个与该图片相关的检索问题。
```


### 增强类型三：表格摘要 `[表格摘要]`

**触发条件**：`is_table_chunk=True` 且 `enable_table_enhanced=True`，内容 ≥ 20 字符。

**LLM Prompt**：
```
你是一个教材内容分析助手。以下是教材《{title}》中的一个表格：

{table_content[:1200]}

请完成以下任务：
1) 用1-2句话概括表格的主题和核心内容；
2) 解释表格中出现的专业术语或缩写（如有，列出3个以内）；
3) 提炼表格中最重要的1-2条数据规律或结论。
请直接输出分析内容，不要加标题或编号前缀。
```

### 增强类型四：片段增强 `[片段增强]`

**触发条件**：普通文本子级，`enable_fragment_enhanced=True`，且满足以下任一条件：
- 内容长度 < 80 字符
- 内容前 400 字符含引用词：`如上所述 / 如前所述 / 见图 / 参见 / 如图所示 / 上述 / 前述 / 以上内容 / 下面将 / 如下所示 / 详见` 等

> 片段增强优先级高于文本增强：满足片段条件时走片段增强，不再走普通文本增强。

**LLM Prompt**：
```
你是一个教材知识点补充助手。以下内容来自教材《{title}》，
可能是一个片段，缺少完整的上下文背景：

片段内容：{content[:800]}

请完成以下任务：
1) 补充该片段所需的背景知识（1-2句，帮助读者理解上下文）；
2) 识别并解释片段中的专业术语（如有，列出2-3个）；
3) 生成2个有助于检索该知识点的问题。
请直接输出增强内容，不要加标题或编号前缀。
```

### 增强分发逻辑

```
_make_enhanced(child)
  ├── is_image_chunk → _make_image_enhanced()    → [图片描述]
  ├── is_table_chunk → _make_table_enhanced()    → [表格摘要]
  ├── is_fragment    → _make_fragment_enhanced() → [片段增强]
  └── 普通文本       → _make_text_enhanced()     → [LLM增强]
```

### 增强执行模式

当前默认使用严格等价的入库前有序并发增强：

```env
HIERARCHICAL_ENHANCE_MODE=parallel_ordered
HIERARCHICAL_TEXT_ENHANCE_WORKERS=16
HIERARCHICAL_TABLE_ENHANCE_WORKERS=3
HIERARCHICAL_IMAGE_ENHANCE_WORKERS=4
HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70
HIERARCHICAL_REUSE_LLM_CLIENTS=true
```

该模式只改变 enhanced 外呼的执行时机：每个 child/table/image 仍按原触发条件、原 prompt、原模型参数单独生成 enhanced；worker 可乱序完成，但最终按原始 slot 顺序合并回 `chunks`。因此正式入库前仍拿到完整三层切片结果，不引入 `ready_basic` 半增强状态。

当前调度口径与低风险验证配置：

| 增强任务 | 调度池 | 当前 worker 软上限 | 说明 |
|---|---|---:|---|
| 普通文本 `[LLM增强]` | text | 16 | 与片段增强共用同一个文本池 |
| 片段文本 `[片段增强]` | text | 16 | 不额外再开独立池 |
| 表格 `[表格摘要]` | table | 3 | 表格 prompt 通常更长，仍低于文本池 |
| 图片/VL `[图片描述]` | image | 4 | 图片或 VL 请求更容易慢和限流，本轮提升到 4 验证 provider 吞吐 |

`HIERARCHICAL_TEXT_ENHANCE_WORKERS`、`HIERARCHICAL_TABLE_ENHANCE_WORKERS`、`HIERARCHICAL_IMAGE_ENHANCE_WORKERS` 是各类型的软上限；`HIERARCHICAL_ENHANCE_MAX_CONCURRENCY=70` 是全局硬上限。调度器优先按软上限派发任务，但当某类任务已经结束或不存在时，空闲容量可以被剩余任务类型借用，因此不会因为图片池/表格池提前空闲而让大量文本增强排队。无论如何，单进程内同时 enhanced 外呼不会超过全局上限。

`HIERARCHICAL_REUSE_LLM_CLIENTS=true` 时，每个 worker 线程会按 `api_key + base_url` 复用 OpenAI 兼容客户端，减少几千个 enhanced 请求下重复创建 client 和连接池的开销。该开关不改变 prompt、请求参数、slot 合并顺序或最终 chunk 结果；如需排查 provider 兼容问题，可设为 `false`。

如需排查或回退到旧式逐条等待，可设置：

```env
HIERARCHICAL_ENHANCE_MODE=serial
```

控制台入库任务的切片阶段会记录 `chunkBaseMs / enhanceWallMs / enhanceTextMs / enhanceFragmentMs / enhanceTableMs / enhanceImageMs / enhanceTasks / enhanceFailures / enhanceClientReuse / enhanceScheduler / enhanceMaxConcurrency / enhancePeakConcurrency / enhanceTextWorkers / enhanceTableWorkers / enhanceImageWorkers / linkRelationsMs`，用于判断慢点在基础切片、模型增强、动态调度还是关系构建。

---

## 默认在线召回职责

| 层级 | 默认是否进入在线问答主召回 | 最终是否作为 citation 证据 | 主要职责 |
|---|---:|---:|---|
| `parent` | 否 | 否 | 章节导航、标题路径、上下文补全、结果分组 |
| `child` | 是 | 是 | 原文/图片/表格证据，详情回溯与答案引用 |
| `enhanced` | 是 | 否，命中后折叠回 child | 图片描述、表格摘要、文本改写、潜在问题、实体/三元组辅助 |

推荐实现流程：

```
query
  ↓
默认检索范围：child + enhanced
  ↓
命中 child     → 保留 child
命中 enhanced  → 根据 parent_id 找到对应 child，折叠回 child
  ↓
按 child 去重、合并命中来源与分数
  ↓
通过 parent_id 补章节路径，通过 relations 补图/表/邻近证据
  ↓
最终答案引用只指向 child
```

---

## 关联关系（linker.py）

关联关系优先使用类型化 `relations` 表达；`related_ids` 仅作为兼容字段，可由 `relations.target_id` 派生。规则按优先级依次应用。

### 规则 1：引用匹配（最高优先级）思考使用大模型做更细节夸围的关联关系

- 扫描图片块内容，提取 `图N` 编号 → 建立 `图N → 图片块` 映射
- 扫描表格块内容，提取 `表N` 编号 → 建立 `表N → 表格块` 映射
- 扫描所有文本子级，匹配教材常见引用：
  - `图1-3-3-6`
  - `如图1-3-3-6`
  - `表1-3-3-1`
  - `如表1-3-3-1`
- 支持半角/全角数字以及 `- / － / – / —` 等连接符
- 匹配成功则双向建立 `relations`，关系类型为 `refers_to`，`source="rule"`，`evidence` 保存原始引用片段

典型场景：

```text
表1-3-3-1：教材中列出的常用血液检查项目汇总表格
这些都直接关系检验结果的正确性（如图1-3-3-6）
```

上述文本应分别关联到 `表1-3-3-1` 对应表格 child 和 `图1-3-3-6` 对应图片 child。

### 规则 2：邻近关联（fallback）

- 仅对**尚无 related_ids** 的图片/表格块生效
- 向前最多回溯 5 个块，找到最近的文本子级（排除 parent/enhanced 层）
- 双向建立 `adjacent` 关系

### 规则 3：同父关联

- 仅在存在 `parent_id` 时生效
- 同一父级下的所有图片/表格子级 ↔ 所有文本子级，全部两两建立 `sibling` 关系

### 规则 4：增强继承

- enhanced 的 `parent_id` 指向 child
- child 已有的 `refers_to / adjacent / sibling` 等关系可由 enhanced 继承
- 这样 enhanced 被语义召回时，仍能通过 child 找回相关图片、表格或上下文证据

---

## 数据流总结

```
PDF 块序列
  ↓
TITLE → 创建 parent，flush 缓冲
TEXT  → 加入缓冲区
TABLE → flush 缓冲 → 直接生成 child(table) → [表格摘要] enhanced
IMAGE → flush 缓冲 → 直接生成 child(image) → [图片描述] enhanced
  ↓
每个文本 child → 增强分发：
  片段内容(<80字/含引用词) → [片段增强] enhanced
  普通文本(≥50字)          → [LLM增强]  enhanced
  ↓
link_related_chunks()
  规则1: 文本引用"图N/表N" → 匹配对应媒体块（refers_to）
  规则2: 无关联的媒体块   → 找最近前驱文本块（adjacent）
  规则3: 同 parent_id 下  → 媒体↔文本全连接（sibling）
  规则4: enhanced         → 继承对应 child 的 relations
```

---

## 引用展示规范

在线问答和召回测试中，引用来源必须优先使用 `documents.filename` 中的规范文档名，而不是上传落盘产生的临时 UUID 文件名。

推荐展示格式：

```text
正确：带表格的教材片段.pdf P.1 · #3
错误：0bbf82c7-1057-4007-98e8-78d74c5a6522.pdf P.1
```

字段口径：
- `documentName`：规范文档名，优先取 `documents.filename`
- `source`：保留原始切片来源，可作为兼容字段，不建议作为 UI 主展示
- `page`：页码
- `chunkIndex`：切片序号
- `location`：面向用户的位置文案，如 `P.1 · #3`

---

## Chunk 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | UUID，自动生成 |
| `layer` | str | `"parent"` / `"child"` / `"enhanced"` |
| `parent_id` | str? | 子级指向父级 id；增强块指向子级 id |
| `relations` | list[Relation] | 类型化关联边，含 `target_id / rel_type / weight / source / evidence` |
| `related_ids` | list[str] | 兼容字段，由 `relations.target_id` 派生 |
| `is_table_chunk` | bool | 是否为表格块 |
| `is_image_chunk` | bool | 是否为图片块 |
| `image_path` | str? | 图片本地路径 |
| `enhanced_text` | str? | LLM 原始输出（增强块专用） |
| `token_cost` | int | LLM token 消耗（增强块专用） |
| `title` | str? | 所属章节标题（继承自当前 parent） |
