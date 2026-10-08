# SPDX-License-Identifier: Apache-2.0
"""Cost comparison across models and providers, and forecasting from history and growth assumptions.

**Forecast** (``forecast``): the attributed ledger's daily history over a
window, per use case (or any attribution dimension), fitted with a linear
trend; the next horizon (a quarter by default) is the trend carried forward,
compounded by an optional monthly growth assumption, never below zero, with a
band from the day-to-day scatter of the history. Missing days count as zero
spend, so a use case that went quiet trends down rather than vanishing.
Every label is projected and counted in the totals; the listed rows are the
highest projected spend first, at most ``MAX_KEYS``, and the answer says how
many labels there were and whether the list was cut.

**Comparison** (``comparison``): what each use case spent on each model over
a window, from the model call records, and what the same tokens would cost at
every catalogue model's list or negotiated price, so an administrator sees the
cheapest alternatives and the saving before changing a routing policy. With a
change date, the daily cost before and after it, per model, says what a
deployment did.

Both are read on request and store nothing. The figures are tokens and USD by
label; never a user, a prompt or a document. Off
(``AGENTICORG_FINOPS_FORECAST_ENABLED``), the endpoints are not found.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from core.config import settings
from core.finops import attribution

logger = structlog.get_logger()

MAX_HISTORY_DAYS = 366
MAX_HORIZON_DAYS = 366
DEFAULT_HISTORY_DAYS = 90
DEFAULT_HORIZON_DAYS = 90
MAX_GROWTH_PCT = 500.0
MAX_KEYS = 100
MAX_ALTERNATIVES = 5
DAYS_PER_MONTH = 30.0


def enabled() -> bool:
    return bool(settings.finops_forecast_enabled)


async def history(
    session: Any, tenant_id: uuid.UUID, *, days: int = DEFAULT_HISTORY_DAYS, group_by: str = "use_case"
) -> dict[str, list[tuple[date, int, float]]]:
    """Daily tokens and cost per key over the window, from the attributed ledger."""
    from sqlalchemy import bindparam, func, select

    from core.models.finops_ledger import FinopsCostLedger

    if group_by not in attribution.DIMENSIONS:
        raise ValueError(f"group_by is one of {', '.join(attribution.DIMENSIONS)}")
    window = max(1, min(int(days), MAX_HISTORY_DAYS))
    since = datetime.now(UTC).date() - timedelta(days=window - 1)
    ledger = FinopsCostLedger.__table__
    dimension = ledger.c[group_by]
    rows = (
        await session.execute(
            select(dimension, ledger.c.period_date, func.sum(ledger.c.tokens), func.sum(ledger.c.cost_usd))
            .where(ledger.c.tenant_id == bindparam("tid"), ledger.c.period_date >= bindparam("since"))
            .group_by(dimension, ledger.c.period_date)
            .order_by(dimension, ledger.c.period_date),
            {"tid": tenant_id, "since": since},
        )
    ).fetchall()
    series: dict[str, list[tuple[date, int, float]]] = {}
    for key, day, tokens, cost in rows:
        label = str(key) if key is not None else ""
        series.setdefault(label, []).append((day, int(tokens or 0), float(cost or 0.0)))
    return series


def _fill(points: list[tuple[date, int, float]], *, days: int, end: date) -> list[tuple[int, float]]:
    """One (tokens, cost) pair per day of the window, zero where the ledger has no row."""
    by_day = {day: (tokens, cost) for day, tokens, cost in points}
    start = end - timedelta(days=days - 1)
    return [by_day.get(start + timedelta(days=i), (0, 0.0)) for i in range(days)]


def _trend(values: list[float]) -> tuple[float, float, float]:
    """Least-squares slope and intercept over day index, and the residual standard deviation."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1:
        return 0.0, values[0], 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values, strict=True)) / var_x if var_x else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, values, strict=True)]
    std = math.sqrt(sum(r * r for r in residuals) / (n - 1)) if n > 1 else 0.0
    return slope, intercept, std


