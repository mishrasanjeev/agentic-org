"""Regression tests for the 2026-09-12 auth/tenancy audit findings.

Each test replays the audited symptom and pins the fail-closed behavior:

1. core.rbac.get_allowed_domains — unmapped / invitable roles never resolve to
   "all domains" (None).
2. auth.sso.provisioning.jit_provision_user — inactive members cannot re-login
   via IdP.
3. api.v1.auth._auth_state_strict — honours strict runtime env, not only the
   env-var override.
4. forgot_password throttle — Redis-backed, keyed by IP and email, fails closed.
5. api.error_handlers — ValueError detail is not echoed; HTTPException(404,
   detail=...) bodies pass through.
6. api.v1.chat — history bucket is user-scoped and company is tenant-validated.
7. api.v1.companies — filing approve/reject lock the row.
8. logout with an API key returns 400 instead of a fake logged_out.
9. public status page does not hard-code "operational" for unprobed services.
10. /demo-request — no request-time DDL, per-IP throttle, follow-ups off path.
11. auth middleware — dead path_params "tenant mismatch" check removed.
"""

from __future__ import annotations

import inspect
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. RBAC fail-closed
# ---------------------------------------------------------------------------


class TestRbacFailClosed:
    def test_unknown_role_gets_no_domains_not_all(self):
        from core.rbac import get_allowed_domains

        assert get_allowed_domains("totally_unknown") == []
        assert get_allowed_domains("") == []

    @pytest.mark.parametrize("role", ["domain_lead", "analyst", "developer"])
    def test_invitable_roles_derive_from_user_domain(self, role):
        from core.rbac import ROLE_DOMAIN_MAP, ROLE_LABELS, ROLE_SCOPES, get_allowed_domains

        assert role in ROLE_DOMAIN_MAP
        assert role in ROLE_SCOPES and ROLE_SCOPES[role]
        assert role in ROLE_LABELS
        assert get_allowed_domains(role, "finance") == ["finance"]
        assert get_allowed_domains(role, None) == []
        assert get_allowed_domains(role, "  ") == []
        assert get_allowed_domains(role) is not None

    def test_admin_and_auditor_remain_unrestricted(self):
        from core.rbac import get_allowed_domains

        assert get_allowed_domains("admin") is None
        assert get_allowed_domains("auditor") is None
        assert get_allowed_domains("cfo") == ["finance"]

    def test_all_jwt_minting_sites_pass_user_domain(self):
        for rel in ("api/v1/auth.py", "api/v1/org.py", "api/v1/sso.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            calls = re.findall(r"get_allowed_domains\(([^)]*)\)", src)
            assert calls, rel
            for args in calls:
                assert "user.domain" in args, f"{rel}: get_allowed_domains({args})"


# ---------------------------------------------------------------------------
# 2. SSO JIT provisioning rejects inactive members
# ---------------------------------------------------------------------------


class TestSsoInactiveUserRejected:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["deactivated", "suspended", "pending", "invited"])
    async def test_existing_inactive_user_raises(self, status):
        from auth.sso.provisioning import jit_provision_user

        existing = SimpleNamespace(id=uuid.uuid4(), status=status, email="u@example.com")
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result)
        with patch("auth.sso.provisioning.async_session_factory") as sf:
            sf.return_value.__aenter__ = AsyncMock(return_value=session)
            sf.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(ValueError, match="not active"):
                await jit_provision_user(uuid.uuid4(), "okta", {"email": "u@example.com"})

    @pytest.mark.asyncio
    async def test_existing_active_user_returned(self):
        from auth.sso.provisioning import jit_provision_user

        existing = SimpleNamespace(id=uuid.uuid4(), status="active", email="u@example.com")
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result)
        with patch("auth.sso.provisioning.async_session_factory") as sf:
            sf.return_value.__aenter__ = AsyncMock(return_value=session)
            sf.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await jit_provision_user(uuid.uuid4(), "okta", {"email": "U@example.com"}) is existing

    def test_sso_callback_maps_value_error_to_403(self):
        from api.v1 import sso

        src = inspect.getsource(sso.sso_callback)
        assert "except ValueError" in src
        assert "HTTPException(403" in src


