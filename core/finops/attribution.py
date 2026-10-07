# SPDX-License-Identifier: Apache-2.0
"""Use-case attribution: every cost carries a business unit, department, application and use case.

A run binds its attribution for its whole span (``bind``): the use case (the
caller's, or the agent's type), the application (the surface the run came
through: ``agents``, ``chat``, ``voice``, ``workflows``, ``a2a``), the business
unit (the caller's, the agent's configuration, or the agent's domain), and the
department and cost centre the agent is charged to. While
``AGENTICORG_FINOPS_ATTRIBUTION_ENABLED`` is on:

* the run's cost write adds one row per day, agent and attribution to
  ``finops_cost_ledger`` (tokens, cost, calls), beside the per-agent ledger
  that exists today;
* each model call record carries the business unit and application of the
  run it was made in, beside the use case and agent it already carries;
* each tool call row carries the use case and application.

``summary`` folds the ledger by any of the dimensions for
``GET /finops/attribution``, and names the share that is unattributed.
The labels are bounded, lower-cased identifiers; they are never a user,
a prompt or a document.

Off, nothing here runs: the ledger is not written, the records and tool
calls carry what they carried before, and the endpoint is not found.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

MAX_LABEL = 64
UNATTRIBUTED = "unattributed"
DEFAULT_APPLICATION = "agents"
APPLICATIONS: tuple[str, ...] = ("agents", "chat", "voice", "workflows", "a2a", "api", "console")
DIMENSIONS: tuple[str, ...] = (
    "use_case",
    "application",
    "business_unit",
    "department_id",
    "cost_center_id",
    "agent_id",
)
MAX_DAYS = 366
MAX_ROWS = 200
_LABEL = re.compile(r"[^a-z0-9_.:-]+")


@dataclass(frozen=True)
class Attribution:
    use_case: str = UNATTRIBUTED
    application: str = DEFAULT_APPLICATION
    business_unit: str = ""
    department_id: str | None = None
    cost_center_id: str | None = None
    agent_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ATTRIBUTION: ContextVar[Attribution | None] = ContextVar("agenticorg_cost_attribution", default=None)


def enabled() -> bool:
    return bool(settings.finops_attribution_enabled)


def bind(attribution: Attribution) -> Token[Attribution | None]:
    """Bind the run's attribution for the span of the run; reset with :func:`reset`."""
    return _ATTRIBUTION.set(attribution)


def reset(token: Token[Attribution | None]) -> None:
    _ATTRIBUTION.reset(token)


def current() -> Attribution | None:
    """The attribution bound for the current run, or None outside one."""
    return _ATTRIBUTION.get()


def label(value: Any) -> str:
    """A bounded, lower-cased identifier; empty when there is nothing usable."""
    text = str(value or "").strip().lower()
    text = _LABEL.sub("-", text).strip("-")
    return text[:MAX_LABEL]


async def resolve_for_agent(
    session: Any,
    agent: Any,
    *,
    use_case: Any = None,
    application: Any = None,
    business_unit: Any = None,
) -> Attribution:
    """The attribution of a run of this agent: the caller's labels first, the agent's configuration second."""
    config = getattr(agent, "config", None) or {}
    cost_center_id = getattr(agent, "cost_center_id", None)
    department_id = None
    if cost_center_id and session is not None:
        from sqlalchemy import select

        from core.models.organization import CostCenter

        try:
            department_id = (
                await session.execute(select(CostCenter.department_id).where(CostCenter.id == cost_center_id))
            ).scalar_one_or_none()
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning("finops_department_lookup_failed", error=type(exc).__name__)
    app = label(application) or label(config.get("application")) or DEFAULT_APPLICATION
    return Attribution(
        use_case=label(use_case)
        or label(config.get("use_case"))
        or label(getattr(agent, "agent_type", None))
        or UNATTRIBUTED,
        application=app if app in APPLICATIONS else label(app),
        business_unit=label(business_unit)
        or label(config.get("business_unit"))
        or label(getattr(agent, "domain", None)),
        department_id=str(department_id) if department_id else None,
        cost_center_id=str(cost_center_id) if cost_center_id else None,
        agent_id=str(getattr(agent, "id", None)) if getattr(agent, "id", None) else None,
    )


