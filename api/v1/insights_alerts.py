# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights alerts: list the caller's alerts and acknowledge one in place.

``GET /insights/alerts`` returns the open alerts the caller's role owns
(administrators: every alert), most severe and newest first; ``state=acked``
lists acknowledged ones still in force. ``POST /insights/alerts/{id}/ack``
acknowledges an open alert the caller may see (a person, not an API key):
it stays until its condition clears and is not emailed again. One signed
``insights.alert.ack`` audit row per acknowledgement. While Insights is off
for the tenant every route answers 404 ``insights_disabled``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import _actor, _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import alerts
from core.insights.audit import audit_entry
from core.ownership import caller_from_request

router = APIRouter(prefix="/insights/alerts", tags=["Insights"])


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.alerts.read",
)
async def list_alerts(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    state: Annotated[Literal["open", "acked"], Query()] = "open",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """The caller's open (or acknowledged) alerts."""
    from sqlalchemy import case, select

    from core.models.insights import InsightsAlert as A

    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    async with get_tenant_session(tid) as session:
        rows = (
            await session.execute(
                select(A)
                .where(
                    A.tenant_id == tid,
                    A.state == state,
                    alerts.visible_clause(A, caller.role, is_admin=caller.is_admin),
                )
                .order_by(case((A.severity == "act", 0), else_=1), A.fired_at.desc())
                .limit(limit)
            )
        ).scalars()
        return {"alerts": [alerts.alert_dict(a) for a in rows]}


@router.post("/{alert_id}/ack")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.alerts.ack",
    rate_limit="reporting-admin",
    idempotency="idempotent-first-write-wins",
    audit_event="insights.alert.ack",
)
async def ack_alert(
    request: Request,
    alert_id: Annotated[uuid.UUID, Path()],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Acknowledge an open alert. Acknowledging it again answers the first acknowledgement."""
    from sqlalchemy import select

    from core.models.insights import InsightsAlert as A

    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    if caller.is_machine or caller.user_id is None:
        raise HTTPException(403, "insights_alert_ack_needs_a_person")
    async with get_tenant_session(tid) as session:
        alert = (
            await session.execute(
                select(A)
                .where(
                    A.tenant_id == tid,
                    A.id == alert_id,
                    alerts.visible_clause(A, caller.role, is_admin=caller.is_admin),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if alert is None:
            raise HTTPException(404, "alert_not_found")
        if alert.state == "resolved":
            raise HTTPException(409, detail={"error": "alert_resolved", "message": "this alert already cleared"})
        if alert.state == "open":
            alert.state = "acked"
            alert.acked_by = caller.user_id
            alert.acked_at = datetime.now(UTC)
            session.add(
                audit_entry(
                    tid,
                    event_type="insights.alert.ack",
                    actor_id=_actor(request),
                    action="ack",
                    resource_type="insights_alert",
                    resource_id=str(alert.id),
                    details={"rule_key": alert.rule_key, "entity_ref": alert.entity_ref, "severity": alert.severity},
                )
            )
        await session.flush()
        return alerts.alert_dict(alert)
