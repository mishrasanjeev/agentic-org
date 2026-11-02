# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights metrics for the role views: operations, HR, marketing and commerce.

Each resolver reads the tenant's own tables in the tenant session (forced
row-level security) for the period's tenant-local days, or now for snapshots,
and answers ``no_data`` with a hint when there is nothing recorded. The run and
approval metrics these views also show come from ``core/insights/metrics.py``,
limited to the view's domains.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from core.insights.metrics import MetricContext, bucket_start, bucket_starts, day_bounds, no_data

TOP = 5
OPEN_WORKFLOW_DONE = ("completed", "failed", "cancelled", "rejected", "timed_out")


def _bounds(ctx: MetricContext) -> tuple[Any, Any]:
    return day_bounds(ctx.period.start, ctx.zone)[0], day_bounds(ctx.period.end, ctx.zone)[1]


def _compare_bounds(ctx: MetricContext) -> tuple[Any, Any] | None:
    if ctx.compare is None:
        return None
    return day_bounds(ctx.compare.start, ctx.zone)[0], day_bounds(ctx.compare.end, ctx.zone)[1]


async def _daily_counts(ctx: MetricContext, column: Any, *clauses: Any) -> dict[date, int]:
    from sqlalchemy import Date, cast, func, select

    lo, hi = _bounds(ctx)
    day = cast(func.timezone(ctx.reporting.timezone, column), Date)
    rows = await ctx.session.execute(select(day, func.count()).where(*clauses, column >= lo, column < hi).group_by(day))
    return {d: int(n) for d, n in rows}


def _series(ctx: MetricContext, daily: dict[date, int]) -> list[dict[str, Any]]:
    buckets: dict[date, int] = defaultdict(int)
    for d, n in daily.items():
        buckets[bucket_start(d, ctx.period.granularity)] += n
    return [{"t": start.isoformat(), "value": buckets.get(start, 0)} for start in bucket_starts(ctx.period)]


async def _count_between(ctx: MetricContext, column: Any, bounds: tuple[Any, Any], *clauses: Any) -> int:
    from sqlalchemy import func, select

    lo, hi = bounds
    return int(
        (await ctx.session.execute(select(func.count()).where(*clauses, column >= lo, column < hi))).scalar_one() or 0
    )


# ------------------------------------------------------------------ operations


