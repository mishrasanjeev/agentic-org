# SPDX-License-Identifier: Apache-2.0
"""Model gateway maintenance: prune routing records past their retention period."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _prune_model_gateway_records_async(days: int | None = None) -> dict:
    """Delete each tenant's routing records older than the retention period; a tenant's failure is isolated."""
    from sqlalchemy import delete, select, text

    from core.config import settings
    from core.database import async_session_factory, get_tenant_session
    from core.models.model_gateway_record import ModelGatewayRecord
    from core.models.tenant import Tenant

    retention = settings.model_gateway_records_retention_days if days is None else days
    cutoff = datetime.now(UTC) - timedelta(days=max(int(retention), 1))
    # model_gateway_records is tenant-scoped under row-level security:
    # enumerate the tenant catalogue, then prune each tenant through its own session.
    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = list((await session.scalars(select(Tenant.id).where(Tenant.deleted_at.is_(None)))).all())
    deleted = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            async with get_tenant_session(tenant_id) as session:
                result = await session.execute(
                    delete(ModelGatewayRecord).where(
                        ModelGatewayRecord.tenant_id == tenant_id, ModelGatewayRecord.created_at < cutoff
                    )
                )
                deleted += int(getattr(result, "rowcount", 0) or 0)
        # enterprise-gate: broad-except-ok reason=one-tenants-prune-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning(
                "model_gateway_records_prune_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__
            )
    logger.info("model_gateway_records_pruned", cutoff=cutoff.isoformat(), deleted=deleted, errors=errors)
    return {"cutoff": cutoff.isoformat(), "tenants": len(tenant_ids), "deleted": deleted, "errors": errors}


@app.task(name="core.tasks.gateway_tasks.prune_model_gateway_records")
def prune_model_gateway_records(days: int | None = None) -> dict:
    """Daily: drop routing records older than AGENTICORG_MODEL_GATEWAY_RECORDS_RETENTION_DAYS."""
    return run_async(_prune_model_gateway_records_async(days))
