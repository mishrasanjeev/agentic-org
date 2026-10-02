# SPDX-License-Identifier: Apache-2.0
"""Operator override endpoints: admin-only, validated input, audited changes."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import operator_overrides as api
from core.governance.operator_override import Override

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "target_kind": "agent",
        "target_id": "agent-1",
        "mode": "halt",
        "limit_per_minute": None,
        "reason": "drill",
        "created_by": "admin",
        "created_at": datetime.now(UTC),
        "expires_at": None,
        "released_at": None,
        "released_by": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.scopes = scopes
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

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
    assert client.get("/api/v1/operator-overrides").status_code == 403
    body = {"target_kind": "all_agents", "reason": "incident 42"}
    assert client.post("/api/v1/operator-overrides", json=body).status_code == 403


def test_list_and_status(session_rows):
    session_rows.append(_row())
    client = TestClient(_app(["agenticorg:admin"]))
    listed = client.get("/api/v1/operator-overrides")
    assert listed.status_code == 200
    assert listed.json()[0]["target_kind"] == "agent" and listed.json()[0]["active"] is True

    active = Override(
        id="o1", target_kind="all_agents", target_id="", mode="halt", limit_per_minute=None, reason="drill"
    )
    with patch.object(api.overrides, "enabled", AsyncMock(return_value=True)):
        with patch.object(api.overrides, "active_overrides", AsyncMock(return_value=[active])):
            status = client.get("/api/v1/operator-overrides/status")
    assert status.status_code == 200
    assert status.json() == {"enabled": True, "active": [active.to_dict()]}


@pytest.mark.parametrize(
    "body",
    [
        {"target_kind": "spaceship", "reason": "incident 42"},
        {"target_kind": "agent", "reason": "incident 42"},
        {"target_kind": "agent", "target_id": "a", "mode": "throttle", "reason": "incident 42"},
        {"target_kind": "agent", "target_id": "a", "mode": "rewind", "reason": "incident 42"},
        {"target_kind": "all_agents", "reason": "x"},
        {"target_kind": "all_agents", "reason": "incident 42", "expires_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_invalid_input_is_refused_before_any_write(session_rows, body):
    client = TestClient(_app(["agenticorg:admin"]))
    with patch.object(api.overrides, "set_override", AsyncMock()) as setter:
        response = client.post("/api/v1/operator-overrides", json=body)
    assert response.status_code == 422
    setter.assert_not_called()


def test_set_and_release(session_rows):
    placed = Override(
        id=str(uuid.uuid4()),
        target_kind="provider",
        target_id="gemini",
        mode="throttle",
        limit_per_minute=5,
        reason="incident 42",
    )
    session_rows.append(
        _row(id=uuid.UUID(placed.id), target_kind="provider", target_id="gemini", mode="throttle", limit_per_minute=5)
    )
    client = TestClient(_app(["agenticorg:admin"]))
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with patch.object(api.overrides, "set_override", AsyncMock(return_value=placed)) as setter:
        response = client.post(
            "/api/v1/operator-overrides",
            json={
                "target_kind": "provider",
                "target_id": "gemini",
                "mode": "throttle",
                "limit_per_minute": 5,
                "reason": "incident 42",
                "expires_at": expires,
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "throttle" and response.json()["limit_per_minute"] == 5
    kwargs = setter.await_args.kwargs
    assert kwargs["target_kind"] == "provider" and kwargs["mode"] == "throttle" and kwargs["limit_per_minute"] == 5
    assert kwargs["expires_at"].tzinfo is not None

    session_rows[0].released_at = datetime.now(UTC)
    with patch.object(api.overrides, "release_override", AsyncMock(return_value=placed)) as releaser:
        released = client.post(f"/api/v1/operator-overrides/{placed.id}/release")
    assert released.status_code == 200 and released.json()["active"] is False
    assert str(releaser.await_args.args[1]) == placed.id

    with patch.object(api.overrides, "release_override", AsyncMock(return_value=None)):
        assert client.post(f"/api/v1/operator-overrides/{uuid.uuid4()}/release").status_code == 404
