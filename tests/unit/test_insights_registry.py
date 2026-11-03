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
    "value.money_saved",
    "value.hours_saved",
    "value.roi",
    "workflows.runs",
    "workflows.stalled",
    "connectors.unhealthy",
    "hr.pt_returns",
    "marketing.leads",
    "marketing.content_drafts",
    "marketing.draft_cycle_time",
    "commerce.funnel",
    "commerce.orders",
    "commerce.webhook_failures",
    "compliance.audit_integrity",
    "compliance.approvals_decided",
    "compliance.rubber_stamps",
    "compliance.dsar_open",
    "compliance.dsar_completion_time",
    "compliance.attestations_expiring",
    "compliance.change_log",
    "engineering.gateway_issues",
    "engineering.slow_spans",
    "engineering.eval_pass_rate",
    "engineering.prompt_edits",
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
    "value.money_saved",
    "value.hours_saved",
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
    "value.money_saved",
    "value.roi",
}
OPERATIONS = {
    "workflows.runs",
    "workflows.stalled",
    "runs.success_rate",
    "runs.latency",
    "approvals.pending",
    "connectors.unhealthy",
    "runs.volume",
}
HR = {"runs.volume", "runs.success_rate", "approvals.pending", "approvals.cycle_time", "hr.pt_returns"}
MARKETING = {
    "marketing.leads",
    "marketing.content_drafts",
    "marketing.draft_cycle_time",
    "runs.volume",
    "approvals.pending",
}
COMMERCE = {"commerce.orders", "commerce.webhook_failures", "commerce.funnel"}
COMPLIANCE = {key for key in V1_KEYS if key.startswith("compliance.")}
ENGINEERING = {
    "runs.success_rate",
    "runs.latency",
    "runs.volume",
    "engineering.gateway_issues",
    "engineering.slow_spans",
    "engineering.eval_pass_rate",
    "engineering.prompt_edits",
}


def test_the_v1_metric_keys_are_exactly_registered() -> None:
    assert set(METRICS) == V1_KEYS
    assert registry.metric_keys() == sorted(V1_KEYS)


def test_views_show_exactly_their_metrics() -> None:
    assert set(VIEWS) == {
        "executive",
        "finance",
        "operations",
        "hr",
        "marketing",
        "commerce",
        "compliance",
        "engineering",
    }
    assert set(VIEWS["executive"].keys) == EXECUTIVE
    assert set(VIEWS["finance"].keys) == FINANCE
    assert set(VIEWS["operations"].keys) == OPERATIONS
    assert set(VIEWS["hr"].keys) == HR
    assert set(VIEWS["marketing"].keys) == MARKETING
    assert set(VIEWS["commerce"].keys) == COMMERCE
    assert set(VIEWS["compliance"].keys) == COMPLIANCE and len(COMPLIANCE) == 7
    assert set(VIEWS["engineering"].keys) == ENGINEERING
    for view in VIEWS.values():
        for key in view.keys:
            assert key in METRICS, key
            assert view.key in METRICS[key].views, (view.key, key)
    for metric in METRICS.values():
        for view in metric.views:
            assert metric.key in VIEWS[view].keys, (metric.key, view)


def test_view_roles() -> None:
    assert VIEW_ROLES == {
        "executive": frozenset({"admin"}),
        "finance": frozenset({"admin", "cfo"}),
        "operations": frozenset({"admin", "coo"}),
        "hr": frozenset({"admin", "chro"}),
        "marketing": frozenset({"admin", "cmo"}),
        "commerce": frozenset({"admin", "merchant"}),
        "compliance": frozenset({"admin", "auditor"}),
        "engineering": frozenset({"admin", "developer"}),
    }
    every = ["executive", "finance", "operations", "hr", "marketing", "commerce", "compliance", "engineering"]
    assert registry.allowed_views("admin", is_admin=True) == every
    assert registry.allowed_views("", is_admin=True) == every
    assert registry.allowed_views("cfo", is_admin=False) == ["finance"]
    assert registry.allowed_views("coo", is_admin=False) == ["operations"]
    assert registry.allowed_views("chro", is_admin=False) == ["hr"]
    assert registry.allowed_views("cmo", is_admin=False) == ["marketing"]
    assert registry.allowed_views("merchant", is_admin=False) == ["commerce"]
    assert registry.allowed_views("auditor", is_admin=False) == ["compliance"]
    assert registry.allowed_views("developer", is_admin=False) == ["engineering"]
    # A role claim of "admin" without the admin scope is not an administrator.
    assert registry.allowed_views("admin", is_admin=False) == []
    for role in ("analyst", "domain_lead", ""):
        assert registry.allowed_views(role, is_admin=False) == [], role
    # The Compliance view is the auditor's (no domain limit); Engineering keeps the developer's domain.
    assert registry.VIEW_DOMAINS["compliance"] is None and registry.VIEW_DOMAINS["engineering"] is None


def test_role_views_read_their_own_domains_within_the_callers() -> None:
    from api.v1.insights import view_domains

    assert registry.VIEW_DOMAINS["executive"] is None and registry.VIEW_DOMAINS["finance"] is None
    assert view_domains("executive", ["finance"]) == ["finance"]
    assert view_domains("executive", None) is None
    assert view_domains("operations", None) == ["ops", "operations"]
    # The COO's "ops" covers agents filed under "operations".
    assert view_domains("operations", ["ops"]) == ["ops", "operations"]
    assert view_domains("hr", ["hr"]) == ["hr"]
    assert view_domains("hr", ["finance"]) == []
    assert view_domains("commerce", ["commerce"]) == ["commerce"]


