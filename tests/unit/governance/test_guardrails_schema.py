# SPDX-License-Identifier: Apache-2.0
"""Guardrail rules and detectors: validation, matching, findings and transforms."""

from __future__ import annotations

import pytest

from core.governance.guardrails import detectors as det
from core.governance.guardrails import schema

CARD = "4111 1111 1111 1111"


def _rule(**over) -> schema.Rule:
    base = {"id": "r1", "name": "r", "stage": "output", "detector": "sensitive_data"}
    base.update(over)
    return schema.Rule(**base)


class TestRuleValidation:
    def test_a_minimal_rule_normalises(self):
        clean = schema.validate_rule_fields(
            {"name": " cards ", "stage": "Output", "detector": "SENSITIVE_DATA", "action": "Redact"}
        )
        assert clean["name"] == "cards" and clean["stage"] == "output" and clean["detector"] == "sensitive_data"
        assert clean["action"] == "redact" and clean["threshold"] == 0.5 and clean["priority"] == 100
        assert clean["enabled"] is True and clean["options"] == {} and clean["risk_tier"] is None

    def test_entities_patterns_and_narrowing(self):
        clean = schema.validate_rule_fields(
            {
                "name": "n",
                "stage": "output",
                "detector": "sensitive_data",
                "options": {"entities": ["credit_card", " pan "]},
                "use_case": "Agent_Run",
                "risk_tier": "HIGH",
                "agent_id": "A1",
            }
        )
        assert clean["options"] == {"entities": ["CREDIT_CARD", "PAN"]}
        assert clean["use_case"] == "agent_run" and clean["risk_tier"] == "high" and clean["agent_id"] == "A1"
        clean = schema.validate_rule_fields(
            {
                "name": "p",
                "stage": "output",
                "detector": "pattern",
                "action": "block",
                "options": {"patterns": ["\\bAcme\\b"], "kind": "competitor"},
            }
        )
        assert clean["options"] == {"patterns": ["\\bAcme\\b"], "kind": "competitor", "ignore_case": True}

    @pytest.mark.parametrize(
        "fields, message",
        [
            ({"stage": "output", "detector": "toxicity"}, "needs a name"),
            ({"name": "n", "stage": "somewhere", "detector": "toxicity"}, "stage must be"),
            ({"name": "n", "stage": "output", "detector": "magic"}, "detector must be"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "action": "shout"}, "action must be"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "action": "tokenise"}, "sensitive data only"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "threshold": 1.5}, "threshold"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "threshold": True}, "threshold"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "priority": -1}, "priority"),
            ({"name": "n", "stage": "output", "detector": "toxicity", "risk_tier": "extreme"}, "risk_tier"),
            ({"name": "n", "stage": "output", "detector": "pattern"}, "non-empty list"),
            (
                {"name": "n", "stage": "output", "detector": "pattern", "options": {"patterns": ["("]}},
                "does not compile",
            ),
            (
                {"name": "n", "stage": "output", "detector": "pattern", "options": {"patterns": [""]}},
                "non-empty string",
            ),
            (
                {"name": "n", "stage": "output", "detector": "sensitive_data", "options": {"entities": []}},
                "at least one",
            ),
            (
                {"name": "n", "stage": "output", "detector": "sensitive_data", "options": {"colour": "red"}},
                "not taken by",
            ),
            ({"name": "n", "stage": "output", "detector": "sensitive_data", "options": "x"}, "must be an object"),
        ],
    )
    def test_unusable_rules_are_refused(self, fields, message):
        with pytest.raises(ValueError, match=message):
            schema.validate_rule_fields(fields)

    @pytest.mark.parametrize(
        "options, message",
        [
            ({"entities": 1}, "must be a list"),
            ({"entities": "PAN"}, "must be a list"),
            ({"entities": ["PASSPORT"]}, "must be among"),
            ({"entities": ["PAN", 3]}, "entity type names"),
            ({"kind": "x"}, "not taken by"),
        ],
    )
    def test_sensitive_data_options_are_validated(self, options, message):
        with pytest.raises(ValueError, match=message):
            schema.validate_rule_fields(
                {"name": "n", "stage": "output", "detector": "sensitive_data", "options": options}
            )

    @pytest.mark.parametrize(
        "options, message",
        [
            ({"patterns": ["(a+)+$"]}, "nests or repeats"),
            ({"patterns": [r"(\d*)*"]}, "nests or repeats"),
            ({"patterns": ["(x)\\1"]}, "backreference"),
            ({"patterns": ["a" * 600]}, "at most 512"),
            ({"patterns": [1]}, "non-empty string"),
            ({"patterns": ["x"] * 33}, "at most 32"),
            ({"patterns": ["x"], "kind": 3}, "kind must be"),
            ({"patterns": ["x"], "entities": ["PAN"]}, "not taken by"),
        ],
    )
    def test_pattern_options_are_validated(self, options, message):
        with pytest.raises(ValueError, match=message):
            schema.validate_rule_fields({"name": "n", "stage": "output", "detector": "pattern", "options": options})

    def test_toxicity_takes_no_options_and_safe_patterns_pass(self):
        with pytest.raises(ValueError, match="not taken by"):
            schema.validate_rule_fields(
                {"name": "n", "stage": "output", "detector": "toxicity", "options": {"patterns": ["secret"]}}
            )
        assert schema.safe_pattern("\\bAcme(Pay|Card)\\b") == "\\bAcme(Pay|Card)\\b"
        assert schema.safe_pattern("[0-9]{4}-[0-9]{4}") == "[0-9]{4}-[0-9]{4}"

    def test_round_trip_and_matching(self):
        rule = _rule(agent_id="a1", use_case="agent_run", risk_tier="high", options={"entities": ["PAN"]})
        assert schema.Rule.from_dict(rule.to_dict()) == rule
        assert rule.matches("output", agent_id="A1", use_case="Agent_Run", risk_tier="HIGH")
        assert not rule.matches("input", agent_id="a1", use_case="agent_run", risk_tier="high")
        assert not rule.matches("output", agent_id="a2", use_case="agent_run", risk_tier="high")
        assert not rule.matches("output", agent_id="a1", use_case=None, risk_tier="high")
        assert _rule().matches("output", agent_id=None, use_case=None, risk_tier=None)

    def test_the_block_error_payload(self):
        exc = schema.GuardrailBlocked("blocked", stage="output", correlation_id="c1", rule_id="r1", rule_name="cards")
        assert exc.code == "E1016"
        assert exc.to_error() == {
            "error": {"code": "E1016", "message": "blocked"},
            "guardrail": {"stage": "output", "correlation_id": "c1", "rule_id": "r1", "rule_name": "cards"},
        }


