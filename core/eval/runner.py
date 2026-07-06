"""
RAG 评估运行器模块

本模块是 RAG 评估系统的"总指挥",负责协调整个评估流程。

【RAG 评估的整体流程】
就像组织一场考试:
1. 准备试卷(加载评估数据集)
2. 安排考生(选择要评估的检索策略)
3. 批改试卷(运行检索并计算指标)
4. 公布成绩(汇总评估结果)

【评估策略】
本模块支持评估多种检索策略:
- baseline_vector: 基础向量检索(只用向量相似度)
- graph_rag: 图增强检索(结合知识图谱和向量检索)

【使用场景】
当你想要对比不同检索策略的效果时,使用这个模块:
- 哪种策略检索更准确?
- 哪种策略更适合特定类型的查询?
- 优化后的策略是否真的提升了效果?
"""

from __future__ import annotations

from statistics import mean

from backend.adapters.rag_adapter import run_graph_rag_pipeline, run_rag_pipeline
from core.eval.dataset import load_dataset
from core.eval.metrics import mrr, ndcg_at_k, recall_at_k


def _run_strategy(strategy: str, query: str, kb_id: str) -> list[str]:
    """
    运行单个检索策略并返回检索结果

    这是评估的"考试答题"环节。根据指定的策略,让 RAG 系统处理查询,
    返回检索到的文档ID列表。

    【支持的策略】
    1. baseline_vector: 基础向量检索
       - 只使用向量相似度进行检索
       - 流程: 向量检索 → 重排序 → 返回Top-K结果
       - 适合作为对比基准(最基础的检索方式)

    2. graph_rag: 图增强检索
       - 结合知识图谱和向量检索
       - 流程: 意图识别 → 图谱推理 → 向量检索 → 结果融合
       - 适合复杂查询(需要多跳推理、跨章节检索)

    Args:
        strategy: 策略名称,支持 "baseline_vector" 或 "graph_rag"
        query: 用户查询问题,如"什么是机器学习?"
        kb_id: 知识库ID,指定从哪个知识库中检索

    Returns:
        list[str]: 检索到的文档ID列表,按相关性排序(最相关的在前)
                  例如: ["chunk_5", "chunk_12", "chunk_3", ...]

    内部流程说明:
        baseline_vector:
            1. 向量检索获取候选集(比如Top-20)
            2. 重排序筛选优质结果
            3. 返回Top-5结果

        graph_rag:
            1. 知识图谱推理找到相关节点
            2. 向量检索补充相关内容
            3. 多路结果融合
            4. 返回Top-5结果

    注意事项:
        - 每次调用都会实际运行检索,可能耗时较长
        - 返回的ID列表长度可能少于5(如果高质量结果不足)
    """
    # 基础向量检索策略
    if strategy == "baseline_vector":
        # run_rag_pipeline 返回四个值:
        # candidates: 候选集(未重排序)
        # reranked: 重排序后的结果
        # answer: 生成的答案(这里不用)
        # scores: 相关性分数(这里不用)
        candidates, reranked, _answer, _scores = run_rag_pipeline(
            query=query,
            kb_id=kb_id,
            top_k=5,  # 返回前5个结果
            min_score=0.3,  # 最低相关性分数阈值
            use_llm_check=False,  # 不使用LLM验证
            use_llm_score=False,  # 不使用LLM评分
        )
        # 清理不需要的变量,释放内存
        del candidates
        # 提取文档ID列表
        return [item["id"] for item in reranked]

    # 图增强检索策略
    result = run_graph_rag_pipeline(
        query=query,
        kb_id=kb_id,
        top_k=5,  # 返回前5个结果
        min_score=0.3,  # 最低相关性分数阈值
        explain=False,  # 不返回解释信息
        intent=None,  # 不指定意图,让系统自动识别
    )
    # 提取文档ID列表
    return [item["id"] for item in result["results"]]


