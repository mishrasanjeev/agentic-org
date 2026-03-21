"""PII masking — default ON. Masks email, phone, Aadhaar, PAN, bank accounts, IFSC."""

from __future__ import annotations

import re
from typing import Any

from core.config import settings

# Patterns for Indian and international PII
PATTERNS = [
    # Email
    (
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        lambda m: m.group()[:2] + "***@***",
    ),
    # Indian phone (+91 or 10 digits)
    (re.compile(r"(?:\+91[\-\s]?)?[6-9]\d{9}"), lambda m: m.group()[:4] + "******"),
    # Aadhaar (12 digits, may have spaces)
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), lambda m: "XXXX-XXXX-" + m.group()[-4:]),
    # PAN (ABCDE1234F)
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), lambda m: "XXXXX" + m.group()[-5:]),
    # Bank account (8-18 digits)
    (
        re.compile(r"\b\d{8,18}\b"),
        lambda m: "****" + m.group()[-4:] if len(m.group()) > 6 else m.group(),
    ),
    # IFSC
    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), lambda m: m.group()[:4] + "0******"),
]


def mask_string(value: str) -> str:
    """Mask PII patterns in a string."""
    if not settings.pii_masking:
        return value
    for pattern, replacer in PATTERNS:
        value = pattern.sub(replacer, value)
    return value


def mask_pii(data: Any) -> Any:
    """Recursively mask PII in dicts, lists, and strings."""
    if not settings.pii_masking:
        return data
    if isinstance(data, str):
        return mask_string(data)
    if isinstance(data, dict):
        return {k: mask_pii(v) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_pii(item) for item in data]
    return data
