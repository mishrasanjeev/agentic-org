"""Account Aggregator consent callback signing + replay protection.

SEC-2026-05-P1-004 (docs/BRUTAL_SECURITY_SCAN_2026-05-01.md). Before
this module, ``api/v1/aa_callback.py`` accepted Finvu/Setu callback
payloads on a public endpoint with no HMAC, no timestamp, no nonce,
and no replay protection. A guessed/leaked/replayed ``consent_handle``
could forge consent lifecycle state — high-stakes because the AA flow
authorizes financial-data fetches.

This module brings AA callbacks to the same signed-webhook discipline
as ``api/v1/webhooks.py`` (SendGrid/Mailchimp/MoEngage), with two
extras the audit specifically asked for:

1. **Timestamp freshness**: rejects requests with timestamps more than
   ``MAX_TIMESTAMP_SKEW_SECONDS`` outside the server clock window.
2. **Nonce replay protection**: every accepted nonce is recorded in
   Redis with TTL = 2 × the skew window. A replayed nonce inside the
   freshness window returns 409 (conflict — already processed).

Signature scheme (matches the SendGrid pattern but with explicit
nonce + version prefix so we can rotate the algorithm without
ambiguity):

    canonical_string = f"v1.{timestamp}.{nonce}.{raw_body_bytes_decoded}"
    signature        = HMAC-SHA256(secret, canonical_string).hexdigest()

Headers expected on the request:

    X-AA-Timestamp: <unix_seconds>
    X-AA-Nonce:     <opaque random string, max 128 chars>
    X-AA-Signature: v1=<hex>

Secret: ``AGENTICORG_AA_CALLBACK_SECRET`` env var. Like the other
webhook secrets, the dev bypass ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1``
opens the gate ONLY in local/dev/test envs (PR-A's env-guard already
prevents misconfig in staging/production).

Per-tenant secrets are a future hardening — the AA spec lets each
tenant onboard their own provider partner, so a per-tenant secret
table is the right long-term shape. PR-C ships the platform-shared
secret (matches every other webhook in the codebase today) and leaves
the per-tenant migration as a follow-up.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Final, Literal

import structlog

logger = structlog.get_logger()

TIMESTAMP_HEADER: Final[str] = "X-AA-Timestamp"
NONCE_HEADER: Final[str] = "X-AA-Nonce"
SIGNATURE_HEADER: Final[str] = "X-AA-Signature"

# Sites that issue AA callbacks (Finvu, Setu) typically retry within a
# 5 minute window. Reject anything outside ±300s to keep the replay
# attack surface tight while tolerating reasonable clock skew.
MAX_TIMESTAMP_SKEW_SECONDS: Final[int] = 300

# Nonce TTL in Redis. 2× the skew window so a replayed nonce inside the
# acceptable timestamp window is always caught. After this TTL, the
# timestamp check itself rejects the message — replay defense reduces
# to timestamp freshness, which is correct.
NONCE_TTL_SECONDS: Final[int] = 2 * MAX_TIMESTAMP_SKEW_SECONDS

NONCE_MAX_LENGTH: Final[int] = 128
SIGNATURE_VERSION: Final[str] = "v1"


def _get_secret() -> str:
    """Return the configured AA callback HMAC secret.

    Empty string means no secret is configured. Callers fail closed
    on empty unless the dev-bypass env-guard is open (local/dev/test
    only — see ``api/v1/webhooks.py::_enforce_unsigned_bypass_env_guard``).
    """
    return os.getenv("AGENTICORG_AA_CALLBACK_SECRET", "").strip()


def _local_dev_envs() -> frozenset[str]:
    """Re-export the envs where the unsigned bypass is honored.

    Single source of truth lives in ``api.v1.webhooks._LOCAL_DEV_ENVS``
    so a future env-list change applies uniformly.
    """
    from api.v1.webhooks import _LOCAL_DEV_ENVS  # noqa: PLC0415

    return _LOCAL_DEV_ENVS


def _dev_bypass_active() -> bool:
    """True iff ``AGENTICORG_WEBHOOK_ALLOW_UNSIGNED=1`` AND env is local/dev/test.

    Mirrors ``api/v1/webhooks._dev_allow_unsigned`` exactly — same
    flag, same env-guard, no separate AA-specific knob to misconfigure.
    """
    if os.getenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED") != "1":
        return False
    env = (os.getenv("AGENTICORG_ENV") or "").strip().lower()
    return env in _local_dev_envs()


def _canonical_string(timestamp: str, nonce: str, body: bytes) -> bytes:
    """Compose the bytes that get signed.

    ``v1.<timestamp>.<nonce>.<body>`` — version prefix lets us rotate
    the algorithm without breaking older signatures (the verifier
    inspects the prefix on the X-AA-Signature header).
    """
    body_str = body.decode("utf-8", errors="strict")
    return f"{SIGNATURE_VERSION}.{timestamp}.{nonce}.{body_str}".encode()


def _compute_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    """Return the hex-encoded HMAC-SHA256 of the canonical string."""
    canonical = _canonical_string(timestamp, nonce, body)
    digest = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


VerifyResult = Literal[
    "ok",
    "missing_signature",
    "missing_secret",
    "bad_signature",
    "stale_timestamp",
    "replay",
    "bad_nonce",
]


async def verify_aa_callback(
    *,
    timestamp_header: str | None,
    nonce_header: str | None,
    signature_header: str | None,
    body: bytes,
    redis_client,  # type: ignore[no-untyped-def]  # noqa: ANN001 — duck-typed Redis
    now: float | None = None,
) -> VerifyResult:
    """Verify an inbound AA callback. Returns one of:

    - ``"ok"``: signature valid, timestamp fresh, nonce never seen → caller proceeds.
    - ``"missing_signature"``: required headers absent → 403.
    - ``"missing_secret"``: ``AGENTICORG_AA_CALLBACK_SECRET`` unset and
      dev bypass not active → 403 (fail closed).
    - ``"bad_signature"``: HMAC mismatch → 403.
    - ``"stale_timestamp"``: outside the freshness window → 403.
    - ``"replay"``: nonce already in Redis → 409.
    - ``"bad_nonce"``: nonce shape invalid (empty, too long, not ASCII) → 403.

    All paths are best-effort tolerant of Redis outages: if Redis is
    unreachable, replay defense degrades to timestamp-freshness only
    (the message must still be within the 5-minute window). This is
    the right tradeoff — refusing all callbacks during a Redis blip
    would lose live consent updates with no security benefit (a
    replayer would still need a fresh timestamp + valid HMAC).
    """
    secret = _get_secret()
    if not secret:
        if _dev_bypass_active():
            logger.warning(
                "aa_callback_secret_unset_dev_bypass",
                note="AGENTICORG_AA_CALLBACK_SECRET unset; dev bypass active",
            )
            return "ok"
        logger.error(
            "aa_callback_secret_unset",
            hint="Set AGENTICORG_AA_CALLBACK_SECRET. Fails closed in non-local envs.",
        )
        return "missing_secret"

    if not timestamp_header or not nonce_header or not signature_header:
        return "missing_signature"

    # Validate nonce shape — protects against a Redis key explosion
    # via huge attacker-supplied nonces.
    nonce = nonce_header.strip()
    if not nonce or len(nonce) > NONCE_MAX_LENGTH or not nonce.isascii():
        return "bad_nonce"

    # Timestamp freshness.
    try:
        ts = int(timestamp_header.strip())
    except (TypeError, ValueError):
        return "stale_timestamp"
    current = now if now is not None else time.time()
    if abs(current - ts) > MAX_TIMESTAMP_SKEW_SECONDS:
        return "stale_timestamp"

    # Constant-time signature compare. Constructing ``expected`` first
    # keeps the timing flat regardless of where the input mismatches.
    expected = _compute_signature(secret, str(ts), nonce, body)
    if not hmac.compare_digest(expected, signature_header.strip()):
        return "bad_signature"

    # Replay check — best effort. Use SET NX EX for atomic
    # "set-if-absent + expire" semantics. ``set(..., nx=True, ex=TTL)``
    # returns ``True`` when the nonce is fresh, ``None`` when it
    # already existed (replay).
    nonce_key = f"aa_callback_nonce:{nonce}"
    try:
        was_set = await redis_client.set(nonce_key, "1", nx=True, ex=NONCE_TTL_SECONDS)
        if was_set is None:
            return "replay"
    except Exception as exc:  # noqa: BLE001 — Redis blip ≠ refuse all callbacks
        logger.warning(
            "aa_callback_replay_store_unavailable",
            error=str(exc),
            note="Falling through to timestamp-freshness-only defense",
        )

    return "ok"
