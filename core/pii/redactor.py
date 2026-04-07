"""Pre-LLM PII redaction engine powered by Microsoft Presidio.

Provides a thread-safe singleton ``PIIRedactor`` that:
- Detects PII entities using Presidio AnalyzerEngine (50+ built-in recognizers)
  plus custom India recognizers (Aadhaar, PAN, GSTIN, UPI).
- Replaces each entity with a deterministic token: ``<ENTITY_TYPE_N>``
  (e.g. ``<PERSON_1>``, ``<AADHAAR_1>``).
- Returns a token_map so the caller can de-anonymize after the LLM responds.

Configuration via ``AGENTICORG_PII_REDACTION_MODE`` env var:
  - ``before_llm`` : redact before LLM, deanonymize after (full protection)
  - ``logs_only``  : only redact in audit logs (legacy behaviour)
  - ``disabled``   : no redaction at all
"""

from __future__ import annotations

import os
import threading
from typing import Any

import structlog

from core.pii.deanonymizer import deanonymize as _deanonymize

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Optional Presidio imports — guarded so tests can mock / run without install
# ---------------------------------------------------------------------------
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    _PRESIDIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PRESIDIO_AVAILABLE = False
    AnalyzerEngine = None  # type: ignore[assignment,misc]
    AnonymizerEngine = None  # type: ignore[assignment,misc]
    OperatorConfig = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Redaction mode
# ---------------------------------------------------------------------------
PII_MODE_BEFORE_LLM = "before_llm"
PII_MODE_LOGS_ONLY = "logs_only"
PII_MODE_DISABLED = "disabled"

_VALID_MODES = {PII_MODE_BEFORE_LLM, PII_MODE_LOGS_ONLY, PII_MODE_DISABLED}


def _get_pii_mode() -> str:
    mode = os.environ.get("AGENTICORG_PII_REDACTION_MODE", PII_MODE_BEFORE_LLM).lower().strip()
    if mode not in _VALID_MODES:
        logger.warning("pii_invalid_mode", mode=mode, fallback=PII_MODE_BEFORE_LLM)
        return PII_MODE_BEFORE_LLM
    return mode


# ---------------------------------------------------------------------------
# PIIRedactor — thread-safe singleton
# ---------------------------------------------------------------------------
class PIIRedactor:
    """Detect and anonymize PII using Presidio, with India-specific recognizers."""

    _instance: PIIRedactor | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> PIIRedactor:
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False  # type: ignore[attr-defined]
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        if not _PRESIDIO_AVAILABLE:
            logger.warning("presidio_not_installed", msg="PII redaction will be a no-op")
            self._analyzer: Any = None
            self._anonymizer: Any = None
            return

        # Build analyzer with custom India recognizers
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

        # Register India-specific recognizers
        from core.pii.india_recognizers import ALL_INDIA_RECOGNIZERS

        for recognizer_cls in ALL_INDIA_RECOGNIZERS:
            self._analyzer.registry.add_recognizer(recognizer_cls())

        logger.info("pii_redactor_initialized", recognizer_count=len(self._analyzer.registry.recognizers))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        """Current redaction mode (re-read from env on every call for hot-reload)."""
        return _get_pii_mode()

    def redact(self, text: str) -> tuple[str, dict[str, str]]:
        """Redact PII from *text*.

        Returns:
            Tuple of (redacted_text, token_map).
            ``token_map`` maps ``"<ENTITY_TYPE_N>"`` -> ``"original_value"``.
            When mode is not ``before_llm`` or Presidio is unavailable,
            returns the original text with an empty map.
        """
        if text is None:
            return "", {}

        if self.mode != PII_MODE_BEFORE_LLM:
            return text, {}

        if self._analyzer is None:
            return text, {}

        # Analyze
        results = self._analyzer.analyze(text=text, language="en")

        if not results:
            return text, {}

        # Remove overlapping entities: when a smaller entity is fully contained
        # inside a larger one, drop the smaller entity (keep the wider span).
        # Sort by span length descending (prefer wider), then by score descending.
        results_sorted = sorted(results, key=lambda r: (-(r.end - r.start), -r.score))
        kept: list[Any] = []
        for r in results_sorted:
            # Check if this result overlaps with any already-kept result
            overlaps = False
            for k in kept:
                if r.start >= k.start and r.end <= k.end:
                    # r is contained inside k — drop it
                    overlaps = True
                    break
                if k.start >= r.start and k.end <= r.end:
                    # k is contained inside r — should not happen since we
                    # process wider spans first, but handle gracefully
                    overlaps = True
                    break
                # Partial overlap: keep the higher-scoring one
                if r.start < k.end and r.end > k.start:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(r)
        results = kept

        # Build token map with deterministic counters per entity type
        entity_counters: dict[str, int] = {}
        # Assign tokens in document order (ascending).
        assignments: list[tuple[Any, str]] = []
        for result in sorted(results, key=lambda r: r.start):
            etype = result.entity_type
            entity_counters[etype] = entity_counters.get(etype, 0) + 1
            token = f"<{etype}_{entity_counters[etype]}>"
            assignments.append((result, token))

        # Build token_map and replace in reverse order to preserve positions
        token_map: dict[str, str] = {}
        # Sort assignments by start descending for replacement
        for result, token in sorted(assignments, key=lambda a: a[0].start, reverse=True):
            original = text[result.start:result.end]
            token_map[token] = original
            text = text[:result.start] + token + text[result.end:]

        logger.debug("pii_redacted", entities_found=len(token_map))
        return text, token_map

    def deanonymize(self, text: str, token_map: dict[str, str]) -> str:
        """Restore original PII values in *text* using *token_map*.

        Delegates to :func:`core.pii.deanonymizer.deanonymize`.
        """
        if not token_map:
            return text
        return _deanonymize(text, token_map)

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (useful for testing)."""
        with cls._lock:
            cls._instance = None
