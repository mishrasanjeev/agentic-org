# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights metrics from the runtime counters (migration v6z115, ``core/insights/runtime_counters.py``).

- ``engineering.tool_usage``: tool calls through the tool gateway in the period, per tool: calls, failures,
  refusals, idempotent replays and the p95 latency.
- ``compliance.guardrail_events``: guardrail outcomes in the period by stage, detector, action and mode.
- ``compliance.security_events``: security events in the period by kind, each kind's last day held against the
  30 days before it (a spike is more than three standard deviations above their mean).

The counters are kept per UTC day, so the counter metrics read the UTC days of the period's dates; tool calls
are timestamped and read for the period's tenant-local days. Counts and names only.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from core.insights.metrics import MetricContext, bucket_start, bucket_starts, no_data, pct
from core.insights.metrics_domain import _bounds, _compare_bounds
from core.insights.metrics_governance import _domain_agent_ids

TOP_TOOLS = 10
SPIKE_SIGMAS = 3.0
SPIKE_BASELINE_DAYS = 30
# A spike needs at least this many events on the day, so one event after a quiet month is not one.
SPIKE_MIN_EVENTS = 5
NOT_RECORDED = "Runtime counters are recorded while Insights is on for the organisation."


def _day_series(ctx: MetricContext, daily: dict[date, int]) -> list[dict[str, Any]]:
    buckets: dict[date, int] = defaultdict(int)
    for d, n in daily.items():
        buckets[bucket_start(d, ctx.period.granularity)] += n
    return [{"t": start.isoformat(), "value": buckets.get(start, 0)} for start in bucket_starts(ctx.period)]


# ------------------------------------------------------------------ tool calls


async def _tool_counts(ctx: MetricContext, bounds: tuple[Any, Any]) -> list[Any]:
    from sqlalchemy import case, func, select

    from core.models.tool_call import ToolCall as T

    lo, hi = bounds
    calls = func.count()

    def status(name: str) -> Any:
        return func.sum(case((T.status == name, 1), else_=0))

    query = (
        select(
            T.tool_name,
            func.max(T.connector_name),
            calls,
            status("failed"),
            status("refused"),
            status("replayed"),
            func.percentile_cont(0.95).within_group(T.latency_ms),
        )
        .where(T.tenant_id == ctx.tenant_id, T.called_at >= lo, T.called_at < hi)
        .group_by(T.tool_name)
        .order_by(calls.desc())
    )
    agents = await _domain_agent_ids(ctx)
    if agents is not None:
        query = query.where(T.agent_id.in_([uuid.UUID(a) for a in agents]))
    return list((await ctx.session.execute(query)).all())


