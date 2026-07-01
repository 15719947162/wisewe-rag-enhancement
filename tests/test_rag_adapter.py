from __future__ import annotations

from unittest.mock import patch

from backend.adapters.rag_adapter import run_rag_pipeline


def test_run_rag_pipeline_short_circuits_media_ref_query() -> None:
    hit = {
        "id": "img-1",
        "content": "图1-3-3 血液检查结果判读示意图",
        "source": "upload.pdf",
        "document_name": "教材.pdf",
        "document_id": "doc-1",
        "page": 1,
        "chunk_index": 15,
        "layer": "child",
        "parent_id": "parent-1",
        "related_ids": [],
        "score": 1.0,
        "dense_score": 0.0,
        "retrieval_mode": "media_ref",
        "matched_media_ref": "图133",
        "is_image_chunk": True,
        "image_path": "data/output/images/fig.png",
    }

    with patch("backend.adapters.rag_adapter.HybridRetriever") as retriever_cls, patch(
        "backend.adapters.rag_adapter.ParentChildReranker"
    ) as reranker_cls, patch("backend.adapters.rag_adapter.RAGGenerator") as generator_cls, patch(
        "backend.adapters.rag_adapter.RAGScorer"
    ) as scorer_cls:
        retriever_cls.return_value.retrieve.return_value = [hit]
        retriever_cls.return_value.last_timings = {
            "media_ref": 1,
            "related": 0,
            "filter": 0,
            "total": 1,
            "short_circuit": True,
        }
        candidates, reranked, answer, scores = run_rag_pipeline(
            query="图1-3-3",
            kb_id="kb",
            top_k=4,
            min_score=0.3,
            use_llm_check=False,
            use_llm_score=False,
        )

    reranker_cls.return_value.rerank.assert_not_called()
    generator_cls.return_value.generate.assert_not_called()
    scorer_cls.return_value.score.assert_not_called()
    assert candidates == [hit]
    assert reranked[0]["id"] == "img-1"
    assert answer["cannot_answer"] is False
    assert "图133" in answer["answer"]
    assert answer["citations"][0]["chunk_id"] == "img-1"
    assert scores["relevance_score"] == 1.0
    assert scores["_latency_ms"]["short_circuit"] is True
    assert scores["_latency_ms"]["rerank"] == 0
    assert scores["_latency_ms"]["generate"] == 0
    assert scores["_latency_ms"]["score"] == 0
    assert scores["_latency_ms"]["total"] >= scores["_latency_ms"]["retrieval"]
    assert scores["_latency_ms"]["retrieval_breakdown"]["short_circuit"] is True
    assert "media_ref" in scores["_latency_ms"]["retrieval_breakdown"]


def test_run_rag_pipeline_aggregates_llm_usage_metrics() -> None:
    with patch("backend.adapters.rag_adapter.HybridRetriever") as retriever_cls, patch(
        "backend.adapters.rag_adapter.ParentChildReranker"
    ) as reranker_cls, patch("backend.adapters.rag_adapter.RAGGenerator") as generator_cls, patch(
        "backend.adapters.rag_adapter.RAGScorer"
    ) as scorer_cls:
        retriever_cls.return_value.retrieve.return_value = [{"id": "c1", "content": "evidence"}]
        retriever_cls.return_value.last_timings = {}
        reranker_cls.return_value.rerank.return_value = [{"id": "c1", "content": "evidence"}]
        reranker_cls.return_value.last_metrics = {
            "rerankLlmCheckRequests": 1,
            "rerankLlmCheckTotalTokens": 7,
        }
        generator_cls.return_value.generate.return_value = {
            "answer": "answer [1]",
            "cannot_answer": False,
            "citations": [{"index": 1}],
            "llm_usage": {
                "generateRequests": 1,
                "generateTotalTokens": 11,
            },
        }
        scorer_cls.return_value.score.return_value = {
            "relevance_score": 0.8,
            "faithfulness_score": 1.0,
            "llm_score": 0.9,
            "llm_usage": {
                "scoreRequests": 1,
                "scoreTotalTokens": 5,
            },
        }

        _candidates, _reranked, _answer, scores = run_rag_pipeline(
            query="q",
            kb_id="kb",
            top_k=4,
            min_score=0.3,
            use_llm_check=True,
            use_llm_score=True,
        )

    usage = scores["_latency_ms"]["llm_usage"]
    assert usage["rerankLlmCheckTotalTokens"] == 7
    assert usage["generateTotalTokens"] == 11
    assert usage["scoreTotalTokens"] == 5
