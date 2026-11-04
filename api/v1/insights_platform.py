# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights platform view: cross-tenant aggregates and the platform posture, for the platform operator only.

Every route depends on ``core.insights.platform.require_platform_operator``:
an active, MFA-enrolled administrator of the tenant named by
``AGENTICORG_PLATFORM_OPERATOR_TENANT_ID``, signed in with a human session.
Anyone else gets 404, as if the routes did not exist (the ``platform`` scope
family also requires ``agenticorg:admin`` before the handler runs).

- ``GET /platform/insights/summary?from&to``: per-day totals and range totals
  (tenants with runs, runs, failure rate, tokens, cost in USD).
- ``GET /platform/insights/tenants?from&to``: one row per live tenant (name,
  plan, runs, failure rate, cost, active agents, last active day, consecutive
  reconciled rollup nights against the 14-night gate).
- ``GET /platform/insights/posture``: schema head, forced tenant RLS, the
  connected role and the last 24 hours of health snapshots.

Ranges are UTC days, at most 92 days apart (422 otherwise). The figures come
only from the aggregate SECURITY DEFINER functions of migration
``v6z107_insights_platform``. Each read writes one signed audit row in the
operator's tenant (the range only; no tenant id or figure).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.route_metadata import route_meta
from core.database import get_platform_session, get_tenant_session
from core.insights import benchmarks, platform
from core.insights.audit import audit_entry
from core.insights.platform import PlatformOperator, PlatformRangeError, require_platform_operator

router = APIRouter(prefix="/platform/insights", tags=["Insights platform"])

FromDay = Annotated[date | None, Query(alias="from")]
ToDay = Annotated[date | None, Query()]
Operator = Annotated[PlatformOperator, Depends(require_platform_operator)]


def _range(start: date | None, end: date | None) -> tuple[date, date]:
    try:
        return platform.platform_range(start, end)
    except PlatformRangeError as exc:
        raise HTTPException(422, detail={"error": "invalid_period", "message": str(exc)}) from None


async def _audit(operator: PlatformOperator, event: str, details: dict[str, Any]) -> None:
    async with get_tenant_session(operator.tenant_id) as session:
        session.add(
            audit_entry(
                operator.tenant_id,
                event_type=event,
                actor_id=str(operator.user_id),
                action="read",
                resource_type="insights_platform",
                resource_id=event.split(".")[2],
                details=details,
            )
        )


@router.get("/summary")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="platform.insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="platform.insights.summary.read",
)
async def platform_summary(
    operator: Operator, from_: FromDay = None, to: ToDay = None
) -> dict[str, Any]:
    """Daily platform totals, the range totals, and the dashboard's own export and rollup figures."""
    start, end = _range(from_, to)
    async with get_platform_session() as session:
        daily = await platform.platform_daily(session, start, end)
        tenants = await platform.platform_tenants(session, start, end)
        operations = await platform.platform_operations(session, start, end)
        opted_in = await benchmarks.participation(session)
    period = {"from": start.isoformat(), "to": end.isoformat()}
    await _audit(operator, "platform.insights.summary.read", {"period": period})
    return {
        "period": period,
        "totals": platform.totals(daily, tenants),
        "series": daily,
        "operations": operations,
        "benchmarks": {"opted_in": opted_in, "min_tenants": benchmarks.MIN_TENANTS},
        "reconciled_gate_nights": platform.RECONCILED_GATE_NIGHTS,
    }


@router.get("/tenants")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="platform.insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="platform.insights.tenants.read",
)
async def platform_tenants(
    operator: Operator, from_: FromDay = None, to: ToDay = None
) -> dict[str, Any]:
    """One row per live tenant, busiest first (at most 2,000 rows)."""
    start, end = _range(from_, to)
    async with get_platform_session() as session:
        rows = await platform.platform_tenants(session, start, end)
    period = {"from": start.isoformat(), "to": end.isoformat()}
    await _audit(operator, "platform.insights.tenants.read", {"period": period, "row_count": len(rows)})
    return {
        "period": period,
        "reconciled_gate_nights": platform.RECONCILED_GATE_NIGHTS,
        "tenants": rows[: platform.MAX_TENANT_ROWS],
        "truncated": len(rows) > platform.MAX_TENANT_ROWS,
    }


@router.get("/posture")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="platform.insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="platform.insights.posture.read",
)
async def platform_posture(operator: Operator) -> dict[str, Any]:
    """The platform posture checklist."""
    async with get_platform_session() as session:
        result = await platform.posture(session)
    await _audit(operator, "platform.insights.posture.read", {"ok": result["ok"]})
    return result


_DATASETS: dict[str, tuple[str, str]] = {
    "daily": ("Platform runs by day", "Per day: tenants with runs, runs, failed runs, tokens and cost (USD)."),
    "tenants": ("Platform tenants", "Per live tenant: plan, runs, failures, cost, active agents and the rollup gate."),
}


@router.get("/export")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="platform.insights.read",
    rate_limit="reporting-export",
    idempotency="read-only",
    audit_event="platform.insights.export.read",
)
async def platform_export(
    operator: Operator,
    dataset: Annotated[str, Query(pattern="^(daily|tenants)$")] = "daily",
    format: Annotated[str, Query(pattern="^(csv|xlsx|json)$")] = "csv",  # noqa: A002 - the public query parameter name
    from_: FromDay = None,
    to: ToDay = None,
) -> Response:
    """The platform view's daily figures or tenant table as CSV, XLSX or JSON (cross-tenant aggregates only)."""
    import asyncio

    from core.insights import export as exports

    start, end = _range(from_, to)
    async with get_platform_session() as session:
        rows = await (platform.platform_daily if dataset == "daily" else platform.platform_tenants)(session, start, end)
    period = {"from": start.isoformat(), "to": end.isoformat(), "granularity": "day"}
    title, definition = _DATASETS[dataset]
    response = {
        "key": f"platform.{dataset}",
        "title": title,
        "definition": definition,
        "unit": "count",
        "currency": "USD",
        "source": "insights_daily+insights_rollup_state+tenants",
        "data_quality": "measured" if rows else "no_data",
        "period": period,
    }
    filters = {"from": period["from"], "to": period["to"]}
    content = await asyncio.to_thread(exports.render, format, rows, filters, response)
    await _audit(operator, "platform.insights.export.read", {"dataset": dataset, "format": format, "period": period,
                                                         "row_count": len(rows)})
    name = exports.filename(f"platform-{dataset}", period, format)
    return Response(
        content=content,
        media_type=exports.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
