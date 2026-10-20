# SPDX-License-Identifier: Apache-2.0
"""Scheme token binding on the payment capability (G52): ``{scheme, token_ref, agent_id, merchant_scope, limits}``.

The Python mirror of the Grantex shared type (``@grantex/sdk``
``src/oacp/token-binding.ts`` and the auth service's
``lib/commerce/scheme-token-binding.ts``): the same members, the same parse
rules and the same ``token_binding_invalid`` refusals. A ``mandate_capability``
OACP artifact may carry it as ``token_binding``; ``validate_oacp_artifact_family``
refuses a malformed one or one that does not sit inside the capability.

    scheme          ``visa`` | ``mastercard`` (network tokens), ``upi_autopay``,
                    ``provider_token`` (a payment provider's tokenised instrument,
                    held behind the provider-neutral interface)
    token_ref       an opaque reference to the token the scheme or provider holds:
                    1 to 128 characters from A-Z a-z 0-9 . _ : -, never a primary
                    account number (a run of 13 to 19 digits is refused) and never
                    the token itself
    agent_id        the buyer agent the token is bound to
    merchant_scope  the merchant ids it may be used with (1 to 32)
    limits          {max_amount (integer minor units), currency (ISO 4217),
                     expires_at? (RFC 3339)}

AgenticOrg describes and checks the binding; it never executes it. The
executor receives it on ``ExecutionCheckout.scheme_token_binding``
(``core/commerce/executors``); the rail's executor (the Plural executor of the
payment-rail work) presents ``token_ref`` to the scheme or provider that holds
the token.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

TOKEN_BINDING_SCHEMES = ("visa", "mastercard", "upi_autopay", "provider_token")
MAX_TOKEN_BINDING_MERCHANTS = 32
TOKEN_BINDING_INVALID = "token_binding_invalid"

TokenBindingScheme = Literal["visa", "mastercard", "upi_autopay", "provider_token"]

_TOKEN_REF = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# A run of 13 to 19 digits anywhere: a primary account number is refused, however it is wrapped.
_PAN_LIKE = re.compile(r"\d{13,19}")
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
_MAX_SAFE_INTEGER = 2**53 - 1


class TokenBindingError(ValueError):
    code = TOKEN_BINDING_INVALID


@dataclass(frozen=True)
class TokenBindingLimits:
    max_amount: int
    currency: str
    expires_at: str | None = None


@dataclass(frozen=True)
class SchemeTokenBinding:
    scheme: TokenBindingScheme
    token_ref: str
    agent_id: str
    merchant_scope: tuple[str, ...]
    limits: TokenBindingLimits

    def to_dict(self) -> dict[str, Any]:
        limits: dict[str, Any] = {"max_amount": self.limits.max_amount, "currency": self.limits.currency}
        if self.limits.expires_at is not None:
            limits["expires_at"] = self.limits.expires_at
        return {
            "scheme": self.scheme,
            "token_ref": self.token_ref,
            "agent_id": self.agent_id,
            "merchant_scope": list(self.merchant_scope),
            "limits": limits,
        }


def _fail(message: str) -> TokenBindingError:
    return TokenBindingError(message)


def _exactly(value: Mapping[str, Any], required: tuple[str, ...], optional: tuple[str, ...], what: str) -> None:
    for name in value:
        if name not in required and name not in optional:
            raise _fail(f"{what} has no member {name}")
    for name in required:
        if name not in value:
            raise _fail(f"{what}.{name} is required")


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_scheme_token_binding(value: Any) -> SchemeTokenBinding:
    """Parse a token binding; every failure is a ``TokenBindingError`` (``token_binding_invalid``)."""
    if not isinstance(value, Mapping):
        raise _fail("token_binding must be an object")
    _exactly(value, ("scheme", "token_ref", "agent_id", "merchant_scope", "limits"), (), "token_binding")
    scheme, token_ref, agent_id = value["scheme"], value["token_ref"], value["agent_id"]
    scope, limits = value["merchant_scope"], value["limits"]
    if not isinstance(scheme, str) or scheme not in TOKEN_BINDING_SCHEMES:
        raise _fail(f"token_binding.scheme must be one of {', '.join(TOKEN_BINDING_SCHEMES)}")
    if not isinstance(token_ref, str) or not _TOKEN_REF.fullmatch(token_ref):
        raise _fail(
            "token_binding.token_ref must be an opaque reference of 1 to 128 characters from A-Z a-z 0-9 . _ : -"
        )
    if _PAN_LIKE.search(token_ref):
        raise _fail("token_binding.token_ref must be a reference, never an account number")
    if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
        raise _fail("token_binding.agent_id must be an agent id")
    if not isinstance(scope, list) or not 1 <= len(scope) <= MAX_TOKEN_BINDING_MERCHANTS:
        raise _fail(f"token_binding.merchant_scope must list 1 to {MAX_TOKEN_BINDING_MERCHANTS} merchant ids")
    for merchant in scope:
        if not isinstance(merchant, str) or not _ID.fullmatch(merchant):
            raise _fail("token_binding.merchant_scope holds merchant ids")
    if len(set(scope)) != len(scope):
        raise _fail("token_binding.merchant_scope lists a merchant twice")
    if not isinstance(limits, Mapping):
        raise _fail("token_binding.limits must be an object")
    _exactly(limits, ("max_amount", "currency"), ("expires_at",), "token_binding.limits")
    max_amount, currency, expires_at = limits["max_amount"], limits["currency"], limits.get("expires_at")
    if type(max_amount) is not int or not 0 < max_amount <= _MAX_SAFE_INTEGER:
        raise _fail("token_binding.limits.max_amount must be a positive integer in minor units")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise _fail("token_binding.limits.currency must be an ISO 4217 code")
    if "expires_at" in limits and (
        not isinstance(expires_at, str) or not _RFC3339.fullmatch(expires_at) or _parse_rfc3339(expires_at) is None
    ):
        raise _fail("token_binding.limits.expires_at must be an RFC 3339 timestamp")
    return SchemeTokenBinding(
        scheme=scheme,  # type: ignore[arg-type]
        token_ref=token_ref,
        agent_id=agent_id,
        merchant_scope=tuple(scope),
        limits=TokenBindingLimits(max_amount=max_amount, currency=currency, expires_at=expires_at),
    )


def check_token_binding_within_capability(
    binding: SchemeTokenBinding, capability: Mapping[str, Any], artifact_expires_at: str | None = None
) -> None:
    """The binding sits inside its payment capability: same buyer agent, merchants in scope, same currency,
    a limit no higher than the cap, and no expiry after the artifact's."""
    if binding.agent_id != capability.get("buyer_agent_id"):
        raise _fail("token_binding.agent_id must be the capability buyer_agent_id")
    scope = capability.get("merchant_scope")
    scope_list = scope if isinstance(scope, list) else []
    for merchant in binding.merchant_scope:
        if merchant not in scope_list:
            raise _fail("token_binding.merchant_scope must be within the capability merchant_scope")
    if binding.limits.currency != capability.get("currency"):
        raise _fail("token_binding.limits.currency must be the capability currency")
    cap = capability.get("max_amount")
    if type(cap) not in (int, float) or binding.limits.max_amount > cap:
        raise _fail("token_binding.limits.max_amount must not exceed the capability max_amount")
    if binding.limits.expires_at is not None and artifact_expires_at is not None:
        binding_expiry = _parse_rfc3339(binding.limits.expires_at)
        artifact_expiry = _parse_rfc3339(artifact_expires_at)
        if binding_expiry is not None and artifact_expiry is not None and binding_expiry > artifact_expiry:
            raise _fail("token_binding.limits.expires_at must not be after the artifact expires_at")
