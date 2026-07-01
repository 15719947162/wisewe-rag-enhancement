from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag.scorer import RAGScorer, _rule_score


def test_rule_score_relevance_avg():
    result = _rule_score([{"dense_score": 0.8}, {"dense_score": 0.6}], {"citations": []})
    assert result["relevance_score"] == 0.7


def test_rule_score_prefers_rerank_score():
    # rerank_score should take priority over dense_score
    result = _rule_score(
        [{"dense_score": 0.0, "rerank_score": 0.9}, {"dense_score": 0.0, "rerank_score": 0.7}],
        {"citations": []},
    )
    assert abs(result["relevance_score"] - 0.8) < 1e-9


def test_rule_score_faithfulness_ratio():
    result = _rule_score([{"dense_score": 0.8}, {"dense_score": 0.6}], {"citations": [{"index": 1}]})
    assert result["faithfulness_score"] == 1.0


def test_rule_score_faithfulness_rejects_invalid_citation():
    result = _rule_score(
        [{"id": "a", "dense_score": 0.8}, {"id": "b", "dense_score": 0.6}],
        {"citations": [{"index": 9, "chunk_id": "missing"}]},
    )
    assert result["faithfulness_score"] == 0.0


def test_rule_score_faithfulness_accepts_chunk_id():
    result = _rule_score(
        [{"id": "a", "dense_score": 0.8}, {"id": "b", "dense_score": 0.6}],
        {"citations": [{"chunk_id": "b"}]},
    )
    assert result["faithfulness_score"] == 1.0


def test_rule_score_empty_contexts():
    result = _rule_score([], {"citations": []})
    assert result["relevance_score"] == 0.0
    assert result["faithfulness_score"] == 0.0


def test_scorer_no_llm_by_default():
    scorer = RAGScorer()
    with patch("core.rag.scorer._llm_score") as mock_llm:
        result = scorer.score("q", [{"dense_score": 0.5}], {"answer": "", "citations": []}, use_llm_score=False)
    assert result["llm_score"] is None
    mock_llm.assert_not_called()


def test_scorer_llm_failure_returns_none():
    scorer = RAGScorer()
    with patch("core.rag.scorer._llm_score", side_effect=RuntimeError("boom")):
        result = scorer.score("q", [{"dense_score": 0.5}], {"answer": "", "citations": []}, use_llm_score=True)
    assert result["llm_score"] is None
