from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.eval.runner import run_eval

router = APIRouter()


@router.get("/api/eval/reports")
def eval_reports(
    dataset_path: str = Query(default="data/eval/textbook-qa.jsonl"),
    strategies: str = Query(default="baseline_vector,graph_full"),
) -> dict:
    try:
        return run_eval(dataset_path, [item.strip() for item in strategies.split(",") if item.strip()])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
