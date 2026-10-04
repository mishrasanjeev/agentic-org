# SPDX-License-Identifier: Apache-2.0
"""The audit chain endpoints: the head and backlog, and a verification, for an audit reader."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant
from api.route_enforcement import enforce_route_metadata
from api.v1 import audit as api
from core.governance import audit_chain

TENANT = uuid.uuid4()


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01", "role": "auditor"}
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


def test_the_chain_reads_need_the_audit_scope():
    client = TestClient(_app(["agents:write"]))
    assert client.get("/api/v1/audit/chain").status_code == 403
    assert client.get("/api/v1/audit/chain/verify").status_code == 403


def test_status_reports_the_head_and_the_backlog(monkeypatch):
    picture = {"enabled": False, "head": {"seq": 12, "hash": "a" * 64, "sealed_at": None}, "unsealed": 3}
    status = AsyncMock(return_value=picture)
    monkeypatch.setattr(audit_chain, "status", status)
    client = TestClient(_app(["agenticorg:admin"]))
    resp = client.get("/api/v1/audit/chain")
    assert resp.status_code == 200 and resp.json() == picture
    status.assert_awaited_once_with(TENANT)


def test_verify_runs_from_a_sequence_under_a_limit(monkeypatch):
    result = audit_chain.Verification(
        tenant_id=TENANT, head=audit_chain.Head(seq=12, hash="a" * 64), unsealed=0, checked_from=5
    )
    result.checked_to = 12
    result.verified = 8
    verify = AsyncMock(return_value=result)
    monkeypatch.setattr(audit_chain, "verify", verify)
    client = TestClient(_app(["agenticorg:admin"]))
    resp = client.get("/api/v1/audit/chain/verify", params={"from_seq": 5, "limit": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "verified" and body["verified"] == 8 and body["first_break"] is None
    verify.assert_awaited_once_with(TENANT, from_seq=5, limit=100, expected=None)
    verify.reset_mock()
    anchored = {"expected_seq": 12, "expected_hash": "a" * 64}
    assert client.get("/api/v1/audit/chain/verify", params=anchored).status_code == 200
    verify.assert_awaited_once_with(TENANT, from_seq=1, limit=10_000, expected=audit_chain.Head(seq=12, hash="a" * 64))
    assert client.get("/api/v1/audit/chain/verify", params={"expected_seq": 12}).status_code == 422
    assert client.get("/api/v1/audit/chain/verify", params={"expected_hash": "a" * 64}).status_code == 422
    assert (
        client.get("/api/v1/audit/chain/verify", params={"expected_seq": 12, "expected_hash": "xyz"}).status_code == 422
    )
    assert client.get("/api/v1/audit/chain/verify", params={"from_seq": 0}).status_code == 422
    assert client.get("/api/v1/audit/chain/verify", params={"limit": 0}).status_code == 422
