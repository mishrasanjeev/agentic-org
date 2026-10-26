# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Opt-in HTTP regressions with real PostgreSQL transactions and Redis state.

Set MFA_STATE_TEST_PG_URL (postgresql+asyncpg, database mfa_state_tests),
MFA_STATE_TEST_REDIS_URL (redis, exclusively owned DB14), and
MFA_STATE_TEST_ENABLE=1. MFA_STATE_TEST_REQUIRED=1 fails instead of skipping
when configuration is missing. Run serially against initially empty, exclusively
owned DB14. Cleanup deletes only keys derived from this test's identities.

This mounts the actual auth/MFA routers, GrantexAuthMiddleware and
CSRFMiddleware, not the full application. Full ORM tables plus their FK
closure are created in a random schema, not migrated. This does not certify
Alembic or NOBYPASSRLS. Passwords use real bcrypt, codes use the real vault,
and every challenge, budget, blacklist and session lookup uses real storage.
Only external mail delivery and Google's verified-claims boundary are synthetic.
The same behavioral tests run on the pre-fix source without new product symbols.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from auth import totp
from core.database import session_factory_scope
from core.models import APIKey, BaseModel, Tenant, User

pytestmark = [
    pytest.mark.asyncio(loop_scope="function"),
    pytest.mark.ambient_redis,
    pytest.mark.real_flag_store,
    pytest.mark.real_flag_lookup,
]

PASSWORD = "SyntheticPassword42!"
NEW_PASSWORD = "ReplacementPassword73!"
PREFIX = "/api/v1"


def _configuration() -> tuple[str, str]:
    pg = os.getenv("MFA_STATE_TEST_PG_URL", "")
    redis = os.getenv("MFA_STATE_TEST_REDIS_URL", "")
    if os.getenv("MFA_STATE_TEST_ENABLE") != "1" or not pg or not redis:
        message = "Explicit MFA_STATE_TEST_ENABLE=1 and dedicated PostgreSQL/Redis URLs required"
        if os.getenv("MFA_STATE_TEST_REQUIRED") == "1":
            pytest.fail(message)
        pytest.skip(message)
    database = make_url(pg)
    cache = urlsplit(redis)
    if (
        database.drivername != "postgresql+asyncpg"
        or database.database != "mfa_state_tests"
        or not database.host
        or cache.scheme != "redis"
        or not cache.hostname
        or cache.path != "/14"
        or cache.query
        or cache.fragment
    ):
        pytest.fail("Unsafe integration targets: require PostgreSQL mfa_state_tests and exclusive Redis DB14")
    # These tests must never be pointed at an internet-accessible service.
    for host in (database.host, cache.hostname):
        if host not in {"localhost", "127.0.0.1", "postgres", "redis", "mfa-test-postgres", "mfa-test-redis"}:
            pytest.fail("Integration hosts must be loopback or isolated MFA Docker services")
    if os.getenv("PYTEST_XDIST_WORKER"):
        pytest.fail("Run serially: Redis DB14 is exclusively owned by this suite")
    return pg, redis


def _no_session(response: httpx.Response) -> None:
    assert "set-cookie" not in response.headers, "Failure/first factor must not mint cookies"
    if response.headers.get("content-type", "").startswith("application/json"):
        assert "access_token" not in response.json()


def _fk_closure():
    tables = {User.__table__, Tenant.__table__, APIKey.__table__}
    pending = list(tables)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in tables:
                tables.add(fk.column.table)
                pending.append(fk.column.table)
    return list(tables)


@dataclass
class Account:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    browser: httpx.AsyncClient
    secret: str = ""
    recovery: list[str] | None = None


