# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights: the Executive and Finance views, single metrics and their exports.

``GET /insights/status`` says whether Insights is on for the caller's tenant,
which views the caller may read and the tenant's reporting settings; it
answers while Insights is off. ``GET /insights/catalog`` lists the metric
definitions the caller may read. ``GET /insights/views/{view}`` returns a
view's tiles, charts and what needs a person in one call;
``GET /insights/metrics/{key}`` one metric with a granularity and grouping;
``GET /insights/metrics/{key}/export`` that metric as CSV, XLSX or JSON
(audited).

Every route needs ``insights:read`` (administrators pass). The view check is
made here, server side: ``executive`` is for administrators, ``finance`` for
administrators and the CFO; anyone else gets 403 ``insights_view_forbidden``.
Run metrics are limited to the caller's domains; cost metrics are
tenant-wide for the roles that may read them. A ``company_id`` must be one of
the tenant's companies (422 otherwise; never silently tenant-wide). While
Insights is off for the tenant (``AGENTICORG_INSIGHTS_ENABLED`` or the
tenant's ``insights_dashboard`` flag), every route but the status answers 404
``insights_disabled``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.insights import export as exports
from core.insights import registry
from core.insights.audit import audit_entry
from core.insights.bi_access import bi_configured
from core.insights.flags import insights_enabled_for
from core.insights.freshness import rollup_late
from core.insights.metrics import (
    MetricContext,
    Period,
    PeriodError,
    apply_statement_timeout,
    local_today,
    make_period,
    needs_you,
    previous_period,
)
from core.insights.platform import is_platform_operator
from core.insights.reporting_settings import load_reporting_settings
from core.ownership import Caller, caller_from_request

router = APIRouter(prefix="/insights", tags=["Insights"])

Compare = Literal["previous", "none"]
Granularity = Literal["day", "week", "month"]
ExportFormat = Literal["csv", "xlsx", "json"]
FromDay = Annotated[date | None, Query(alias="from")]
ToDay = Annotated[date | None, Query()]
CompanyId = Annotated[str | None, Query(max_length=64)]
GroupBy = Annotated[str | None, Query(max_length=32)]


def _tenant(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(401, "Invalid tenant context") from None


async def _require_enabled(tid: uuid.UUID) -> None:
    if not await insights_enabled_for(tid):
        raise HTTPException(404, "insights_disabled")


def _views_for(caller: Caller) -> list[str]:
    return registry.allowed_views(caller.role, is_admin=caller.is_admin)


def _forbidden() -> HTTPException:
    return HTTPException(403, "insights_view_forbidden")


def _unprocessable(error: str, message: str) -> HTTPException:
    return HTTPException(422, detail={"error": error, "message": message})


async def _company(tid: uuid.UUID, company_id: str | None) -> uuid.UUID | None:
    """The requested company, proven to be the tenant's; 422 for anything else."""
    if company_id is None or not company_id.strip():
        return None
    from api.v1.agents import _require_company_for_tenant

    try:
        return await _require_company_for_tenant(tid, company_id.strip())
    except HTTPException as exc:
        if exc.status_code in (400, 404):
            raise _unprocessable("invalid_company_id", "company_id is not one of this tenant's companies") from None
        raise


def _metric(key: str, views: list[str]) -> registry.Metric:
    metric = registry.METRICS.get(key)
    if metric is None:
        raise HTTPException(404, detail={"error": "unknown_metric", "message": f"no metric {key!r}"})
    if not set(views).intersection(metric.views):
        raise _forbidden()
    return metric


def _group_by(metric: registry.Metric, group_by: str | None) -> str | None:
    if group_by is None or not group_by.strip():
        return None
    if group_by not in metric.group_by:
        allowed = ", ".join(metric.group_by) or "none"
        raise _unprocessable("group_by_not_allowed", f"{metric.key} may be grouped by: {allowed}")
    return group_by


def _period(start: date | None, end: date | None, today: date, granularity: str | None) -> Period:
    try:
        return make_period(start, end, today=today, granularity=granularity)
    except PeriodError as exc:
        raise _unprocessable("invalid_period", str(exc)) from None


async def _context(
    session: Any,
    tid: uuid.UUID,
    caller: Caller,
    *,
    start: date | None,
    end: date | None,
    compare: str,
    company: uuid.UUID | None,
    granularity: str | None = None,
    group_by: str | None = None,
) -> MetricContext:
    await apply_statement_timeout(session)
    reporting = await load_reporting_settings(session, tid)
    today = local_today(reporting.zone())
    period = _period(start, end, today, granularity)
    return MetricContext(
        session=session,
        tenant_id=tid,
        reporting=reporting,
        period=period,
        compare=previous_period(period) if compare == "previous" else None,
        company_id=company,
        domains=caller.domains,
        group_by=group_by,
    )


def _actor(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or "")


# Rules whose items the needs-you list already shows live (pending approvals, budgets).
_LIVE_IN_NEEDS_YOU = frozenset({"approvals.pending", "cost.budget"})


async def _alert_items(session: Any, tid: uuid.UUID, caller: Caller) -> list[dict[str, Any]]:
    """The caller's open alerts as needs-you items (each can be acknowledged by its id)."""
    from core.insights.alerts import open_alerts

    items = []
    for alert in await open_alerts(session, tid, caller.role, is_admin=caller.is_admin):
        if alert["rule_key"] in _LIVE_IN_NEEDS_YOU:
            continue
        items.append(
            {
                "kind": "alert",
                "id": alert["id"],
                "title": alert["title"],
                "link": alert["link"],
                "state": alert["severity"],
                "rule_key": alert["rule_key"],
            }
        )
    return items


# Domains filed under another name: the COO's "ops" also covers agents in "operations".
DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {"ops": ("operations",)}


def view_domains(view: str, caller_domains: list[str] | None) -> list[str] | None:
    """The domains a view's run and approval metrics read: the view's own, within the caller's."""
    own = registry.VIEW_DOMAINS.get(view)
    if own is None:
        return caller_domains
    if caller_domains is None:
        return list(own)
    allowed = set(caller_domains)
    for domain in caller_domains:
        allowed.update(DOMAIN_ALIASES.get(domain, ()))
    return [d for d in own if d in allowed]


# ---------------------------------------------------------------- status and catalog


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.status.read",
)
async def insights_status(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether Insights is on for the tenant, the caller's views and the reporting settings."""
    tid = _tenant(tenant_id)
    caller = caller_from_request(request)
    enabled = await insights_enabled_for(tid)
    async with get_tenant_session(tid) as session:
        reporting = await load_reporting_settings(session, tid)
        late = await rollup_late(session, tid, reporting.zone(), datetime.now(UTC)) if enabled else False
    return {
        "enabled": enabled,
        "views": _views_for(caller) if enabled else [],
        # Yesterday's daily figures are not rolled up yet (the landing views show a banner).
        "late_data": late,
        # The caller may open the cross-tenant platform view (never raises; see core/insights/platform.py).
        "platform_operator": await is_platform_operator(request),
        "settings": reporting.model_dump(),
        # Per-tenant BI database access is on and configured, for a human administrator.
        "bi_access": bi_configured() and caller.is_admin and not caller.is_machine,
    }


@router.get("/catalog")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.catalog.read",
)
async def insights_catalog(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The definitions of the metrics the caller may read, and the views they appear in."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    views = _views_for(caller_from_request(request))
    allowed = set(views)
    return {
        "views": [
            {
                "key": key,
                "title": registry.VIEWS[key].title,
                "tiles": list(registry.VIEWS[key].tiles),
                "charts": list(registry.VIEWS[key].charts),
            }
            for key in views
        ],
        "metrics": [metric.describe(allowed) for metric in registry.readable_metrics(views)],
    }


# ---------------------------------------------------------------- views and metrics


@router.get("/views/{view}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.view.read",
)
async def insights_view(
    view: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    from_: FromDay = None,
    to: ToDay = None,
    compare: Annotated[Compare, Query()] = "previous",
    company_id: CompanyId = None,
) -> dict[str, Any]:
    """A view's tiles, charts and what needs a person, for the period (default: the last 30 local days)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    if view not in registry.VIEWS:
        raise HTTPException(404, detail={"error": "unknown_view", "message": f"no view {view!r}"})
    caller = caller_from_request(request)
    if view not in _views_for(caller):
        raise _forbidden()
    company = await _company(tid, company_id)
    spec = registry.VIEWS[view]
    async with get_tenant_session(tid) as session:
        ctx = await _context(session, tid, caller, start=from_, end=to, compare=compare, company=company)
        ctx.domains = view_domains(view, caller.domains)
        resolved: dict[str, dict[str, Any]] = {}
        for key in spec.keys:
            resolved[key] = await registry.resolve(registry.METRICS[key], ctx)
        attention = await needs_you(ctx)
        attention = [*await _alert_items(session, tid, caller), *attention]
    return {
        "view": view,
        "period": ctx.period.as_dict(),
        "compare": ctx.compare.range_dict() if ctx.compare else None,
        "tiles": [resolved[key] for key in spec.tiles],
        "charts": [resolved[key] for key in spec.charts],
        "needs_you": attention,
    }


@router.get("/metrics/{key}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.metric.read",
)
async def insights_metric(
    key: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    from_: FromDay = None,
    to: ToDay = None,
    compare: Annotated[Compare, Query()] = "previous",
    company_id: CompanyId = None,
    granularity: Annotated[Granularity | None, Query()] = None,
    group_by: GroupBy = None,
) -> dict[str, Any]:
    """One metric for the period, at a granularity, optionally grouped by an allowed dimension."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    metric = _metric(key, _views_for(caller))
    grouping = _group_by(metric, group_by)
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        ctx = await _context(
            session,
            tid,
            caller,
            start=from_,
            end=to,
            compare=compare,
            company=company,
            granularity=granularity,
            group_by=grouping,
        )
        return await registry.resolve(metric, ctx)


@router.get("/metrics/{key}/export")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-export",
    idempotency="read-only",
    audit_event="insights.export",
)
async def insights_export(
    key: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    format: Annotated[ExportFormat, Query()] = "csv",  # noqa: A002 - the public query parameter name
    from_: FromDay = None,
    to: ToDay = None,
    compare: Annotated[Compare, Query()] = "none",
    company_id: CompanyId = None,
    granularity: Annotated[Granularity | None, Query()] = None,
    group_by: GroupBy = None,
) -> Response:
    """The metric's data as a file (at most 50,000 rows), with one signed ``insights.export`` audit row."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = caller_from_request(request)
    metric = _metric(key, _views_for(caller))
    grouping = _group_by(metric, group_by)
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        ctx = await _context(
            session,
            tid,
            caller,
            start=from_,
            end=to,
            compare=compare,
            company=company,
            granularity=granularity,
            group_by=grouping,
        )
        response = await registry.resolve(metric, ctx)
        rows = exports.export_rows(response)
        try:
            exports.check_size(rows)
        except exports.ExportTooLargeError as exc:
            raise HTTPException(413, detail={"error": "export_too_large", "hint": str(exc), "rows": exc.rows}) from None
        filters = {
            "from": ctx.period.start.isoformat(),
            "to": ctx.period.end.isoformat(),
            "granularity": ctx.period.granularity,
            "compare": compare,
            "company_id": str(company) if company else None,
            "group_by": grouping,
        }
        content = await asyncio.to_thread(exports.render, format, rows, filters, response)
        session.add(
            audit_entry(
                tid,
                event_type="insights.export",
                actor_id=_actor(request),
                action="export",
                resource_type="insights_metric",
                resource_id=metric.key,
                details={"metric": metric.key, "format": format, "filters": filters, "row_count": len(rows)},
            )
        )
    name = exports.filename(metric.key, response["period"], format)
    return Response(
        content=content,
        media_type=exports.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )
