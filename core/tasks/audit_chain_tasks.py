# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Tamper-evident audit maintenance: seal each tenant's chain, and verify every chain daily."""

from __future__ import annotations

from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()

MAX_ROUNDS = 20


async def _tenant_ids() -> list[Any]:
    """The whole tenant catalogue, deleted tenants included: their rows still exist and still seal."""
    from sqlalchemy import select, text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        return list((await session.scalars(select(Tenant.id))).all())


async def _commerce_tenant_ids() -> list[Any]:
    """Tenants with a commerce merchant config: sealed under AGENTICORG_AUDIT_CHAIN_DEFAULT_ON.

    The catalogue is enumerated as for every sweep, then each tenant is asked
    inside its own tenant-bound session, so this works under a NOBYPASSRLS role.
    """
    from core.database import get_tenant_session
    from core.governance import audit_chain

    tenant_ids: list[Any] = []
    for tenant_id in await _tenant_ids():
        async with get_tenant_session(tenant_id) as session:
            if await audit_chain.has_commerce_config(session, tenant_id):
                tenant_ids.append(tenant_id)
    return tenant_ids


async def _seal_audit_chains_async(*, max_rounds: int = MAX_ROUNDS) -> dict:
    """Seal every tenant's unsealed rows, a bounded number of batches each; a tenant's failure is isolated.

    With ``audit_chain_enabled`` off and ``audit_chain_default_on`` on, only the
    tenants that have a commerce merchant config are sealed.
    """
    from core.config import settings
    from core.governance import audit_chain

    if settings.audit_chain_enabled:
        tenant_ids = await _tenant_ids()
        scope = "all"
    elif settings.audit_chain_default_on:
        tenant_ids = await _commerce_tenant_ids()
        scope = "commerce"
    else:
        return {"enabled": False, "tenants": 0, "sealed": 0, "errors": 0}
    from core.governance.audit_witness import witness_head

    sealed = 0
    errors = 0
    witnessed: dict[str, int] = {}
    for tenant_id in tenant_ids:
        try:
            tenant_sealed = 0
            outcome = None
            for _ in range(max(1, max_rounds)):
                outcome = await audit_chain.seal(tenant_id)
                tenant_sealed += outcome.sealed
                if not outcome.more:
                    break
            sealed += tenant_sealed
        # enterprise-gate: broad-except-ok reason=one-tenants-sealing-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning("audit_chain_seal_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
            continue
        if tenant_sealed and outcome is not None:
            # After the sealing committed: the new head goes to Grantex (AGENTICORG_AUDIT_WITNESS_ENABLED).
            try:
                answer = await witness_head(
                    tenant_id,
                    head_seq=outcome.head.seq,
                    head_hash=outcome.head.hash,
                    anchored_at=outcome.head.sealed_at,
                )
            # enterprise-gate: broad-except-ok reason=a-witness-failure-never-undoes-or-stops-the-sealing-logged
            except Exception as exc:
                logger.warning("audit_chain_witness_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
                answer = {"status": "error"}
            witnessed[answer["status"]] = witnessed.get(answer["status"], 0) + 1
    logger.info(
        "audit_chains_sealed", tenants=len(tenant_ids), sealed=sealed, errors=errors, scope=scope, witness=witnessed
    )
    return {"enabled": True, "tenants": len(tenant_ids), "sealed": sealed, "errors": errors}


async def _verify_audit_chains_async() -> dict:
    """Verify every tenant's chain end to end; a break is logged with its sequence number and reason."""
    from core.governance import audit_chain
    from observability.metrics import audit_chain_verifications_total

    tenant_ids = await _tenant_ids()
    broken: list[dict] = []
    verified = 0
    errors = 0
    for tenant_id in tenant_ids:
        try:
            result = await audit_chain.verify(tenant_id)
        # enterprise-gate: broad-except-ok reason=one-tenants-verification-failure-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            audit_chain_verifications_total.labels(result="error").inc()
            logger.warning("audit_chain_verify_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__)
            continue
        audit_chain_verifications_total.labels(result=result.status).inc()
        verified += result.verified
        if result.first_break is not None:
            broken.append({"tenant_id": str(tenant_id), **result.first_break.to_dict()})
            logger.error(
                "audit_chain_broken",
                tenant_id=str(tenant_id),
                seq=result.first_break.seq,
                reason=result.first_break.reason,
                head_seq=result.head.seq,
            )
    logger.info("audit_chains_verified", tenants=len(tenant_ids), verified=verified, broken=len(broken), errors=errors)
    return {"tenants": len(tenant_ids), "verified": verified, "broken": broken, "errors": errors}


@app.task(name="core.tasks.audit_chain_tasks.seal_audit_chains")
def seal_audit_chains() -> dict:
    """Every five minutes: link each tenant's new audit rows onto its chain (AGENTICORG_AUDIT_CHAIN_ENABLED)."""
    return run_async(_seal_audit_chains_async())


@app.task(name="core.tasks.audit_chain_tasks.verify_audit_chains")
def verify_audit_chains() -> dict:
    """Daily: recompute every tenant's chain and report the first break of each."""
    return run_async(_verify_audit_chains_async())