class Harness:
    def __init__(self, app, factory, redis):
        self.app = app
        self.factory = factory
        self.redis = redis
        self.clients: list[httpx.AsyncClient] = []
        self.owned_keys: set[str] = set()

    def client(self, ip: str = "192.0.2.10") -> httpx.AsyncClient:
        self.owned_keys.update(f"auth:{kind}:{ip}" for kind in ("login_attempts", "failures", "blocked", "reset_ip"))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=(ip, 12345), raise_app_exceptions=False),
            base_url="https://mfa.example.test",
        )
        self.clients.append(client)
        return client

    async def account(self, *, enrolled: bool = True, tenant_id=None) -> Account:
        from api.v1.auth import _hash_password

        tid, uid = tenant_id or uuid.uuid4(), uuid.uuid4()
        email = f"member-{uid.hex}@example.test"
        scope_digest = hashlib.sha256(f"{tid}:{uid}".encode("ascii")).hexdigest()
        state_digest = hashlib.sha256(f"{tid}:{email}".encode()).hexdigest()
        email_digest = hashlib.sha256(email.encode()).hexdigest()
        self.owned_keys.update({
            f"onetime-attempts:mfa-account:{scope_digest}", f"auth:user_state:{state_digest}",
            f"auth:reset_email:{email_digest}",
        })
        async with self.factory() as session:
            if tenant_id is None:
                session.add(Tenant(id=tid, name="Synthetic Organization", slug=tid.hex, settings={}))
                await session.flush()
            session.add(User(
                id=uid, tenant_id=tid, email=email, name="Synthetic Member", role="analyst",
                status="active", domain="finance", password_hash=await _hash_password(PASSWORD),
            ))
            await session.commit()
        browser = self.client()
        response = await browser.post(f"{PREFIX}/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
        assert browser.cookies.get("agenticorg_session")
        assert browser.cookies.get("agenticorg_csrf")
        account = Account(uid, tid, email, browser)
        if enrolled:
            await self.enroll(account)
        return account

    async def mutate(self, account: Account, path: str, body: dict | None = None, *, ip=None):
        browser = account.browser
        if ip:
            browser = self.client(ip)
            browser.cookies.update(account.browser.cookies)
        return await browser.post(
            f"{PREFIX}{path}", json=body,
            headers={"X-CSRF-Token": account.browser.cookies["agenticorg_csrf"]},
        )

    async def enroll(self, account: Account) -> None:
        setup = await self.mutate(account, "/auth/mfa/setup")
        assert setup.status_code == 200, setup.text
        account.secret = setup.json()["secret"].replace(" ", "")
        assert setup.json()["qr_code"].startswith("data:image/svg+xml;base64,")
        # Use the previous accepted drift step so the current code is available
        # for a real sign-in immediately, without freezing the application clock.
        code = totp.code_at(account.secret, totp.current_step() - 1)
        enabled = await self.mutate(account, "/auth/mfa/enable", {"code": code})
        assert enabled.status_code == 200, enabled.text
        account.recovery = enabled.json()["recovery_codes"]
        assert len(account.recovery) == 10

    async def challenge(self, account: Account, *, ip="192.0.2.10", password=PASSWORD, method="password") -> str:
        client = self.client(ip)
        path = "/auth/google" if method == "google" else "/auth/login"
        body = {"credential": account.email} if method == "google" else {"email": account.email, "password": password}
        response = await client.post(f"{PREFIX}{path}", json=body)
        assert response.status_code == 200, response.text
        _no_session(response)
        assert response.json()["mfa_required"] is True
        code = response.json()["mfa_challenge"]
        self.owned_keys.update({f"onetime:mfa:{code}", f"onetime-attempts:mfa:{code}"})
        stored = await self.redis.get(f"onetime:mfa:{code}")
        assert stored is not None
        payload = json.loads(stored)
        assert payload["user_id"] == str(account.id)
        assert payload["tenant_id"] == str(account.tenant_id)
        assert payload["method"] == method
        assert 0 < await self.redis.ttl(f"onetime:mfa:{code}") <= 300
        return code

    async def verify(self, challenge: str, code: str, *, ip="192.0.2.10"):
        response = await self.client(ip).post(
            f"{PREFIX}/auth/mfa/verify", json={"challenge": challenge, "code": code},
        )
        if response.status_code != 200:
            _no_session(response)
        return response

    async def row(self, account: Account):
        async with self.factory() as session:
            return (await session.execute(select(User).where(User.id == account.id))).scalar_one()


async def _close_product_redis_clients():
    from api.v1 import auth
    from auth import jwt
    from core import auth_state

    # Loop-local lifecycle only: no replacements for storage or rate decisions.
    for module, name in ((auth, "_throttle_redis"), (jwt, "_redis_client"), (auth_state, "_redis")):
        client = getattr(module, name, None)
        if client is not None:
            await client.aclose()
            setattr(module, name, None)
    auth_state._mem_user_state.clear()


@pytest_asyncio.fixture(loop_scope="function")
async def state(monkeypatch):
    pg_url, redis_url = _configuration()
    from api.v1 import auth, auth_mfa, org
    from auth.csrf_middleware import CSRFMiddleware
    from auth.grantex_middleware import GrantexAuthMiddleware
    from core.config import settings

    monkeypatch.setenv("AGENTICORG_REDIS_URL", redis_url)
    monkeypatch.setenv("AGENTICORG_AUTH_STATE_STRICT", "1")
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")
    monkeypatch.setenv("AGENTICORG_SECRET_KEY", "synthetic-mfa-state-tests-key-not-for-deployment")
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "secret_key", "synthetic-mfa-state-tests-key-not-for-deployment")
    await _close_product_redis_clients()
    schema = f"mfa_state_{uuid.uuid4().hex}"
    engine = create_async_engine(pg_url, poolclass=NullPool, execution_options={"schema_translate_map": {None: schema}})
    redis = Redis.from_url(redis_url, decode_responses=True)
    harness = None
    created = False
    try:
        assert await redis.ping()
        if await redis.dbsize():
            pytest.fail("Redis DB14 must initially be empty and exclusively owned; no existing keys were deleted")
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == "mfa_state_tests"
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
            await connection.run_sync(lambda conn: BaseModel.metadata.create_all(conn, tables=_fk_closure()))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        app = FastAPI()
        app.include_router(auth.router, prefix=PREFIX)
        app.include_router(auth_mfa.router, prefix=PREFIX)
        app.include_router(org.router, prefix=PREFIX)
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(GrantexAuthMiddleware)
        harness = Harness(app, factory, redis)
        with session_factory_scope(lambda: factory):
            yield harness
    finally:
        if harness:
            for client in harness.clients:
                await client.aclose()
        await _close_product_redis_clients()
        if created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
        if harness and harness.owned_keys:
            await redis.delete(*harness.owned_keys)
        await redis.aclose()


