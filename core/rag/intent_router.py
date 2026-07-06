"""
意图路由模块

本模块负责识别和分类用户查询的意图类型，用于优化 RAG 检索策略。

意图路由的作用：
- 根据用户查询内容，快速判断查询意图
- 为不同意图选择最合适的检索和处理策略
- 提升问答系统的响应质量和准确性

支持的意图类型：
1. procedure（流程型）：用户询问如何做某事，需要步骤化答案
   关键词：如何、怎么、步骤、流程、方法、操作

2. concept（概念型）：用户询问概念定义，需要解释性答案
   关键词：什么是、定义、含义、概念、介绍

3. data（数据型）：用户询问具体数据，需要精确数值答案
   关键词：多少、数据、比例、数值、年份

4. visual（视觉型）：用户需要图示说明，需要图表或示意图
   关键词：图、示意图、图示、展示

5. general（通用型）：其他查询，使用通用检索策略

分类方法：
- rule：基于规则的分类（当前实现）
- 未来可扩展：llm（基于大模型的智能分类）
"""

from __future__ import annotations


def classify_intent(query: str) -> tuple[str, str]:
    """
    分类用户查询的意图类型。

    该函数通过关键词匹配识别用户意图，返回意图类型和分类方法。
    这种基于规则的方法速度快、可解释性强，适合快速原型开发。

    参数：
        query: 用户查询字符串

    返回：
        tuple[str, str]: (意图类型, 分类方法)
        - 意图类型：procedure | concept | data | visual | general
        - 分类方法：rule（基于规则）

    示例：
        >>> classify_intent("如何配置数据库连接？")
        ('procedure', 'rule')
        >>> classify_intent("什么是向量数据库？")
        ('concept', 'rule')
        >>> classify_intent("系统支持多少并发用户？")
        ('data', 'rule')
        >>> classify_intent("请展示系统架构图")
        ('visual', 'rule')
        >>> classify_intent("帮我写一段代码")
        ('general', 'rule')
    """
    query = query.strip()

    # 流程型意图：用户需要操作步骤或方法指导
    # 这类查询通常需要按步骤分解的答案
    if any(token in query for token in ("如何", "怎么", "步骤", "流程", "方法", "操作")):
        return "procedure", "rule"

    # 概念型意图：用户需要概念解释或定义说明
    # 这类查询需要清晰的解释和背景知识
    if any(token in query for token in ("什么是", "定义", "含义", "概念", "介绍")):
        return "concept", "rule"

    # 数据型意图：用户需要具体数值或统计数据
    # 这类查询需要精确的数据和事实
    if any(token in query for token in ("多少", "数据", "比例", "数值", "年份")):
        return "data", "rule"

    # 视觉型意图：用户需要图表或可视化内容
    # 这类查询可能需要检索图片或生成示意图
    if any(token in query for token in ("图", "示意图", "图示", "展示")):
        return "visual", "rule"

    # 通用型意图：其他查询
    # 使用默认的通用检索策略
    return "general", "rule"
