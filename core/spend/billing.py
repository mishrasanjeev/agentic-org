# SPDX-License-Identifier: Apache-2.0
"""Who paid for a usage: the tenant's own key, the platform's key, or in-house serving.

A record's ``billing_account`` is captured at call time where the credential
is resolved (the router notes it; the graph reads the credential the runner
prefetched). For a record that arrives without one (a direct caller, a
backfill), the writer infers it here: a tenant with an active or unverified
credential of its own for the provider pays itself (``tenant_key``),
otherwise the platform's key paid (``platform_key``; a tenant without its
own key runs on the platform's, ``core/ai_providers/resolver.py``).
In-house providers and platform storage are ``in_house``. Reconciliation
compares only tenant-billed usage with the tenant's invoice.

The credential rows are read once per tenant and cached for 60 seconds in
this process.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Collection
from typing import Any

import structlog
from sqlalchemy import select, tuple_

from core.spend import vocab

logger = structlog.get_logger()

CACHE_TTL_SECONDS = 60.0
_CACHE_MAX = 10_000
_ACTIVE_STATUSES = ("active", "unverified")  # the statuses core/ai_providers/resolver.py accepts

# enterprise-gate: process-local-ok reason=billing-account-cache-ttl-60s-keeps-no-cross-tenant-data
_BILLING_CACHE: dict[str, tuple[float, frozenset[tuple[str, str]]]] = {}


def invalidate(tenant_id: uuid.UUID | str) -> None:
    _BILLING_CACHE.pop(str(tenant_id), None)


def fixed_account(provider: str) -> str | None:
    """``in_house`` for in-house providers and platform storage; ``None`` when it depends on credentials."""
    if provider in vocab.IN_HOUSE_PROVIDERS or provider == vocab.STORAGE_PROVIDER:
        return "in_house"
    return None


async def _tenant_credentials(session: Any, tenant_id: uuid.UUID, *, now: float) -> frozenset[tuple[str, str]]:
    key = str(tenant_id)
    hit = _BILLING_CACHE.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    from core.models.tenant_ai_credential import TenantAICredential as C

    pairs = sorted(set(vocab.BILLING_CREDENTIAL_KEYS.values()))
    statement = select(C.provider, C.credential_kind).where(
        C.tenant_id == tenant_id,
        C.status.in_(_ACTIVE_STATUSES),
        tuple_(C.provider, C.credential_kind).in_(pairs),
    )
    rows = (await session.execute(statement)).all()
    found = frozenset((str(r[0]), str(r[1])) for r in rows)
    if len(_BILLING_CACHE) >= _CACHE_MAX:
        _BILLING_CACHE.clear()
    _BILLING_CACHE[key] = (now, found)
    return found


async def infer(
    session: Any, tenant_id: uuid.UUID, providers: Collection[str], *, now: float | None = None
) -> dict[str, str]:
    """``provider -> account`` for the providers whose account can be inferred; never raises.

    A provider spend does not know the credential of (a tool connector, an
    unknown name) is left out, so its records keep no account. The read runs
    in a savepoint: a failure leaves the caller's transaction usable and the
    accounts unknown.
    """
    out: dict[str, str] = {}
    needs_lookup = []
    for provider in sorted(set(providers)):
        fixed = fixed_account(provider)
        if fixed is not None:
            out[provider] = fixed
        elif provider in vocab.BILLING_CREDENTIAL_KEYS:
            needs_lookup.append(provider)
    if not needs_lookup:
        return out
    try:
        async with session.begin_nested():
            held = await _tenant_credentials(session, tenant_id, now=time.monotonic() if now is None else now)
    # enterprise-gate: broad-except-ok reason=billing-inference-failure-leaves-the-account-unknown-and-logs
    except Exception as exc:
        logger.warning("spend_billing_inference_failed", error_type=type(exc).__name__)
        return out
    for provider in needs_lookup:
        out[provider] = "tenant_key" if vocab.BILLING_CREDENTIAL_KEYS[provider] in held else "platform_key"
    return out
