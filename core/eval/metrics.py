"""
RAG 评估指标计算模块

本模块提供了用于评估 RAG 检索效果的常用指标计算函数。

【RAG 评估指标的作用】
在 RAG 系统中,检索质量直接影响最终答案的质量。我们需要一些"尺子"来
测量检索效果好不好。这个模块就提供了这些"尺子"。

【常用的三大指标】
1. Recall@K(召回率): 在前K个结果中,找到了多少个正确答案?
2. MRR(平均倒数排名): 正确答案排在第几位?越靠前越好。
3. NDCG@K(归一化折损累积增益): 综合考虑排序位置和相关性,最全面的指标。

【为什么需要多个指标?】
就像考试不能只看总分一样,不同指标反映不同方面:
- Recall@K 关注"找全了没"(查全率)
- MRR 关注"找得快不快"(排序质量)
- NDCG@K 关注"整体效果如何"(综合评价)

【使用场景】
当 RAG 系统返回检索结果后,用这些指标来衡量检索质量,帮助优化系统。
"""

from __future__ import annotations

import math


def recall_at_k(predicted: list[str], gt: list[str], k: int) -> float:
    """
    计算 Recall@K 指标 - "在前K个结果中找到了多少正确答案?"

    这是最基础的检索指标,就像考试中的"得分率"。假设正确答案有10个,
    系统返回前5个结果中有3个是正确答案,那 Recall@5 = 3/10 = 0.3。

    【通俗理解】
    - 就像去超市买菜,列了10样要买的,结果只找到3样
    - Recall@K = 3/10 = 0.3(找到了30%需要的商品)

    【适用场景】
    - 评估检索系统的"查全能力"(能不能把相关内容都找出来)
    - 适合需要全面覆盖的场景(如法律检索、医疗诊断)

    Args:
        predicted: 系统返回的文档ID列表,按相关性排序(最相关的在前)
                  例如: ["doc_1", "doc_5", "doc_3", ...]
        gt: ground truth,正确答案应该包含的文档ID列表
           例如: ["doc_5", "doc_10", "doc_15", ...]
        k: 只看前K个结果(比如 k=5 就只看前5个返回结果)

    Returns:
        float: 召回率,范围[0, 1],越高越好
              1.0 表示在前K个结果中找到了所有正确答案
              0.0 表示前K个结果中没有一个正确答案

    计算示例:
        >>> predicted = ["doc_1", "doc_5", "doc_3", "doc_10", "doc_2"]
        >>> gt = ["doc_5", "doc_10", "doc_15"]
        >>> recall_at_k(predicted, gt, k=5)
        0.666  # 前5个结果中找到了doc_5和doc_10,共2个正确答案,总共有3个正确答案,所以是2/3

    注意事项:
        - 如果 gt 为空,返回 0(避免除零错误)
        - predicted 和 gt 都可以有重复,会自动去重
    """
    # 取前K个预测结果,与正确答案取交集,计算找到的比例
    # set() 用于去重,& 是集合交集运算
    return len(set(predicted[:k]) & set(gt)) / max(len(gt), 1)


def mrr(predicted: list[str], gt: list[str]) -> float:
    """
    计算 MRR(Mean Reciprocal Rank)指标 - "正确答案排在第几位?"

    MRR 关注的是"第一个正确答案出现的位置"。就像买东西,你希望想要的商品
    排在货架的前面,而不是翻了好几层才找到。

    【通俗理解】
    - 正确答案排在第1位 → MRR = 1/1 = 1.0(满分)
    - 正确答案排在第2位 → MRR = 1/2 = 0.5
    - 正确答案排在第3位 → MRR = 1/3 = 0.33
    - 找不到正确答案 → MRR = 0.0

    【适用场景】
    - 评估检索系统的"排序能力"(能不能把最相关的排在前面)
    - 适合用户只看前几个结果的场景(如搜索引擎、问答系统)

    Args:
        predicted: 系统返回的文档ID列表,按相关性排序
                  例如: ["doc_1", "doc_5", "doc_3", ...]
        gt: ground truth,正确答案应该包含的文档ID列表
           例如: ["doc_5", "doc_10", "doc_15"]

    Returns:
        float: 倒数排名,范围[0, 1],越高越好
              1.0 表示第一个结果就是正确答案
              0.0 表示所有结果都不是正确答案

    计算示例:
        >>> predicted = ["doc_1", "doc_5", "doc_3", "doc_10", "doc_2"]
        >>> gt = ["doc_5", "doc_10"]
        >>> mrr(predicted, gt)
        0.5  # doc_5排在第2位,所以是1/2=0.5

        >>> predicted = ["doc_5", "doc_1", "doc_3"]
        >>> gt = ["doc_5", "doc_10"]
        >>> mrr(predicted, gt)
        1.0  # doc_5排在第1位,所以是1/1=1.0

    注意事项:
        - 只看第一个命中的正确答案位置
        - 如果有多个正确答案,取最早出现的那个
    """
    # enumerate(predicted, start=1) 从1开始计数(第1个、第2个...)
    for idx, item in enumerate(predicted, start=1):
        # 找到第一个正确答案,返回其倒数排名
        if item in gt:
            return 1 / idx

    # 所有结果都不是正确答案,返回0
    return 0.0


