# SPDX-License-Identifier: Apache-2.0
"""Request Intent, Cart and Payment mandates from Grantex for one checkout (cross-repo contract section 8).

AgenticOrg asks; Grantex issues. ``request_mandate_chain`` makes three calls
in order with the AgenticOrg service token (each route has its own scope on
the Grantex side):

1. ``POST /v1/commerce/oacp/mandates/intent`` with the checkout Commerce
   Passport the buyer platform presented (Grantex verifies it in
   payment-affecting mode and the consent behind it), the natural-language
   description of what the buyer delegated, the amount cap and the presence.
2. ``POST /v1/commerce/oacp/mandates/cart`` with the intent mandate and the
   checkout object whose RFC 8785 SHA-256 is the AP2 ``checkout_hash``. A
   ``human_present`` cart carries the buyer's WebAuthn assertion over that
   hash, passed through unchanged from the platform: AgenticOrg never creates
   or alters one.
3. ``POST /v1/commerce/oacp/mandates/payment`` with the cart mandate, the rail
   and the token binding (a digest reference of the payment token, never the
   token).

Transport: ``core.commerce.grantex_service.GrantexServiceClient`` (async
httpx over the DNS-pinning egress transport, async egress validation, 5 s
timeout, no redirect, no retry). A Grantex refusal raises
``MandateRequestRefusedError`` with Grantex's refusal code (for example
``passport_cap_exceeded``, ``consent_not_active``, ``passkey_evidence_required``);
an unreachable Grantex raises ``MandateServiceUnavailableError``. Nothing
waits for a person; nothing is retried.

What comes back is not trusted as such: the caller verifies the three tokens
locally (``core.commerce.oacp_mandates.verify_mandate_chain``) before the
arbiter may read them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from core.commerce.grantex_service import GrantexServiceClient, GrantexServiceUnavailableError

logger = structlog.get_logger()

INTENT_PATH = "/v1/commerce/oacp/mandates/intent"
CART_PATH = "/v1/commerce/oacp/mandates/cart"
PAYMENT_PATH = "/v1/commerce/oacp/mandates/payment"
_MAX_PASSPORT_CHARS = 8192
_MAX_DESCRIPTION_CHARS = 4000
_MAX_ASSERTION_MEMBER_CHARS = 16384


class MandateRequestError(ValueError):
    """The mandate request cannot be made from these inputs (a protocol error for the caller)."""


class MandateRequestRefusedError(RuntimeError):
    """Grantex refused to issue a mandate; ``code`` is its refusal code, ``mandate_type`` the step."""

    def __init__(self, mandate_type: str, code: str, http_status: int, refusal_record_id: str | None) -> None:
        super().__init__(f"Grantex refused the {mandate_type} mandate: {code}")
        self.mandate_type = mandate_type
        self.code = code
        self.http_status = http_status
        self.refusal_record_id = refusal_record_id


class MandateServiceUnavailableError(RuntimeError):
    """Grantex could not be reached or answered with something unusable."""


@dataclass(frozen=True)
class MandateChainRequest:
    """Everything one chain needs. ``tenant_id`` is the Grantex commerce tenant of the merchant."""

    tenant_id: str
    passport_jwt: str
    description: str
    max_amount_minor_units: int
    currency: str
    presence: str
    checkout: Mapping[str, Any]
    rail: str
    token_binding: Mapping[str, str]
    webauthn_assertion: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class IssuedMandate:
    mandate_type: str
    mandate: str
    jti: str
    exp: int | None


def validate_webauthn_assertion(value: Any) -> dict[str, Any]:
    """Shape check of a platform-supplied WebAuthn ``AuthenticationResponseJSON`` (it is verified by Grantex)."""
    if not isinstance(value, Mapping):
        raise MandateRequestError("webauthn_assertion must be a WebAuthn AuthenticationResponseJSON object")
    response = value.get("response")
    if not isinstance(response, Mapping):
        raise MandateRequestError("webauthn_assertion.response must be an object")
    for name in ("clientDataJSON", "authenticatorData", "signature"):
        member = response.get(name)
        if not isinstance(member, str) or not 1 <= len(member) <= _MAX_ASSERTION_MEMBER_CHARS:
            raise MandateRequestError(f"webauthn_assertion.response.{name} must be a base64url string")
    for name in ("id", "rawId"):
        member = value.get(name)
        if not isinstance(member, str) or not 1 <= len(member) <= 1024:
            raise MandateRequestError(f"webauthn_assertion.{name} must be a string")
    if value.get("type") != "public-key":
        raise MandateRequestError("webauthn_assertion.type must be public-key")
    return dict(value)


def _check(request: MandateChainRequest) -> None:
    if not isinstance(request.passport_jwt, str) or not 0 < len(request.passport_jwt) <= _MAX_PASSPORT_CHARS:
        raise MandateRequestError("passport_jwt must be a compact JWT")
    if not request.description.strip() or len(request.description) > _MAX_DESCRIPTION_CHARS:
        raise MandateRequestError("description must be 1 to 4000 characters")
    if request.presence not in ("human_present", "human_not_present"):
        raise MandateRequestError("presence must be human_present or human_not_present")
    if request.presence == "human_present" and request.webauthn_assertion is None:
        raise MandateRequestError("a human_present purchase needs the buyer's webauthn_assertion")
    if request.presence == "human_not_present" and request.webauthn_assertion is not None:
        raise MandateRequestError("a webauthn_assertion is accepted only for a human_present purchase")


async def _issue(client: GrantexServiceClient, mandate_type: str, path: str, body: Mapping[str, Any]) -> IssuedMandate:
    try:
        response = await client.post_json(path, body)
    except GrantexServiceUnavailableError as exc:
        logger.warning("oacp_mandate_request_unavailable", mandate_type=mandate_type, reason=str(exc))
        raise MandateServiceUnavailableError(str(exc)) from exc
    answer = response.body
    if response.status_code != 201:
        error = answer.get("error") if isinstance(answer.get("error"), Mapping) else answer
        code = str(error.get("code") or f"grantex_http_{response.status_code}")[:64]
        details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
        record = details.get("refusal_record_id") if isinstance(details, Mapping) else None
        logger.info("oacp_mandate_refused", mandate_type=mandate_type, code=code, http_status=response.status_code)
        if response.status_code >= 500:
            raise MandateServiceUnavailableError(f"Grantex answered HTTP {response.status_code} ({code})")
        raise MandateRequestRefusedError(
            mandate_type, code, response.status_code, str(record)[:80] if isinstance(record, str) else None
        )
    mandate = answer.get("mandate")
    jti = answer.get("jti")
    if (
        not isinstance(mandate, str)
        or not isinstance(jti, str)
        or answer.get("mandate_type") not in (None, mandate_type)
    ):
        raise MandateServiceUnavailableError(f"Grantex answered the {mandate_type} request without a mandate")
    exp = answer.get("exp")
    return IssuedMandate(
        mandate_type=mandate_type,
        mandate=mandate,
        jti=jti,
        exp=exp if isinstance(exp, int) and not isinstance(exp, bool) else None,
    )


async def request_mandate_chain(
    request: MandateChainRequest, *, client: GrantexServiceClient
) -> dict[str, IssuedMandate]:
    """Intent, then Cart under it, then Payment under the cart; returns the three issued mandates by type."""
    _check(request)
    common = {"tenant_id": request.tenant_id, "passport_jwt": request.passport_jwt}
    intent = await _issue(
        client,
        "intent",
        INTENT_PATH,
        {
            **common,
            "description": request.description,
            "max_amount": {"value": request.max_amount_minor_units, "currency": request.currency.upper()},
            "presence": request.presence,
        },
    )
    cart_body: dict[str, Any] = {
        **common,
        "intent_mandate": intent.mandate,
        "checkout": dict(request.checkout),
        "presence": request.presence,
    }
    if request.webauthn_assertion is not None:
        cart_body["webauthn_assertion"] = dict(request.webauthn_assertion)
    cart = await _issue(client, "cart", CART_PATH, cart_body)
    payment = await _issue(
        client,
        "payment",
        PAYMENT_PATH,
        {**common, "cart_mandate": cart.mandate, "rail": request.rail, "token_binding": dict(request.token_binding)},
    )
    return {"intent": intent, "cart": cart, "payment": payment}
