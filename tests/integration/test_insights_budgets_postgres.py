# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights budgets against real Postgres under a NOBYPASSRLS role.

Tenant "Alpha" has 3 USD of ledger spend today (the shared seed). Its CFO adds a
monthly budget and sees this month's spend against it; changes and deletes are
audited with the values before and after. Auditors read, other roles and API keys
may not change budgets, Slack and webhook channels are refused, a company or cost
centre must be the tenant's own, and tenant "Bravo" neither sees nor changes
Alpha's budgets.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.integration.test_insights_isolation_postgres import _seed_tenant
from tests.integration.test_insights_isolation_postgres import world as world  # the shared fixture

pytestmark = pytest.mark.timeout(300)


def _person(role: str, user_id: uuid.UUID | None = None, *, admin: bool = False, machine: bool = False):
    scopes = ["agenticorg:admin"] if admin else ["insights:read", "insights:write"]
    claims = {
        "sub": f"apikey:{role}" if machine else f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(user_id or uuid.uuid4()),
        "agenticorg:domains": None,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode="api_key" if machine else None),
        query_params={},
    )


def _audit(world, tid: str) -> list[tuple[str, dict]]:
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        rows = conn.execute(
            text(
                "SELECT event_type, details FROM audit_log WHERE tenant_id = :t AND event_type LIKE 'insights.budget.%' "
                "ORDER BY created_at, event_type"
            ),
            {"t": tid},
        ).all()
    return [(r[0], r[1]) for r in rows]


def _company_of(world, tid: str) -> uuid.UUID:
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        return conn.execute(text("SELECT id FROM companies WHERE tenant_id = :t"), {"t": tid}).scalar_one()


async def _status(coro) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await coro
    detail = refused.value.detail
    return refused.value.status_code, detail["error"] if isinstance(detail, dict) else str(detail)


@pytest.mark.asyncio
async def test_budgets_are_set_read_changed_and_deleted_by_the_right_people(world) -> None:
    from api.v1 import insights_budgets as api

    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    cfo_id = uuid.uuid4()
    cfo = _person("cfo", cfo_id)

    body = api.BudgetIn(name="  Monthly   AI spend ", period="monthly", threshold_usd="10", warn_at_percent=80)
    created = (await api.create_budget(body, cfo, tenant_id=alpha.tid))["budget"]
    assert created["name"] == "Monthly AI spend" and created["period"] == "monthly"
    assert created["threshold_usd"] == "10.00" and created["spend_usd"] == "3.00"
    assert created["used_pct"] == 30.0 and created["state"] == "ok" and created["forecast_alert"] is True
    assert created["notify_channels"] == "email" and created["currency"] == "USD"
    budget_id = uuid.UUID(created["id"])

    # The same name in another case is a duplicate; Slack and webhooks are refused; limits are checked.
    assert await _status(api.create_budget(api.BudgetIn(name="monthly ai SPEND", period="weekly", threshold_usd="5"),
                                           cfo, tenant_id=alpha.tid)) == (409, "duplicate_name")
    assert await _status(api.create_budget(api.BudgetIn(name="Ops", period="daily", threshold_usd="5",
                                                        notify_channels=["email", "slack"]),
                                           cfo, tenant_id=alpha.tid)) == (422, "unsupported_channel")
    assert await _status(api.create_budget(api.BudgetIn(name="Ops", period="yearly", threshold_usd="5"),
                                           cfo, tenant_id=alpha.tid)) == (422, "invalid_period")
    assert await _status(api.create_budget(api.BudgetIn(name="Ops", period="daily", threshold_usd="0"),
                                           cfo, tenant_id=alpha.tid)) == (422, "invalid_threshold")
    assert await _status(api.create_budget(api.BudgetIn(name="Ops", period="daily", threshold_usd="5",
                                                        warn_at_percent=0),
                                           cfo, tenant_id=alpha.tid)) == (422, "invalid_warn_at")

    # A company must be the tenant's own.
    bravo_company = _company_of(world, bravo.tid)
    assert await _status(api.create_budget(api.BudgetIn(name="Ops", period="daily", threshold_usd="5",
                                                        company_id=bravo_company),
                                           cfo, tenant_id=alpha.tid)) == (404, "unknown_company")
    company_budget = (await api.create_budget(
        api.BudgetIn(name="Books daily", period="daily", threshold_usd="2", company_id=_company_of(world, alpha.tid)),
        cfo, tenant_id=alpha.tid))["budget"]
    assert company_budget["forecast_alert"] is False
    # The seeded agents carry no company, so a company budget reads none of today's 3 USD.
    assert company_budget["spend_usd"] == "0.00" and company_budget["state"] == "ok"

    # Who may read and change.
    auditor = await api.list_budgets(_person("auditor"), tenant_id=alpha.tid)
    assert auditor["can_edit"] is False and {b["name"] for b in auditor["budgets"]} == {"Monthly AI spend",
                                                                                         "Books daily"}
    assert (await api.list_budgets(cfo, tenant_id=alpha.tid))["can_edit"] is True
    assert await _status(api.list_budgets(_person("coo"), tenant_id=alpha.tid)) == (403, "insights_budgets_forbidden")
    patch = api.BudgetPatch(threshold_usd="2.5")
    assert await _status(api.update_budget(patch, _person("auditor"), budget_id, tenant_id=alpha.tid)) == (
        403, "insights_budgets_forbidden")
    assert await _status(api.update_budget(patch, _person("cfo", machine=True), budget_id, tenant_id=alpha.tid)) == (
        403, "insights_budget_change_needs_a_person")

    # Bravo sees none of Alpha's budgets and cannot change them.
    assert (await api.list_budgets(_person("cfo"), tenant_id=bravo.tid))["budgets"] == []
    assert await _status(api.update_budget(patch, _person("cfo"), budget_id, tenant_id=bravo.tid)) == (
        404, "unknown_budget")
    assert await _status(api.delete_budget(_person("cfo"), budget_id, tenant_id=bravo.tid)) == (404, "unknown_budget")

    # A lower limit: breached, and the evaluator may warn again this period.
    with world.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{world.schema}"')
        conn.execute(text("UPDATE budget_alerts SET last_triggered_at = now() WHERE id = :i"), {"i": budget_id})
    changed = (await api.update_budget(patch, cfo, budget_id, tenant_id=alpha.tid))["budget"]
    assert changed["threshold_usd"] == "2.50" and changed["state"] == "breached" and changed["used_pct"] == 120.0
    assert changed["last_triggered_at"] is None
    # Changing nothing writes no audit row.
    await api.update_budget(api.BudgetPatch(threshold_usd="2.50"), cfo, budget_id, tenant_id=alpha.tid)
    assert await _status(api.update_budget(api.BudgetPatch(), cfo, budget_id, tenant_id=alpha.tid)) == (
        422, "nothing_to_change")

    # The administrator deletes; the audit row keeps what it was.
    admin_id = uuid.uuid4()
    assert (await api.delete_budget(_person("admin", admin_id, admin=True), budget_id, tenant_id=alpha.tid)) == {
        "deleted": str(budget_id)}
    names = {b["name"] for b in (await api.list_budgets(cfo, tenant_id=alpha.tid))["budgets"]}
    assert names == {"Books daily"}

    trail = _audit(world, alpha.tid)
    kinds = [kind for kind, _ in trail]
    assert kinds.count("insights.budget.created") == 2
    assert kinds.count("insights.budget.updated") == 1 and kinds.count("insights.budget.deleted") == 1
    updated = next(details for kind, details in trail if kind == "insights.budget.updated")
    assert updated["before"]["threshold_usd"] == "10.00" and updated["after"]["threshold_usd"] == "2.50"
    deleted = next(details for kind, details in trail if kind == "insights.budget.deleted")
    assert deleted["before"]["name"] == "Monthly AI spend" and deleted["after"] is None
    assert _audit(world, bravo.tid) == []


