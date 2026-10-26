# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Fail-closed MFA state tests; persistence boundaries here are deliberately synthetic."""

from __future__ import annotations

import base64
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from api.v1 import auth, auth_mfa
from auth import one_time_codes, totp
from auth.mfa_challenges import MAX_GENERATION, MfaChallengeClaims, challenge_generation
from core.crypto.credential_vault import encrypt_credential
from core.models.user import User

SECRET = base64.b32encode(b"12345678901234567890").decode()
CHALLENGE = "synthetic-pending-challenge-0123456789"


def _claims(**updates):
    return {
        "version": 1,
        "tenant_id": str(uuid.UUID(int=1)),
        "user_id": str(uuid.UUID(int=2)),
        "method": "password",
        "generation": 3,
    } | updates


@pytest.mark.parametrize("value", [None, True, False, -1, "0", 1.0, {}, [], MAX_GENERATION + 1])
def test_persisted_generation_never_defaults_or_coerces(value):
    with pytest.raises(ValueError):
        challenge_generation(value)


@pytest.mark.parametrize("value", [0, 1, MAX_GENERATION])
def test_valid_generations_are_exact(value):
    assert challenge_generation(value) == value


@pytest.mark.parametrize(
    "updates",
    [
        {"version": True},
        {"version": "1"},
        {"version": 0},
        {"version": 2},
        {"generation": True},
        {"generation": "3"},
        {"generation": -1},
        {"generation": MAX_GENERATION + 1},
        {"generation": None},
        {"method": "api_key"},
        {"user_id": "not-a-user"},
        {"tenant_id": None},
        {"extra": "untrusted"},
    ],
)
def test_challenge_schema_refuses_ambiguous_claims(updates):
    with pytest.raises(ValidationError):
        MfaChallengeClaims.model_validate_json(json.dumps(_claims(**updates)))


@pytest.mark.parametrize("missing", ["version", "generation", "user_id", "tenant_id", "method"])
def test_old_or_incomplete_challenges_require_fresh_first_factor(missing):
    claims = _claims()
    del claims[missing]
    with pytest.raises(ValidationError):
        MfaChallengeClaims.model_validate_json(json.dumps(claims))


@pytest.mark.parametrize("generation", [None, True, -1, MAX_GENERATION])
def test_generation_invalidation_refuses_corruption_and_overflow(generation):
    user = SimpleNamespace(mfa_challenge_generation=generation)
    with pytest.raises(HTTPException) as exc:
        auth.invalidate_mfa_challenges(user)
    assert exc.value.status_code == 503
    assert user.mfa_challenge_generation == generation


def test_generation_invalidation_is_monotonic():
    user = SimpleNamespace(mfa_challenge_generation=8)
    auth.invalidate_mfa_challenges(user)
    auth.invalidate_mfa_challenges(user)
    assert user.mfa_challenge_generation == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RedisConnectionError("private-store-location"), OSError("private-store-location")])
async def test_store_errors_are_retryable_without_internal_details(error):
    operation = AsyncMock(side_effect=error)
    with pytest.raises(HTTPException) as exc:
        await auth.mfa_store(operation())
    assert exc.value.status_code == 503
    assert "private-store-location" not in exc.value.detail


@pytest.mark.asyncio
async def test_counter_has_one_atomic_operation_and_closes_client(monkeypatch):
    cache = AsyncMock()
    cache.eval.return_value = 1
    monkeypatch.setattr(one_time_codes, "_redis", lambda: cache)
    assert await one_time_codes.record_mfa_account_attempt(str(uuid.UUID(int=1)), str(uuid.UUID(int=2))) == 1
    cache.eval.assert_awaited_once()
    script, numkeys, key, ttl = cache.eval.await_args.args
    assert script == one_time_codes._ATTEMPT_SCRIPT and numkeys == 1 and ttl == 300
    assert key.startswith("onetime-attempts:mfa-account:")
    cache.incr.assert_not_called()
    cache.expire.assert_not_called()
    cache.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, "1", True, 0, -1])
async def test_counter_refuses_malformed_store_result(monkeypatch, result):
    cache = AsyncMock()
    cache.eval.return_value = result
    monkeypatch.setattr(one_time_codes, "_redis", lambda: cache)
    with pytest.raises(HTTPException) as exc:
        await auth.mfa_store(one_time_codes.record_attempt("mfa", CHALLENGE, 300))
    assert exc.value.status_code == 503
    cache.aclose.assert_awaited_once()


