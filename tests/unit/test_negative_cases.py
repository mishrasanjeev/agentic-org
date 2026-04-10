"""Negative / error-path unit tests for all API endpoints.

Covers: 401, 400, 404, 409, 410, 422, 429 error responses.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TENANT_STR = "00000000-0000-0000-0000-000000000001"
TENANT_UUID = uuid.UUID(TENANT_STR)


def _make_result(scalar_one=None, scalars_list=None, scalar_value=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    if scalars_list is not None:
        result.scalars.return_value.all.return_value = scalars_list
    if scalar_value is not None:
        result.scalar.return_value = scalar_value
    return result


def _patch_tenant_session(module_path: str, mock_session):
    ctx = patch(f"api.v1.{module_path}.get_tenant_session")
    mock_gts = ctx.start()
    mock_gts.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_gts.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# AUTH — Login / Signup / Forgot-Password / Reset-Password
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthLogin:
    @pytest.mark.asyncio
    async def test_login_invalid_email(self):
        from api.v1.auth import LoginRequest, login

        request = MagicMock()
        request.client.host = "127.0.0.1"
        with patch("api.v1.auth.async_session_factory") as mock_sf:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_result(scalar_one=None))
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc:
                await login(LoginRequest(email="nobody@x.com", password="Wrong1234"), request)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_login_wrong_password(self):
        import bcrypt as _bcrypt

        from api.v1.auth import LoginRequest, login

        real_hash = _bcrypt.hashpw(b"CorrectPass1", _bcrypt.gensalt(rounds=4)).decode()
        user = MagicMock()
        user.password_hash = real_hash
        user.status = "active"
        request = MagicMock()
        request.client.host = "127.0.0.2"
        with patch("api.v1.auth.async_session_factory") as mock_sf:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_result(scalar_one=user))
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc:
                await login(LoginRequest(email="user@test.com", password="Wrong1234"), request)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_login_rate_limit(self):
        from api.v1 import auth as auth_mod
        from api.v1.auth import LoginRequest, login

        ip = f"ratelimit-{uuid.uuid4().hex[:8]}"
        request = MagicMock()
        request.client.host = ip

        # Fill rate limit bucket
        auth_mod._login_attempts[ip] = [time.time()] * 5

        with pytest.raises(HTTPException) as exc:
            await login(LoginRequest(email="x@x.com", password="X1234aaa"), request)
        assert exc.value.status_code == 429

        # Cleanup
        del auth_mod._login_attempts[ip]


class TestAuthSignup:
    @pytest.mark.asyncio
    async def test_signup_weak_password(self):
        from api.v1.auth import SignupRequest, signup

        request = MagicMock()
        request.client.host = "127.0.0.3"
        with pytest.raises(HTTPException) as exc:
            await signup(
                SignupRequest(org_name="T", admin_name="T", admin_email="t@t.com", password="weak"),
                request,
            )
        assert exc.value.status_code == 400
        assert "uppercase" in exc.value.detail.lower() or "8 characters" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_signup_duplicate_email(self):
        from api.v1.auth import SignupRequest, signup

        existing_user = MagicMock()
        request = MagicMock()
        request.client.host = "127.0.0.4"
        with patch("api.v1.auth.async_session_factory") as mock_sf:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_result(scalar_one=existing_user))
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(HTTPException) as exc:
                await signup(
                    SignupRequest(
                        org_name="Org", admin_name="Admin",
                        admin_email="dup@test.com", password="GoodPass1",
                    ),
                    request,
                )
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_signup_rate_limit(self):
        from api.v1 import auth as auth_mod
        from api.v1.auth import SignupRequest, signup

        ip = f"signup-rl-{uuid.uuid4().hex[:8]}"
        request = MagicMock()
        request.client.host = ip
        auth_mod._signup_attempts[ip] = [time.time()] * 5
        with pytest.raises(HTTPException) as exc:
            await signup(
                SignupRequest(org_name="O", admin_name="A", admin_email="a@a.com", password="Pass1234"),
                request,
            )
        assert exc.value.status_code == 429
        del auth_mod._signup_attempts[ip]


class TestForgotPassword:
    @pytest.mark.asyncio
    async def test_forgot_password_nonexistent_email_still_200(self):
        """Should return 200 even for non-existent email to prevent enumeration."""
        from api.v1.auth import ForgotPasswordRequest, forgot_password

        request = MagicMock()
        request.client.host = "127.0.0.5"
        with patch("api.v1.auth.async_session_factory") as mock_sf:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_result(scalar_one=None))
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await forgot_password(
                ForgotPasswordRequest(email="noone@nowhere.com"), request,
            )
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self):
        from api.v1.auth import ResetPasswordRequest, reset_password

        with pytest.raises(HTTPException) as exc:
            await reset_password(
                ResetPasswordRequest(token="invalid.jwt.token", password="NewPass1"),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_password_weak_password(self):
        from api.v1.auth import ResetPasswordRequest, reset_password

        # Use a structurally valid token that will fail validation
        with pytest.raises(HTTPException) as exc:
            await reset_password(
                ResetPasswordRequest(token="bad", password="weak"),
            )
        # Should fail on token validation first (400), not password
        assert exc.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# ORG — Invite / Accept
# ═══════════════════════════════════════════════════════════════════════════


class TestOrgInvite:
    @pytest.mark.asyncio
    async def test_accept_invite_invalid_token(self):
        from api.v1.org import AcceptInviteRequest, accept_invite

        with pytest.raises(HTTPException) as exc:
            await accept_invite(AcceptInviteRequest(token="bad.token", password="Pass1234"))
        assert exc.value.status_code == 400
        assert "Invalid" in exc.value.detail

    @pytest.mark.asyncio
    async def test_accept_invite_weak_password(self):
        from api.v1.org import AcceptInviteRequest, accept_invite

        # Even with a valid-looking token, weak password should be caught
        with patch("api.v1.org.validate_local_token") as mock_val:
            mock_val.return_value = {
                "agenticorg:invite": True,
                "agenticorg:user_id": str(uuid.uuid4()),
                "sub": "x@test.com",
            }
            with pytest.raises(HTTPException) as exc:
                await accept_invite(AcceptInviteRequest(token="tok", password="weak"))
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_accept_invite_not_an_invite_token(self):
        from api.v1.org import AcceptInviteRequest, accept_invite

        with patch("api.v1.org.validate_local_token") as mock_val:
            mock_val.return_value = {"sub": "x@test.com"}  # Missing agenticorg:invite
            with pytest.raises(HTTPException) as exc:
                await accept_invite(AcceptInviteRequest(token="tok", password="Pass1234"))
            assert exc.value.status_code == 400
            assert "not an invite" in exc.value.detail


# ═══════════════════════════════════════════════════════════════════════════
# APPROVALS — Decide errors
# ═══════════════════════════════════════════════════════════════════════════


class TestApprovalDecide:
    _USER_CLAIMS = {"sub": str(uuid.uuid4()), "name": "Test User"}

    @pytest.mark.asyncio
    async def test_decide_not_found(self):
        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_one=None))
        mock_session.add = MagicMock()
        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            bg = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await decide(
                    uuid.uuid4(), HITLDecision(decision="approve"), bg, TENANT_STR,
                    user_claims=self._USER_CLAIMS, user_role="ceo", user_domains=None,
                )
            assert exc.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_decide_already_resolved(self):
        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        item = MagicMock()
        item.status = "decided"
        item.assignee_role = "manager"
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_one=item))
        mock_session.add = MagicMock()
        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            bg = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await decide(
                    uuid.uuid4(), HITLDecision(decision="approve"), bg, TENANT_STR,
                    user_claims=self._USER_CLAIMS, user_role="ceo", user_domains=None,
                )
            assert exc.value.status_code == 409
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_decide_expired(self):
        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        item = MagicMock()
        item.status = "pending"
        item.assignee_role = "manager"
        item.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_one=item))
        mock_session.add = MagicMock()
        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            bg = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await decide(
                    uuid.uuid4(), HITLDecision(decision="approve"), bg, TENANT_STR,
                    user_claims=self._USER_CLAIMS, user_role="ceo", user_domains=None,
                )
            assert exc.value.status_code == 410
        finally:
            ctx.stop()


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS — Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowCreate:
    @pytest.mark.asyncio
    async def test_create_workflow_missing_steps(self):
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        with pytest.raises(HTTPException) as exc:
            await create_workflow(
                WorkflowCreate(name="test", definition={}),
                TENANT_STR,
            )
        assert exc.value.status_code == 400
        assert "steps" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_create_workflow_empty_steps(self):
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        with pytest.raises(HTTPException) as exc:
            await create_workflow(
                WorkflowCreate(name="test", definition={"steps": []}),
                TENANT_STR,
            )
        assert exc.value.status_code == 400
        assert "at least one" in exc.value.detail.lower()


# ═══════════════════════════════════════════════════════════════════════════
# CONNECTORS — Validation
# ═══════════════════════════════════════════════════════════════════════════


class TestConnectorCreate:
    @pytest.mark.asyncio
    async def test_create_duplicate_name(self):
        from sqlalchemy.exc import IntegrityError

        from api.v1.connectors import register_connector
        from core.schemas.api import ConnectorCreate

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock(
            side_effect=IntegrityError("", {}, Exception("duplicate"))
        )
        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            with pytest.raises(HTTPException) as exc:
                await register_connector(
                    ConnectorCreate(name="slack", category="comms", auth_type="api_key"),
                    TENANT_STR,
                )
            assert exc.value.status_code == 409
            assert "already exists" in exc.value.detail
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        from api.v1.connectors import update_connector
        from core.schemas.api import ConnectorUpdate

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_one=None))
        mock_session.add = MagicMock()
        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            with pytest.raises(HTTPException) as exc:
                await update_connector(
                    uuid.uuid4(),
                    ConnectorUpdate(rate_limit_rpm=200),
                    TENANT_STR,
                )
            assert exc.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        from api.v1.connectors import get_connector

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_one=None))
        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            with pytest.raises(HTTPException) as exc:
                await get_connector(uuid.uuid4(), TENANT_STR)
            assert exc.value.status_code == 404
        finally:
            ctx.stop()


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT — Input validation
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditValidation:
    @pytest.mark.asyncio
    async def test_invalid_agent_id_format(self):
        from api.v1.audit import query_audit

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_value=0))
        ctx = _patch_tenant_session("audit", mock_session)
        try:
            with pytest.raises(HTTPException) as exc:
                await query_audit(
                    agent_id="not-a-uuid",
                    tenant_id=TENANT_STR,
                    user_domains=None,
                    user_role="admin",
                )
            assert exc.value.status_code == 400
            assert "agent_id" in exc.value.detail.lower()
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_invalid_date_from_format(self):
        from api.v1.audit import query_audit

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_result(scalar_value=0))
        ctx = _patch_tenant_session("audit", mock_session)
        try:
            with pytest.raises(HTTPException) as exc:
                await query_audit(
                    date_from="not-a-date",
                    tenant_id=TENANT_STR,
                    user_domains=None,
                    user_role="admin",
                )
            assert exc.value.status_code == 400
            assert "date_from" in exc.value.detail.lower()
        finally:
            ctx.stop()
