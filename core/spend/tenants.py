# SPDX-License-Identifier: Apache-2.0
"""The tenants spend's cross-tenant jobs walk: every tenant not deleted."""

from __future__ import annotations

import uuid


async def active_tenant_ids() -> list[uuid.UUID]:
    """Ids of tenants with ``deleted_at IS NULL``, read with row security off in a plain session.

    The enumeration pattern of ``core/tasks/gateway_tasks.py``, with the
    ``deleted_at`` filter the prune task deliberately omits: a deleted tenant
    meters nothing and is never swept.
    """
    from sqlalchemy import select, text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        rows = (await session.scalars(select(Tenant.id).where(Tenant.deleted_at.is_(None)).order_by(Tenant.id))).all()
    return list(rows)
