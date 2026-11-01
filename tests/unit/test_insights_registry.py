# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights metric registry: every view key resolves, every metric is described, groupings are allow-listed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.insights import registry
from core.insights.metrics import RUN_GROUPS
from core.insights.registry import METRICS, UNITS, VIEW_ROLES, VIEWS, Thresholds

REPO_ROOT = Path(__file__).resolve().parents[2]
V1_KEYS = {
    "agents.by_status",
    "runs.volume",
    "runs.success_rate",
    "runs.straight_through_rate",
    "runs.latency",
    "approvals.pending",
    "approvals.cycle_time",
    "cost.spend",
    "cost.per_run",
    "cost.by_agent",
    "cost.by_model",
    "cost.forecast",
    "cost.budgets",
    "tokens.by_unit",
    "cost.data_quality",
}
EXECUTIVE = {
    "agents.by_status",
    "runs.volume",
    "runs.success_rate",
    "runs.straight_through_rate",
    "runs.latency",
    "approvals.pending",
    "approvals.cycle_time",
    "cost.spend",
}
FINANCE = {
    "cost.spend",
    "cost.per_run",
    "cost.by_agent",
    "cost.by_model",
    "cost.forecast",
    "cost.budgets",
    "tokens.by_unit",
    "cost.data_quality",
    "runs.volume",
}


def test_the_v1_metric_keys_are_exactly_registered() -> None:
    assert set(METRICS) == V1_KEYS
    assert registry.metric_keys() == sorted(V1_KEYS)


def test_views_show_exactly_their_metrics() -> None:
    assert set(VIEWS) == {"executive", "finance"}
    assert set(VIEWS["executive"].keys) == EXECUTIVE
    assert set(VIEWS["finance"].keys) == FINANCE
    for view in VIEWS.values():
        for key in view.keys:
            assert key in METRICS, key
            assert view.key in METRICS[key].views, (view.key, key)
    for metric in METRICS.values():
        for view in metric.views:
            assert metric.key in VIEWS[view].keys, (metric.key, view)


def test_view_roles() -> None:
    assert VIEW_ROLES == {"executive": frozenset({"admin"}), "finance": frozenset({"admin", "cfo"})}
    assert registry.allowed_views("admin", is_admin=True) == ["executive", "finance"]
    assert registry.allowed_views("", is_admin=True) == ["executive", "finance"]
    assert registry.allowed_views("cfo", is_admin=False) == ["finance"]
    # A role claim of "admin" without the admin scope is not an administrator.
    assert registry.allowed_views("admin", is_admin=False) == []
    for role in ("analyst", "chro", "cmo", "coo", "auditor", "domain_lead", "developer", "merchant", ""):
        assert registry.allowed_views(role, is_admin=False) == [], role


@pytest.mark.parametrize("key", sorted(V1_KEYS))
def test_every_metric_is_fully_described(key: str) -> None:
    metric = METRICS[key]
    assert metric.key == key
    assert metric.title.strip() and metric.definition.strip() and metric.data_quality_rule.strip()
    assert metric.unit in UNITS
    assert metric.source and all(isinstance(name, str) and name for name in metric.source)
    assert metric.views and set(metric.views) <= set(VIEWS)
    assert callable(metric.resolver)
    assert metric.kind in {"run", "cost", "snapshot"}
    if metric.thresholds is not None:
        assert metric.thresholds.direction in {"above", "below"}
        if metric.thresholds.direction == "above":
            assert metric.thresholds.watch <= metric.thresholds.act
        else:
            assert metric.thresholds.watch >= metric.thresholds.act
    described = metric.describe()
    assert described["key"] == key and described["source"] == list(metric.source)


def test_group_by_is_an_allow_list() -> None:
    allowed = set(RUN_GROUPS) | {"agent"}
    for metric in METRICS.values():
        assert set(metric.group_by) <= allowed, metric.key
        if metric.kind == "snapshot":
            assert metric.group_by == ()
    assert "status" in METRICS["runs.volume"].group_by
    # A rate grouped by status would be 0% or 100% per group: not offered.
    for key in ("runs.success_rate", "runs.straight_through_rate", "runs.latency"):
        assert "status" not in METRICS[key].group_by
    assert METRICS["cost.spend"].group_by == ("agent",)
    for key in ("cost.by_agent", "cost.by_model", "cost.forecast", "cost.budgets", "cost.data_quality"):
        assert METRICS[key].group_by == ()


def test_thresholds_state() -> None:
    below = Thresholds(watch=95, act=90, direction="below")
    assert [below.state(v) for v in (99, 95, 92, 90, 10, None)] == ["ok", "watch", "watch", "act", "act", None]
    above = Thresholds(watch=10, act=20, direction="above")
    assert [above.state(v) for v in (1, 10, 15, 20, None)] == ["ok", "watch", "watch", "act", None]


def test_readable_metrics_follow_the_views() -> None:
    assert {m.key for m in registry.readable_metrics(["finance"])} == FINANCE
    assert {m.key for m in registry.readable_metrics(["executive", "finance"])} == V1_KEYS
    assert registry.readable_metrics([]) == []


def test_ui_metric_keys_file_matches_the_registry() -> None:
    path = REPO_ROOT / "ui" / "src" / "lib" / "insights" / "metricKeys.generated.json"
    assert json.loads(path.read_text(encoding="utf-8")) == registry.metric_keys()
    from scripts.generate_insights_metric_keys import render

    assert path.read_text(encoding="utf-8") == render()


@pytest.mark.asyncio
async def test_resolve_wraps_the_resolver_in_the_shared_envelope() -> None:
    from dataclasses import replace
    from datetime import date

    from core.insights.metrics import MetricContext, Period
    from core.insights.reporting_settings import defaults_for_region

    async def fake(ctx):
        return {
            "value": 91.0,
            "compare_value": 97.0,
            "series": [{"t": "2026-10-01", "value": 91.0}],
            "source": "run_facts",
            "data_quality": "measured",
        }

    metric = replace(METRICS["runs.success_rate"], resolver=fake)
    period = Period(date(2026, 10, 1), date(2026, 10, 30))
    ctx = MetricContext(session=None, tenant_id=None, reporting=defaults_for_region("IN"), period=period,
                        compare=Period(date(2026, 9, 1), date(2026, 9, 30)))
    out = await registry.resolve(metric, ctx)
    assert out["key"] == "runs.success_rate" and out["unit"] == "percent"
    assert out["period"] == {"from": "2026-10-01", "to": "2026-10-30", "granularity": "day"}
    assert out["compare"] == {"from": "2026-09-01", "to": "2026-09-30"}
    assert out["value"] == 91.0 and out["compare_value"] == 97.0 and out["state"] == "watch"
    assert out["data_quality"] == "measured" and out["source"] == "run_facts" and "computed_at" in out
    assert "hint" not in out and "rows" not in out

    async def empty(ctx):
        return {"value": None, "source": "run_facts", "data_quality": "no_data", "hint": "Run an agent."}

    out = await registry.resolve(replace(metric, resolver=empty), ctx)
    assert out["value"] is None and out["data_quality"] == "no_data" and out["hint"] == "Run an agent."
    assert "state" not in out

    async def bad(ctx):
        return {"value": 1, "data_quality": "demo"}

    with pytest.raises(ValueError):
        await registry.resolve(replace(metric, resolver=bad), ctx)
