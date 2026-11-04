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