def run_eval(dataset_path: str, strategies: list[str]) -> dict:
    """
    运行完整的 RAG 评估流程

    这是评估系统的"主控函数",协调整个评估过程。就像组织一场考试:
    发卷子 → 学生答题 → 批改试卷 → 统计成绩。

    【评估流程】
    1. 加载评估数据集(准备试卷)
    2. 对每个策略运行所有查询(考生答题)
    3. 计算检索指标(批改试卷)
    4. 汇总平均指标(统计成绩)

    【评估指标】
    对每个查询,计算三个核心指标:
    - Recall@5: 前5个结果中找到了多少正确答案?
    - MRR: 第一个正确答案排在第几位?
    - NDCG@5: 综合考虑排序质量的整体评分

    Args:
        dataset_path: 评估数据集文件路径(JSONL格式)
                     例如: "data/eval_dataset.jsonl"
        strategies: 要评估的策略名称列表
                   例如: ["baseline_vector", "graph_rag"]

    Returns:
        dict: 评估结果汇总,包含:
            - records: 评估的查询总数
            - strategies: 评估的策略列表
            - summary: 每个策略的平均指标
              [
                {
                  "strategy": "baseline_vector",
                  "recallAt5": 0.65,  # 平均召回率
                  "mrr": 0.78,        # 平均倒数排名
                  "ndcgAt5": 0.72     # 平均NDCG
                },
                ...
              ]

    使用示例:
        >>> result = run_eval(
        ...     dataset_path="data/eval_dataset.jsonl",
        ...     strategies=["baseline_vector", "graph_rag"]
        ... )
        >>> print(f"评估了 {result['records']} 个查询")
        >>> for summary in result["summary"]:
        ...     print(f"策略 {summary['strategy']}:")
        ...     print(f"  Recall@5: {summary['recallAt5']:.2%}")
        ...     print(f"  MRR: {summary['mrr']:.2%}")
        ...     print(f"  NDCG@5: {summary['ndcgAt5']:.2%}")

    输出示例:
        评估了 100 个查询
        策略 baseline_vector:
          Recall@5: 65.00%
          MRR: 78.00%
          NDCG@5: 72.00%
        策略 graph_rag:
          Recall@5: 78.00%
          MRR: 85.00%
          NDCG@5: 81.00%

    注意事项:
        - 如果数据集为空,返回空结果
        - 每个策略会对所有查询运行检索,可能耗时较长
        - 使用平均值汇总指标,适合整体评估
        - 如需详细分析,可以保存每个查询的具体指标
    """
    # 第一步: 加载评估数据集(准备试卷)
    records = load_dataset(dataset_path)

    # 如果数据集为空,返回空结果
    if not records:
        return {"records": 0, "strategies": [], "summary": []}

    summary = []

    # 第二步: 对每个策略进行评估
    for strategy in strategies:
        recalls = []  # 存储每个查询的 Recall@5
        mrrs = []  # 存储每个查询的 MRR
        ndcgs = []  # 存储每个查询的 NDCG@5

        # 第三步: 对每个查询运行检索并计算指标
        for record in records:
            # 运行检索策略,获取检索结果
            predicted = _run_strategy(strategy, record.query, record.kb_id)

            # 计算三个核心指标
            recalls.append(recall_at_k(predicted, record.ground_truth_chunks, 5))
            mrrs.append(mrr(predicted, record.ground_truth_chunks))
            ndcgs.append(ndcg_at_k(predicted, record.ground_truth_chunks, 5))

        # 第四步: 计算平均指标(统计成绩)
        summary.append(
            {
                "strategy": strategy,  # 策略名称
                "recallAt5": mean(recalls) if recalls else 0.0,  # 平均召回率
                "mrr": mean(mrrs) if mrrs else 0.0,  # 平均倒数排名
                "ndcgAt5": mean(ndcgs) if ndcgs else 0.0,  # 平均NDCG
            }
        )

    # 返回完整的评估结果
    return {"records": len(records), "strategies": strategies, "summary": summary}