# ---------------------------------------------------------------------------
# 3. _auth_state_strict honours strict runtime env
# ---------------------------------------------------------------------------


class TestAuthStateStrictAlignment:
    def test_production_env_is_strict_without_override(self, monkeypatch):
        from api.v1 import auth as auth_mod

        monkeypatch.delenv("AGENTICORG_AUTH_STATE_STRICT", raising=False)
        with patch.object(auth_mod, "settings", SimpleNamespace(env="production")):
            assert auth_mod._auth_state_strict() is True
        with patch.object(auth_mod, "settings", SimpleNamespace(env="development")):
            assert auth_mod._auth_state_strict() is False

    def test_env_override_still_wins(self, monkeypatch):
        from api.v1 import auth as auth_mod

        monkeypatch.setenv("AGENTICORG_AUTH_STATE_STRICT", "1")
        with patch.object(auth_mod, "settings", SimpleNamespace(env="development")):
            assert auth_mod._auth_state_strict() is True


# ---------------------------------------------------------------------------
# 4. forgot_password throttle: Redis-backed, IP + email, fails closed
# ---------------------------------------------------------------------------


def _request(ip: str = "203.0.113.7") -> MagicMock:
    req = MagicMock()
    req.client.host = ip
    return req


class TestForgotPasswordThrottle:
    def test_per_process_dict_removed(self):
        from api.v1 import auth as auth_mod

        assert not hasattr(auth_mod, "_reset_attempts")
        src = inspect.getsource(auth_mod.forgot_password)
        assert "check_window_rate" in src

    @pytest.mark.asyncio
    async def test_throttle_keys_include_ip_and_hashed_email(self):
        from api.v1.auth import ForgotPasswordRequest, forgot_password

        calls: list[tuple] = []

        async def fake_rate(namespace, key, limit, window):
            calls.append((namespace, key, limit, window))
            return False

        with (
            patch("api.v1.auth.auth_state.check_window_rate", side_effect=fake_rate),
            patch("api.v1.auth.async_session_factory") as sf,
        ):
            session = AsyncMock()
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            session.execute = AsyncMock(return_value=result)
            sf.return_value.__aenter__ = AsyncMock(return_value=session)
            sf.return_value.__aexit__ = AsyncMock(return_value=False)
            out = await forgot_password(ForgotPasswordRequest(email="Someone@Example.com"), _request("203.0.113.7"))
        assert out["status"] == "ok"
        namespaces = {c[0] for c in calls}
        assert namespaces == {"reset_ip", "reset_email"}
        ip_call = next(c for c in calls if c[0] == "reset_ip")
        email_call = next(c for c in calls if c[0] == "reset_email")
        assert ip_call[1] == "203.0.113.7"
        # Email is hashed, never stored raw as a Redis key
        assert "someone@example.com" not in email_call[1]
        assert len(email_call[1]) == 64

    @pytest.mark.asyncio
    async def test_blocked_ip_short_circuits_before_db(self):
        from api.v1.auth import ForgotPasswordRequest, forgot_password

        with (
            patch("api.v1.auth.auth_state.check_window_rate", new=AsyncMock(return_value=True)),
            patch("api.v1.auth.async_session_factory") as sf,
        ):
            out = await forgot_password(ForgotPasswordRequest(email="x@example.com"), _request())
        assert out["status"] == "ok"  # enumeration-safe
        sf.assert_not_called()

    @pytest.mark.asyncio
    async def test_strict_redis_failure_returns_503(self):
        from api.v1.auth import ForgotPasswordRequest, forgot_password

        with (
            patch("api.v1.auth.auth_state.check_window_rate", new=AsyncMock(side_effect=RuntimeError("no redis"))),
            pytest.raises(HTTPException) as exc,
        ):
            await forgot_password(ForgotPasswordRequest(email="x@example.com"), _request())
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_check_window_rate_memory_fallback_counts(self, monkeypatch):
        from core import auth_state

        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_state, "_strict", lambda: False)
        auth_state._mem_window.clear()
        key = f"k-{uuid.uuid4().hex}"
        assert await auth_state.check_window_rate("t", key, 2, 60) is False
        assert await auth_state.check_window_rate("t", key, 2, 60) is False
        assert await auth_state.check_window_rate("t", key, 2, 60) is True

    @pytest.mark.asyncio
    async def test_check_window_rate_strict_without_redis_raises(self, monkeypatch):
        from core import auth_state

        monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))
        monkeypatch.setattr(auth_state, "_strict", lambda: True)
        with pytest.raises(RuntimeError):
            await auth_state.check_window_rate("t", "k", 2, 60)


