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

import importlib.util
import json
import os
import re
import threading
from types import SimpleNamespace
from typing import Any

import structlog

from core.pii.deanonymizer import deanonymize as _deanonymize

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Optional Presidio imports — guarded so tests can mock / run without install
# ---------------------------------------------------------------------------
try:
    from presidio_analyzer import AnalyzerEngine

    _PRESIDIO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PRESIDIO_AVAILABLE = False
    AnalyzerEngine = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Redaction mode
# ---------------------------------------------------------------------------
PII_MODE_BEFORE_LLM = "before_llm"
PII_MODE_LOGS_ONLY = "logs_only"
PII_MODE_DISABLED = "disabled"

_VALID_MODES = {PII_MODE_BEFORE_LLM, PII_MODE_LOGS_ONLY, PII_MODE_DISABLED}

_SPACY_MODEL = "en_core_web_sm"

# Possessive runs and boundary anchors prevent rescanning malformed user input.
_COUNTED_QUANTITIES = re.compile(
    r"(?:(?<![\d\s,])\s++|(?<![\w\s,./:-]))"
    r"(?P<counts>\d++(?:\s*+,\s*+\d++)*+(?:(?:\s*+,\s*+|\s++)and\s++\d++)?)"
    r"\s++(?:more\s++)?(?:items?|units?|boxes?|records?|rows?|requests?|tokens?)\b",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]++@[A-Za-z0-9.-]+\.[A-Za-z]{2,}+\b")
_TEMPORAL_CONTEXT = re.compile(
    r"\b(?:dob|birth|birthday|born|date|time|appointment|meeting|calendar)\b",
    re.IGNORECASE,
)

# These productions contain grammatical references, not a slot for a person's
# or organization's name. Never exempt an unknown word or an overlapping span.
_HINDI_REFERENCE_QUERY = re.compile(
    r"(?<![\w\u0900-\u097f])(?:अभी|पहले)\s++(?:बताई|बताया|बताए)\s++(?:गई|गया|गए)\s++"
    r"(?:मेरी|मेरा|मेरे|हमारी|हमारा|हमारे|आपकी|आपका|आपके)\s++"
    r"(?:काल्पनिक\s++)?(?:दुकान|कंपनी|वस्तु|खाते|खाता)\s++"
    r"(?:का|की|के)\s++नाम\s++और\s++रंग\s++क्या\s++(?:है|हैं)\s*+[?।]"
)
_HINDI_GRAMMAR_PARTS = (
    re.compile(r"(?<![\w\u0900-\u097f])उपकरण\s++(?P<grammar>या)\s++बाहरी\s++कार्रवाई(?=[।.!?\s]|$)"),
    re.compile(r"(?<![\w\u0900-\u097f])याद\s++(?P<grammar>रखा\s++गया\s++है)(?=[।.!?\s]|$)"),
)
_JSON_STRING = re.compile(r'"(?:[^"\\]++|\\.)*+"')
_QUOTED_TEXT = re.compile(r'"[^"]*+"|“[^”]*+”|\'[^\']*+\'|‘[^’]*+’')


def _quoted_hindi_grammar(text: str, start: int, end: int) -> bool:
    """Literal names stay protected, including escaped quotes in JSON utterances."""
    line_start, line_end = text.rfind("\n", 0, start) + 1, text.find("\n", end)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    for prefix in ("Inputs: ", "Context: "):
        if line.startswith(prefix):
            line, line_start = line[len(prefix):], line_start + len(prefix)
    try:
        json.loads(line)
    except (ValueError, TypeError, RecursionError):
        return any(match.start() < end and start < match.end() for match in _QUOTED_TEXT.finditer(text))
    for token in _JSON_STRING.finditer(line):
        if token.start() <= start - line_start < end - line_start <= token.end():
            # Only utterance fields are grammatical reference text. Other
            # JSON values may be literal entity names, even if they look like a query.
            if re.search(r'"(?:task|text)"\s*:\s*$', line[:token.start()]) is None:
                return True
            decoded = json.loads(token.group())
            fragment = text[start:end]
            return any(fragment in match.group() for match in _QUOTED_TEXT.finditer(decoded))
    return False


