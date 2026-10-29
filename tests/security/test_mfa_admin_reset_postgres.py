# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Opt-in local PostgreSQL/Redis replay of the human-admin MFA reset boundary.

Set AGENTICORG_MFA_ADMIN_POSTGRES_TESTS=1 and provide
AGENTICORG_MFA_ADMIN_POSTGRES_URL (bootstrap login, empty mfa_admin_tests DB)
and AGENTICORG_MFA_ADMIN_REDIS_URL (dedicated Redis DB 13). Set
AGENTICORG_MFA_ADMIN_POSTGRES_REQUIRED=1 in the dedicated CI job: missing opt-in
or configuration then fails instead of skipping. Only exact loopback/service
hosts and standard ports are accepted. Never point these tests at shared data.

Five tables come from ORM metadata, NOT the full migration chain. The actual
v6z16 upgrade installs its users/API-key pre-auth RLS policies. Requests use a
generated non-owner, NOSUPERUSER/NOBYPASSRLS login, real transaction-local GUCs,
Redis, bcrypt, JWT validation, the org router and its dependencies, Grantex auth,
CSRF and route-metadata enforcement. No full-app lifespan or provider is run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import bcrypt
import httpx
import jwt
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"), pytest.mark.ambient_redis,
    pytest.mark.real_flag_store, pytest.mark.real_flag_lookup,
]

_TABLES = ("tenants", "companies", "departments", "users", "api_keys")
_MFA_FIELDS = (
    "mfa_enabled", "mfa_totp_secret", "mfa_pending_secret", "mfa_pending_created_at",
    "mfa_last_totp_step", "mfa_recovery_codes", "mfa_enabled_at", "sessions_invalid_before",
)


def _validated_urls(database_url: str, redis_url: str):
    try:
        database = make_url(database_url)
        cache = urlsplit(redis_url)
        safe = (
            database.drivername == "postgresql+asyncpg"
            and database.host in {"localhost", "127.0.0.1", "::1", "postgres"}
            and database.port in {None, 5432} and database.database == "mfa_admin_tests"
            and bool(database.username and database.password) and not database.query
            and cache.scheme == "redis"
            and cache.hostname in {"localhost", "127.0.0.1", "::1", "redis"}
            and cache.port in {None, 6379} and cache.path == "/13"
            and not cache.query and not cache.fragment
        )
    except (ArgumentError, TypeError, ValueError):
        raise ValueError("Invalid local MFA test configuration") from None
    if not safe:
        raise ValueError("Refusing nonlocal or non-dedicated MFA test configuration")
    return database, redis_url


def _configuration():
    enabled = os.getenv("AGENTICORG_MFA_ADMIN_POSTGRES_TESTS") == "1"
    required = os.getenv("AGENTICORG_MFA_ADMIN_POSTGRES_REQUIRED") == "1"
    if not enabled:
        if required:
            pytest.fail("Required MFA PostgreSQL replay is not explicitly enabled")
        pytest.skip("Local MFA PostgreSQL replay requires explicit opt-in")
    try:
        return _validated_urls(
            os.getenv("AGENTICORG_MFA_ADMIN_POSTGRES_URL", ""),
            os.getenv("AGENTICORG_MFA_ADMIN_REDIS_URL", ""),
        )
    except ValueError:
        pytest.fail("Explicit safe PostgreSQL and Redis URLs are required; configuration refused")


@pytest.mark.parametrize(
    ("database_url", "redis_url"),
    [
        ("postgresql+asyncpg://test:test@postgres.example/mfa_admin_tests", "redis://redis/13"),
        ("postgresql+asyncpg://test:test@postgres/other", "redis://redis/13"),
        ("postgresql+asyncpg://test:test@postgres/mfa_admin_tests?host=elsewhere", "redis://redis/13"),
        ("postgresql+asyncpg://test:test@postgres/mfa_admin_tests", "redis://redis.example/13"),
        ("postgresql+asyncpg://test:test@postgres/mfa_admin_tests", "redis://redis/14"),
        ("postgresql+asyncpg://test:test@postgres/mfa_admin_tests", "redis://redis/13?db=15"),
    ],
)
async def test_replay_configuration_refuses_unsafe_targets(database_url, redis_url):
    with pytest.raises(ValueError, match="configuration"):
        _validated_urls(database_url, redis_url)


