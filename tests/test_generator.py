from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag.generator import RAGGenerator, RAG_SYSTEM_PROMPT, _build_rag_prompt, _parse_answer_with_citations


def test_build_rag_prompt_contains_query():
    _, user_prompt = _build_rag_prompt(
        "系统如何保证答案证据可追溯？",
        [{"source": "a.pdf", "page": 1, "content": "系统会返回引用证据和来源位置。"}],
    )
    assert "系统如何保证答案证据可追溯？" in user_prompt


def test_build_rag_prompt_system_no_hallucination():
    assert "不得编造" in RAG_SYSTEM_PROMPT


def test_build_rag_prompt_uses_document_name_and_location():
    _, user_prompt = _build_rag_prompt(
        "系统如何保证答案证据可追溯？",
        [
            {
                "source": "0bbf82c7-1057-4007-98e8-78d74c5a6522.pdf",
                "document_name": "口腔医学教材.pdf",
                "page": 1,
                "chunk_index": 2,
                "content": "系统会返回引用证据和来源位置。",
            }
        ],
    )
    assert "口腔医学教材.pdf" in user_prompt
    assert "P.1 · #3" in user_prompt
    assert "0bbf82c7-1057-4007-98e8-78d74c5a6522.pdf" not in user_prompt


def test_parse_citations_only_referenced():
    contexts = [
        {
            "id": "a",
            "source": "0bbf82c7-1057-4007-98e8-78d74c5a6522.pdf",
            "document_name": "口腔医学教材.pdf",
            "page": 1,
            "chunk_index": 2,
            "content": "证据A",
        },
        {"id": "b", "source": "b.pdf", "page": 2, "content": "证据B"},
    ]
    parsed = _parse_answer_with_citations("系统会返回引用证据[1]", contexts)
    assert len(parsed["citations"]) == 1
    assert parsed["citations"][0]["chunk_id"] == "a"
    assert parsed["citations"][0]["source"] == "口腔医学教材.pdf"
    assert parsed["citations"][0]["location"] == "P.1 · #3"


def test_parse_cannot_answer_flag():
    parsed = _parse_answer_with_citations("根据现有文档无法回答该问题", [])
    assert parsed["cannot_answer"] is True


def test_generate_empty_contexts_no_llm_call():
    generator = RAGGenerator()
    with patch("core.rag.generator._get_rag_llm_client") as mock_client:
        result = generator.generate("q", [])
    assert result["cannot_answer"] is True
    mock_client.assert_not_called()


def test_generate_llm_failure_returns_error_dict():
    generator = RAGGenerator()
    contexts = [{"id": "a", "source": "a.pdf", "page": 1, "content": "证据A"}]
    with patch("core.rag.generator._get_rag_llm_client", side_effect=RuntimeError("boom")):
        result = generator.generate("q", contexts)
    assert result["cannot_answer"] is True
    assert "error" in result


def test_generate_llm_failure_falls_back_with_high_confidence_context():
    generator = RAGGenerator()
    contexts = [
        {
            "id": "needle-therapy",
            "source": "medicine.pdf",
            "document_name": "medicine.pdf",
            "page": 257,
            "chunk_index": 3,
            "score": 0.77,
            "content": "Acupuncture therapy developed from meridian and acupoint theory; it uses needling and moxibustion methods to treat disease.",
        }
    ]

    with patch("core.rag.generator._get_rag_llm_client", side_effect=RuntimeError("boom")):
        result = generator.generate("acupuncture therapy development", contexts)

    assert result["cannot_answer"] is False
    assert result["fallback"] == "extractive_high_confidence"
    assert result["citations"]
    assert result["citations"][0]["chunk_id"] == "needle-therapy"
    assert result["error"] == "boom"


def test_generate_falls_back_to_cited_answer_when_llm_omits_citations():
    generator = RAGGenerator()
    contexts = [
        {
            "id": "a",
            "source": "a.pdf",
            "document_name": "a.pdf",
            "page": 1,
            "chunk_index": 0,
            "score": 0.9,
            "content": "系统会返回引用证据和来源位置。",
        }
    ]

    class _FakeMessage:
        content = "系统会返回答案但这里没有引用编号。"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    with patch("core.rag.generator._get_rag_llm_client") as mock_client, patch(
        "core.rag.generator._get_rag_llm_model", return_value="demo-model"
    ):
        mock_client.return_value.chat.completions.create.return_value = _FakeResponse()
        result = generator.generate("q", contexts)

    assert result["citations"]
    assert result["citations"][0]["chunk_id"] == "a"
    assert result["fallback"] == "extractive_high_confidence"
