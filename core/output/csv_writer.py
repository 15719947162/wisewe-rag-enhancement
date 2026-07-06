"""
CSV 知识库导出模块
==================

本模块负责将切片（Chunk）和向量嵌入（Embedding）数据导出为 CSV 格式，
用于 RAG 知识库的离线存储和迁移。

## 功能说明

在 RAG 管道中，PDF 经过解析、清洗、切片、向量化后，需要将结果持久化。
CSV 格式具有以下优势：
- 人类可读，便于调试和验证
- 兼容性强，可导入各种数据库和工具
- 文件体积适中，适合中小规模知识库

## 数据格式

输出的 CSV 文件包含以下字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | str | 切片唯一标识符（UUID） |
| content | str | 切片文本内容 |
| source | str | 来源文件名 |
| page | int | 页码 |
| chunk_index | int | 切片序号 |
| strategy | str | 切片策略名称 |
| title | str | 标题（如有） |
| char_count | int | 字符数 |
| is_table_chunk | bool | 是否为表格切片 |
| embedding | str | 向量嵌入（逗号分隔的浮点数） |

## 输出示例

```csv
id,content,source,page,chunk_index,strategy,title,char_count,is_table_chunk,embedding
550e8400-e29b-41d4-a716-446655440000,"本文介绍了...",sample.pdf,1,0,fixed_length,概述,256,False,"0.123456,0.234567,..."
550e8400-e29b-41d4-a716-446655440001,"| 列1 | 列2 |...",sample.pdf,2,1,fixed_length,数据表,128,True,"0.345678,0.456789,..."
```

## 使用场景

1. **知识库备份**：将处理结果保存为 CSV 便于归档
2. **数据迁移**：从一个系统迁移到另一个系统
3. **质量检查**：人工检查切片和向量质量
4. **批量导入**：导入到向量数据库（如 pgvector、Milvus）
"""

from __future__ import annotations

import csv
from pathlib import Path

from core.models.content_block import Chunk


def write_knowledge_csv(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    output_path: str,
    encoding: str = "utf-8-sig",
) -> str:
    """
    将切片和向量嵌入写入 CSV 知识库文件。

    该函数将处理后的文本切片及其对应的向量嵌入导出为 CSV 格式，
    便于存储、迁移和后续导入到向量数据库。

    Args:
        chunks: 切片对象列表，每个 Chunk 包含文本内容、来源、页码等信息。
            由切片策略（如 fixed_length、paragraph、semantic 等）生成。
        embeddings: 向量嵌入列表，与 chunks 一一对应。
            每个元素是一个浮点数列表，表示文本的向量表示。
            通常维度为 768（如 text-embedding-ada-002）或 1024/1536。
        output_path: 输出 CSV 文件的路径。父目录不存在时会自动创建。
        encoding: 文件编码，默认为 "utf-8-sig"。
            utf-8-sig 会在文件开头添加 BOM，确保 Excel 正确识别 UTF-8 编码。

    Returns:
        str: 实际写入的文件路径（绝对路径）。

    Raises:
        ValueError: 如果 chunks 和 embeddings 长度不匹配。
        IOError: 文件写入失败时抛出。

    Example:
        >>> from core.chunker import get_strategy
        >>> from core.embedding import embed_texts
        >>>
        >>> # 获取切片
        >>> strategy = get_strategy("fixed_length", chunk_size=500)
        >>> chunks = strategy.chunk(blocks)
        >>>
        >>> # 生成向量
        >>> embeddings = embed_texts([c.content for c in chunks])
        >>>
        >>> # 导出 CSV
        >>> path = write_knowledge_csv(chunks, embeddings, "output/knowledge.csv")
        >>> print(f"已导出到: {path}")

    Note:
        - 向量嵌入以逗号分隔的字符串形式存储，每个浮点数保留 6 位小数
        - 使用 utf-8-sig 编码确保 Excel 打开时不会乱码
        - 如果 chunks 和 embeddings 数量不一致，只处理较小的数量
    """
    # 创建 Path 对象，便于处理路径操作
    path = Path(output_path)

    # 确保父目录存在，不存在则递归创建
    path.parent.mkdir(parents=True, exist_ok=True)

    # 定义 CSV 文件的列名（表头）
    # 这些字段与 Chunk 模型的属性对应
    fieldnames = [
        "id",              # 切片唯一标识符
        "content",         # 切片文本内容
        "source",          # 来源文件名
        "page",            # 页码
        "chunk_index",     # 切片序号（同一切片策略内的顺序）
        "strategy",        # 切片策略名称（如 fixed_length、paragraph）
        "title",           # 标题（从文档结构提取，可能为空）
        "char_count",      # 字符数（用于质量控制）
        "is_table_chunk",  # 是否为表格切片（布尔值）
        "embedding",       # 向量嵌入（逗号分隔的浮点数字符串）
    ]

    # 打开文件并写入 CSV
    # newline="" 防止 Windows 系统出现多余空行
    with open(path, "w", newline="", encoding=encoding) as f:
        # 创建 DictWriter，按字段名写入
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        # 写入表头
        writer.writeheader()

        # 遍历切片和向量，逐行写入
        # zip 会自动截断到较短的列表长度
        for chunk, embedding in zip(chunks, embeddings):
            # 将 Chunk 对象转换为字典
            row = chunk.model_dump()

            # 将向量列表转换为逗号分隔的字符串
            # 保留 6 位小数，平衡精度和文件大小
            # 例如: [0.123456789, 0.234567890] -> "0.123457,0.234568"
            row["embedding"] = ",".join(f"{v:.6f}" for v in embedding)

            # 写入一行数据
            writer.writerow(row)

    # 返回实际写入的文件路径
    return str(path)
