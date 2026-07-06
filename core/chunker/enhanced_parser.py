"""
增强解析器 - 解析 LLM 增强切片的输出结果

这个模块用于解析层次化切片策略中 LLM 生成的增强内容。

什么是增强切片？
===============
在层次化切片策略中，每个知识点切片（child）都会让 LLM 生成一个"增强版本"，
包含：
- summary: 知识点的摘要
- questions: 可能的问答
- entities: 提取的实体（人名、地名、术语等）
- triples: 提取的知识三元组（主语-谓语-宾语）

这些增强信息能够提升 RAG 系统的检索效果和回答质量。

工作原理：
=========
1. LLM 返回 JSON 格式的增强内容
2. 本模块解析 JSON，提取结构化信息
3. 封装成 EnhancedOutput 对象返回

容错机制：
=========
- 如果 JSON 格式错误，返回原始文本作为摘要
- 如果字段缺失，使用默认值
- 使用正则表达式兼容 Markdown 代码块和纯 JSON

使用示例：
=========
    from core.chunker.enhanced_parser import parse_enhanced_response

    llm_output = '''
    ```json
    {
        "summary": "机器学习是AI的分支",
        "questions": ["什么是机器学习？"],
        "entities": [{"name": "机器学习", "type": "概念"}],
        "triples": [{"s": "机器学习", "p": "属于", "o": "人工智能"}]
    }
    ```
    '''

    result = parse_enhanced_response(llm_output, source_chunk="chunk_001")
    print(result.summary)  # "机器学习是AI的分支"
"""

import json
import re

from pydantic import BaseModel, Field

from core.models.extracted_entity import ExtractedEntity
from core.models.triple import Triple


class EnhancedOutput(BaseModel):
    """LLM 增强切片的输出结构。

    Attributes:
        summary: 知识点摘要（最多300字符）
        questions: 可能的问答列表
        entities: 提取的实体列表
        triples: 提取的知识三元组列表
    """
    summary: str = ""
    questions: list[str] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    triples: list[Triple] = Field(default_factory=list)


# ============ JSON 提取的正则表达式 ============
# 匹配 Markdown 代码块中的 JSON：```json {...} ```
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)

# 匹配纯 JSON 格式
_RAW_JSON_RE = re.compile(r"(\{.*\})", re.S)


def _extract_json(raw: str) -> str | None:
    """从 LLM 输出中提取 JSON 字符串。

    支持两种格式：
    1. Markdown 代码块：```json {...} ```
    2. 纯 JSON：{...}

    Args:
        raw: LLM 的原始输出文本

    Returns:
        JSON 字符串，如果提取失败返回 None
    """
    # 先尝试匹配代码块格式
    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        return fenced.group(1)
    # 再尝试匹配纯 JSON
    direct = _RAW_JSON_RE.search(raw)
    if direct:
        return direct.group(1)
    return None


def _normalize_json_text(raw: str) -> str:
    """修复常见的 JSON 格式问题。

    主要处理：移除末尾多余的逗号
    比如 {"a": 1,} -> {"a": 1}

    Args:
        raw: 原始 JSON 字符串

    Returns:
        修复后的 JSON 字符串
    """
    return re.sub(r",(\s*[}\]])", r"\1", raw.strip())


def parse_enhanced_response(
    raw: str,
    source_chunk: str,
    fallback_text: str | None = None,
) -> EnhancedOutput:
    """解析 LLM 生成的增强内容。

    处理流程：
    1. 从原始文本中提取 JSON
    2. 修复常见的 JSON 格式问题
    3. 解析并验证各个字段
    4. 如果解析失败，使用 fallback_text 作为摘要

    Args:
        raw: LLM 的原始输出
        source_chunk: 源切片 ID（用于追踪三元组来源）
        fallback_text: 解析失败时的备选文本

    Returns:
        EnhancedOutput 对象，包含摘要、问答、实体、三元组

    示例：
        >>> result = parse_enhanced_response(
        ...     '{"summary": "测试", "questions": ["问题1"]}',
        ...     source_chunk="chunk_001"
        ... )
        >>> result.summary
        '测试'
    """
    # 尝试提取 JSON
    payload = _extract_json(raw) or raw

    try:
        # 解析 JSON
        data = json.loads(_normalize_json_text(payload))
    except Exception:
        # 解析失败，返回原始文本作为摘要
        return EnhancedOutput(summary=(fallback_text or raw.strip())[:200])

    # 提取实体列表
    entities = [
        ExtractedEntity(
            name=item.get("name", "").strip(),
            type=item.get("type", "Unknown"),
            aliases=item.get("aliases", []) or [],
        )
        for item in (data.get("entities", []) or [])
        if item.get("name")  # 只保留有名称的实体
    ]

    # 提取三元组列表
    triples: list[Triple] = []
    for item in (data.get("triples", []) or []):
        # 验证三元组的三个要素都存在
        if not item.get("s") or not item.get("p") or not item.get("o"):
            continue
        triples.append(
            Triple(
                s=item["s"],  # 主语
                p=item["p"],  # 谓语
                o=item["o"],  # 宾语
                confidence=float(item.get("confidence", 0.7)),  # 置信度
                source_chunk=source_chunk,  # 来源切片
            )
        )

    # 构建返回对象
    return EnhancedOutput(
        summary=(data.get("summary") or fallback_text or "")[:300],  # 截断摘要
        questions=[str(item).strip() for item in (data.get("questions", []) or []) if str(item).strip()],
        entities=entities,
        triples=triples,
    )
