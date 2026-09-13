"""Durable billing entitlement — ``billing_subscriptions`` is the source of truth.

Stripe and Plural activations used to live only in Redis (``tenant_tier:*``,
``tenant:{id}:plan`` ...). A Redis flush silently downgraded every paid
tenant and a one-time Plural order never expired. This module persists one
row per tenant (tenant_id is UNIQUE) and treats Redis purely as a cache:

* ``record_subscription`` / ``deactivate_subscription`` write the row in a
  tenant-scoped (RLS) session and then warm the Redis cache.
* ``get_subscription`` reads the row, warms the cache, and only falls back
  to the cache when the database is unavailable.
* ``expire_overdue_plural_subscriptions`` (Celery beat) downgrades Plural
  rows whose ``current_period_end`` has passed.

The provider clients are synchronous (they run under ``asyncio.to_thread``
from the webhook handlers), so ``*_sync`` bridges run the same coroutines
on a private event loop with a short-lived engine / Redis client. Billing
events are rare (a handful per tenant per month), so the extra connection
is a non-issue, and it avoids touching the request loop's pools from a
worker thread.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

ACTIVE_STATUSES = frozenset({"active", "trialing"})
PROVIDERS = frozenset({"stripe", "plural"})
FREE_PLAN = "free"

# Plural is a one-time hosted-checkout order, not a recurring mandate. Each
# successful payment buys one billing period of this length.
PLURAL_PERIOD_DAYS = 30

_UPSERT_SQL = text(
    """
    INSERT INTO billing_subscriptions (
        tenant_id, provider, external_id, provider_customer_id, plan, status,
        current_period_start, current_period_end, updated_at
    ) VALUES (
        CAST(:tenant_id AS uuid), :provider, :external_id, :provider_customer_id,
        :plan, :status, :period_start, :period_end, :now
    )
    ON CONFLICT (tenant_id) DO UPDATE SET
        provider = EXCLUDED.provider,
        external_id = EXCLUDED.external_id,
        provider_customer_id = COALESCE(
            NULLIF(EXCLUDED.provider_customer_id, ''),
            billing_subscriptions.provider_customer_id
        ),
        plan = EXCLUDED.plan,
        status = EXCLUDED.status,
        current_period_start = EXCLUDED.current_period_start,
        current_period_end = EXCLUDED.current_period_end,
        updated_at = EXCLUDED.updated_at
    """
)

_SELECT_SQL = text(
    """
    SELECT provider, external_id, provider_customer_id, plan, status,
           current_period_start, current_period_end, updated_at
    FROM billing_subscriptions
    WHERE tenant_id = CAST(:tenant_id AS uuid)
    """
)

# ``external_id`` guard: a provider cancellation event names the subscription
# it is about. Only the row holding that id may be downgraded, so a stale or
# replayed event for a replaced subscription cannot cancel the current one.
# An empty ``external_id`` (authenticated cancel endpoint, which already acted
# on the stored id) matches the tenant's row unconditionally.
_DEACTIVATE_SQL = text(
    """
    UPDATE billing_subscriptions
    SET plan = :plan, status = :status, updated_at = :now
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND (CAST(:external_id AS text) = '' OR external_id = CAST(:external_id AS text))
    """
)


# ── Shape helpers ────────────────────────────────────────────────────


def _tenant_uuid(tenant_id: str | uuid.UUID) -> uuid.UUID:
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("billing subscription requires a UUID tenant_id") from exc


def free_subscription(tenant_id: str) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "plan": FREE_PLAN,
        "tier": FREE_PLAN,
        "status": "none",
        "provider": "",
        "order_id": "",
        "provider_customer_id": "",
        "current_period_start": None,
        "current_period_end": None,
        "is_paid": False,
    }


def _row_to_dict(tenant_id: str, row: Any) -> dict[str, Any]:
    plan = row.plan or FREE_PLAN
    status = row.status or "none"
    paid = status in ACTIVE_STATUSES and plan != FREE_PLAN
    return {
        "tenant_id": str(tenant_id),
        "plan": plan if paid else FREE_PLAN,
        "tier": plan if paid else FREE_PLAN,
        "status": status,
        "provider": row.provider or "",
        "order_id": row.external_id or "",
        "provider_customer_id": row.provider_customer_id or "",
        "current_period_start": (
            row.current_period_start.isoformat() if row.current_period_start else None
        ),
        "current_period_end": (
            row.current_period_end.isoformat() if row.current_period_end else None
        ),
        "is_paid": paid,
    }


def plural_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    start = now or datetime.now(UTC)
    return start, start + timedelta(days=PLURAL_PERIOD_DAYS)


# ── Redis cache (keys shared with the legacy readers) ───────────────


def _cache_keys(tenant_id: str) -> dict[str, str]:
    return {
        "tier": f"tenant_tier:{tenant_id}",
        "plan": f"tenant:{tenant_id}:plan",
        "provider": f"tenant:{tenant_id}:billing_provider",
        "order_id": f"tenant:{tenant_id}:billing_order_id",
        "stripe_subscription_id": f"tenant:{tenant_id}:stripe_subscription_id",
        "stripe_customer_id": f"tenant:{tenant_id}:stripe_customer_id",
    }


async def _warm_cache(redis: Any, sub: dict[str, Any]) -> None:
    if redis is None:
        return
    keys = _cache_keys(sub["tenant_id"])
    try:
        await redis.set(keys["tier"], sub["tier"])
        await redis.set(keys["plan"], sub["plan"])
        if sub["provider"]:
            await redis.set(keys["provider"], sub["provider"])
        else:
            await redis.delete(keys["provider"])
        if sub["is_paid"] and sub["order_id"]:
            await redis.set(keys["order_id"], sub["order_id"])
            if sub["provider"] == "stripe":
                await redis.set(keys["stripe_subscription_id"], sub["order_id"])
        else:
            await redis.delete(keys["order_id"], keys["stripe_subscription_id"])
        if sub["provider"] == "stripe" and sub["provider_customer_id"]:
            await redis.set(keys["stripe_customer_id"], sub["provider_customer_id"])
    # enterprise-gate: broad-except-ok reason=redis-cache-warm-is-best-effort-db-row-is-authoritative
    except Exception:
        logger.warning("billing_subscription_cache_warm_failed", tenant_id=sub["tenant_id"])


async def _read_cache(redis: Any, tenant_id: str) -> dict[str, Any] | None:
    if redis is None:
        return None
    keys = _cache_keys(tenant_id)
    try:
        plan = await redis.get(keys["plan"])
        provider = await redis.get(keys["provider"])
        order_id = await redis.get(keys["order_id"])
    # enterprise-gate: broad-except-ok reason=cache-read-failure-falls-back-to-free-plan
    except Exception:
        return None
    if plan is None:
        return None
    plan = plan.decode() if isinstance(plan, bytes) else str(plan)
    provider = provider.decode() if isinstance(provider, bytes) else (provider or "")
    order_id = order_id.decode() if isinstance(order_id, bytes) else (order_id or "")
    sub = free_subscription(tenant_id)
    paid = plan not in ("", FREE_PLAN)
    sub.update(
        plan=plan if paid else FREE_PLAN,
        tier=plan if paid else FREE_PLAN,
        status="active" if paid else "none",
        provider=provider,
        order_id=order_id if paid else "",
        is_paid=paid,
    )
    return sub


# ── Core DB operations (session-agnostic) ───────────────────────────


async def _upsert(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    provider: str,
    plan: str,
    status: str,
    provider_subscription_id: str,
    provider_customer_id: str,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown billing provider: {provider}")
    if not plan:
        raise ValueError("billing subscription requires a plan")
    await session.execute(
        _UPSERT_SQL,
        {
            "tenant_id": str(tenant_id),
            "provider": provider,
            "external_id": provider_subscription_id or "",
            "provider_customer_id": provider_customer_id or "",
            "plan": plan,
            "status": status,
            "period_start": current_period_start,
            "period_end": current_period_end,
            "now": datetime.now(UTC),
        },
    )


async def _select(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    result = await session.execute(_SELECT_SQL, {"tenant_id": str(tenant_id)})
    row = result.first()
    if row is None:
        return free_subscription(str(tenant_id))
    return _row_to_dict(str(tenant_id), row)


async def _deactivate(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: str,
    provider_subscription_id: str = "",
) -> None:
    result = await session.execute(
        _DEACTIVATE_SQL,
        {
            "tenant_id": str(tenant_id),
            "plan": FREE_PLAN,
            "status": status,
            "now": datetime.now(UTC),
            "external_id": provider_subscription_id or "",
        },
    )
    if provider_subscription_id and getattr(result, "rowcount", None) == 0:
        logger.warning(
            "billing_subscription_deactivate_ignored_unmatched_id",
            tenant_id=str(tenant_id),
            provider_subscription_id=provider_subscription_id,
            status=status,
        )


# ── Public async API (request loop / Celery runner) ─────────────────


async def record_subscription(
    tenant_id: str,
    *,
    provider: str,
    plan: str,
    status: str = "active",
    provider_subscription_id: str = "",
    provider_customer_id: str = "",
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
) -> dict[str, Any]:
    """Persist an activation / provider sync and warm the cache.

    Raises on database failure so webhook handlers fail closed (the
    provider retries the event) instead of acknowledging a lost activation.
    """
    from core.async_redis import get_async_redis
    from core.database import get_tenant_session

    tid = _tenant_uuid(tenant_id)
    async with get_tenant_session(tid) as session:
        await _upsert(
            session,
            tid,
            provider=provider,
            plan=plan,
            status=status,
            provider_subscription_id=provider_subscription_id,
            provider_customer_id=provider_customer_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        sub = await _select(session, tid)
    await _warm_cache(await get_async_redis(), sub)
    logger.info(
        "billing_subscription_recorded",
        tenant_id=str(tid),
        provider=provider,
        plan=plan,
        status=status,
        period_end=sub["current_period_end"],
    )
    return sub


async def deactivate_subscription(
    tenant_id: str, *, status: str = "cancelled", provider_subscription_id: str = ""
) -> dict[str, Any]:
    """Downgrade a tenant to free (cancel / expiry / provider deletion).

    ``provider_subscription_id``, when given, must equal the stored
    ``external_id`` for the row to change (see ``_DEACTIVATE_SQL``).
    """
    from core.async_redis import get_async_redis
    from core.database import get_tenant_session

    tid = _tenant_uuid(tenant_id)
    async with get_tenant_session(tid) as session:
        await _deactivate(
            session, tid, status=status, provider_subscription_id=provider_subscription_id
        )
        sub = await _select(session, tid)
    await _warm_cache(await get_async_redis(), sub)
    logger.info("billing_subscription_deactivated", tenant_id=str(tid), status=status)
    return sub


async def get_subscription(tenant_id: str) -> dict[str, Any]:
    """Return the tenant's entitlement. DB first; Redis only as a fallback."""
    from core.async_redis import get_async_redis
    from core.database import get_tenant_session

    try:
        tid = _tenant_uuid(tenant_id)
    except ValueError:
        return free_subscription(str(tenant_id))

    redis = await get_async_redis()
    try:
        async with get_tenant_session(tid) as session:
            sub = await _select(session, tid)
    # enterprise-gate: broad-except-ok reason=db-outage-falls-back-to-cache-then-free-never-raises-to-reader
    except Exception:
        logger.warning("billing_subscription_db_read_failed", tenant_id=str(tid))
        cached = await _read_cache(redis, str(tid))
        return cached or free_subscription(str(tid))
    await _warm_cache(redis, sub)
    return sub


