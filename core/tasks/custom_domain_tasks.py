# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains: advance domains waiting on their mapping or certificate.

Every few minutes: list (platform context, SELECT only) the domains in
``pending_mapping``, ``pending_certificate`` or ``pending_unmap``, then advance
each in its own tenant's session (``api.v1.merchant_custom_domains.progress_domain``:
the Cloud Run Admin API with automap on, else the HTTPS certificate probe; an
unmap item is closed at once when another domain holds its host) and email the
operator about any queue item not yet notified. A no-op unless
``AGENTICORG_CUSTOM_DOMAINS_ENABLED`` is on.
"""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

BATCH_LIMIT = 50


async def _advance_custom_domains_async() -> dict:
    from sqlalchemy import select

    from api.v1.merchant_custom_domains import notify_operator, progress_domain
    from core.commerce.custom_domains import custom_domains_enabled
    from core.database import get_platform_session
    from core.models.merchant_custom_domain import MerchantCustomDomainRow

    if not custom_domains_enabled():
        return {"enabled": False, "checked": 0, "live": 0, "errors": 0}
    async with get_platform_session() as session:
        rows = (
            await session.execute(
                select(MerchantCustomDomainRow.id, MerchantCustomDomainRow.tenant_id)
                .where(MerchantCustomDomainRow.status.in_(("pending_mapping", "pending_certificate", "pending_unmap")))
                .order_by(MerchantCustomDomainRow.last_checked_at.asc().nulls_first())
                .limit(BATCH_LIMIT)
            )
        ).all()
    live = 0
    errors = 0
    for domain_id, tenant_id in rows:
        try:
            status = await progress_domain(domain_id, tenant_id=str(tenant_id))
            if status in ("pending_mapping", "pending_unmap"):
                await notify_operator(domain_id, tenant_id=str(tenant_id))
        # enterprise-gate: broad-except-ok reason=one-domains-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning("custom_domain_advance_failed", error_type=type(exc).__name__)
            continue
        live += status == "live"
    logger.info("custom_domains_advanced", checked=len(rows), live=live, errors=errors)
    return {"enabled": True, "checked": len(rows), "live": live, "errors": errors}


@app.task(name="core.tasks.custom_domain_tasks.advance_custom_domains")
def advance_custom_domains() -> dict:
    """Every five minutes: move custom domains toward live (behind the flag)."""
    return run_async(_advance_custom_domains_async())
