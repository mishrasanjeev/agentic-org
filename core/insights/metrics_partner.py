# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights metrics for the Partner view: a CA firm's client companies, their filings and what the firm billed.

A partner (CA firm) is a tenant whose clients are its ``companies``. Every
resolver reads the tenant's own rows in the tenant session (forced row-level
security). Runs per company keep to the caller's domains; billing figures are
the firm's own invoices and payments (``ca_client_invoices``,
``ca_client_payments``), in the invoices' currency. No revenue is estimated.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from core.insights.metrics import MetricContext, day_bounds, money, no_data, pct

OVERDUE_PENALTY = 25
PENDING_PENALTY = 5


def client_health(stored_score: int | None, *, pending_filings: int, overdue_filings: int) -> int:
    """The client's health after live filing risk (the same rule as the legacy partner dashboard)."""
    base = 100 if stored_score is None else max(0, min(int(stored_score), 100))
    penalty = max(overdue_filings, 0) * OVERDUE_PENALTY + max(pending_filings, 0) * PENDING_PENALTY
    return max(0, min(100, base - penalty))


def _bounds(ctx: MetricContext) -> tuple[Any, Any]:
    return day_bounds(ctx.period.start, ctx.zone)[0], day_bounds(ctx.period.end, ctx.zone)[1]


