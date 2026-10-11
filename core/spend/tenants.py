# SPDX-License-Identifier: Apache-2.0
"""The tenants spend's cross-tenant jobs walk: every tenant not deleted."""

from __future__ import annotations

import uuid
from datetime import datetime


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


async def tenants_since(moment: datetime) -> list[tuple[uuid.UUID, bool]]:
    """``(id, active)`` of every tenant not deleted before ``moment``; ``active`` is ``deleted_at IS NULL``.

    GPU allocation counts the calls of a tenant deleted after an hour began,
    so its share of that hour stays with the platform instead of moving onto
    the tenants that remain; it writes records for active tenants only.
    """
    from sqlalchemy import or_, select, text

    from core.database import async_session_factory
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        rows = (
            await session.execute(
                select(Tenant.id, Tenant.deleted_at.is_(None))
                .where(or_(Tenant.deleted_at.is_(None), Tenant.deleted_at >= moment))
                .order_by(Tenant.id)
            )
        ).all()
    return [(row[0], bool(row[1])) for row in rows]
