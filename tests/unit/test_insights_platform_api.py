# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights platform view: the operator matrix, ranges, response shapes, audit rows and route metadata.

The routes run through the real app (auth middleware, route-scope
enforcement and rate limiting, with the token check and the limiter's backend
replaced). The user lookup and the platform session are stand-ins; the
aggregate functions themselves are proven against Postgres in
``tests/integration/test_insights_platform_postgres.py``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from api.v1.auth import MFA_CLAIM
from auth.jwt import local_issuer
from core.insights import platform
from core.rbac import ROLE_SCOPES

OPERATOR_TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
LISTED_TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000a3")
USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000000d1")
ADMIN_SCOPES = list(ROLE_SCOPES["admin"])
# A claims_extra value that removes the claim.
_ABSENT = object()
PATHS = ("/summary", "/tenants", "/posture", "/export")
POSTURE = {
    "computed_at": "2026-11-01T00:00:00+00:00",
    "ok": True,
    "checks": [{"key": "schema", "ok": True}],
    "schema": {"ok": True, "database_heads": ["x"], "code_heads": ["x"]},
}


def _user(*, mfa: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID,
        tenant_id=OPERATOR_TENANT,
        mfa_enabled=mfa,
        mfa_totp_secret="encrypted-secret" if mfa else None,
    )


class _Result:
    def __init__(self, value: Any = None, rows: list[dict[str, Any]] | None = None) -> None:
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def mappings(self):
        return self

    def all(self):
        return self._rows


class UserSession:
    """The operator tenant's session: answers the user lookup, collects audit rows."""

    def __init__(self, user: Any) -> None:
        self.user = user
        self.statements: list[Any] = []
        self.added: list[Any] = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return _Result(self.user)

    def add(self, obj) -> None:
        self.added.append(obj)


