# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights budgets: the organisation's spend limits (``budget_alerts``), set by an administrator or the CFO.

A budget is a USD threshold for a day, a week or a month, for the whole
organisation or one company or cost centre, with a warn-at percentage. The
hourly budget evaluator (``core/billing/budget_evaluator.py``) emails the
tenant's own recipient when spend reaches it, and the Insights alert sweep
raises the in-app ``cost.budget`` alert (and ``cost.forecast_over_budget`` for a
tenant-wide monthly budget the month's forecast is above). Spend is read from
``agent_cost_ledger`` exactly as the evaluator reads it.

Administrators and the CFO change budgets; auditors read them. Every change is
a signed audit row with the values before and after. Email is the only
notification channel: ``slack`` and ``webhook`` once posted to one
platform-wide URL for every tenant and are refused.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PERIODS: tuple[str, ...] = ("daily", "weekly", "monthly")
CHANNELS: tuple[str, ...] = ("email",)
REFUSED_CHANNELS = frozenset({"slack", "webhook"})
MAX_NAME = 100
MAX_THRESHOLD = Decimal("10000000")
MAX_PER_TENANT = 200
MANAGERS = frozenset({"cfo"})
READERS = frozenset({"cfo", "auditor"})


class BudgetError(ValueError):
    def __init__(self, error: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status


def may_read(role: str, *, is_admin: bool) -> bool:
    return is_admin or (role or "").lower() in READERS


def may_manage(role: str, *, is_admin: bool) -> bool:
    return is_admin or (role or "").lower() in MANAGERS


def clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())
    if not name or len(name) > MAX_NAME:
        raise BudgetError("invalid_name", f"the name is 1 to {MAX_NAME} characters")
    return name


def clean_period(raw: Any) -> str:
    period = str(raw or "").strip().lower()
    if period not in PERIODS:
        raise BudgetError("invalid_period", f"period is one of {', '.join(PERIODS)}")
    return period


def clean_threshold(raw: Any) -> Decimal:
    if isinstance(raw, bool):
        raise BudgetError("invalid_threshold", "the threshold is an amount in USD")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise BudgetError("invalid_threshold", "the threshold is an amount in USD") from None
    if not value.is_finite() or value <= 0 or value > MAX_THRESHOLD:
        raise BudgetError("invalid_threshold", f"the threshold is more than 0 and at most {MAX_THRESHOLD:,} USD")
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise BudgetError("invalid_threshold", "the threshold is at least 0.01 USD")
    return rounded


def clean_warn_at(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise BudgetError("invalid_warn_at", "warn_at_percent is a whole number from 1 to 100")
    try:
        value = int(str(raw))
    except ValueError:
        raise BudgetError("invalid_warn_at", "warn_at_percent is a whole number from 1 to 100") from None
    if not 1 <= value <= 100:
        raise BudgetError("invalid_warn_at", "warn_at_percent is a whole number from 1 to 100")
    return value


def clean_channels(raw: Any) -> str:
    if raw is None:
        return "email"
    items = raw if isinstance(raw, list) else str(raw).split(",")
    channels = sorted({str(item).strip().lower() for item in items if str(item).strip()})
    refused = [c for c in channels if c in REFUSED_CHANNELS]
    if refused:
        raise BudgetError(
            "unsupported_channel",
            f"{', '.join(refused)} delivery is not offered for budgets: alerts are emailed and shown in Insights",
        )
    unknown = [c for c in channels if c not in CHANNELS]
    if unknown or not channels:
        raise BudgetError("unsupported_channel", "notify_channels is ['email']")
    return ",".join(channels)


def _uuid(raw: Any, error: str, label: str) -> uuid.UUID | None:
    if raw in (None, ""):
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise BudgetError(error, f"{label} is not an id") from None


async def _check_scope(
    session: Any, tenant_id: uuid.UUID, company_id: uuid.UUID | None, cost_center_id: uuid.UUID | None
) -> None:
    """The company and cost centre must be the tenant's own, and the cost centre in the company when both are set."""
    from sqlalchemy import select

    from core.models.company import Company
    from core.models.organization import CostCenter

    if company_id is not None:
        found = await session.execute(
            select(Company.id).where(Company.id == company_id, Company.tenant_id == tenant_id)
        )
        if found.scalar_one_or_none() is None:
            raise BudgetError("unknown_company", "no such company in this organisation", 404)
    if cost_center_id is not None:
        row = (
            await session.execute(
                select(CostCenter.id, CostCenter.company_id).where(
                    CostCenter.id == cost_center_id, CostCenter.tenant_id == tenant_id
                )
            )
        ).first()
        if row is None:
            raise BudgetError("unknown_cost_center", "no such cost centre in this organisation", 404)
        if company_id is not None and row.company_id != company_id:
            raise BudgetError("cost_center_not_in_company", "the cost centre belongs to another company")


async def _name_taken(session: Any, tenant_id: uuid.UUID, name: str, except_id: uuid.UUID | None = None) -> bool:
    from sqlalchemy import func, select

    from core.models.budget_alert import BudgetAlert

    query = select(BudgetAlert.id).where(
        BudgetAlert.tenant_id == tenant_id, func.lower(BudgetAlert.name) == name.lower()
    )
    if except_id is not None:
        query = query.where(BudgetAlert.id != except_id)
    return (await session.execute(query.limit(1))).first() is not None


def settings_of(row: Any) -> dict[str, Any]:
    """The stored values of a budget (what the audit row records)."""
    return {
        "name": row.name,
        "period": row.period,
        "threshold_usd": format(Decimal(row.threshold_usd).quantize(Decimal("0.01")), "f"),
        "warn_at_percent": int(row.warn_at_percent),
        "company_id": str(row.company_id) if row.company_id else None,
        "cost_center_id": str(row.cost_center_id) if row.cost_center_id else None,
        "notify_channels": row.notify_channels,
    }


async def spend_of(session: Any, tenant_id: uuid.UUID, row: Any, now: datetime) -> Decimal:
    """This period's ledger spend for one budget, as the evaluator sums it."""
    from sqlalchemy import func, select

    from core.billing.budget_evaluator import _period_start
    from core.models.agent import Agent, AgentCostLedger

    since = _period_start(row.period, now).replace(tzinfo=None)
    spend = select(func.coalesce(func.sum(AgentCostLedger.cost_usd), 0)).where(
        AgentCostLedger.tenant_id == tenant_id, AgentCostLedger.period_date >= since
    )
    if row.company_id is not None or row.cost_center_id is not None:
        spend = spend.join(Agent, Agent.id == AgentCostLedger.agent_id)
        if row.company_id is not None:
            spend = spend.where(Agent.company_id == row.company_id)
        if row.cost_center_id is not None:
            spend = spend.where(Agent.cost_center_id == row.cost_center_id)
    return Decimal((await session.execute(spend)).scalar_one() or 0)


def state_of(used_pct: float | None, warn_at: int) -> str:
    if used_pct is None:
        return "ok"
    if used_pct >= 100:
        return "breached"
    if used_pct >= warn_at:
        return "warn"
    return "ok"


async def serialize(session: Any, tenant_id: uuid.UUID, row: Any, now: datetime) -> dict[str, Any]:
    actual = await spend_of(session, tenant_id, row, now)
    threshold = Decimal(row.threshold_usd or 0)
    used = round(float(actual / threshold * 100), 1) if threshold > 0 else None
    return {
        "id": str(row.id),
        **settings_of(row),
        "currency": "USD",
        "spend_usd": format(actual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
        "used_pct": used,
        "state": state_of(used, int(row.warn_at_percent)),
        "forecast_alert": row.period == "monthly" and row.company_id is None and row.cost_center_id is None,
        "last_triggered_at": row.last_triggered_at.isoformat() if row.last_triggered_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def list_budgets(
    session: Any, tenant_id: uuid.UUID, *, company_id: uuid.UUID | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from core.models.budget_alert import BudgetAlert

    moment = now or datetime.now(UTC)
    query = select(BudgetAlert).where(BudgetAlert.tenant_id == tenant_id).order_by(BudgetAlert.name)
    if company_id is not None:
        query = query.where(BudgetAlert.company_id == company_id)
    rows = list((await session.execute(query)).scalars())
    return [await serialize(session, tenant_id, row, moment) for row in rows]


def _audit(tenant_id: uuid.UUID, actor: uuid.UUID, action: str, row_id: uuid.UUID, before: Any, after: Any) -> Any:
    from core.insights.audit import audit_entry

    return audit_entry(
        tenant_id,
        event_type=f"insights.budget.{action}",
        actor_id=str(actor),
        action={"created": "create", "updated": "update", "deleted": "delete"}[action],
        resource_type="budget_alert",
        resource_id=str(row_id),
        details={"before": before, "after": after},
    )


async def _owned(session: Any, tenant_id: uuid.UUID, budget_id: uuid.UUID, *, lock: bool = False) -> Any:
    from sqlalchemy import select

    from core.models.budget_alert import BudgetAlert

    query = select(BudgetAlert).where(BudgetAlert.tenant_id == tenant_id, BudgetAlert.id == budget_id)
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise BudgetError("unknown_budget", "no such budget", 404)
    return row


async def create_budget(
    session: Any, tenant_id: uuid.UUID, actor: uuid.UUID, body: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.budget_alert import BudgetAlert

    name = clean_name(body.get("name"))
    company_id = _uuid(body.get("company_id"), "invalid_company", "company_id")
    cost_center_id = _uuid(body.get("cost_center_id"), "invalid_cost_center", "cost_center_id")
    row = BudgetAlert(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        period=clean_period(body.get("period")),
        threshold_usd=clean_threshold(body.get("threshold_usd")),
        warn_at_percent=clean_warn_at(body.get("warn_at_percent", 80)),
        company_id=company_id,
        cost_center_id=cost_center_id,
        notify_channels=clean_channels(body.get("notify_channels")),
    )
    await _check_scope(session, tenant_id, company_id, cost_center_id)
    count = (
        await session.execute(select(func.count()).select_from(BudgetAlert).where(BudgetAlert.tenant_id == tenant_id))
    ).scalar_one()
    if int(count or 0) >= MAX_PER_TENANT:
        raise BudgetError("too_many_budgets", f"an organisation has at most {MAX_PER_TENANT} budgets", 409)
    if await _name_taken(session, tenant_id, name):
        raise BudgetError("duplicate_name", "a budget with this name exists", 409)
    session.add(row)
    await session.flush()
    session.add(_audit(tenant_id, actor, "created", row.id, None, settings_of(row)))
    await session.flush()
    await session.refresh(row)
    return await serialize(session, tenant_id, row, now or datetime.now(UTC))


async def update_budget(
    session: Any,
    tenant_id: uuid.UUID,
    budget_id: uuid.UUID,
    actor: uuid.UUID,
    changes: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not changes:
        raise BudgetError("nothing_to_change", "send at least one field to change")
    row = await _owned(session, tenant_id, budget_id, lock=True)
    before = settings_of(row)
    if "name" in changes:
        name = clean_name(changes["name"])
        if await _name_taken(session, tenant_id, name, except_id=row.id):
            raise BudgetError("duplicate_name", "a budget with this name exists", 409)
        row.name = name
    if "period" in changes:
        row.period = clean_period(changes["period"])
    if "threshold_usd" in changes:
        row.threshold_usd = clean_threshold(changes["threshold_usd"])
    if "warn_at_percent" in changes:
        row.warn_at_percent = clean_warn_at(changes["warn_at_percent"])
    if "notify_channels" in changes:
        row.notify_channels = clean_channels(changes["notify_channels"])
    if "company_id" in changes:
        row.company_id = _uuid(changes["company_id"], "invalid_company", "company_id")
    if "cost_center_id" in changes:
        row.cost_center_id = _uuid(changes["cost_center_id"], "invalid_cost_center", "cost_center_id")
    if "company_id" in changes or "cost_center_id" in changes:
        await _check_scope(session, tenant_id, row.company_id, row.cost_center_id)
    after = settings_of(row)
    if after == before:
        return await serialize(session, tenant_id, row, now or datetime.now(UTC))
    if (before["period"], before["threshold_usd"], before["company_id"], before["cost_center_id"]) != (
        after["period"],
        after["threshold_usd"],
        after["company_id"],
        after["cost_center_id"],
    ):
        # A new limit or scope is a new budget for the evaluator: it may warn again this period.
        row.last_triggered_at = None
    session.add(_audit(tenant_id, actor, "updated", row.id, before, after))
    await session.flush()
    return await serialize(session, tenant_id, row, now or datetime.now(UTC))


async def delete_budget(session: Any, tenant_id: uuid.UUID, budget_id: uuid.UUID, actor: uuid.UUID) -> None:
    row = await _owned(session, tenant_id, budget_id, lock=True)
    before = settings_of(row)
    await session.delete(row)
    session.add(_audit(tenant_id, actor, "deleted", budget_id, before, None))
    await session.flush()
