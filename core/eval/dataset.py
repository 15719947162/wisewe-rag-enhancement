"""
RAG 评估数据集加载模块

本模块负责加载和管理用于评估 RAG 系统效果的测试数据集。

【RAG 评估的作用】
RAG（检索增强生成）系统需要评估其检索质量和生成质量。就像考试一样,
我们需要准备一套"题目+标准答案"来测试 RAG 系统的表现。这个模块就
是用来加载这些"考试题"的。

【数据集的作用】
- 提供标准化的测试用例,确保评估的一致性
- 包含查询问题、正确答案、相关文档片段等信息
- 支持不同类型的评估场景(单跳查询、多跳查询、跨章节查询等)

【使用场景】
在运行评估时,我们会加载这个数据集,然后用 RAG 系统处理每个查询,
最后对比检索结果和标准答案,计算各种指标。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class EvalRecord(BaseModel):
    """
    单条评估记录 - 相当于一道"考试题"

    这就像试卷上的一道题,包含问题、标准答案、相关知识点等信息。
    评估系统会用这道题来测试 RAG 系统的表现。

    Attributes:
        id: 这道题的唯一编号,方便追踪和统计
        kb_id: 知识库ID,指定这道题要从哪个知识库里找答案
        query: 用户提出的问题(比如"什么是机器学习?")
        intent: 查询意图分类(比如"概念解释"、"对比分析"、"步骤说明"等)
        ground_truth_chunks: 标准答案对应的知识片段ID列表
                             (这是"正确答案"应该包含的内容)
        ground_truth_answer: 完整的标准答案文本(可选,用于生成质量评估)
        cross_section: 是否需要跨章节检索(有些问题需要综合多个章节的内容)
        tags: 标签列表,用于分类统计(比如["难度高", "需要推理"])
        notes: 备注信息,记录这道题的特殊说明
    """

    id: str  # 题目ID
    kb_id: str  # 知识库ID
    query: str  # 用户查询
    intent: str  # 查询意图
    ground_truth_chunks: list[str]  # 正确答案应该包含的文档片段ID
    ground_truth_answer: str | None = None  # 完整的标准答案(可选)
    cross_section: bool = False  # 是否需要跨章节检索
    tags: list[str] = Field(default_factory=list)  # 分类标签
    notes: str = ""  # 备注说明


def load_dataset(path: str) -> list[EvalRecord]:
    """
    从文件加载评估数据集

    这个函数就像"发卷子",把准备好的测试题目加载到内存中。
    数据集存储在 JSONL 文件中(每行一个 JSON 对象)。

    数据集文件格式示例:
    ```
    {"id": "q1", "kb_id": "kb_001", "query": "什么是机器学习?", "intent": "概念解释", "ground_truth_chunks": ["chunk_1", "chunk_5"], "cross_section": false}
    {"id": "q2", "kb_id": "kb_001", "query": "机器学习和深度学习有什么区别?", "intent": "对比分析", "ground_truth_chunks": ["chunk_3", "chunk_8", "chunk_12"], "cross_section": true}
    ```

    Args:
        path: 数据集文件的路径(支持相对路径和绝对路径)

    Returns:
        list[EvalRecord]: 评估记录列表,每条记录就是一道测试题

    使用示例:
        >>> records = load_dataset("data/eval_dataset.jsonl")
        >>> print(f"加载了 {len(records)} 道测试题")
        >>> for record in records:
        >>>     print(f"题目: {record.query}")
        >>>     print(f"正确答案片段: {record.ground_truth_chunks}")

    注意事项:
        - 如果文件不存在,返回空列表(不会报错)
        - 文件中的空行会被自动跳过
        - 文件格式必须是 JSONL(每行一个 JSON 对象)
    """
    target = Path(path)

    # 文件不存在时返回空列表
    if not target.exists():
        return []

    records: list[EvalRecord] = []

    # 逐行读取 JSONL 文件
    with target.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            # 跳过空行
            if not line:
                continue
            # 将 JSON 字符串转换为 EvalRecord 对象
            records.append(EvalRecord.model_validate(json.loads(line)))

    return records
