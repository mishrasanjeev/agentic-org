# SPDX-License-Identifier: Apache-2.0
"""The customer's permissions page: every mandate the bank's agent holds, and the way to every outside agent.

The bank's agent acts under mandates stored here. Outside agents act under
grants the authority service holds; when the deployment is connected to it,
a principal session is opened for the customer and the dashboard link is
returned, so one page lists both. Revoking a mandate demotes the task to the
default level at once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import external_keys, grantex_base_url_for_env, settings
from core.customer_agent import autonomy

logger = structlog.get_logger()


def mandate_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "task_class": row.task_class,
        "autonomy_level": int(row.autonomy_level),
        "level_name": autonomy.LEVEL_NAMES.get(int(row.autonomy_level), str(row.autonomy_level)),
        "per_action_cap": row.per_action_cap,
        "period_cap": row.period_cap,
        "period": row.period,
        "currency": row.currency,
        "counterparties": list(row.counterparties or []),
        "window": dict(row.window or {}),
        "status": row.status,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def list_mandates(tenant_id: uuid.UUID, customer_ref: str) -> list[Any]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerMandate

    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(CustomerMandate)
                .where(CustomerMandate.tenant_id == tenant_id, CustomerMandate.customer_ref == customer_ref)
                .order_by(CustomerMandate.created_at.desc())
            )
        ).scalars()
        return list(rows)


async def set_mandate(tenant_id: uuid.UUID, customer_ref: str, cleaned: dict[str, Any], *, created_by: str) -> Any:
    """Create the mandate for a task class, revoking any active one for the same class (one envelope per task)."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerMandate

    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        existing = (
            await session.execute(
                select(CustomerMandate).where(
                    CustomerMandate.tenant_id == tenant_id,
                    CustomerMandate.customer_ref == customer_ref,
                    CustomerMandate.task_class == cleaned["task_class"],
                    CustomerMandate.status == "active",
                )
            )
        ).scalars()
        for row in existing:
            row.status = "revoked"
            row.revoked_at = now
            row.updated_at = now
        mandate = CustomerMandate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            customer_ref=customer_ref,
            task_class=cleaned["task_class"],
            autonomy_level=int(cleaned["autonomy_level"]),
            per_action_cap=cleaned.get("per_action_cap"),
            period_cap=cleaned.get("period_cap"),
            period=cleaned.get("period", "month"),
            counterparties=cleaned.get("counterparties", []),
            window=cleaned.get("window", {}),
            expires_at=cleaned.get("expires_at"),
            created_by=created_by[:128],
        )
        session.add(mandate)
    logger.info("customer_mandate_set", task_class=mandate.task_class, level=mandate.autonomy_level)
    return mandate


async def revoke_mandate(tenant_id: uuid.UUID, customer_ref: str, mandate_id: uuid.UUID) -> Any | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerMandate

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(CustomerMandate).where(
                    CustomerMandate.tenant_id == tenant_id,
                    CustomerMandate.customer_ref == customer_ref,
                    CustomerMandate.id == mandate_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.status == "active":
            now = datetime.now(UTC)
            row.status = "revoked"
            row.revoked_at = now
            row.updated_at = now
    logger.info("customer_mandate_revoked", task_class=row.task_class)
    return row


def _principal_dashboard(principal_id: str) -> dict[str, Any]:
    """A principal session on the authority service for the customer; the dashboard URL lists the outside agents."""
    from grantex import CreatePrincipalSessionParams, Grantex

    client = Grantex(api_key=external_keys.grantex_api_key, base_url=grantex_base_url_for_env())
    try:
        response = client.principal_sessions.create(
            CreatePrincipalSessionParams(principal_id=principal_id, expires_in="30m")
        )
    finally:
        try:
            client.close()
        # enterprise-gate: broad-except-ok reason=client-close-never-fails-the-page
        except Exception:
            logger.debug("grantex_client_close_failed")
    return {"status": "available", "dashboard_url": response.dashboard_url, "expires_at": response.expires_at}


async def outside_agents(principal_id: str | None) -> dict[str, Any]:
    """Where the customer sees and revokes outside agents, or why that is not available here."""
    if not principal_id:
        return {"status": "unavailable", "reason": "no principal", "dashboard_url": None}
    if not str(external_keys.grantex_api_key or "").strip() or not bool(settings.customer_agent_outside_agents):
        return {"status": "not_connected", "reason": "authority service not connected", "dashboard_url": None}
    import asyncio

    try:
        return await asyncio.to_thread(_principal_dashboard, principal_id)
    # enterprise-gate: broad-except-ok reason=degrades-to-unavailable-status-on-the-page-and-logged
    except Exception as exc:
        logger.warning("customer_principal_session_failed", error_type=type(exc).__name__)
        return {"status": "unavailable", "reason": type(exc).__name__, "dashboard_url": None}


async def permissions_page(session_row: Any) -> dict[str, Any]:
    rows = await list_mandates(session_row.tenant_id, session_row.customer_ref)
    return {
        "customer_ref": session_row.customer_ref,
        "autonomy_ceiling": autonomy.ceiling(),
        "levels": {str(k): v for k, v in autonomy.LEVEL_NAMES.items()},
        "mandates": [mandate_view(row) for row in rows],
        "outside_agents": await outside_agents(session_row.principal_id),
    }
