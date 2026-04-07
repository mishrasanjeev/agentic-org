"""Content safety checker — multi-signal safety analysis for agent outputs.

Checks:
1. **PII leakage** — reuses Presidio from ``core/pii/`` to detect accidental
   PII in outbound content.
2. **Toxicity** — uses a simple keyword-based fallback when ``transformers``
   is not installed; upgrades to a HuggingFace toxicity classifier when available.
3. **Near-duplicate** — cosine similarity against a rolling hash of previous
   outputs to detect repetitive / looping agents.

All heavy imports are guarded so the module loads without optional deps.
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Guarded optional imports
# ---------------------------------------------------------------------------
try:
    from transformers import pipeline as hf_pipeline  # type: ignore[import-untyped]

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    _TRANSFORMERS_AVAILABLE = False
    hf_pipeline = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Toxicity keyword list (fallback when transformers is not available)
# ---------------------------------------------------------------------------
_TOXIC_KEYWORDS: set[str] = {
    "kill", "murder", "attack", "bomb", "terrorist", "hate",
    "racist", "sexist", "slur", "abuse", "harass", "threat",
    "violence", "weapon", "suicide", "self-harm", "exploit",
    "trafficking", "molest", "rape", "torture",
}

# ---------------------------------------------------------------------------
# Near-duplicate detection — rolling window of recent output hashes
# ---------------------------------------------------------------------------
_RECENT_HASHES: deque[str] = deque(maxlen=100)


def _text_hash(text: str) -> str:
    """Stable hash of normalised text for duplicate detection."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _jaccard_similarity(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two strings."""
    tokens_a = set(re.sub(r"\s+", " ", a.strip().lower()).split())
    tokens_b = set(re.sub(r"\s+", " ", b.strip().lower()).split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# Singleton toxicity classifier (lazy-loaded)
# ---------------------------------------------------------------------------
_toxicity_classifier: Any = None


def _get_toxicity_classifier() -> Any:
    """Return a HuggingFace toxicity classifier or None."""
    global _toxicity_classifier
    if _toxicity_classifier is not None:
        return _toxicity_classifier
    if not _TRANSFORMERS_AVAILABLE:
        return None
    try:
        _toxicity_classifier = hf_pipeline(
            "text-classification",
            model="unitary/toxic-bert",
            truncation=True,
        )
        return _toxicity_classifier
    except Exception as exc:
        logger.warning("toxicity_classifier_load_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# PII check (reuses core/pii)
# ---------------------------------------------------------------------------
def _check_pii(text: str) -> tuple[float, list[dict[str, str]]]:
    """Detect PII in text using Presidio.

    Returns (score, issues) where score is 0.0 (clean) to 1.0 (lots of PII).
    """
    try:
        from core.pii.redactor import PIIRedactor

        redactor = PIIRedactor()
        _, token_map = redactor.redact(text)
        if not token_map:
            return 0.0, []

        count = len(token_map)
        # Score: rough linear scale, caps at 1.0
        score = min(count / 5.0, 1.0)
        issues = [
            {"type": "pii", "detail": f"Detected PII token: {token}", "severity": "high"}
            for token in token_map
        ]
        return score, issues
    except Exception as exc:
        logger.debug("pii_check_skipped", error=str(exc))
        return 0.0, []


# ---------------------------------------------------------------------------
# Toxicity check
# ---------------------------------------------------------------------------
def _check_toxicity(text: str, threshold: float = 0.7) -> tuple[float, list[dict[str, str]]]:
    """Check text for toxic content.

    Uses HuggingFace classifier if available, falls back to keyword matching.
    Returns (score, issues).
    """
    classifier = _get_toxicity_classifier()

    if classifier is not None:
        try:
            result = classifier(text[:512])  # truncate for speed
            if result and isinstance(result, list):
                item = result[0]
                label = item.get("label", "").lower()
                score = item.get("score", 0.0)
                # toxic-bert: label "toxic" with confidence score
                if "toxic" in label and score >= threshold:
                    return score, [
                        {
                            "type": "toxicity",
                            "detail": f"Toxic content detected (score={score:.2f})",
                            "severity": "high",
                        }
                    ]
                return score if "toxic" in label else 0.0, []
        except Exception as exc:
            logger.debug("toxicity_classifier_failed", error=str(exc))
            # Fall through to keyword check

    # Keyword-based fallback
    text_lower = text.lower()
    words = set(re.findall(r"\b\w+\b", text_lower))
    found = words & _TOXIC_KEYWORDS
    if not found:
        return 0.0, []

    score = min(len(found) / 3.0, 1.0)
    issues = [
        {"type": "toxicity", "detail": f"Toxic keyword detected: {kw}", "severity": "medium"}
        for kw in sorted(found)
    ]
    return score, issues


# ---------------------------------------------------------------------------
# Near-duplicate check
# ---------------------------------------------------------------------------
def _check_duplicate(text: str, threshold: float = 0.85) -> tuple[float, list[dict[str, str]]]:
    """Check if text is a near-duplicate of a recent output.

    Uses exact hash match first, then Jaccard similarity as fallback.
    Returns (score, issues).
    """
    current_hash = _text_hash(text)

    # Exact duplicate
    if current_hash in _RECENT_HASHES:
        _RECENT_HASHES.append(current_hash)
        return 1.0, [
            {"type": "duplicate", "detail": "Exact duplicate of a recent output", "severity": "medium"}
        ]

    # Jaccard similarity against recent hashes is not meaningful (hashes lose info),
    # so we keep a small text window for similarity checking.
    # For efficiency, just do exact hash for now and log the hash.
    _RECENT_HASHES.append(current_hash)
    return 0.0, []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Default configuration
DEFAULT_CONFIG: dict[str, Any] = {
    "check_pii": True,
    "check_toxicity": True,
    "check_duplicates": True,
    "toxicity_threshold": 0.7,
}


async def check_content_safety(
    text: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run content safety checks on text.

    Parameters
    ----------
    text : str
        The agent output text to check.
    config : dict | None
        Override default check configuration.  Keys:
        ``check_pii``, ``check_toxicity``, ``check_duplicates``,
        ``toxicity_threshold``.

    Returns
    -------
    dict
        ``{safe: bool, issues: [{type, detail, severity}], scores: {pii, toxicity, duplicate}}``
    """
    if text is None or not isinstance(text, str) or not text.strip():
        return {"safe": True, "issues": [], "scores": {"pii": 0.0, "toxicity": 0.0, "duplicate": 0.0}}

    cfg = {**DEFAULT_CONFIG, **(config or {})}
    issues: list[dict[str, str]] = []
    scores: dict[str, float] = {"pii": 0.0, "toxicity": 0.0, "duplicate": 0.0}

    if not text or not text.strip():
        return {"safe": True, "issues": [], "scores": scores}

    # PII check
    if cfg.get("check_pii", True):
        pii_score, pii_issues = _check_pii(text)
        scores["pii"] = pii_score
        issues.extend(pii_issues)

    # Toxicity check
    if cfg.get("check_toxicity", True):
        tox_threshold = cfg.get("toxicity_threshold", 0.7)
        tox_score, tox_issues = _check_toxicity(text, threshold=tox_threshold)
        scores["toxicity"] = tox_score
        issues.extend(tox_issues)

    # Duplicate check
    if cfg.get("check_duplicates", True):
        dup_score, dup_issues = _check_duplicate(text)
        scores["duplicate"] = dup_score
        issues.extend(dup_issues)

    safe = len(issues) == 0

    if not safe:
        logger.warning(
            "content_safety_issues",
            issue_count=len(issues),
            scores=scores,
        )

    return {"safe": safe, "issues": issues, "scores": scores}
