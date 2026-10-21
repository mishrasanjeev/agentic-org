# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Mandate-capability evidence for the Grantex C6Z authority request (``oacp.mandate_capability_evidence.v1``).

Grantex binds this object into the signed ``mandate_capability`` artifact: with
it (and ``result_status: available``) the artifact says
``mandate_capability_status: verified``; without it ``unverified``, and the
UCP profile withholds checkout. The object is closed and holds no secrets.

Built from two things AgenticOrg already has, never from a request body:

* the Plural capability verifier result
  (``core.commerce.c6z_runtime_vertical.verify_plural_pine_mandate_capability``,
  stored as ``commerce_c6z_provider_capability_evidence``): evidence id,
  result status, ``checked_at`` (sent as ``verified_at``), ``expires_at``,
  environment and the redacted reference;
* the merchant's ``provider_policy.ucp.mandate_capability``: which rails and
  caps the merchant offers on Plural.

Rails (the Grantex rail names): ``plural`` (the Plural tokenised instrument),
``card_network_token`` when card instruments are offered (tokens only, a PAN is
never accepted), ``upi_autopay`` when a complete UPI AutoPay block is
configured (``core.commerce.india_rails``). Caps are the handler allowance per
rail (the UPI AutoPay mandate limit for ``upi_autopay``). ``evidence_hash`` is
the lowercase hex SHA-256 of the RFC 8785 form of the redacted verifier result
the object is built from. ``build`` returns None (send nothing, fail closed)
when the result is not usable: another provider, an environment other than
sandbox or production, an expired or more than 6 hours old result, or no cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from core.commerce.oacp_jws import oacp_payload_hash

SCHEMA_VERSION = "oacp.mandate_capability_evidence.v1"
MAX_AGE = timedelta(hours=6)
RESULT_STATUSES = frozenset({"available", "unavailable", "blocked_missing_credentials", "blocked_provider_error"})
TOKEN_TYPES_BY_RAIL: dict[str, str] = {
    "plural": "plural_pine_token",
    "card_network_token": "network_token",
    "upi_autopay": "upi_mandate_token",
}


def _iso(value: Any) -> str | None:
    """RFC 3339 UTC with milliseconds (what every verifier parses), or None."""
    from datetime import UTC

    moment = value if isinstance(value, datetime) else _parse(value if isinstance(value, str) else None)
    if moment is None or moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    from core.commerce.arbiter import parse_time

    return parse_time(value)


def build(
    verifier_result: Any,
    *,
    tenant_id: str,
    merchant_id: str,
    mandate_capability: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    """The evidence object for the authority request, or None when there is nothing usable to send."""
    from core.commerce.india_rails import INSTRUMENT_UPI_AUTOPAY, parse_upi_autopay_capability

    def get(name: str) -> Any:
        if isinstance(verifier_result, Mapping):
            return verifier_result.get(name)
        return getattr(verifier_result, name, None)

    if verifier_result is None or get("provider") != "plural_pine":
        return None
    environment = str(get("provider_environment") or "")
    status = str(get("result_status") or "")
    verified_at, expires_at = _iso(get("checked_at")), _iso(get("expires_at"))
    verified, expires = _parse(verified_at), _parse(expires_at)
    if environment not in ("sandbox", "production") or status not in RESULT_STATUSES:
        return None
    if verified is None or expires is None or verified > now + timedelta(minutes=1):
        return None
    if now - verified > MAX_AGE or expires <= now or expires <= verified:
        return None
    capability = mandate_capability if isinstance(mandate_capability, Mapping) else {}
    max_amount = capability.get("max_amount_minor_units")
    currency = str(capability.get("currency") or "").upper()
    if isinstance(max_amount, bool) or not isinstance(max_amount, int) or max_amount < 0 or len(currency) != 3:
        return None
    types = {str(t) for t in capability.get("instrument_types") or () if t}
    caps = [{"rail": "plural", "max_amount": max_amount, "currency": currency}]
    if "card" in types:
        caps.append({"rail": "card_network_token", "max_amount": max_amount, "currency": currency})
    upi = parse_upi_autopay_capability(capability.get("upi_autopay"))
    if upi is not None and INSTRUMENT_UPI_AUTOPAY in types:
        caps.append({"rail": "upi_autopay", "max_amount": upi.max_amount_minor_units, "currency": "INR"})
    rails = [str(cap["rail"]) for cap in caps]
    redacted = {
        "evidence_id": get("evidence_id"),
        "provider": "plural_pine",
        "capability_type": get("capability_type"),
        "result_status": status,
        "checked_at": verified_at,
        "expires_at": expires_at,
        "redacted_evidence_ref": get("redacted_evidence_ref"),
        "provider_environment": environment,
        "external_validation_performed": bool(get("external_validation_performed")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": str(get("evidence_id") or ""),
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "provider": "plural_pine",
        "provider_environment": environment,
        "result_status": status,
        "external_validation_performed": bool(get("external_validation_performed")),
        "rails": rails,
        "caps": caps,
        "token_types": [TOKEN_TYPES_BY_RAIL[rail] for rail in rails],
        "verified_at": verified_at,
        "expires_at": expires_at,
        "redacted_evidence_ref": str(get("redacted_evidence_ref") or ""),
        "evidence_hash": oacp_payload_hash(redacted),
    }
