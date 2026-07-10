"""Behavior regressions for defects found in the 2026-07-10 project audit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def test_production_ui_and_deploy_do_not_embed_demo_admin_credentials() -> None:
    email = "ceo@" + "agenticorg.local"
    password = "ceo" + "123!"
    sources = [
        ROOT / "ui" / "src" / "pages" / "Login.tsx",
        ROOT / "ui" / "src" / "pages" / "Playground.tsx",
        ROOT / ".github" / "workflows" / "deploy.yml",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert email not in text, source
        assert password not in text, source


@pytest.mark.asyncio
async def test_blacklist_waits_for_redis_and_fails_closed() -> None:
    from auth import jwt as jwt_module

    redis = MagicMock()
    redis.setex = AsyncMock(side_effect=OSError("redis unavailable"))

    with (
        patch.object(jwt_module, "_get_redis", return_value=redis),
        patch.object(jwt_module, "_auth_state_strict", return_value=True),
        pytest.raises(RuntimeError, match="Redis write failed"),
    ):
        await jwt_module.blacklist_token("signed-token")

    redis.setex.assert_awaited_once()


def test_password_reset_rejects_legacy_raw_jwt_input() -> None:
    from api.v1.auth import ResetPasswordRequest

    with pytest.raises(ValidationError):
        ResetPasswordRequest.model_validate(
            {"token": "legacy-reset-jwt", "password": "NewPassword1"}
        )


@pytest.mark.asyncio
async def test_password_reset_code_is_one_shot_and_tenant_bound() -> None:
    from api.v1 import auth as auth_module
    from auth.jwt import create_access_token

    tenant_id = uuid.uuid4()
    reset_token = create_access_token(
        {
            "sub": "member@example.com",
            "agenticorg:tenant_id": str(tenant_id),
            "agenticorg:reset": True,
        }
    )
    user = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="member@example.com",
        status="active",
        password_hash="old",
    )
    session = MagicMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=db_result)
    session.commit = AsyncMock()
    session.add = MagicMock()
    seen_tenants: list[uuid.UUID] = []

    @asynccontextmanager
    async def tenant_session(value: uuid.UUID):
        seen_tenants.append(value)
        yield session

    consume = AsyncMock(side_effect=[reset_token, None])
    with (
        patch.object(auth_module, "consume_code", consume),
        patch.object(auth_module, "get_tenant_session", tenant_session),
    ):
        result = await auth_module.reset_password(
            auth_module.ResetPasswordRequest(code="one-time-code", password="NewPassword1")
        )
        with pytest.raises(HTTPException) as replay:
            await auth_module.reset_password(
                auth_module.ResetPasswordRequest(code="one-time-code", password="OtherPassword2")
            )

    assert result["status"] == "ok"
    assert replay.value.status_code == 400
    assert seen_tenants == [tenant_id]


@pytest.mark.asyncio
async def test_logout_revokes_the_middleware_selected_credential() -> None:
    from api.v1 import auth as auth_module

    request = MagicMock()
    request.state = SimpleNamespace(auth_token="explicit-bearer-token")
    request.cookies = {"agenticorg_session": "ambient-cookie-token"}
    response = Response()

    with patch.object(auth_module, "blacklist_token", new=AsyncMock()) as blacklist:
        result = await auth_module.logout(request, response)

    assert result == {"status": "logged_out"}
    blacklist.assert_awaited_once_with("explicit-bearer-token")


@pytest.mark.asyncio
async def test_logout_does_not_claim_success_when_revocation_fails() -> None:
    from api.v1 import auth as auth_module

    request = MagicMock()
    request.state = SimpleNamespace(auth_token="selected-token")
    response = Response()

    with patch.object(
        auth_module,
        "blacklist_token",
        new=AsyncMock(side_effect=OSError("redis write failed")),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_module.logout(request, response)

    assert exc.value.status_code == 503
    assert not any(name.lower() == b"set-cookie" for name, _ in response.raw_headers)


@pytest.mark.asyncio
async def test_auth_me_uses_verified_tenant_context_not_ambient_cookie() -> None:
    from api.v1 import auth as auth_module

    tenant_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="member@example.com",
        name="Member",
        role="analyst",
        domain="finance",
        mfa_enabled=False,
    )
    tenant = SimpleNamespace(
        id=tenant_id,
        settings={"onboarding_complete": True},
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    tenant_result = MagicMock()
    tenant_result.scalar_one_or_none.return_value = tenant
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[user_result, tenant_result])
    seen_tenants: list[uuid.UUID] = []

    @asynccontextmanager
    async def tenant_session(value: uuid.UUID):
        seen_tenants.append(value)
        yield session

    request = MagicMock()
    request.state = SimpleNamespace(
        tenant_id=str(tenant_id),
        claims={
            "sub": user.email,
            "agenticorg:tenant_id": str(tenant_id),
            "agenticorg:user_id": str(user.id),
        },
    )
    request.cookies = {"agenticorg_session": "different-tenant-cookie"}

    with patch.object(auth_module, "get_tenant_session", tenant_session):
        profile = await auth_module.get_current_user_profile(request)

    assert profile["tenant_id"] == str(tenant_id)
    assert profile["email"] == user.email
    assert profile["onboarding_complete"] is True
    assert seen_tenants == [tenant_id]


@pytest.mark.asyncio
async def test_oidc_callback_sets_cookie_session_without_bearer_fragment() -> None:
    from api.v1 import sso as sso_module

    tenant_id = uuid.uuid4()
    state = "state-value"
    redis = MagicMock()
    redis.get = AsyncMock(
        return_value=json.dumps(
            {
                "tenant_id": str(tenant_id),
                "nonce": "nonce",
                "verifier": "verifier",
                "return_to": "/dashboard",
            }
        ).encode()
    )
    redis.delete = AsyncMock()
    provider = MagicMock()
    provider.exchange_code = AsyncMock(
        return_value=SimpleNamespace(claims={"sub": "member@example.com"})
    )
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="member@example.com",
        name="Member",
        role="admin",
        domain="finance",
    )
    tenant_result = MagicMock()
    tenant_result.scalar_one.return_value = SimpleNamespace(name="Tenant A")
    session = MagicMock()
    session.execute = AsyncMock(return_value=tenant_result)

    @asynccontextmanager
    async def tenant_session(value: uuid.UUID):
        assert value == tenant_id
        yield session

    with (
        patch.object(sso_module, "_redis", new=AsyncMock(return_value=redis)),
        patch.object(
            sso_module,
            "_load_provider",
            new=AsyncMock(return_value=(provider, MagicMock())),
        ),
        patch.object(
            sso_module,
            "jit_provision_user",
            new=AsyncMock(return_value=user),
        ),
        patch.object(sso_module, "get_tenant_session", tenant_session),
    ):
        response = await sso_module.sso_callback(
            provider_key="oidc",
            code="provider-code",
            state=state,
        )

    location = response.headers["location"]
    cookies = [
        value.decode()
        for name, value in response.raw_headers
        if name.lower() == b"set-cookie"
    ]
    assert location.endswith("/dashboard")
    assert "#token=" not in location
    assert any(cookie.startswith("agenticorg_session=") for cookie in cookies)
    assert any(cookie.startswith("agenticorg_csrf=") for cookie in cookies)


def test_valid_old_stripe_event_is_processed_on_retry() -> None:
    from core.billing import stripe_client

    event = {
        "id": "evt_old_retry",
        "created": 1,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"tenant_id": "tenant-a", "plan": "pro"},
                "payment_status": "paid",
                "subscription": "sub-1",
                "customer": "cus-1",
            }
        },
    }
    stripe = SimpleNamespace(
        Webhook=SimpleNamespace(construct_event=lambda *_args: event)
    )

    with (
        patch.object(stripe_client, "_get_stripe", return_value=stripe),
        patch.object(stripe_client, "_activate_subscription") as activate,
    ):
        result = stripe_client.handle_webhook(b"{}", "valid-signature")

    assert result["processed"] is True
    activate.assert_called_once_with(
        tenant_id="tenant-a",
        plan="pro",
        subscription_id="sub-1",
        customer_id="cus-1",
    )


@pytest.mark.parametrize("script", ["qa_audit.py", "qa_matrix.py"])
def test_qa_cli_help_is_console_encoding_safe(script: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and repo script
        [sys.executable, str(ROOT / "scripts" / script), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
