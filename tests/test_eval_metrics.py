from unittest.mock import patch

from core.eval.metrics import mrr, ndcg_at_k, recall_at_k
from core.eval.runner import run_eval


def test_eval_metrics_basic():
    predicted = ["a", "b", "c"]
    gt = ["b", "d"]
    assert recall_at_k(predicted, gt, 2) == 0.5
    assert mrr(predicted, gt) == 0.5
    assert ndcg_at_k(predicted, gt, 3) > 0


def test_run_eval_returns_summary(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"q1","kb_id":"default","query":"什么是应急预案","intent":"concept","ground_truth_chunks":["a"],"ground_truth_answer":"x","cross_section":false,"tags":[],"notes":""}\n',
        encoding="utf-8",
    )

    with patch("core.eval.runner.run_rag_pipeline", return_value=([], [{"id": "a"}], {}, {})), patch(
        "core.eval.runner.run_graph_rag_pipeline",
        return_value={"results": [{"id": "a"}]},
    ):
        result = run_eval(str(dataset), ["baseline_vector", "graph_full"])

    assert result["records"] == 1
    assert len(result["summary"]) == 2
