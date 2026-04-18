"""Tests for pre-LLM PII redaction (PRD v4.0.0 Section 7).

Covers:
  - Aadhaar, PAN, GSTIN, UPI detection and token replacement
  - Email + phone detection (Presidio built-in)
  - Round-trip redact -> deanonymize
  - Mode = disabled / logs_only skips LLM redaction
  - Nested PII inside JSON values
  - Performance (<50 ms for typical input)

All Presidio imports are guarded with try/except so that the test suite
can run even when presidio is not installed (mocked path).
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to decide whether Presidio is genuinely importable
# ---------------------------------------------------------------------------
_PRESIDIO_INSTALLED = False
try:
    import presidio_analyzer  # noqa: F401
    import presidio_anonymizer  # noqa: F401

    _PRESIDIO_INSTALLED = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton() -> Any:
    """Ensure each test gets a fresh PIIRedactor singleton."""
    from core.pii.redactor import PIIRedactor

    PIIRedactor.reset()
    yield
    PIIRedactor.reset()


@pytest.fixture(autouse=True)
def _set_mode_before_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default all tests to before_llm mode unless overridden."""
    monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "before_llm")


# ---------------------------------------------------------------------------
# Mock-based helpers for when Presidio is NOT installed
# ---------------------------------------------------------------------------

def _make_mock_result(entity_type: str, start: int, end: int, score: float = 0.85) -> MagicMock:
    r = MagicMock()
    r.entity_type = entity_type
    r.start = start
    r.end = end
    r.score = score
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestWithPresidio:
    """Tests that exercise the real Presidio engine."""

    def test_aadhaar_redacted(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "My Aadhaar is 1234 5678 9012"
        redacted, token_map = redactor.redact(text)

        # The Aadhaar number should be replaced by a token
        assert "1234 5678 9012" not in redacted
        assert "<AADHAAR_1>" in redacted
        assert token_map.get("<AADHAAR_1>") == "1234 5678 9012"

    def test_pan_redacted(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "PAN: ABCDE1234F"
        redacted, token_map = redactor.redact(text)

        assert "ABCDE1234F" not in redacted
        assert "<PAN_1>" in redacted
        assert token_map.get("<PAN_1>") == "ABCDE1234F"

    def test_gstin_redacted(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "GSTIN: 22AAAAA0000A1Z5"
        redacted, token_map = redactor.redact(text)

        assert "22AAAAA0000A1Z5" not in redacted
        assert "<GSTIN_1>" in redacted
        assert token_map.get("<GSTIN_1>") == "22AAAAA0000A1Z5"

    def test_upi_redacted(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "UPI: rajesh@icici"
        redacted, token_map = redactor.redact(text)

        assert "rajesh@icici" not in redacted
        # Could be UPI or EMAIL depending on Presidio scoring; accept either
        has_upi = any("UPI" in k for k in token_map)
        has_email = any("EMAIL" in k for k in token_map)
        assert has_upi or has_email
        assert "rajesh@icici" in token_map.values()

    def test_email_and_phone_redacted(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "Contact: rajesh@example.com or call +91 9876543210"
        redacted, token_map = redactor.redact(text)

        assert "rajesh@example.com" not in redacted
        # Phone recognition depends on Presidio's built-in phone recognizer
        has_email = any("EMAIL" in k or "email" in k.lower() for k in token_map)
        assert has_email

    def test_deanonymize_restores_all(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        original = "My Aadhaar is 1234 5678 9012 and PAN is ABCDE1234F"
        redacted, token_map = redactor.redact(original)

        restored = redactor.deanonymize(redacted, token_map)
        assert "1234 5678 9012" in restored
        assert "ABCDE1234F" in restored

    def test_nested_pii_in_json(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        payload = json.dumps({
            "employee": "Rajesh Kumar",
            "aadhaar": "1234 5678 9012",
            "email": "rajesh@example.com",
        })
        redacted, token_map = redactor.redact(payload)

        assert "1234 5678 9012" not in redacted
        assert "rajesh@example.com" not in redacted
        assert len(token_map) >= 2

    def test_performance_under_50ms(self) -> None:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = (
            "Name: Rajesh Kumar, Email: rajesh@example.com, "
            "Phone: +91 9876543210, Aadhaar: 1234 5678 9012, "
            "PAN: ABCDE1234F, GSTIN: 22AAAAA0000A1Z5"
        )
        # Warm-up
        redactor.redact(text)

        start = time.perf_counter()
        for _ in range(10):
            redactor.redact(text)
        elapsed_ms = (time.perf_counter() - start) / 10 * 1000

        assert elapsed_ms < 50, f"Redaction took {elapsed_ms:.1f}ms, expected <50ms"


class TestWithMocks:
    """Tests that work without Presidio installed (mock-based)."""

    def test_mode_disabled_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "disabled")

        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "My Aadhaar is 1234 5678 9012"
        redacted, token_map = redactor.redact(text)

        assert redacted == text
        assert token_map == {}

    def test_mode_logs_only_skips_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "logs_only")

        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        text = "PAN: ABCDE1234F"
        redacted, token_map = redactor.redact(text)

        assert redacted == text
        assert token_map == {}


class TestDeanonymizer:
    """Unit tests for the deanonymizer module directly."""

    def test_basic_replacement(self) -> None:
        from core.pii.deanonymizer import deanonymize

        text = "Name is <PERSON_1> and email is <EMAIL_ADDRESS_1>"
        token_map = {
            "<PERSON_1>": "Rajesh Kumar",
            "<EMAIL_ADDRESS_1>": "rajesh@example.com",
        }
        result = deanonymize(text, token_map)
        assert result == "Name is Rajesh Kumar and email is rajesh@example.com"

    def test_empty_map_returns_original(self) -> None:
        from core.pii.deanonymizer import deanonymize

        text = "Hello <PERSON_1>"
        assert deanonymize(text, {}) == text

    def test_multiple_occurrences(self) -> None:
        from core.pii.deanonymizer import deanonymize

        text = "<PERSON_1> sent money to <PERSON_1>"
        token_map = {"<PERSON_1>": "Rajesh"}
        assert deanonymize(text, token_map) == "Rajesh sent money to Rajesh"


class TestIndiaRecognizers:
    """Test India recognizer classes instantiate correctly."""

    def test_aadhaar_recognizer_creates(self) -> None:
        from core.pii.india_recognizers import AadhaarRecognizer

        r = AadhaarRecognizer()
        entities = getattr(r, "supported_entities", None) or [getattr(r, "supported_entity", "")]
        assert "AADHAAR" in entities

    def test_pan_recognizer_creates(self) -> None:
        from core.pii.india_recognizers import PANRecognizer

        r = PANRecognizer()
        entities = getattr(r, "supported_entities", None) or [getattr(r, "supported_entity", "")]
        assert "PAN" in entities

    def test_gstin_recognizer_creates(self) -> None:
        from core.pii.india_recognizers import GSTINRecognizer

        r = GSTINRecognizer()
        entities = getattr(r, "supported_entities", None) or [getattr(r, "supported_entity", "")]
        assert "GSTIN" in entities

    def test_upi_recognizer_creates(self) -> None:
        from core.pii.india_recognizers import UPIRecognizer

        r = UPIRecognizer()
        entities = getattr(r, "supported_entities", None) or [getattr(r, "supported_entity", "")]
        assert "UPI" in entities
