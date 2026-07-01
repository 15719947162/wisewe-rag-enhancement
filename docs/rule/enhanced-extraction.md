# Enhanced Extraction 协议说明

## 目标

在 enhanced 层单次 LLM 调用中，同时产出：

- `summary`
- `questions`
- `entities`
- `triples`

不额外增加一次调用。

## 输出协议

模型应尽量返回严格 JSON：

```json
{
  "summary": "string",
  "questions": ["string"],
  "entities": [
    {"name": "string", "type": "Concept", "aliases": []}
  ],
  "triples": [
    {"s": "string", "p": "string", "o": "string", "confidence": 0.7}
  ]
}
```

## 解析约定

- 允许 ```json fenced block
- 允许尾随逗号
- `confidence` 缺失时默认 0.7
- 三元组主谓宾任一为空则丢弃
- 解析失败时退化为纯文本 summary，不阻塞流水线

## 向后兼容

- `enhanced_text` 始终保持纯文本摘要
- `extracted_entities` / `extracted_triples` 作为增强字段追加，不破坏旧下游
