# SPDX-License-Identifier: Apache-2.0
"""Model gateway endpoints: admin-only, validated input, audited changes, dry-run evaluation."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import model_gateway as api
from core.governance.model_gateway import Evaluation, ModelGatewayRefused, Policy, RouteDecision

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "finance",
        "priority": 10,
        "enabled": True,
        "use_case": None,
        "sensitivity": None,
        "agent_id": None,
        "business_unit": "finance",
        "language": None,
        "provider": "openai",
        "model": "gpt-4o",
        "tier": None,
        "allowed_providers": ["openai"],
        "in_region_only": False,
        "reason": "finance stays with one provider",
        "created_by": "user:1",
        "created_at": datetime.now(UTC),
        "updated_by": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _policy(row) -> Policy:
    return Policy(
        id=str(row.id),
        name=row.name,
        priority=row.priority,
        enabled=row.enabled,
        business_unit=row.business_unit,
        provider=row.provider,
        model=row.model,
        allowed_providers=tuple(row.allowed_providers) if row.allowed_providers else None,
        reason=row.reason,
    )


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = app.state.auth_mode
        request.state.claims = dict(app.state.claims)
        request.state.scopes = scopes
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.state.auth_mode = "api_key"
    app.state.claims = {"sub": "apikey:key_01"}

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    return app


@pytest.fixture(autouse=True)
def _no_rate_limit_redis():
    # The route rate limiter counts in Redis; a unit test never reaches one (FINDINGS A-59).
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


@pytest.fixture
def session_rows(monkeypatch):
    rows: list = []

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return self

        def all(self):
            return list(self._items)

        def scalar_one(self):
            return self._items[0]

    session = MagicMock()

    async def _execute(_query):
        return _Result(rows)

    session.execute = _execute

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield session

    monkeypatch.setattr(api, "get_tenant_session", _ctx)
    return rows


def test_non_admin_is_refused(session_rows):
    client = TestClient(_app(["agents:write"]))
    assert client.get("/api/v1/model-gateway/policies").status_code == 403
    assert client.post("/api/v1/model-gateway/policies", json={"name": "p", "tier": "tier1"}).status_code == 403
    assert client.post("/api/v1/model-gateway/evaluate", json={"use_case": "agent_run"}).status_code == 403


def test_list_and_status(session_rows):
    row = _row()
    session_rows.append(row)
    client = TestClient(_app(["agenticorg:admin"]))
    listed = client.get("/api/v1/model-gateway/policies")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "finance" and listed.json()[0]["allowed_providers"] == ["openai"]

    with (
        patch.object(api.gateway, "enabled", AsyncMock(return_value=True)),
        patch.object(api.gateway, "active_policies", AsyncMock(return_value=[_policy(row)])),
    ):
        status = client.get("/api/v1/model-gateway/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True and status.json()["active_policies"][0]["name"] == "finance"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "p"},
        {"name": "p", "tier": "tier9"},
        {"name": "p", "sensitivity": "secret", "tier": "tier1"},
        {"name": "p", "provider": "openai", "model": "gemini-2.5-pro"},
        {"name": "p", "provider": "openai", "allowed_providers": ["gemini"]},
        {"name": "p", "tier": "tier1", "priority": -1},
        {"tier": "tier1"},
    ],
)
def test_invalid_input_is_refused_before_any_write(session_rows, body):
    client = TestClient(_app(["agenticorg:admin"]))
    with patch.object(api.gateway, "set_policy", AsyncMock()) as setter:
        response = client.post("/api/v1/model-gateway/policies", json=body)
    assert response.status_code == 422
    setter.assert_not_called()


def test_create_update_and_delete(session_rows):
    row = _row()
    session_rows.append(row)
    client = TestClient(_app(["agenticorg:admin"]))
    body = {
        "name": "finance",
        "priority": 10,
        "business_unit": "finance",
        "provider": "openai",
        "model": "gpt-4o",
        "allowed_providers": ["openai"],
        "reason": "finance stays with one provider",
    }
    with patch.object(api.gateway, "set_policy", AsyncMock(return_value=_policy(row))) as setter:
        created = client.post("/api/v1/model-gateway/policies", json=body)
    assert created.status_code == 201 and created.json()["id"] == str(row.id)
    assert setter.await_args.kwargs["actor_id"] == "api_key:apikey:key_01"
    assert setter.await_args.kwargs["name"] == "finance" and setter.await_args.kwargs["in_region_only"] is False

    with patch.object(api.gateway, "update_policy", AsyncMock(return_value=_policy(row))) as updater:
        changed = client.patch(f"/api/v1/model-gateway/policies/{row.id}", json={"priority": 5})
    assert changed.status_code == 200
    assert updater.await_args.kwargs["changes"] == {"priority": 5}
    assert client.patch(f"/api/v1/model-gateway/policies/{row.id}", json={}).status_code == 422
    with patch.object(api.gateway, "update_policy", AsyncMock(return_value=None)):
        assert client.patch(f"/api/v1/model-gateway/policies/{uuid.uuid4()}", json={"priority": 5}).status_code == 404
    with patch.object(api.gateway, "update_policy", AsyncMock(side_effect=ValueError("must be among"))):
        assert client.patch(f"/api/v1/model-gateway/policies/{row.id}", json={"provider": "gemini"}).status_code == 422

    with patch.object(api.gateway, "delete_policy", AsyncMock(return_value=True)) as deleter:
        assert client.delete(f"/api/v1/model-gateway/policies/{row.id}").status_code == 204
    assert deleter.await_args.kwargs["actor_id"] == "api_key:apikey:key_01"
    with patch.object(api.gateway, "delete_policy", AsyncMock(return_value=False)):
        assert client.delete(f"/api/v1/model-gateway/policies/{uuid.uuid4()}").status_code == 404


def test_the_actor_is_the_authenticated_principal(session_rows):
    row = _row()
    session_rows.append(row)
    app = _app(["agenticorg:admin"])
    app.state.auth_mode = "jwt"
    app.state.claims = {"sub": "ops@example.com", "agenticorg:user_id": str(uuid.uuid4()), "role": "admin"}
    client = TestClient(app)
    with patch.object(api.gateway, "set_policy", AsyncMock(return_value=_policy(row))) as setter:
        response = client.post("/api/v1/model-gateway/policies", json={"name": "finance", "tier": "tier1"})
    assert response.status_code == 201
    assert setter.await_args.kwargs["actor_id"] == "user:" + app.state.claims["agenticorg:user_id"]

    app.state.auth_mode = None
    app.state.claims = {}
    with patch.object(api.gateway, "set_policy", AsyncMock()) as setter:
        response = client.post("/api/v1/model-gateway/policies", json={"name": "finance", "tier": "tier1"})
    assert response.status_code == 403
    setter.assert_not_called()


def test_evaluate_returns_the_decision_or_the_refusal_as_data(session_rows):
    client = TestClient(_app(["agenticorg:admin"]))
    decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="c1", reason="finance", applied=True)
    evaluation = Evaluation(enabled=False, decision=decision)
    with patch.object(api.gateway, "evaluate", AsyncMock(return_value=evaluation)) as ask:
        response = client.post(
            "/api/v1/model-gateway/evaluate",
            json={"use_case": "agent_run", "business_unit": "finance", "sensitivity": "Internal"},
        )
    assert response.status_code == 200
    assert response.json() == {"refused": False, "enabled": False, "decision": decision.to_dict()}
    request = ask.await_args.args[0]
    assert str(request.tenant_id) == str(TENANT) and request.sensitivity == "internal"

    refusal = ModelGatewayRefused("Model gateway: refused", correlation_id="c2", policy_id="p1", policy_name="fence")
    with patch.object(api.gateway, "evaluate", AsyncMock(return_value=Evaluation(enabled=True, refusal=refusal))):
        response = client.post("/api/v1/model-gateway/evaluate", json={"use_case": "agent_run"})
    assert response.status_code == 200
    assert response.json()["refused"] is True and response.json()["enabled"] is True
    assert response.json()["model_gateway"]["policy_name"] == "fence"
    unknown = client.post("/api/v1/model-gateway/evaluate", json={"use_case": "x", "sensitivity": "secret"})
    assert unknown.status_code == 422
