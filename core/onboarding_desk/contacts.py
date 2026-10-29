# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A lead's email and phone: normalised, masked for display, and hashed for matching.

Contact details are stored encrypted under the tenant key. Matching an
opt-out against a lead uses a keyed hash, so a database reader cannot recover a
number by hashing the small phone-number space. Phones are North American
numbers in E.164 form (+1 and ten digits, valid area and exchange codes).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

MAX_EMAIL = 254
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[A-Za-z]{2,63}$")
_NANP_RE = re.compile(r"^\+1([2-9]\d{2})([2-9]\d{2})(\d{4})$")
# Carrier opt-out keywords, plus their French forms for Canada.
OPT_OUT_KEYWORDS: frozenset[str] = frozenset(
    {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT", "ARRET", "ARRETER", "DESABONNER"}
)


class ContactError(ValueError):
    """A contact detail that cannot be used."""


def normalise_email(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    email = str(value).strip().lower()
    if len(email) > MAX_EMAIL or not _EMAIL_RE.match(email):
        raise ContactError("email is not a valid address")
    return email


def normalise_phone(value: str | None) -> str | None:
    """``+1NXXNXXXXXX`` from common North American formats, or ``ContactError``."""
    if value in (None, ""):
        return None
    digits = re.sub(r"[^\d+]", "", str(value).strip())
    if digits.startswith("+"):
        candidate = digits
    elif len(digits) == 11 and digits.startswith("1"):
        candidate = "+" + digits
    elif len(digits) == 10:
        candidate = "+1" + digits
    else:
        raise ContactError("phone must be a North American number")
    if not _NANP_RE.match(candidate):
        raise ContactError("phone must be a valid North American number in E.164 form")
    return candidate


def mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    return f"+1******{phone[-4:]}"


def contact_hash(secret: str, tenant_id: str, kind: str, value: str) -> str:
    """A keyed hash of a normalised email or phone, scoped to the tenant."""
    message = f"onboarding-desk:{tenant_id}:{kind}:{value}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


def is_opt_out(text: str | None) -> bool:
    """Whether an inbound message is an opt-out: a keyword alone, ignoring case, accents, spaces and punctuation."""
    if not text:
        return False
    word = re.sub(r"[^A-Z]", "", _fold(text.strip()))
    return word in OPT_OUT_KEYWORDS
