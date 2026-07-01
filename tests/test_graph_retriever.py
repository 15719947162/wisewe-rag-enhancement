from unittest.mock import patch

from core.rag.graph_retriever import GraphRetriever


def test_graph_retriever_includes_context_and_entities():
    with patch("core.rag.graph_retriever.classify_intent", return_value=("concept", "rule")), patch(
        "core.rag.graph_retriever.embed_query_cached", return_value=([0.1, 0.2], False)
    ), patch(
        "core.rag.graph_retriever._dense_retrieve",
        return_value=[{"id": "c1", "score": 0.8, "dense_score": 0.8, "content": "dense", "source": "doc.pdf", "page": 1, "layer": "child", "related_ids": []}],
    ), patch(
        "core.rag.graph_retriever._sparse_retrieve",
        return_value=[],
    ), patch(
        "core.rag.graph_retriever._entity_retrieve",
        return_value=[{"id": "c1", "score": 0.9, "dense_score": 0.0, "content": "entity", "source": "doc.pdf", "page": 1, "layer": "child", "related_ids": []}],
    ), patch(
        "core.rag.graph_retriever._expand_related",
        side_effect=lambda items, _kb_id: items,
    ), patch(
        "core.rag.graph_retriever.graph_expand",
        return_value=[],
    ), patch(
        "core.rag.graph_retriever._fetch_chunks_by_ids",
        return_value=[{"id": "c1", "content": "应急预案正文", "source": "doc.pdf", "page": 1, "layer": "child", "parent_id": None, "related_ids": []}],
    ), patch.object(
        GraphRetriever,
        "_load_entities_for_chunks",
        return_value={"c1": [{"name": "应急预案", "type": "Concept", "definition": "定义"}]},
    ):
        result = GraphRetriever().retrieve("什么是应急预案", "kb", top_k=5, explain=True)

    assert result["intent"] == "concept"
    assert result["stats"]["recall_counts"]["entity"] == 1
    assert result["results"][0]["entities"][0]["name"] == "应急预案"
    assert "相关实体" in result["context"]
def test_graph_retriever_uses_cached_query_embedding():
    with patch("core.rag.graph_retriever.classify_intent", return_value=("concept", "rule")), patch(
        "core.rag.graph_retriever.embed_query_cached", return_value=([0.1, 0.2], True)
    ) as mocked_embed, patch(
        "core.rag.graph_retriever._dense_retrieve",
        return_value=[{"id": "c1", "score": 0.8, "dense_score": 0.8, "content": "dense", "source": "doc.pdf", "page": 1, "layer": "child", "related_ids": []}],
    ), patch(
        "core.rag.graph_retriever._sparse_retrieve",
        return_value=[],
    ), patch(
        "core.rag.graph_retriever._entity_retrieve",
        return_value=[],
    ), patch(
        "core.rag.graph_retriever._expand_related",
        side_effect=lambda items, _kb_id: items,
    ), patch(
        "core.rag.graph_retriever.graph_expand",
        return_value=[],
    ), patch(
        "core.rag.graph_retriever._fetch_chunks_by_ids",
        return_value=[{"id": "c1", "content": "content", "source": "doc.pdf", "page": 1, "layer": "child", "parent_id": None, "related_ids": []}],
    ), patch.object(
        GraphRetriever,
        "_load_entities_for_chunks",
        return_value={},
    ):
        result = GraphRetriever().retrieve("query", "kb", top_k=5)

    mocked_embed.assert_called_once_with("query")
    assert result["stats"]["query_embedding_cache_hit"] is True