@pytest.mark.asyncio
async def test_the_cost_view_reads_the_budget_and_the_forecast_alert_fires(world, monkeypatch) -> None:
    from api.v1 import insights_budgets as api
    from core.insights import alerts, metrics_cost, registry
    from core.insights.metrics import MetricContext, Period, local_today
    from core.insights.reporting_settings import settings_from_tenant

    alpha = _seed_tenant(world, "Alpha")
    cfo = _person("cfo")
    await api.create_budget(api.BudgetIn(name="Month", period="monthly", threshold_usd="100"), cfo,
                            tenant_id=alpha.tid)
    await api.create_budget(api.BudgetIn(name="Day", period="daily", threshold_usd="2"), cfo, tenant_id=alpha.tid)

    from core.database import get_tenant_session

    reporting = settings_from_tenant({}, "IN")
    today = local_today(reporting.zone())
    tid = uuid.UUID(alpha.tid)
    async with get_tenant_session(tid) as session:
        ctx = MetricContext(session=session, tenant_id=tid, reporting=reporting, period=Period(today, today, "day"))
        rows = await metrics_cost.budget_rows(ctx)
        by_name = {row["name"]: row for row in rows}
        assert by_name["Day"]["state"] == "breached" and by_name["Month"]["state"] == "ok"
        assert by_name["Month"]["tenant_wide"] is True
        summary = await metrics_cost.resolve_cost_budgets(ctx)
        assert summary["value"] == 1 and summary["state"] == "act"

        async def forecast(_ctx):
            return {"value": 130.0, "currency": "USD", "data_quality": "estimated"}

        from dataclasses import replace

        monkeypatch.setitem(registry.METRICS, "cost.forecast", replace(registry.METRICS["cost.forecast"],
                                                                       resolver=forecast))
        found = await alerts._forecast_over_budget(ctx, rows)
    assert [(c.rule_key, c.severity, c.owner_role, c.entity_ref) for c in found] == [
        ("cost.forecast_over_budget", "act", "cfo", by_name["Month"]["id"])
    ]
    assert found[0].detail["forecast_pct"] == 130.0
