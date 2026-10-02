# SPDX-License-Identifier: Apache-2.0
"""Run timeline maintenance: prune stored run spans past their retention period."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _prune_run_spans_async(days: int | None = None) -> dict:
    """Delete every tenant's run spans older than the retention period; a tenant's failure is isolated."""
    from sqlalchemy import delete, select, text

    from core.config import settings
    from core.database import async_session_factory, get_tenant_session
    from core.models.run_span import RunSpan
    from core.models.tenant import Tenant

    retention = settings.tracing_timeline_retention_days if days is None else days
    cutoff = datetime.now(UTC) - timedelta(days=max(int(retention), 1))
    # run_spans is tenant-scoped under row-level security: enumerate the whole
    # tenant catalogue, deleted tenants included (their rows have no cascade
    # and must still age out), then prune each tenant through its own session.
    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())
    deleted = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            async with get_tenant_session(tenant_id) as session:
                result = await session.execute(
                    delete(RunSpan).where(RunSpan.tenant_id == tenant_id, RunSpan.created_at < cutoff)
                )
                deleted += int(getattr(result, "rowcount", 0) or 0)
        # enterprise-gate: broad-except-ok reason=one-tenants-prune-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning("run_spans_prune_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
    logger.info("run_spans_pruned", cutoff=cutoff.isoformat(), deleted=deleted, errors=errors)
    return {"cutoff": cutoff.isoformat(), "tenants": len(tenant_ids), "deleted": deleted, "errors": errors}


@app.task(name="core.tasks.timeline_tasks.prune_run_spans")
def prune_run_spans(days: int | None = None) -> dict:
    """Daily: drop run spans older than AGENTICORG_TRACING_TIMELINE_RETENTION_DAYS."""
    return run_async(_prune_run_spans_async(days))
