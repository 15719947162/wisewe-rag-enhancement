# 切片规则文档

**版本：** v1.2  
**更新：** 2026-05-26

---

## 通用规则（所有策略共享）

| 规则 | 说明 |
|------|------|
| 表格块独立 | is_table=True 的块直接作为独立 chunk，不参与合并或截断，is_table_chunk=True |
| 空块跳过 | text.strip() 为空的块不产生 chunk |
| 元数据完整 | 每个 chunk 携带 id（UUID）、source（文件名）、page（页码）、strategy（策略名）、char_count（字符数） |

---

## 策略一：固定长度（fixed_length）

适用场景：内容均匀、无明显结构的文档

| 参数 | 可选范围 | 默认值 | 说明 |
|------|---------|--------|------|
| chunk_size | 64 – 17000 字符 | 1000 | 每个 chunk 的最大字符数 |
| overlap | 0 – 200 字符 | 50 | 相邻 chunk 的重叠字符数，防止语义在边界断裂 |

切分规则：
1. 对每个文本块按 chunk_size 字符截断
2. 下一个 chunk 从「当前结束位置 - overlap」处开始
3. 不感知段落、句子、标题，纯字符计数

---

## 策略二：段落（paragraph）

适用场景：正文密集、段落清晰的文档（论文、教材正文、报告）

| 参数 | 可选范围 | 默认值 | 说明 |
|------|---------|--------|------|
| min_chars | 64 – 512 字符 | 64 | 短于此值的段落自动与下一段合并 |
| max_chars | 64 – 17000 字符 | 512 | 超过此值的段落按句子边界拆分 |
| max_depth | 1 – 3 | 3 | 最大合并层级（段落嵌套深度），防止过度合并 |

切分规则：
1. 切段落：先按空行（

）切，无空行则按单换行（
）切
2. 合并短段落：短于 min_chars 且合并深度未超 max_depth 时与下一段合并
3. 合并相邻段落：合并后总长不超 max_chars 且深度未超 max_depth 时继续合并
4. 拆分超长段落：超过 max_chars 时按句子结束符（。！？.!?）拆分
5. 兜底：无句子边界时按 max_chars 硬切

---

## 策略三：语义化（semantic）

适用场景：有明确章节结构的文档（教材、技术文档、规范）

| 参数 | 可选范围 | 默认值 | 说明 |
|------|---------|--------|------|
| max_chunk_size | 200 – 3000 字符 | 1000 | 单个 chunk 的最大字符数 |

切分规则：
1. 遇到标题块（type=title）开启新 chunk，标题存入 chunk.title
2. 同一标题下的段落依次追加，超过 max_chunk_size 才强制切分
3. 表格块先 flush 当前 chunk，再独立输出

---

## 策略四：分隔符（separator）

适用场景：需要句子级粒度的检索场景

| 参数 | 默认值 | 说明 |
|------|--------|------|
| separators | ["

", "
", "。", "；", ". "] | 按优先级依次尝试的分隔符 |
| keep_separator | True | 是否将分隔符保留在 chunk 末尾 |

切分规则：按分隔符列表依次尝试（双换行 > 单换行 > 句号 > 分号 > 英文句点），找到能切开的就用它。

---

## 策略五：LLM 智能（llm）

适用场景：追求最高切分质量，已配置 LLM API

| 参数 | 可选范围 | 默认值 | 说明 |
|------|---------|--------|------|
| max_chunk_size | 200 – 2000 字符 | 800 | 超过此长度才调用 LLM |
| model | string | qwen-plus | 调用的 LLM 模型名 |

Fallback 链：LLM API 失败 → 按句子合并切分 → 按 max_chunk_size 硬切

配置要求：
  LLM_API_KEY=sk-xxx
  LLM_BASE_URL=https://your-service/v1
  LLM_EMBEDDING_BATCH_SIZE=10

---

## 策略六：三层切片（hierarchical）

适用场景：教材类 PDF，有章节标题结构，需要多媒体内容（图片/表格）的语义增强

| 参数 | 默认值 | 说明 |
|------|--------|------|
| child_max_chars | 600 | 文本子级最大字符数 |
| enable_enhanced | True | 是否启用第三层 LLM 增强 |
| enable_image_enhanced | True | 是否对图片块生成描述增强 |
| enable_table_enhanced | True | 是否对表格块生成摘要增强 |
| enable_fragment_enhanced | True | 是否对片段内容生成补充增强 |
| llm_model | qwen-plus | 文本增强使用的 LLM 模型 |
| vl_model | （空）| 图片识别使用的视觉语言模型，如 qwen-vl-plus |

三层结构：
- **第一层 parent**：章节标题，不参与向量检索，仅提供上下文容器
- **第二层 child**：知识点块，实际用于 embedding 检索
- **第三层 enhanced**：LLM 生成的增强文本，辅助检索，挂在子级下

增强类型（按子级类型分发）：

| 子级类型 | 增强标签 | 触发条件 | LLM 输出内容 |
|---------|---------|---------|------------|
| 普通文本 | `[LLM增强]` | 内容 ≥ 50 字符，非片段 | 一句话摘要 + 3个检索问题 |
| 图片块 | `[图片描述]` | is_image_chunk=True | 图片内容描述 + 教学意义（VL模型优先，fallback文本LLM） |
| 表格块 | `[表格摘要]` | is_table_chunk=True，内容 ≥ 20 字符 | 表格主题摘要 + 术语解释 + 数据规律 |
| 片段文本 | `[片段增强]` | 内容 < 80 字符，或含引用词（如上所述/参见等） | 背景补充 + 术语解释 + 检索问题 |

配置要求：
```
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_CLEANER_MODEL=qwen-plus
VL_MODEL=qwen-vl-plus          # 可选，不配置则图片增强 fallback 到文本 LLM
VL_API_KEY=sk-xxx              # 可选，默认继承 LLM_API_KEY
```

详细规则见：`docs/rule/hierarchical-chunking.md`

---

## 策略对比

| 策略 | 默认块大小 | 粒度 | 保持语义 | 需要 API | 适合文档类型 |
|------|-----------|------|---------|---------|------------|
| fixed_length | 1000 字符 | 均匀 | 否 | 否 | 无结构文档 |
| paragraph | 512 字符 | 自然段落 | 部分 | 否 | 正文密集文档 |
| semantic | 1000 字符 | 章节级 | 是 | 否 | 教材、技术文档 |
| separator | ~50 字符 | 句子级 | 部分 | 否 | 细粒度检索 |
| llm | 800 字符 | 语义级 | 是 | 是 | 最高质量要求 |
| hierarchical | 600 字符 | 三层结构 | 是 | 是 | 教材PDF，含图表 |

---

## 参数速查

  固定长度：  chunk_size=64~17000 (默认1000)  overlap=0~200 (默认50)
  段落：      min_chars=64~512 (默认64)  max_chars=64~17000 (默认512)  max_depth=1~3 (默认3)
  语义化：    max_chunk_size=200~3000 (默认1000)
  分隔符：    separators=列表  keep_separator=bool
  LLM：       max_chunk_size=200~2000 (默认800)