# ---------------------------------------------------------------------------
# 5. Error handlers
# ---------------------------------------------------------------------------


def _handlers():
    from api.error_handlers import register_error_handlers

    handlers: dict = {}

    class FakeApp:
        def exception_handler(self, exc_type):
            def deco(fn):
                handlers[exc_type] = fn
                return fn

            return deco

    register_error_handlers(FakeApp())
    return handlers


class TestErrorHandlers:
    @pytest.mark.asyncio
    async def test_value_error_detail_not_echoed(self):
        h = _handlers()
        secret = "postgresql://user:hunter2@db/prod"
        resp = await h[ValueError](MagicMock(), ValueError(secret))
        body = json.loads(resp.body)
        assert resp.status_code == 400
        assert body["error"]["code"] == "E2001"
        assert "hunter2" not in resp.body.decode()
        assert body["error"]["message"] == "Invalid request"

    @pytest.mark.asyncio
    async def test_http_404_with_structured_detail_passes_through(self):
        h = _handlers()
        detail = {"error": "unknown_tool", "name": "foo", "supported_prefix": "agenticorg_"}
        resp = await h[404](MagicMock(), HTTPException(status_code=404, detail=detail))
        body = json.loads(resp.body)
        assert resp.status_code == 404
        assert body == {"detail": detail}

    @pytest.mark.asyncio
    async def test_http_404_with_string_detail_passes_through(self):
        h = _handlers()
        resp = await h[404](MagicMock(), HTTPException(status_code=404, detail="Company not found"))
        assert json.loads(resp.body) == {"detail": "Company not found"}

    @pytest.mark.asyncio
    async def test_bare_router_404_keeps_envelope(self):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        h = _handlers()
        resp = await h[404](MagicMock(), StarletteHTTPException(status_code=404))
        body = json.loads(resp.body)
        assert body["error"]["code"] == "E1005"
        resp2 = await h[404](MagicMock(), Exception("not found"))
        assert json.loads(resp2.body)["error"]["code"] == "E1005"


# ---------------------------------------------------------------------------
# 6. Chat history scoping
# ---------------------------------------------------------------------------