async def resolve_workflow_runs(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.workflow import WorkflowRun as W

    started = func.coalesce(W.started_at, W.created_at)
    daily = await _daily_counts(ctx, started, W.tenant_id == ctx.tenant_id)
    total = sum(daily.values())
    if not total:
        return no_data("workflow_runs", "No workflow run started in this period.")
    lo, hi = _bounds(ctx)
    by_status = (
        await ctx.session.execute(
            select(W.status, func.count())
            .where(W.tenant_id == ctx.tenant_id, started >= lo, started < hi)
            .group_by(W.status)
        )
    ).all()
    out: dict[str, Any] = {
        "value": total,
        "series": _series(ctx, daily),
        "rows": sorted(({"label": s, "value": int(n)} for s, n in by_status), key=lambda r: -r["value"]),
        "source": "workflow_runs",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = await _count_between(ctx, started, previous, W.tenant_id == ctx.tenant_id) or None
    return out


async def resolve_workflows_stalled(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.workflow import WorkflowDefinition as D
    from core.models.workflow import WorkflowRun as W

    clauses = [
        W.tenant_id == ctx.tenant_id,
        W.completed_at.is_(None),
        W.status.notin_(OPEN_WORKFLOW_DONE),
        W.timeout_at.is_not(None),
        W.timeout_at < ctx.now,
    ]
    count = int((await ctx.session.execute(select(func.count()).select_from(W).where(*clauses))).scalar_one() or 0)
    rows = (
        await ctx.session.execute(
            select(D.name, W.status, W.timeout_at)
            .join(D, D.id == W.workflow_def_id)
            .where(*clauses)
            .order_by(W.timeout_at.asc())
            .limit(TOP)
        )
    ).all()
    return {
        "value": count,
        "rows": [
            {"label": name, "status": status, "timeout_at": timeout.isoformat() if timeout else None}
            for name, status, timeout in rows
        ],
        "source": "workflow_runs",
        "data_quality": "measured",
    }


async def resolve_connectors_unhealthy(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.connector_config import ConnectorConfig as C

    rows = (
        await ctx.session.execute(
            select(C.health_status, func.count()).where(C.tenant_id == ctx.tenant_id).group_by(C.health_status)
        )
    ).all()
    if not rows:
        return no_data("connector_configs", "No connector is configured. Add one from the Connectors page.")
    counts = {str(status or "unknown"): int(n) for status, n in rows}
    return {
        "value": counts.get("unhealthy", 0),
        "rows": sorted(({"label": k, "value": v} for k, v in counts.items()), key=lambda r: -r["value"]),
        "source": "connector_configs",
        "data_quality": "measured",
    }


# ------------------------------------------------------------------ HR


async def resolve_pt_returns(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.professional_tax import ProfessionalTaxReturn as R

    clauses = [R.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(R.company_id == ctx.company_id)
    daily = await _daily_counts(ctx, R.submitted_at, *clauses)
    _lo, hi = _bounds(ctx)
    waiting = int(
        (
            await ctx.session.execute(select(func.count()).where(*clauses, R.status != "submitted", R.created_at < hi))
        ).scalar_one()
        or 0
    )
    total = sum(daily.values())
    if not total and not waiting:
        return no_data("professional_tax_returns", "No professional tax return was prepared or filed in this period.")
    out: dict[str, Any] = {
        "value": total,
        "series": _series(ctx, daily),
        "rows": [{"label": "submitted", "value": total}, {"label": "not_submitted", "value": waiting}],
        "source": "professional_tax_returns",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = await _count_between(ctx, R.submitted_at, previous, *clauses) or None
    return out


# ------------------------------------------------------------------ marketing


async def resolve_leads(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.lead_pipeline import LeadPipeline as L

    daily = await _daily_counts(ctx, L.created_at, L.tenant_id == ctx.tenant_id)
    total = sum(daily.values())
    if not total:
        return no_data("lead_pipeline", "No lead was added in this period.")
    lo, hi = _bounds(ctx)
    stages = (
        await ctx.session.execute(
            select(L.stage, func.count())
            .where(L.tenant_id == ctx.tenant_id, L.created_at >= lo, L.created_at < hi)
            .group_by(L.stage)
        )
    ).all()
    out: dict[str, Any] = {
        "value": total,
        "series": _series(ctx, daily),
        "rows": sorted(({"label": s, "value": int(n)} for s, n in stages), key=lambda r: -r["value"]),
        "source": "lead_pipeline",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = await _count_between(ctx, L.created_at, previous, L.tenant_id == ctx.tenant_id) or None
    return out


async def resolve_content_drafts(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.content_draft import ContentDraft as C

    daily = await _daily_counts(ctx, C.created_at, C.tenant_id == ctx.tenant_id)
    total = sum(daily.values())
    if not total:
        return no_data("content_drafts", "No content draft was created in this period.")
    lo, hi = _bounds(ctx)
    statuses = (
        await ctx.session.execute(
            select(C.status, func.count())
            .where(C.tenant_id == ctx.tenant_id, C.created_at >= lo, C.created_at < hi)
            .group_by(C.status)
        )
    ).all()
    out: dict[str, Any] = {
        "value": total,
        "series": _series(ctx, daily),
        "rows": sorted(({"label": s, "value": int(n)} for s, n in statuses), key=lambda r: -r["value"]),
        "source": "content_drafts",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = await _count_between(ctx, C.created_at, previous, C.tenant_id == ctx.tenant_id) or None
    return out


async def _median_cycle(ctx: MetricContext, bounds: tuple[Any, Any]) -> tuple[int, float | None]:
    from sqlalchemy import extract, func, select

    from core.models.content_draft import ContentDraft as C

    lo, hi = bounds
    millis = extract("epoch", C.decided_at - C.created_at) * 1000
    row = (
        await ctx.session.execute(
            select(func.count(), func.percentile_cont(0.5).within_group(millis)).where(
                C.tenant_id == ctx.tenant_id, C.decided_at.is_not(None), C.decided_at >= lo, C.decided_at < hi
            )
        )
    ).one()
    return int(row[0] or 0), (round(float(row[1]), 1) if row[1] is not None else None)


async def resolve_draft_cycle_time(ctx: MetricContext) -> dict[str, Any]:
    count, median = await _median_cycle(ctx, _bounds(ctx))
    if not count:
        return no_data("content_drafts", "No content draft was approved or rejected in this period.")
    out: dict[str, Any] = {
        "value": median,
        "rows": [{"label": "decided", "value": count}],
        "source": "content_drafts",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        prev_count, prev_median = await _median_cycle(ctx, previous)
        out["compare_value"] = prev_median if prev_count else None
    return out


# ------------------------------------------------------------------ commerce


FUNNEL = ("carts", "checkouts", "checkouts_completed", "orders", "orders_paid")


async def resolve_commerce_funnel(ctx: MetricContext) -> dict[str, Any]:
    """Carts, checkout sessions, completed checkouts, orders and paid orders created in the period."""
    from sqlalchemy import func, select

    from core.models.commerce_payments import CommercePaymentOperationRow as P
    from core.models.commerce_ucp import CommerceCartRow as Cart
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as S
    from core.models.commerce_ucp import CommerceOrderRow as O

    lo, hi = _bounds(ctx)

    async def count(model: Any, *extra: Any) -> int:
        return int(
            (
                await ctx.session.execute(
                    select(func.count()).where(
                        model.tenant_id == ctx.tenant_id, model.created_at >= lo, model.created_at < hi, *extra
                    )
                )
            ).scalar_one()
            or 0
        )

    carts = await count(Cart)
    checkouts = await count(S)
    completed = await count(S, S.status == "completed")
    orders = await count(O)
    paid = int(
        (
            await ctx.session.execute(
                select(func.count(func.distinct(P.order_id))).where(
                    P.tenant_id == ctx.tenant_id,
                    P.order_id.is_not(None),
                    P.operation == "capture",
                    P.phase == "succeeded",
                    P.created_at >= lo,
                    P.created_at < hi,
                )
            )
        ).scalar_one()
        or 0
    )
    values = dict(zip(FUNNEL, (carts, checkouts, completed, orders, paid), strict=True))
    if not any(values.values()):
        return no_data(
            "commerce_carts+commerce_checkout_sessions+commerce_orders", "No shopping activity in this period."
        )
    return {
        "value": orders,
        "rows": [{"label": k, "value": v} for k, v in values.items()],
        "source": "commerce_carts+commerce_checkout_sessions+commerce_orders+commerce_payment_operations",
        "data_quality": "measured",
    }


async def resolve_commerce_orders(ctx: MetricContext) -> dict[str, Any]:
    from core.models.commerce_ucp import CommerceOrderRow as O

    daily = await _daily_counts(ctx, O.created_at, O.tenant_id == ctx.tenant_id)
    total = sum(daily.values())
    if not total:
        return no_data("commerce_orders", "No order was placed in this period.")
    out: dict[str, Any] = {
        "value": total,
        "series": _series(ctx, daily),
        "source": "commerce_orders",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = await _count_between(ctx, O.created_at, previous, O.tenant_id == ctx.tenant_id) or None
    return out


async def resolve_webhook_failures(ctx: MetricContext) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.commerce_ucp import CommerceOrderWebhookDeliveryRow as Wd

    lo, hi = _bounds(ctx)
    rows = (
        await ctx.session.execute(
            select(Wd.status, func.count())
            .where(Wd.tenant_id == ctx.tenant_id, Wd.created_at >= lo, Wd.created_at < hi)
            .group_by(Wd.status)
        )
    ).all()
    counts = {str(s): int(n) for s, n in rows}
    if not counts:
        return no_data("commerce_order_webhook_deliveries", "No order webhook was sent in this period.")
    return {
        "value": counts.get("dead_lettered", 0),
        "rows": [{"label": k, "value": counts.get(k, 0)} for k in ("delivered", "pending", "dead_lettered")],
        "source": "commerce_order_webhook_deliveries",
        "data_quality": "measured",
    }
