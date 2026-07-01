from __future__ import annotations

from statistics import mean

from backend.adapters.rag_adapter import run_graph_rag_pipeline, run_rag_pipeline
from core.eval.dataset import load_dataset
from core.eval.metrics import mrr, ndcg_at_k, recall_at_k


def _run_strategy(strategy: str, query: str, kb_id: str) -> list[str]:
    if strategy == "baseline_vector":
        candidates, reranked, _answer, _scores = run_rag_pipeline(
            query=query,
            kb_id=kb_id,
            top_k=5,
            min_score=0.3,
            use_llm_check=False,
            use_llm_score=False,
        )
        del candidates
        return [item["id"] for item in reranked]

    result = run_graph_rag_pipeline(
        query=query,
        kb_id=kb_id,
        top_k=5,
        min_score=0.3,
        explain=False,
        intent=None,
    )
    return [item["id"] for item in result["results"]]


def run_eval(dataset_path: str, strategies: list[str]) -> dict:
    records = load_dataset(dataset_path)
    if not records:
        return {"records": 0, "strategies": [], "summary": []}

    summary = []
    for strategy in strategies:
        recalls = []
        mrrs = []
        ndcgs = []
        for record in records:
            predicted = _run_strategy(strategy, record.query, record.kb_id)
            recalls.append(recall_at_k(predicted, record.ground_truth_chunks, 5))
            mrrs.append(mrr(predicted, record.ground_truth_chunks))
            ndcgs.append(ndcg_at_k(predicted, record.ground_truth_chunks, 5))
        summary.append(
            {
                "strategy": strategy,
                "recallAt5": mean(recalls) if recalls else 0.0,
                "mrr": mean(mrrs) if mrrs else 0.0,
                "ndcgAt5": mean(ndcgs) if ndcgs else 0.0,
            }
        )
    return {"records": len(records), "strategies": strategies, "summary": summary}