@pytest.mark.parametrize("key", sorted(V1_KEYS))
def test_every_metric_is_fully_described(key: str) -> None:
    metric = METRICS[key]
    assert metric.key == key
    assert metric.title.strip() and metric.definition.strip() and metric.data_quality_rule.strip()
    assert metric.unit in UNITS
    assert metric.source and all(isinstance(name, str) and name for name in metric.source)
    assert metric.views and set(metric.views) <= set(VIEWS)
    assert callable(metric.resolver)
    assert metric.kind in {"run", "cost", "snapshot", "value"}
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
    for key in ("cost.by_agent", "cost.by_model", "cost.forecast", "cost.budgets", "cost.data_quality",
                "value.money_saved", "value.hours_saved", "value.roi", "workflows.runs", "hr.pt_returns",
                "marketing.leads", "commerce.funnel", "commerce.orders"):
        assert METRICS[key].group_by == ()


def test_thresholds_state() -> None:
    below = Thresholds(watch=95, act=90, direction="below")
    assert [below.state(v) for v in (99, 95, 92, 90, 10, None)] == ["ok", "watch", "watch", "act", "act", None]
    above = Thresholds(watch=10, act=20, direction="above")
    assert [above.state(v) for v in (1, 10, 15, 20, None)] == ["ok", "watch", "watch", "act", None]


def test_readable_metrics_follow_the_views() -> None:
    assert {m.key for m in registry.readable_metrics(["finance"])} == FINANCE
    assert {m.key for m in registry.readable_metrics(["executive", "finance"])} == EXECUTIVE | FINANCE
    assert {m.key for m in registry.readable_metrics(list(registry.VIEWS))} == V1_KEYS
    assert registry.readable_metrics([]) == []


def test_ui_metric_keys_file_matches_the_registry() -> None:
    path = REPO_ROOT / "ui" / "src" / "lib" / "insights" / "metricKeys.generated.json"
    assert json.loads(path.read_text(encoding="utf-8")) == registry.metric_keys()
    from scripts.generate_insights_metric_keys import render

    assert path.read_text(encoding="utf-8") == render()


def test_the_metric_keys_generator_writes_the_file_and_checks_it(tmp_path, monkeypatch, capsys) -> None:
    import scripts.generate_insights_metric_keys as gen

    out = tmp_path / "ui" / "metricKeys.generated.json"
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "OUTPUT", out)

    # Missing file: --check fails and names the file; a plain run writes it.
    assert gen.main(["--check"]) == 1
    assert "metricKeys.generated.json is out of date" in capsys.readouterr().out
    assert gen.main([]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == registry.metric_keys()
    assert gen.main(["--check"]) == 0

    # A stale file fails the check until it is rewritten.
    out.write_text('["runs.volume"]\n', encoding="utf-8")
    assert gen.main(["--check"]) == 1
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0


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
    assert out["compare"] == {"from": "2026-09-01", "to": "2026-09-30", "basis": "full_period"}
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


@pytest.mark.asyncio
async def test_a_period_with_a_partial_today_compares_complete_days_only() -> None:
    """Replay: a 7-day period ending on a running today against the 7 days before it.

    The tile value covers all 7 days (today included); the change is computed over
    the 6 complete days against the first 6 days of the comparison window, and the
    response says so.
    """
    from dataclasses import replace
    from datetime import UTC, date, datetime

    from core.insights.metrics import MetricContext, Period
    from core.insights.reporting_settings import defaults_for_region

    seen: list[tuple[str, str, str | None, str | None]] = []

    async def days_counted(ctx):
        # Stands in for a count metric: one per day of each window.
        seen.append((ctx.period.start.isoformat(), ctx.period.end.isoformat(),
                     ctx.compare.start.isoformat() if ctx.compare else None,
                     ctx.compare.end.isoformat() if ctx.compare else None))
        return {"value": ctx.period.days, "compare_value": ctx.compare.days if ctx.compare else None,
                "series": [{"t": ctx.period.start.isoformat(), "value": ctx.period.days}],
                "source": "run_facts", "data_quality": "measured"}

    metric = replace(METRICS["runs.volume"], resolver=days_counted)
    period = Period(date(2026, 10, 26), date(2026, 11, 1))
    ctx = MetricContext(session=None, tenant_id=None, reporting=defaults_for_region("IN"), period=period,
                        compare=Period(date(2026, 10, 19), date(2026, 10, 25)),
                        now=datetime(2026, 11, 1, 6, 0, tzinfo=UTC))  # 11:30 IST, today is running
    out = await registry.resolve(metric, ctx)
    assert out["value"] == 7  # the tile still includes today
    assert out["compare"] == {"from": "2026-10-19", "to": "2026-10-25", "basis": "complete_days",
                              "complete_days": 6, "current_value": 6}
    assert out["compare_value"] == 6
    assert out["series"] == [{"t": "2026-10-26", "value": 7}]  # the sparkline is unchanged
    assert seen[1] == ("2026-10-26", "2026-10-31", "2026-10-19", "2026-10-24")

    # A period that starts today has no complete day to compare.
    today_only = replace(ctx, period=Period(date(2026, 11, 1), date(2026, 11, 1)),
                         compare=Period(date(2026, 10, 31), date(2026, 10, 31)), cache={})
    out = await registry.resolve(metric, today_only)
    assert out["compare"]["basis"] == "complete_days" and out["compare"]["current_value"] is None
    assert out["compare_value"] is None
