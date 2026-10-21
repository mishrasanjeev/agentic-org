# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Bounded recovery of durable upload indexing on the existing maintenance queue."""

import structlog
from sqlalchemy import select, text

from core.database import async_session_factory
from core.models.tenant import Tenant
from core.rag import upload_recovery
from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger(__name__)


async def _sweep() -> int:
    if not upload_recovery.enabled():
        return 0
    async with async_session_factory() as session:
        # Enumerate identifiers only. All document reads and writes below use tenant RLS.
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = (await session.scalars(select(Tenant.id))).all()
    count = 0
    for tenant_id in tenant_ids:
        for document_id in await upload_recovery.candidates(tenant_id):
            try:
                await upload_recovery.process(tenant_id, document_id)
            # enterprise-gate: broad-except-ok reason=isolates-document-errors-leaves-durable-recovery-state
            except Exception:
                logger.warning("knowledge_index_retry_failed", exc_info=True)
            count += 1
    return count


@app.task(name="core.tasks.knowledge_tasks.sweep", acks_late=True)
def sweep() -> int:
    return run_async(_sweep())
