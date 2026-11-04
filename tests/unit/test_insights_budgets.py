# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights budgets: validation, who may act, the forecast alert's currency, the evaluator's channels and routes."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.insights import alerts, budgets


def test_names_periods_thresholds_and_warn_at_are_validated() -> None:
    assert budgets.clean_name("  AI   spend ") == "AI spend"
    for bad in ("", "   ", "x" * 101, None):
        with pytest.raises(budgets.BudgetError, match="1 to 100"):
            budgets.clean_name(bad)
    assert budgets.clean_period(" Monthly ") == "monthly"
    with pytest.raises(budgets.BudgetError) as period:
        budgets.clean_period("yearly")
    assert period.value.error == "invalid_period"
    assert budgets.clean_threshold("12.345") == Decimal("12.35")
    assert budgets.clean_threshold(10) == Decimal("10.00")
    for bad in ("0", "-1", "10000000.01", "nan", "Infinity", "abc", True, "0.001"):
        with pytest.raises(budgets.BudgetError) as threshold:
            budgets.clean_threshold(bad)
        assert threshold.value.error == "invalid_threshold", bad
    assert budgets.clean_warn_at(80) == 80 and budgets.clean_warn_at("1") == 1 and budgets.clean_warn_at(100) == 100
    for bad in (0, 101, "x", True, None, 2.5):
        with pytest.raises(budgets.BudgetError) as warn:
            budgets.clean_warn_at(bad)
        assert warn.value.error == "invalid_warn_at", bad


def test_only_email_is_a_budget_channel() -> None:
    assert budgets.clean_channels(None) == "email"
    assert budgets.clean_channels(["Email", "email"]) == "email"
    assert budgets.clean_channels("email") == "email"
    for refused in (["slack"], ["email", "webhook"], "slack,email"):
        with pytest.raises(budgets.BudgetError, match="not offered") as exc:
            budgets.clean_channels(refused)
        assert exc.value.error == "unsupported_channel"
    for unknown in (["sms"], [], ""):
        with pytest.raises(budgets.BudgetError, match=r"\['email'\]"):
            budgets.clean_channels(unknown)


def test_who_reads_and_who_changes_budgets() -> None:
    assert budgets.may_read("auditor", is_admin=False) and budgets.may_read("CFO", is_admin=False)
    assert budgets.may_read("", is_admin=True)
    assert not budgets.may_read("coo", is_admin=False) and not budgets.may_read("analyst", is_admin=False)
    assert budgets.may_manage("cfo", is_admin=False) and budgets.may_manage("admin", is_admin=True)
    assert not budgets.may_manage("auditor", is_admin=False) and not budgets.may_manage("chro", is_admin=False)


def test_state_follows_the_warn_at_percent() -> None:
    assert budgets.state_of(None, 80) == "ok"
    assert budgets.state_of(79.9, 80) == "ok" and budgets.state_of(80, 80) == "warn"
    assert budgets.state_of(100, 80) == "breached"


def test_settings_of_records_the_stored_values() -> None:
    row = SimpleNamespace(name="AI", period="weekly", threshold_usd=Decimal("5"), warn_at_percent=90,
                          company_id=None, cost_center_id=uuid.UUID(int=7), notify_channels="email")
    assert budgets.settings_of(row) == {
        "name": "AI",
        "period": "weekly",
        "threshold_usd": "5.00",
        "warn_at_percent": 90,
        "company_id": None,
        "cost_center_id": str(uuid.UUID(int=7)),
        "notify_channels": "email",
    }


def test_the_forecast_is_compared_in_usd() -> None:
    assert alerts.forecast_in_usd({"value": 120.5, "currency": "USD", "data_quality": "estimated"}) == 120.5
    inr = {"value": 8400.0, "currency": "INR", "conversion": {"rate_to_inr": "84"}, "data_quality": "estimated"}
    assert alerts.forecast_in_usd(inr) == 100.0
    assert alerts.forecast_in_usd({"value": 8400.0, "currency": "INR"}) is None  # no rate recorded
    assert alerts.forecast_in_usd({"value": 1.0, "currency": "INR", "conversion": {"rate_to_inr": "0"}}) is None
    assert alerts.forecast_in_usd({"value": 1.0, "currency": "INR", "conversion": {"rate_to_inr": "x"}}) is None
    assert alerts.forecast_in_usd({"value": None, "currency": "USD"}) is None
    assert alerts.forecast_in_usd({"value": 3.0, "currency": "USD", "data_quality": "no_data"}) is None
    assert alerts.forecast_in_usd({"value": 3.0, "currency": "EUR"}) is None


