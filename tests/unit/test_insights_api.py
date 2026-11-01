# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights API: role matrix, kill switch, company validation, no placeholder numbers, exports and their audit rows.

The routes run through the real app (auth middleware, route-scope
enforcement and rate limiting, with the token check and the limiter's
backend replaced). The database session is a stand-in that returns no rows,
so every figure a response carries comes from a resolver, never a default.
Resolver SQL is covered against Postgres in
``tests/integration/test_insights_api_postgres.py``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.insights import export as exports
from core.insights import metrics, metrics_cost, registry
from core.insights.reporting_settings import (
    ReportingSettings,
    defaults_for_region,
    settings_from_tenant,
    store_reporting_settings,
)
from core.rbac import ROLE_SCOPES

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000b1"))
COMPANY = str(uuid.UUID("00000000-0000-4000-8000-0000000000c2"))
REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES = ("admin", "cfo", "chro", "cmo", "coo", "auditor", "domain_lead", "analyst", "developer", "merchant")
EXPECTED_VIEWS: dict[str, set[str]] = {"admin": {"executive", "finance"}, "cfo": {"finance"}}


# ---------------------------------------------------------------- fakes


class FakeResult:
    """An empty result for any statement."""

    def __iter__(self):
        return iter(())

    def all(self):
        return []

    def first(self):
        return None

    def one(self):
        return (0, None, None)

    def scalar(self):
        return False

    def scalar_one(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return []


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.statements: list[Any] = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return FakeResult()

    def add(self, obj) -> None:
        self.added.append(obj)


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(_app_):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def _client(app, *, role: str | None, scopes: list[str], enabled: bool = True, session: FakeSession | None = None):
    claims: dict[str, Any] = {
        "sub": "insights-reader@example.test",
        "agenticorg:tenant_id": TENANT,
        "agenticorg:user_id": str(uuid.UUID("00000000-0000-4000-8000-0000000000d1")),
        "grantex:scopes": scopes,
    }
    if role is not None:
        claims["role"] = role
        claims["agenticorg:domains"] = None if role == "admin" else ["finance"]
    session = session or FakeSession()

    @asynccontextmanager
    async def _tenant_session(tid, company_id=None):
        assert str(tid) == TENANT  # always the authenticated tenant
        yield session

    async def _fake_validate(_token):
        return claims

    async def _noop(*_a, **_k):
        return None

    with (
        patch("auth.grantex_middleware.is_ip_blocked", return_value=False),
        patch("auth.grantex_middleware.record_auth_failure", side_effect=_noop),
        patch("auth.grantex_middleware.clear_auth_failures", side_effect=_noop),
        patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate),
        patch("auth.grantex_middleware.check_user_session_state", AsyncMock(return_value=None)),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("core.auth_state._get_redis", AsyncMock(return_value=None)),
        patch("core.auth_state._load_user_state_from_db", AsyncMock(side_effect=RuntimeError("no db in test"))),
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=enabled)),
        patch("api.v1.insights.get_tenant_session", _tenant_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=defaults_for_region("IN"))),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


def _role_client(app, role: str, **kwargs):
    return _client(app, role=role, scopes=list(ROLE_SCOPES[role]), **kwargs)


# ---------------------------------------------------------------- role matrix


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix_for_views(app, role: str) -> None:
    with _role_client(app, role) as client:
        status = client.get("/api/v1/insights/status")
        assert status.status_code == 200, status.text
        assert set(status.json()["views"]) == EXPECTED_VIEWS.get(role, set())
        for view in ("executive", "finance"):
            resp = client.get(f"/api/v1/insights/views/{view}")
            expected = 200 if view in EXPECTED_VIEWS.get(role, set()) else 403
            assert resp.status_code == expected, (role, view, resp.status_code, resp.text)
            if expected == 403:
                assert resp.json()["detail"] == "insights_view_forbidden"


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix_for_metrics_and_exports(app, role: str) -> None:
    readable = {m.key for m in registry.readable_metrics(EXPECTED_VIEWS.get(role, set()))}
    with _role_client(app, role) as client:
        catalog = client.get("/api/v1/insights/catalog")
        assert catalog.status_code == 200
        assert {m["key"] for m in catalog.json()["metrics"]} == readable
        for key in ("runs.success_rate", "cost.spend", "cost.budgets"):
            expected = 200 if key in readable else 403
            assert client.get(f"/api/v1/insights/metrics/{key}").status_code == expected, (role, key)
            exported = client.get(f"/api/v1/insights/metrics/{key}/export", params={"format": "json"})
            assert exported.status_code == expected, (role, key, exported.text)


def test_admin_reads_both_views_cfo_finance_only(app) -> None:
    with _role_client(app, "admin") as client:
        assert client.get("/api/v1/insights/views/executive").status_code == 200
        assert client.get("/api/v1/insights/views/finance").status_code == 200
    with _role_client(app, "cfo") as client:
        assert client.get("/api/v1/insights/views/finance").status_code == 200
        assert client.get("/api/v1/insights/views/executive").status_code == 403
        # A CFO may not read an executive-only metric directly either.
        assert client.get("/api/v1/insights/metrics/approvals.pending").status_code == 403


@pytest.mark.parametrize("role", ("analyst", "chro"))
def test_analyst_and_chro_are_refused(app, role: str) -> None:
    with _role_client(app, role) as client:
        for path in ("/views/finance", "/views/executive", "/metrics/cost.spend", "/metrics/cost.spend/export"):
            resp = client.get("/api/v1/insights" + path)
            assert resp.status_code == 403 and resp.json()["detail"] == "insights_view_forbidden", path


