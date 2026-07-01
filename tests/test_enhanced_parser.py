from core.chunker.enhanced_parser import parse_enhanced_response


def test_parse_enhanced_response_json():
    raw = '{"summary":"s","questions":["q1"],"entities":[{"name":"e1","type":"Concept","aliases":[]}],"triples":[{"s":"a","p":"is","o":"b","confidence":0.8}]}'
    out = parse_enhanced_response(raw, "chunk-1")
    assert out.summary == "s"
    assert len(out.entities) == 1
    assert len(out.triples) == 1


def test_parse_enhanced_response_fallback():
    out = parse_enhanced_response("not json", "chunk-1", fallback_text="fallback")
    assert out.summary == "fallback"


def test_parse_enhanced_response_from_fenced_json():
    raw = '```json\n{"summary":"s2","questions":["q2"],"entities":[],"triples":[]}\n```'
    out = parse_enhanced_response(raw, "chunk-2")
    assert out.summary == "s2"
    assert out.questions == ["q2"]


def test_parse_enhanced_response_trailing_comma_and_default_confidence():
    raw = '{"summary":"s3","questions":[],"entities":[{"name":"e","type":"Concept","aliases":[]}],"triples":[{"s":"a","p":"is","o":"b",}],}'
    out = parse_enhanced_response(raw, "chunk-3")
    assert len(out.triples) == 1
    assert out.triples[0].confidence == 0.7


def test_parse_enhanced_response_skips_incomplete_triples():
    raw = '{"summary":"s4","questions":[],"entities":[],"triples":[{"s":"a","p":"","o":"b","confidence":0.8}]}'
    out = parse_enhanced_response(raw, "chunk-4")
    assert out.summary == "s4"
    assert out.triples == []