@pytest.mark.parametrize("enabled", [False, True])
async def test_required_mode_never_skips_missing_configuration(monkeypatch, enabled):
    monkeypatch.setenv("AGENTICORG_MFA_ADMIN_POSTGRES_REQUIRED", "1")
    monkeypatch.setenv("AGENTICORG_MFA_ADMIN_POSTGRES_TESTS", "1" if enabled else "0")
    monkeypatch.delenv("AGENTICORG_MFA_ADMIN_POSTGRES_URL", raising=False)
    monkeypatch.delenv("AGENTICORG_MFA_ADMIN_REDIS_URL", raising=False)
    with pytest.raises(pytest.fail.Exception):
        _configuration()


def _create_schema(connection):
    from core.models.base import BaseModel

    metadata = MetaData()
    for name in _TABLES:
        BaseModel.metadata.tables[name].to_metadata(metadata)
    metadata.create_all(connection)
    path = Path(__file__).resolve().parents[2] / "migrations/versions/v6_z16_rls_tenant_coverage.py"
    spec = importlib.util.spec_from_file_location("mfa_test_rls_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


@dataclass
class _Backend:
    owner: object
    factory: object
    cache: object
    role: str
    app: object


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def backend():
    owner_url, redis_url = _configuration()
    from fastapi import Depends, FastAPI

    from api.route_enforcement import enforce_route_metadata
    from api.v1 import org
    from auth import jwt as jwt_auth
    from auth.csrf_middleware import CSRFMiddleware
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core import auth_state
    from core.config import settings

    owner = create_async_engine(
        owner_url, poolclass=NullPool, hide_parameters=True, connect_args={"timeout": 5, "command_timeout": 10},
    )
    role = "mfa_admin_app_" + uuid.uuid4().hex
    password = secrets.token_urlsafe(32)
    role_sql = '"' + role + '"'
    app_engine = None
    cache = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    token_cache = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3)
    guard = await owner.connect()
    created_schema = False
    created_role = False
    initial_keys = None
    try:
        assert await guard.scalar(text("SELECT current_database()")) == "mfa_admin_tests"
        assert await guard.scalar(text("SELECT pg_try_advisory_lock(73429102)")), "Replay DB already in use"
        assert not (await guard.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))).all(), "Replay requires an empty, dedicated database"
        await cache.ping()
        initial_keys = {key async for key in cache.scan_iter()}
        async with owner.begin() as connection:
            # PostgreSQL format quotes both the generated identifier and password as data.
            create_role = await connection.scalar(text(
                "SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L', CAST(:role AS text), CAST(:password AS text))"
            ), {"role": role, "password": password})
            await connection.exec_driver_sql(create_role)
            await connection.run_sync(_create_schema)
            await connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role_sql}")
            await connection.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON users, api_keys TO {role_sql}")
            await connection.exec_driver_sql(f"GRANT SELECT ON tenants TO {role_sql}")
        created_schema = True
        created_role = True
        app_url = owner_url.set(username=role, password=password)
        app_engine = create_async_engine(
            app_url, pool_size=5, max_overflow=1, hide_parameters=True,
            connect_args={"timeout": 5, "command_timeout": 10, "server_settings": {
                "statement_timeout": "10000", "lock_timeout": "7000", "application_name": "mfa-admin-replay",
            }},
        )
        factory = async_sessionmaker(app_engine, expire_on_commit=False)
        app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])
        app.include_router(org.router, prefix="/api/v1")
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(GrantexAuthMiddleware)
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("AGENTICORG_ENV", "test")
            patch.setenv("AGENTICORG_AUTH_STATE_STRICT", "1")
            patch.setenv("AGENTICORG_REDIS_URL", redis_url)
            patch.setenv("AGENTICORG_VAULT_KEYRING", "test:" + secrets.token_urlsafe(32))
            patch.setattr(settings, "env", "test")
            patch.setattr(settings, "secret_key", secrets.token_urlsafe(48))
            patch.setattr(settings, "db_url", app_url.render_as_string(hide_password=False))
            patch.setattr(settings, "redis_url", redis_url)
            patch.setattr(settings, "jwt_public_key_url", "")
            # These are real connected clients, not cache/DB/rate doubles.
            patch.setattr(auth_state, "_redis", cache)
            patch.setattr(auth_state, "_mem_user_state", {})
            patch.setattr(jwt_auth, "_redis_client", token_cache)
            assert auth_state._strict() and jwt_auth._auth_state_strict()
            assert cache.connection_pool.connection_kwargs["db"] == 13
            yield _Backend(owner, factory, cache, role, app)
    finally:
        if app_engine is not None:
            await app_engine.dispose()
        try:
            # Only keys created in our exclusively allocated Redis DB are removed.
            if initial_keys is not None:
                keys = {key async for key in cache.scan_iter()} - initial_keys
                if keys:
                    await cache.delete(*keys)
        finally:
            await cache.aclose()
            await token_cache.aclose()
            try:
                async with owner.begin() as connection:
                    if created_schema:
                        await connection.exec_driver_sql(
                            "DROP TABLE api_keys, users, departments, companies, tenants CASCADE"
                        )
                    if created_role:
                        await connection.exec_driver_sql(f"DROP OWNED BY {role_sql}")
                        await connection.exec_driver_sql(f"DROP ROLE {role_sql}")
            finally:
                await guard.close()  # releases the session-level advisory lock
                await owner.dispose()


