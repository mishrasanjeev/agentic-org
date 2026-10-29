# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Replay admin MFA resets through real auth middleware and route dependencies.

User/API-key persistence, session cache and throttling are synthetic boundaries.
These tests inspect emitted lock SQL; they do not prove PostgreSQL locking or RLS.
"""

from __future__ import annotations

import copy
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import bcrypt
import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, ColumnClause
from sqlalchemy.sql.functions import Function

from api import deps
from api.v1 import org
from auth import grantex_middleware as middleware
from auth import jwt as jwt_auth
from core import database
from core.auth_state import UserSessionState
from core.models.api_key import APIKey
from core.models.user import User

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
OTHER_TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
API_TOKEN = "ao_sk_synthetic_admin_reset_credential"
MFA_FIELDS = (
    "mfa_enabled", "mfa_totp_secret", "mfa_pending_secret", "mfa_pending_created_at",
    "mfa_last_totp_step", "mfa_recovery_codes", "mfa_enabled_at", "sessions_invalid_before",
    "mfa_challenge_generation",
)


def _user(number: int, email: str, **overrides) -> User:
    now = datetime.now(UTC) - timedelta(days=1)
    values = {
        "id": uuid.UUID(int=number), "tenant_id": TENANT, "email": email,
        "role": "admin", "status": "active", "mfa_enabled": True,
        "mfa_totp_secret": "synthetic-encrypted-enrollment",
        "mfa_pending_secret": "synthetic-encrypted-pending",
        "mfa_pending_created_at": now, "mfa_last_totp_step": 123,
        "mfa_recovery_codes": ["synthetic-recovery-hash"], "mfa_enabled_at": now,
        "sessions_invalid_before": now,
        "mfa_challenge_generation": 0,
    }
    return User(**(values | overrides))


def _evaluate(expression, row):
    """Evaluate only the simple predicates emitted at these synthetic DB boundaries."""
    if isinstance(expression, BindParameter):
        return expression.value
    if isinstance(expression, ColumnClause):
        return getattr(row, expression.key)
    if isinstance(expression, Function) and expression.name == "lower":
        return _evaluate(list(expression.clauses)[0], row).lower()
    if isinstance(expression, BooleanClauseList) and expression.operator is operators.and_:
        return all(_evaluate(clause, row) for clause in expression.clauses)
    if isinstance(expression, BinaryExpression):
        left, right = _evaluate(expression.left, row), _evaluate(expression.right, row)
        if expression.operator is operators.eq:
            return left == right
        if expression.operator is operators.ne:
            return left != right
        if expression.operator is operators.in_op:
            return left in right
    raise AssertionError(f"Unsupported synthetic predicate: {expression}")


class _Store:
    def __init__(self):
        self.actor = _user(2, "admin@example.test")
        self.target = _user(1, "member@example.test")
        self.foreign = _user(3, "foreign@example.test", tenant_id=OTHER_TENANT)
        self.users = [self.actor, self.target, self.foreign]
        self.key = APIKey(
            id=uuid.UUID(int=4), tenant_id=TENANT, user_id=self.actor.id, name="Synthetic admin key",
            prefix=f"ao_sk_{API_TOKEN[6:12]}", status="active", expires_at=None,
            scopes=["agenticorg:admin"],
            key_hash=bcrypt.hashpw(API_TOKEN.encode(), bcrypt.gensalt(rounds=4)).decode(),
        )
        self.lock_queries = []
        self.locked_user_ids = set()
        self.before_lock = None
        self.events = []
        self.user_commits = 0

    def snapshot(self):
        return {user.id: copy.deepcopy([getattr(user, name) for name in MFA_FIELDS]) for user in self.users}

    @asynccontextmanager
    async def session(self, tenant_id=None, _user_id=None):
        session = AsyncMock()
        session.add = MagicMock()
        user_query = False
        locked = False

        async def execute(statement):
            nonlocal user_query, locked
            result = MagicMock()
            if isinstance(statement, Update):
                assert statement.table.name == "api_keys" and tenant_id == TENANT
                return result
            entity = statement.column_descriptions[0]["entity"]
            if entity is User:
                assert tenant_id is not None
                user_query = True
                sql = str(statement.compile(dialect=postgresql.dialect()))
                if "FOR UPDATE" in sql:
                    locked = True
                    self.lock_queries.append(statement)
                    if self.before_lock:
                        self.before_lock()
                rows = [user for user in self.users if user.tenant_id == tenant_id]
            else:
                assert entity is APIKey and tenant_id is None
                rows = [self.key]
            rows = [row for row in rows if _evaluate(statement.whereclause, row)]
            if locked:
                self.locked_user_ids = {row.id for row in rows}
            result.scalar_one_or_none.return_value = rows[0] if rows else None
            result.scalars.return_value.all.return_value = rows
            return result

        async def commit():
            if user_query:
                self.user_commits += 1
                self.events.append("user_commit")

        session.execute.side_effect = execute
        session.commit.side_effect = commit
        try:
            yield session
        finally:
            if locked:
                self.locked_user_ids.clear()


@pytest.fixture
def replay(monkeypatch):
    from api.v1 import auth_mfa

    store = _Store()
    original_clear_mfa = auth_mfa.clear_mfa

    def clear_under_lock(user):
        assert user.id in store.locked_user_ids
        original_clear_mfa(user)

    cleared = MagicMock(side_effect=clear_under_lock)
    monkeypatch.setattr(auth_mfa, "clear_mfa", cleared)
    monkeypatch.setattr(deps, "get_tenant_session", store.session)
    monkeypatch.setattr(org, "get_tenant_session", store.session)
    monkeypatch.setattr(database, "get_tenant_session", store.session)
    monkeypatch.setattr(database, "get_platform_session", store.session)
    monkeypatch.setattr(jwt_auth.settings, "secret_key", "synthetic-mfa-reset-test-signing-key-only")
    monkeypatch.setattr(jwt_auth.settings, "env", "test")
    monkeypatch.setattr(jwt_auth.settings, "jwt_public_key_url", "")
    monkeypatch.setattr(jwt_auth, "_is_blacklisted", AsyncMock(return_value=False))
    monkeypatch.setattr(jwt_auth, "_fetch_jwks", AsyncMock(side_effect=AssertionError("No provider calls")))
    monkeypatch.setattr(middleware, "_is_grantex_token", lambda _token: False)
    monkeypatch.setattr(middleware, "clear_auth_failures", AsyncMock())
    monkeypatch.setattr(middleware, "is_ip_blocked", AsyncMock(return_value=False))
    monkeypatch.setattr(middleware, "record_auth_failure", AsyncMock(return_value=False))
    # A stale cache may still say active; the action must check the authoritative row.
    monkeypatch.setattr(
        middleware, "get_user_session_state", AsyncMock(return_value=UserSessionState(found=True, status="active"))
    )

    async def invalidate(*_args):
        store.events.append("invalidate")

    invalidated = AsyncMock(side_effect=invalidate)
    monkeypatch.setattr(org, "invalidate_user_session_state", invalidated)
    app = FastAPI()
    app.include_router(org.router, prefix="/api/v1")
    app.add_middleware(middleware.GrantexAuthMiddleware)
    return SimpleNamespace(store=store, app=app, invalidated=invalidated, cleared=cleared)


def _token(replay, **overrides):
    actor = replay.store.actor
    claims = {
        "sub": actor.email, "agenticorg:user_id": str(actor.id),
        "agenticorg:tenant_id": str(TENANT), "role": "admin", "grantex:scopes": ["agenticorg:admin"],
    }
    claims.update(overrides)
    return jwt_auth.create_access_token(claims)


async def _reset(replay, target=None, *, bearer=None, cookie=None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer is not None else {}
    cookies = {"agenticorg_session": cookie} if cookie is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=replay.app), base_url="http://example.test", cookies=cookies,
    ) as client:
        return await client.delete(f"/api/v1/org/members/{target or replay.store.target.id}/mfa", headers=headers)


def _unchanged(replay, before):
    assert replay.store.snapshot() == before
    assert replay.store.user_commits == 0
    replay.invalidated.assert_not_awaited()
    replay.cleared.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_owner", [False, True], ids=["other-human", "key-owner"])
@pytest.mark.parametrize("admin_key", [True, False], ids=["admin-key", "nonadmin-key"])
async def test_api_key_cannot_clear_human_mfa(replay, target_owner, admin_key):
    if not admin_key:
        replay.store.key.scopes = ["agents:read"]
    before = replay.store.snapshot()
    target = replay.store.actor.id if target_owner else replay.store.target.id
    response = await _reset(replay, target, bearer=API_TOKEN)
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_explicit_machine_credential_cannot_borrow_human_cookie(replay):
    before = replay.store.snapshot()
    response = await _reset(replay, bearer=API_TOKEN, cookie=_token(replay))
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_delegated_machine_cannot_borrow_human_admin_identity(replay, monkeypatch):
    claims = jwt_auth.validate_local_token(_token(replay))
    claims["agenticorg:agent_id"] = str(uuid.UUID(int=5))
    claims["grantex:grant_id"] = "synthetic-delegation"
    monkeypatch.setattr(middleware, "_is_grantex_token", lambda _token: True)
    monkeypatch.setattr(middleware, "resolve_grantex_claims", AsyncMock(return_value=claims))
    before = replay.store.snapshot()
    response = await _reset(replay, bearer="synthetic-delegated-token")
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_buyer_credential_cannot_clear_human_mfa(replay):
    before = replay.store.snapshot()
    response = await _reset(replay, bearer="ao_buyer_synthetic_credential")
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_revoked_human_session_preserves_mfa(replay, monkeypatch):
    monkeypatch.setattr(
        middleware, "get_user_session_state",
        AsyncMock(return_value=UserSessionState(
            found=True, status="active", sessions_invalid_before=datetime.now(UTC).timestamp() + 60,
        )),
    )
    before = replay.store.snapshot()
    response = await _reset(replay, bearer=_token(replay))
    assert response.status_code == 401
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["cookie", "bearer", "legacy-email"])
async def test_active_human_admin_resets_other_member_and_revokes_sessions(replay, credential):
    token = _token(replay)
    if credential == "legacy-email":
        token = _token(replay, **{"agenticorg:user_id": None, "sub": "ADMIN@EXAMPLE.TEST"})
    before_actor = replay.store.snapshot()[replay.store.actor.id]
    before = datetime.now(UTC)
    response = await _reset(replay, **{"cookie" if credential == "cookie" else "bearer": token})
    assert response.status_code == 200
    assert response.json() == {"status": "mfa_reset", "user_id": str(replay.store.target.id)}
    target = replay.store.target
    assert target.mfa_enabled is False and target.mfa_recovery_codes == []
    assert all(getattr(target, name) is None for name in MFA_FIELDS[1:5] + ("mfa_enabled_at",))
    assert target.sessions_invalid_before >= before
    assert replay.store.snapshot()[replay.store.actor.id] == before_actor
    assert replay.store.user_commits == 1
    assert replay.store.events == ["user_commit", "invalidate"]
    replay.cleared.assert_called_once_with(target)
    replay.invalidated.assert_awaited_once_with(str(TENANT), target.email)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("role", "analyst"), ("role", "developer"), ("status", "inactive"),
     ("status", "pending"), ("status", "deleted"), ("tenant_id", OTHER_TENANT)],
)
async def test_claimed_admin_scope_and_role_cannot_override_authoritative_actor(replay, field, value):
    token = _token(replay)
    setattr(replay.store.actor, field, value)
    before = replay.store.snapshot()
    response = await _reset(replay, bearer=token)
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"agenticorg:user_id": str(uuid.UUID(int=99))},
        {"agenticorg:user_id": "not-a-uuid"},
        {"agenticorg:user_id": None, "sub": "missing@example.test"},
        {"agenticorg:user_id": None, "sub": ""},
        {"agenticorg:tenant_id": str(OTHER_TENANT)},
        {"grantex:grant_id": "synthetic-delegation"},
        {"sub": "apikey:synthetic"},
    ],
)
async def test_invalid_missing_cross_tenant_or_machine_identity_preserves_mfa(replay, claims):
    before = replay.store.snapshot()
    response = await _reset(replay, bearer=_token(replay, **claims))
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_user_id_cannot_fall_back_to_another_admin_email(replay):
    replay.store.actor.role = "analyst"
    before = replay.store.snapshot()
    response = await _reset(replay, bearer=_token(replay, sub=replay.store.target.email))
    assert response.status_code == 403
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["ADMIN@EXAMPLE.TEST", "old-admin@example.test"])
async def test_self_reset_uses_immutable_user_id_not_subject_email(replay, subject):
    before = replay.store.snapshot()
    response = await _reset(replay, replay.store.actor.id, bearer=_token(replay, sub=subject))
    assert response.status_code == 400
    _unchanged(replay, before)


@pytest.mark.asyncio
async def test_legacy_email_session_resolves_id_before_self_reset_check(replay):
    before = replay.store.snapshot()
    response = await _reset(
        replay, replay.store.actor.id,
        bearer=_token(replay, **{"agenticorg:user_id": None, "sub": "ADMIN@EXAMPLE.TEST"}),
    )
    assert response.status_code == 400
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "forged", "expired", "no-scope", "no-tenant"])
async def test_missing_invalid_or_insufficient_credentials_preserve_mfa(replay, kind):
    before = replay.store.snapshot()
    token = _token(replay)
    expected = 401
    if kind == "missing":
        token = None
    elif kind == "forged":
        claims = jwt.decode(token, options={"verify_signature": False})
        token = jwt.encode(claims, "synthetic-wrong-signing-key-at-least-32-bytes", algorithm="HS256")
    elif kind == "expired":
        token = jwt_auth.create_access_token(
            {"sub": replay.store.actor.email, "agenticorg:tenant_id": str(TENANT)}, expires_minutes=-1
        )
    elif kind == "no-scope":
        token = _token(replay, **{"grantex:scopes": []})
        expected = 403
    elif kind == "no-tenant":
        token = _token(replay, **{"agenticorg:tenant_id": ""})
    response = await _reset(replay, bearer=token)
    assert response.status_code == expected
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["foreign", "missing", "deleted", "malformed"])
async def test_unavailable_or_other_tenant_target_preserves_mfa(replay, target):
    target_id = {"foreign": replay.store.foreign.id, "missing": uuid.UUID(int=99), "malformed": "not-a-uuid"}
    if target == "deleted":
        replay.store.target.status = "deleted"
    before = replay.store.snapshot()
    response = await _reset(replay, target_id.get(target), bearer=_token(replay))
    assert response.status_code == 404
    _unchanged(replay, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["demoted", "inactive", "removed"])
async def test_actor_is_revalidated_under_mutation_locks(replay, change):
    def revoke_actor():
        if change == "removed":
            replay.store.users.remove(replay.store.actor)
        else:
            setattr(replay.store.actor, "role" if change == "demoted" else "status",
                    "analyst" if change == "demoted" else "inactive")

    replay.store.before_lock = revoke_actor
    before_target = replay.store.snapshot()[replay.store.target.id]
    response = await _reset(replay, bearer=_token(replay))
    assert response.status_code == 403
    assert replay.store.snapshot()[replay.store.target.id] == before_target
    assert replay.store.user_commits == 0
    replay.invalidated.assert_not_awaited()
    replay.cleared.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_first", [True, False])
async def test_actor_and_target_lock_query_uses_stable_id_order(replay, actor_first):
    if actor_first:
        replay.store.actor.id, replay.store.target.id = replay.store.target.id, replay.store.actor.id
    response = await _reset(replay, bearer=_token(replay))
    assert response.status_code == 200
    assert len(replay.store.lock_queries) == 1
    statement = replay.store.lock_queries[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "ORDER BY users.id FOR UPDATE" in str(compiled)
    assert compiled.params["tenant_id_1"] == TENANT
    assert set(compiled.params["id_1"]) == {replay.store.actor.id, replay.store.target.id}
