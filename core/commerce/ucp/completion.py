# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""What the commerce arbiter decides on when a UCP checkout is completed.

``session_complete`` decisions bind ``completion_payload``: the session id,
revision, merchant and seller, release, currency, totals, line items, the
selected fulfillment, and each payment instrument's handler, type and
credential SHA-256 (never the credential). Any change to the session or a
different credential needs a new decision.

Buyer authority comes from one of two places:

- Server-verified mandates (cross-repo contract section 8). The Complete body's
  vendor member ``ai.agenticorg.commerce.mandate_request`` carries the
  checkout Commerce Passport, the presence, the delegated description, an
  optional amount cap and, for ``human_present``, the buyer's WebAuthn
  assertion over ``checkout_hash``. The server asks Grantex for the Intent,
  Cart and Payment mandates (``core.commerce.mandates_client``), verifies them
  locally (``core.commerce.oacp_mandates.verify_mandate_chain``) and only then
  builds the arbiter's mandate, consent and passport evidence from the
  verified claims, with the server-side ``authority_verification_ref``.
- Caller-asserted references (``ai.agenticorg.commerce.buyer_authority``,
  or the AP2 ``ap2.checkout_mandate`` whose SHA-256 stands in for the mandate
  hash). These never carry a verification ref, so the strict runtime refuses
  them at ``buyer_authority_verified``.

The mandate checkout object (``mandate_checkout``) is derived from the session
by the server, and its RFC 8785 SHA-256 is the ``checkout_hash`` the cart
mandate and the buyer's passkey assertion bind. The decision's buyer agent is
the authenticated platform (``platform_buyer_agent_id`` of the ``UCP-Agent``
profile recorded on the session at create time).
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
from core.commerce.oacp_mandates import VerifiedMandateChain
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
        "mandate_request": parse_mandate_request(raw.get("mandate_request")),
    }


def parse_mandate_request(raw: Any) -> dict[str, Any] | None:
    """The platform's inputs for server-requested mandates (verified by Grantex, then locally)."""
    from core.commerce.mandates_client import MandateRequestError, validate_webauthn_assertion

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise AuthorityRequestError("mandate_request must be an object")
    unknown = set(raw) - {"passport_jwt", "presence", "description", "max_amount_minor_units", "webauthn_assertion"}
    if unknown:
        raise AuthorityRequestError(f"mandate_request.{sorted(unknown)[0]} is not a known field")
    passport = raw.get("passport_jwt")
    if not isinstance(passport, str) or not 0 < len(passport) <= 8192 or passport.count(".") != 2:
        raise AuthorityRequestError("mandate_request.passport_jwt must be a compact JWT")
    presence = raw.get("presence")
    if presence not in ("human_present", "human_not_present"):
        raise AuthorityRequestError("mandate_request.presence must be human_present or human_not_present")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 4000:
        raise AuthorityRequestError("mandate_request.description must be 1 to 4000 characters")
    cap = raw.get("max_amount_minor_units")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 0):
        raise AuthorityRequestError("mandate_request.max_amount_minor_units must be a non-negative integer")
    assertion = raw.get("webauthn_assertion")
    if presence == "human_present" and assertion is None:
        raise AuthorityRequestError("a human_present mandate_request needs the buyer's webauthn_assertion")
    if presence == "human_not_present" and assertion is not None:
        raise AuthorityRequestError("webauthn_assertion is accepted only for a human_present mandate_request")
    try:
        checked = validate_webauthn_assertion(assertion) if assertion is not None else None
    except MandateRequestError as exc:
        raise AuthorityRequestError(f"mandate_request.{exc}") from exc
    return {
        "passport_jwt": passport,
        "presence": presence,
        "description": description,
        "max_amount_minor_units": cap,
        "webauthn_assertion": checked,
    }


def platform_buyer_agent_id(profile_url: str | None) -> str | None:
    """The buyer agent id of an authenticated UCP platform: a digest of its ``UCP-Agent`` profile URL.

    Grantex binds a countersigned decision to this id (``sub``); a profile URL
    has characters its id grammar refuses, so the id is ``ucp_platform:`` and
    the first 40 hex characters of the URL's SHA-256.
    """
    if not isinstance(profile_url, str) or not profile_url:
        return None
    return "ucp_platform:" + hashlib.sha256(profile_url.encode("utf-8")).hexdigest()[:40]