@pytest.fixture
def verification(monkeypatch):
    user = User(
        id=uuid.UUID(int=2),
        tenant_id=uuid.UUID(int=1),
        email="member@example.test",
        name="Member",
        role="member",
        domain="all",
        status="active",
        mfa_enabled=True,
        mfa_totp_secret=encrypt_credential(SECRET),
        mfa_challenge_generation=3,
        mfa_recovery_codes=[],
        mfa_last_totp_step=None,
    )
    session = AsyncMock()
    session.add = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.scalar_one_or_none.return_value = user
    second.scalar_one_or_none.return_value = SimpleNamespace(settings={})
    session.execute.side_effect = [first, second]
    events = []

    @asynccontextmanager
    async def tenant_session(tenant_id):
        assert tenant_id == user.tenant_id
        events.append("lock-context")
        yield session
        events.append("release-context")

    monkeypatch.setattr(auth_mfa, "get_tenant_session", tenant_session)
    monkeypatch.setattr(auth_mfa, "_check_rate_limit", AsyncMock(return_value=False))
    monkeypatch.setattr(auth_mfa, "_clear_rate_limit", AsyncMock())
    peek = AsyncMock(return_value=json.dumps(_claims()))
    consume = AsyncMock(return_value=peek.return_value)
    budget = AsyncMock(return_value=1)
    clear = AsyncMock()
    monkeypatch.setattr(auth_mfa, "peek_code", peek)
    monkeypatch.setattr(auth_mfa, "consume_code", consume)
    monkeypatch.setattr(auth_mfa, "record_attempt", AsyncMock(return_value=1))
    monkeypatch.setattr(auth_mfa, "record_mfa_account_attempt", budget)
    monkeypatch.setattr(auth_mfa, "clear_mfa_account_attempts", clear)
    app = FastAPI()
    app.include_router(auth_mfa.router, prefix="/api/v1")
    return SimpleNamespace(
        user=user, session=session, peek=peek, consume=consume, budget=budget, clear=clear, app=app, events=events
    )


async def _verify(state, code=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app, raise_app_exceptions=False), base_url="http://testserver"
    ) as client:
        return await client.post(
            "/api/v1/auth/mfa/verify",
            json={
                "challenge": CHALLENGE,
                "code": code or totp.code_at(SECRET, totp.current_step()),
            },
        )


def _no_session(response, status):
    assert response.status_code == status, response.text
    assert "set-cookie" not in response.headers
    assert "access_token" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", ["[]", "null", "{}", "not-json", json.dumps(_claims(generation=2)), json.dumps(_claims(generation=4))]
)
async def test_invalid_or_stale_challenges_never_check_a_factor(verification, monkeypatch, payload):
    state = verification
    state.peek.return_value = payload
    factor = MagicMock()
    monkeypatch.setattr(auth_mfa, "check_second_factor", factor)
    _no_session(await _verify(state), 401)
    factor.assert_not_called()
    state.budget.assert_not_awaited()
    state.consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_limit_blocks_even_a_correct_factor(verification, monkeypatch):
    state = verification
    state.budget.return_value = 6
    factor = MagicMock()
    monkeypatch.setattr(auth_mfa, "check_second_factor", factor)
    _no_session(await _verify(state), 429)
    factor.assert_not_called()
    state.clear.assert_not_awaited()
    state.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["peek", "budget", "consume", "clear"])
async def test_store_failure_never_delivers_a_session(verification, boundary):
    state = verification
    getattr(state, boundary).side_effect = RedisConnectionError("private-store-location")
    response = await _verify(state)
    _no_session(response, 503)
    assert "private-store-location" not in response.text
    state.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumed", [None, "different-payload"])
async def test_consumption_must_match_the_exact_peeked_challenge(verification, consumed):
    state = verification
    state.consume.return_value = consumed
    _no_session(await _verify(state), 401)
    state.clear.assert_not_awaited()
    state.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_failure_does_not_deliver_prepared_cookie(verification):
    state = verification
    state.session.commit.side_effect = OSError("synthetic-db-failure")
    _no_session(await _verify(state), 500)


@pytest.mark.asyncio
async def test_session_is_minted_before_commit_and_context_release(verification, monkeypatch):
    state = verification
    original = auth_mfa.start_session

    def mint(*args):
        assert state.events == ["lock-context"]
        state.session.commit.assert_not_awaited()
        query = state.session.execute.await_args_list[0].args[0]
        assert query._for_update_arg is not None
        state.events.append("mint")
        return original(*args)

    monkeypatch.setattr(auth_mfa, "start_session", mint)
    response = await _verify(state)
    assert response.status_code == 200, response.text
    assert "agenticorg_session=" in response.headers.get("set-cookie", "")
    assert state.events == ["lock-context", "mint", "release-context"]
    state.session.commit.assert_awaited_once()
    state.clear.assert_awaited_once_with(str(state.user.tenant_id), str(state.user.id))


@pytest.mark.asyncio
async def test_atomic_script_errors_close_without_fallback(monkeypatch):
    cache = AsyncMock()
    cache.eval.side_effect = ResponseError("synthetic-counter-without-ttl")
    monkeypatch.setattr(one_time_codes, "_redis", lambda: cache)
    with pytest.raises(HTTPException) as exc:
        await auth.mfa_store(one_time_codes.record_mfa_account_attempt(str(uuid.UUID(int=1)), str(uuid.UUID(int=2))))
    assert exc.value.status_code == 503
    cache.aclose.assert_awaited_once()
