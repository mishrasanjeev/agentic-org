# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A durable inbox sweep publishes only identifiers. Providers never carry authority claims."""

import asyncio
import uuid

from sqlalchemy import select, text

from core.channels.runtime import dispatch_one
from core.database import async_session_factory, get_tenant_session
from core.models.channel import ChannelDelivery
from core.models.tenant import Tenant
from core.tasks.async_runner import run_async
from core.tasks.celery_app import app


@app.task(name="core.tasks.channel_tasks.dispatch", acks_late=True)
def dispatch(tenant_id: str, binding_id: str) -> str:
    return run_async(dispatch_one(uuid.UUID(tenant_id), uuid.UUID(binding_id)))


async def _sweep() -> int:
    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = (await session.scalars(select(Tenant.id))).all()
    count = 0
    for tenant_id in tenant_ids:
        async with get_tenant_session(tenant_id) as session:
            bindings = (
                await session.scalars(
                    select(ChannelDelivery.binding_id)
                    .where(
                        ChannelDelivery.tenant_id == tenant_id, ChannelDelivery.status.in_(["pending", "processing"])
                    )
                    .distinct()
                    .limit(100)
                )
            ).all()
        for binding_id in bindings:
            # Broker failure leaves the durable pending row for the next sweep.
            await asyncio.to_thread(dispatch.apply_async, args=[str(tenant_id), str(binding_id)], queue="workflows")
            count += 1
    return count


@app.task(name="core.tasks.channel_tasks.sweep")
def sweep() -> int:
    return run_async(_sweep())
