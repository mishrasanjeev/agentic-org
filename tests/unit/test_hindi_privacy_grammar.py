# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Hindi references must not turn into questions about a person or organization."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.langgraph.runner import _build_user_message
from core.pii.redactor import PIIRedactor, _is_hindi_grammar_entity

FOLLOWUP = "अभी बताई गई मेरी काल्पनिक दुकान का नाम और रंग क्या है? हिंदी में उत्तर दें। कोई उपकरण या बाहरी कार्रवाई न करें।"


@pytest.fixture(autouse=True)
def enable_hindi_grammar(monkeypatch):
    monkeypatch.setenv("AGENTICORG_PII_HINDI_GRAMMAR_ENABLED", "true")


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda text: text,
        lambda text: json.dumps({"task": text}, ensure_ascii=False),
        lambda text: "Inputs: " + json.dumps({"task": text}, ensure_ascii=False),
    ],
)
def test_a_short_task_question_retains_structural_json_quotes(wrapper):
    text = wrapper(FOLLOWUP.split("?")[0] + "?")
    assert _is_hindi_grammar_entity(text, entity(text, "मेरी काल्पनिक दुकान का नाम और"))


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda text: 'कंपनी का नाम "' + text + '" है।',
        lambda text: json.dumps({"company_name": text}, ensure_ascii=False),
        lambda text: json.dumps({"task": 'कंपनी का नाम "' + text + '" है।'}, ensure_ascii=False),
        lambda text: 'कंपनी का नाम "' + text + ' विशेष" है।',
        lambda text: json.dumps({"task": 'कंपनी का नाम "' + text + ' विशेष" है।'}, ensure_ascii=False),
        lambda text: json.dumps({"task": "कंपनी का नाम “" + text + " विशेष” है।"}, ensure_ascii=False),
        lambda text: json.dumps({"company_name": text + " विशेष"}, ensure_ascii=False),
        lambda text: 'कंपनी का नाम "' + text + '\nविशेष" है।',
        lambda text: json.dumps({"task": 'कंपनी का नाम "' + text + '\nविशेष" है।'}, ensure_ascii=False),
    ],
)
def test_even_a_name_equal_to_an_entire_query_remains_protected(wrapper):
    text = wrapper(FOLLOWUP.split("?")[0] + "?")
    assert not _is_hindi_grammar_entity(text, entity(text, "मेरी काल्पनिक दुकान का नाम और"))


def entity(text, fragment, entity_type="PERSON", recognizer="SpacyRecognizer"):
    start = text.index(fragment)
    return SimpleNamespace(
        start=start,
        end=start + len(fragment),
        entity_type=entity_type,
        score=0.85,
        recognition_metadata={"recognizer_name": recognizer},
    )


@pytest.mark.parametrize(
    "fragment,kind",
    [
        ("मेरी काल्पनिक दुकान का नाम और", "PERSON"),
        ("रंग", "ORGANIZATION"),
        ("या", "ORGANIZATION"),
    ],
)
def test_exact_reported_followup_grammar(fragment, kind):
    assert _is_hindi_grammar_entity(FOLLOWUP, entity(FOLLOWUP, fragment, kind))


@pytest.mark.parametrize(
    "text,fragment,kind,recognizer",
    [
        ("मेरी दुकान का नाम “रंग” है।", "रंग", "ORGANIZATION", "SpacyRecognizer"),
        ("मेरी दुकान का नाम “या” है।", "या", "ORGANIZATION", "SpacyRecognizer"),
        ("व्यक्ति का नाम “रखा गया है” है।", "रखा गया है", "PERSON", "SpacyRecognizer"),
        ("अभी बताई गई प्रिया शर्मा की दुकान का नाम और रंग क्या है?", "प्रिया शर्मा", "PERSON", "SpacyRecognizer"),
        ("अभी बताई गई मेरी काल्पनिक दुकान का नाम और रंग क्या है? राजेश कुमार", "राजेश कुमार", "PERSON", "SpacyRecognizer"),
        (FOLLOWUP, "रंग", "ORGANIZATION", "TenantCustomRecognizer"),
        (FOLLOWUP, "मेरी काल्पनिक दुकान का नाम और", "AADHAAR", "SpacyRecognizer"),
        ("अभी बताई गई मेरी काल्पनिक दुकान का नाम और “रंग” क्या है?", "रंग", "ORGANIZATION", "SpacyRecognizer"),
        ("अभी बताई गई मेरी प्रिया दुकान का नाम और रंग क्या है?", "मेरी प्रिया दुकान", "PERSON", "SpacyRecognizer"),
        ("कंपनीअभी बताई गई मेरी काल्पनिक दुकान का नाम और रंग क्या है?", "मेरी काल्पनिक दुकान", "PERSON", "SpacyRecognizer"),
    ],
)
def test_named_values_unknown_words_and_other_recognizers_are_never_exempted(text, fragment, kind, recognizer):
    assert not _is_hindi_grammar_entity(text, entity(text, fragment, kind, recognizer))