async def test_f2_new_password_challenge_and_ip_do_not_reset_account_budget(state):
    account = await state.account()
    statuses = []
    for index in range(6):
        ip = f"192.0.2.{20 + index}"
        challenge = await state.challenge(account, ip=ip)
        response = await state.verify(challenge, "not-a-valid-code", ip=ip)
        statuses.append(response.status_code)
    assert statuses == [401] * 5 + [429], f"Fresh-challenge account budget: {statuses}"


@pytest.mark.parametrize("action", ["logout-all", "reset-password", "recovery-codes", "disable-reenroll"])
async def test_f3_real_mutation_revokes_old_challenge(state, action):
    account = await state.account()
    challenge = await state.challenge(account)
    password = PASSWORD
    if action == "logout-all":
        response = await state.mutate(account, "/auth/logout-all")
    elif action == "reset-password":
        response = await state.client().post(f"{PREFIX}/auth/forgot-password", json={"email": account.email})
        assert response.status_code == 200
        keys = [key async for key in state.redis.scan_iter("onetime:reset:*")]
        assert len(keys) == 1
        state.owned_keys.add(keys[0])
        response = await state.client().post(
            f"{PREFIX}/auth/reset-password", json={"code": keys[0].split(":")[-1], "password": NEW_PASSWORD},
        )
        password = NEW_PASSWORD
    elif action == "recovery-codes":
        response = await state.mutate(account, "/auth/mfa/recovery-codes", {"code": account.recovery.pop()})
        assert response.status_code == 200, response.text
        account.recovery = response.json()["recovery_codes"]
    else:
        response = await state.mutate(account, "/auth/mfa/disable", {"code": account.recovery.pop()})
        assert response.status_code == 200, response.text
        await state.enroll(account)
    assert response.status_code == 200, response.text
    if action in {"logout-all", "reset-password"}:
        assert (await state.row(account)).sessions_invalid_before is not None
    before = list((await state.row(account)).mfa_recovery_codes)
    stale = await state.verify(challenge, account.recovery[0])
    assert stale.status_code == 401, f"Old challenge after {action}: HTTP {stale.status_code}"
    _no_session(stale)
    assert (await state.row(account)).mfa_recovery_codes == before
    fresh = await state.challenge(account, password=password)
    control = await state.verify(fresh, account.recovery[0])
    assert control.status_code == 200, control.text
    assert control.json()["user"]["tenant_id"] == str(account.tenant_id)


