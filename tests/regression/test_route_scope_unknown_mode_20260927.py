# SPDX-License-Identifier: Apache-2.0
"""Route scope residuals of review H-1 (FINDINGS A-68).

* ``_check_scope`` never read ``auth_mode``. The auth middleware sets
  ``api_key``, ``grantex`` or ``legacy`` once it has verified a credential, but
  a request carrying any other mode was checked against its scopes all the
  same, so ``agenticorg:admin`` in them passed every route and a route in an
  unmapped family needed no scope at all. Such a request is now always logged.
  With ``AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE`` on it is refused before
  any scope is read, in log mode too; off (the default), it is checked on its
  scopes as before (FINDINGS A-95).
* A2A (``/a2a/tasks``) and MCP (``/mcp/call``) run agents, but their families
  were unmapped, so any authenticated credential reached them. With
  ``AGENTICORG_ROUTE_SCOPE_A2A_MCP`` on they need ``a2a:read`` / ``a2a:write``
  and ``mcp:read`` / ``mcp:write`` (or ``agenticorg:admin``) like every mapped
  family; off (the default), nothing changes.
* The refusal leaves real callers alone only while every authenticated route
  goes through the auth middleware and the middleware sets nothing but the
  known modes. The route table tests at the end pin both.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import auth.grantex_middleware as auth_middleware
from api import route_enforcement
from api.route_enforcement import (
    GRANTABLE_ROUTE_SCOPES,
    KNOWN_AUTH_MODES,
    enforce_route_metadata,
    required_scopes_for,
    unmapped_scope_families,
    validate_route_scopes,
)
from api.route_metadata import ROUTE_METADATA_ATTR, route_meta
from api.v1 import a2a, mcp
from auth.grantex_middleware import GrantexAuthMiddleware
from core.config import Settings, settings
from core.rbac import ROLE_SCOPES

ADMIN = ["agenticorg:admin"]
TOOL_ONLY = ["tool:mock:read"]
KNOWN_MODES = ("api_key", "grantex", "legacy")
REFUSED = "Unrecognised authentication mode; request refused"
UNSET = object()  # request.state.auth_mode never assigned
UNKNOWN_MODES = [pytest.param(UNSET, id="unset"), None, "", "mystery", "Legacy", "api-key"]


def _declared(endpoint: Any) -> dict[str, Any]:
    return dict(getattr(endpoint, ROUTE_METADATA_ATTR))


# The real A2A and MCP routes' declared metadata, so a change to their scope
# strings is caught here.
CREATE_TASK = _declared(a2a.create_task)
GET_TASK = _declared(a2a.get_task)
CALL_TOOL = _declared(mcp.call_tool)


def _client(auth_mode: object, scopes: list[str]) -> TestClient:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        if auth_mode is not UNSET:
            request.state.auth_mode = auth_mode
        request.state.scopes = list(scopes)
        request.state.tenant_id = "t-1"
        return await call_next(request)

    @app.get("/agents")
    @route_meta(auth_required=True, tenant_required=True, scope="agents.read", rate_limit="standard")
    async def list_agents():
        return {"agents": []}

    @app.post("/billing/subscribe")  # a family with no mapping
    @route_meta(auth_required=True, tenant_required=True, scope="billing.subscribe", rate_limit="standard")
    async def subscribe():
        return {"ok": True}

    @app.post("/a2a/tasks")
    @route_meta(**CREATE_TASK)
    async def create_task():
        return {"ok": True}

    @app.get("/a2a/tasks/{task_id}")
    @route_meta(**GET_TASK)
    async def get_task(task_id: str):
        return {"id": task_id}

    @app.post("/mcp/call")
    @route_meta(**CALL_TOOL)
    async def call_tool():
        return {"ok": True}

    @app.get("/a2a/agents")
    @route_meta(**_declared(a2a.list_available_agents))
    async def list_available_agents():
        return {"agents": []}

    @app.get("/mcp/tools")
    @route_meta(**_declared(mcp.list_tools))
    async def list_tools():
        return {"tools": []}

    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


def _unknown_mode_warnings(warning: Any) -> list[dict[str, Any]]:
    """The ``extra`` of every unknown-mode warning the patched logger received."""
    return [c.kwargs["extra"] for c in warning.call_args_list if c.args == ("route_enforcement_unknown_auth_mode",)]


def _logged_mode(mode: object) -> str:
    return repr(None if mode is UNSET else mode)[:40]


@pytest.mark.parametrize("mode", UNKNOWN_MODES)
def test_an_unknown_auth_mode_is_refused_even_with_admin_scope(mode: object) -> None:
    client = _client(mode, ADMIN)
    with (
        patch.object(settings, "route_refuse_unknown_auth_mode", True),
        patch.object(route_enforcement.logger, "warning") as warning,
    ):
        for method, path in (("get", "/agents"), ("post", "/billing/subscribe"), ("post", "/a2a/tasks")):
            response = getattr(client, method)(path)
            assert response.status_code == 403, path
            assert response.json()["detail"] == REFUSED

        # Log mode stages scope denials; it does not let an unknown mode through.
        with patch.object(settings, "route_enforcement_mode", "log"):
            assert client.get("/agents").status_code == 403

        # Public routes are not authenticated, so they are not checked for a mode.
        assert client.get("/a2a/agents").status_code == 200
        assert client.get("/mcp/tools").status_code == 200

    assert _unknown_mode_warnings(warning) == [
        {"path": path, "auth_mode": _logged_mode(mode)}
        for path in ("/agents", "/billing/subscribe", "/a2a/tasks", "/agents")
    ]


@pytest.mark.parametrize("mode", UNKNOWN_MODES)
def test_an_unknown_auth_mode_is_logged_and_checked_as_before_while_the_refusal_is_off(mode: object) -> None:
    """Off (the default), the scope checks run on whatever scopes the request carries, as before."""
    assert Settings.model_fields["route_refuse_unknown_auth_mode"].default is False

    with (
        patch.object(settings, "route_refuse_unknown_auth_mode", False),
        patch.object(route_enforcement.logger, "warning") as warning,
    ):
        # The admin scope, or the family's own scope, passes a mapped route.
        assert _client(mode, ADMIN).get("/agents").status_code == 200
        assert _client(mode, [*TOOL_ONLY, "agents:read"]).get("/agents").status_code == 200

        # Without it the route answers Missing scope, not the unknown-mode refusal.
        response = _client(mode, TOOL_ONLY).get("/agents")
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing scope: agents:read"

        # An unmapped family needs no scope, and log mode lets a denial through.
        assert _client(mode, TOOL_ONLY).post("/billing/subscribe").status_code == 200
        with patch.object(settings, "route_enforcement_mode", "log"):
            assert _client(mode, TOOL_ONLY).get("/agents").status_code == 200

    # Every one of those requests was logged, with the path and mode only.
    assert _unknown_mode_warnings(warning) == [
        {"path": path, "auth_mode": _logged_mode(mode)}
        for path in ("/agents", "/agents", "/agents", "/billing/subscribe", "/agents")
    ]


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", KNOWN_MODES)
def test_a_known_auth_mode_is_not_logged_as_unknown(mode: str, enabled: bool) -> None:
    with (
        patch.object(settings, "route_refuse_unknown_auth_mode", enabled),
        patch.object(route_enforcement.logger, "warning") as warning,
    ):
        assert _client(mode, ADMIN).get("/agents").status_code == 200
        assert _client(mode, TOOL_ONLY).get("/agents").status_code == 403
    assert _unknown_mode_warnings(warning) == []


def test_the_refusal_setting_defaults_off_and_reads_its_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE", raising=False)
    assert Settings(_env_file=None).route_refuse_unknown_auth_mode is False
    monkeypatch.setenv("AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE", "true")
    assert Settings(_env_file=None).route_refuse_unknown_auth_mode is True
    monkeypatch.setenv("AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE", "false")
    assert Settings(_env_file=None).route_refuse_unknown_auth_mode is False


@pytest.mark.parametrize("mode", KNOWN_MODES)
def test_the_known_auth_modes_are_checked_as_before(mode: str) -> None:
    assert _client(mode, ADMIN).get("/agents").status_code == 200
    assert _client(mode, ADMIN).post("/billing/subscribe").status_code == 200
    assert _client(mode, TOOL_ONLY).get("/agents").status_code == 403
    assert _client(mode, TOOL_ONLY).post("/billing/subscribe").status_code == 200


@pytest.mark.parametrize("mode", KNOWN_MODES)
def test_a2a_and_mcp_routes_need_their_scope_when_enabled(mode: str) -> None:
    with patch.object(settings, "route_scope_a2a_mcp", True):
        assert required_scopes_for(CREATE_TASK["scope"], "POST") == ("a2a:write",)
        assert required_scopes_for(GET_TASK["scope"], "GET") == ("a2a:read",)
        assert required_scopes_for(CALL_TOOL["scope"], "POST") == ("mcp:write",)
        assert unmapped_scope_families([CREATE_TASK["scope"], GET_TASK["scope"], CALL_TOOL["scope"]]) == set()

        refused = [
            (TOOL_ONLY, "post", "/a2a/tasks"),
            (TOOL_ONLY, "get", "/a2a/tasks/t1"),
            (TOOL_ONLY, "post", "/mcp/call"),
            (["a2a:read"], "post", "/a2a/tasks"),
            (["mcp:read"], "post", "/mcp/call"),
            (["agents:write"], "post", "/a2a/tasks"),
            (["agents:write"], "post", "/mcp/call"),
            (["mcp:write"], "post", "/a2a/tasks"),
        ]
        for scopes, method, path in refused:
            response = getattr(_client(mode, scopes), method)(path)
            assert response.status_code == 403, (scopes, path)
            assert response.json()["detail"].startswith("Missing scope: ")

        allowed = [
            (["a2a:write"], "post", "/a2a/tasks"),
            (["a2a:read"], "get", "/a2a/tasks/t1"),
            (["mcp:write"], "post", "/mcp/call"),
            (["mcp:call"], "post", "/mcp/call"),  # what create_api_key has always issued
            (ADMIN, "post", "/a2a/tasks"),
            (ADMIN, "get", "/a2a/tasks/t1"),
            (ADMIN, "post", "/mcp/call"),
        ]
        for scopes, method, path in allowed:
            assert getattr(_client(mode, [*TOOL_ONLY, *scopes]), method)(path).status_code == 200, (scopes, path)

        # Discovery stays public.
        assert _client(mode, TOOL_ONLY).get("/a2a/agents").status_code == 200
        assert _client(mode, TOOL_ONLY).get("/mcp/tools").status_code == 200

    # A2A and MCP run any agent type with no domain check, so no role holds
    # their scopes: of human sessions, only an admin reaches them.
    for role, scopes in ROLE_SCOPES.items():
        assert not {s for s in scopes if s.startswith(("a2a", "mcp"))}, role


@pytest.mark.parametrize("mode", KNOWN_MODES)
def test_a2a_and_mcp_routes_unchanged_when_disabled(mode: str) -> None:
    assert Settings.model_fields["route_scope_a2a_mcp"].default is False

    with patch.object(settings, "route_scope_a2a_mcp", False):
        assert required_scopes_for(CREATE_TASK["scope"], "POST") == ()
        assert required_scopes_for(GET_TASK["scope"], "GET") == ()
        assert required_scopes_for(CALL_TOOL["scope"], "POST") == ()
        # Reported as unmapped, exactly as before.
        assert unmapped_scope_families([CREATE_TASK["scope"], CALL_TOOL["scope"]]) == {"a2a", "mcp"}

        client = _client(mode, TOOL_ONLY)
        assert client.post("/a2a/tasks").status_code == 200
        assert client.get("/a2a/tasks/t1").status_code == 200
        assert client.post("/mcp/call").status_code == 200

    # Grantable while off, so agents can hold the scopes before they are required.
    assert {"a2a:read", "a2a:write", "mcp:read", "mcp:write"} <= GRANTABLE_ROUTE_SCOPES
    assert validate_route_scopes(["mcp:write", "a2a:write"]) == ["a2a:write", "mcp:write"]
    with pytest.raises(ValueError, match="cannot be granted"):
        validate_route_scopes(["mcp:call"])  # a legacy alias, not the canonical scope


# ── Through the real app ──────────────────────────────────────────────────

TENANT = str(uuid.UUID(int=0xA3))


@contextmanager
def _real_client(scopes: list[str]) -> Iterator[TestClient]:
    """The real app, authenticating a legacy session that carries ``scopes``."""
    from api.main import app

    claims = {
        "sub": "user@example.com",
        "role": "cfo",
        "agenticorg:tenant_id": TENANT,
        "agenticorg:domains": ["finance"],
        "agenticorg:user_id": str(uuid.UUID(int=0xC0)),
        "grantex:scopes": scopes,
    }

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    with (
        patch("auth.grantex_middleware.is_ip_blocked", return_value=False),
        patch("auth.grantex_middleware.record_auth_failure", side_effect=_noop),
        patch("auth.grantex_middleware.clear_auth_failures", side_effect=_noop),
        patch("auth.grantex_middleware.validate_token", AsyncMock(return_value=claims)),
        patch("auth.grantex_middleware.check_user_session_state", AsyncMock(return_value=None)),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
    ):
        # Not entered as a context manager, so the app's lifespan does not run.
        client = TestClient(app, raise_server_exceptions=False)
        client.headers["Authorization"] = "Bearer fake-test-token"
        yield client


@pytest.mark.parametrize("enabled", [True, False])
def test_the_real_mcp_route_follows_the_setting(enabled: bool) -> None:
    """``POST /api/v1/mcp/call`` with no tool name answers 400 once past the scope check."""
    with patch.object(settings, "route_scope_a2a_mcp", enabled):
        for role in ("cfo", "auditor"):
            with _real_client(list(ROLE_SCOPES[role])) as client:
                response = client.post("/api/v1/mcp/call", json={})
            if enabled:
                assert response.status_code == 403, role
                assert response.json()["detail"] == "Missing scope: mcp:write"
            else:
                assert response.status_code == 400, role

        with _real_client(list(ROLE_SCOPES["admin"])) as admin:
            assert admin.post("/api/v1/mcp/call", json={}).status_code == 400


# ── The route table the refusal relies on ─────────────────────────────────
#
# GrantexAuthMiddleware passes every OPTIONS request, and every path in
# EXEMPT_PATHS or under EXEMPT_PREFIXES, to the app without reading a
# credential, so no ``auth_mode`` is set on it. An authenticated route reached
# that way is logged as an unknown mode and, with
# AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE on, refused. That fails closed, but
# a valid caller would be refused too, so no authenticated route may be
# reachable through an exemption.

# (route path, exemption) pairs where a path parameter reaches under an
# exempt prefix. Remove an entry with the fix; never add one without a finding.
KNOWN_EXEMPT_OVERLAPS = {
    # FINDINGS A-88: a consent handle that begins with "callback" falls under
    # the "/api/v1/aa/consent/callback" prefix. Refused as an unknown mode with
    # the refusal on, and by the route's tenant dependency with it off
    # (test_a_request_the_middleware_skips_is_refused_on_an_authenticated_route).
    ("/api/v1/aa/consent/{consent_handle}/status", "/api/v1/aa/consent/callback"),
}


def _walk(routes: Iterable[Any]) -> Iterator[Any]:
    # FastAPI keeps included routers lazy (_IncludedRouter); their effective
    # candidates carry the full path, methods and endpoint of each route.
    for route in routes:
        if hasattr(route, "effective_candidates"):
            candidates = route.effective_candidates
            yield from _walk(candidates() if callable(candidates) else candidates)
        else:
            yield route


def _http_routes() -> list[Any]:
    """Every HTTP API route on the real app. WebSocket routes are A-86."""
    from api.main import app

    return [r for r in _walk(app.routes) if isinstance(getattr(r, "original_route", r), APIRoute)]


def _auth_required(route: Any) -> bool:
    meta = getattr(route.endpoint, ROUTE_METADATA_ATTR, None)
    return isinstance(meta, dict) and bool(meta.get("auth_required"))


def _exemptions_reaching(route: Any) -> set[str]:
    """Exempt paths and prefixes that some request path matching ``route`` falls under.

    Conservative for prefixes: a path parameter that starts inside an exempt
    prefix counts, whether or not a value can complete it.
    """
    template: str = route.path
    static = template.split("{", 1)[0]
    hits = {path for path in GrantexAuthMiddleware.EXEMPT_PATHS if route.path_regex.match(path)}
    for prefix in GrantexAuthMiddleware.EXEMPT_PREFIXES:
        if template.startswith(prefix) or ("{" in template and prefix.startswith(static)):
            hits.add(prefix)
    return hits


def test_every_authenticated_route_goes_through_the_auth_middleware() -> None:
    from api.main import app

    assert GrantexAuthMiddleware in [m.cls for m in app.user_middleware]

    routes = _http_routes()
    authenticated = [r for r in routes if _auth_required(r)]
    # The walk reaches the real route table: authenticated routes and the
    # public routes the middleware exempts.
    methods_and_paths = {(m, r.path) for r in authenticated for m in r.methods}
    assert {("POST", "/api/v1/a2a/tasks"), ("POST", "/api/v1/mcp/call"), ("GET", "/api/v1/agents")} <= (
        methods_and_paths
    )
    assert len(authenticated) >= 100, len(authenticated)
    public_exempt = {r.path for r in routes if not _auth_required(r) and _exemptions_reaching(r)}
    assert {"/api/v1/health", "/api/v1/mcp/tools", "/api/v1/a2a/agents", "/api/v1/cron/schedules"} <= public_exempt

    assert [r.path for r in authenticated if "OPTIONS" in r.methods] == []
    overlaps = {(r.path, hit) for r in authenticated for hit in _exemptions_reaching(r)}
    assert overlaps == KNOWN_EXEMPT_OVERLAPS, {
        "new": sorted(overlaps - KNOWN_EXEMPT_OVERLAPS),
        "gone": sorted(KNOWN_EXEMPT_OVERLAPS - overlaps),
    }


def test_the_known_modes_are_exactly_the_ones_the_auth_middleware_sets() -> None:
    """A mode the middleware set but the refusal did not know would lock out valid callers."""
    assigned: set[object] = set()
    for node in ast.walk(ast.parse(inspect.getsource(auth_middleware))):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "auth_mode":
                assert isinstance(node.value, ast.Constant), ast.unparse(node)
                assigned.add(node.value.value)
    assert assigned == set(KNOWN_AUTH_MODES)


def test_a_request_the_middleware_skips_is_refused_on_an_authenticated_route() -> None:
    """The known overlap (A-88) fails closed: the handler never runs, with or without a token."""
    from api.main import app

    overlap_path = "/api/v1/aa/consent/callback-0/status"
    assert overlap_path.startswith(GrantexAuthMiddleware.EXEMPT_PREFIXES)
    with (
        patch.object(settings, "route_refuse_unknown_auth_mode", True),
        patch("api.v1.aa_callback._get_consent_manager", AsyncMock()) as manager,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        for headers in ({}, {"Authorization": "Bearer fake-test-token"}):
            response = client.get(overlap_path, headers=headers)
            assert response.status_code == 403, headers
            assert response.json()["detail"] == "Unrecognised authentication mode; request refused"
    manager.assert_not_awaited()


def test_a_request_the_middleware_skips_gets_the_old_answer_while_the_refusal_is_off() -> None:
    """Off (the default), the known overlap (A-88) is logged and refused by the route's tenant dependency."""
    from api.main import app

    overlap_path = "/api/v1/aa/consent/callback-0/status"
    with (
        patch.object(settings, "route_refuse_unknown_auth_mode", False),
        patch.object(route_enforcement.logger, "warning") as warning,
        patch("api.v1.aa_callback._get_consent_manager", AsyncMock()) as manager,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        for headers in ({}, {"Authorization": "Bearer fake-test-token"}):
            response = client.get(overlap_path, headers=headers)
            assert response.status_code == 401, headers
            assert response.json()["detail"] == "No tenant context"
    manager.assert_not_awaited()
    assert _unknown_mode_warnings(warning) == [{"path": overlap_path, "auth_mode": "None"}] * 2