@pytest.mark.parametrize("flag", [None, "false", "0", "invalid", "true", "1", "yes", " ON "])
def test_shared_redaction_and_pseudonymisation_lookup_preserve_real_entities(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("AGENTICORG_PII_HINDI_GRAMMAR_ENABLED", raising=False)
    else:
        monkeypatch.setenv("AGENTICORG_PII_HINDI_GRAMMAR_ENABLED", flag)
    enabled = flag is not None and flag.strip().lower() in {"true", "1", "yes", "on"}
    monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "before_llm")
    text = FOLLOWUP + " संपर्क: प्रिया शर्मा, buyer@example.test, PAN ABCDE1234F।"
    detections = [
        entity(text, "मेरी काल्पनिक दुकान का नाम और"),
        entity(text, "रंग", "ORGANIZATION"),
        entity(text, "या", "ORGANIZATION"),
        entity(text, "प्रिया शर्मा"),
        entity(text, "ABCDE1234F", "PAN", "PanRecognizer"),
    ]
    redactor = object.__new__(PIIRedactor)
    redactor._analyzer = Mock()

    def analyze(**kwargs):
        requested = kwargs.get("entities")
        return [result for result in detections if requested is None or result.entity_type in requested]

    redactor._analyzer.analyze.side_effect = analyze
    masked, tokens = redactor.redact(text)
    assert (FOLLOWUP in masked) is enabled
    assert "प्रिया शर्मा" not in masked and "buyer@example.test" not in masked and "ABCDE1234F" not in masked
    assert redactor.deanonymize(masked, tokens) == text
    assert redactor.find_entities(text, ["PERSON", "ORGANIZATION"]) == [
        (result.start, result.end, result.entity_type)
        for result in detections
        if result.entity_type in {"PERSON", "ORGANIZATION"} and (not enabled or result is detections[3])
    ]


@pytest.mark.parametrize("enabled", [False, True])
def test_full_packaged_nlp_preserves_entire_question_and_context_grammar(monkeypatch, enabled):
    if not enabled:
        monkeypatch.delenv("AGENTICORG_PII_HINDI_GRAMMAR_ENABLED", raising=False)
    redactor = PIIRedactor()
    if redactor._analyzer is None:
        pytest.skip("Full packaged Presidio is required; immutable artifact gate runs this without skips")
    seed = (
        "यह केवल परीक्षण है। इस बातचीत के लिए मेरी काल्पनिक दुकान का नाम “नीलकमल” "
        "और उसका रंग “बैंगनी” याद रखें। हिंदी में एक वाक्य में पुष्टि करें। "
        "कोई उपकरण या बाहरी कार्रवाई न करें।"
    )
    reply = "आपकी काल्पनिक दुकान का नाम “नीलकमल” और उसका रंग “बैंगनी” इस बातचीत के लिए याद रखा गया है।"
    text = _build_user_message(
        {
            "inputs": {"task": FOLLOWUP, "email": "buyer@example.test"},
            "context": {"conversation": [{"role": "user", "text": seed}, {"role": "assistant", "text": reply}]},
        }
    )
    masked, tokens = redactor.redact(text)
    if enabled:
        assert FOLLOWUP in masked and seed in masked and reply in masked
        assert not redactor.find_entities(FOLLOWUP, ["PERSON", "ORGANIZATION"])
    else:
        assert FOLLOWUP not in masked
        assert redactor.find_entities(FOLLOWUP, ["PERSON", "ORGANIZATION"])
    assert "buyer@example.test" not in masked
    assert redactor.deanonymize(masked, tokens) == text