def project(
    points: list[tuple[date, int, float]],
    *,
    history_days: int,
    horizon_days: int,
    growth_monthly_pct: float | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """The next horizon from a key's daily history: trend carried forward, compounded by the growth assumption."""
    end = end or datetime.now(UTC).date()
    filled = _fill(points, days=history_days, end=end)
    costs = [c for _, c in filled]
    tokens = [float(t) for t, _ in filled]
    cost_slope, cost_intercept, cost_std = _trend(costs)
    token_slope, token_intercept, _ = _trend(tokens)
    growth = (growth_monthly_pct or 0.0) / 100.0
    projected_cost = 0.0
    projected_tokens = 0.0
    for step in range(1, horizon_days + 1):
        x = history_days - 1 + step
        factor = (1.0 + growth) ** (step / DAYS_PER_MONTH) if growth else 1.0
        projected_cost += max(0.0, cost_intercept + cost_slope * x) * factor
        projected_tokens += max(0.0, token_intercept + token_slope * x) * factor
    band = 1.96 * cost_std * math.sqrt(horizon_days)
    history_cost = sum(costs)
    return {
        "history_cost_usd": round(history_cost, 6),
        "history_tokens": int(sum(tokens)),
        "daily_mean_cost_usd": round(history_cost / history_days, 6) if history_days else 0.0,
        "trend_cost_usd_per_day": round(cost_slope, 6),
        "projected_cost_usd": round(projected_cost, 2),
        "projected_cost_low_usd": round(max(0.0, projected_cost - band), 2),
        "projected_cost_high_usd": round(projected_cost + band, 2),
        "projected_tokens": int(projected_tokens),
        "baseline_cost_usd": round(history_cost / history_days * horizon_days, 2) if history_days else 0.0,
    }


async def forecast(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    days: int = DEFAULT_HISTORY_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    group_by: str = "use_case",
    growth_monthly_pct: float | None = None,
) -> dict[str, Any]:
    """The projection per key for the horizon, with the assumptions it was made under and the totals."""
    window = max(1, min(int(days), MAX_HISTORY_DAYS))
    horizon = max(1, min(int(horizon_days), MAX_HORIZON_DAYS))
    growth = None
    if growth_monthly_pct is not None:
        growth = max(-100.0, min(float(growth_monthly_pct), MAX_GROWTH_PCT))
    series = await history(session, tenant_id, days=window, group_by=group_by)
    end = datetime.now(UTC).date()
    rows = []
    for key, points in series.items():
        rows.append(
            {
                group_by: key,
                **project(points, history_days=window, horizon_days=horizon, growth_monthly_pct=growth, end=end),
            }
        )
    # Every label is projected so the totals are complete; only the listed rows are limited, highest spend first.
    rows.sort(key=lambda r: (-r["projected_cost_usd"], -r["history_cost_usd"], str(r[group_by])))
    totals = {
        "history_cost_usd": round(sum(r["history_cost_usd"] for r in rows), 6),
        "projected_cost_usd": round(sum(r["projected_cost_usd"] for r in rows), 2),
        "projected_cost_low_usd": round(sum(r["projected_cost_low_usd"] for r in rows), 2),
        "projected_cost_high_usd": round(sum(r["projected_cost_high_usd"] for r in rows), 2),
        "projected_tokens": sum(r["projected_tokens"] for r in rows),
    }
    total_rows = len(rows)
    return {
        "group_by": group_by,
        "assumptions": {
            "history_days": window,
            "horizon_days": horizon,
            "growth_monthly_pct": growth,
            "method": "linear trend on daily cost, compounded monthly by the growth assumption, floored at zero",
        },
        "rows": rows[:MAX_KEYS],
        "total_rows": total_rows,
        "truncated": total_rows > MAX_KEYS,
        "totals": totals,
    }


async def usage_by_model(
    session: Any, tenant_id: uuid.UUID, *, days: int, since: date | None = None, until: date | None = None
) -> list[tuple[Any, ...]]:
    """Completed model calls over the window from the records, per use case, provider and model.

    Each row is: use case, provider, model, tokens, input tokens, output tokens, cost, calls, unsplit tokens.
    The input and output sums cover only the calls that recorded both counts; the tokens of every other call
    are summed separately as the unsplit tokens, so they can be priced at the blended rate.
    """
    from sqlalchemy import text as sqltext

    window = max(1, min(int(days), MAX_HISTORY_DAYS))
    start = since or (datetime.now(UTC).date() - timedelta(days=window - 1))
    params: dict[str, Any] = {"tid": str(tenant_id), "since": start}
    until_sql = ""
    if until is not None:
        params["until"] = until
        until_sql = " AND created_at < :until"
    return list(
        (
            await session.execute(
                sqltext(
                    "SELECT use_case, provider, model, SUM(tokens), "  # noqa: S608  # nosec B608 — until_sql is a fixed fragment, the dates are bound
                    "SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
                    "THEN input_tokens ELSE 0 END), "
                    "SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
                    "THEN output_tokens ELSE 0 END), "
                    "SUM(cost_usd), COUNT(*), "
                    "SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN tokens ELSE 0 END) "
                    "FROM model_gateway_records "
                    f"WHERE tenant_id = :tid AND outcome = 'completed' AND created_at >= :since{until_sql} "  # noqa: S608  # nosec B608 — until_sql is a fixed fragment, the dates are bound
                    "GROUP BY use_case, provider, model ORDER BY use_case, provider, model"
                ),
                params,
            )
        ).fetchall()
    )


def _unsplit_tokens(row: tuple[Any, ...]) -> int:
    """The tokens of the calls in a usage row that did not record both input and output counts."""
    if len(row) > 8:
        return int(row[8] or 0)
    _use_case, _provider, _model, tokens, input_tokens, output_tokens = row[:6]
    return int(tokens or 0) if input_tokens is None or output_tokens is None else 0


def alternatives(
    tokens: int,
    input_tokens: int | None,
    output_tokens: int | None,
    current_cost: float,
    *,
    unsplit_tokens: int = 0,
) -> list[dict[str, Any]]:
    """What the same tokens would cost at every priced catalogue model, cheapest first.

    With a known split, the split tokens are priced by input and output rate and the unsplit tokens (calls
    that did not record both counts) at the blended rate; without one, every token is priced at the blended rate.
    """
    from core.ai_providers.catalog import LLM_CATALOG
    from core.governance.model_pricing import price_for

    priced = []
    seen: set[tuple[str, str]] = set()
    for entry in LLM_CATALOG:
        if entry.model == "*" or (entry.provider, entry.model) in seen:
            continue
        seen.add((entry.provider, entry.model))
        price = price_for(entry.provider, entry.model)
        if price is None:
            continue
        if input_tokens is not None and output_tokens is not None:
            cost = price.cost_usd(input_tokens=input_tokens, output_tokens=output_tokens, tokens=tokens)
            if unsplit_tokens > 0:
                cost += price.cost_usd(input_tokens=None, output_tokens=None, tokens=unsplit_tokens)
        else:
            cost = price.cost_usd(input_tokens=None, output_tokens=None, tokens=tokens)
        priced.append(
            {
                "provider": entry.provider,
                "model": entry.model,
                "cost_usd": round(cost, 6),
                "saving_usd": round(current_cost - cost, 6),
                "price_source": price.source,
            }
        )
    priced.sort(key=lambda r: (r["cost_usd"], r["provider"], r["model"]))
    return priced[:MAX_ALTERNATIVES]


async def comparison(
    session: Any, tenant_id: uuid.UUID, *, days: int = 30, changed_at: date | None = None
) -> dict[str, Any]:
    """Per use case: the current model mix and cost, the cheapest alternatives; with a change date, before and after."""
    rows = await usage_by_model(session, tenant_id, days=days)
    by_use_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        use_case, provider, model, tokens, input_tokens, output_tokens, cost, calls = row[:8]
        unsplit = _unsplit_tokens(row)
        key = str(use_case or attribution.UNATTRIBUTED)
        bucket = by_use_case.setdefault(
            key,
            {
                "use_case": key,
                "models": [],
                "tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "unsplit_tokens": 0,
                "cost_usd": 0.0,
                "calls": 0,
            },
        )
        bucket["models"].append(
            {
                "provider": str(provider or "unknown"),
                "model": str(model or "unknown"),
                "tokens": int(tokens or 0),
                "cost_usd": round(float(cost or 0.0), 6),
                "calls": int(calls or 0),
            }
        )
        bucket["tokens"] += int(tokens or 0)
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
        bucket["unsplit_tokens"] += unsplit
        bucket["cost_usd"] = round(bucket["cost_usd"] + float(cost or 0.0), 6)
        bucket["calls"] += int(calls or 0)
    use_cases = []
    for bucket in by_use_case.values():
        total = bucket["cost_usd"] or 0.0
        for item in bucket["models"]:
            item["share"] = round(item["cost_usd"] / total, 4) if total else 0.0
        bucket["models"].sort(key=lambda m: (-m["cost_usd"], m["provider"], m["model"]))
        split_known = bucket["unsplit_tokens"] < bucket["tokens"] and bool(
            bucket["input_tokens"] or bucket["output_tokens"]
        )
        bucket["alternatives"] = alternatives(
            bucket["tokens"],
            bucket["input_tokens"] if split_known else None,
            bucket["output_tokens"] if split_known else None,
            bucket["cost_usd"],
            unsplit_tokens=bucket["unsplit_tokens"] if split_known else 0,
        )
        use_cases.append(bucket)
    use_cases.sort(key=lambda b: (-b["cost_usd"], b["use_case"]))
    answer: dict[str, Any] = {
        "days": max(1, min(int(days), MAX_HISTORY_DAYS)),
        "use_cases": use_cases,
        "totals": {
            "cost_usd": round(sum(b["cost_usd"] for b in use_cases), 6),
            "tokens": sum(b["tokens"] for b in use_cases),
            "calls": sum(b["calls"] for b in use_cases),
        },
    }
    if changed_at is not None:
        answer["change"] = await before_after(session, tenant_id, days=days, changed_at=changed_at)
    return answer


async def before_after(session: Any, tenant_id: uuid.UUID, *, days: int, changed_at: date) -> dict[str, Any]:
    """Daily mean cost and calls per model before and after a date, from the records."""
    window = max(1, min(int(days), MAX_HISTORY_DAYS))
    today = datetime.now(UTC).date()
    before_start = changed_at - timedelta(days=window)
    before = await usage_by_model(session, tenant_id, days=window, since=before_start, until=changed_at)
    after = await usage_by_model(session, tenant_id, days=window, since=changed_at)
    after_days = max(1, (today - changed_at).days + 1)

    def fold(rows: list[tuple[Any, ...]], span: int) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            _use_case, provider, model, tokens, _i, _o, cost, calls = row[:8]
            key = f"{provider}/{model}"
            item = out.setdefault(key, {"cost_usd": 0.0, "calls": 0, "tokens": 0})
            item["cost_usd"] = round(item["cost_usd"] + float(cost or 0.0), 6)
            item["calls"] += int(calls or 0)
            item["tokens"] += int(tokens or 0)
        for item in out.values():
            item["daily_cost_usd"] = round(item["cost_usd"] / span, 6)
            item["daily_calls"] = round(item["calls"] / span, 2)
        return out

    before_fold = fold(before, window)
    after_fold = fold(after, after_days)
    models = sorted(set(before_fold) | set(after_fold))
    return {
        "changed_at": changed_at.isoformat(),
        "before_days": window,
        "after_days": after_days,
        "models": [
            {
                "model": key,
                "before": before_fold.get(key),
                "after": after_fold.get(key),
                "daily_cost_delta_usd": round(
                    (after_fold.get(key, {}).get("daily_cost_usd", 0.0))
                    - (before_fold.get(key, {}).get("daily_cost_usd", 0.0)),
                    6,
                ),
            }
            for key in models
        ],
        "daily_cost_delta_usd": round(
            sum(i["daily_cost_usd"] for i in after_fold.values())
            - sum(i["daily_cost_usd"] for i in before_fold.values()),
            6,
        ),
    }
