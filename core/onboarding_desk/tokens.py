# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Signed, expiring tokens that name a lead: the onboarding link and the email unsubscribe link.

A token is ``<base64url payload>.<hex HMAC>``. The payload names the purpose,
the tenant, the lead and the expiry; the HMAC is keyed with the platform secret
and bound to the purpose, so an unsubscribe token cannot open an application,
an onboarding token cannot unsubscribe anyone, and neither works as an
applicant's working session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.config import settings

PURPOSES: tuple[str, ...] = ("apply", "unsubscribe", "session")
TTL: dict[str, timedelta] = {
    "apply": timedelta(days=14),
    "unsubscribe": timedelta(days=365),
    # An applicant's working session, exchanged for the onboarding link so the link stays out of later requests.
    "session": timedelta(hours=2),
}
MAX_TOKEN = 512


class TokenError(ValueError):
    """The token is malformed, forged, for another purpose, or expired."""


@dataclass(frozen=True)
class LinkClaims:
    purpose: str
    tenant_id: uuid.UUID
    lead_id: uuid.UUID
    expires_at: datetime


def _mac(payload: str, purpose: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), f"onboarding-link:{purpose}:{payload}".encode(), hashlib.sha256
    ).hexdigest()


def sign(purpose: str, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, now: datetime | None = None) -> str:
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}")
    moment = now or datetime.now(UTC)
    expires = int((moment + TTL[purpose]).timestamp())
    body = json.dumps({"p": purpose, "t": str(tenant_id), "l": str(lead_id), "e": expires}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{payload}.{_mac(payload, purpose)}"


def verify(token: str, purpose: str, *, now: datetime | None = None) -> LinkClaims:
    if not token or len(token) > MAX_TOKEN or token.count(".") != 1:
        raise TokenError("malformed")
    payload, mac = token.split(".")
    if not hmac.compare_digest(mac, _mac(payload, purpose)):
        raise TokenError("signature")
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims = LinkClaims(
            purpose=str(data["p"]),
            tenant_id=uuid.UUID(str(data["t"])),
            lead_id=uuid.UUID(str(data["l"])),
            expires_at=datetime.fromtimestamp(int(data["e"]), tz=UTC),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise TokenError("malformed") from exc
    if claims.purpose != purpose:
        raise TokenError("purpose")
    if claims.expires_at <= (now or datetime.now(UTC)):
        raise TokenError("expired")
    return claims
