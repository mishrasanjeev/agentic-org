# SPDX-License-Identifier: Apache-2.0
"""G21 routes: the /ucp REST binding is flag-gated (404 off), scope-checked (ucp:read / ucp:write), and every
write records its declared commerce ledger event, including refusals answered without a business write."""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api import route_enforcement
from api.route_enforcement import enforce_route_metadata, is_commerce_write_route, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR
from api.v1 import commerce_ucp
from core.commerce.ledger import current_request_scope
from core.commerce.ucp import handlers
from core.config import settings
from tests import ucp_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000b1"))


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.tenant_id = TENANT
        request.state.scopes = scopes
        request.state.auth_mode = "api_key"
        request.state.api_key_name = "platform-key"
        return await call_next(request)

    app.include_router(commerce_ucp.router)
    return app


@pytest.fixture()
def ledger(monkeypatch):
    async def _no_rate_limit(_request, _meta):
        return None

    monkeypatch.setattr(route_enforcement, "_check_rate_limit", _no_rate_limit)
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    events: list[dict] = []

    async def _record_own(**kwargs):
        # Stands in for the own-transaction ledger write and marks the request scope like the real one.
        scope = current_request_scope()
        if scope is not None:
            scope.recorded.add(kwargs["event"])
        events.append({**kwargs, "in_session": False})

    monkeypatch.setattr(commerce_ucp, "record_commerce_event_in_own_transaction", _record_own)
    return events


def test_routes_answer_404_while_the_flag_is_off(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    client = TestClient(_app(["ucp:read", "ucp:write"]))
    assert client.post("/ucp/checkout-sessions", json={}).status_code == 404
    assert client.get("/ucp/orders/ord_1").status_code == 404
    assert ledger == []


def test_unknown_host_is_404_and_the_refusal_is_ledgered(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(commerce_ucp, "runtime_factory", lambda: uf.runtime(TENANT))
    client = TestClient(_app(["ucp:read", "ucp:write"]))
    response = client.post(
        "/ucp/checkout-sessions", json=uf.create_body(), headers={"Host": "unknown.example", "UCP-Agent": 'profile="x"'}
    )
    assert response.status_code == 404
    assert [e["event"] for e in ledger] == ["commerce.ucp.checkout.create"]
    assert ledger[0]["outcome"] == "refused" and ledger[0]["in_session"] is False


def test_missing_ucp_agent_is_400_invalid_profile_url(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(commerce_ucp, "runtime_factory", lambda: uf.runtime(TENANT))
    client = TestClient(_app(["ucp:read", "ucp:write"]))
    response = client.post("/ucp/checkout-sessions", json=uf.create_body(), headers={"Host": uf.HOST})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_profile_url"
    version = client.post(
        "/ucp/checkout-sessions",
        json=uf.create_body(),
        headers={"Host": uf.HOST, "UCP-Agent": f'profile="{uf.PLATFORM_PROFILE}"; version="2099-01-01"'},
    )
    assert version.status_code == 422 and version.json()["code"] == "version_unsupported"
    assert all(e["event"] == "commerce.ucp.checkout.create" for e in ledger) and len(ledger) == 2


def test_capabilities_incompatible_is_a_200_error_body(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    profile = uf.platform_profile(capabilities=["dev.ucp.shopping.order"])
    monkeypatch.setattr(commerce_ucp, "runtime_factory", lambda: uf.runtime(TENANT, profile=profile))
    client = TestClient(_app(["ucp:read", "ucp:write"]))
    response = client.post(
        "/ucp/checkout-sessions",
        json=uf.create_body(),
        headers={"Host": uf.HOST, "UCP-Agent": f'profile="{uf.PLATFORM_PROFILE}"'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ucp"]["status"] == "error" and body["messages"][0]["code"] == "capabilities_incompatible"
    assert "id" not in body


def test_write_routes_need_ucp_write(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    monkeypatch.setattr(commerce_ucp, "runtime_factory", lambda: uf.runtime(TENANT))
    client = TestClient(_app(["ucp:read"]))
    response = client.post("/ucp/checkout-sessions", json=uf.create_body(), headers={"Host": uf.HOST})
    assert response.status_code == 403


def test_route_table_declares_ledger_events_and_scope_family() -> None:
    writes = []
    for route in commerce_ucp.router.routes:
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
        method = next(iter(route.methods))
        assert meta["auth_required"] and meta["tenant_required"]
        assert required_scopes_for(meta["scope"], method) == (("ucp:read",) if method == "GET" else ("ucp:write",))
        if method != "GET":
            writes.append(route.path)
            assert meta["audit_event"].startswith("commerce.ucp.")
            assert is_commerce_write_route(route.path, method)
    assert len(writes) == 7
    for route in commerce_ucp.runtime_router.routes:
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
        if next(iter(route.methods)) != "GET":
            assert meta["audit_event"].startswith("commerce.ucp.")
    assert set(handlers.EVENTS.values()) == {
        getattr(r.endpoint, ROUTE_METADATA_ATTR)["audit_event"]
        for r in commerce_ucp.router.routes
        if getattr(r.endpoint, ROUTE_METADATA_ATTR)["audit_event"]
    }


@pytest.mark.asyncio
async def test_idempotency_store_failure_refuses_with_503(monkeypatch) -> None:
    from core.commerce.ucp import idempotency

    async def _broken(**_kwargs):
        raise idempotency.IdempotencyUnavailableError("database down")

    monkeypatch.setattr(idempotency, "claim", _broken)
    response = await handlers.run("create_checkout", uf.runtime(TENANT), uf.request(TENANT, uf.create_body(), key="k1"))
    assert response.status == 503
    assert response.body["messages"][0]["code"] == "idempotency_unavailable"


@pytest.mark.asyncio
async def test_idempotency_in_progress_is_409(monkeypatch) -> None:
    from core.commerce.ucp import idempotency

    async def _pending(**_kwargs):
        return idempotency.Claim(claimed=False, conflict="idempotency_key_in_progress")

    monkeypatch.setattr(idempotency, "claim", _pending)
    response = await handlers.run("create_checkout", uf.runtime(TENANT), uf.request(TENANT, uf.create_body(), key="k1"))
    assert response.status == 409
    assert response.body["messages"][0]["code"] == "idempotency_key_in_progress"