async def get_effective_plan(tenant_id: str) -> str:
    return (await get_subscription(tenant_id))["plan"]


async def expire_overdue_plural_subscriptions(now: datetime | None = None) -> dict[str, Any]:
    """Downgrade Plural subscriptions whose paid period has ended.

    Plural rows are tenant-scoped (RLS), so this iterates tenants and reads
    each row inside its own tenant session — the same shape the invoice
    generator uses. Returns counters for the beat log.
    """
    from sqlalchemy import select

    from core.async_redis import get_async_redis
    from core.database import async_session_factory, get_tenant_session
    from core.models.tenant import Tenant

    moment = now or datetime.now(UTC)
    expired = 0
    checked = 0
    failed = 0

    async with async_session_factory() as session:
        result = await session.execute(select(Tenant.id).where(Tenant.deleted_at.is_(None)))
        tenant_ids = [row[0] for row in result.all()]

    redis = await get_async_redis()
    for tid in tenant_ids:
        try:
            async with get_tenant_session(tid) as session:
                sub = await _select(session, tid)
                checked += 1
                if sub["provider"] != "plural" or sub["status"] not in ACTIVE_STATUSES:
                    continue
                end = sub["current_period_end"]
                if not end or datetime.fromisoformat(end) > moment:
                    continue
                await _deactivate(
                    session, tid, status="expired", provider_subscription_id=sub["order_id"]
                )
                sub = await _select(session, tid)
            await _warm_cache(redis, sub)
            expired += 1
            logger.info("plural_subscription_expired", tenant_id=str(tid), period_end=end)
        # enterprise-gate: broad-except-ok reason=expiry-failure-isolated-per-tenant-and-counted
        except Exception:
            failed += 1
            logger.exception("plural_subscription_expiry_failed", tenant_id=str(tid))

    return {"checked": checked, "expired": expired, "failed": failed}


