# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights benchmarks: the window, positions against the band, who reads, the floors and the routes."""

from __future__ import annotations

import importlib
from datetime import date

import pytest

from core.insights import benchmarks


def test_the_window_is_the_last_30_complete_days() -> None:
    assert benchmarks.window(date(2026, 11, 4)) == (date(2026, 10, 5), date(2026, 11, 3))  # 30 days
    assert benchmarks.plan_cohort("pro") == "plan:pro" and benchmarks.plan_cohort(None) == "plan:none"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (1.0, "below_p25"), (2.0, "p25_p50"), (3.0, "p50_p75"), (4.0, "p50_p75"), (4.1, "above_p75")],
)
def test_position_against_the_band(value, expected) -> None:
    assert benchmarks.position(value, 2.0, 3.0, 4.0) == expected


def test_who_reads_benchmarks() -> None:
    assert benchmarks.may_read("CFO", is_admin=False) and benchmarks.may_read("coo", is_admin=False)
    assert benchmarks.may_read("", is_admin=True)
    assert not benchmarks.may_read("auditor", is_admin=False) and not benchmarks.may_read("cmo", is_admin=False)


def test_the_floors_match_the_database_function() -> None:
    migration = importlib.import_module("migrations.versions.v6_z117_insights_benchmarks")
    assert migration.revision == "v6z117_insights_benchmarks" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z116_insights_export_formats"
    assert (migration.MIN_TENANTS, migration.MIN_RUNS) == (benchmarks.MIN_TENANTS, benchmarks.MIN_RUNS) == (20, 100)
    sql = migration.COHORTS_SQL
    assert f"GREATEST(COALESCE(p_min_tenants, {migration.MIN_TENANTS}), {migration.MIN_TENANTS})" in sql
    assert f"GREATEST(COALESCE(p_min_runs, {migration.MIN_RUNS}), {migration.MIN_RUNS})" in sql
    assert "d.environment = 'production' AND d.data_quality = 'measured'" in sql
    assert "SECURITY DEFINER" in sql and "SET search_path = pg_catalog" in sql
    assert "tenant_id" not in sql.split("RETURNS TABLE(")[1].split(")")[0]
    for key in benchmarks.KEYS:
        assert f"('{key}'::text" in sql, key
    from core.models.insights import InsightsBenchmark

    assert "tenant_id" not in InsightsBenchmark.__table__.c


def test_benchmark_routes_and_scopes() -> None:
    from api.route_enforcement import required_scopes_for
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import insights_benchmarks

    meta = {
        (route.path, tuple(sorted(route.methods))): getattr(route.endpoint, ROUTE_METADATA_ATTR)
        for route in insights_benchmarks.router.routes
    }
    assert set(meta) == {("/insights/benchmarks", ("GET",)), ("/insights/benchmarks/opt-in", ("PUT",))}
    read = meta[("/insights/benchmarks", ("GET",))]
    write = meta[("/insights/benchmarks/opt-in", ("PUT",))]
    assert required_scopes_for(read["scope"], "GET") == ("insights:read",)
    # Opting the organisation in is the administrator's, at the route as well as in the handler.
    assert required_scopes_for(write["scope"], "PUT") == ("agenticorg:admin",)


def test_the_nightly_task_is_scheduled_and_off_without_insights(monkeypatch) -> None:
    from core.config import settings
    from core.tasks import insights_tasks
    from core.tasks.celery_app import app

    entry = app.conf.beat_schedule["insights-compute-benchmarks"]
    assert entry["task"] == "core.tasks.insights_tasks.compute_benchmarks"
    monkeypatch.setattr(settings, "insights_enabled", False)
    assert insights_tasks.compute_benchmarks() == {"skipped": "insights_disabled"}


class _Session:
    async def __aenter__(self):
        return "session"

    async def __aexit__(self, *exc):
        return False


def _wire(monkeypatch, *, role: str, is_admin: bool, is_machine: bool = False) -> list:
    import uuid
    from types import SimpleNamespace

    from api.v1 import insights_benchmarks as route

    calls: list = []

    async def enabled(tid):
        calls.append(("enabled", tid))

    async def card(session, tid):
        calls.append(("card", session, tid))
        return {"opted_in": True, "cohorts": []}

    async def set_opt_in(session, tid, user_id, opt_in):
        calls.append(("opt_in", session, tid, user_id, opt_in))
        return {"opted_in": opt_in}

    caller = SimpleNamespace(role=role, is_admin=is_admin, is_machine=is_machine, user_id=uuid.uuid4())
    monkeypatch.setattr(route, "_require_enabled", enabled)
    monkeypatch.setattr(route, "caller_from_request", lambda request: caller)
    monkeypatch.setattr(route, "get_tenant_session", lambda tid: _Session())
    monkeypatch.setattr(route.benchmarks, "card", card)
    monkeypatch.setattr(route.benchmarks, "set_opt_in", set_opt_in)
    return calls


@pytest.mark.parametrize(
    ("role", "is_admin", "is_machine", "can_change"),
    [("admin", True, False, True), ("cfo", False, False, False), ("admin", True, True, False)],
)
async def test_reading_benchmarks_says_who_may_change_them(monkeypatch, role, is_admin, is_machine, can_change) -> None:
    import uuid

    from api.v1 import insights_benchmarks as route

    calls = _wire(monkeypatch, role=role, is_admin=is_admin, is_machine=is_machine)
    tid = uuid.uuid4()
    body = await route.read_benchmarks(object(), str(tid))
    assert body == {"opted_in": True, "cohorts": [], "can_change": can_change}
    assert calls == [("enabled", tid), ("card", "session", tid)]


async def test_roles_outside_the_card_cannot_read_benchmarks(monkeypatch) -> None:
    import uuid

    from fastapi import HTTPException

    from api.v1 import insights_benchmarks as route

    calls = _wire(monkeypatch, role="analyst", is_admin=False)
    with pytest.raises(HTTPException) as caught:
        await route.read_benchmarks(object(), str(uuid.uuid4()))
    assert caught.value.status_code == 403 and caught.value.detail == "insights_benchmarks_forbidden"
    assert [c[0] for c in calls] == ["enabled"]


async def test_opting_in_is_the_administrators_own_tenant_only(monkeypatch) -> None:
    import uuid

    from fastapi import HTTPException

    from api.deps import ActiveHumanAdmin
    from api.v1 import insights_benchmarks as route

    calls = _wire(monkeypatch, role="admin", is_admin=True)
    tid, uid = uuid.uuid4(), uuid.uuid4()
    admin = ActiveHumanAdmin(user_id=uid, tenant_id=tid, email="a@example.com", role="admin")
    assert await route.set_opt_in(route.OptIn(opt_in=True), admin, str(tid)) == {"opted_in": True}
    assert calls == [("enabled", tid), ("opt_in", "session", tid, uid, True)]

    with pytest.raises(HTTPException) as caught:
        await route.set_opt_in(route.OptIn(opt_in=False), admin, str(uuid.uuid4()))
    assert caught.value.status_code == 403
    assert len(calls) == 2
