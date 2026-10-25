"""Authenticator-app (TOTP) multi-factor sign-in: codes, enrolment, the login gate and resets."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from auth import totp
from core.crypto.credential_vault import decrypt_credential, encrypt_credential
from core.models.user import User

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        email="owner@example.com",
        name="Owner",
        role="admin",
        domain="all",
        status="active",
        mfa_enabled=False,
        mfa_recovery_codes=[],
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _enrolled(secret: str = RFC_SECRET, **overrides) -> User:
    return _user(
        mfa_enabled=True,
        mfa_totp_secret=encrypt_credential(secret),
        mfa_enabled_at=datetime.now(UTC),
        **overrides,
    )


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = [value] if value is not None else []
    return result


def _session(*values):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_result(v) for v in values])
    session.add = MagicMock()
    return session


def _patch_session(module: str, session):
    ctx = patch(f"{module}.get_tenant_session")
    factory = ctx.start()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _signed_in(user: User):
    claims = {"sub": user.email, "agenticorg:user_id": str(user.id), "role": user.role}
    return SimpleNamespace(state=SimpleNamespace(auth_mode="legacy", claims=claims, tenant_id=str(TENANT)))


def _now_code(secret: str = RFC_SECRET) -> str:
    return totp.code_at(secret, totp.current_step())


# ── RFC 6238 codes ─────────────────────────────────────────────────────────


def test_rfc6238_sha1_vectors_truncated_to_six_digits() -> None:
    # RFC 6238 appendix B (SHA-1), last six of the eight published digits.
    assert totp.code_at(RFC_SECRET, 59 // 30) == "287082"
    assert totp.code_at(RFC_SECRET, 1111111109 // 30) == "081804"
    assert totp.code_at(RFC_SECRET, 2000000000 // 30) == "279037"


def test_verify_accepts_one_step_of_drift_and_refuses_replay() -> None:
    now = 1_800_000_000.0
    step = totp.current_step(now)
    previous = totp.code_at(RFC_SECRET, step - 1)
    assert totp.verify(RFC_SECRET, previous, now=now) == step - 1
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step + 1), now=now) == step + 1
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), now=now) is None
    assert totp.verify(RFC_SECRET, previous, last_used_step=step - 1, now=now) is None
    assert totp.verify(RFC_SECRET, "12345", now=now) is None
    assert totp.verify(RFC_SECRET, "abcdef", now=now) is None
    spaced = totp.code_at(RFC_SECRET, step)
    assert totp.verify(RFC_SECRET, f"{spaced[:3]} {spaced[3:]}", now=now) == step


def test_secret_uri_and_qr_code() -> None:
    secret = totp.generate_secret()
    assert len(secret) == 32 and secret == secret.upper()
    assert totp.format_secret(secret).replace(" ", "") == secret
    uri = totp.provisioning_uri(secret, "owner@example.com")
    assert uri.startswith("otpauth://totp/AgenticOrg%3Aowner%40example.com?")
    assert f"secret={secret}" in uri and "issuer=AgenticOrg" in uri and "digits=6" in uri and "period=30" in uri
    qr = totp.qr_svg_data_uri(uri)
    assert qr.startswith("data:image/svg+xml;base64,")
    assert b"<svg" in base64.b64decode(qr.split(",", 1)[1])


def test_recovery_codes_are_unique_typed_and_user_bound() -> None:
    codes = totp.generate_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(code) == 11 and code[5] == "-" and totp.looks_like_recovery_code(code) for code in codes)
    first = codes[0]
    assert totp.hash_recovery_code("a", first) == totp.hash_recovery_code("a", first.upper().replace("-", " "))
    assert totp.hash_recovery_code("a", first) != totp.hash_recovery_code("b", first)
    assert not totp.looks_like_recovery_code("123456")


# ── Enrolment ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_then_enable_returns_recovery_codes_once() -> None:
    from api.v1 import auth_mfa

    user = _user()
    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        setup = await auth_mfa.mfa_setup(_signed_in(user))
    finally:
        ctx.stop()
    secret = setup["secret"].replace(" ", "")
    assert setup["qr_code"].startswith("data:image/svg+xml;base64,")
    assert f"secret={secret}" in setup["otpauth_uri"]
    assert user.mfa_pending_secret and secret not in user.mfa_pending_secret
    assert decrypt_credential(user.mfa_pending_secret) == secret
    assert user.mfa_enabled is False

    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        enabled = await auth_mfa.mfa_enable(auth_mfa.MfaCodeRequest(code=_now_code(secret)), _signed_in(user))
    finally:
        ctx.stop()
    assert enabled["enabled"] is True and len(enabled["recovery_codes"]) == 10
    assert user.mfa_enabled is True and decrypt_credential(user.mfa_totp_secret) == secret
    assert user.mfa_pending_secret is None and user.mfa_last_totp_step is not None
    stored = set(user.mfa_recovery_codes)
    assert all(code not in stored for code in enabled["recovery_codes"])
    assert {totp.hash_recovery_code(str(user.id), code) for code in enabled["recovery_codes"]} == stored


@pytest.mark.asyncio
async def test_enable_refuses_wrong_or_expired_codes_and_setup_refuses_when_on() -> None:
    from api.v1 import auth_mfa

    secret = totp.generate_secret()
    stale = _user(
        mfa_pending_secret=encrypt_credential(secret), mfa_pending_created_at=datetime.now(UTC) - timedelta(minutes=16)
    )
    ctx = _patch_session("api.v1.auth_mfa", _session(stale))
    try:
        with pytest.raises(HTTPException) as exc:
            await auth_mfa.mfa_enable(auth_mfa.MfaCodeRequest(code=_now_code(secret)), _signed_in(stale))
    finally:
        ctx.stop()
    assert exc.value.status_code == 400 and "expired" in exc.value.detail

    fresh = _user(mfa_pending_secret=encrypt_credential(secret), mfa_pending_created_at=datetime.now(UTC))
    wrong = str((int(_now_code(secret)) + 1) % 1_000_000).zfill(6)
    ctx = _patch_session("api.v1.auth_mfa", _session(fresh))
    try:
        with pytest.raises(HTTPException) as exc:
            await auth_mfa.mfa_enable(auth_mfa.MfaCodeRequest(code=wrong), _signed_in(fresh))
    finally:
        ctx.stop()
    assert exc.value.status_code == 400 and fresh.mfa_enabled is False

    enrolled = _enrolled()
    ctx = _patch_session("api.v1.auth_mfa", _session(enrolled))
    try:
        with pytest.raises(HTTPException) as exc:
            await auth_mfa.mfa_setup(_signed_in(enrolled))
    finally:
        ctx.stop()
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_api_keys_cannot_manage_mfa() -> None:
    from api.v1 import auth_mfa

    request = SimpleNamespace(state=SimpleNamespace(auth_mode="api_key", claims={}, tenant_id=str(TENANT)))
    with pytest.raises(HTTPException) as exc:
        await auth_mfa.mfa_setup(request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_disable_needs_a_current_code_and_clears_everything() -> None:
    from api.v1 import auth_mfa

    user = _enrolled(mfa_recovery_codes=["x"])
    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        with pytest.raises(HTTPException):
            await auth_mfa.mfa_disable(auth_mfa.MfaCodeRequest(code="000000"), _signed_in(user))
    finally:
        ctx.stop()
    assert user.mfa_enabled is True

    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        assert (await auth_mfa.mfa_disable(auth_mfa.MfaCodeRequest(code=_now_code()), _signed_in(user))) == {
            "enabled": False
        }
    finally:
        ctx.stop()
    assert user.mfa_enabled is False and user.mfa_totp_secret is None and user.mfa_recovery_codes == []


# ── The sign-in gate ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_password_login_with_mfa_returns_a_challenge_not_a_session(monkeypatch) -> None:
    import bcrypt as _bcrypt

    from api.v1 import auth as auth_mod

    user = _enrolled(password_hash=_bcrypt.hashpw(b"CorrectPass1", _bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setattr(auth_mod, "_get_throttle_redis", AsyncMock(return_value=None))
    issued = AsyncMock(return_value="challenge-" + "x" * 32)
    monkeypatch.setattr(auth_mod, "issue_code", issued)
    monkeypatch.setattr(auth_mod, "_seed_tenant_defaults_isolated", AsyncMock())
    request = MagicMock()
    request.client.host = f"mfa-{uuid.uuid4().hex[:8]}"
    response = MagicMock()
    with patch("api.v1.auth.get_platform_session") as factory:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[_result(user), _result(SimpleNamespace(settings={}))])
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await auth_mod.login(
            auth_mod.LoginRequest(email=user.email, password="CorrectPass1"), request, response
        )
    assert isinstance(result, auth_mod.MfaChallengeResponse) and result.mfa_required is True
    assert result.mfa_challenge.startswith("challenge-")
    response.set_cookie.assert_not_called()
    kind, payload, ttl = issued.await_args.args
    assert kind == "mfa" and ttl == 300
    assert json.loads(payload) == {"user_id": str(user.id), "tenant_id": str(TENANT), "method": "password"}


def test_legacy_flag_without_a_secret_is_not_enrolment() -> None:
    from api.v1.auth import mfa_enrolled

    assert mfa_enrolled(_user(mfa_enabled=True)) is False
    assert mfa_enrolled(_enrolled()) is True


class _Challenges:
    """In-memory stand-in for the Redis one-time challenge store."""

    def __init__(self, user: User) -> None:
        self.store = {
            "good-challenge-0123456789": json.dumps(
                {"user_id": str(user.id), "tenant_id": str(TENANT), "method": "password"}
            )
        }
        self.attempts: dict[str, int] = {}

    async def peek(self, kind, code):
        return self.store.get(code)

    async def consume(self, kind, code):
        return self.store.pop(code, None)

    async def attempt(self, kind, code, ttl):
        self.attempts[code] = self.attempts.get(code, 0) + 1
        return self.attempts[code]


def _verify_env(monkeypatch, user: User):
    from api.v1 import auth as auth_mod
    from api.v1 import auth_mfa

    store = _Challenges(user)
    monkeypatch.setattr(auth_mod, "_get_throttle_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_mfa, "peek_code", store.peek)
    monkeypatch.setattr(auth_mfa, "consume_code", store.consume)
    monkeypatch.setattr(auth_mfa, "record_attempt", store.attempt)
    return store


def _ip_request():
    request = MagicMock()
    request.client.host = f"mfa-verify-{uuid.uuid4().hex[:8]}"
    return request


@pytest.mark.asyncio
async def test_verify_exchanges_challenge_and_code_for_a_session_once(monkeypatch) -> None:
    from api.v1 import auth_mfa

    user = _enrolled()
    store = _verify_env(monkeypatch, user)
    response = MagicMock()
    ctx = _patch_session("api.v1.auth_mfa", _session(user, SimpleNamespace(settings={"onboarding_complete": True})))
    try:
        result = await auth_mfa.mfa_verify(
            auth_mfa.MfaVerifyRequest(challenge="good-challenge-0123456789", code=_now_code()), _ip_request(), response
        )
    finally:
        ctx.stop()
    assert result.access_token and result.user["email"] == user.email and result.user["onboarding_complete"] is True
    assert any(call.kwargs.get("key") == "agenticorg_session" for call in response.set_cookie.call_args_list)
    assert "good-challenge-0123456789" not in store.store

    with pytest.raises(HTTPException) as exc:
        await auth_mfa.mfa_verify(
            auth_mfa.MfaVerifyRequest(challenge="good-challenge-0123456789", code=_now_code()),
            _ip_request(),
            MagicMock(),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_refuses_a_replayed_code(monkeypatch) -> None:
    from api.v1 import auth_mfa

    user = _enrolled(mfa_last_totp_step=totp.current_step() + 1)
    _verify_env(monkeypatch, user)
    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        with pytest.raises(HTTPException) as exc:
            await auth_mfa.mfa_verify(
                auth_mfa.MfaVerifyRequest(challenge="good-challenge-0123456789", code=_now_code()),
                _ip_request(),
                MagicMock(),
            )
    finally:
        ctx.stop()
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_spends_the_challenge_after_five_wrong_codes(monkeypatch) -> None:
    from api.v1 import auth_mfa

    user = _enrolled()
    store = _verify_env(monkeypatch, user)
    store.attempts["good-challenge-0123456789"] = 5
    with pytest.raises(HTTPException) as exc:
        await auth_mfa.mfa_verify(
            auth_mfa.MfaVerifyRequest(challenge="good-challenge-0123456789", code=_now_code()),
            _ip_request(),
            MagicMock(),
        )
    assert exc.value.status_code == 401 and "Too many" in exc.value.detail
    assert "good-challenge-0123456789" not in store.store


@pytest.mark.asyncio
async def test_a_recovery_code_signs_in_once(monkeypatch) -> None:
    from api.v1 import auth_mfa

    codes = totp.generate_recovery_codes()
    user = _enrolled()
    user.mfa_recovery_codes = [totp.hash_recovery_code(str(user.id), code) for code in codes]
    store = _verify_env(monkeypatch, user)
    ctx = _patch_session("api.v1.auth_mfa", _session(user, SimpleNamespace(settings={})))
    try:
        await auth_mfa.mfa_verify(
            auth_mfa.MfaVerifyRequest(challenge="good-challenge-0123456789", code=codes[0].upper()),
            _ip_request(),
            MagicMock(),
        )
    finally:
        ctx.stop()
    assert len(user.mfa_recovery_codes) == 9

    store.store["second-challenge-0123456789"] = json.dumps(
        {"user_id": str(user.id), "tenant_id": str(TENANT), "method": "password"}
    )
    ctx = _patch_session("api.v1.auth_mfa", _session(user))
    try:
        with pytest.raises(HTTPException):
            await auth_mfa.mfa_verify(
                auth_mfa.MfaVerifyRequest(challenge="second-challenge-0123456789", code=codes[0]),
                _ip_request(),
                MagicMock(),
            )
    finally:
        ctx.stop()
    assert len(user.mfa_recovery_codes) == 9


# ── Admin reset ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_reset_clears_a_member_and_revokes_sessions_but_not_self(monkeypatch) -> None:
    from api.v1 import org

    member = _enrolled(email="member@example.com")
    monkeypatch.setattr(org, "invalidate_user_session_state", AsyncMock())
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(TENANT), user_sub="owner@example.com"))
    before = datetime.now(UTC)
    ctx = _patch_session("api.v1.org", _session(member))
    try:
        result = await org.reset_member_mfa(str(member.id), request)
    finally:
        ctx.stop()
    assert result["status"] == "mfa_reset"
    assert member.mfa_enabled is False and member.mfa_totp_secret is None
    assert member.sessions_invalid_before is not None and member.sessions_invalid_before >= before

    me = _enrolled(email="owner@example.com")
    ctx = _patch_session("api.v1.org", _session(me))
    try:
        with pytest.raises(HTTPException) as exc:
            await org.reset_member_mfa(str(me.id), request)
    finally:
        ctx.stop()
    assert exc.value.status_code == 400 and me.mfa_enabled is True


def test_compliance_evidence_names_the_real_mfa_methods() -> None:
    source = (__import__("pathlib").Path(__file__).resolve().parents[2] / "api" / "v1" / "compliance.py").read_text(
        encoding="utf-8"
    )
    assert '"mfa_methods": ["totp_authenticator_app", "recovery_codes"]' in source
    assert '"min_length": 8' in source


def test_migration_chain_and_columns() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z94_user_totp_mfa.py"
    spec = importlib.util.spec_from_file_location("v6_z94_user_totp_mfa", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.revision == "v6z94_user_totp_mfa" and len(module.revision) <= 32
    assert module.down_revision == "v6z93_custom_domain_firebase"
    source = path.read_text(encoding="utf-8")
    for column in (
        "mfa_totp_secret",
        "mfa_pending_secret",
        "mfa_pending_created_at",
        "mfa_last_totp_step",
        "mfa_recovery_codes",
        "mfa_enabled_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
        assert column in User.__table__.columns