@dataclass
class _Case:
    backend: _Backend
    actor: object
    target: object
    foreign: object
    key_id: uuid.UUID
    api_token: str
    ip: str

    def token(self, user=None, **claims):
        from auth.jwt import create_access_token
        from core.config import settings

        user = user or self.actor
        token = create_access_token({
            "sub": user.email, "agenticorg:user_id": str(user.id),
            "agenticorg:tenant_id": str(user.tenant_id), "role": "admin",
            "grantex:scopes": ["agenticorg:admin"], **claims,
        })
        payload = jwt.decode(token, options={"verify_signature": False})
        payload["iat"] = int(time.time()) - 60
        return jwt.encode(payload, settings.secret_key, algorithm="HS256")

    async def reset(self, target=None, *, token=None, cookie=False, csrf="valid"):
        from auth.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, generate_csrf_token

        headers, cookies = {}, {}
        if cookie:
            value = generate_csrf_token()
            cookies = {"agenticorg_session": token or self.token()}
            if csrf != "missing":
                cookies[CSRF_COOKIE_NAME] = value
                headers[CSRF_HEADER_NAME] = value if csrf == "valid" else generate_csrf_token()
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.backend.app, client=(self.ip, 12345)),
            base_url="http://example.test", cookies=cookies,
        ) as client:
            return await client.delete(f"/api/v1/org/members/{target or self.target.id}/mfa", headers=headers)

    async def snapshot(self):
        from core.models.user import User

        fields = _MFA_FIELDS + (("mfa_challenge_generation",) if hasattr(User, "mfa_challenge_generation") else ())
        async with async_sessionmaker(self.backend.owner)() as session:
            users = (await session.execute(select(User).where(
                User.id.in_([self.actor.id, self.target.id, self.foreign.id])
            ))).scalars().all()
            return {user.id: {name: getattr(user, name) for name in fields} for user in users}