# ── Sync bridges for the thread-run provider clients ────────────────


async def _run_with_private_resources(
    op: Callable[[AsyncSession, Any], Awaitable[dict[str, Any]]],
    tid: uuid.UUID,
) -> dict[str, Any]:
    """Run ``op`` on a private engine + Redis client bound to this loop."""
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from core.config import redis_socket_timeout_kwargs, redis_url_from_env, settings

    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    redis = None
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text("SELECT set_config('agenticorg.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tid)},
            )
            await session.execute(
                text("SELECT set_config('agenticorg.company_id', '', true)")
            )
            try:
                sub = await op(session, tid)
                await session.commit()
            # enterprise-gate: broad-except-ok reason=rollback-then-reraise-so-webhook-fails-closed
            except Exception:
                await session.rollback()
                raise
        try:
            redis = aioredis.from_url(
                redis_url_from_env(default_db=0),
                decode_responses=True,
                **redis_socket_timeout_kwargs(),
            )
            await _warm_cache(redis, sub)
        # enterprise-gate: broad-except-ok reason=cache-warm-best-effort-after-durable-write
        except Exception:
            logger.warning("billing_subscription_cache_warm_unavailable", tenant_id=str(tid))
        return sub
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            # enterprise-gate: broad-except-ok reason=redis-close-failure-is-not-actionable
            except Exception:  # noqa: S110
                pass
        await engine.dispose()


