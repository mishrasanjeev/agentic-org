# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""``Idempotency-Key`` records for UCP writes (UCP signatures, Replay Protection; checkout REST binding).

A key is scoped to the tenant and merchant and kept for 48 hours (at least
24 hours, enforced by a CHECK). The same key with the same raw body (compared
by SHA-256) replays the stored response; the same key with a different body,
or for a different operation, is 409; a key whose first request is still
running is 409 as well. If the records cannot be read or written the request
is refused with 503 (fail closed): a write is never performed unprotected.

The claim is written in its own transaction before the operation runs, and the
response is stored after it. A response of 500 or above is not stored: the
claim is released so the client can retry.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()

RETENTION = timedelta(hours=48)
MAX_KEY_LENGTH = 255


class IdempotencyUnavailableError(Exception):
    """The idempotency store failed: refuse with 503."""


@dataclass(frozen=True)
class Claim:
    """``replay`` holds ``(status, body)`` when the key was already answered; ``conflict`` names a 409."""

    claimed: bool
    replay: tuple[int, dict[str, Any]] | None = None
    conflict: str | None = None


def body_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def valid_key(key: str | None) -> bool:
    return bool(key) and len(key or "") <= MAX_KEY_LENGTH and (key or "").isprintable()


async def claim(*, tenant_id: str, merchant_id: str, key: str, operation: str, raw_body: bytes) -> Claim:
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.postgresql import insert

    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceUcpIdempotencyRow as Row

    tenant = uuid.UUID(str(tenant_id))
    digest = body_sha256(raw_body)
    now = datetime.now(UTC)
    try:
        async with get_tenant_session(tenant) as session:
            inserted = (
                await session.execute(
                    insert(Row)
                    .values(
                        tenant_id=tenant,
                        merchant_id=merchant_id,
                        idempotency_key=key,
                        operation=operation,
                        body_sha256=digest,
                        status="pending",
                        created_at=now,
                        expires_at=now + RETENTION,
                    )
                    .on_conflict_do_nothing(index_elements=["tenant_id", "merchant_id", "idempotency_key"])
                    .returning(Row.idempotency_key)
                )
            ).first()
            if inserted is not None:
                return Claim(claimed=True)
            row = (
                await session.execute(
                    select(Row)
                    .where(Row.tenant_id == tenant, Row.merchant_id == merchant_id, Row.idempotency_key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise IdempotencyUnavailableError("idempotency record vanished")
            if row.expires_at <= now:
                await session.execute(
                    delete(Row).where(
                        Row.tenant_id == tenant, Row.merchant_id == merchant_id, Row.idempotency_key == key
                    )
                )
                await session.flush()
                session.add(
                    Row(
                        tenant_id=tenant,
                        merchant_id=merchant_id,
                        idempotency_key=key,
                        operation=operation,
                        body_sha256=digest,
                        status="pending",
                        created_at=now,
                        expires_at=now + RETENTION,
                    )
                )
                return Claim(claimed=True)
            if row.operation != operation or row.body_sha256 != digest:
                return Claim(claimed=False, conflict="idempotency_key_reused")
            if row.status != "complete" or row.response_status is None:
                return Claim(claimed=False, conflict="idempotency_key_in_progress")
            return Claim(claimed=False, replay=(int(row.response_status), dict(row.response_body or {})))
    except IdempotencyUnavailableError:
        raise
    # enterprise-gate: broad-except-ok reason=idempotency-store-failure-fails-closed-with-503
    except Exception as exc:
        logger.error("ucp_idempotency_store_failed", error_type=type(exc).__name__)
        raise IdempotencyUnavailableError(type(exc).__name__) from exc


async def store(*, tenant_id: str, merchant_id: str, key: str, status: int, body: dict[str, Any]) -> None:
    """Record the answer to a claimed key; a 5xx answer releases the claim instead."""
    from sqlalchemy import delete, update

    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceUcpIdempotencyRow as Row

    tenant = uuid.UUID(str(tenant_id))
    where = (Row.tenant_id == tenant, Row.merchant_id == merchant_id, Row.idempotency_key == key)
    try:
        async with get_tenant_session(tenant) as session:
            if status >= 500:
                await session.execute(delete(Row).where(*where))
            else:
                await session.execute(
                    update(Row).where(*where).values(status="complete", response_status=status, response_body=body)
                )
    # enterprise-gate: broad-except-ok reason=store-failure-after-durable-write-only-blocks-replay-until-expiry
    except Exception as exc:
        logger.error("ucp_idempotency_store_response_failed", error_type=type(exc).__name__)


async def prune_expired() -> int:
    """Delete expired records tenant by tenant (beat sweep)."""
    from sqlalchemy import delete, select, text

    from core.database import async_session_factory, get_tenant_session
    from core.models.commerce_ucp import CommerceUcpIdempotencyRow as Row
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        tenants = list((await session.scalars(select(Tenant.id))).all())
    removed = 0
    now = datetime.now(UTC)
    for tenant in tenants:
        async with get_tenant_session(tenant) as session:
            result = await session.execute(delete(Row).where(Row.tenant_id == tenant, Row.expires_at <= now))
            removed += int(getattr(result, "rowcount", 0) or 0)
    return removed
