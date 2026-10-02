# SPDX-License-Identifier: Apache-2.0
"""The authenticated caller of the current request, for policies that match on identity.

The auth middleware binds a :class:`CallerIdentity` for the span of each
authenticated request; the model gateway reads it when a model call is routed,
so an access policy can say which application, principal or business unit may
use which provider or model without the identity being threaded through every
runner signature. Work that runs outside a request (a worker task, a schedule)
carries no identity: a policy that matches on ``application`` or ``principal``
does not match it, and a policy with neither field applies as before.

``principal`` names who called, in the same spelling the audit rows use:
``user:<id>`` for a human session, ``api_key:<prefix>`` for an API key,
``grantex:<subject>`` for an Agent Passport, the buyer subject for a commerce
credential. ``application`` names what called: the API key's name, the
passport's agent (``agent:<id>``), ``console`` for a human session,
``commerce`` for a buyer credential.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CallerIdentity:
    principal: str | None = None
    application: str | None = None
    auth_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_IDENTITY: ContextVar[CallerIdentity | None] = ContextVar("agenticorg_caller_identity", default=None)


def current_identity() -> CallerIdentity | None:
    """The identity bound for the current request, or None outside one."""
    return _IDENTITY.get()


def bind_identity(identity: CallerIdentity | None) -> Token[CallerIdentity | None]:
    """Bind ``identity`` for the current context; reset with :func:`reset_identity`."""
    return _IDENTITY.set(identity)


def reset_identity(token: Token[CallerIdentity | None]) -> None:
    _IDENTITY.reset(token)


def _clean(value: object) -> str:
    return str(value or "").strip()


def identity_from_state(state: object) -> CallerIdentity:
    """Derive the caller's identity from the request state the auth middleware wrote.

    Unknown or missing modes give an identity with nothing but the mode, which
    no identity-matching policy matches.
    """
    claims: dict[str, Any] = getattr(state, "claims", None) or {}
    if not isinstance(claims, dict):
        claims = {}
    auth_mode = getattr(state, "auth_mode", None)
    mode = auth_mode if isinstance(auth_mode, str) and auth_mode else None
    subject = _clean(claims.get("sub"))
    if mode == "api_key":
        prefix = subject.removeprefix("apikey:")
        name = _clean(getattr(state, "api_key_name", "")).lower()
        principal = f"api_key:{prefix}" if prefix else None
        return CallerIdentity(principal=principal, application=name or principal, auth_mode=mode)
    if mode == "grantex":
        agent = _clean(claims.get("agenticorg:agent_id") or getattr(state, "agent_id", ""))
        return CallerIdentity(
            principal=f"grantex:{subject}" if subject else None,
            application=f"agent:{agent}" if agent else None,
            auth_mode=mode,
        )
    if mode == "commerce_buyer":
        return CallerIdentity(principal=subject or None, application="commerce", auth_mode=mode)
    if mode == "legacy":
        user_id = _clean(claims.get("agenticorg:user_id"))
        principal = f"user:{user_id}" if user_id else (f"user:{subject}" if subject else None)
        application = _clean(claims.get("azp")).lower() or "console"
        return CallerIdentity(principal=principal, application=application, auth_mode=mode)
    return CallerIdentity(auth_mode=mode)