def _run_bridge(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "record_subscription_sync must not be called from an event loop; "
        "await record_subscription() instead"
    )


def record_subscription_sync(
    tenant_id: str,
    *,
    provider: str,
    plan: str,
    status: str = "active",
    provider_subscription_id: str = "",
    provider_customer_id: str = "",
    current_period_start: datetime | None = None,
    current_period_end: datetime | None = None,
) -> dict[str, Any]:
    """Thread-safe sync variant of :func:`record_subscription`."""
    tid = _tenant_uuid(tenant_id)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown billing provider: {provider}")
    if not plan:
        raise ValueError("billing subscription requires a plan")

    async def _op(session: AsyncSession, _tid: uuid.UUID) -> dict[str, Any]:
        await _upsert(
            session,
            _tid,
            provider=provider,
            plan=plan,
            status=status,
            provider_subscription_id=provider_subscription_id,
            provider_customer_id=provider_customer_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        return await _select(session, _tid)

    sub = _run_bridge(_run_with_private_resources(_op, tid))
    logger.info(
        "billing_subscription_recorded",
        tenant_id=str(tid),
        provider=provider,
        plan=plan,
        status=status,
        period_end=sub["current_period_end"],
    )
    return sub


def deactivate_subscription_sync(
    tenant_id: str, *, status: str = "cancelled", provider_subscription_id: str = ""
) -> dict[str, Any]:
    """Thread-safe sync variant of :func:`deactivate_subscription`."""
    tid = _tenant_uuid(tenant_id)

    async def _op(session: AsyncSession, _tid: uuid.UUID) -> dict[str, Any]:
        await _deactivate(
            session, _tid, status=status, provider_subscription_id=provider_subscription_id
        )
        return await _select(session, _tid)

    sub = _run_bridge(_run_with_private_resources(_op, tid))
    logger.info("billing_subscription_deactivated", tenant_id=str(tid), status=status)
    return sub


def get_subscription_sync(tenant_id: str) -> dict[str, Any]:
    """Thread-safe sync variant of :func:`get_subscription` (DB read only).

    Falls back to ``free`` when the tenant id is not a UUID; raises on
    database failure so callers deciding on paid state fail closed.
    """
    try:
        tid = _tenant_uuid(tenant_id)
    except ValueError:
        return free_subscription(str(tenant_id))

    async def _op(session: AsyncSession, _tid: uuid.UUID) -> dict[str, Any]:
        return await _select(session, _tid)

    return _run_bridge(_run_with_private_resources(_op, tid))