def _hindi_grammar_spans(text: str) -> list[tuple[int, int]]:
    if os.environ.get("AGENTICORG_PII_HINDI_GRAMMAR_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return []
    spans = [
        match.span() for match in _HINDI_REFERENCE_QUERY.finditer(text)
        if not _quoted_hindi_grammar(text, match.start(), match.end())
    ]
    spans.extend(
        match.span("grammar") for pattern in _HINDI_GRAMMAR_PARTS for match in pattern.finditer(text)
        if not _quoted_hindi_grammar(text, match.start(), match.end())
    )
    return spans


def _is_hindi_grammar_entity(text: str, result: Any, spans: list[tuple[int, int]] | None = None) -> bool:
    if result.entity_type not in {"PERSON", "ORGANIZATION"}:
        return False
    metadata = getattr(result, "recognition_metadata", None)
    if not isinstance(metadata, dict) or metadata.get("recognizer_name") != "SpacyRecognizer":
        return False
    if spans is None:
        spans = _hindi_grammar_spans(text)
    return any(start <= result.start < result.end <= end for start, end in spans)


_REGEX_FALLBACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AADHAAR", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")),
    ("GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("EMAIL", _EMAIL_PATTERN),
    ("UPI", re.compile(r"\b[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9._-]{2,}\b")),
    ("PHONE", re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")),
)


def _get_pii_mode() -> str:
    mode = os.environ.get("AGENTICORG_PII_REDACTION_MODE", PII_MODE_BEFORE_LLM).lower().strip()
    if mode not in _VALID_MODES:
        logger.warning("pii_invalid_mode", mode=mode, fallback=PII_MODE_BEFORE_LLM)
        return PII_MODE_BEFORE_LLM
    return mode


def _strict_runtime() -> bool:
    """Return whether missing full PII support must stop execution."""
    from core.config import is_strict_runtime_env, settings

    runtime_env = os.environ.get("AGENTICORG_ENV", settings.env)
    return is_strict_runtime_env(runtime_env)


def _spacy_model_available() -> bool:
    """Check the packaged spaCy model without invoking its downloader."""
    try:
        return importlib.util.find_spec(_SPACY_MODEL) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _regex_fallback_redact(text: str) -> tuple[str, dict[str, str]]:
    """Redact common PII patterns when Presidio is unavailable."""
    matches: list[tuple[int, int, str]] = []
    for entity_type, pattern in _REGEX_FALLBACK_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), entity_type))

    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, entity_type in matches:
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in kept):
            continue
        kept.append((start, end, entity_type))

    if not kept:
        return text, {}

    counters: dict[str, int] = {}
    assignments: list[tuple[int, int, str, str]] = []
    for start, end, entity_type in kept:
        counters[entity_type] = counters.get(entity_type, 0) + 1
        token = f"<{entity_type}_{counters[entity_type]}>"
        assignments.append((start, end, entity_type, token))

    token_map: dict[str, str] = {}
    for start, end, _entity_type, token in sorted(assignments, reverse=True):
        token_map[token] = text[start:end]
        text = text[:start] + token + text[end:]
    return text, token_map