def session_buyer_agent_id(row: Any) -> str | None:
    """The buyer agent recorded on a checkout session at create time (or derived from its platform profile)."""
    state = getattr(row, "state", None) or {}
    platform = state.get("platform") if isinstance(state, Mapping) else None
    recorded = platform.get("buyer_agent_id") if isinstance(platform, Mapping) else None
    if isinstance(recorded, str) and recorded:
        return recorded
    return platform_buyer_agent_id(getattr(row, "platform_profile_url", None))


def mandate_checkout(
    *, session_id: str, revision: int, state: Mapping[str, Any], grantex_merchant_id: str
) -> dict[str, Any]:
    """The checkout object a cart mandate binds (its RFC 8785 SHA-256 is ``checkout_hash``). No credentials."""
    total = int(next(t["amount"] for t in state["totals"] if t["type"] == "total"))
    return json_safe(
        {
            "id": session_id,
            "revision": revision,
            "currency": state.get("currency"),
            "merchant": {"id": grantex_merchant_id},
            "line_items": [
                {
                    "id": li["id"],
                    "item": {"id": li["item"]["id"], "price": li["item"]["price"]},
                    "quantity": li["quantity"],
                }
                for li in state.get("line_items") or []
            ],
            "totals": [{"type": "total", "amount": total}],
        }
    )


def token_binding_for(credential_sha256: str | None, token_type: str) -> dict[str, str]:
    """The payment mandate's token binding: the token type and a digest reference of the credential."""
    return {"type": token_type, "id": f"sha256:{credential_sha256 or ''}"[:256]}


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
    buyer_agent_id: str | None = None,
    verified_mandates: VerifiedMandateChain | None = None,
    require_verified_authority: bool = False,
) -> ArbiterContext:
    """The arbiter's inputs. With a valid ``verified_mandates`` chain the buyer authority comes from it alone."""
    chain = verified_mandates if verified_mandates is not None and verified_mandates.valid else None
    if chain is not None:
        return _verified_context(
            merchant=merchant,
            now=now,
            payload=payload,
            amount_minor_units=amount_minor_units,
            currency=currency,
            quantity=quantity,
            cache_records=cache_records,
            merchant_config=merchant_config,
            afa=afa,
            buyer_agent_id=buyer_agent_id,
            chain=chain,
            require_verified_authority=require_verified_authority,
        )
    a = authority or {}
    has_mandate = bool(a.get("mandate_ref") or a.get("mandate_hash"))
    has_consent = bool(a.get("consent_ref") or a.get("consent_status"))
    has_passport = bool(a.get("passport_ref"))
    return ArbiterContext(
        tenant_id=merchant.tenant_id,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=buyer_agent_id,
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
        require_verified_authority=require_verified_authority,
    )


def _verified_context(
    *,
    merchant: MerchantUcp,
    now: datetime,
    payload: Mapping[str, Any],
    amount_minor_units: int,
    currency: str | None,
    quantity: int,
    cache_records: Sequence[Any],
    merchant_config: Mapping[str, Any] | None,
    afa: AfaEvidence | None,
    buyer_agent_id: str | None,
    chain: VerifiedMandateChain,
    require_verified_authority: bool,
) -> ArbiterContext:
    """Buyer authority from a verified mandate chain.

    - mandate: the payment mandate (jti, ``mandate_hash``), expiring with the
      earliest mandate of the chain;
    - consent: the chain's ``consent_ref``, active (Grantex issued every
      mandate only under granted consent, and the status list says VALID);
    - passport: the chain's ``passport_ref`` with the intent mandate cap as its
      cap (Grantex refuses an intent above the passport cap, so it is the
      tighter bound) and the chain expiry (no mandate outlives its passport).
    """
    return ArbiterContext(
        tenant_id=merchant.tenant_id,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        now=now,
        payload=payload,
        amount_minor_units=amount_minor_units,
        currency=currency,
        quantity=quantity,
        mandate=MandateEvidence(ref=chain.jtis["payment"], hash=chain.hashes["payment"], expires_at=chain.expires_at),
        consent=ConsentEvidence(ref=chain.consent_ref, status="active"),
        passport=PassportEvidence(
            ref=chain.passport_ref,
            max_amount_minor_units=chain.intent_max_amount_minor_units,
            currency=chain.intent_currency,
            expires_at=chain.expires_at,
        ),
        artifacts=artifact_evidence_from_records(cache_records),
        merchant_limits=merchant_limits_from_config(merchant_config),
        afa=afa,
        authority_verification_ref=chain.verification_ref,
        require_verified_authority=require_verified_authority,
        mandate_hashes=dict(chain.hashes),
        mandate_jtis=dict(chain.jtis),
        presence=chain.presence,
        intent_max_amount_minor_units=chain.intent_max_amount_minor_units,
        intent_currency=chain.intent_currency,
    )