class TestSensitiveData:
    def test_card_numbers_need_the_luhn_check(self):
        assert det.card_numbers(f"pay with {CARD} today") == [(9, 28)]
        assert det.card_numbers("4111 1111 1111 1112") == []
        assert det.card_numbers("4111-1111-1111-1111 and 5500000000000004") == [(0, 19), (24, 40)]
        assert det.card_numbers("order 123456789012") == []

    def test_the_regex_recognisers_find_the_national_identifiers_and_cards(self, monkeypatch):
        detector = det.SensitiveDataDetector()
        monkeypatch.setattr(detector, "_analyser_spans", lambda text, entities: None)
        text = f"PAN ABCDE1234F, mail a.b@example.test, card {CARD}"
        findings = detector.detect(text, {}, threshold=0.5)
        assert [(f.kind, text[f.start : f.end]) for f in findings] == [
            ("PAN", "ABCDE1234F"),
            ("EMAIL", "a.b@example.test"),
            ("CREDIT_CARD", CARD),
        ]
        only_cards = detector.detect(text, {"entities": ["CREDIT_CARD"]}, threshold=0.5)
        assert [f.kind for f in only_cards] == ["CREDIT_CARD"]

    def test_the_analyser_is_preferred_when_installed(self, monkeypatch):
        detector = det.SensitiveDataDetector()
        monkeypatch.setattr(detector, "_analyser_spans", lambda text, entities: [(0, 10, "PAN", 0.9)])
        findings = detector.detect("ABCDE1234F", {"entities": ["PAN"]}, threshold=0.5)
        assert findings == [det.Finding("sensitive_data", "PAN", 0, 10, 0.9, "pan at 0-10")]


class TestOtherDetectors:
    def test_toxicity_uses_the_checker_and_its_threshold(self, monkeypatch):
        calls = []

        def fake(text, threshold):
            calls.append(threshold)
            return 0.8, [{"type": "toxicity", "detail": "Toxic keyword detected: x", "severity": "medium"}]

        monkeypatch.setattr("core.content_safety.checker._check_toxicity", fake)
        findings = det.ToxicityDetector().detect("some text", {}, threshold=0.6)
        assert calls == [0.6] and findings[0].score == 0.8 and findings[0].end == 9
        monkeypatch.setattr("core.content_safety.checker._check_toxicity", lambda text, threshold: (0.0, []))
        assert det.ToxicityDetector().detect("clean", {}, threshold=0.6) == []

    def test_patterns_match_case_insensitively_by_default(self):
        findings = det.PatternDetector().detect(
            "AcmePay beats acmepay", {"patterns": ["\\bacmepay\\b"], "kind": "competitor"}, threshold=0.5
        )
        assert [(f.start, f.end, f.kind) for f in findings] == [(0, 7, "competitor"), (14, 21, "competitor")]
        strict = det.PatternDetector().detect(
            "AcmePay beats acmepay", {"patterns": ["\\bacmepay\\b"], "ignore_case": False}, threshold=0.5
        )
        assert [(f.start, f.end) for f in strict] == [(14, 21)]


class TestTransforms:
    def _findings(self, text):
        return det.SensitiveDataDetector().detect(text, {"entities": ["CREDIT_CARD", "EMAIL"]}, threshold=0.5)

    def test_mask_redact_and_tokenise(self, monkeypatch):
        monkeypatch.setattr(det.SensitiveDataDetector, "_analyser_spans", lambda self, text, entities: None)
        text = f"card {CARD} mail a@b.co"
        findings = self._findings(text)
        masked, _ = det.apply_transform(text, findings, "mask")
        assert masked == "card ******************* mail ******"
        redacted, _ = det.apply_transform(text, findings, "redact")
        assert redacted == "card <CREDIT_CARD> mail <EMAIL>"
        counters: dict[str, int] = {}
        tokenised, token_map = det.apply_transform(text, findings, "tokenise", counters)
        assert tokenised == "card <CREDIT_CARD_1> mail <EMAIL_1>"
        assert token_map == {"<CREDIT_CARD_1>": CARD, "<EMAIL_1>": "a@b.co"}
        again, more = det.apply_transform("x a@b.co", self._findings("x a@b.co"), "tokenise", counters)
        assert again == "x <EMAIL_2>" and more == {"<EMAIL_2>": "a@b.co"}
        with pytest.raises(ValueError, match="not a transform"):
            det.apply_transform(text, findings, "block")

    def test_overlapping_spans_keep_the_widest(self):
        findings = [
            det.Finding("pattern", "a", 0, 4),
            det.Finding("pattern", "b", 2, 10),
            det.Finding("pattern", "c", 12, 14),
        ]
        kept = det._without_overlaps(findings)
        assert [(f.kind, f.start, f.end) for f in kept] == [("b", 2, 10), ("c", 12, 14)]