def ndcg_at_k(predicted: list[str], gt: list[str], k: int) -> float:
    """
    计算 NDCG@K(Normalized Discounted Cumulative Gain at K)
    - "综合考虑位置和相关性,给个综合评分"

    NDCG 是最全面的检索指标,它不仅看"找没找到",还看"找到的排在什么位置"。
    排在前面的正确答案贡献更大的分数,排在后面的贡献递减。

    【通俗理解】
    想象你在考试,题目有重要程度之分:
    - 第1题(最重要的题)做对了 → 得 1.0 分
    - 第2题(次重要的题)做对了 → 得 0.63 分
    - 第3题做对了 → 得 0.5 分
    ...
    越往后,得分越低(这就是"折损"的含义)

    【计算步骤】
    1. 计算 DCG(实际得分):对前K个结果,如果命中正确答案,就按位置打折计分
    2. 计算 IDCG(理想得分):假设前K个位置都是正确答案,能得多少分
    3. NDCG = DCG / IDCG:实际得分除以理想得分,归一化到[0, 1]

    【适用场景】
    - 综合评估检索质量(既要找得全,又要排得好)
    - 适合需要排序质量的场景(如推荐系统、信息检索)

    Args:
        predicted: 系统返回的文档ID列表,按相关性排序
                  例如: ["doc_1", "doc_5", "doc_3", ...]
        gt: ground truth,正确答案应该包含的文档ID列表
           例如: ["doc_5", "doc_10", "doc_15"]
        k: 只看前K个结果

    Returns:
        float: NDCG值,范围[0, 1],越高越好
              1.0 表示前K个位置完美排序(所有正确答案都排在前面)
              0.0 表示前K个结果中没有正确答案

    计算示例:
        >>> predicted = ["doc_5", "doc_1", "doc_10", "doc_3", "doc_2"]
        >>> gt = ["doc_5", "doc_10", "doc_15"]
        >>> ndcg_at_k(predicted, gt, k=5)
        0.874  # doc_5在第1位,doc_10在第3位,综合计算得到NDCG

    技术细节:
        - DCG = Σ(1 / log2(rank + 1)) for each relevant item in top-k
        - IDCG = Σ(1 / log2(rank + 1)) for rank in 1..min(|gt|, k)
        - NDCG = DCG / IDCG
        - 使用 log2 折损,位置越靠后折损越严重
    """
    # 计算 DCG(Discounted Cumulative Gain):实际得分
    dcg = 0.0
    for idx, item in enumerate(predicted[:k], start=1):
        # 如果这个位置是正确答案,就计分(位置越靠前,分数越高)
        if item in gt:
            # 折损公式: 1 / log2(位置 + 1)
            # 位置1: 1/log2(2) = 1.0
            # 位置2: 1/log2(3) ≈ 0.63
            # 位置3: 1/log2(4) = 0.5
            dcg += 1 / math.log2(idx + 1)

    # 计算 IDCG(Ideal DCG):理想情况下的最高得分
    # 假设前 min(正确答案数量, k) 个位置都是正确答案
    ideal_hits = min(len(gt), k)
    idcg = sum(1 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1))

    # 计算归一化的 NDCG
    return dcg / idcg if idcg else 0.0
