# SPDX-License-Identifier: Apache-2.0
"""Transaction-local RLS context for the internal durable workflow repositories."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def tenant_transaction(factory: Any, tenant_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    async with factory() as session, session.begin():
        await bind_transaction_tenant(session, tenant_id)
        yield session


async def bind_transaction_tenant(session: AsyncSession, tenant_id: uuid.UUID | None) -> None:
    from core.database import _bind_tenant_context

    tenant = str(uuid.UUID(str(tenant_id))) if tenant_id is not None else ""
    await _bind_tenant_context(session, tenant, "")


async def lookup_tenants(factory: Any, tenant_id: uuid.UUID | None) -> AsyncIterator[uuid.UUID | None]:
    if tenant_id is not None:
        yield tenant_id
        return

    # Trusted resume/timeout jobs carry an engine run ID, not client authority.
    # Find its scope from the server tenant registry, preserving RLS for every
    # lookup. Never use Redis or an incoming message to select that authority.
    # The unscoped pass supports legacy rows only where the DB policy permits it.
    yield None
    from core.models.tenant import Tenant

    cursor = None
    while True:
        async with tenant_transaction(factory, None) as session:
            statement = select(Tenant.id).where(Tenant.deleted_at.is_(None)).order_by(Tenant.id).limit(100)
            if cursor is not None:
                statement = statement.where(Tenant.id > cursor)
            batch = list((await session.scalars(statement)).all())
        if not batch:
            return
        for tenant in batch:
            yield tenant
        cursor = batch[-1]