async def test_success_challenge_replay_and_recovery_replay(state):
    account = await state.account()
    challenge = await state.challenge(account)
    response = await state.verify(challenge, account.recovery[0])
    assert response.status_code == 200, response.text
    assert response.cookies.get("agenticorg_session")
    assert response.cookies.get("agenticorg_csrf")
    assert await state.redis.get(f"onetime:mfa:{challenge}") is None
    assert len((await state.row(account)).mfa_recovery_codes) == 9
    assert (await state.verify(challenge, account.recovery[1])).status_code == 401
    fresh = await state.challenge(account)
    assert (await state.verify(fresh, account.recovery[0])).status_code == 401
    assert (await state.verify(fresh, account.recovery[1])).status_code == 200


async def test_concurrent_same_challenge_mints_exactly_one_session(state):
    account = await state.account()
    challenge = await state.challenge(account)
    responses = await asyncio.gather(*(state.verify(challenge, code) for code in account.recovery[:2]))
    assert sorted(response.status_code for response in responses) == [200, 401]
    assert len((await state.row(account)).mfa_recovery_codes) == 9


async def test_cookie_mutation_requires_real_csrf(state):
    account = await state.account(enrolled=False)
    rejected = await account.browser.post(f"{PREFIX}/auth/mfa/setup")
    assert rejected.status_code == 403
    _no_session(rejected)
    assert (await state.row(account)).mfa_pending_secret is None
    accepted = await state.mutate(account, "/auth/mfa/setup")
    assert accepted.status_code == 200, accepted.text


async def test_f2_replacing_setup_does_not_reset_enable_budget(state):
    account = await state.account(enrolled=False)
    statuses = []
    for index in range(6):
        setup = await state.mutate(account, "/auth/mfa/setup", ip=f"192.0.2.{40 + index}")
        assert setup.status_code == 200, setup.text
        invalid = await state.mutate(
            account, "/auth/mfa/enable", {"code": "not-a-valid-code"}, ip=f"192.0.2.{50 + index}",
        )
        _no_session(invalid)
        statuses.append(invalid.status_code)
    assert statuses == [400] * 5 + [429], f"Replacing-setup account budget: {statuses}"
    assert (await state.row(account)).mfa_enabled is False


async def test_f2_disable_recovery_and_verify_share_one_budget(state):
    account = await state.account()
    statuses = []
    for index, operation in enumerate(["disable", "recovery-codes", "verify"] * 2):
        ip = f"192.0.2.{60 + index}"
        if operation == "verify":
            challenge = await state.challenge(account, ip=ip)
            response = await state.verify(challenge, "not-a-valid-code", ip=ip)
        else:
            response = await state.mutate(account, f"/auth/mfa/{operation}", {"code": "not-a-valid-code"}, ip=ip)
        _no_session(response)
        statuses.append(response.status_code)
    assert statuses == [400, 400, 401, 400, 400, 429], f"Shared mutation/verify budget: {statuses}"
    row = await state.row(account)
    assert row.mfa_enabled is True and len(row.mfa_recovery_codes) == 10


async def test_f2_password_and_google_challenges_share_account_budget(state, monkeypatch):
    from api.v1 import auth
    from core.config import settings

    account = await state.account()
    monkeypatch.setattr(settings, "google_oauth_client_id", "synthetic-client.example.test")

    def verified_claims(credential, _request, _client_id):
        assert credential == account.email
        return {"email": account.email, "email_verified": True, "name": "Synthetic Member"}

    # Only the external provider's verified-claims boundary is synthetic.
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", verified_claims)
    statuses = []
    for index, method in enumerate(["password", "google"] * 3):
        ip = f"192.0.2.{80 + index}"
        challenge = await state.challenge(account, ip=ip, method=method)
        response = await state.verify(challenge, "not-a-valid-code", ip=ip)
        statuses.append(response.status_code)
    assert statuses == [401] * 5 + [429], f"Password/Google account budget: {statuses}"


async def test_cross_tenant_factor_cannot_authorize_another_account(state):
    first = await state.account()
    second = await state.account()
    challenge = await state.challenge(first)
    rejected = await state.verify(challenge, second.recovery[0])
    assert rejected.status_code == 401
    assert len((await state.row(first)).mfa_recovery_codes) == 10
    assert len((await state.row(second)).mfa_recovery_codes) == 10
    accepted = await state.verify(challenge, first.recovery[0])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["tenant_id"] == str(first.tenant_id)


