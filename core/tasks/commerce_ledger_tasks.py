# SPDX-License-Identifier: Apache-2.0
"""Commerce ledger maintenance: apply due OACP retention dispositions (core/commerce/oacp_retention.py)."""

from __future__ import annotations

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _apply_oacp_retention_async() -> dict:
    """Every tenant's due dispositions; a tenant's failure is isolated. A no-op unless the flag is on."""
    from core.commerce.oacp_retention import apply_due_dispositions
    from core.config import settings
    from core.tasks.audit_chain_tasks import _tenant_ids

    if not settings.oacp_retention_apply_enabled:
        return {"enabled": False, "tenants": 0, "deleted": 0, "errors": 0}
    tenant_ids = await _tenant_ids()
    deleted = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            outcome = await apply_due_dispositions(tenant_id)
        # enterprise-gate: broad-except-ok reason=one-tenants-retention-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning("oacp_retention_tenant_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
            continue
        deleted += outcome["deleted"]
        errors += outcome["errors"]
    logger.info("oacp_retention_applied", tenants=len(tenant_ids), deleted=deleted, errors=errors)
    return {"enabled": True, "tenants": len(tenant_ids), "deleted": deleted, "errors": errors}


@app.task(name="core.tasks.commerce_ledger_tasks.apply_oacp_retention")
def apply_oacp_retention() -> dict:
    """Daily: delete OACP review manifests whose retention disposition is due (behind the flag)."""
    return run_async(_apply_oacp_retention_async())
