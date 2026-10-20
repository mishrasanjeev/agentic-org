# SPDX-License-Identifier: Apache-2.0
"""What the commerce arbiter decides on when a UCP checkout is completed.

``session_complete`` decisions bind ``completion_payload``: the session id,
revision, merchant and seller, release, currency, totals, line items, the
selected fulfillment, and each payment instrument's handler, type and
credential SHA-256 (never the credential). Any change to the session or a
different credential needs a new decision.

Buyer authority (mandate, consent and Commerce Passport references) is
caller-asserted until the mandate work issues verified mandates: it comes from
the vendor member ``ai.agenticorg.commerce.buyer_authority`` of the complete
(or create/update) body, and with the AP2 extension active the
``ap2.checkout_mandate`` stands in for the mandate reference (its SHA-256 is
the hash; its ``exp`` is read without verification and sealed as asserted).
Every value is hashed into the decision and the ledger, never treated as a
credential. A missing value refuses with the rule that needed it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from core.commerce.arbiter import (
    AfaEvidence,
    ArbiterContext,
    ConsentEvidence,
    MandateEvidence,
    PassportEvidence,
    artifact_evidence_from_records,
    merchant_limits_from_config,
    parse_time,
)
from core.commerce.ledger import json_safe
from core.commerce.ucp.merchant import MerchantUcp

VENDOR_MEMBER = "ai.agenticorg.commerce"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
CHECKOUT_MANDATE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+(~[A-Za-z0-9_-]+)*$")


class AuthorityRequestError(ValueError):
    """A malformed buyer authority object (protocol error, 400)."""


def parse_buyer_authority(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise AuthorityRequestError("buyer_authority must be an object")
    allowed = {
        "mandate_ref": 200,
        "mandate_hash": 64,
        "mandate_expires_at": 40,
        "consent_ref": 200,
        "consent_status": 40,
        "passport_ref": 200,
        "passport_max_amount_minor_units": 0,
        "passport_currency": 3,
        "passport_expires_at": 40,
    }
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            raise AuthorityRequestError(f"buyer_authority.{key} is not a known field")
        if value is None:
            continue
        if key == "passport_max_amount_minor_units":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AuthorityRequestError("passport_max_amount_minor_units must be a non-negative integer")
            out[key] = value
            continue
        if not isinstance(value, str) or len(value) > allowed[key]:
            raise AuthorityRequestError(f"buyer_authority.{key} must be a string of at most {allowed[key]} characters")
        if key == "mandate_hash" and not _HEX64.match(value):
            raise AuthorityRequestError("mandate_hash must be lowercase hex SHA-256")
        if key.endswith("_expires_at") and parse_time(value) is None:
            raise AuthorityRequestError(f"{key} must be an RFC 3339 time with an offset")
        out[key] = value
    return out


def vendor_member(body: Mapping[str, Any]) -> dict[str, Any]:
    raw = body.get(VENDOR_MEMBER)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise AuthorityRequestError(f"{VENDOR_MEMBER} must be an object")
    decision_id = raw.get("decision_id")
    if decision_id is not None and (not isinstance(decision_id, str) or not 1 <= len(decision_id) <= 80):
        raise AuthorityRequestError("decision_id must be a string of at most 80 characters")
    decide_only = raw.get("decide_only", False)
    if not isinstance(decide_only, bool):
        raise AuthorityRequestError("decide_only must be a boolean")
    return {
        "decision_id": decision_id,
        "decide_only": decide_only,
        "buyer_authority": parse_buyer_authority(raw.get("buyer_authority")),
    }


def ap2_mandate_evidence(checkout_mandate: str) -> dict[str, Any]:
    """Reference, SHA-256 and the asserted ``exp`` of an AP2 checkout mandate (not verified here)."""
    digest = hashlib.sha256(checkout_mandate.encode("ascii")).hexdigest()
    expires_at = None
    try:
        payload_segment = checkout_mandate.split("~", 1)[0].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4)))
        exp = claims.get("exp") if isinstance(claims, Mapping) else None
        if isinstance(exp, int) and not isinstance(exp, bool):
            expires_at = datetime.fromtimestamp(exp, tz=UTC).isoformat()
    except (IndexError, ValueError, OverflowError, OSError):
        expires_at = None
    return {
        "mandate_ref": f"ap2:checkout_mandate:{digest[:32]}",
        "mandate_hash": digest,
        "mandate_expires_at": expires_at,
    }


def completion_payload(
    *,
    session_id: str,
    revision: int,
    merchant: MerchantUcp,
    version: str,
    state: Mapping[str, Any],
    instruments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The binding payload of a ``session_complete`` decision (JSON-safe; no credential values)."""
    fulfillment = [
        {
            "method_id": m.get("id"),
            "type": m.get("type"),
            "destination": m.get("selected_destination_id"),
            "options": [g.get("selected_option_id") for g in m.get("groups") or []],
        }
        for m in (state.get("fulfillment") or {}).get("methods") or []
    ]
    return json_safe(
        {
            "action": "session_complete",
            "session_id": session_id,
            "revision": revision,
            "merchant_id": merchant.merchant_id,
            "seller_agent_id": merchant.seller_agent_id,
            "ucp_version": version,
            "currency": state.get("currency"),
            "totals": state.get("totals"),
            "line_items": [
                {"id": li["id"], "item_id": li["item"]["id"], "quantity": li["quantity"], "price": li["item"]["price"]}
                for li in state.get("line_items") or []
            ],
            "fulfillment": fulfillment,
            "payment": [
                {
                    "id": i.get("id"),
                    "handler_id": i.get("handler_id"),
                    "type": i.get("type"),
                    "credential_sha256": i.get("credential_sha256"),
                    "amount": i.get("amount"),
                }
                for i in instruments
            ],
        }
    )


def arbiter_context(
    *,
    merchant: MerchantUcp,
    now: datetime,
    payload: Mapping[str, Any],
    amount_minor_units: int,
    currency: str | None,
    quantity: int,
    authority: Mapping[str, Any] | None,
    cache_records: Sequence[Any],
    merchant_config: Mapping[str, Any] | None,
    afa: AfaEvidence | None = None,
    mandate_hashes: Mapping[str, str | None] | None = None,
) -> ArbiterContext:
    a = authority or {}
    has_mandate = bool(a.get("mandate_ref") or a.get("mandate_hash"))
    has_consent = bool(a.get("consent_ref") or a.get("consent_status"))
    has_passport = bool(a.get("passport_ref"))
    return ArbiterContext(
        tenant_id=merchant.tenant_id,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=None,
        now=now,
        payload=payload,
        amount_minor_units=amount_minor_units,
        currency=currency,
        quantity=quantity,
        mandate=MandateEvidence(
            ref=a.get("mandate_ref"), hash=a.get("mandate_hash"), expires_at=parse_time(a.get("mandate_expires_at"))
        )
        if has_mandate
        else None,
        consent=ConsentEvidence(ref=a.get("consent_ref"), status=a.get("consent_status")) if has_consent else None,
        passport=PassportEvidence(
            ref=a.get("passport_ref"),
            max_amount_minor_units=a.get("passport_max_amount_minor_units"),
            currency=a.get("passport_currency"),
            expires_at=parse_time(a.get("passport_expires_at")),
        )
        if has_passport
        else None,
        artifacts=artifact_evidence_from_records(cache_records),
        merchant_limits=merchant_limits_from_config(merchant_config),
        afa=afa,
        mandate_hashes=mandate_hashes,
    )