@pytest_asyncio.fixture(loop_scope="module")
async def case(backend):
    from core import auth_state, database
    from core.crypto.credential_vault import encrypt_credential
    from core.models.api_key import APIKey
    from core.models.tenant import Tenant
    from core.models.user import User

    tenant, foreign_tenant = uuid.uuid4(), uuid.uuid4()
    ids = sorted([uuid.uuid4(), uuid.uuid4()])
    marker = uuid.uuid4().hex
    now = datetime.now(UTC) - timedelta(days=1)
    users = [User(
        id=user_id, tenant_id=scope, email=f"{name}-{marker}@example.test", name="Synthetic administrator",
        role="admin", status="active", mfa_enabled=True,
        mfa_totp_secret=encrypt_credential("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        mfa_pending_secret=encrypt_credential("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        mfa_pending_created_at=now, mfa_last_totp_step=123, mfa_recovery_codes=["synthetic-recovery-hash"],
        mfa_enabled_at=now, sessions_invalid_before=now,
    ) for user_id, scope, name in [
        (ids[0], tenant, "admin"), (ids[1], tenant, "member"), (uuid.uuid4(), foreign_tenant, "foreign"),
    ]]
    api_token = "ao_sk_" + secrets.token_urlsafe(32)
    key_id = uuid.uuid4()
    async with async_sessionmaker(backend.owner, expire_on_commit=False)() as session:
        session.add_all([
            Tenant(id=scope, name="Synthetic tenant", slug=uuid.uuid4().hex) for scope in [tenant, foreign_tenant]
        ])
        await session.flush()
        session.add_all(users)
        await session.flush()
        session.add(APIKey(
            id=key_id, tenant_id=tenant, user_id=users[0].id, name="Synthetic administrator key",
            prefix=f"ao_sk_{api_token[6:12]}",
            key_hash=bcrypt.hashpw(api_token.encode(), bcrypt.gensalt(rounds=4)).decode(),
            scopes=["agenticorg:admin"], status="active",
        ))
        await session.commit()
    auth_state._mem_user_state.clear()
    instance = _Case(backend, *users, key_id, api_token, f"192.0.2.{secrets.randbelow(240) + 1}")
    try:
        with database.session_factory_scope(lambda: backend.factory):
            yield instance
    finally:
        async with async_sessionmaker(backend.owner)() as session:
            await session.execute(delete(APIKey).where(APIKey.id == key_id))
            await session.execute(delete(User).where(User.id.in_([user.id for user in users])))
            await session.execute(delete(Tenant).where(Tenant.id.in_([tenant, foreign_tenant])))
            await session.commit()
        auth_state._mem_user_state.clear()


async def _owner_change(case, user, **values):
    from core.models.user import User

    async with async_sessionmaker(case.backend.owner)() as session:
        await session.execute(update(User).where(User.id == user.id).values(**values))
        await session.commit()


def _assert_cleared(before, after):
    assert after["mfa_enabled"] is False and after["mfa_recovery_codes"] == []
    assert all(after[name] is None for name in _MFA_FIELDS[1:5] + ("mfa_enabled_at",))
    assert after["sessions_invalid_before"] > before["sessions_invalid_before"]
    if "mfa_challenge_generation" in before:
        assert after["mfa_challenge_generation"] == before["mfa_challenge_generation"] + 1


@pytest.mark.parametrize("owner_target", [True, False], ids=["key-owner", "other-human"])
async def test_machine_admin_cannot_reset_human_mfa(case, owner_target):
    before = await case.snapshot()
    target_id = case.actor.id if owner_target else case.target.id
    response = await case.reset(target_id, token=case.api_token)
    async with case.backend.owner.connect() as connection:
        used = await connection.scalar(text("SELECT last_used_at FROM api_keys WHERE id = :id"), {"id": case.key_id})
    assert used is not None, "The real bcrypt/API-key middleware did not accept the credential"
    after = await case.snapshot()
    assert response.status_code == 403, (
        f"machine reset returned HTTP {response.status_code}; persisted MFA enabled={after[target_id]['mfa_enabled']}"
    )
    assert after == before


async def test_human_admin_reset_clears_mfa_and_real_redis_session_cache(case):
    from core import auth_state

    before = await case.snapshot()
    old_token = case.token(case.target)
    await auth_state.get_user_session_state(str(case.actor.tenant_id), case.target.email)
    key = auth_state._user_state_key(str(case.actor.tenant_id), case.target.email)
    assert await case.backend.cache.exists(key) == 1
    auth_state._mem_user_state.clear()
    assert (await auth_state.get_user_session_state(str(case.actor.tenant_id), case.target.email)).found
    response = await case.reset(cookie=True)
    assert response.status_code == 200
    after = await case.snapshot()
    _assert_cleared(before[case.target.id], after[case.target.id])
    assert after[case.actor.id] == before[case.actor.id] and after[case.foreign.id] == before[case.foreign.id]
    assert await case.backend.cache.exists(key) == 0
    assert (await case.reset(case.actor.id, token=old_token)).status_code == 401
    assert await case.snapshot() == after
    rate_key = f"auth:rl:admin-mutating:t:{case.actor.tenant_id}"
    assert await case.backend.cache.get(rate_key) == "1", "Real route rate-limit storage was not exercised"


async def test_human_admin_cannot_reset_self(case):
    from core import auth_state

    before = await case.snapshot()
    token = case.token()
    await auth_state.get_user_session_state(str(case.actor.tenant_id), case.actor.email)
    await _owner_change(case, case.actor, email=f"renamed-{uuid.uuid4().hex}@example.test")
    auth_state._mem_user_state.clear()
    # The old subject still has a real cached active session; the action must compare IDs.
    assert (await auth_state.get_user_session_state(str(case.actor.tenant_id), case.actor.email)).status == "active"
    response = await case.reset(case.actor.id, token=token)
    assert response.status_code == 400
    assert await case.snapshot() == before


@pytest.mark.parametrize("csrf", ["missing", "mismatch"])
async def test_cookie_reset_requires_real_csrf_check(case, csrf):
    before = await case.snapshot()
    response = await case.reset(cookie=True, csrf=csrf)
    assert response.status_code == 403 and "CSRF" in response.json()["detail"]
    assert await case.snapshot() == before


@pytest.mark.parametrize("attempt", ["target", "identity"])
async def test_cross_tenant_reset_preserves_all_mfa(case, attempt):
    before = await case.snapshot()
    if attempt == "target":
        response = await case.reset(case.foreign.id, token=case.token())
        assert response.status_code == 404
    else:
        response = await case.reset(token=case.token(**{"agenticorg:user_id": str(case.foreign.id)}))
        assert response.status_code == 403
    assert await case.snapshot() == before


@pytest.mark.parametrize("values", [{"role": "analyst"}, {"status": "inactive"}])
async def test_stale_admin_claims_and_real_cached_active_state_cannot_authorize(case, values):
    from core import auth_state

    await auth_state.get_user_session_state(str(case.actor.tenant_id), case.actor.email)
    await _owner_change(case, case.actor, **values)
    auth_state._mem_user_state.clear()
    cached = await auth_state.get_user_session_state(str(case.actor.tenant_id), case.actor.email)
    assert cached.status == "active"
    before = await case.snapshot()
    response = await case.reset(token=case.token())
    assert response.status_code == 403
    assert await case.snapshot() == before


async def test_bad_api_key_uses_real_redis_failure_counter(case):
    before = await case.snapshot()
    response = await case.reset(token=case.api_token + "wrong")
    assert response.status_code == 401
    assert await case.backend.cache.get(f"auth:failures:{case.ip}") == "1"
    assert await case.snapshot() == before


async def test_missing_credential_preserves_mfa(case):
    before = await case.snapshot()
    assert (await case.reset()).status_code == 401
    assert await case.snapshot() == before


async def test_nonadmin_api_key_preserves_mfa(case):
    async with case.backend.owner.begin() as connection:
        await connection.execute(
            text("UPDATE api_keys SET scopes = ARRAY['agents:read'] WHERE id = :id"), {"id": case.key_id}
        )
    before = await case.snapshot()
    assert (await case.reset(token=case.api_token)).status_code == 403
    assert await case.snapshot() == before


async def test_nonowner_nobypassrls_role_and_actual_pre_auth_policies(case):
    from core import database
    from core.models.api_key import APIKey
    from core.models.user import User

    async with database.get_tenant_session(case.actor.tenant_id) as session:
        role = (await session.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).one()
        assert role == (case.backend.role, False, False)
        assert await session.scalar(text("SELECT current_setting('agenticorg.tenant_id')")) == str(case.actor.tenant_id)
        assert (await session.execute(select(User.id).where(User.id == case.foreign.id))).all() == []
        result = await session.execute(update(User).where(User.id == case.foreign.id).values(mfa_enabled=False))
        assert result.rowcount == 0
        assert (await session.execute(select(APIKey.id))).scalars().all() == [case.key_id]
        await session.commit()
        assert await session.scalar(text("SELECT current_setting('agenticorg.tenant_id')")) == str(case.actor.tenant_id)
    async with database.get_tenant_session(case.foreign.tenant_id) as session:
        assert (await session.execute(select(APIKey.id))).all() == []
    async with database.get_platform_session() as session:
        assert (await session.execute(select(APIKey.id))).scalars().all() == [case.key_id]
        visible = set((await session.execute(select(User.id))).scalars())
        assert visible == {case.actor.id, case.target.id, case.foreign.id}
    async with case.backend.owner.connect() as connection:
        rows = (await connection.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
            "FROM pg_class c WHERE c.oid IN ('users'::regclass, 'api_keys'::regclass)"
        ))).all()
        assert len(rows) == 2
        assert all(enabled and forced and owner != case.backend.role for _, enabled, forced, owner in rows)


