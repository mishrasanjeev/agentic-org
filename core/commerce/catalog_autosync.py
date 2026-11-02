# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The scheduled catalog sync of listed network shops that sell through Shopify.

Buyers' AI apps see a shop's prices and stock as AgenticOrg last read them from
Shopify. Every ten minutes, on the existing beat and worker, each listed shop
whose Shopify catalog is older than ``network_catalog_sync_minutes`` (default
60), or that a Shopify product webhook marked for refresh, is read again
(``api.v1.commerce_runtime.resync_shopify_scope``, the same read-only sync as the
setup page's button, except that it never marks the shop blocked). Uploaded
catalogs change only when the shop uploads, so they are left alone.

Bounds: at most ``SYNC_MAX_SHOPS`` shops are read from Shopify per run, in a
random order, so a shop just read is fresh on the next run and the rest follow
then; a shop whose read failed waits ``FAILURE_BACKOFF_SECONDS`` before the next
try; and one run at a time (a Redis lock), so a slow run never overlaps the next.
Without Redis the backoff and the lock are skipped. A no-op while the network
is off or the setting is 0.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

import structlog

logger = structlog.get_logger()

SYNC_MAX_SHOPS = 25
FAILURE_BACKOFF_SECONDS = 3600
RUN_LOCK_SECONDS = 900
RUN_LOCK_KEY = "network-catalog-sync:run"
OUTCOMES = ("synced", "fresh", "not_shopify", "failed", "backing_off")


async def _redis() -> Any:
    try:
        from core.async_redis import get_async_redis  # noqa: PLC0415

        return await get_async_redis()
    # enterprise-gate: broad-except-ok reason=redis-down-logged-sync-runs-without-backoff-or-lock
    except Exception as exc:
        logger.warning("network_catalog_sync_redis_unavailable", error_type=type(exc).__name__)
        return None


# What ``_call`` returns when there is no Redis or the call failed (the sync runs without the
# backoff or the lock); a held lock is redis-py's None for a refused ``SET NX``.
UNAVAILABLE = object()


async def _call(client: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """One Redis call, or ``UNAVAILABLE``."""
    if client is None:
        return UNAVAILABLE
    try:
        return await getattr(client, method)(*args, **kwargs)
    # enterprise-gate: broad-except-ok reason=redis-error-logged-sync-runs-without-backoff-or-lock
    except Exception as exc:
        logger.warning("network_catalog_sync_redis_failed", error_type=type(exc).__name__)
        return UNAVAILABLE


def _failure_key(listing: Any) -> str:
    scope = f"{listing.tenant_id}|{listing.merchant_id}|{listing.seller_agent_id or ''}"
    return "network-catalog-sync:failed:" + hashlib.sha256(scope.encode()).hexdigest()


async def sync_listed_catalogs() -> dict[str, Any]:
    """Re-read the listed Shopify shops whose catalog is due; returns counts per outcome."""
    from core.commerce import network as net  # noqa: PLC0415
    from core.config import settings  # noqa: PLC0415

    minutes = int(settings.network_catalog_sync_minutes or 0)
    if not net.network_enabled() or minutes <= 0:
        return {"enabled": False}
    client = await _redis()
    locked = await _call(client, "set", RUN_LOCK_KEY, "1", nx=True, ex=RUN_LOCK_SECONDS)
    if not locked:
        return {"enabled": True, "skipped": "another_run"}
    try:
        return await _sync(net, client, minutes)
    finally:
        await _call(client, "delete", RUN_LOCK_KEY)


async def _sync(net: Any, client: Any, minutes: int) -> dict[str, Any]:
    from api.v1 import commerce_runtime  # noqa: PLC0415 - the API module owns the Shopify sync

    counts: dict[str, Any] = dict.fromkeys(OUTCOMES, 0)
    listings = list(await net.listed_rows())
    random.shuffle(listings)  # noqa: S311  # nosec B311
    for checked, listing in enumerate(listings):
        if counts["synced"] + counts["failed"] >= SYNC_MAX_SHOPS:
            counts["left_for_next_run"] = len(listings) - checked
            break
        backing_off = await _call(client, "exists", _failure_key(listing))
        if backing_off is not UNAVAILABLE and backing_off:
            counts["backing_off"] += 1
            continue
        try:
            outcome = await commerce_runtime.resync_shopify_scope(
                tenant_id=str(listing.tenant_id),
                merchant_id=listing.merchant_id,
                seller_agent_id=listing.seller_agent_id or "",
                max_age_seconds=minutes * 60,
            )
        # enterprise-gate: broad-except-ok reason=one-shops-failed-sync-never-stops-the-other-shops
        except Exception as exc:
            logger.warning("network_catalog_sync_failed", slug=listing.slug, error_type=type(exc).__name__)
            outcome = "failed"
        if outcome == "failed":
            await _call(client, "set", _failure_key(listing), "1", ex=FAILURE_BACKOFF_SECONDS)
        counts[outcome] = counts.get(outcome, 0) + 1
    counts["enabled"] = True
    counts["shops"] = len(listings)
    counts.setdefault("left_for_next_run", 0)
    return counts