async def resolve_tool_usage(ctx: MetricContext) -> dict[str, Any]:
    """Tool calls in the period; rows give each tool's calls, failures, refusals, replays and p95 latency."""
    rows = await _tool_counts(ctx, _bounds(ctx))
    total = sum(int(r[2] or 0) for r in rows)
    if not total:
        return no_data("tool_calls", "No tool call was recorded in this period. " + NOT_RECORDED)
    out_rows = [
        {
            "label": str(tool),
            "connector": connector,
            "value": int(n or 0),
            "failed": int(f or 0),
            "refused": int(refused or 0),
            "replayed": int(replayed or 0),
            "success_rate": pct(int(n or 0) - int(f or 0) - int(refused or 0), int(n or 0)),
            "p95_ms": round(float(p95), 1) if p95 is not None else None,
        }
        for tool, connector, n, f, refused, replayed, p95 in rows[:TOP_TOOLS]
    ]
    out: dict[str, Any] = {
        "value": total,
        "rows": out_rows,
        "source": "tool_calls",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        before = await _tool_counts(ctx, previous)
        out["compare_value"] = sum(int(r[2] or 0) for r in before) or None
    return out


# ------------------------------------------------------------------ counters


def _utc_days(ctx: MetricContext) -> tuple[date, date]:
    return ctx.period.start, ctx.period.end


async def resolve_guardrail_events(ctx: MetricContext) -> dict[str, Any]:
    """Guardrail outcomes in the period; rows by stage, detector, action and mode, the most frequent first."""
    from sqlalchemy import func, select

    from core.models.insights import GuardrailEvent as G

    start, end = _utc_days(ctx)
    clauses = (G.tenant_id == ctx.tenant_id, G.day >= start, G.day <= end)
    daily = {
        d: int(n)
        for d, n in await ctx.session.execute(select(G.day, func.sum(G.count)).where(*clauses).group_by(G.day))
    }
    total = sum(daily.values())
    if not total:
        return no_data(
            "guardrail_events",
            "No guardrail outcome was recorded in this period. Outcomes are counted when a rule finds something "
            "(dry runs are not). " + NOT_RECORDED,
        )
    found = (
        await ctx.session.execute(
            select(G.stage, G.detector, G.action, G.mode, func.sum(G.count))
            .where(*clauses)
            .group_by(G.stage, G.detector, G.action, G.mode)
        )
    ).all()
    rows = sorted(
        (
            {"label": f"{stage} / {detector}", "stage": stage, "detector": detector, "action": action,
             "mode": mode, "value": int(n)}
            for stage, detector, action, mode, n in found
        ),
        key=lambda r: (-int(r["value"]), str(r["label"])),
    )
    out: dict[str, Any] = {
        "value": total,
        "series": _day_series(ctx, daily),
        "rows": rows,
        "source": "guardrail_events",
        "data_quality": "measured",
    }
    if ctx.compare is not None:
        before = (
            await ctx.session.execute(
                select(func.sum(G.count)).where(
                    G.tenant_id == ctx.tenant_id, G.day >= ctx.compare.start, G.day <= ctx.compare.end
                )
            )
        ).scalar_one()
        out["compare_value"] = int(before) if before else None
    return out


def spike(day_count: int, baseline: list[int]) -> bool:
    """True when ``day_count`` is over three standard deviations above the baseline's mean (and at least 5)."""
    if day_count < SPIKE_MIN_EVENTS or not baseline:
        return False
    mean = sum(baseline) / len(baseline)
    sd = math.sqrt(sum((n - mean) ** 2 for n in baseline) / len(baseline))
    return day_count > mean + SPIKE_SIGMAS * sd


async def resolve_security_events(ctx: MetricContext) -> dict[str, Any]:
    """Security events in the period by kind; a kind spikes when its last day is 3 sigma above the 30 days before."""
    from sqlalchemy import func, select

    from core.models.insights import SECURITY_EVENT_KINDS
    from core.models.insights import SecurityEventDaily as S

    start, end = _utc_days(ctx)
    since = end - timedelta(days=SPIKE_BASELINE_DAYS)
    found = (
        await ctx.session.execute(
            select(S.day, S.event_kind, S.count).where(
                S.tenant_id == ctx.tenant_id, S.day >= min(start, since), S.day <= end
            )
        )
    ).all()
    in_period: dict[str, int] = defaultdict(int)
    daily: dict[date, int] = defaultdict(int)
    by_day: dict[str, dict[date, int]] = defaultdict(dict)
    for day, kind, n in found:
        by_day[kind][day] = int(n)
        if start <= day <= end:
            in_period[kind] += int(n)
            daily[day] += int(n)
    total = sum(in_period.values())
    if not total:
        return no_data(
            "security_events_daily",
            "No security event was recorded in this period: failed sign-ins, rate-limit and scope denials, "
            "session revocations, API-key use and webhook signature failures are counted. " + NOT_RECORDED,
        )
    rows = []
    for kind in SECURITY_EVENT_KINDS:
        if not in_period.get(kind):
            continue
        days = by_day[kind]
        baseline = [days.get(since + timedelta(days=i), 0) for i in range(SPIKE_BASELINE_DAYS)]
        last = days.get(end, 0)
        rows.append({"label": kind, "value": in_period[kind], "last_day": last, "spike": spike(last, baseline)})
    out: dict[str, Any] = {
        "value": total,
        "series": _day_series(ctx, dict(daily)),
        "rows": rows,
        "spikes": [r["label"] for r in rows if r["spike"]],
        "source": "security_events_daily",
        "data_quality": "measured",
    }
    if ctx.compare is not None:
        before = (
            await ctx.session.execute(
                select(func.sum(S.count)).where(
                    S.tenant_id == ctx.tenant_id, S.day >= ctx.compare.start, S.day <= ctx.compare.end
                )
            )
        ).scalar_one()
        out["compare_value"] = int(before) if before else None
    return out
