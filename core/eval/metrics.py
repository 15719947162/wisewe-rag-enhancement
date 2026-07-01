from __future__ import annotations

import math


def recall_at_k(predicted: list[str], gt: list[str], k: int) -> float:
    return len(set(predicted[:k]) & set(gt)) / max(len(gt), 1)


def mrr(predicted: list[str], gt: list[str]) -> float:
    for idx, item in enumerate(predicted, start=1):
        if item in gt:
            return 1 / idx
    return 0.0


def ndcg_at_k(predicted: list[str], gt: list[str], k: int) -> float:
    dcg = 0.0
    for idx, item in enumerate(predicted[:k], start=1):
        if item in gt:
            dcg += 1 / math.log2(idx + 1)
    ideal_hits = min(len(gt), k)
    idcg = sum(1 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0