def test_a_role_claim_without_the_admin_scope_is_not_an_administrator(app) -> None:
    with _client(app, role="admin", scopes=["insights:read"]) as client:
        assert client.get("/api/v1/insights/status").json()["views"] == []
        assert client.get("/api/v1/insights/views/executive").status_code == 403


def test_a_caller_without_insights_read_is_refused_by_the_scope_family(app) -> None:
    with _client(app, role="cfo", scopes=["agents:read"]) as client:
        assert client.get("/api/v1/insights/views/finance").status_code == 403


def test_unknown_view_and_metric_are_not_found(app) -> None:
    with _role_client(app, "admin") as client:
        assert client.get("/api/v1/insights/views/operations").status_code == 404
        assert client.get("/api/v1/insights/metrics/runs.nothing").status_code == 404


# ---------------------------------------------------------------- kill switch


def test_disabled_answers_not_found_except_status(app) -> None:
    with _role_client(app, "admin", enabled=False) as client:
        status = client.get("/api/v1/insights/status")
        assert status.status_code == 200
        body = status.json()
        assert body["enabled"] is False and body["views"] == [] and body["platform_operator"] is False
        assert body["settings"] == {"timezone": "Asia/Kolkata", "fiscal_year_start_month": 4, "currency": "INR"}
        for path in (
            "/catalog",
            "/views/executive",
            "/views/finance",
            "/metrics/cost.spend",
            "/metrics/cost.spend/export",
        ):
            resp = client.get("/api/v1/insights" + path)
            assert resp.status_code == 404 and resp.json() == {"detail": "insights_disabled"}, path


def test_disabled_is_checked_before_the_role(app) -> None:
    with _role_client(app, "analyst", enabled=False) as client:
        assert client.get("/api/v1/insights/views/executive").json() == {"detail": "insights_disabled"}


# ---------------------------------------------------------------- filters


def test_company_id_must_belong_to_the_tenant(app) -> None:
    with _role_client(app, "admin") as client:
        malformed = client.get("/api/v1/insights/views/finance", params={"company_id": "not-a-uuid"})
        assert malformed.status_code == 422 and malformed.json()["detail"]["error"] == "invalid_company_id"
        from fastapi import HTTPException

        with patch(
            "api.v1.agents._require_company_for_tenant", AsyncMock(side_effect=HTTPException(404, "Company not found"))
        ) as lookup:
            foreign = client.get("/api/v1/insights/views/finance", params={"company_id": COMPANY})
        assert foreign.status_code == 422 and foreign.json()["detail"]["error"] == "invalid_company_id"
        assert str(lookup.await_args.args[0]) == TENANT  # checked against the caller's tenant
        with patch("api.v1.agents._require_company_for_tenant", AsyncMock(return_value=uuid.UUID(COMPANY))):
            seen = {}

            async def capture(metric, ctx):
                seen["company"] = ctx.company_id
                return {"key": metric.key, "value": None, "data_quality": "no_data"}

            with patch.object(registry, "resolve", capture):
                ok = client.get("/api/v1/insights/metrics/cost.spend", params={"company_id": COMPANY})
        assert ok.status_code == 200 and seen["company"] == uuid.UUID(COMPANY)


def test_period_and_grouping_are_validated(app) -> None:
    with _role_client(app, "admin") as client:
        base = "/api/v1/insights/metrics/runs.volume"
        assert client.get(base, params={"from": "2026-10-10", "to": "2026-10-01"}).status_code == 422
        assert client.get(base, params={"from": "2025-01-01", "to": "2026-10-01"}).status_code == 422
        assert client.get(base, params={"from": "not-a-date"}).status_code == 422
        assert client.get(base, params={"granularity": "hour"}).status_code == 422
        assert client.get(base, params={"compare": "last-year"}).status_code == 422
        refused = client.get("/api/v1/insights/metrics/runs.success_rate", params={"group_by": "status"})
        assert refused.status_code == 422 and refused.json()["detail"]["error"] == "group_by_not_allowed"
        assert client.get(base, params={"group_by": "tenant_id"}).status_code == 422
        assert client.get(base, params={"group_by": "domain", "granularity": "week"}).status_code == 200


# ---------------------------------------------------------------- no placeholder numbers


def _assert_no_invented_numbers(metric: dict[str, Any]) -> None:
    if metric["key"] == "approvals.pending":
        # A real count of an empty queue.
        assert metric["value"] == 0 and metric["data_quality"] == "measured" and metric["source"] == "hitl_queue"
        return
    assert metric["data_quality"] == "no_data", metric
    assert metric["value"] is None, metric
    assert metric.get("hint"), metric
    assert not metric.get("series") and not metric.get("rows"), metric
    assert metric.get("compare_value") is None


