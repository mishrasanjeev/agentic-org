"""route_meta(scope=, rate_limit=) is enforced at runtime (audit C13).

Before 2026-09-13 the decorator only attached metadata; nothing read it at
request time. These tests pin that a declared rate-limit class throttles and a
declared scope family denies callers whose session lacks the RBAC scope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.route_enforcement import (
    RATE_LIMIT_CLASSES,
    SCOPE_FAMILIES,
    enforce_route_metadata,
    required_scopes_for,
    unmapped_scope_families,
)
from api.route_metadata import route_meta


def _app(auth_mode: str = "legacy", scopes: list[str] | None = None, tenant: str = "t-1") -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.auth_mode = auth_mode
        request.state.scopes = list(scopes or [])
        request.state.tenant_id = tenant
        return await call_next(request)

    @app.get("/agents")
    @route_meta(auth_required=True, tenant_required=True, scope="agents.read", rate_limit="standard")
    async def list_agents():
        return {"ok": True}

    @app.post("/agents/{agent_id}/run")
    @route_meta(auth_required=True, tenant_required=True, scope="agents.execute", rate_limit="agent-execution")
    async def run_agent(agent_id: str):
        return {"ran": agent_id}

    @app.get("/public")
    @route_meta(auth_required=False, tenant_required=False, rate_limit="public-read")
    async def public():
        return {"public": True}

    @app.get("/plain")
    async def plain():
        return {"plain": True}

    return app


class TestScopeEnforcement:
    def test_family_mapping_read_vs_write(self):
        assert required_scopes_for("agents.read", "GET") == ("agents:read",)
        assert required_scopes_for("agents.execute", "POST") == ("agents:write",)
        assert required_scopes_for("workflows.sensitive.list", "GET") == ("workflows:read",)
        assert required_scopes_for("billing.subscribe", "POST") == ()  # unmapped family → not enforced
        assert required_scopes_for(None, "GET") == ()

    def test_user_without_family_scope_is_denied(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            client = TestClient(_app(scopes=["approvals:read"]))
            assert client.get("/agents").status_code == 403
            assert client.post("/agents/a1/run").status_code == 403

    def test_user_with_read_scope_can_read_but_not_write(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            client = TestClient(_app(scopes=["agents:read"]))
            assert client.get("/agents").status_code == 200
            assert client.post("/agents/a1/run").status_code == 403

    def test_admin_scope_satisfies_every_family(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            client = TestClient(_app(scopes=["agenticorg:admin"]))
            assert client.post("/agents/a1/run").status_code == 200

    def test_grantex_agent_tokens_skip_rbac_families(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            client = TestClient(_app(auth_mode="grantex", scopes=["tool:hubspot:read:contacts"]))
            assert client.get("/agents").status_code == 200

    def test_routes_without_metadata_are_untouched(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            assert TestClient(_app(scopes=[])).get("/plain").status_code == 200

    def test_log_mode_records_instead_of_denying(self):
        from core.config import settings

        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)), patch.object(
            settings, "route_enforcement_mode", "log"
        ):
            assert TestClient(_app(scopes=[])).get("/agents").status_code == 200


class TestRateLimitEnforcement:
    def test_declared_class_is_counted_per_tenant(self):
        calls: list[tuple] = []

        async def fake_rate(namespace, key, limit, window):
            calls.append((namespace, key, limit, window))
            return False

        with patch("core.auth_state.check_window_rate", fake_rate):
            TestClient(_app(scopes=["agents:read"], tenant="tenant-42")).get("/agents")
        assert calls == [("rl:standard", "t:tenant-42", *RATE_LIMIT_CLASSES["standard"])]

    def test_public_routes_are_counted_per_ip(self):
        calls: list[tuple] = []

        async def fake_rate(namespace, key, limit, window):
            calls.append((namespace, key))
            return False

        with patch("core.auth_state.check_window_rate", fake_rate):
            TestClient(_app()).get("/public")
        assert calls[0][0] == "rl:public-read"
        assert calls[0][1].startswith("ip:")

    def test_blocked_returns_429(self):
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=True)):
            assert TestClient(_app(scopes=["agents:read"])).get("/agents").status_code == 429

    def test_backend_outage_fails_closed_only_for_credential_and_public_classes(self):
        async def broken(*_a, **_k):
            raise RuntimeError("redis down (strict)")

        with patch("core.auth_state.check_window_rate", broken):
            client = TestClient(_app(scopes=["agents:read"]))
            assert client.get("/public").status_code == 503  # public-* fails closed
            assert client.get("/agents").status_code == 200  # standard stays available


class TestRouteTableCoverage:
    """Pin which declared scope families are enforced vs merely reported."""

    def _declared(self) -> list[str]:
        # FastAPI >= 0.141 keeps included routers lazy (_IncludedRouter); walk
        # their effective candidates to reach the real APIRoute endpoints.
        from api.main import app

        def walk(routes):
            for r in routes:
                if hasattr(r, "effective_candidates"):
                    cands = r.effective_candidates
                    yield from walk(cands() if callable(cands) else cands)
                else:
                    yield r

        out: list[str] = []
        for route in walk(app.routes):
            meta = getattr(getattr(route, "endpoint", None), "__enterprise_route_metadata__", None)
            if isinstance(meta, dict) and meta.get("scope"):
                out.append(meta["scope"])
        return out

    def test_real_app_applies_rate_limit_class_end_to_end(self):
        from api.main import app

        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=True)):
            assert TestClient(app).get("/api/v1/evals").status_code == 429
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
            assert TestClient(app).get("/api/v1/evals").status_code == 200

    def test_every_mapped_family_targets_a_real_rbac_scope(self):
        from core.rbac import ROLE_SCOPES

        known = {s for scopes in ROLE_SCOPES.values() for s in scopes}
        for read_scope, write_scope in SCOPE_FAMILIES.values():
            assert read_scope in known and write_scope in known

    def test_unmapped_families_are_reported_not_silently_enforced(self):
        declared = self._declared()
        assert declared, "route table has declared scopes"
        unmapped = unmapped_scope_families(declared)
        # These families are authenticated + admin-gated at the route level
        # where needed but have no RBAC family yet; they are intentionally
        # visible here. Adding a family to SCOPE_FAMILIES removes it from
        # this set — extend the list deliberately, never by accident.
        assert "agents" not in unmapped and "workflows" not in unmapped and "approvals" not in unmapped
        assert len(unmapped) > 0


@pytest.mark.asyncio
async def test_main_app_registers_the_global_dependency():
    from api.main import app

    assert any(
        getattr(d, "dependency", None) is enforce_route_metadata for d in app.router.dependencies
    )
