# SPDX-License-Identifier: Apache-2.0
"""Guardrail endpoints: admin-only, validated input, audited changes, dry-run evaluation."""

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
from api.v1 import guardrails as api
from core.governance.guardrails.schema import GuardrailResult, Outcome, Rule

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "cards",
        "stage": "output",
        "detector": "sensitive_data",
        "action": "redact",
        "priority": 10,
        "enabled": True,
        "threshold": 0.5,
        "agent_id": None,
        "use_case": None,
        "risk_tier": None,
        "options": {"entities": ["CREDIT_CARD"]},
        "reason": "card numbers are redacted before delivery",
        "created_by": "user:1",
        "created_at": datetime.now(UTC),
        "updated_by": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _rule(row) -> Rule:
    return Rule(
        id=str(row.id),
        name=row.name,
        stage=row.stage,
        detector=row.detector,
        action=row.action,
        priority=row.priority,
        options=dict(row.options),
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
    assert client.get("/api/v1/guardrails/rules").status_code == 403
    assert (
        client.post(
            "/api/v1/guardrails/rules", json={"name": "r", "stage": "output", "detector": "toxicity"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/guardrails/evaluate", json={"stage": "output", "text": "x"}).status_code == 403


def test_list_and_status(session_rows):
    row = _row()
    session_rows.append(row)
    client = TestClient(_app(["agenticorg:admin"]))
    listed = client.get("/api/v1/guardrails/rules")
    assert listed.status_code == 200 and listed.json()[0]["options"] == {"entities": ["CREDIT_CARD"]}
    with (
        patch.object(api.guardrails, "enforcing", AsyncMock(return_value=False)),
        patch.object(api.guardrails, "active_rules", AsyncMock(return_value=[_rule(row)])),
    ):
        status = client.get("/api/v1/guardrails/status")
    assert status.status_code == 200
    assert status.json()["enforcing"] is False and status.json()["mode"] == "flag_only"
    assert status.json()["active_rules"][0]["name"] == "cards"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "r", "stage": "output"},
        {"name": "r", "stage": "elsewhere", "detector": "toxicity"},
        {"name": "r", "stage": "output", "detector": "toxicity", "action": "tokenise"},
        {"name": "r", "stage": "output", "detector": "pattern"},
        {"name": "r", "stage": "output", "detector": "toxicity", "threshold": 2},
        {"name": "r", "stage": "output", "detector": "toxicity", "risk_tier": "extreme"},
    ],
)
def test_invalid_input_is_refused_before_any_write(session_rows, body):
    client = TestClient(_app(["agenticorg:admin"]))
    with patch.object(api.guardrails, "set_rule", AsyncMock()) as setter:
        assert client.post("/api/v1/guardrails/rules", json=body).status_code == 422
    setter.assert_not_called()


def test_create_update_and_delete(session_rows):
    row = _row()
    session_rows.append(row)
    client = TestClient(_app(["agenticorg:admin"]))
    body = {
        "name": "cards",
        "stage": "output",
        "detector": "sensitive_data",
        "action": "redact",
        "options": {"entities": ["CREDIT_CARD"]},
    }
    with patch.object(api.guardrails, "set_rule", AsyncMock(return_value=_rule(row))) as setter:
        created = client.post("/api/v1/guardrails/rules", json=body)
    assert created.status_code == 201 and created.json()["id"] == str(row.id)
    assert (
        setter.await_args.kwargs["actor_id"] == "api_key:apikey:key_01"
        and setter.await_args.kwargs["action"] == "redact"
    )
    with patch.object(api.guardrails, "update_rule", AsyncMock(return_value=_rule(row))) as updater:
        assert client.patch(f"/api/v1/guardrails/rules/{row.id}", json={"priority": 5}).status_code == 200
    assert updater.await_args.kwargs["changes"] == {"priority": 5}
    assert client.patch(f"/api/v1/guardrails/rules/{row.id}", json={}).status_code == 422
    with patch.object(api.guardrails, "update_rule", AsyncMock(return_value=None)):
        assert client.patch(f"/api/v1/guardrails/rules/{uuid.uuid4()}", json={"priority": 5}).status_code == 404
    with patch.object(api.guardrails, "update_rule", AsyncMock(side_effect=ValueError("sensitive data only"))):
        assert client.patch(f"/api/v1/guardrails/rules/{row.id}", json={"action": "tokenise"}).status_code == 422
    with patch.object(api.guardrails, "delete_rule", AsyncMock(return_value=True)) as deleter:
        assert client.delete(f"/api/v1/guardrails/rules/{row.id}").status_code == 204
    assert deleter.await_args.kwargs["actor_id"] == "api_key:apikey:key_01"
    with patch.object(api.guardrails, "delete_rule", AsyncMock(return_value=False)):
        assert client.delete(f"/api/v1/guardrails/rules/{uuid.uuid4()}").status_code == 404


def test_a_change_needs_an_attributable_caller(session_rows):
    app = _app(["agenticorg:admin"])
    app.state.auth_mode = None
    app.state.claims = {}
    client = TestClient(app)
    with patch.object(api.guardrails, "set_rule", AsyncMock()) as setter:
        assert (
            client.post(
                "/api/v1/guardrails/rules", json={"name": "r", "stage": "output", "detector": "toxicity"}
            ).status_code
            == 403
        )
    setter.assert_not_called()


def test_evaluate_is_a_dry_run_reported_as_data(session_rows):
    client = TestClient(_app(["agenticorg:admin"]))
    outcome = Outcome(
        "r1", "cards", "output", "sensitive_data", "redact", 1, 1.0, ["CREDIT_CARD"], True, transformed=True
    )
    result = GuardrailResult(
        stage="output", text="card <CREDIT_CARD>", allowed=True, enforced=False, correlation_id="c1", outcomes=[outcome]
    )
    with patch.object(api.guardrails, "evaluate", AsyncMock(return_value=result)) as ask:
        response = client.post(
            "/api/v1/guardrails/evaluate",
            json={"stage": "Output", "text": "card 4111 1111 1111 1111", "agent_id": "a1", "risk_tier": "high"},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "card <CREDIT_CARD>" and response.json()["outcomes"][0]["transformed"] is True
    assert ask.await_args.args == ("output", "card 4111 1111 1111 1111")
    assert ask.await_args.kwargs["dry_run"] is True and ask.await_args.kwargs["agent_id"] == "a1"
    assert client.post("/api/v1/guardrails/evaluate", json={"stage": "nowhere", "text": "x"}).status_code == 422
