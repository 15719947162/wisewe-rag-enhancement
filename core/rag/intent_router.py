from __future__ import annotations


def classify_intent(query: str) -> tuple[str, str]:
    query = query.strip()
    if any(token in query for token in ("如何", "怎么", "步骤", "流程", "方法", "操作")):
        return "procedure", "rule"
    if any(token in query for token in ("什么是", "定义", "含义", "概念", "介绍")):
        return "concept", "rule"
    if any(token in query for token in ("多少", "数据", "比例", "数值", "年份")):
        return "data", "rule"
    if any(token in query for token in ("图", "示意图", "图示", "展示")):
        return "visual", "rule"
    return "general", "rule"