class PlatformSession:
    """The platform session: the two aggregate functions' rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "insights_platform_daily" in sql:
            return _Result(rows=[
                {"day": date(2026, 10, 30), "tenants_active": 2, "runs": 10, "failed": 1, "tokens": 900,
                 "cost_usd": Decimal("1.500000")},
                {"day": date(2026, 10, 31), "tenants_active": 1, "runs": 4, "failed": 0, "tokens": 100,
                 "cost_usd": Decimal("0.250000")},
            ])
        if "insights_platform_operations" in sql:
            return _Result(rows=[
                {"exports_queued": 1, "exports_running": 0, "exports_ready": 6, "exports_failed": 2,
                 "exports_expired": 0, "oldest_open_minutes": 12, "tenants_late": 1, "max_lag_hours": Decimal("3.5")},
            ])
        if "insights_benchmark_participation" in sql:
            return _Result(23)
        if "insights_platform_tenants" in sql:
            return _Result(rows=[
                {"tenant_id": LISTED_TENANT, "tenant_name": "Synthetic Traders", "plan": "enterprise", "runs": 14,
                 "failed": 1, "cost_usd": Decimal("1.750000"), "active_agents": 3,
                 "last_active_day": date(2026, 10, 31), "reconciled_nights": 15, "last_rollup_status": "reconciled"},
                {"tenant_id": OTHER_TENANT, "tenant_name": "Synthetic Idle", "plan": "free", "runs": 0,
                 "failed": 0, "cost_usd": Decimal("0"), "active_agents": 0, "last_active_day": None,
                 "reconciled_nights": 0, "last_rollup_status": None},
            ])
        return _Result()


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(_app_):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def _client(
    app,
    *,
    tenant: uuid.UUID = OPERATOR_TENANT,
    role: str = "admin",
    scopes: list[str] | None = None,
    user: Any = "default",
    operator_setting: str = str(OPERATOR_TENANT),
    claims_extra: dict[str, Any] | None = None,
    lookup_error: Exception | None = None,
):
    scopes = ADMIN_SCOPES if scopes is None else scopes
    claims: dict[str, Any] = {
        "sub": "operator@example.test",
        "agenticorg:tenant_id": str(tenant),
        "agenticorg:user_id": str(USER_ID),
        "grantex:scopes": scopes,
        "role": role,
        "agenticorg:domains": None,
        # As minted by POST /auth/mfa/verify (api.v1.auth.session_token_for(mfa_verified=True)).
        MFA_CLAIM: True,
        "iss": local_issuer(),
        **(claims_extra or {}),
    }
    for key in [key for key, value in claims.items() if value is _ABSENT]:
        del claims[key]
    user_session = UserSession(_user() if user == "default" else user)
    platform_session = PlatformSession()
    sessions: dict[str, Any] = {"user": user_session, "platform": platform_session, "tenants": []}

    @asynccontextmanager
    async def _tenant_session(tid, company_id=None):
        sessions["tenants"].append(tid)
        if lookup_error is not None:
            raise lookup_error
        yield user_session

    @asynccontextmanager
    async def _platform_session():
        yield platform_session

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
        patch("auth.grantex_middleware.extract_tenant_id", return_value=str(tenant)),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("core.auth_state._get_redis", AsyncMock(return_value=None)),
        patch("core.auth_state._load_user_state_from_db", AsyncMock(side_effect=RuntimeError("no db in test"))),
        patch("core.database.get_tenant_session", _tenant_session),
        patch("api.v1.insights_platform.get_tenant_session", _tenant_session),
        patch("api.v1.insights_platform.get_platform_session", _platform_session),
        patch("core.insights.platform.posture", AsyncMock(return_value=POSTURE)),
        patch.object(platform.settings, "platform_operator_tenant_id", operator_setting),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            c.sessions = sessions  # type: ignore[attr-defined]
            yield c


# ---------------------------------------------------------------- operator matrix


def test_the_operator_reads_every_route(app) -> None:
    with _client(app) as client:
        for path in PATHS:
            resp = client.get("/api/v1/platform/insights" + path)
            assert resp.status_code == 200, (path, resp.text)


def _without_timestamp(body: dict[str, Any]) -> dict[str, Any]:
    error = dict(body.get("error") or {})
    error.pop("timestamp", None)
    return {**body, "error": error}


def _assert_hidden(client) -> None:
    # The same answer as a path that does not exist.
    unknown = client.get("/api/v1/platform/insights/no-such-route")
    assert unknown.status_code == 404
    for path in PATHS:
        resp = client.get("/api/v1/platform/insights" + path)
        assert resp.status_code == 404, (path, resp.text)
        assert _without_timestamp(resp.json()) == _without_timestamp(unknown.json()), (path, resp.text)
    # Nothing cross-tenant was read.
    assert client.sessions["platform"].calls == []


def test_an_administrator_of_another_tenant_is_not_found(app) -> None:
    with _client(app, tenant=OTHER_TENANT) as client:
        _assert_hidden(client)
        # Decided on the tenant alone: no user lookup.
        assert client.sessions["tenants"] == []


def test_an_operator_tenant_user_who_is_not_an_active_admin_in_the_database_is_not_found(app) -> None:
    # The token still claims admin; the database (role = 'admin', status = 'active') says otherwise.
    with _client(app, user=None) as client:
        _assert_hidden(client)
        statement = client.sessions["user"].statements[0]
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "users.role = 'admin'" in sql and "users.status = 'active'" in sql
        assert f"users.tenant_id = '{OPERATOR_TENANT}'" in sql
        assert client.sessions["tenants"] == [OPERATOR_TENANT] * len(PATHS)


def test_an_operator_tenant_analyst_is_refused_before_the_handler(app) -> None:
    # The platform scope family requires agenticorg:admin, which an analyst does not hold.
    with _client(app, role="analyst", scopes=list(ROLE_SCOPES["analyst"])) as client:
        for path in PATHS:
            resp = client.get("/api/v1/platform/insights" + path)
            assert resp.status_code == 403, (path, resp.text)
        assert client.sessions["platform"].calls == [] and client.sessions["tenants"] == []


def test_an_operator_without_mfa_is_not_found(app) -> None:
    with _client(app, user=_user(mfa=False)) as client:
        _assert_hidden(client)
    # The bare legacy flag without an enrolled secret is not MFA either.
    half = _user()
    half.mfa_totp_secret = None
    with _client(app, user=half) as client:
        _assert_hidden(client)


@pytest.mark.parametrize(
    "extra",
    [
        # A session minted without the second factor: password-only, Google-only, or before enrolment.
        {MFA_CLAIM: _ABSENT},
        {MFA_CLAIM: False},
        {MFA_CLAIM: "true"},
        # Not minted by this service.
        {"iss": "https://idp.example.com"},
        # SSO never sets the claim; refused even if a token somehow carried it.
        {"auth_method": "sso_oidc", "sso_provider": "okta"},
        {"auth_method": "sso_oidc", MFA_CLAIM: _ABSENT},
    ],
)
def test_an_enrolled_operator_without_a_second_factor_session_is_not_found(app, extra) -> None:
    # The user row is an active admin of the operator tenant, enrolled in MFA: only the session differs.
    with _client(app, claims_extra=extra) as client:
        _assert_hidden(client)
        # Decided on the token alone: no user lookup.
        assert client.sessions["tenants"] == []
    with (
        _client(app, claims_extra=extra) as client,
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=False)),
        patch("api.v1.insights.get_tenant_session", _noop_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=_reporting())),
    ):
        assert client.get("/api/v1/insights/status").json()["platform_operator"] is False


def test_an_api_key_is_not_found(app) -> None:
    with _client(app, claims_extra={"sub": "apikey:ao_sk_test"}) as client:
        _assert_hidden(client)
        assert client.sessions["tenants"] == []


def test_a_grantex_grant_is_not_found(app) -> None:
    with _client(app, claims_extra={"grantex:grant_id": "grant-1"}) as client:
        _assert_hidden(client)


@pytest.mark.parametrize("setting", ["", "   ", "not-a-uuid"])
def test_no_operator_tenant_setting_hides_the_view_from_everyone(app, setting: str) -> None:
    with (
        _client(app, operator_setting=setting) as client,
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=False)),
        patch("api.v1.insights.get_tenant_session", _noop_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=_reporting())),
    ):
        _assert_hidden(client)
        status = client.get("/api/v1/insights/status")
        assert client.sessions["tenants"] == []
        assert status.status_code == 200 and status.json()["platform_operator"] is False


@pytest.mark.parametrize("mode", ["api_key", "grantex", "commerce_buyer", "something_else"])
async def test_machine_auth_modes_are_never_an_operator(mode: str) -> None:
    request = SimpleNamespace(state=SimpleNamespace(
        claims={"sub": "operator@example.test", "agenticorg:user_id": str(USER_ID), MFA_CLAIM: True,
                "iss": local_issuer()},
        auth_mode=mode,
        tenant_id=str(OPERATOR_TENANT),
        agent_id=None,
    ))
    with patch.object(platform.settings, "platform_operator_tenant_id", str(OPERATOR_TENANT)):
        assert await platform.platform_operator(request) is None
        with pytest.raises(HTTPException) as exc:
            await platform.require_platform_operator(request)
    assert exc.value.status_code == 404


async def test_an_agent_token_is_never_an_operator() -> None:
    request = SimpleNamespace(state=SimpleNamespace(
        claims={"sub": "agent", "agenticorg:agent_id": "a1"}, auth_mode="legacy",
        tenant_id=str(OPERATOR_TENANT), agent_id="a1",
    ))
    with patch.object(platform.settings, "platform_operator_tenant_id", str(OPERATOR_TENANT)):
        assert await platform.platform_operator(request) is None


# ---------------------------------------------------------------- status flag


def test_status_reports_platform_operator(app) -> None:
    with (
        _client(app) as client,
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=False)),
        patch("api.v1.insights.get_tenant_session", _noop_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=_reporting())),
    ):
        assert client.get("/api/v1/insights/status").json()["platform_operator"] is True
    with (
        _client(app, tenant=OTHER_TENANT) as client,
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=False)),
        patch("api.v1.insights.get_tenant_session", _noop_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=_reporting())),
    ):
        assert client.get("/api/v1/insights/status").json()["platform_operator"] is False


def test_status_never_fails_when_the_operator_lookup_fails(app) -> None:
    with (
        _client(app, lookup_error=RuntimeError("database unavailable")) as client,
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=False)),
        patch("api.v1.insights.get_tenant_session", _noop_session),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=_reporting())),
    ):
        resp = client.get("/api/v1/insights/status")
        assert resp.status_code == 200 and resp.json()["platform_operator"] is False


@asynccontextmanager
async def _noop_session(tid, company_id=None):
    yield SimpleNamespace()


def _reporting():
    from core.insights.reporting_settings import defaults_for_region

    return defaults_for_region("IN")


# ---------------------------------------------------------------- responses


def test_summary_shape_totals_and_audit(app) -> None:
    with _client(app) as client:
        resp = client.get("/api/v1/platform/insights/summary", params={"from": "2026-10-30", "to": "2026-10-31"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        audits = client.sessions["user"].added
        calls = client.sessions["platform"].calls
    assert body["period"] == {"from": "2026-10-30", "to": "2026-10-31"}
    assert body["series"] == [
        {"day": "2026-10-30", "tenants_active": 2, "runs": 10, "failed": 1, "tokens": 900, "cost_usd": 1.5},
        {"day": "2026-10-31", "tenants_active": 1, "runs": 4, "failed": 0, "tokens": 100, "cost_usd": 0.25},
    ]
    assert body["totals"] == {
        "tenants_active": 1,
        "tenants_total": 2,
        "runs": 14,
        "failed": 1,
        "failure_rate": round(1 / 14, 4),
        "tokens": 1000,
        "cost_usd": 1.75,
    }
    assert body["reconciled_gate_nights"] == 14
    # Benchmarks: the opted-in count only (a database function; never which tenants).
    assert body["benchmarks"] == {"opted_in": 23, "min_tenants": 20}
    assert body["operations"] == {
        "exports": {"queued": 1, "running": 0, "ready": 6, "failed": 2, "expired": 0},
        "export_failure_rate": 0.25,
        "oldest_open_minutes": 12,
        "tenants_late": 1,
        "max_lag_hours": 3.5,
        "late_after_hours": 2,
    }
    # The functions are called with bound dates, never interpolated.
    function_calls = [params for sql, params in calls if "insights_platform" in sql]
    assert function_calls == [{"p_from": date(2026, 10, 30), "p_to": date(2026, 10, 31)}] * 3
    assert len(audits) == 1
    entry = audits[0]
    assert entry.event_type == "platform.insights.summary.read" and entry.tenant_id == OPERATOR_TENANT
    assert entry.actor_id == str(USER_ID) and entry.signature
    assert str(LISTED_TENANT) not in str(entry.details) and "Synthetic" not in str(entry.details)


def test_tenants_shape_and_gate(app) -> None:
    with _client(app) as client:
        body = client.get("/api/v1/platform/insights/tenants").json()
        audits = client.sessions["user"].added
    assert body["reconciled_gate_nights"] == 14 and body["truncated"] is False
    first, idle = body["tenants"]
    assert first == {
        "tenant_id": str(LISTED_TENANT),
        "tenant_name": "Synthetic Traders",
        "plan": "enterprise",
        "runs": 14,
        "failed": 1,
        "failure_rate": round(1 / 14, 4),
        "cost_usd": 1.75,
        "active_agents": 3,
        "last_active_day": "2026-10-31",
        "reconciled_nights": 15,
        "gate_met": True,
        "last_rollup_status": "reconciled",
    }
    # No runs: no failure rate (never a placeholder 0), no last active day.
    assert idle["failure_rate"] is None and idle["last_active_day"] is None and idle["gate_met"] is False
    assert [a.event_type for a in audits] == ["platform.insights.tenants.read"]
    assert audits[0].details == {"period": body["period"], "row_count": 2}


def test_posture_route_returns_the_checklist(app) -> None:
    with _client(app) as client:
        resp = client.get("/api/v1/platform/insights/posture")
        audits = client.sessions["user"].added
    assert resp.json() == POSTURE
    assert [a.event_type for a in audits] == ["platform.insights.posture.read"]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-07-01", "to": "2026-10-31"},  # 122 days
        {"from": "2026-10-31", "to": "2026-10-01"},  # reversed
    ],
)
def test_ranges_longer_than_92_days_or_reversed_are_refused(app, params) -> None:
    with _client(app) as client:
        resp = client.get("/api/v1/platform/insights/summary", params=params)
        assert resp.status_code == 422 and resp.json()["detail"]["error"] == "invalid_period"
        assert client.sessions["platform"].calls == []


def test_platform_range_defaults_and_bounds() -> None:
    today = date(2026, 11, 1)
    assert platform.platform_range(None, None, today=today) == (date(2026, 10, 3), today)
    assert platform.platform_range(date(2026, 8, 1), None, today=today) == (date(2026, 8, 1), date(2026, 8, 30))
    assert platform.platform_range(None, date(2026, 9, 30), today=today) == (date(2026, 9, 1), date(2026, 9, 30))
    start = date(2026, 8, 1)
    assert platform.platform_range(start, start + timedelta(days=92), today=today)[1] == start + timedelta(days=92)
    with pytest.raises(platform.PlatformRangeError):
        platform.platform_range(start, start + timedelta(days=93), today=today)


@pytest.mark.asyncio
async def test_operations_without_jobs_or_rollups_have_no_rates() -> None:
    from core.insights import platform

    class Empty:
        async def execute(self, statement, params=None):
            return _Result(rows=[] if "operations" in str(statement) else None)

    ops = await platform.platform_operations(Empty(), date(2026, 10, 1), date(2026, 10, 2))
    assert ops == {
        "exports": {"queued": 0, "running": 0, "ready": 0, "failed": 0, "expired": 0},
        "export_failure_rate": None,
        "oldest_open_minutes": None,
        "tenants_late": 0,
        "max_lag_hours": None,
        "late_after_hours": 2,
    }


def test_totals_without_runs_have_no_failure_rate() -> None:
    assert platform.totals([], []) == {
        "tenants_active": 0, "tenants_total": 0, "runs": 0, "failed": 0, "failure_rate": None, "tokens": 0,
        "cost_usd": 0,
    }


# ---------------------------------------------------------------- posture helpers


class _PostureSession:
    def __init__(self, health: list[tuple[str, int]], latest: dict[str, Any] | None, unforced: bool) -> None:
        self.health = health
        self.latest = latest
        self.unforced = unforced

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "to_regclass('public.alembic_version')" in sql:
            return SimpleNamespace(scalar=lambda: True)
        if "FROM alembic_version" in sql:
            return SimpleNamespace(scalars=lambda: iter(["head_a"]))
        if "pg_attribute" in sql:
            rows = [("agents", True, True), ("run_facts", True, True), ("legacy_table", True, not self.unforced)]
            return SimpleNamespace(all=lambda: rows)
        if "pg_roles" in sql:
            return SimpleNamespace(first=lambda: ("agenticorg_app", False, False))
        if "GROUP BY status" in sql:
            return SimpleNamespace(all=lambda: self.health)
        if "ORDER BY recorded_at DESC" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: self.latest))
        return SimpleNamespace()


async def test_posture_checklist_from_catalogue_reads() -> None:
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    latest = {"recorded_at": now, "status": "healthy", "version": "9.9.9", "commit": "abc1234"}
    session = _PostureSession([("healthy", 280), ("unhealthy: TimeoutError", 8)], latest, unforced=False)
    with patch("core.database.get_expected_alembic_heads", return_value=frozenset({"head_a"})):
        result = await platform.posture(session, now=now)
    assert result["ok"] is True
    assert result["schema"] == {"ok": True, "database_heads": ["head_a"], "code_heads": ["head_a"]}
    assert result["rls"] == {"ok": True, "tenant_tables": 3, "forced": 3, "unforced": []}
    assert result["role"] == {"ok": True, "role": "agenticorg_app", "superuser": False, "bypass_rls": False}
    assert result["health"]["counts"] == {"healthy": 280, "unhealthy": 8, "other": 0}
    assert (result["health"]["latest_version"], result["health"]["latest_commit"]) == ("9.9.9", "abc1234")

    stale = _PostureSession([], None, unforced=True)
    with patch("core.database.get_expected_alembic_heads", return_value=frozenset({"head_b"})):
        result = await platform.posture(stale, now=now)
    assert result["ok"] is False
    assert {c["key"]: c["ok"] for c in result["checks"]} == {
        "schema": False, "rls": False, "role": True, "health": False,
    }
    assert result["rls"]["unforced"] == ["legacy_table"]
    assert result["health"]["snapshots"] == 0 and result["health"]["latest_status"] is None


# ---------------------------------------------------------------- route metadata


def test_routes_are_registered_with_their_metadata(app) -> None:
    from api.route_enforcement import GRANTABLE_ROUTE_SCOPES, SCOPE_FAMILIES, required_scopes_for
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import insights_platform

    paths = set(app.openapi()["paths"])
    assert {f"/api/v1/platform/insights{p}" for p in PATHS} <= paths
    meta = {r.path: getattr(r.endpoint, ROUTE_METADATA_ATTR) for r in insights_platform.router.routes}
    assert set(meta) == {f"/platform/insights{p}" for p in PATHS}
    for path, m in meta.items():
        assert m["scope"] == "platform.insights.read" and m["auth_required"] and m["tenant_required"], path
        expected_rate = "reporting-export" if path.endswith("/export") else "reporting-read"
        assert m["rate_limit"] == expected_rate and m["idempotency"] == "read-only", path
        assert m["audit_event"] == f"platform.insights.{path.rsplit('/', 1)[1]}.read", path
    for route in insights_platform.router.routes:
        names = {d.call.__name__ for d in route.dependant.dependencies}
        assert "require_platform_operator" in names, route.path
    assert SCOPE_FAMILIES["platform"] == ("agenticorg:admin", "agenticorg:admin")
    assert required_scopes_for("platform.insights.read", "GET") == ("agenticorg:admin",)
    assert "agenticorg:admin" not in GRANTABLE_ROUTE_SCOPES


def test_export_route_writes_each_dataset_in_each_format_and_audits_it(app) -> None:
    import csv
    import io
    import json

    with _client(app) as client:
        daily = client.get("/api/v1/platform/insights/export",
                           params={"dataset": "daily", "format": "csv", "from": "2026-10-30", "to": "2026-10-31"})
        tenants = client.get("/api/v1/platform/insights/export", params={"dataset": "tenants", "format": "json"})
        workbook = client.get("/api/v1/platform/insights/export", params={"dataset": "tenants", "format": "xlsx"})
        table = client.get("/api/v1/platform/insights/export", params={"dataset": "daily", "format": "parquet"})
        refused = client.get("/api/v1/platform/insights/export", params={"dataset": "runs", "format": "csv"})
        audits = client.sessions["user"].added
    assert daily.status_code == 200 and "platform-daily" in daily.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(daily.content.decode("utf-8-sig"))))
    assert [r["runs"] for r in rows] == ["10", "4"]
    body = json.loads(tenants.content)
    assert [r["tenant_name"] for r in body["rows"]][0] == "Synthetic Traders"
    assert workbook.content[:2] == b"PK"
    assert table.status_code == 200 and table.content[:4] == b"PAR1"
    assert table.headers["content-type"].startswith("application/vnd.apache.parquet")
    assert refused.status_code == 422
    assert [a.event_type for a in audits] == ["platform.insights.export.read"] * 4
    assert audits[0].details["row_count"] == 2 and audits[0].details["dataset"] == "daily"
