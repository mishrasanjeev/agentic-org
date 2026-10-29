# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Time-based one-time passwords (RFC 6238) for authenticator-app MFA.

Secrets are 160-bit random values, shown to the user once as a QR code and a
base32 key, then stored encrypted (``core.crypto.credential_vault``). Codes are
the six-digit HMAC-SHA1, 30-second variant every authenticator app supports.

``verify`` accepts the current step and one step either side for clock drift,
and refuses any step at or before the last one accepted, so a code that was
seen once (shoulder surfing, a proxy log) cannot be replayed in its window.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote, urlencode

ISSUER = "AgenticOrg"
DIGITS = 6
STEP_SECONDS = 30
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def generate_secret() -> str:
    """A fresh 160-bit secret as unpadded base32 (what authenticator apps read)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(secret: str, step: int) -> str:
    """The code for one 30-second step (RFC 4226 dynamic truncation)."""
    digest = hmac.new(_secret_bytes(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def normalize_code(code: str) -> str:
    """Remove ASCII spacing and hyphens; refuse non-text or non-ASCII input."""
    if not isinstance(code, str) or not code.isascii():
        return ""
    return "".join(ch for ch in code if not ch.isspace() and ch != "-")


def verify(secret: str, code: str, *, last_used_step: int | None = None, now: float | None = None) -> int | None:
    """Return the matched step, or ``None`` when the code is wrong or replayed."""
    candidate = normalize_code(code)
    if len(candidate) != DIGITS or not all("0" <= ch <= "9" for ch in candidate):
        return None
    step = current_step(now)
    matched: int | None = None
    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        probe = step + offset
        # Compare every candidate so timing does not reveal which window matched.
        if hmac.compare_digest(code_at(secret, probe), candidate) and matched is None:
            matched = probe
    if matched is None:
        return None
    if last_used_step is not None and matched <= last_used_step:
        return None
    return matched


def provisioning_uri(secret: str, account: str) -> str:
    """The ``otpauth://`` URI an authenticator app imports from the QR code."""
    label = quote(f"{ISSUER}:{account}", safe="")
    query = urlencode(
        {"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def qr_svg_data_uri(uri: str) -> str:
    """Render ``uri`` as a QR code SVG ``data:`` URI (rendered server-side, no CDN)."""
    import segno  # noqa: PLC0415 - only the enrolment path needs it

    buffer = io.BytesIO()
    segno.make(uri, error="m").save(buffer, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff")
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def format_secret(secret: str) -> str:
    """Group the key in fours for people who type it instead of scanning."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """One-time recovery codes, ``xxxxx-xxxxx`` (about 49 bits each)."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(user_id: str, code: str) -> str:
    """scrypt with a per-user salt: slow to brute-force, and two users' identical codes never collide.

    One derivation per sign-in attempt is compared against the user's stored
    hashes, so the cost is paid once, not once per remaining code.
    Invalid recovery input raises ``ValueError`` before any derivation.
    """
    if not looks_like_recovery_code(code):
        raise ValueError("Invalid recovery code")
    normalized = normalize_code(code).lower()
    salt = f"agenticorg-mfa-recovery:{user_id}".encode()
    return hashlib.scrypt(normalized.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def looks_like_recovery_code(code: str) -> bool:
    normalized = normalize_code(code).lower()
    return len(normalized) == 10 and all(ch in _RECOVERY_ALPHABET for ch in normalized)