async def test_exhausted_account_does_not_block_other_tenant(state):
    first = await state.account()
    second = await state.account()
    for index in range(5):
        ip = f"192.0.2.{100 + index}"
        challenge = await state.challenge(first, ip=ip)
        assert (await state.verify(challenge, "not-a-valid-code", ip=ip)).status_code == 401
    challenge = await state.challenge(second, ip="192.0.2.110")
    response = await state.verify(challenge, second.recovery[0], ip="192.0.2.110")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["tenant_id"] == str(second.tenant_id)


async def test_concurrent_different_challenges_share_atomic_account_budget(state):
    account = await state.account()
    challenges = [await state.challenge(account, ip=f"192.0.2.{120 + index}") for index in range(9)]
    responses = await asyncio.gather(*(
        state.verify(challenge, "not-a-valid-code", ip=f"192.0.2.{140 + index}")
        for index, challenge in enumerate(challenges)
    ))
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [401] * 5 + [429] * 4, f"Concurrent account budget: {statuses}"


async def test_ascii_totp_positive_and_same_step_replay(state):
    account = await state.account()
    challenge = await state.challenge(account)
    code = totp.code_at(account.secret, totp.current_step())
    response = await state.verify(challenge, code)
    assert response.status_code == 200, response.text
    assert len((await state.row(account)).mfa_recovery_codes) == 10
    fresh = await state.challenge(account)
    assert (await state.verify(fresh, code)).status_code == 401


@pytest.mark.parametrize("digits", ["arabic", "fullwidth"])
async def test_f4_non_ascii_totp_does_not_authenticate_or_consume(state, digits):
    account = await state.account()
    challenge = await state.challenge(account)
    ascii_code = totp.code_at(account.secret, totp.current_step())
    start = 0x0660 if digits == "arabic" else 0xFF10
    translated = "".join(chr(start + int(char)) for char in ascii_code)
    before = (await state.row(account)).mfa_last_totp_step
    rejected = await state.verify(challenge, translated)
    assert rejected.status_code == 401
    assert (await state.row(account)).mfa_last_totp_step == before
    assert await state.redis.exists(f"onetime:mfa:{challenge}")
    control = await state.verify(challenge, account.recovery[0])
    assert control.status_code == 200, control.text


async def test_redis_challenge_store_outage_fails_closed_without_cookies(state, monkeypatch):
    account = await state.account()
    challenge = await state.challenge(account)
    monkeypatch.setenv("AGENTICORG_REDIS_URL", "redis://127.0.0.1:1/14")
    response = await state.verify(challenge, account.recovery[0])
    assert response.status_code == 503, f"Challenge store unavailable: HTTP {response.status_code}"
    assert len((await state.row(account)).mfa_recovery_codes) == 10
    assert await state.redis.exists(f"onetime:mfa:{challenge}")


async def test_postgres_outage_fails_closed_without_cookies(state):
    account = await state.account()
    challenge = await state.challenge(account)
    pg_url, _redis_url = _configuration()
    unavailable = create_async_engine(make_url(pg_url).set(host="127.0.0.1", port=1), poolclass=NullPool)
    factory = async_sessionmaker(unavailable, expire_on_commit=False)
    try:
        with session_factory_scope(lambda: factory):
            response = await state.verify(challenge, account.recovery[0])
        # This proves denial/no cookies, not the application's outage error mapping.
        assert response.status_code in {500, 503}, f"PostgreSQL unavailable: HTTP {response.status_code}"
    finally:
        await unavailable.dispose()
    assert len((await state.row(account)).mfa_recovery_codes) == 10
    assert await state.redis.exists(f"onetime:mfa:{challenge}")


