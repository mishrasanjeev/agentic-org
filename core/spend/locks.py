# SPDX-License-Identifier: Apache-2.0
"""Advisory lock keys for spend intelligence, built in one place.

Every key is made by one helper here and passed as a bind parameter, so two
callers can never format the same key differently. Locks are transaction
scoped (``pg_advisory_xact_lock``): they end with the transaction that took
them. No lock or statement timeout is set anywhere else in the code base;
spend sets one locally where a caller may wait.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import text

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))")
_LOCK_SHARED = text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:k, 0))")
_TRY_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))")
_TRY_LOCK_SHARED = text("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(:k, 0))")
_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :v, true)")


def key(*parts: object) -> str:
    """``spend:`` followed by the parts joined with ``:``."""
    return "spend:" + ":".join(str(part) for part in parts)


def rollup_day(tenant_id: uuid.UUID | str, day: date) -> str:
    return key("rollup", tenant_id, day.isoformat())


def rate_card(
    tenant_id: uuid.UUID | str, provider: str, usage_type: str, model_sku: str, unit: str, source: str
) -> str:
    return key("rate_card", tenant_id, provider, usage_type, model_sku, unit, source)


def commitment(
    tenant_id: uuid.UUID | str, provider: str, usage_type: str | None, model_sku: str, unit: str | None, kind: str
) -> str:
    return key("commitment", tenant_id, provider, usage_type or "", model_sku, unit or "", kind)


def org_tree(tenant_id: uuid.UUID | str) -> str:
    return key("org_tree", tenant_id)


def model_alias(tenant_id: uuid.UUID | str, provider: str) -> str:
    return key("model_alias", tenant_id, provider)


def fx_rate(tenant_id: uuid.UUID | str, currency: str) -> str:
    return key("fx_rate", tenant_id, currency)


def commitment_recompute(tenant_id: uuid.UUID | str, provider: str) -> str:
    return key("commitment_recompute", tenant_id, provider)


async def xact_lock(session: Any, k: str) -> None:
    """Wait for the exclusive lock on ``k`` until the transaction ends."""
    await session.execute(_LOCK, {"k": k})


async def xact_lock_shared(session: Any, k: str) -> None:
    """Wait for a shared lock on ``k`` until the transaction ends."""
    await session.execute(_LOCK_SHARED, {"k": k})


async def try_xact_lock(session: Any, k: str) -> bool:
    """Take the exclusive lock on ``k`` if it is free; never waits."""
    return bool((await session.execute(_TRY_LOCK, {"k": k})).scalar())


async def try_xact_lock_shared(session: Any, k: str) -> bool:
    """Take a shared lock on ``k`` if no exclusive holder has it; never waits."""
    return bool((await session.execute(_TRY_LOCK_SHARED, {"k": k})).scalar())


async def set_lock_timeout(session: Any, ms: int) -> None:
    """``SET LOCAL lock_timeout`` for the rest of the transaction."""
    await session.execute(_LOCK_TIMEOUT, {"v": f"{max(0, int(ms))}ms"})