@pytest.mark.asyncio
async def test_the_forecast_alert_reads_only_tenant_wide_monthly_budgets(monkeypatch) -> None:
    from dataclasses import replace

    from core.insights import registry

    async def forecast(_ctx):
        return {"value": 105.0, "currency": "USD", "data_quality": "estimated"}

    monkeypatch.setitem(registry.METRICS, "cost.forecast", replace(registry.METRICS["cost.forecast"],
                                                                   resolver=forecast))
    rows = [
        {"id": "m", "name": "Month", "period": "monthly", "threshold": 100.0, "tenant_wide": True},
        {"id": "c", "name": "Company", "period": "monthly", "threshold": 50.0, "tenant_wide": False},
        {"id": "w", "name": "Week", "period": "weekly", "threshold": 10.0, "tenant_wide": True},
        {"id": "big", "name": "Big", "period": "monthly", "threshold": 1000.0, "tenant_wide": True},
        {"id": "zero", "name": "Zero", "period": "monthly", "threshold": 0, "tenant_wide": True},
    ]
    found = await alerts._forecast_over_budget(SimpleNamespace(company_id=None), rows)
    assert [(c.entity_ref, c.severity, c.link) for c in found] == [("m", "watch", "/dashboard/insights/budgets")]
    assert "$105.00" in found[0].title and "$100.00" in found[0].title
    assert await alerts._forecast_over_budget(SimpleNamespace(company_id=uuid.uuid4()), rows) == []
    assert await alerts._forecast_over_budget(SimpleNamespace(company_id=None), rows[1:3]) == []


@pytest.mark.asyncio
async def test_the_evaluator_emails_escaped_text_and_never_posts_to_platform_urls(monkeypatch) -> None:
    from core.billing import budget_evaluator as be

    async def recipient(_alert):
        return "owner@tenant.example"

    sent: list[tuple[str, str, str]] = []
    posted: list[str] = []
    monkeypatch.setattr(be, "_alert_recipient", recipient)
    monkeypatch.setattr("core.email.send_email", lambda to, subject, html: sent.append((to, subject, html)) or True)
    monkeypatch.setenv("SLACK_BUDGET_WEBHOOK_URL", "https://hooks.example.test/platform")
    monkeypatch.setenv("BUDGET_ALERT_WEBHOOK_URL", "https://hooks.example.test/platform")

    class _Client:
        def __init__(self, *a, **k) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None: ...

        async def post(self, url, **_):
            posted.append(url)

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    alert = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="<b>AI</b>", threshold_usd=Decimal("10"),
                            period="monthly", notify_channels="email,slack,webhook")
    await be._send_notification(alert, spend=Decimal("9"), percent=90)
    assert posted == []
    assert len(sent) == 1 and sent[0][0] == "owner@tenant.example"
    assert "<b>AI</b>" not in sent[0][2] and "&lt;b&gt;AI&lt;/b&gt;" in sent[0][2]


def test_budget_routes_need_insights_write_and_are_registered() -> None:
    from api.route_enforcement import required_scopes_for
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import insights_budgets

    meta = {
        (route.path, tuple(sorted(route.methods))): getattr(route.endpoint, ROUTE_METADATA_ATTR)
        for route in insights_budgets.router.routes
    }
    assert set(meta) == {
        ("/insights/budgets", ("GET",)),
        ("/insights/budgets", ("POST",)),
        ("/insights/budgets/{budget_id}", ("PATCH",)),
        ("/insights/budgets/{budget_id}", ("DELETE",)),
    }
    for (path, methods), m in meta.items():
        read = methods == ("GET",)
        assert m["scope"] == ("insights.budgets.read" if read else "insights.budgets.write"), path
        assert required_scopes_for(m["scope"], methods[0]) == (("insights:read",) if read else ("insights:write",))
        assert m["rate_limit"] == ("reporting-read" if read else "reporting-admin"), path
        assert m["auth_required"] and m["tenant_required"] and m["audit_event"].startswith("insights.budget")
