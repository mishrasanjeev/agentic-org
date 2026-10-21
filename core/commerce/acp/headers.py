# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""ACP request headers: ``API-Version``, ``Idempotency-Key``, ``Request-Id``, ``Signature``, ``Timestamp`` (G34).

ACP 2026-04-17 (RFC agentic checkout section 2.1 and 6; ``openapi.agentic_checkout.yaml``
``components.parameters``):

* ``API-Version`` is required on every call. Missing is 400 ``missing_api_version``,
  a value this server does not speak is 400 ``unsupported_api_version``; both carry
  ``supported_versions``.
* ``Idempotency-Key`` is required on every POST (1 to 255 characters): missing is
  400 ``idempotency_key_required``. Replays, conflicts (422 ``idempotency_conflict``)
  and requests in flight (409 ``idempotency_in_flight`` with ``Retry-After``) are
  decided against the stored record (``core.commerce.ucp.idempotency``); the body is
  compared as canonical JSON (key order ignored, array order and ``null`` kept).
* ``Request-Id`` is echoed (one is generated when absent).
* ``Signature`` and ``Timestamp`` are RECOMMENDED. The signing algorithm, the
  canonical form, whether ``Timestamp`` is signed and how keys are distributed are
  arranged out of band and not published (UNCONFIRMED in the spec). Verification is
  therefore a verifier registered per agent platform (``register_signature_verifier``);
  none ships. ``AGENTICORG_ACP_SIGNATURE_MODE=log`` (default) records the outcome and
  never refuses; ``enforce`` refuses (401 ``invalid_signature``) anything a registered
  verifier does not accept, including a platform with no verifier, a missing
  signature and a ``Timestamp`` outside 300 seconds.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from core.commerce.acp import SUPPORTED_API_VERSIONS
from core.commerce.acp.errors import acp_error

logger = structlog.get_logger()

MAX_IDEMPOTENCY_KEY_LENGTH = 255
MAX_REQUEST_ID_LENGTH = 128
SIGNATURE_SKEW_SECONDS = 300
SIGNATURE_MODES = ("log", "enforce")


def api_version_error(value: str | None) -> tuple[int, dict[str, Any]] | None:
    supported = list(SUPPORTED_API_VERSIONS)
    if not value or not value.strip():
        return 400, acp_error(
            "invalid_request",
            "missing_api_version",
            "The API-Version header is required.",
            supported_versions=supported,
        )
    if value.strip() not in SUPPORTED_API_VERSIONS:
        return 400, acp_error(
            "invalid_request",
            "unsupported_api_version",
            f"API version {value.strip()[:40]} is not supported.",
            supported_versions=supported,
        )
    return None


def idempotency_key_error(value: str | None) -> tuple[int, dict[str, Any]] | None:
    """Every ACP POST needs ``Idempotency-Key`` (1 to 255 printable characters)."""
    if value is None or not value:
        return 400, acp_error(
            "invalid_request",
            "idempotency_key_required",
            "The Idempotency-Key header is required on POST requests.",
        )
    if len(value) > MAX_IDEMPOTENCY_KEY_LENGTH or not value.isprintable():
        return 400, acp_error(
            "invalid_request",
            "invalid",
            "Idempotency-Key must be 1 to 255 printable characters.",
            param="Idempotency-Key",
        )
    return None


def request_id(value: str | None) -> str:
    """The caller's ``Request-Id`` (bounded, printable) or a new one."""
    if value and value.isprintable():
        return value[:MAX_REQUEST_ID_LENGTH]
    return f"req_{uuid.uuid4().hex}"


def canonical_body(raw: bytes) -> bytes:
    """Semantic JSON form for idempotency comparison: keys sorted, array order and ``null`` kept."""
    try:
        document = json.loads(raw) if raw else {}
    except ValueError:
        return raw
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --- request signatures ---------------------------------------------------------------------------


class SignatureVerifier(Protocol):
    """Verifies one platform's ``Signature`` over a request, by the scheme agreed with that platform."""

    def verify(
        self, *, method: str, path: str, body: bytes, signature: str, timestamp: str | None
    ) -> bool: ...  # pragma: no cover - protocol


# enterprise-gate: process-local-ok reason=import-time-verifier-registry-identical-on-every-replica
_VERIFIERS: dict[str, SignatureVerifier] = {}


def register_signature_verifier(platform: str, verifier: SignatureVerifier) -> None:
    """Accept ``Signature`` from ``platform`` (``acp:api_key:<key id>``) through ``verifier``."""
    _VERIFIERS[platform] = verifier


def unregister_signature_verifier(platform: str) -> None:
    _VERIFIERS.pop(platform, None)


@dataclass(frozen=True)
class SignatureCheck:
    """``outcome``: verified, absent, no_verifier, stale_timestamp, invalid; ``refuse`` in enforce mode."""

    outcome: str
    refuse: bool


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def check_signature(
    *,
    platform: str | None,
    method: str,
    path: str,
    body: bytes,
    headers: Mapping[str, str],
    mode: str,
    now: datetime | None = None,
) -> SignatureCheck:
    """Verify ``Signature``/``Timestamp`` with the platform's verifier; refuse only in ``enforce`` mode."""
    lowered = {str(k).lower(): str(val) for k, val in headers.items()}
    signature = lowered.get("signature", "").strip()
    timestamp = lowered.get("timestamp")
    moment = now or datetime.now(UTC)
    verifier = _VERIFIERS.get(platform or "")
    if not signature:
        outcome = "absent"
    elif verifier is None:
        outcome = "no_verifier"
    else:
        signed_at = _parse_timestamp(timestamp)
        if signed_at is None or abs((moment - signed_at).total_seconds()) > SIGNATURE_SKEW_SECONDS:
            outcome = "stale_timestamp"
        else:
            try:
                accepted = verifier.verify(
                    method=method.upper(), path=path, body=body, signature=signature, timestamp=timestamp
                )
            # enterprise-gate: broad-except-ok reason=verifier-failure-is-an-unverified-signature
            except Exception as exc:
                logger.warning("acp_signature_verifier_failed", error_type=type(exc).__name__)
                accepted = False
            outcome = "verified" if accepted else "invalid"
    enforce = mode == "enforce"
    if outcome != "verified":
        logger.info("acp_signature_unverified", outcome=outcome, mode="enforce" if enforce else "log")
    return SignatureCheck(outcome=outcome, refuse=enforce and outcome != "verified")


def signature_error() -> tuple[int, dict[str, Any]]:
    return 401, acp_error("invalid_request", "invalid_signature", "The request signature could not be verified.")
