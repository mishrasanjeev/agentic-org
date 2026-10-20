# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The customer's actions list: everything the agent prepared, ran, refused or saw cancelled."""

from __future__ import annotations

import uuid
from typing import Any

MAX_LIST = 50


def action_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "task_class": row.task_class,
        "intent": row.intent,
        "parameters": dict(row.parameters or {}),
        "amount": row.amount,
        "status": row.status,
        "level_applied": int(row.level_applied),
        "requires": row.requires,
        "approved": bool(row.approval),
        "explanation": dict(row.explanation or {}),
        "execution": dict(row.execution or {}) if row.execution else None,
        "channel": row.channel,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


async def list_actions(
    tenant_id: uuid.UUID, customer_ref: str, *, limit: int = MAX_LIST, status: str | None = None
) -> list[Any]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    query = select(CustomerAction).where(
        CustomerAction.tenant_id == tenant_id, CustomerAction.customer_ref == customer_ref
    )
    if status:
        query = query.where(CustomerAction.status == status)
    query = query.order_by(CustomerAction.created_at.desc()).limit(max(1, min(limit, MAX_LIST)))
    async with get_tenant_session(tenant_id) as session:
        return list((await session.execute(query)).scalars())


async def get_action(tenant_id: uuid.UUID, customer_ref: str, action_id: uuid.UUID) -> Any | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    async with get_tenant_session(tenant_id) as session:
        return (
            await session.execute(
                select(CustomerAction).where(
                    CustomerAction.tenant_id == tenant_id,
                    CustomerAction.customer_ref == customer_ref,
                    CustomerAction.id == action_id,
                )
            )
        ).scalar_one_or_none()
