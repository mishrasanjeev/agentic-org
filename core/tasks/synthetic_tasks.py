# SPDX-License-Identifier: Apache-2.0
"""Synthetic checks: the scheduled sweep that runs every tenant's due checks, and the result prune."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

# A sweep runs at most this many checks for one tenant; the rest wait for the next sweep.
MAX_PER_TENANT = 10


async def _tenants_with_checks() -> list[Any]:
    """The tenants that have an enabled check (read across tenants; each is then run under its own session)."""
    from sqlalchemy import select, text

    from core.database import async_session_factory
    from core.models.synthetic_check import SyntheticCheck

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        rows = await session.scalars(
            select(SyntheticCheck.tenant_id).where(SyntheticCheck.enabled.is_(True)).distinct()
        )
        return list(rows.all())


async def _result_tenants() -> list[Any]:
    from sqlalchemy import select, text

    from core.database import async_session_factory
    from core.models.synthetic_check import SyntheticCheckResult

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        return list((await session.scalars(select(SyntheticCheckResult.tenant_id).distinct())).all())


async def _run_synthetic_checks_async(*, max_per_tenant: int = MAX_PER_TENANT) -> dict:
    """Run every tenant's due checks; a tenant's or a check's failure is isolated."""
    from core.config import settings
    from observability import synthetic

    if not settings.synthetic_checks_enabled:
        return {"enabled": False, "tenants": 0, "ran": 0, "not_ok": 0, "errors": 0}
    tenant_ids = await _tenants_with_checks()
    ran = 0
    not_ok = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            due = await synthetic.due_checks(tenant_id, limit=max_per_tenant)
        # enterprise-gate: broad-except-ok reason=one-tenants-unreadable-checks-never-stop-the-sweep-logged
        except Exception as exc:
            errors += 1
            logger.warning("synthetic_checks_unreadable", tenant_id=str(tenant_id), error_type=type(exc).__name__)
            continue
        for check in due:
            try:
                result = await synthetic.run_check(check)
            # enterprise-gate: broad-except-ok reason=one-checks-storage-failure-never-stops-the-sweep-logged
            except Exception as exc:
                errors += 1
                logger.warning(
                    "synthetic_check_run_failed",
                    tenant_id=str(tenant_id),
                    check_id=str(check.id),
                    error_type=type(exc).__name__,
                )
                continue
            if result is None:
                # Another sweep or a run by hand holds the check.
                continue
            ran += 1
            if result.status != "ok":
                not_ok += 1
    logger.info("synthetic_checks_ran", tenants=len(tenant_ids), ran=ran, not_ok=not_ok, errors=errors)
    return {"enabled": True, "tenants": len(tenant_ids), "ran": ran, "not_ok": not_ok, "errors": errors}


async def _prune_synthetic_results_async(days: int | None = None) -> dict:
    """Delete results older than the retention period, each tenant through its own session."""
    from sqlalchemy import delete

    from core.config import settings
    from core.database import get_tenant_session
    from core.models.synthetic_check import SyntheticCheckResult

    retention = settings.synthetic_checks_retention_days if days is None else days
    cutoff = datetime.now(UTC) - timedelta(days=max(int(retention), 1))
    tenant_ids = await _result_tenants()
    deleted = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            async with get_tenant_session(tenant_id) as session:
                removed = await session.execute(
                    delete(SyntheticCheckResult).where(
                        SyntheticCheckResult.tenant_id == tenant_id, SyntheticCheckResult.started_at < cutoff
                    )
                )
                deleted += int(getattr(removed, "rowcount", 0) or 0)
        # enterprise-gate: broad-except-ok reason=one-tenants-prune-failure-never-stops-the-sweep-logged
        except Exception as exc:
            errors += 1
            logger.warning("synthetic_results_prune_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
    logger.info("synthetic_results_pruned", cutoff=cutoff.isoformat(), deleted=deleted, errors=errors)
    return {"cutoff": cutoff.isoformat(), "tenants": len(tenant_ids), "deleted": deleted, "errors": errors}


@app.task(name="core.tasks.synthetic_tasks.run_synthetic_checks")
def run_synthetic_checks() -> dict:
    """Every five minutes: run each tenant's due synthetic checks (AGENTICORG_SYNTHETIC_CHECKS_ENABLED)."""
    return run_async(_run_synthetic_checks_async())


@app.task(name="core.tasks.synthetic_tasks.prune_synthetic_results")
def prune_synthetic_results(days: int | None = None) -> dict:
    """Daily: drop results older than AGENTICORG_SYNTHETIC_CHECKS_RETENTION_DAYS."""
    return run_async(_prune_synthetic_results_async(days))