async def ledger_add(
    session: Any,
    tenant_id: uuid.UUID,
    attribution: Attribution,
    *,
    tokens: int,
    cost_usd: float,
    calls: int = 1,
    period_date: date | None = None,
) -> None:
    """Add a run's tokens and cost to the day's row for this attribution (an upsert)."""
    from sqlalchemy import text as sqltext

    await session.execute(
        sqltext(
            "INSERT INTO finops_cost_ledger "
            "(id, tenant_id, period_date, agent_id, use_case, application, business_unit, department_id, "
            " cost_center_id, tokens, cost_usd, calls, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :tid, :day, CAST(:agent_id AS uuid), :use_case, :application, "
            " :business_unit, CAST(:department_id AS uuid), CAST(:cost_center_id AS uuid), :tokens, :cost_usd, "
            " :calls, now(), now()) "
            "ON CONFLICT (tenant_id, period_date, COALESCE(agent_id::text, ''), use_case, application, business_unit, "
            " COALESCE(department_id::text, ''), COALESCE(cost_center_id::text, '')) "
            "DO UPDATE SET tokens = finops_cost_ledger.tokens + EXCLUDED.tokens, "
            " cost_usd = finops_cost_ledger.cost_usd + EXCLUDED.cost_usd, "
            " calls = finops_cost_ledger.calls + EXCLUDED.calls, updated_at = now()"
        ),
        {
            "tid": str(tenant_id),
            "day": period_date or datetime.now(UTC).date(),
            "agent_id": attribution.agent_id,
            "use_case": attribution.use_case or UNATTRIBUTED,
            "application": attribution.application or DEFAULT_APPLICATION,
            "business_unit": attribution.business_unit or "",
            "department_id": attribution.department_id,
            "cost_center_id": attribution.cost_center_id,
            "tokens": max(0, int(tokens or 0)),
            "cost_usd": max(0.0, float(cost_usd or 0.0)),
            "calls": max(0, int(calls or 0)),
        },
    )


async def summary(session: Any, tenant_id: uuid.UUID, *, days: int = 30, group_by: str = "use_case") -> dict[str, Any]:
    """The ledger folded by one dimension over the window, with totals and the unattributed share."""
    from sqlalchemy import text as sqltext

    if group_by not in DIMENSIONS:
        raise ValueError(f"group_by is one of {', '.join(DIMENSIONS)}")
    window = max(1, min(int(days), MAX_DAYS))
    since = datetime.now(UTC).date() - timedelta(days=window - 1)
    params = {"tid": str(tenant_id), "since": since, "limit": MAX_ROWS}
    rows = (
        await session.execute(
            sqltext(
                f"SELECT {group_by}, SUM(tokens), SUM(cost_usd), SUM(calls), COUNT(DISTINCT agent_id) "  # noqa: S608  # nosec B608 — group_by is one of the fixed dimension names checked above
                "FROM finops_cost_ledger WHERE tenant_id = :tid AND period_date >= :since "
                f"GROUP BY {group_by} ORDER BY 3 DESC, 1 ASC LIMIT :limit"
            ),
            params,
        )
    ).fetchall()
    totals = (
        await session.execute(
            sqltext(
                "SELECT SUM(tokens), SUM(cost_usd), SUM(calls), "
                "SUM(CASE WHEN use_case = :unattributed THEN cost_usd ELSE 0 END) "
                "FROM finops_cost_ledger WHERE tenant_id = :tid AND period_date >= :since"
            ),
            {**params, "unattributed": UNATTRIBUTED},
        )
    ).fetchone()
    total_cost = float((totals[1] if totals else 0) or 0)
    return {
        "days": window,
        "since": since.isoformat(),
        "group_by": group_by,
        "rows": [
            {
                group_by: (str(r[0]) if r[0] is not None else None) or ("" if group_by == "business_unit" else None),
                "tokens": int(r[1] or 0),
                "cost_usd": round(float(r[2] or 0), 6),
                "calls": int(r[3] or 0),
                "agents": int(r[4] or 0),
            }
            for r in rows
        ],
        "totals": {
            "tokens": int((totals[0] if totals else 0) or 0),
            "cost_usd": round(total_cost, 6),
            "calls": int((totals[2] if totals else 0) or 0),
            "unattributed_share": round(float((totals[3] if totals else 0) or 0) / total_cost, 4)
            if total_cost
            else 0.0,
        },
    }