# ---------------------------------------------------------------------------
# PIIRedactor — thread-safe singleton
# ---------------------------------------------------------------------------
class PIIRedactor:
    """Detect and anonymize PII using Presidio, with India-specific recognizers."""

    _instance: PIIRedactor | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    # Class-level defaults so `self._analyzer`/`self._anonymizer` always
    # exist from first attribute access. Without these, a second thread
    # that sees ``_initialized=True`` before the first thread finishes
    # ``__init__`` would hit AttributeError on ``_analyzer`` — the exact
    # crash reported in Session 5 BUG-S5-005 during agent sample generation.
    _analyzer: Any = None
    _anonymizer: Any = None

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
        # Lock the whole __init__ so two concurrent `PIIRedactor()` calls
        # cannot race on partial initialization. The _initialized flag
        # inside the lock is what makes this safe under multi-threaded
        # startup (uvicorn workers, LangGraph concurrent nodes).
        with self._lock:
            if self._initialized:
                return
            if not _PRESIDIO_AVAILABLE:
                if _strict_runtime():
                    raise RuntimeError(
                        "Presidio is unavailable in a strict runtime; refusing to initialize PII redaction"
                    )
                logger.warning(
                    "presidio_not_installed",
                    msg="Using bounded regex PII redaction in this relaxed runtime",
                )
                # Defensive explicit assignment even though class-level
                # defaults already cover this. Keeps the intent obvious.
                self._analyzer = None
                self._anonymizer = None
                self._initialized = True
                return

            # Build analyzer with custom India recognizers. AnalyzerEngine()
            # constructs a spaCy NLP pipeline eagerly and raises OSError when
            # the language model isn't installed — which is how the
            # production image shipped before this fix. Rather than crashing
            # the agent runtime with a 500 ("util.py:531 OSError"), degrade
            # to regex-only redaction: the India recognizers (Aadhaar, PAN,
            # GSTIN, UPI) are pattern-based and remain effective without
            # spaCy.
            #
            # Presidio's default is ``en_core_web_lg`` (~500MB). Point it at
            # ``en_core_web_sm`` (~15MB) via NlpEngineProvider — the Dockerfile
            # installs this smaller model, which keeps image size reasonable
            # while still covering English NER.
            try:
                from presidio_analyzer.nlp_engine import NlpEngineProvider

                if not _spacy_model_available():
                    raise OSError(f"spaCy model {_SPACY_MODEL!r} is not installed")
                nlp_engine = NlpEngineProvider(
                    nlp_configuration={
                        "nlp_engine_name": "spacy",
                        "models": [{"lang_code": "en", "model_name": _SPACY_MODEL}],
                    }
                ).create_engine()
                self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
                # Redaction is performed below with deterministic tokens.
                # Presidio Anonymizer is deliberately not required: it was
                # unused here and its cryptography upper bound prevented us
                # from installing patched cryptography releases.
                self._anonymizer = None
            except (OSError, SystemExit) as exc:
                if _strict_runtime():
                    raise RuntimeError(
                        "Full Presidio NLP support is unavailable in a strict runtime; "
                        "refusing to continue with partial PII coverage"
                    ) from exc
                logger.warning(
                    "presidio_spacy_model_unavailable",
                    error=str(exc),
                    msg=(
                        "Presidio's spaCy model is unavailable; using bounded "
                        "regex PII redaction in this relaxed runtime"
                    ),
                )
                self._analyzer = None
                self._anonymizer = None
                self._initialized = True
                return

            # Register India-specific recognizers (kept inside the lock so
            # _initialized only flips to True after the analyzer is fully
            # populated — otherwise a second thread could find a half-built
            # analyzer with no recognizers).
            from core.pii.india_recognizers import ALL_INDIA_RECOGNIZERS

            for recognizer_cls in ALL_INDIA_RECOGNIZERS:
                self._analyzer.registry.add_recognizer(recognizer_cls())

            logger.info(
                "pii_redactor_initialized",
                recognizer_count=len(self._analyzer.registry.recognizers),
            )
            self._initialized = True

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
            return _regex_fallback_redact(text)

        # Analyze
        grammar_spans = _hindi_grammar_spans(text)
        results = [
            result
            for result in self._analyzer.analyze(text=text, language="en")
            if not _is_hindi_grammar_entity(text, result, grammar_spans)
        ]

        # A bare count in an explicit quantity list is not a date. Keep every
        # other entity, including dates with explicit temporal context and
        # overlapping identifiers.
        count_spans = {
            (match.start("counts") + number.start(), match.start("counts") + number.end())
            for match in _COUNTED_QUANTITIES.finditer(text)
            if not _TEMPORAL_CONTEXT.search(text[max(0, match.start("counts") - 64):match.start("counts")])
            for number in re.finditer(r"\d+", match.group("counts"))
        }
        results = [
            result for result in results
            if result.entity_type != "DATE_TIME" or (result.start, result.end) not in count_spans
        ]

        # Full email matches must outrank a shorter UPI match, including on
        # reserved/internal domains not recognized by the NLP email detector.
        results.extend(
            SimpleNamespace(entity_type="EMAIL_ADDRESS", start=match.start(), end=match.end(), score=1.0)
            for match in _EMAIL_PATTERN.finditer(text)
        )

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

        if os.environ.get("AGENTICORG_PII_DEBUG", "").lower() in ("1", "true", "yes"):
            logger.debug("pii_redacted", entities_found=len(token_map))
        return text, token_map

    def find_entities(self, text: str, entities: list[str]) -> list[tuple[int, int, str]]:
        """``(start, end, entity_type)`` spans the NLP analyser finds, regardless of mode.

        Empty when the analyser is not installed (relaxed runtimes only; strict
        runtimes refuse to construct the redactor without it).
        """
        if self._analyzer is None or not text:
            return []
        results = self._analyzer.analyze(text=text, language="en", entities=entities)
        grammar_spans = _hindi_grammar_spans(text)
        return [
            (result.start, result.end, result.entity_type)
            for result in results
            if not _is_hindi_grammar_entity(text, result, grammar_spans)
        ]

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