@pytest.mark.parametrize("target", ["self", "other"])
async def test_f1_real_admin_scoped_api_key_cannot_reset_mfa(state, target):
    from api.v1.auth import _hash_password

    owner = await state.account()
    member = owner if target == "self" else await state.account(tenant_id=owner.tenant_id)
    token = f"ao_sk_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    key_id = uuid.uuid4()
    async with state.factory() as session:
        session.add(APIKey(
            id=key_id, tenant_id=owner.tenant_id, user_id=owner.id, name="Synthetic Integration Key",
            prefix=token[:12], key_hash=await _hash_password(token), scopes=["agenticorg:admin"], status="active",
        ))
        await session.commit()
    before = await state.row(member)
    client = state.client("192.0.2.180")
    # Prove the actual bcrypt/DB API-key middleware accepted this credential.
    control = await client.get(f"{PREFIX}/org/members", headers={"Authorization": f"Bearer {token}"})
    assert control.status_code == 200, control.text
    async with state.factory() as session:
        key = (await session.execute(select(APIKey).where(APIKey.id == key_id))).scalar_one()
        assert key.last_used_at is not None
    response = await client.delete(
        f"{PREFIX}/org/members/{member.id}/mfa", headers={"Authorization": f"Bearer {token}"},
    )
    _no_session(response)
    assert response.status_code == 403, f"API key resetting {target}: HTTP {response.status_code}"
    after = await state.row(member)
    assert after.mfa_totp_secret == before.mfa_totp_secret
    assert after.mfa_recovery_codes == before.mfa_recovery_codes
    assert after.sessions_invalid_before == before.sessions_invalid_before


async def test_failed_mutation_keeps_pending_challenge_usable(state):
    account = await state.account()
    challenge = await state.challenge(account)
    before = await state.row(account)
    response = await state.mutate(account, "/auth/mfa/disable", {"code": "not-a-valid-code"})
    assert response.status_code == 400
    _no_session(response)
    after = await state.row(account)
    assert after.mfa_totp_secret == before.mfa_totp_secret
    assert after.mfa_recovery_codes == before.mfa_recovery_codes
    assert (await state.verify(challenge, account.recovery[0])).status_code == 200


async def test_concurrent_same_recovery_code_across_challenges_is_single_use(state):
    account = await state.account()
    challenges = [await state.challenge(account, ip=f"192.0.2.{190 + index}") for index in range(2)]
    responses = await asyncio.gather(*(
        state.verify(challenge, account.recovery[0], ip=f"192.0.2.{200 + index}")
        for index, challenge in enumerate(challenges)
    ))
    assert sorted(response.status_code for response in responses) == [200, 401]
    assert len((await state.row(account)).mfa_recovery_codes) == 9


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("generation", True), ("generation", -1), ("method", "unrecognized"),
    ("generation", "missing"), ("version", "missing"),
])
async def test_f3_malformed_challenge_state_cannot_mint_session(state, field, value):
    account = await state.account()
    challenge = await state.challenge(account)
    key = f"onetime:mfa:{challenge}"
    original = json.loads(await state.redis.get(key))
    corrupted = copy.deepcopy(original)
    if value == "missing":
        corrupted.pop(field, None)
    else:
        corrupted[field] = value
    await state.redis.setex(key, 300, json.dumps(corrupted))
    rejected = await state.verify(challenge, account.recovery[0])
    assert rejected.status_code == 401, f"Malformed {field}: HTTP {rejected.status_code}"
    assert len((await state.row(account)).mfa_recovery_codes) == 10
    fresh = await state.challenge(account, ip="192.0.2.210")
    assert (await state.verify(fresh, account.recovery[0], ip="192.0.2.210")).status_code == 200


async def test_f2_fixed_window_does_not_extend_and_only_valid_factor_clears_it(state):
    account = await state.account()
    digest = hashlib.sha256(f"{account.tenant_id}:{account.id}".encode("ascii")).hexdigest()
    key = f"onetime-attempts:mfa-account:{digest}"
    challenge = await state.challenge(account, ip="192.0.2.220")
    assert (await state.verify(challenge, "not-a-valid-code", ip="192.0.2.220")).status_code == 401
    assert await state.redis.get(key) == "1"
    ttl = await state.redis.pttl(key)
    assert 0 < ttl <= 300_000
    await asyncio.sleep(0.05)
    # Successful first-factor authentication must not erase the factor budget.
    fresh = await state.challenge(account, ip="192.0.2.221")
    assert await state.redis.get(key) == "1"
    assert (await state.verify(fresh, "not-a-valid-code", ip="192.0.2.221")).status_code == 401
    assert await state.redis.get(key) == "2"
    assert 0 < await state.redis.pttl(key) < ttl - 30
    accepted = await state.verify(fresh, account.recovery[0], ip="192.0.2.222")
    assert accepted.status_code == 200, accepted.text
    assert await state.redis.get(key) is None