class TestChatHistoryScoping:
    def test_session_key_includes_user(self):
        from api.v1 import chat as chat_mod

        a = chat_mod._session_key("t1", "c1", "a1", "alice@x.io")
        b = chat_mod._session_key("t1", "c1", "a1", "bob@x.io")
        assert a != b
        assert chat_mod._session_key("t1", "c1", "", "alice@x.io") != chat_mod._session_key("t1", "c1", "", "bob@x.io")

    def test_query_and_history_use_user_scoped_key_and_company_check(self):
        from api.v1 import chat as chat_mod

        for fn in (chat_mod.chat_query, chat_mod.chat_history):
            src = inspect.getsource(fn)
            assert "_require_company_for_tenant(" in src, fn.__name__
            assert "_session_user_id(request)" in src, fn.__name__

    @pytest.mark.asyncio
    async def test_history_rejects_foreign_company(self):
        from api.v1 import chat as chat_mod

        req = MagicMock()
        req.state.claims = {"sub": "alice@x.io"}
        with (
            patch(
                "api.v1.agents._require_company_for_tenant",
                new=AsyncMock(side_effect=HTTPException(404, "Company not found")),
            ),
            patch.object(chat_mod, "_load_session", new=AsyncMock(return_value=[{"id": "1"}])) as load,
            pytest.raises(HTTPException) as exc,
        ):
            await chat_mod.chat_history(req, company_id=str(uuid.uuid4()), agent_id="", tenant_id=str(uuid.uuid4()))
        assert exc.value.status_code == 404
        load.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_history_bucket_is_per_user(self):
        from api.v1 import chat as chat_mod

        tenant = str(uuid.uuid4())
        company = uuid.uuid4()
        seen: list[str] = []

        async def fake_load(key):
            seen.append(key)
            return []

        with (
            patch("api.v1.agents._require_company_for_tenant", new=AsyncMock(return_value=company)),
            patch.object(chat_mod, "_load_session", side_effect=fake_load),
        ):
            for who in ("alice@x.io", "bob@x.io"):
                req = MagicMock()
                req.state.claims = {"sub": who}
                await chat_mod.chat_history(req, company_id=str(company), agent_id="a1", tenant_id=tenant)
        assert len(set(seen)) == 2

    def test_missing_principal_fails_closed(self):
        from api.v1 import chat as chat_mod

        req = MagicMock()
        req.state.claims = {}
        req.state.user_sub = ""
        with pytest.raises(HTTPException) as exc:
            chat_mod._session_user_id(req)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# 7. Filing approvals lock the row
# ---------------------------------------------------------------------------


class TestFilingApprovalRowLock:
    @pytest.mark.parametrize("fn_name", ["approve_filing", "reject_filing"])
    def test_pending_read_uses_for_update(self, fn_name):
        from api.v1 import companies

        src = inspect.getsource(getattr(companies, fn_name))
        assert ".with_for_update()" in src
        assert src.index(".with_for_update()") < src.index('approval.status != "pending"')


# ---------------------------------------------------------------------------
# 8. Logout with API key
# ---------------------------------------------------------------------------


class TestLogoutApiKey:
    @pytest.mark.asyncio
    async def test_api_key_logout_returns_400_and_does_not_blacklist(self):
        from api.v1.auth import logout

        req = MagicMock()
        req.state.auth_token = "ao_sk_" + "a" * 40
        req.state.auth_mode = "api_key"
        with patch("api.v1.auth.blacklist_token", new=AsyncMock()) as bl, pytest.raises(HTTPException) as exc:
            await logout(req, MagicMock())
        assert exc.value.status_code == 400
        bl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_jwt_logout_still_blacklists(self):
        from api.v1.auth import logout

        req = MagicMock()
        req.state.auth_token = "eyJ.jwt.token"
        req.state.auth_mode = "legacy"
        with patch("api.v1.auth.blacklist_token", new=AsyncMock()) as bl:
            out = await logout(req, MagicMock())
        assert out == {"status": "logged_out"}
        bl.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. Public status page honesty
# ---------------------------------------------------------------------------


class TestPublicStatusHonesty:
    @pytest.mark.asyncio
    async def test_unprobed_services_are_unknown(self):
        from api.v1 import status as status_mod

        with (
            patch.object(status_mod, "_check_db", new=AsyncMock(return_value=True)),
            patch.object(status_mod, "_check_redis", new=AsyncMock(return_value=True)),
            patch.object(status_mod, "_load_incidents", return_value=[]),
        ):
            out = await status_mod.public_status()
        by_name = {s.name: s.status for s in out.services}
        assert by_name["Scheduled jobs (Celery)"] == "unknown"
        assert by_name["LLM routing"] == "unknown"
        assert by_name["Database"] == "operational"
        assert out.overall == "operational"


# ---------------------------------------------------------------------------
# 10. Public demo request hardening
# ---------------------------------------------------------------------------


