# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
# ruff: noqa: S608 - the strings compared are the migration's own constant SQL
"""Insights BI access: the SCRAM verifier, login names, the v6z108 migration's shape and the API guards.

The verifier is checked against the RFC 7677 SCRAM-SHA-256 example exchange
(the client proof and the server signature it implies). The migration is read
statically (revision chain, idempotent SQL, the exposed columns, both policies
on every source and none on a partition). The routes run through the real app
(auth middleware, scope enforcement and rate limiting, with the token check and
the limiter's backend replaced) and the real ``get_active_human_admin``, with
a stand-in database session; isolation itself is proven against Postgres in
``tests/integration/test_insights_bi_postgres.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from core.insights import bi_access
from core.insights.reporting_settings import defaults_for_region
from core.models import InsightsBiLogin
from core.rbac import ROLE_SCOPES

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "v6_z108_insights_bi.py"
TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
USER = uuid.UUID("00000000-0000-4000-8000-0000000000d1")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z108_insights_bi", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- SCRAM-SHA-256


def test_scram_keys_match_the_rfc_7677_example() -> None:
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    client_key, stored_key, server_key = bi_access.scram_keys("pencil", salt, 4096)
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth_message = (
        f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,r={nonce}"
    ).encode()
    client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
    assert base64.b64encode(proof).decode() == "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
    server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
    assert base64.b64encode(server_signature).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_verifier_format_and_the_migration_accepts_it() -> None:
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = bi_access.scram_verifier("pencil", salt=salt)
    _client, stored, server = bi_access.scram_keys("pencil", salt, 4096)
    assert verifier == (
        f"SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==${base64.b64encode(stored).decode()}:"
        f"{base64.b64encode(server).decode()}"
    )
    pattern = re.compile(_migration().VERIFIER_PATTERN)
    for _ in range(20):
        password = bi_access.new_password()
        made = bi_access.scram_verifier(password)
        assert pattern.fullmatch(made) and password not in made
    # A random salt each time.
    assert bi_access.scram_verifier("pencil") != bi_access.scram_verifier("pencil")
    # A plaintext password is never a verifier.
    assert not pattern.fullmatch("SCRAM-SHA-256$4096:plaintext")
    assert not pattern.fullmatch("pencil")


@pytest.mark.parametrize("password", ["", "pässword", "tab\there"])
def test_verifier_refuses_passwords_saslprep_could_change(password: str) -> None:
    with pytest.raises(ValueError):
        bi_access.scram_verifier(password)


def test_verifier_refuses_fewer_iterations() -> None:
    with pytest.raises(ValueError):
        bi_access.scram_verifier("pencil", iterations=1000)


def test_role_names_and_passwords() -> None:
    pattern = re.compile(_migration().ROLE_NAME_PATTERN)
    names = {bi_access.new_role_name(TENANT) for _ in range(50)}
    assert len(names) == 50
    for name in names:
        assert pattern.fullmatch(name) and bi_access.ROLE_NAME_RE.fullmatch(name)
        assert re.fullmatch(rf"bi_{TENANT.hex[:8]}_[a-z0-9]{{10}}", name)
    password = bi_access.new_password()
    assert len(password) == 32 and re.fullmatch(r"[A-Za-z0-9_-]{32}", password)
    created = bi_access.CreatedLogin(uuid.uuid4(), "bi_00000000_abcdefghij", password, None)
    assert password not in repr(created) and password not in str(created)


def test_configuration_needs_the_switch_host_and_database(monkeypatch) -> None:
    from core.config import settings

    for enabled, host, database, expected in (
        (False, "10.0.0.5", "agenticorg", False),
        (True, "", "agenticorg", False),
        (True, "10.0.0.5", " ", False),
        (True, "10.0.0.5", "agenticorg", True),
    ):
        monkeypatch.setattr(settings, "insights_bi_enabled", enabled)
        monkeypatch.setattr(settings, "insights_bi_host", host)
        monkeypatch.setattr(settings, "insights_bi_database", database)
        assert bi_access.bi_configured() is expected
    details = bi_access.connection_details()
    assert details["port"] == 5432 and details["sslmode"] == "require"
    assert details["views"] == [f"insights_bi.{v}" for v in bi_access.BI_VIEWS]
    assert bi_access.connection_string("bi_00000000_abcdefghij", "p-w_d") == (
        "postgresql://bi_00000000_abcdefghij:p-w_d@10.0.0.5:5432/agenticorg?sslmode=require"
    )


# ---------------------------------------------------------------- the migration


class _Op:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql) -> None:
        self.sql.append(" ".join(str(sql).split()))


def _upgrade_sql(monkeypatch) -> list[str]:
    migration = _migration()
    op = _Op()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    return op.sql


def test_revision_chain() -> None:
    migration = _migration()
    assert migration.revision == "v6z108_insights_bi" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z107_insights_platform"
    children = [
        path.name
        for path in MIGRATION.parent.glob("*.py")
        if re.search(r'^down_revision = "v6z107_insights_platform"$', path.read_text(encoding="utf-8"), re.M)
    ]
    assert children == [MIGRATION.name]


def test_views_expose_only_the_listed_columns(monkeypatch) -> None:
    migration = _migration()
    assert tuple(migration.VIEWS) == bi_access.BI_VIEWS
    run_columns = set(migration.VIEWS["run_facts"][1])
    assert not run_columns & {"correlation_id", "trace_id", "run_key"}
    assert migration.VIEWS["spend_daily"][1] == (
        "day", "provider", "model", "usage_type", "unit", "quantity", "amount_inr", "agent_id", "use_case",
        "record_count",
    )  # fmt: skip
    assert migration.VIEWS["agents"][1] == ("id", "name", "agent_type", "domain", "status", "company_id")
    assert migration.VIEWS["approvals"][1] == (
        "id", "agent_id", "trigger_type", "priority", "status", "assignee_role", "created_at", "decision",
        "decision_at",
    )  # fmt: skip
    from core.models import HITLQueue, InsightsDaily, RunFact
    from core.models.agent import Agent
    from core.models.spend_usage import SpendUsageRollup

    models = {"run_facts": RunFact, "daily": InsightsDaily, "spend_daily": SpendUsageRollup, "agents": Agent,
              "approvals": HITLQueue}  # fmt: skip
    for view, (source, columns) in migration.VIEWS.items():
        assert models[view].__tablename__ == source
        assert set(columns) <= set(models[view].__table__.c.keys()), view
    assert run_columns == set(RunFact.__table__.c.keys()) - {"correlation_id", "trace_id", "run_key"}

    sql = _upgrade_sql(monkeypatch)
    for view, (source, columns) in migration.VIEWS.items():
        created = [s for s in sql if s.startswith(f"CREATE OR REPLACE VIEW insights_bi.{view} ")]
        assert created == [
            f"CREATE OR REPLACE VIEW insights_bi.{view} WITH (security_invoker = true, security_barrier = true) "
            f"AS SELECT {', '.join(columns)} FROM public.{source}"
        ]
        assert f"GRANT SELECT ({', '.join(columns)}) ON public.{source} TO agenticorg_bi" in sql


def test_both_policies_on_every_source_and_nothing_on_a_partition(monkeypatch) -> None:
    sql = _upgrade_sql(monkeypatch)
    for source in _migration().SOURCES:
        for kind, name in (("PERMISSIVE", "insights_bi_read"), ("RESTRICTIVE", "insights_bi_only_own")):
            statement = (
                f"CREATE POLICY {name} ON public.{source} AS {kind} FOR SELECT TO agenticorg_bi "
                "USING (tenant_id = (SELECT public.insights_bi_tenant()))"
            )
            assert statement in sql, statement
            assert sql.index(f"DROP POLICY IF EXISTS {name} ON public.{source}") < sql.index(statement)
    joined = "\n".join(sql)
    assert not re.search(r"run_facts_(y\d{4}m\d{2}|default)", joined)
    # Nothing is granted to PUBLIC, and the BI group gets no table-level privilege.
    assert not re.search(r"GRANT [^;]* TO PUBLIC", joined)
    assert not re.search(r"GRANT (SELECT|INSERT|UPDATE|DELETE|ALL)[ ,A-Z]* ON public\.\w+ TO agenticorg_bi\b", joined)


def test_functions_are_definer_with_a_fixed_search_path_and_session_user(monkeypatch) -> None:
    migration = _migration()
    for ddl in (migration._TENANT_FN_SQL, migration._CREATE_FN_SQL, migration._REVOKE_FN_SQL):
        flat = " ".join(ddl.split())
        assert "SECURITY DEFINER SET search_path = pg_catalog" in flat
    assert "session_user" in migration._TENANT_FN_SQL and "current_user" not in migration._TENANT_FN_SQL
    create = " ".join(migration._CREATE_FN_SQL.split())
    for required in (
        "current_setting('agenticorg.tenant_id', true) IS DISTINCT FROM p_tenant::text",
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
        "CONNECTION LIMIT 3",
        "SET search_path = insights_bi",
        "statement_timeout",
        "default_transaction_read_only = on",
        "pg_advisory_xact_lock",
        "'insights_bi_limit_reached' USING ERRCODE = 'BI409'",
        "format('CREATE ROLE %I LOGIN PASSWORD %L",
    ):
        assert required in create, required
    revoke = " ".join(migration._REVOKE_FN_SQL.split())
    assert "WHERE l.tenant_id = p_tenant AND l.role_name = p_role" in revoke
    assert "ERRCODE = 'BI404'" in revoke and "NOLOGIN" in revoke and "dependent_objects_still_exist" in revoke
    joined = "\n".join(_upgrade_sql(monkeypatch))
    # EXECUTE: the tenant lookup to the BI group only; the provisioning functions to the installer and the app.
    assert "GRANT EXECUTE ON FUNCTION public.insights_bi_tenant() TO agenticorg_bi;" in joined
    for fn in (migration.CREATE_FN, migration.REVOKE_FN):
        assert f"GRANT EXECUTE ON FUNCTION {fn} TO agenticorg_app;" in joined
        assert f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC;" in joined
    assert "Installer retained BI role access" in joined


def test_upgrade_sql_is_idempotent(monkeypatch) -> None:
    for statement in _upgrade_sql(monkeypatch):
        upper = statement.upper()
        if upper.startswith("CREATE TABLE"):
            assert "IF NOT EXISTS" in upper, statement
        elif upper.startswith("CREATE INDEX") or upper.startswith("CREATE UNIQUE INDEX"):
            assert "IF NOT EXISTS" in upper, statement
        elif upper.startswith("CREATE VIEW"):
            raise AssertionError(statement)
        elif upper.startswith("CREATE POLICY"):
            pass  # each is preceded by DROP POLICY IF EXISTS (checked above for the sources)
        elif upper.startswith("CREATE ROLE") or upper.startswith("CREATE SCHEMA"):
            raise AssertionError(f"unguarded: {statement}")


def test_logins_table_matches_the_model_and_is_tenant_scoped(monkeypatch) -> None:
    sql = _upgrade_sql(monkeypatch)
    joined = "\n".join(sql)
    table = InsightsBiLogin.__table__
    assert set(table.c.keys()) == {"id", "tenant_id", "role_name", "created_by", "created_at", "revoked_at"}
    assert table.c.role_name.type.length == 63 and not table.c.role_name.nullable
    assert {c.name for c in table.constraints if c.name} >= {"uq_insights_bi_logins_role_name"}
    assert {i.name for i in table.indexes} == {"ix_insights_bi_logins_tenant"}
    for needed in (
        "ALTER TABLE insights_bi_logins ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE insights_bi_logins FORCE ROW LEVEL SECURITY",
        "CREATE INDEX IF NOT EXISTS ix_insights_bi_logins_tenant ON insights_bi_logins(tenant_id)",
        "CONSTRAINT uq_insights_bi_logins_role_name UNIQUE (role_name)",
        "GRANT SELECT (role_name, tenant_id, revoked_at) ON public.insights_bi_logins TO agenticorg_bi_lookup",
        "REVOKE ALL ON public.insights_bi_logins FROM agenticorg_app;",
        "GRANT SELECT ON public.insights_bi_logins TO agenticorg_app;",
    ):
        assert needed in joined, needed


# ---------------------------------------------------------------- the API


class _Result:
    def __init__(self, value: Any = None, rows: list[Any] | None = None) -> None:
        self.value = value
        self.rows = rows or []

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return iter(self.rows)


class FakeSession:
    """Answers the statements the BI routes and ``get_active_human_admin`` make."""

    def __init__(self, *, user: Any = None, create_error: str | None = None, revoke_error: str | None = None):
        self.user = user
        self.create_error = create_error
        self.revoke_error = revoke_error
        self.added: list[Any] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.logins: list[Any] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "FROM users" in sql:
            return _Result(self.user)
        if "insights_bi_create_login" in sql:
            if self.create_error:
                raise DBAPIError(sql, params, SimpleNamespace(sqlstate=self.create_error))
            return _Result(uuid.uuid4())
        if "insights_bi_revoke_login" in sql:
            if self.revoke_error:
                raise DBAPIError(sql, params, SimpleNamespace(sqlstate=self.revoke_error))
            return _Result("dropped")
        if "SELECT created_at FROM insights_bi_logins" in sql:
            return _Result(datetime(2026, 11, 1, 9, 30, tzinfo=UTC))
        if "FROM insights_bi_logins" in sql:
            return _Result(rows=self.logins)
        return _Result()

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


@pytest.fixture
def configured(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "insights_bi_enabled", True)
    monkeypatch.setattr(settings, "insights_bi_host", "10.20.0.7")
    monkeypatch.setattr(settings, "insights_bi_database", "agenticorg")
    monkeypatch.setattr(settings, "insights_bi_max_logins_per_tenant", 3)


def _admin_user() -> Any:
    return SimpleNamespace(id=USER, tenant_id=TENANT, email="admin@example.test", role="admin")


@contextmanager
def _client(app, *, role: str, session: FakeSession, api_key: bool = False):
    scopes = list(ROLE_SCOPES[role])
    claims: dict[str, Any] = {
        "sub": "apikey:0123" if api_key else "admin@example.test",
        "agenticorg:tenant_id": str(TENANT),
        "grantex:scopes": scopes,
        "role": role,
        "agenticorg:domains": None,
    }
    if not api_key:
        claims["agenticorg:user_id"] = str(USER)
        claims["email"] = "admin@example.test"

    @asynccontextmanager
    async def _tenant_session(tid, company_id=None):
        assert str(tid) == str(TENANT)  # always the authenticated tenant
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
        patch("auth.grantex_middleware.extract_tenant_id", return_value=str(TENANT)),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("core.auth_state._get_redis", AsyncMock(return_value=None)),
        patch("core.auth_state._load_user_state_from_db", AsyncMock(side_effect=RuntimeError("no db in test"))),
        patch("api.deps.get_tenant_session", _tenant_session),
        patch("api.v1.insights_bi.get_tenant_session", _tenant_session),
        patch("api.v1.insights.get_tenant_session", _tenant_session),
        patch("api.v1.insights.insights_enabled_for", AsyncMock(return_value=True)),
        patch("api.v1.insights.load_reporting_settings", AsyncMock(return_value=defaults_for_region("IN"))),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.headers["Authorization"] = "Bearer fake-test-token"
            yield client


ROUTES = (
    ("GET", "/api/v1/insights/bi-access"),
    ("POST", "/api/v1/insights/bi-access"),
    ("DELETE", "/api/v1/insights/bi-access/bi_00000000_abcdefghij"),
)


def test_disabled_answers_not_found_to_an_administrator(app, monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_bi_enabled", False)
    session = FakeSession(user=_admin_user())
    with _client(app, role="admin", session=session) as client:
        for method, path in ROUTES:
            resp = client.request(method, path)
            assert resp.status_code == 404 and resp.json() == {"detail": "insights_bi_disabled"}, (method, path)
        assert client.get("/api/v1/insights/status").json()["bi_access"] is False
    assert not any("insights_bi_" in sql for sql, _ in session.calls)


def test_enabled_without_host_or_database_is_still_disabled(app, monkeypatch, configured) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_bi_host", "")
    with _client(app, role="admin", session=FakeSession(user=_admin_user())) as client:
        assert client.get("/api/v1/insights/bi-access").status_code == 404


@pytest.mark.parametrize("role", ["cfo", "auditor", "analyst"])
def test_non_administrators_are_refused(app, configured, role: str) -> None:
    session = FakeSession(user=None)  # the DB has no active admin with this identity
    with _client(app, role=role, session=session) as client:
        for method, path in ROUTES:
            assert client.request(method, path).status_code == 403, (role, method)
        assert client.get("/api/v1/insights/status").json()["bi_access"] is False


def test_a_stale_admin_claim_without_an_active_db_admin_is_refused(app, configured) -> None:
    with _client(app, role="admin", session=FakeSession(user=None)) as client:
        for method, path in ROUTES:
            assert client.request(method, path).status_code == 403, method


def test_api_keys_are_refused_even_with_the_admin_scope(app, configured) -> None:
    session = FakeSession(user=_admin_user())
    with _client(app, role="admin", session=session, api_key=True) as client:
        for method, path in ROUTES:
            assert client.request(method, path).status_code == 403, method
        assert client.get("/api/v1/insights/status").json()["bi_access"] is False
    assert not any("insights_bi_" in sql for sql, _ in session.calls)


def test_list_returns_logins_and_connection_details(app, configured) -> None:
    session = FakeSession(user=_admin_user())
    session.logins = [
        SimpleNamespace(
            role_name="bi_00000000_abcdefghij",
            created_at=datetime(2026, 11, 1, tzinfo=UTC),
            created_by=USER,
            revoked_at=None,
        ),
        SimpleNamespace(
            role_name="bi_00000000_klmnopqrst",
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
            created_by=None,
            revoked_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
    ]
    with _client(app, role="admin", session=session) as client:
        resp = client.get("/api/v1/insights/bi-access")
        assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["connection"] == {
            "host": "10.20.0.7",
            "port": 5432,
            "database": "agenticorg",
            "sslmode": "require",
            "schema": "insights_bi",
            "views": [f"insights_bi.{v}" for v in bi_access.BI_VIEWS],
        }
        assert body["max_logins"] == 3 and body["active_logins"] == 1
        assert [login["role_name"] for login in body["logins"]] == ["bi_00000000_abcdefghij", "bi_00000000_klmnopqrst"]
        assert body["logins"][0]["created_by"] == str(USER) and body["logins"][1]["revoked_at"]
        assert client.get("/api/v1/insights/status").json()["bi_access"] is True


def test_create_returns_the_password_once_and_never_logs_or_audits_it(app, configured, caplog) -> None:
    session = FakeSession(user=_admin_user())
    caplog.set_level(logging.DEBUG)
    with capture_logs() as logs, _client(app, role="admin", session=session) as client:
        resp = client.post("/api/v1/insights/bi-access")
    assert resp.status_code == 201, resp.text
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    password, role_name = body["password"], body["role_name"]
    assert len(password) == 32 and re.fullmatch(rf"bi_{TENANT.hex[:8]}_[a-z0-9]{{10}}", role_name)
    assert body["connection_string"].startswith(f"postgresql://{role_name}:")
    assert body["connection_string"].endswith("@10.20.0.7:5432/agenticorg?sslmode=require")
    assert body["port"] == 5432 and body["sslmode"] == "require" and body["created_at"]
    # The database got the verifier, bound to the caller's tenant and the configured limit.
    (call,) = [params for sql, params in session.calls if "insights_bi_create_login" in sql]
    assert call["tenant"] == TENANT and call["role"] == role_name and call["max"] == 3
    assert call["created_by"] == USER
    assert call["verifier"].startswith("SCRAM-SHA-256$4096:") and password not in json.dumps(call, default=str)
    salt = base64.b64decode(call["verifier"].split(":")[1].split("$")[0])
    _client_key, stored, server = bi_access.scram_keys(password, salt, 4096)
    assert call["verifier"].endswith(f"${base64.b64encode(stored).decode()}:{base64.b64encode(server).decode()}")
    # One signed audit row, without the password or the verifier.
    (entry,) = session.added
    assert entry.event_type == "insights.bi_access.create" and entry.resource_id == role_name
    assert entry.actor_id == str(USER) and entry.signature
    audited = json.dumps(entry.details, default=str)
    assert password not in audited and "SCRAM" not in audited
    assert entry.details == {"role_name": role_name, "login_id": entry.details["login_id"], "max_logins": 3}
    # Nothing logged carries the password.
    assert password not in json.dumps(logs, default=str)
    assert password not in caplog.text
    assert all(password not in json.dumps(params, default=str) for _sql, params in session.calls)


def test_create_at_the_limit_is_a_conflict(app, configured) -> None:
    session = FakeSession(user=_admin_user(), create_error=bi_access.SQLSTATE_LIMIT_REACHED)
    with _client(app, role="admin", session=session) as client:
        resp = client.post("/api/v1/insights/bi-access")
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"error": "insights_bi_limit_reached", "max_logins": 3}
    assert session.added == []


def test_a_database_refusal_is_a_server_error_without_the_statement(app, configured) -> None:
    session = FakeSession(user=_admin_user(), create_error="42501")
    with _client(app, role="admin", session=session) as client:
        resp = client.post("/api/v1/insights/bi-access")
    assert resp.status_code == 500 and "SCRAM" not in resp.text
    assert session.added == []


def test_revoke(app, configured) -> None:
    session = FakeSession(user=_admin_user())
    with _client(app, role="admin", session=session) as client:
        resp = client.delete("/api/v1/insights/bi-access/bi_00000000_abcdefghij")
    assert resp.status_code == 200
    assert resp.json() == {"role_name": "bi_00000000_abcdefghij", "revoked": True, "result": "dropped"}
    (call,) = [params for sql, params in session.calls if "insights_bi_revoke_login" in sql]
    assert call == {"tenant": TENANT, "role": "bi_00000000_abcdefghij"}
    (entry,) = session.added
    assert entry.event_type == "insights.bi_access.revoke" and entry.details["result"] == "dropped"


@pytest.mark.parametrize("name", ["bi_00000000_abcdefghij", "agenticorg_app", "postgres", "BI_UPPERCASE_NAME"])
def test_revoke_of_another_tenants_or_a_non_bi_role_is_not_found(app, configured, name: str) -> None:
    session = FakeSession(user=_admin_user(), revoke_error=bi_access.SQLSTATE_NOT_FOUND)
    with _client(app, role="admin", session=session) as client:
        resp = client.delete(f"/api/v1/insights/bi-access/{name}")
    assert resp.status_code == 404 and resp.json() == {"detail": "insights_bi_login_not_found"}
    assert session.added == []
    called = [params for sql, params in session.calls if "insights_bi_revoke_login" in sql]
    # Names that cannot be BI logins never reach the database.
    assert len(called) == (1 if bi_access.ROLE_NAME_RE.fullmatch(name) else 0)


def test_route_metadata_scopes_and_rate_class(app) -> None:
    from api.route_enforcement import RATE_LIMIT_CLASSES, required_scopes_for
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import insights_bi

    assert RATE_LIMIT_CLASSES["reporting-admin"] == (20, 60)
    assert required_scopes_for("insights.bi.read", "GET") == ("insights:read",)
    assert required_scopes_for("insights.bi.write", "POST") == ("agenticorg:admin",)
    assert required_scopes_for("insights.bi.write", "DELETE") == ("agenticorg:admin",)
    meta = {
        (route.path, tuple(sorted(route.methods))): getattr(route.endpoint, ROUTE_METADATA_ATTR)
        for route in insights_bi.router.routes
    }
    assert meta == {
        ("/insights/bi-access", ("GET",)): meta[("/insights/bi-access", ("GET",))],
        ("/insights/bi-access", ("POST",)): meta[("/insights/bi-access", ("POST",))],
        ("/insights/bi-access/{role_name}", ("DELETE",)): meta[("/insights/bi-access/{role_name}", ("DELETE",))],
    }
    for (path, methods), m in meta.items():
        assert m["auth_required"] and m["tenant_required"] and m["rate_limit"] == "reporting-admin", path
        assert m["scope"] == ("insights.bi.read" if methods == ("GET",) else "insights.bi.write"), path
        assert m["audit_event"].startswith("insights.bi_access."), path
    paths = set(app.openapi()["paths"])
    assert {"/api/v1/insights/bi-access", "/api/v1/insights/bi-access/{role_name}"} <= paths
