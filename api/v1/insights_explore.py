# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights explorers (Level 2 tables) and run evidence (Level 4).

``GET /insights/explorers`` lists the explorers the caller may open with their
columns. ``GET /insights/explore/{key}`` returns one page of an explorer for
the period, filters (``filter[column]=value``), sort and page;
``GET /insights/explore/{key}/export`` the filtered rows as CSV, XLSX or JSON
(at most 50,000; audited). ``GET /insights/runs/{fact_id}?at=...`` returns one
run's evidence.

Every route needs ``insights:read``. Which explorers a role may open is
decided here, server side (``core/insights/explore_registry.py``); any other
gets 403 ``insights_explorer_forbidden``. Rows are limited to the caller's
domains (a COO's ``ops`` includes agents filed under ``operations``) and,
with ``company_id``, to that company. While Insights is off for the tenant,
every route answers 404 ``insights_disabled``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.insights import CompanyId, FromDay, ToDay, _actor, _company, _context, _require_enabled, _tenant
from core.database import get_tenant_session
from core.insights import explore_registry
from core.insights import explorers as ex
from core.insights import export as exports
from core.insights.audit import audit_entry
from core.insights.evidence import EvidenceNotFoundError, parse_at, run_evidence
from core.ownership import Caller, caller_from_request

router = APIRouter(prefix="/insights", tags=["Insights"])

ExportFormat = Literal["csv", "xlsx", "json"]
# Domains filed under another name: the COO's "ops" also covers agents in "operations".
DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {"ops": ("operations",)}
_RESERVED = frozenset({"from", "to", "compare", "company_id", "format"})


def explorer_domains(domains: list[str] | None) -> list[str] | None:
    if domains is None:
        return None
    out = list(dict.fromkeys(domains))
    for domain in domains:
        for alias in DOMAIN_ALIASES.get(domain, ()):
            if alias not in out:
                out.append(alias)
    return out


def _caller(request: Request) -> Caller:
    caller = caller_from_request(request)
    return replace(caller, domains=explorer_domains(caller.domains))


def _explorer(key: str, caller: Caller) -> explore_registry.Explorer:
    explorer = explore_registry.EXPLORERS.get(key)
    if explorer is None:
        raise HTTPException(404, detail={"error": "unknown_explorer", "message": f"no explorer {key!r}"})
    if not explorer.readable_by(caller.role, is_admin=caller.is_admin):
        raise HTTPException(403, "insights_explorer_forbidden")
    return explorer


def _query(explorer: ex.Explorer, request: Request, *, export: bool = False) -> ex.ExploreQuery:
    params = {k: v for k, v in request.query_params.items() if k not in _RESERVED}
    try:
        query = ex.parse_query(explorer, params)
    except ex.ExploreQueryError as exc:
        raise HTTPException(422, detail={"error": exc.error, "message": exc.message}) from None
    if export:
        # One page with every row the export may carry, plus one to know it was too many.
        query = replace(query, page=1, page_size=exports.MAX_EXPORT_ROWS + 1)
    return query


async def _resolve(explorer: ex.Explorer, ctx: ex.ExploreContext) -> dict[str, Any]:
    try:
        return await ex.resolve(explorer, ctx)
    except ex.ExploreQueryError as exc:
        raise HTTPException(422, detail={"error": exc.error, "message": exc.message}) from None


@router.get("/explorers")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.explorers.read",
)
async def insights_explorers(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The explorers the caller may open, with their columns and filters."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    return {
        "explorers": [
            explorer.describe()
            for explorer in explore_registry.readable_explorers(caller.role, is_admin=caller.is_admin)
        ]
    }


@router.get("/explore/{key}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.explore.read",
)
async def insights_explore(
    key: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    from_: FromDay = None,
    to: ToDay = None,
    company_id: CompanyId = None,
) -> dict[str, Any]:
    """One page of an explorer for the period (default: the last 30 local days)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    explorer = _explorer(key, caller)
    query = _query(explorer, request)
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        metric_ctx = await _context(session, tid, caller, start=from_, end=to, compare="previous", company=company)
        ctx = ex.ExploreContext(metric=metric_ctx, query=query, role=caller.role, is_admin=caller.is_admin)
        return await _resolve(explorer, ctx)


@router.get("/explore/{key}/export")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-export",
    idempotency="read-only",
    audit_event="insights.export",
)
async def insights_explore_export(
    key: str,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    format: Annotated[ExportFormat, Query()] = "csv",  # noqa: A002 - the public query parameter name
    from_: FromDay = None,
    to: ToDay = None,
    company_id: CompanyId = None,
) -> Response:
    """Every row of the explorer with the filters applied (at most 50,000), with one signed audit row."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    explorer = _explorer(key, caller)
    query = _query(explorer, request, export=True)
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        metric_ctx = await _context(session, tid, caller, start=from_, end=to, compare="previous", company=company)
        ctx = ex.ExploreContext(metric=metric_ctx, query=query, role=caller.role, is_admin=caller.is_admin)
        response = await _resolve(explorer, ctx)
        if response["total"] > exports.MAX_EXPORT_ROWS:
            raise HTTPException(
                413,
                detail={"error": "export_too_large", "hint": exports.EXPORT_TOO_LARGE_HINT, "rows": response["total"]},
            )
        rows = ex.export_rows(explorer, response)
        period = response.get("period") or {"from": metric_ctx.today.isoformat(), "to": metric_ctx.today.isoformat()}
        filters: dict[str, Any] = {
            "from": period.get("from"),
            "to": period.get("to"),
            "company_id": str(company) if company else None,
            "sort": query.sort,
            "order": query.order,
            **{f"filter[{name}]": value for name, value in response["filters"].items()},
        }
        definition = {
            "key": f"explore.{explorer.key}",
            "title": explorer.title,
            "definition": explorer.definition,
            "source": response["source"],
            "data_quality": response["data_quality"],
            "computed_at": response["computed_at"],
            "period": period,
            "hint": response.get("hint"),
            "columns": [(c.key, c.title, c.description) for c in explorer.columns],
        }
        content = await asyncio.to_thread(exports.render, format, rows, filters, definition)
        session.add(
            audit_entry(
                tid,
                event_type="insights.export",
                actor_id=_actor(request),
                action="export",
                resource_type="insights_explorer",
                resource_id=explorer.key,
                details={"explorer": explorer.key, "format": format, "filters": filters, "row_count": len(rows)},
            )
        )
    name = exports.filename(f"explore-{explorer.key}", period, format)
    return Response(
        content=content,
        media_type=exports.MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.get("/runs/{fact_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.read",
    rate_limit="reporting-read",
    idempotency="read-only",
    audit_event="insights.run_evidence.read",
)
async def insights_run_evidence(
    fact_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    at: Annotated[str | None, Query(max_length=64)] = None,
    company_id: CompanyId = None,
) -> dict[str, Any]:
    """One run's fact, model gateway records, spans, approvals and audit rows (no output text or prompts)."""
    tid = _tenant(tenant_id)
    await _require_enabled(tid)
    caller = _caller(request)
    _explorer("runs", caller)
    instant = parse_at(at)
    if instant is None:
        raise HTTPException(
            422, detail={"error": "invalid_at", "message": "at is the run's started_at as an ISO timestamp with offset"}
        )
    company = await _company(tid, company_id)
    async with get_tenant_session(tid) as session:
        ctx = await _context(session, tid, caller, start=None, end=None, compare="none", company=company)
        try:
            return await run_evidence(ctx, fact_id, instant)
        except EvidenceNotFoundError:
            raise HTTPException(404, detail={"error": "run_not_found", "message": "no such run for you"}) from None