class TestDemoRequestHardening:
    def test_no_request_time_ddl_and_migration_exists(self):
        from api.v1 import demo

        src = inspect.getsource(demo.submit_demo_request)
        assert "CREATE TABLE" not in src
        mig = REPO / "migrations" / "versions" / "v6_z14_demo_requests.py"
        assert mig.exists()
        text = mig.read_text(encoding="utf-8")
        rev = re.search(r'^revision = "([^"]+)"', text, re.M).group(1)
        assert len(rev) <= 32
        assert "demo_requests" in text

    def test_sales_agent_and_email_not_awaited_inline(self):
        from api.v1 import demo

        src = inspect.getsource(demo.submit_demo_request)
        assert "_run_sales_agent_on_lead" not in src
        assert "asyncio.to_thread" not in src
        assert "background_tasks.add_task(_demo_request_followups" in src

    @pytest.mark.asyncio
    async def test_throttled_ip_gets_429_before_db(self):
        from api.v1.demo import DemoRequest, submit_demo_request

        body = DemoRequest(name="A", email="a@example.com")
        with (
            patch("api.v1.demo.auth_state.check_window_rate", new=AsyncMock(return_value=True)),
            patch("api.v1.demo.async_session_factory") as sf,
            pytest.raises(HTTPException) as exc,
        ):
            await submit_demo_request(body, _request("198.51.100.9"), MagicMock())
        assert exc.value.status_code == 429
        sf.assert_not_called()

    @pytest.mark.asyncio
    async def test_strict_redis_failure_is_503(self):
        from api.v1.demo import DemoRequest, submit_demo_request

        body = DemoRequest(name="A", email="a@example.com")
        with (
            patch("api.v1.demo.auth_state.check_window_rate", new=AsyncMock(side_effect=RuntimeError("no redis"))),
            pytest.raises(HTTPException) as exc,
        ):
            await submit_demo_request(body, _request(), MagicMock())
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_accepted_request_schedules_followups_and_returns_immediately(self):
        from api.v1 import demo
        from api.v1.demo import DemoRequest, submit_demo_request

        body = DemoRequest(name="A", email="a@example.com", firm="Acme")
        session = AsyncMock()
        row = MagicMock()
        row.fetchone.return_value = None
        session.execute = AsyncMock(return_value=row)
        bg = MagicMock()
        with (
            patch("api.v1.demo.auth_state.check_window_rate", new=AsyncMock(return_value=False)),
            patch("api.v1.demo.async_session_factory") as sf,
            patch.object(demo, "_send_email_notification") as notify,
            patch.object(demo, "_send_trial_confirmation") as confirm,
        ):
            sf.return_value.__aenter__ = AsyncMock(return_value=session)
            sf.return_value.__aexit__ = AsyncMock(return_value=False)
            out = await submit_demo_request(body, _request(), bg)
        assert out["status"] == "received"
        assert out["lead_id"]
        bg.add_task.assert_called_once()
        assert bg.add_task.call_args.args[0] is demo._demo_request_followups
        notify.assert_not_called()
        confirm.assert_not_called()
        ddl = [c.args[0].text for c in session.execute.call_args_list if hasattr(c.args[0], "text")]
        assert not any("CREATE TABLE" in t for t in ddl)


# ---------------------------------------------------------------------------
# 11. Dead tenant-mismatch check removed from middleware
# ---------------------------------------------------------------------------


class TestMiddlewareDeadTenantCheckRemoved:
    @pytest.mark.parametrize("rel", ["auth/grantex_middleware.py", "auth/middleware.py"])
    def test_no_path_params_tenant_check(self, rel):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert 'request.path_params.get("tenant_id")' not in src
        assert '"Tenant mismatch"' not in src

    def test_csrf_comment_points_at_mounted_middleware(self):
        src = (REPO / "auth" / "csrf_middleware.py").read_text(encoding="utf-8")
        assert "GrantexAuthMiddleware.EXEMPT_PATHS" in src