@pytest.mark.parametrize("view", ("executive", "finance"))
def test_an_empty_tenant_shows_no_numbers(app, view: str) -> None:
    with _role_client(app, "admin") as client:
        resp = client.get(f"/api/v1/insights/views/{view}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["view"] == view and body["needs_you"] == []
    assert {m["key"] for m in body["tiles"]} == set(registry.VIEWS[view].tiles)
    assert {m["key"] for m in body["charts"]} == set(registry.VIEWS[view].charts)
    for metric in body["tiles"] + body["charts"]:
        _assert_no_invented_numbers(metric)
        for field in ("key", "title", "definition", "unit", "source", "data_quality", "computed_at", "period"):
            assert field in metric, (metric["key"], field)


def test_the_view_period_defaults_to_the_last_30_local_days(app) -> None:
    with _role_client(app, "cfo") as client:
        body = client.get("/api/v1/insights/views/finance").json()
    period = body["period"]
    start, end = date.fromisoformat(period["from"]), date.fromisoformat(period["to"])
    assert (end - start).days == 29 and period["granularity"] == "day"
    assert body["compare"]["to"] == (start.fromordinal(start.toordinal() - 1)).isoformat()


# ---------------------------------------------------------------- exports


def _metric_with(key: str, part: dict[str, Any]):
    async def fake(ctx):
        return dict(part)

    return patch.dict(registry.METRICS, {key: replace(registry.METRICS[key], resolver=fake)})


def test_export_csv_guards_formulas_and_names_the_file(app) -> None:
    session = FakeSession()
    part = {
        "value": 3,
        "series": [
            {"t": "2026-10-01", "value": 1, "group": "=HYPERLINK(\"http://evil.example\")"},
            {"t": "2026-10-02", "value": 2, "group": "+cmd"},
            {"t": "2026-10-03", "value": -5, "group": "@sum"},
            {"t": "2026-10-04", "value": 0, "group": "-x"},
            {"t": "2026-10-05", "value": 0, "group": "\tlead"},
            {"t": "2026-10-06", "value": 0, "group": "plain"},
        ],
        "source": "run_facts",
        "data_quality": "measured",
    }
    with _metric_with("runs.volume", part), _role_client(app, "admin", session=session) as client:
        resp = client.get(
            "/api/v1/insights/metrics/runs.volume/export",
            params={"format": "csv", "from": "2026-10-01", "to": "2026-10-06", "group_by": "agent_type"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="insights-runs.volume-2026-10-01-2026-10-06.csv"' in resp.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8"))))
    assert rows[0] == ["t", "group", "value"]
    groups = [row[1] for row in rows[1:]]
    assert groups == ["'=HYPERLINK(\"http://evil.example\")", "'+cmd", "'@sum", "'-x", "'\tlead", "plain"]
    assert rows[3][2] == "-5"  # numbers are numbers, not guarded text
    (entry,) = session.added
    assert entry.event_type == "insights.export" and entry.action == "export"
    assert entry.tenant_id == uuid.UUID(TENANT) and entry.signature
    assert entry.details["metric"] == "runs.volume" and entry.details["format"] == "csv"
    assert entry.details["row_count"] == 6
    assert entry.details["filters"]["from"] == "2026-10-01" and entry.details["filters"]["group_by"] == "agent_type"
    assert "series" not in entry.details and "value" not in entry.details  # never the figures


def test_export_xlsx_has_data_filters_and_definitions_sheets(app) -> None:
    from openpyxl import load_workbook

    part = {
        "value": 2,
        "rows": [{"agent_name": "=1+1", "value": 1.5}, {"agent_name": "Invoices", "value": 0.5}],
        "source": "agent_cost_ledger",
        "currency": "USD",
        "unit": "usd",
        "data_quality": "measured",
    }
    session = FakeSession()
    with _metric_with("cost.by_agent", part), _role_client(app, "cfo", session=session) as client:
        resp = client.get("/api/v1/insights/metrics/cost.by_agent/export", params={"format": "xlsx"})
    assert resp.status_code == 200, resp.text
    workbook = load_workbook(io.BytesIO(resp.content))
    assert workbook.sheetnames == ["Data", "Filters", "Definitions"]
    data = [list(r) for r in workbook["Data"].iter_rows(values_only=True)]
    assert data == [["agent_name", "value"], ["'=1+1", 1.5], ["Invoices", 0.5]]
    filters = dict(workbook["Filters"].iter_rows(min_row=2, values_only=True))
    assert set(filters) >= {"from", "to", "granularity", "compare", "company_id", "group_by"}
    definitions = dict(workbook["Definitions"].iter_rows(min_row=2, values_only=True))
    assert definitions["key"] == "cost.by_agent" and definitions["currency"] == "USD"
    assert definitions["definition"] == registry.METRICS["cost.by_agent"].definition
    assert len(session.added) == 1 and session.added[0].details["format"] == "xlsx"


def test_export_json_carries_rows_filters_and_definition(app) -> None:
    part = {"value": 1, "series": [{"t": "2026-10-01", "value": 1}], "source": "run_facts", "data_quality": "measured"}
    with _metric_with("runs.volume", part), _role_client(app, "admin") as client:
        resp = client.get("/api/v1/insights/metrics/runs.volume/export", params={"format": "json"})
    body = json.loads(resp.content)
    assert body["rows"] == [{"t": "2026-10-01", "value": 1}]
    assert body["metric"]["key"] == "runs.volume" and body["filters"]["compare"] == "none"


def test_export_over_50000_rows_is_refused_with_a_hint(app) -> None:
    session = FakeSession()
    many = [{"t": "2026-10-01", "value": i, "group": f"g{i}"} for i in range(exports.MAX_EXPORT_ROWS + 1)]
    with _metric_with("runs.volume", {"value": 1, "series": many, "data_quality": "measured"}):
        with _role_client(app, "admin", session=session) as client:
            resp = client.get("/api/v1/insights/metrics/runs.volume/export", params={"format": "csv"})
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert detail["error"] == "export_too_large" and "Narrow the date range" in detail["hint"]
    assert detail["rows"] == exports.MAX_EXPORT_ROWS + 1
    assert session.added == []  # nothing exported, nothing audited
    exact = many[: exports.MAX_EXPORT_ROWS]
    exports.check_size(exact)  # exactly the cap is allowed


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@A1", "'@A1"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("\nx", "'\nx"),
        ("plain", "plain"),
        ("", ""),
        (-1, -1),
        (None, None),
    ],
)
def test_formula_guard(value, expected) -> None:
    assert exports.guard_cell(value) == expected


# ---------------------------------------------------------------- reporting settings


def test_reporting_defaults_follow_the_region() -> None:
    assert defaults_for_region("IN").model_dump() == {
        "timezone": "Asia/Kolkata",
        "fiscal_year_start_month": 4,
        "currency": "INR",
    }
    for region in ("US", "EU", None, ""):
        expected = {"timezone": "UTC", "fiscal_year_start_month": 1, "currency": "USD"}
        assert defaults_for_region(region).model_dump() == expected


def test_reporting_settings_validation() -> None:
    from pydantic import ValidationError

    ok = ReportingSettings(timezone="Europe/London", fiscal_year_start_month=7, currency="USD")
    assert ok.zone().key == "Europe/London"
    for bad in (
        {"timezone": "Mars/Base", "fiscal_year_start_month": 1, "currency": "USD"},
        {"timezone": "UTC", "fiscal_year_start_month": 13, "currency": "USD"},
        {"timezone": "UTC", "fiscal_year_start_month": 0, "currency": "USD"},
        {"timezone": "UTC", "fiscal_year_start_month": 1, "currency": "EUR"},
        {"timezone": "UTC", "fiscal_year_start_month": 1, "currency": "USD", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            ReportingSettings(**bad)


def test_stored_settings_override_defaults_field_by_field() -> None:
    stored = {"reporting": {"timezone": "Not/AZone", "fiscal_year_start_month": 7, "currency": "USD"}, "x": 1}
    merged = settings_from_tenant(stored, "IN")
    assert merged.model_dump() == {"timezone": "Asia/Kolkata", "fiscal_year_start_month": 7, "currency": "USD"}
    assert settings_from_tenant({"reporting": "junk"}, "US") == defaults_for_region("US")
    updated = store_reporting_settings(stored, merged)
    assert updated["x"] == 1 and updated["reporting"]["fiscal_year_start_month"] == 7
    assert stored["reporting"]["timezone"] == "Not/AZone"  # the input is not mutated


class _Tenant(SimpleNamespace):
    pass


class _TenantResult:
    def __init__(self, tenant):
        self.tenant = tenant

    def scalar_one_or_none(self):
        return self.tenant


class _TenantSession(FakeSession):
    def __init__(self, tenant):
        super().__init__()
        self.tenant = tenant

    async def execute(self, statement, params=None):
        return _TenantResult(self.tenant)


def test_config_reporting_get_and_put_are_admin_only_and_audited(app) -> None:
    tenant = _Tenant(id=uuid.UUID(TENANT), settings={"fleet_limits": {"max_active_agents": 3}}, data_region="IN")
    session = _TenantSession(tenant)

    @asynccontextmanager
    async def _tenant_session(tid, company_id=None):
        assert str(tid) == TENANT
        yield session

    with patch("api.v1.config.get_tenant_session", _tenant_session):
        with _role_client(app, "cfo") as client:
            assert client.get("/api/v1/config/reporting").status_code == 403
            assert client.put("/api/v1/config/reporting", json={}).status_code == 403
        with _role_client(app, "admin") as client:
            got = client.get("/api/v1/config/reporting")
            assert got.status_code == 200
            assert got.json() == {"timezone": "Asia/Kolkata", "fiscal_year_start_month": 4, "currency": "INR",
                                  "stored": False}
            bad = client.put(
                "/api/v1/config/reporting",
                json={"timezone": "Nowhere/City", "fiscal_year_start_month": 4, "currency": "INR"},
            )
            assert bad.status_code == 422 and session.added == []
            put = client.put(
                "/api/v1/config/reporting",
                json={"timezone": "UTC", "fiscal_year_start_month": 1, "currency": "USD"},
            )
            assert put.status_code == 200, put.text
    assert put.json()["stored"] is True
    assert tenant.settings["reporting"] == {"timezone": "UTC", "fiscal_year_start_month": 1, "currency": "USD"}
    assert tenant.settings["fleet_limits"] == {"max_active_agents": 3}  # other settings kept
    (entry,) = session.added
    assert entry.event_type == "config.reporting.write" and entry.signature
    assert entry.details["before"]["timezone"] == "Asia/Kolkata" and entry.details["after"]["currency"] == "USD"


# ---------------------------------------------------------------- resolver arithmetic


def _row(
    day: date,
    status: str,
    runs: int,
    *,
    hitl: int = 0,
    dq: str = "measured",
    grp: str = "",
    ms: int = 0,
    resume: bool = False,
):
    buckets = [0] * 12
    buckets[0] = runs if ms <= 500 else 0
    buckets[3] = runs if 2000 < ms <= 5000 else 0
    return {
        "day": day,
        "grp": grp,
        "status": status,
        "resume": resume,
        "data_quality": dq,
        "runs": runs,
        "hitl_required": hitl,
        "outcome_validated": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cached": 0,
        "tokens_total": 0,
        "cost_usd": Decimal("0"),
        "duration_ms_sum": ms * runs,
        "duration_n": runs,
        "confidence_sum": 0.0,
        "confidence_n": 0,
        "below_floor": 0,
        "buckets": buckets,
    }


def test_rates_count_what_their_definitions_say() -> None:
    day = date(2026, 10, 1)
    t = metrics.totals(
        [
            _row(day, "completed", 8, hitl=2),
            _row(day, "failed", 1),
            _row(day, "hitl_triggered", 1, hitl=1),
            _row(day, "paused", 0),
        ]
    )
    assert metrics._volume(t) == 10
    assert metrics._success_rate(t) == pytest.approx(88.9)  # 8 of the 9 completed or failed runs
    assert metrics._straight_through(t) == pytest.approx(75.0)  # 6 of the 8 completed runs, no person involved
    assert metrics.totals([])["buckets"] == [0] * 12
    assert metrics._success_rate(metrics.totals([_row(day, "hitl_triggered", 3)])) is None


def test_one_run_paused_for_approval_and_approved_is_one_successful_run() -> None:
    """Replay: a run pauses for approval (its fact: hitl_triggered) and is approved (the resume's fact: completed)."""
    day = date(2026, 10, 1)
    paused = _row(day, "hitl_triggered", 1, hitl=1)
    resumed = _row(day, "completed", 1, hitl=1, resume=True)
    t = metrics.totals([paused, resumed])
    assert metrics._volume(t) == 1
    assert metrics._success_rate(t) == 100.0
    assert metrics._straight_through(t) == 0.0
    # A resume fact written before resumes were always hitl_required is still not straight through.
    legacy = metrics.totals([paused, _row(day, "completed", 1, hitl=0, resume=True)])
    assert metrics._straight_through(legacy) == 0.0 and metrics._volume(legacy) == 1


def test_refusals_are_neither_successes_nor_failures() -> None:
    day = date(2026, 10, 1)
    t = metrics.totals(
        [
            _row(day, "completed", 9),
            _row(day, "failed", 1),
            _row(day, "refused", 5),  # operator halts, guardrail and gateway blocks
            _row(day, "limit_exceeded", 3),  # billing limits
            _row(day, "rejected", 2),
        ]
    )
    assert metrics._success_rate(t) == 90.0  # 9 of 10, the 10 refusals and rejections left out
    assert (t["refused"], t["rejected"], t["failed"]) == (8, 2, 1)
    assert metrics._volume(t) == 20
    assert metrics._success_rate(metrics.totals([_row(day, "refused", 4)])) is None


def test_latency_percentiles_come_from_the_buckets() -> None:
    day = date(2026, 10, 1)
    t = metrics.totals([_row(day, "completed", 90, ms=100), _row(day, "completed", 10, ms=3000)])
    assert metrics._p95(t) == 5000.0
    assert metrics.totals([_row(day, "completed", 1, ms=100)])["buckets"][0] == 1


@pytest.mark.asyncio
async def test_a_p95_over_ten_minutes_is_reported_as_a_lower_bound(monkeypatch) -> None:
    day = date(2026, 10, 30)
    slow = _row(day, "completed", 10)
    slow["buckets"] = [0] * 11 + [10]  # every run took longer than 10 minutes
    slow["duration_ms_sum"], slow["duration_n"] = 10 * 900_000, 10

    async def fake_run_data(ctx, period, *, group=None, domain_filter=True):
        return metrics.RunData(rows=[slow], sources={"run_facts"}, covered=[day])

    monkeypatch.setattr(metrics, "run_data", fake_run_data)
    ctx = _ctx()
    ctx.period = metrics.Period(day, day)
    out = await metrics.resolve_runs_latency(ctx)
    assert out["value"] == 600_000.0 and out["lower_bound"] is True
    rows = {r["label"]: r for r in out["rows"]}
    assert rows["p95"]["lower_bound"] is True and rows["p50"]["lower_bound"] is True
    response = await registry.resolve(registry.METRICS["runs.latency"], ctx)
    assert response["lower_bound"] is True and response["value"] == 600_000.0

    fast = _row(day, "completed", 10, ms=100)
    monkeypatch.setattr(
        metrics,
        "run_data",
        AsyncMock(return_value=metrics.RunData(rows=[fast], sources={"run_facts"}, covered=[day])),
    )
    out = await metrics.resolve_runs_latency(_ctx())
    assert out["value"] == 500.0 and "lower_bound" not in out


@pytest.mark.asyncio
async def test_skipped_days_are_missing_from_the_series_never_zero(monkeypatch) -> None:
    """Replay: days beyond the raw cap with no rollup are left out of the volume line, and the hint is accurate."""
    ctx = _ctx()  # October 2026, today is 31 October (IST)
    ctx.period = metrics.Period(date(2026, 10, 1), date(2026, 10, 31))
    skipped = [date(2026, 10, 1), date(2026, 10, 2)]
    covered = [date(2026, 10, 1) + timedelta(days=offset) for offset in range(2, 31)]
    rows = [_row(day, "completed", 2) for day in covered]

    async def fake_run_data(ctx, period, *, group=None, domain_filter=True):
        return metrics.RunData(rows=rows, sources={"run_facts"}, skipped=skipped, covered=covered)

    monkeypatch.setattr(metrics, "run_data", fake_run_data)
    out = await metrics.resolve_runs_volume(ctx)
    drawn = {point["t"] for point in out["series"]}
    assert "2026-10-01" not in drawn and "2026-10-02" not in drawn and "2026-10-03" in drawn
    assert out["value"] == 58
    assert "2 day(s) from 2026-10-01 to 2026-10-02 are not rolled up yet" in out["hint"]
    assert "catches up the last 31 days" in out["hint"] and "appear after its next run" in out["hint"]

    # Weekly: a week with some skipped days is drawn but marked partial.
    ctx.period = metrics.Period(date(2026, 10, 1), date(2026, 10, 31), "week")
    ctx.cache.clear()
    out = await metrics.resolve_runs_volume(ctx)
    first = out["series"][0]
    assert first["t"] == "2026-09-28" and first["partial"] is True


def test_data_quality_labels() -> None:
    assert metrics.quality_of([]) == "no_data"
    assert metrics.quality_of(["measured", "measured"]) == "measured"
    assert metrics.quality_of(["seeded"]) == "seeded"
    assert metrics.quality_of(["seeded", "measured"]) == "mixed"


def test_periods() -> None:
    today = date(2026, 11, 1)
    default = metrics.make_period(None, None, today=today)
    assert (default.start, default.end, default.granularity) == (date(2026, 10, 3), today, "day")
    assert metrics.previous_period(default) == metrics.Period(date(2026, 9, 3), date(2026, 10, 2), "day")
    year = metrics.make_period(date(2025, 11, 2), today, today=today)
    assert year.days == 365 and year.granularity == "week"
    with pytest.raises(metrics.PeriodError):
        metrics.make_period(date(2025, 1, 1), today, today=today)
    with pytest.raises(metrics.PeriodError):
        metrics.make_period(today, date(2026, 10, 1), today=today)
    assert metrics.bucket_start(date(2026, 10, 29), "week") == date(2026, 10, 26)
    assert metrics.bucket_start(date(2026, 10, 29), "month") == date(2026, 10, 1)
    weeks = metrics.bucket_starts(metrics.Period(date(2026, 10, 1), date(2026, 10, 14), "week"))
    assert weeks == [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)]


def _ctx(currency: str = "INR") -> metrics.MetricContext:
    reporting = ReportingSettings(timezone="Asia/Kolkata", fiscal_year_start_month=4, currency=currency)
    period = metrics.Period(date(2026, 10, 1), date(2026, 10, 31))
    return metrics.MetricContext(session=FakeSession(), tenant_id=uuid.UUID(TENANT), reporting=reporting,
                                 period=period, now=datetime(2026, 10, 31, 12, tzinfo=UTC))


def _days(*days: int) -> frozenset[date]:
    return frozenset(date(2026, 10, d) for d in days)


@pytest.mark.asyncio
async def test_cost_basis_and_currency_conversion(monkeypatch) -> None:
    from core.config import settings

    rate = (Decimal("84.0"), date(2026, 10, 30))
    monkeypatch.setattr(metrics_cost, "_usd_rate", AsyncMock(return_value=rate))
    monkeypatch.setattr(metrics_cost, "_ledger_days", AsyncMock(return_value=_days(1, 2)))
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)

    # No metered day: the ledger (USD) converted to an INR tenant's currency.
    monkeypatch.setattr(metrics_cost, "_rollup_days", AsyncMock(return_value=frozenset()))
    basis = await metrics_cost.cost_basis(_ctx("INR"))
    assert (basis.currency, basis.rollups_on) == ("INR", False)
    assert basis.shown(Decimal("2"), "USD") == Decimal("168.0")
    assert basis.conversion == {"kind": "fx_converted", "rate_to_inr": "84.0", "rate_date": "2026-10-30"}

    # Metered days (INR) and ledger-only days (USD): both converted to the tenant's currency.
    monkeypatch.setattr(metrics_cost, "_rollup_days", AsyncMock(return_value=_days(2, 3)))
    basis = await metrics_cost.cost_basis(_ctx("USD"))
    assert (basis.currency, basis.rollups_on) == ("USD", True)
    assert basis.shown(Decimal("168"), "INR") == Decimal("2") and basis.shown(Decimal("2"), "USD") == Decimal("2")
    basis = await metrics_cost.cost_basis(_ctx("INR"))
    assert basis.shown(Decimal("5"), "INR") == Decimal("5") and basis.conversion is not None

    # Every day metered and an INR tenant: no conversion needed.
    monkeypatch.setattr(metrics_cost, "_ledger_days", AsyncMock(return_value=_days(2)))
    basis = await metrics_cost.cost_basis(_ctx("INR"))
    assert basis.conversion is None and basis.rollups_on and basis.currency == "INR"

    # No FX row with both sources: INR and USD days cannot be added, so every day is read from the ledger.
    monkeypatch.setattr(metrics_cost, "_ledger_days", AsyncMock(return_value=_days(1, 2)))
    monkeypatch.setattr(metrics_cost, "_usd_rate", AsyncMock(return_value=None))
    basis = await metrics_cost.cost_basis(_ctx("INR"))
    assert basis.currency == "USD" and basis.rollups_on is False
    assert "cannot be added" in basis.hint and "No USD to INR rate" in basis.hint

    # No FX row, ledger only: the ledger's own currency, and the hint says so.
    monkeypatch.setattr(metrics_cost, "_rollup_days", AsyncMock(return_value=frozenset()))
    basis = await metrics_cost.cost_basis(_ctx("INR"))
    assert basis.currency == "USD" and basis.shown(Decimal("5"), "USD") == Decimal("5")
    assert "No USD to INR rate" in basis.hint


class _SpendSession:
    """Answers the per-day spend statements: rollup rows (INR) and ledger rows (USD) by day."""

    def __init__(self, rollups: dict[date, str], ledger: dict[date, str]) -> None:
        self.rollups = rollups
        self.ledger = ledger

    async def execute(self, statement, params=None):
        text = str(statement)
        if "spend_usage_rollups" in text:
            return [(day, "", Decimal(amount)) for day, amount in self.rollups.items()]
        return [(day, "", Decimal(amount)) for day, amount in self.ledger.items()]


@pytest.mark.asyncio
async def test_spend_is_read_per_day_from_rollups_where_metered_else_the_ledger(monkeypatch) -> None:
    """Replay: metering turned on mid-period. Earlier days come from the ledger, later ones from the rollups."""
    from core.config import settings

    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    ledger = {date(2026, 10, d): "1.00" for d in range(1, 31)}  # the ledger has every day, in USD
    rollups = {date(2026, 10, d): "100.00" for d in range(20, 31)}  # metered from 20 October, in INR
    ctx = _ctx("INR")
    ctx.session = _SpendSession(rollups, ledger)
    monkeypatch.setattr(metrics_cost, "_rollup_days", AsyncMock(return_value=frozenset(rollups)))
    monkeypatch.setattr(metrics_cost, "_ledger_days", AsyncMock(return_value=frozenset(ledger)))
    monkeypatch.setattr(metrics_cost, "_usd_rate", AsyncMock(return_value=(Decimal("80"), date(2026, 10, 1))))
    spend = await metrics_cost.daily_spend(ctx, date(2026, 10, 1), date(2026, 10, 31))
    assert spend.sources == {"spend_usage_rollups", "agent_cost_ledger"} and spend.mixed
    assert spend.amounts[(date(2026, 10, 19), "")] == Decimal("80.00")  # 1 USD from the ledger, in INR
    assert spend.amounts[(date(2026, 10, 20), "")] == Decimal("100.00")  # from the rollups, the ledger ignored
    assert spend.total() == Decimal("80") * 19 + Decimal("100") * 11
    out = await metrics_cost.resolve_cost_spend(ctx)
    assert out["source"] == "spend_usage_rollups+agent_cost_ledger" and out["data_quality"] == "mixed"
    assert out["value"] == float(Decimal("80") * 19 + Decimal("100") * 11) and out["currency"] == "INR"
    assert "Days with metered usage" in out["hint"]


def _spend(amounts: dict[date, str], sources: set[str] | None = None) -> metrics_cost.Spend:
    return metrics_cost.Spend(
        amounts={(day, ""): Decimal(value) for day, value in amounts.items()},
        sources=sources or {"agent_cost_ledger"},
    )


@pytest.mark.asyncio
async def test_the_forecast_fits_complete_days_and_needs_seven_days_of_spend(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    monkeypatch.setattr(metrics_cost, "_usd_rate", AsyncMock(return_value=(Decimal("80"), date(2026, 10, 1))))
    monkeypatch.setattr(metrics_cost, "_rollup_days", AsyncMock(return_value=frozenset()))
    spans: list[tuple[date, date]] = []

    def serve(data: dict[date, str]):
        async def fake(ctx, start, end, *, group=None):
            spans.append((start, end))
            return _spend({day: v for day, v in data.items() if start <= day <= end})

        return fake

    # 31 October (today, still running): 30 complete days of 1.00 USD, and today already 5.00 USD.
    month = {date(2026, 10, d): "1.00" for d in range(1, 31)} | {date(2026, 10, 31): "5.00"}
    monkeypatch.setattr(metrics_cost, "daily_spend", serve(month))
    out = await metrics_cost.resolve_cost_forecast(_ctx("USD"))
    row = out["rows"][0]
    assert out["data_quality"] == "estimated" and row["fit"] == "month_to_date" and row["fit_days"] == 30
    # A flat trend of 1.00 a day, fitted without today's partial 5.00: the rest of the month is today,
    # which has already spent 5.00, so the forecast is 30 + 5.
    assert row["point"] == 35.0 and row["actual"] == 35.0 and row["low"] == 35.0

    # A young month (3 November): fewer than 7 complete days of spend, so the last 14 complete days are fitted.
    ctx = _ctx("USD")
    ctx.now = datetime(2026, 11, 3, 6, tzinfo=UTC)
    history = {date(2026, 10, 20) + timedelta(days=i): "2.00" for i in range(14)}
    history |= {date(2026, 11, 3): "0.50"}
    spans.clear()
    monkeypatch.setattr(metrics_cost, "daily_spend", serve(history))
    out = await metrics_cost.resolve_cost_forecast(ctx)
    row = out["rows"][0]
    assert row["fit"] == "trailing_14_days" and row["fit_days"] == 14
    assert (date(2026, 10, 20), date(2026, 11, 2)) in spans  # complete days only: today is not fitted
    # 2 complete November days at 2.00, then 28 days (3 to 30 November) at the trend of 2.00.
    assert row["point"] == 4.0 + 28 * 2.0
    assert row["low"] < row["point"] < row["high"] or row["low"] == row["point"] == row["high"]

    # One day of spend: no forecast at all, never a zero-width band from a 1-point fit.
    monkeypatch.setattr(metrics_cost, "daily_spend", serve({date(2026, 11, 2): "9.00"}))
    out = await metrics_cost.resolve_cost_forecast(ctx)
    assert out["data_quality"] == "no_data" and out["value"] is None
    assert "needs 7 days of spend" in out["hint"] and "rows" not in out


def test_budget_state() -> None:
    assert metrics_cost._budget_state(None, 80) == "ok"
    assert metrics_cost._budget_state(79.9, 80) == "ok"
    assert metrics_cost._budget_state(80.0, 80) == "warn"
    assert metrics_cost._budget_state(100.0, 80) == "breached"


# ---------------------------------------------------------------- route enforcement pins


def test_insights_family_rate_classes_and_role_scopes_are_pinned() -> None:
    from api.route_enforcement import GRANTABLE_ROUTE_SCOPES, RATE_LIMIT_CLASSES, SCOPE_FAMILIES, required_scopes_for

    assert SCOPE_FAMILIES["insights"] == ("insights:read", "agenticorg:admin")
    assert RATE_LIMIT_CLASSES["reporting-read"] == (300, 60)
    assert RATE_LIMIT_CLASSES["reporting-export"] == (30, 60)
    assert required_scopes_for("insights.read", "GET") == ("insights:read",)
    assert "insights:read" in GRANTABLE_ROUTE_SCOPES and "agenticorg:admin" not in GRANTABLE_ROUTE_SCOPES
    holders = {role for role, scopes in ROLE_SCOPES.items() if "insights:read" in scopes}
    assert holders == {"cfo", "chro", "cmo", "coo", "auditor", "domain_lead", "analyst", "developer", "merchant"}
    assert ROLE_SCOPES["admin"] == ["agenticorg:admin"]


def test_routes_are_registered_with_their_metadata(app) -> None:
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import config as config_api
    from api.v1 import insights as insights_api

    paths = set(app.openapi()["paths"])
    expected = {
        "/api/v1/insights/status",
        "/api/v1/insights/catalog",
        "/api/v1/insights/views/{view}",
        "/api/v1/insights/metrics/{key}",
        "/api/v1/insights/metrics/{key}/export",
        "/api/v1/config/reporting",
    }
    assert expected <= paths
    meta = {r.path: getattr(r.endpoint, ROUTE_METADATA_ATTR) for r in insights_api.router.routes}
    assert set(meta) == {p.removeprefix("/api/v1") for p in expected if "insights" in p}
    for path, m in meta.items():
        assert m["scope"] == "insights.read" and m["auth_required"] and m["tenant_required"], path
        assert m["idempotency"] == "read-only" and m["audit_event"], path
        assert m["rate_limit"] == ("reporting-export" if path.endswith("/export") else "reporting-read"), path
    config_meta = {
        (r.path, tuple(sorted(r.methods))): getattr(r.endpoint, ROUTE_METADATA_ATTR) for r in config_api.router.routes
    }
    assert config_meta[("/config/reporting", ("PUT",))]["audit_event"] == "config.reporting.write"
    assert config_meta[("/config/reporting", ("GET",))]["rate_limit"] == "config-read"


# Rate-limit names routes declared before the class table existed for them: they
# fall back to the default (300 per minute). The list may only shrink; a new
# route names a declared class.
_UNDECLARED_LEGACY_RATE_LIMITS = frozenset(
    {
        "a2a-discovery", "a2a-task-read", "aa-consent-read", "aa-consent-request",
        "abm-campaign-launch", "abm-read", "abm-write", "admin-mutating",
        "agent-control", "agent-team-read", "agent-team-write", "agent-write",
        "approval-decision", "approval-write", "audit-read", "billing-status",
        "branding-admin-read", "branding-admin-write", "bridge-routing", "chat-history-read",
        "commerce-buyer-session", "commerce-runtime-external", "commerce-runtime-write", "company-write",
        "compliance-dsar-export", "compliance-dsar-request", "compliance-evidence-export", "compliance-mutating",
        "composio-catalog-read", "config-read", "config-write", "connector-test",
        "cost-dashboard-read", "cron-api-key", "default", "delegation-read",
        "delegation-write", "demo-seed", "external-bridge", "external-credential-check",
        "external-intent-aggregation", "external-provider-check", "feature-flag-evaluate", "feature-flag-read",
        "feature-flag-write", "gateway-callback", "governance-read", "governance-write",
        "health-diagnostics-read", "health-sla-read", "integration-status-read", "invoice-generate",
        "invoice-read", "knowledge-write", "manual-report-trigger", "manual-rpa-trigger",
        "mcp-discovery", "org-admin-read", "org-admin-write", "packs-install",
        "packs-read", "prompt-template-read", "prompt-template-write", "push-subscription-write",
        "push-test-send", "report-schedule-read", "report-schedule-write", "rpa-execution",
        "rpa-read", "sales-inbox-sync", "sales-read", "sales-write",
        "schemas-read", "schemas-write", "security-admin-read", "sop-read",
        "voice-call-create", "voice-config-read", "voice-config-write", "voice-test-connection",
        "websocket-connect", "workflow-control", "workflow-variant-read", "workflow-variant-write",
        "workflow-write",
    }
)  # fmt: skip


def test_every_route_rate_limit_names_a_declared_class() -> None:
    from api.route_enforcement import RATE_LIMIT_CLASSES

    named: set[str] = set()
    for path in (REPO_ROOT / "api").rglob("*.py"):
        named |= set(re.findall(r'rate_limit\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8")))
    undeclared = named - set(RATE_LIMIT_CLASSES)
    assert undeclared <= _UNDECLARED_LEGACY_RATE_LIMITS, sorted(undeclared - _UNDECLARED_LEGACY_RATE_LIMITS)
    for name in ("reporting-read", "reporting-export", "business-metrics-read"):
        assert name in named and name in RATE_LIMIT_CLASSES, name
