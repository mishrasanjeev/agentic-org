# SPDX-License-Identifier: Apache-2.0
"""Agent tokens need a route's scope like any other credential (review H-1).

``_check_scope`` returned before looking at scopes for any ``auth_mode`` other
than ``legacy`` and ``api_key``. A Grantex agent token carries tool scopes
(``tool:<connector>:<permission>``), none of which is a route scope, so every
agent token passed every route check: one granted only ``tool:mock:read``
could read the whole tenant audit trail and run any agent. An agent token is
now checked exactly as an API key is, and an unrecognised mode is refused.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.route_enforcement import enforce_route_metadata, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR, route_meta

READ_ONLY_AGENT = ["tool:mock:read"]


def _app(auth_mode: str | None, scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.auth_mode = auth_mode
        request.state.scopes = list(scopes)
        request.state.tenant_id = "t-1"
        return await call_next(request)

    @app.get("/audit")
    @route_meta(auth_required=True, tenant_required=True, scope="audit.sensitive.read", rate_limit="standard")
    async def audit():
        return {"rows": []}

    @app.post("/agents/{agent_id}/run")
    @route_meta(auth_required=True, tenant_required=True, scope="agents.execute", rate_limit="agent-execution")
    async def run_agent(agent_id: str):
        return {"ran": agent_id}

    @app.get("/agents")
    @route_meta(auth_required=True, tenant_required=True, scope="agents.read", rate_limit="standard")
    async def list_agents():
        return {"agents": []}

    return app


def _client(auth_mode: str | None, scopes: list[str]) -> TestClient:
    return TestClient(_app(auth_mode, scopes))


@pytest.fixture(autouse=True)
def _no_rate_limit():
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


def test_a_read_only_agent_token_cannot_read_the_tenant_audit_trail() -> None:
    assert _client("grantex", READ_ONLY_AGENT).get("/audit").status_code == 403


def test_a_read_only_agent_token_cannot_run_an_agent() -> None:
    assert _client("grantex", READ_ONLY_AGENT).post("/agents/any/run").status_code == 403


def test_a_read_only_agent_token_cannot_list_agents() -> None:
    assert _client("grantex", READ_ONLY_AGENT).get("/agents").status_code == 403


@pytest.mark.parametrize(
    ("scopes", "method", "path"),
    [
        (["audit:read"], "get", "/audit"),
        (["agents:run"], "post", "/agents/a1/run"),
        (["agents:write"], "post", "/agents/a1/run"),
        (["agents:read"], "get", "/agents"),
        (["agenticorg:admin"], "get", "/audit"),
    ],
)
def test_an_agent_token_with_the_route_scope_is_allowed(scopes: list[str], method: str, path: str) -> None:
    client = _client("grantex", [*READ_ONLY_AGENT, *scopes])
    assert getattr(client, method)(path).status_code == 200


def test_an_agent_token_with_a_read_scope_still_cannot_run_an_agent() -> None:
    assert _client("grantex", ["agents:read"]).post("/agents/a1/run").status_code == 403


@pytest.mark.parametrize("mode", [None, "", "mystery"])
def test_an_unrecognised_auth_mode_is_refused(mode: str | None) -> None:
    assert _client(mode, ["tool:mock:read"]).get("/audit").status_code == 403


def test_api_keys_are_checked_as_before() -> None:
    assert _client("api_key", READ_ONLY_AGENT).get("/audit").status_code == 403
    assert _client("api_key", ["audit:read"]).get("/audit").status_code == 200


def _declared_scope(endpoint) -> str:
    meta = getattr(endpoint, ROUTE_METADATA_ATTR)
    assert meta["auth_required"] is True
    return meta["scope"]


def test_the_review_routes_declare_an_enforced_scope() -> None:
    """The real routes from the finding map onto a checked family, so the fix reaches them."""
    from api.v1.agents import run_agent
    from api.v1.audit import query_audit

    assert required_scopes_for(_declared_scope(query_audit), "GET") == ("audit:read",)
    assert required_scopes_for(_declared_scope(run_agent), "POST") == ("agents:write",)