async def _wait_for_lock_waiters(case, count=1):
    deadline = asyncio.get_running_loop().time() + 4
    while asyncio.get_running_loop().time() < deadline:
        async with case.backend.owner.connect() as connection:
            queries = (await connection.execute(text(
                "SELECT query FROM pg_stat_activity WHERE datname = current_database() AND usename = :role "
                "AND wait_event_type = 'Lock' AND query LIKE '%FOR UPDATE%'"
            ), {"role": case.backend.role})).scalars().all()
        if len(queries) >= count:
            assert all("ORDER BY users.id FOR UPDATE" in query for query in queries)
            return
        await asyncio.sleep(0.02)
    pytest.fail("Expected bounded real PostgreSQL row-lock wait was not observed")


async def _cancel(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_target_reset_waits_for_real_user_row_lock(case):
    from core import database
    from core.models.user import User

    before = await case.snapshot()
    tasks = []
    try:
        async with database.get_tenant_session(case.actor.tenant_id) as blocker:
            await blocker.execute(select(User).where(User.id == case.target.id).with_for_update())
            tasks.append(asyncio.create_task(case.reset(token=case.token())))
            await _wait_for_lock_waiters(case)
            assert not tasks[0].done() and await case.snapshot() == before
        assert (await asyncio.wait_for(tasks[0], 8)).status_code == 200
        _assert_cleared(before[case.target.id], (await case.snapshot())[case.target.id])
    finally:
        await _cancel(tasks)


@pytest.mark.parametrize("values", [{"role": "analyst"}, {"status": "inactive"}])
async def test_actor_authority_is_rechecked_after_real_lock_wait(case, values):
    from core import database
    from core.models.user import User

    before = await case.snapshot()
    tasks = []
    try:
        async with database.get_tenant_session(case.actor.tenant_id) as blocker:
            actor = (await blocker.execute(select(User).where(User.id == case.actor.id).with_for_update())).scalar_one()
            tasks.append(asyncio.create_task(case.reset(token=case.token())))
            await _wait_for_lock_waiters(case)
            for name, value in values.items():
                setattr(actor, name, value)
        assert (await asyncio.wait_for(tasks[0], 8)).status_code == 403
        assert await case.snapshot() == before
    finally:
        await _cancel(tasks)


async def test_reciprocal_admin_resets_lock_both_users_in_id_order(case):
    from core import database
    from core.models.user import User

    before = await case.snapshot()
    tasks = []
    try:
        async with database.get_tenant_session(case.actor.tenant_id) as blocker:
            await blocker.execute(select(User).where(User.id == case.actor.id).with_for_update())
            tasks = [
                asyncio.create_task(case.reset(token=case.token())),
                asyncio.create_task(case.reset(case.actor.id, token=case.token(case.target))),
            ]
            await _wait_for_lock_waiters(case, 2)
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 8)
        assert [response.status_code for response in responses] == [200, 200]
        after = await case.snapshot()
        for user in [case.actor, case.target]:
            _assert_cleared(before[user.id], after[user.id])
        assert after[case.foreign.id] == before[case.foreign.id]
    finally:
        await _cancel(tasks)
