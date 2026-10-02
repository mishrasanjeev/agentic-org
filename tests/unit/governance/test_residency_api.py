# SPDX-License-Identifier: Apache-2.0
"""Residency endpoints: admin-only, validated input, audited changes."""

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
from api.v1 import residency as api
from core.governance.residency import Attestation

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "provider": "gemini",
        "data_region": "IN",
        "in_region": True,
        "no_training": True,
        "evidence_ref": "clause 7.2",
        "attested_by": "admin",
        "attested_at": datetime.now(UTC),
        "expires_at": None,
        "revoked_at": None,
        "revoked_by": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


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
    assert client.get("/api/v1/residency/status").status_code == 403
    assert (
        client.post("/api/v1/residency/attestations", json={"provider": "gemini", "data_region": "IN"}).status_code
        == 403
    )


def test_status_and_list(session_rows):
    session_rows.append(_row())
    client = TestClient(_app(["agenticorg:admin"]))
    with patch.object(
        api.residency, "report_section", AsyncMock(return_value={"control_id": "RES-1", "data_region": "IN"})
    ):
        status = client.get("/api/v1/residency/status")
    assert status.status_code == 200 and status.json()["data_region"] == "IN"
    listed = client.get("/api/v1/residency/attestations")
    assert listed.status_code == 200
    assert listed.json()[0]["provider"] == "gemini" and listed.json()[0]["active"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "gemini", "data_region": "MARS"},
        {"provider": "Not Valid!", "data_region": "IN"},
        {"provider": "gemini", "data_region": "IN", "expires_at": "2020-01-01T00:00:00Z"},
        {"data_region": "IN"},
    ],
)
def test_invalid_input_is_refused_before_any_write(session_rows, body):
    client = TestClient(_app(["agenticorg:admin"]))
    with patch.object(api.residency, "set_attestation", AsyncMock()) as setter:
        response = client.post("/api/v1/residency/attestations", json=body)
    assert response.status_code == 422
    setter.assert_not_called()


def test_set_and_revoke(session_rows):
    placed = Attestation(
        id=str(uuid.uuid4()),
        provider="gemini",
        data_region="IN",
        in_region=True,
        no_training=True,
        evidence_ref="clause 7.2",
        attested_by="admin",
        expires_at=None,
    )
    session_rows.append(_row(id=uuid.UUID(placed.id)))
    client = TestClient(_app(["agenticorg:admin"]))
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    with patch.object(api.residency, "set_attestation", AsyncMock(return_value=placed)) as setter:
        response = client.post(
            "/api/v1/residency/attestations",
            json={
                "provider": "gemini",
                "data_region": "in",
                "in_region": True,
                "no_training": True,
                "evidence_ref": "clause 7.2",
                "expires_at": expires,
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["provider"] == "gemini" and response.json()["active"] is True
    kwargs = setter.await_args.kwargs
    assert kwargs["data_region"] == "IN" and kwargs["no_training"] is True and kwargs["expires_at"].tzinfo is not None
    assert kwargs["actor_id"] == "api_key:apikey:key_01"

    session_rows[0].revoked_at = datetime.now(UTC)
    with patch.object(api.residency, "revoke_attestation", AsyncMock(return_value=placed)) as revoker:
        revoked = client.post(f"/api/v1/residency/attestations/{placed.id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    assert str(revoker.await_args.args[1]) == placed.id

    with patch.object(api.residency, "revoke_attestation", AsyncMock(return_value=None)):
        assert client.post(f"/api/v1/residency/attestations/{uuid.uuid4()}/revoke").status_code == 404


def test_no_attributable_caller_is_refused(session_rows):
    app = _app(["agenticorg:admin"])
    app.state.auth_mode = ""
    app.state.claims = {}
    client = TestClient(app)
    with patch.object(api.residency, "set_attestation", AsyncMock()) as setter:
        response = client.post("/api/v1/residency/attestations", json={"provider": "gemini", "data_region": "IN"})
    assert response.status_code == 403
    setter.assert_not_called()
