from core.rag.intent_router import classify_intent


def test_classify_intent_procedure():
    intent, source = classify_intent("应急预案如何编制？")
    assert intent == "procedure"
    assert source == "rule"
