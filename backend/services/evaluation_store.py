"""
RAG 评估记录存储模块

本模块负责存储和管理 RAG 系统的评估记录，主要包括：
1. 用户对问答结果的评价（相关性、可信度）
2. LLM 评分记录
3. 无法回答的情况记录
4. 失败原因记录

评估数据存储在 data/results/console_evaluations.json 文件中，
最多保留 200 条记录（按时间倒序）。

评估记录用途：
- 分析 RAG 系统的问答质量
- 发现需要改进的知识库或文档
- 收集用户反馈用于后续优化
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# 评估记录存储路径
EVALUATION_STORE_PATH = os.path.join("data", "results", "console_evaluations.json")
# 最大记录数量，超过时删除最旧的记录
MAX_EVALUATION_RECORDS = 200


def _utc_now() -> str:
    """获取当前 UTC 时间（ISO 格式）"""
    return datetime.now(timezone.utc).isoformat()


def _ensure_parent_dir() -> None:
    """确保存储目录存在"""
    os.makedirs(os.path.dirname(EVALUATION_STORE_PATH), exist_ok=True)


def load_evaluations() -> list[dict]:
    """
    加载所有评估记录

    从 JSON 文件中读取评估记录列表。

    返回：
        list[dict]: 评估记录列表

    说明：
        - 如果文件不存在，返回空列表
        - 如果文件损坏，返回空列表
        - 只返回有效的字典类型记录
    """
    if not os.path.exists(EVALUATION_STORE_PATH):
        return []

    try:
        with open(EVALUATION_STORE_PATH, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    return [item for item in payload if isinstance(item, dict)]


def save_evaluations(records: list[dict]) -> None:
    """
    保存评估记录列表

    将评估记录列表写入 JSON 文件。

    参数：
        records: 评估记录列表

    说明：
        - 会自动创建目录
        - 使用 UTF-8 编码
        - 美化 JSON 格式（indent=2）
    """
    _ensure_parent_dir()
    with open(EVALUATION_STORE_PATH, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)


def append_evaluation(record: dict) -> dict:
    """
    添加一条评估记录

    将新的评估记录追加到列表中，并自动清理超过限制的旧记录。

    参数：
        record: 评估记录字典，包含：
            - id: 记录 ID（可选，自动生成）
            - kbId: 知识库 ID
            - query: 用户问题
            - answer: 系统回答
            - relevanceScore: 相关性评分（0-1）
            - faithfulnessScore: 可信度评分（0-1）
            - llmScore: LLM 评分（可选）
            - cannotAnswer: 是否无法回答
            - failureReason: 失败原因（可选）
            - createdAt: 创建时间（可选，自动生成）

    返回：
        dict: 规范化后的评估记录

    说明：
        - 自动规范化字段格式
        - 按创建时间倒序排列
        - 超过 MAX_EVALUATION_RECORDS 时删除最旧的记录
    """
    records = load_evaluations()
    normalized = {
        "id": record.get("id") or f"eval-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "kbId": record.get("kbId", "default"),
        "query": record.get("query", ""),
        "answer": record.get("answer", ""),
        "relevanceScore": float(record.get("relevanceScore", 0.0) or 0.0),
        "faithfulnessScore": float(record.get("faithfulnessScore", 0.0) or 0.0),
        "llmScore": record.get("llmScore"),
        "cannotAnswer": bool(record.get("cannotAnswer", False)),
        "failureReason": record.get("failureReason"),
        "createdAt": record.get("createdAt") or _utc_now(),
    }
    records.append(normalized)
    records = sorted(records, key=lambda item: item.get("createdAt", ""), reverse=True)[:MAX_EVALUATION_RECORDS]
    save_evaluations(records)
    return normalized
