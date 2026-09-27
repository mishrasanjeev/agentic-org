# SPDX-License-Identifier: Apache-2.0
"""Per-tenant email webhook URLs: the tenant comes from the path, never from the payload.

SendGrid, Mailchimp and MoEngage each sign with one key for the whole deployment, so a valid
signature says which provider sent an event, not which tenant it belongs to. The tenant is bound by
the URL instead: ``/api/v1/webhooks/email/{provider}/{tenant_id}/{path_token}``, where the token is
an HMAC of the tenant and provider under the application secret (the construction
``core.cases.provider_webhooks.webhook_path_token`` uses for case provider inboxes, under its own
label so a token issued for one purpose is never accepted for the other).

On that URL an event whose payload names a tenant (a SendGrid ``tenant:`` category,
``custom_args.tenant_id``, a ``tenant_id`` field) must name the bound tenant; if it names another
one, or something that is not a tenant id, the event is refused (``api.v1.webhooks``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Iterable
from typing import Any

from prometheus_client import Counter

PROVIDERS: tuple[str, ...] = ("sendgrid", "mailchimp", "moengage")
#: Payload keys that name a tenant, wherever the shared routes look for one.
TENANT_FIELD_NAMES: tuple[str, ...] = ("tenant_id", "agenticorg:tenant_id", "agenticorg_tenant_id")
PATH_TOKEN_BYTES = 16
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

#: ``bound``: accepted on a tenant's own URL. ``tenant_mismatch``: refused there, the payload names
#: another tenant. ``unbound``: a delivery to a wrong or stale path, refused before it is read.
#: ``shared_path_tenant_named``: a shared-URL event that names a tenant, accepted because
#: ``AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS`` is off (what switching it on would refuse).
#: ``shared_path_refused``: the same event with the setting on, refused.
tenant_binding_total = Counter(
    "agenticorg_email_webhook_tenant_binding_total",
    "Email webhook events by provider and how their tenant was decided",
    ["provider", "outcome"],
)


def webhook_path_token(tenant_id: uuid.UUID | str, provider: str) -> str:
    """The unguessable token in a tenant's email webhook path, derived from the application secret.

    Derived rather than stored, so it needs no secret at rest; rotating ``AGENTICORG_SECRET_KEY``
    changes every tenant's email webhook URLs.
    """
    from core.config import settings

    message = f"email-webhook:v1:{tenant_id}:{provider}".encode()
    digest = hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256).digest()
    return digest[:PATH_TOKEN_BYTES].hex()


def webhook_path(tenant_id: uuid.UUID | str, provider: str) -> str:
    """The path this tenant's provider should post its email events to."""
    return f"/api/v1/webhooks/email/{provider}/{tenant_id}/{webhook_path_token(tenant_id, provider)}"


def path_token_matches(tenant_id: uuid.UUID, provider: str, token: str) -> bool:
    if provider not in PROVIDERS or not isinstance(token, str) or not _TOKEN_RE.match(token):
        return False
    return hmac.compare_digest(token, webhook_path_token(tenant_id, provider))


def tenant_claims(fields: Any, *, prefix: str = "") -> list[Any]:
    """Every value under a tenant key of ``fields`` (``prefix`` wraps the key, e.g. ``data[{}]``)."""
    if not isinstance(fields, dict):
        return []
    claims: list[Any] = []
    for name in TENANT_FIELD_NAMES:
        value = fields.get(prefix.format(name) if prefix else name)
        if value is not None and value != "":
            claims.append(value)
    return claims


def claims_name_only(tenant_id: uuid.UUID, claims: Iterable[Any]) -> bool:
    """True when every tenant the payload names is ``tenant_id`` (or it names none).

    A value that is not a tenant id cannot be the bound tenant, so it fails the check: the payload
    is signed by a key every tenant shares and is never evidence of a tenant on its own.
    """
    for claim in claims:
        try:
            named = uuid.UUID(str(claim).strip())
        except (TypeError, ValueError):
            # Fail closed: a value that is not a tenant id is not the bound tenant.
            return False
        if named != tenant_id:
            return False
    return True