async def _company_rows(ctx: MetricContext) -> list[Any]:
    from sqlalchemy import select

    from core.models.company import Company

    clauses = [Company.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(Company.id == ctx.company_id)
    return list(
        (
            await ctx.session.execute(
                select(Company.id, Company.name, Company.is_active, Company.client_health_score)
                .where(*clauses)
                .order_by(Company.name)
            )
        ).all()
    )


async def _counts_by_company(ctx: MetricContext, model: Any, *clauses: Any) -> dict[Any, int]:
    from sqlalchemy import func, select

    rows = await ctx.session.execute(
        select(model.company_id, func.count()).where(model.tenant_id == ctx.tenant_id, *clauses).group_by(
            model.company_id
        )
    )
    return {company: int(n) for company, n in rows}


async def resolve_partner_clients(ctx: MetricContext) -> dict[str, Any]:
    """Active client companies; rows give each client's health, the period's runs and success, and open work."""
    from sqlalchemy import func, select

    from core.models.ca_client_billing import CAClientInvoice as I
    from core.models.compliance_deadline import ComplianceDeadline as D
    from core.models.filing_approval import FilingApproval as F
    from core.models.insights import RunFact as R

    companies = await _company_rows(ctx)
    if not companies:
        return no_data("companies", "No client company is recorded. Add one from the Companies page.")
    today = ctx.today
    overdue = await _counts_by_company(ctx, D, D.filed.is_(False), D.due_date < today)
    pending = await _counts_by_company(ctx, F, F.status == "pending")
    outstanding = {
        company: Decimal(total or 0)
        for company, total in await ctx.session.execute(
            select(I.company_id, func.sum(I.balance_due))
            .where(I.tenant_id == ctx.tenant_id, I.balance_due > 0, I.status.notin_(("void", "cancelled", "draft")))
            .group_by(I.company_id)
        )
    }
    lo, hi = _bounds(ctx)
    run_clauses = [R.tenant_id == ctx.tenant_id, R.company_id.is_not(None), R.started_at >= lo, R.started_at < hi]
    if ctx.domains is not None:
        run_clauses.append(R.domain.in_(list(ctx.domains)))
    runs: dict[Any, dict[str, int]] = defaultdict(lambda: {"runs": 0, "completed": 0, "failed": 0})
    for company, status, n in await ctx.session.execute(
        select(R.company_id, R.status, func.count()).where(*run_clauses).group_by(R.company_id, R.status)
    ):
        entry = runs[company]
        entry["runs"] += int(n)
        if status in ("completed", "failed"):
            entry[status] += int(n)
    rows = []
    for company_id, name, active, stored in companies:
        r = runs.get(company_id, {"runs": 0, "completed": 0, "failed": 0})
        rows.append(
            {
                "label": name,
                "company_id": str(company_id),
                "active": bool(active),
                "health": client_health(
                    stored, pending_filings=pending.get(company_id, 0), overdue_filings=overdue.get(company_id, 0)
                ),
                "runs": r["runs"],
                "success_rate": pct(r["completed"], r["completed"] + r["failed"]),
                "overdue_deadlines": overdue.get(company_id, 0),
                "pending_filings": pending.get(company_id, 0),
                "outstanding": money(outstanding.get(company_id, Decimal("0")), 2),
            }
        )
    rows.sort(key=lambda row: (row["health"], row["label"]))
    return {
        "value": sum(1 for row in rows if row["active"]),
        "rows": rows,
        "source": "companies+run_facts+compliance_deadlines+filing_approvals+ca_client_invoices",
        "data_quality": "measured",
    }


async def resolve_partner_filings(ctx: MetricContext) -> dict[str, Any]:
    """Filing approvals waiting for a decision now, by filing type."""
    from sqlalchemy import func, select

    from core.models.filing_approval import FilingApproval as F

    clauses = [F.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(F.company_id == ctx.company_id)
    by_type = (
        await ctx.session.execute(
            select(F.filing_type, func.count()).where(*clauses, F.status == "pending").group_by(F.filing_type)
        )
    ).all()
    if not by_type:
        any_filing = (await ctx.session.execute(select(func.count()).where(*clauses))).scalar_one()
        if not any_filing:
            return no_data("filing_approvals", "No filing has been prepared for approval yet.")
        return {"value": 0, "rows": [], "source": "filing_approvals", "data_quality": "measured"}
    counts = sorted(((str(t), int(n)) for t, n in by_type), key=lambda kv: (-kv[1], kv[0]))
    rows = [{"label": label, "value": n} for label, n in counts]
    total = sum(n for _, n in counts)
    return {"value": total, "rows": rows, "source": "filing_approvals", "data_quality": "measured"}


async def resolve_partner_billing(ctx: MetricContext) -> dict[str, Any]:
    """Invoiced to clients in the period; rows give invoiced, received and outstanding (now), per currency."""
    from sqlalchemy import func, select

    from core.models.ca_client_billing import CAClientInvoice as I
    from core.models.ca_client_billing import CAClientPayment as P

    clauses = [I.tenant_id == ctx.tenant_id, I.status.notin_(("void", "cancelled", "draft"))]
    pay_clauses = [P.tenant_id == ctx.tenant_id]
    if ctx.company_id is not None:
        clauses.append(I.company_id == ctx.company_id)
        pay_clauses.append(P.company_id == ctx.company_id)
    start, end = ctx.period.start, ctx.period.end
    invoiced = dict(
        (
            await ctx.session.execute(
                select(I.currency, func.sum(I.total))
                .where(*clauses, I.issue_date >= start, I.issue_date <= end)
                .group_by(I.currency)
            )
        ).all()
    )
    received = dict(
        (
            await ctx.session.execute(
                select(I.currency, func.sum(P.amount))
                .join(I, I.id == P.invoice_id)
                .where(*pay_clauses, P.payment_date >= start, P.payment_date <= end)
                .group_by(I.currency)
            )
        ).all()
    )
    outstanding = dict(
        (
            await ctx.session.execute(
                select(I.currency, func.sum(I.balance_due)).where(*clauses, I.balance_due > 0).group_by(I.currency)
            )
        ).all()
    )
    currencies = sorted(set(invoiced) | set(received) | set(outstanding))
    if not currencies:
        return no_data(
            "ca_client_invoices",
            "No client invoice is recorded. Invoices are raised from a client's billing page.",
        )
    rows = [
        {
            "label": str(cur),
            "invoiced": money(invoiced.get(cur), 2) or 0.0,
            "received": money(received.get(cur), 2) or 0.0,
            "outstanding": money(outstanding.get(cur), 2) or 0.0,
        }
        for cur in currencies
    ]
    main = "INR" if "INR" in currencies else currencies[0]
    out: dict[str, Any] = {
        "value": money(invoiced.get(main), 2) or 0.0,
        "unit": "inr" if main == "INR" else "usd",
        "rows": rows,
        "source": "ca_client_invoices+ca_client_payments",
        "data_quality": "measured",
    }
    if len(currencies) > 1:
        out["hint"] = f"The figure is in {main}; rows give each currency the firm invoices in."
    return out
