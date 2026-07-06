"""
评估报告路由模块

这个模块提供了 RAG 系统效果评估的接口,用于对比不同切片策略的检索质量。
评估结果可以帮助选择最适合业务场景的切片策略。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.eval.runner import run_eval

router = APIRouter()


@router.get("/api/eval/reports")
def eval_reports(
    dataset_path: str = Query(default="data/eval/textbook-qa.jsonl"),
    strategies: str = Query(default="baseline_vector,graph_full"),
) -> dict:
    """
    运行评估并生成报告

    这个接口会对指定的评估数据集运行多种策略的评估,生成对比报告。
    评估过程包括:对数据集中的问题进行检索,计算召回率、准确率等指标。

    参数:
        dataset_path: 评估数据集的路径,默认是教科书问答数据集。
                     数据集格式为 JSONL,每行包含一个问题和标准答案。
        strategies: 要评估的策略列表,用逗号分隔。
                   默认评估基线向量检索和图谱检索两种策略。

    返回值:
        dict: 评估报告,包含各种指标对比
            - 各策略的检索命中率
            - 各策略的响应时间
            - 各策略的成本指标

    使用场景:
        - 系统上线前对比不同策略的效果
        - 定期评估系统性能变化
        - 调优策略参数后的验证

    错误情况:
        - 503: 数据集不存在或评估过程出错
    """
    try:
        return run_eval(dataset_path, [item.strip() for item in strategies.split(",") if item.strip()])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
