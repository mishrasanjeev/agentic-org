# ruff: noqa: S106 — test files use fake tokens intentionally
"""Bug sheet 2026-09-14 rows 17, 18, 29 — per-user connector ownership.

Tester story: a CFO registers their own connector; another CFO must not see,
change, probe, or re-authorize it; an analyst may not register connectors at
all; tenant admins keep full control; and a second user cannot take over a
connector name someone else already owns (names stay unique tenant-wide
because runtime credentials are keyed by connector name).

Tests replay the HTTP routes through TestClient with a fake tenant session.
The OAuth callback persistence is exercised through the public callback route
with the Redis state pop and provider token exchange stubbed.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from core.models.connector import Connector
from core.rbac import ROLE_SCOPES
from tests.company_scope import TEST_TENANT_ID

TENANT = str(TEST_TENANT_ID)
USER_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
EMAIL_A = "cfo.a@example.test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connector(name: str = "hubspot", owner: uuid.UUID | None = None, status: str = "active") -> Connector:
    return Connector(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        name=name,
        category="crm",
        base_url=None,
        auth_type="api_key",
        auth_config={},
        secret_ref=None,
        tool_functions=[],
        data_schema_ref=None,
        rate_limit_rpm=60,
        timeout_ms=10000,
        status=status,
        owner_user_id=owner,
    )


class _FakeSession:
    """Every SELECT resolves to ``row``; statements are recorded."""

    def __init__(self, row=None, flush_error: Exception | None = None):
        self.row = row
        self.flush_error = flush_error
        self.statements: list = []
        self.added: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        res = MagicMock()
        res.scalar_one_or_none.return_value = self.row
        res.scalar.return_value = 1 if self.row is not None else 0
        res.scalars.return_value.all.return_value = [self.row] if isinstance(self.row, Connector) else []
        return res

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.flush_error is not None:
            err, self.flush_error = self.flush_error, None
            raise err

    async def rollback(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, _row):
        return None


def _session_factory(session: _FakeSession):
    @asynccontextmanager
    async def factory(*_a, **_k):
        yield session

    return factory


@contextmanager
def _no_external_auth_state():
    """Skip the Redis connect and users-table lookup timeouts (no infra in unit runs)."""
    with (
        patch("core.auth_state._get_redis", AsyncMock(return_value=None)),
        patch("core.auth_state._load_user_state_from_db", AsyncMock(side_effect=RuntimeError("no db in test"))),
    ):
        yield


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def _client(app, *, role: str, user_id: uuid.UUID, scopes: list[str], sub: str = "user@example.test"):
    claims = {
        "sub": sub,
        "role": role,
        "agenticorg:tenant_id": TENANT,
        "agenticorg:domains": None if role == "admin" else ["finance"],
        "agenticorg:user_id": str(user_id),
        "grantex:scopes": scopes,
    }

    async def _fake_validate(token):
        return claims

    async def _noop(*_a, **_k):
        return None

    with (
        patch("auth.grantex_middleware.is_ip_blocked", return_value=False),
        patch("auth.grantex_middleware.record_auth_failure", side_effect=_noop),
        patch("auth.grantex_middleware.clear_auth_failures", side_effect=_noop),
        patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate),
        # Authorization tests use synthetic users; session revocation has its own
        # coverage. With a live DB (CI integration job) the lookup would 401.
        patch("auth.grantex_middleware.check_user_session_state", AsyncMock(return_value=None)),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        _no_external_auth_state(),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


def _cfo_a(app):
    return _client(app, role="cfo", user_id=USER_A, scopes=list(ROLE_SCOPES["cfo"]), sub=EMAIL_A)


def _cfo_b(app):
    return _client(app, role="cfo", user_id=USER_B, scopes=list(ROLE_SCOPES["cfo"]))


def _analyst(app):
    return _client(app, role="analyst", user_id=uuid.uuid4(), scopes=list(ROLE_SCOPES["analyst"]))


def _admin(app):
    return _client(app, role="admin", user_id=uuid.uuid4(), scopes=["agenticorg:admin"])


def _patch_connector_session(session: _FakeSession):
    return patch("api.v1.connectors.get_tenant_session", side_effect=_session_factory(session))


# ---------------------------------------------------------------------------
# Registration (rows 17, 29)
# ---------------------------------------------------------------------------


class TestRegisterConnectorOwnership:
    def test_cfo_registers_connector_owned_by_self(self, app):
        session = _FakeSession()
        with _cfo_a(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "cfo_ledger", "category": "finance", "auth_type": "none"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["owner_user_id"] == str(USER_A)
        assert body["visibility"] == "personal"
        assert [row.owner_user_id for row in session.added] == [USER_A]

    def test_admin_registers_shared_connector(self, app):
        session = _FakeSession()
        with _admin(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "slack", "category": "comms", "auth_type": "none"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["owner_user_id"] is None
        assert resp.json()["visibility"] == "shared"

    def test_analyst_cannot_register(self, app):
        session = _FakeSession()
        with _analyst(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "x", "category": "crm", "auth_type": "none"})
        assert resp.status_code == 403
        assert session.added == []

    def test_second_user_same_name_is_409_without_owner_leak(self, app):
        existing = _connector("cfo_ledger", owner=USER_A)
        session = _FakeSession(existing, flush_error=IntegrityError("dup", {}, Exception("duplicate")))
        with _cfo_b(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "cfo_ledger", "category": "finance", "auth_type": "none"})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail == "Connector 'cfo_ledger' already exists"
        assert str(USER_A) not in resp.text
        assert EMAIL_A not in resp.text
        assert existing.owner_user_id == USER_A

    def test_soft_deleted_twin_of_other_owner_is_not_reactivated(self, app):
        twin = _connector("cfo_ledger", owner=USER_A, status="deleted")
        session = _FakeSession(twin, flush_error=IntegrityError("dup", {}, Exception("duplicate")))
        with _cfo_b(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "cfo_ledger", "category": "finance", "auth_type": "none"})
        assert resp.status_code == 409
        assert str(USER_A) not in resp.text
        assert twin.status == "deleted"
        assert twin.owner_user_id == USER_A

    def test_owner_reactivates_own_soft_deleted_twin_and_keeps_ownership(self, app):
        twin = _connector("cfo_ledger", owner=USER_A, status="deleted")
        session = _FakeSession(twin, flush_error=IntegrityError("dup", {}, Exception("duplicate")))
        with _cfo_a(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "cfo_ledger", "category": "finance", "auth_type": "none"})
        assert resp.status_code == 201, resp.text
        assert twin.status == "active"
        assert twin.owner_user_id == USER_A

    def test_cfo_cannot_reactivate_shared_soft_deleted_twin(self, app):
        twin = _connector("slack", owner=None, status="deleted")
        session = _FakeSession(twin, flush_error=IntegrityError("dup", {}, Exception("duplicate")))
        with _cfo_a(app) as c, _patch_connector_session(session):
            resp = c.post("/api/v1/connectors", json={"name": "slack", "category": "comms", "auth_type": "none"})
        assert resp.status_code == 409
        assert twin.status == "deleted"
        assert twin.owner_user_id is None


# ---------------------------------------------------------------------------
# Visibility and mutation (rows 17, 18)
# ---------------------------------------------------------------------------


class TestConnectorReadAndMutate:
    def test_other_cfo_get_is_404(self, app):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _cfo_b(app) as c, _patch_connector_session(_FakeSession(conn)):
            resp = c.get(f"/api/v1/connectors/{conn.id}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Connector not found"

    def test_owner_get_includes_ownership_fields(self, app):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _cfo_a(app) as c, _patch_connector_session(_FakeSession(conn)):
            resp = c.get(f"/api/v1/connectors/{conn.id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["owner_user_id"] == str(USER_A)
        assert resp.json()["visibility"] == "personal"

    @pytest.mark.parametrize(
        ("method", "suffix", "json"),
        [
            ("put", "", {"rate_limit_rpm": 5}),
            ("delete", "", None),
            ("post", "/test", None),
            ("get", "/health", None),
        ],
    )
    def test_other_cfo_mutations_are_404(self, app, method, suffix, json):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _cfo_b(app) as c, _patch_connector_session(_FakeSession(conn)):
            kwargs = {"json": json} if json is not None else {}
            resp = getattr(c, method)(f"/api/v1/connectors/{conn.id}{suffix}", **kwargs)
        assert resp.status_code == 404, resp.text
        assert conn.status == "active"
        assert conn.rate_limit_rpm == 60

    def test_owner_put_own_connector_is_200(self, app):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _cfo_a(app) as c, _patch_connector_session(_FakeSession(conn)):
            resp = c.put(f"/api/v1/connectors/{conn.id}", json={"rate_limit_rpm": 5, "owner_user_id": str(USER_B)})
        assert resp.status_code == 200, resp.text
        assert conn.rate_limit_rpm == 5
        # Ownership is never reassigned through PUT.
        assert conn.owner_user_id == USER_A
        assert resp.json()["owner_user_id"] == str(USER_A)

    @pytest.mark.parametrize(
        ("method", "suffix", "json"),
        [
            ("put", "", {"rate_limit_rpm": 5}),
            ("delete", "", None),
            ("post", "/test", None),
            ("get", "/health", None),
        ],
    )
    def test_cfo_mutations_on_shared_connector_are_403(self, app, method, suffix, json):
        conn = _connector("slack", owner=None)
        with _cfo_a(app) as c, _patch_connector_session(_FakeSession(conn)):
            kwargs = {"json": json} if json is not None else {}
            resp = getattr(c, method)(f"/api/v1/connectors/{conn.id}{suffix}", **kwargs)
        assert resp.status_code == 403, resp.text
        assert conn.status == "active"
        assert conn.rate_limit_rpm == 60

    def test_admin_sees_and_mutates_personal_connector(self, app):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _admin(app) as c, _patch_connector_session(_FakeSession(conn)):
            got = c.get(f"/api/v1/connectors/{conn.id}")
            put = c.put(f"/api/v1/connectors/{conn.id}", json={"rate_limit_rpm": 7})
            deleted = c.delete(f"/api/v1/connectors/{conn.id}")
        assert got.status_code == 200, got.text
        assert put.status_code == 200, put.text
        assert deleted.status_code == 200, deleted.text
        assert conn.rate_limit_rpm == 7
        assert conn.status == "deleted"
        assert conn.owner_user_id == USER_A

    def test_rename_collision_stays_409(self, app):
        conn = _connector("cfo_ledger", owner=USER_A)
        with _cfo_a(app) as c, _patch_connector_session(_FakeSession(conn)):
            resp = c.put(f"/api/v1/connectors/{conn.id}", json={"name": "someone_elses"})
        # The duplicate lookup resolves to a row, so the rename is refused.
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Connector 'someone_elses' already exists"


class TestConnectorListVisibility:
    def _list(self, app, client_factory):
        session = _FakeSession(_connector("cfo_ledger", owner=USER_A))
        with client_factory(app) as c, _patch_connector_session(session):
            resp = c.get("/api/v1/connectors")
        return resp, session

    def test_non_admin_list_is_filtered_to_shared_or_own(self, app):
        resp, session = self._list(app, _cfo_a)
        assert resp.status_code == 200, resp.text
        count_stmt, page_stmt = session.statements[0], session.statements[1]
        for stmt in (count_stmt, page_stmt):
            compiled = stmt.compile()
            sql = str(compiled)
            assert "connectors.owner_user_id IS NULL OR connectors.owner_user_id = :owner_user_id_1" in sql
            assert compiled.params["owner_user_id_1"] == USER_A
        assert "ORDER BY connectors.name" in str(page_stmt.compile())
        item = resp.json()["items"][0]
        assert item["owner_user_id"] == str(USER_A)
        assert item["visibility"] == "personal"

    def test_admin_list_has_no_owner_filter(self, app):
        resp, session = self._list(app, _admin)
        assert resp.status_code == 200, resp.text
        sql = str(session.statements[1].compile())
        assert "owner_user_id IS NULL" not in sql
        assert "owner_user_id =" not in sql


# ---------------------------------------------------------------------------
# OAuth handoff (rows 17, 18, 29)
# ---------------------------------------------------------------------------


def _patch_oauth_session(session: _FakeSession):
    return patch("api.v1.oauth_connector.get_tenant_session", side_effect=_session_factory(session))


_OAUTH_BODY = {"connector_name": "hubspot", "user_fields": {"client_id": "cid", "client_secret": "csecret"}}


class TestOAuthOwnership:
    def test_cfo_initiate_for_new_name_records_owner_in_state(self, app):
        store = AsyncMock()
        with (
            _cfo_a(app) as c,
            _patch_oauth_session(_FakeSession(None)),
            patch("api.v1.oauth_connector._store_oauth_state", store),
            patch("api.v1.oauth_connector._stash_for_reconnect", AsyncMock()),
        ):
            resp = c.post("/api/v1/connectors/oauth/initiate", json=_OAUTH_BODY)
        assert resp.status_code == 200, resp.text
        payload = store.await_args.args[2]
        assert payload["owner_user_id"] == str(USER_A)

    def test_admin_initiate_records_shared_owner(self, app):
        store = AsyncMock()
        with (
            _admin(app) as c,
            patch("api.v1.oauth_connector._store_oauth_state", store),
            patch("api.v1.oauth_connector._stash_for_reconnect", AsyncMock()),
        ):
            resp = c.post("/api/v1/connectors/oauth/initiate", json=_OAUTH_BODY)
        assert resp.status_code == 200, resp.text
        assert store.await_args.args[2]["owner_user_id"] is None

    @pytest.mark.parametrize("owner", [None, USER_B], ids=["shared", "other_user"])
    def test_cfo_initiate_for_connector_they_do_not_own_is_403(self, app, owner):
        store = AsyncMock()
        with (
            _cfo_a(app) as c,
            _patch_oauth_session(_FakeSession(_connector("hubspot", owner=owner))),
            patch("api.v1.oauth_connector._store_oauth_state", store),
            patch("api.v1.oauth_connector._stash_for_reconnect", AsyncMock()),
        ):
            resp = c.post("/api/v1/connectors/oauth/initiate", json=_OAUTH_BODY)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You cannot authorize this connector"
        assert str(USER_B) not in resp.text
        store.assert_not_awaited()

    def test_analyst_cannot_initiate(self, app):
        with _analyst(app) as c:
            resp = c.post("/api/v1/connectors/oauth/initiate", json=_OAUTH_BODY)
        assert resp.status_code == 403

    def test_revoke_and_retry_refuses_other_users_stash(self, app):
        stash = {**_OAUTH_BODY, "extra_config": {}, "owner_user_id": str(USER_A), "redirect_uri": ""}
        store = AsyncMock()
        with (
            _cfo_b(app) as c,
            _patch_oauth_session(_FakeSession(None)),
            patch("api.v1.oauth_connector._pop_reconnect_payload", AsyncMock(return_value=stash)),
            patch("api.v1.oauth_connector._revoke_existing_grant", AsyncMock()) as revoke,
            patch("api.v1.oauth_connector._store_oauth_state", store),
        ):
            resp = c.post("/api/v1/connectors/oauth/revoke-and-retry", json={"connector_name": "hubspot"})
        assert resp.status_code == 403
        revoke.assert_not_awaited()
        store.assert_not_awaited()

    def _callback(self, app, session: _FakeSession, owner: uuid.UUID | None):
        payload = {
            "tenant_id": TENANT,
            "connector_name": "hubspot",
            "user_fields": {"client_id": "cid", "client_secret": "csecret"},
            "redirect_uri": "https://api.example.test/api/v1/oauth/callback",
            "extra_config": {},
            "owner_user_id": str(owner) if owner else None,
        }
        with (
            _no_external_auth_state(),
            TestClient(app, raise_server_exceptions=False) as c,
            _patch_oauth_session(session),
            patch("api.v1.oauth_connector._pop_oauth_state", AsyncMock(return_value=payload)),
            patch(
                "api.v1.oauth_connector._exchange_authorization_code",
                AsyncMock(return_value={"access_token": "at", "refresh_token": "rt"}),
            ),
            patch("api.v1.oauth_connector._assert_public_base_url", return_value=None),
            patch("api.v1.oauth_connector.encrypt_for_tenant", AsyncMock(return_value="ciphertext")),
        ):
            return c.get("/api/v1/oauth/callback", params={"code": "code", "state": "state"})

    def test_callback_on_existing_connector_with_non_owner_state_is_refused(self, app):
        conn = _connector("hubspot", owner=USER_A)
        session = _FakeSession(conn)
        resp = self._callback(app, session, owner=USER_B)
        assert resp.status_code == 403
        assert conn.owner_user_id == USER_A
        assert conn.auth_type == "api_key"
        assert session.added == []

    def test_callback_on_shared_connector_with_personal_state_is_refused(self, app):
        conn = _connector("hubspot", owner=None)
        session = _FakeSession(conn)
        resp = self._callback(app, session, owner=USER_A)
        assert resp.status_code == 403
        assert conn.owner_user_id is None
        assert session.added == []

    def test_callback_creates_connector_owned_by_state_owner(self, app):
        session = _FakeSession(None)
        resp = self._callback(app, session, owner=USER_A)
        assert resp.status_code == 200, resp.text
        created = [row for row in session.added if isinstance(row, Connector)]
        assert [row.owner_user_id for row in created] == [USER_A]

    def test_admin_state_callback_keeps_existing_owner(self, app):
        conn = _connector("hubspot", owner=USER_A)
        resp = self._callback(app, _FakeSession(conn), owner=None)
        assert resp.status_code == 200, resp.text
        assert conn.owner_user_id == USER_A
        assert conn.auth_type == "oauth2"


# ---------------------------------------------------------------------------
# CMO vendor sandbox rows stay shared
# ---------------------------------------------------------------------------


def test_cmo_vendor_sandbox_creates_shared_connector_rows() -> None:
    import inspect

    from api.v1 import connectors

    src = inspect.getsource(connectors.upsert_cmo_vendor_sandbox_connectors)
    create_block = src.split("Connector(", 1)[1].split(")", 1)[0]
    assert "owner_user_id" not in create_block  # new rows default to NULL (tenant-shared)
    # Sandbox setup must refuse (not overwrite/convert) a personal connector.
    assert "connector.owner_user_id is not None" in src
    assert "raise HTTPException(409" in src
    assert Connector.__table__.c.owner_user_id.nullable is True
    assert Connector.__table__.c.owner_user_id.default is None
