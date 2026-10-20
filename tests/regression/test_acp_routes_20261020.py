# SPDX-License-Identifier: Apache-2.0
"""G33/G34 routes: /acp checkout sessions and the feed export are flag-gated (404 off), scope-checked
(acp:read / acp:write), answer ACP Error objects, and every write records its declared ledger event,
including refusals answered before any business write."""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api import route_enforcement
from api.route_enforcement import enforce_route_metadata, is_commerce_write_route, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR
from api.v1 import commerce_acp
from core.commerce.acp import checkout as acp
from core.commerce.ledger import current_request_scope
from core.config import settings
from tests import acp_fixtures as af

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000f1"))


def _app(scopes: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.tenant_id = TENANT
        request.state.scopes = scopes
        request.state.auth_mode = "api_key"
        request.state.api_key_name = "platform-key"
        request.state.user_sub = "apikey:ao_sk_platform"
        request.state.api_key_id = "00000000-0000-4000-8000-00000000a001"
        return await call_next(request)

    app.include_router(commerce_acp.router)
    app.include_router(commerce_acp.runtime_router, prefix="/api/v1")
    return app


@pytest.fixture()
def ledger(monkeypatch):
    async def _no_rate_limit(_request, _meta):
        return None

    monkeypatch.setattr(route_enforcement, "_check_rate_limit", _no_rate_limit)
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    events: list[dict] = []

    async def _record_own(**kwargs):
        scope = current_request_scope()
        if scope is not None:
            scope.recorded.add(kwargs["event"])
        events.append(kwargs)

    monkeypatch.setattr(commerce_acp, "record_commerce_event_in_own_transaction", _record_own)
    return events


def test_checkout_routes_answer_404_while_the_flag_is_off(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", False)
    client = TestClient(_app(["acp:read", "acp:write"]))
    assert client.post("/acp/checkout_sessions", json={}).status_code == 404
    assert client.get("/acp/checkout_sessions/chk_1").status_code == 404
    assert ledger == []


def test_feed_answers_404_while_its_flag_is_off(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "acp_feed_enabled", False)
    client = TestClient(_app(["agenticorg:admin"]))
    from api import deps

    app = client.app
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    assert client.get("/api/v1/commerce/runtime/acp/feed", params={"merchant_id": "m"}).status_code == 404


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({}, 400, "missing_api_version"),
        ({"API-Version": "2026-01-30"}, 400, "unsupported_api_version"),
        ({"API-Version": "2026-04-17"}, 400, "idempotency_key_required"),
    ],
)
def test_header_refusals_are_acp_errors_and_ledgered(monkeypatch, ledger, headers, status, code) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    monkeypatch.setattr(commerce_acp, "runtime_factory", lambda: af.runtime(TENANT))
    client = TestClient(_app(["acp:read", "acp:write"]))
    response = client.post(
        "/acp/checkout_sessions", json=af.create_body(), headers={"Host": "shop.merchant.example", **headers}
    )
    assert response.status_code == status
    assert response.json()["code"] == code and af.errors("Error", response.json()) == []
    assert response.headers["request-id"]
    assert [e["event"] for e in ledger] == ["commerce.acp.checkout.create"]
    assert ledger[0]["outcome"] == "refused"


def test_signature_enforce_refuses_with_401(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    monkeypatch.setattr(commerce_acp, "runtime_factory", lambda: af.runtime(TENANT, mode="enforce"))
    client = TestClient(_app(["acp:read", "acp:write"]))
    response = client.post(
        "/acp/checkout_sessions",
        json=af.create_body(),
        headers={"Host": "shop.merchant.example", "API-Version": "2026-04-17", "Idempotency-Key": "k"},
    )
    assert response.status_code == 401 and response.json()["code"] == "invalid_signature"


def test_an_invalid_signature_mode_fails_closed(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    monkeypatch.setattr(commerce_acp, "runtime_factory", lambda: af.runtime(TENANT, mode="maybe"))
    client = TestClient(_app(["acp:read", "acp:write"]))
    response = client.get(
        "/acp/checkout_sessions/chk_1", headers={"Host": "shop.merchant.example", "API-Version": "2026-04-17"}
    )
    assert response.status_code == 401


def test_write_routes_need_acp_write(monkeypatch, ledger) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    monkeypatch.setattr(commerce_acp, "runtime_factory", lambda: af.runtime(TENANT))
    client = TestClient(_app(["acp:read"]))
    response = client.post("/acp/checkout_sessions", json=af.create_body(), headers={"Host": "shop.merchant.example"})
    assert response.status_code == 403


def test_route_table_declares_ledger_events_and_scope_family() -> None:
    writes = []
    for route in commerce_acp.router.routes:
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
        method = next(iter(route.methods))
        assert meta["auth_required"] and meta["tenant_required"]
        assert required_scopes_for(meta["scope"], method) == (("acp:read",) if method == "GET" else ("acp:write",))
        if method != "GET":
            writes.append(route.path)
            assert meta["audit_event"] == acp.EVENTS[acp.OPERATIONS[_operation(route.path)]]
            assert is_commerce_write_route(route.path, method)
    assert sorted(writes) == [
        "/acp/checkout_sessions",
        "/acp/checkout_sessions/{session_id}",
        "/acp/checkout_sessions/{session_id}/cancel",
        "/acp/checkout_sessions/{session_id}/complete",
    ]


def _operation(path: str) -> str:
    if path.endswith("/complete"):
        return "complete"
    if path.endswith("/cancel"):
        return "cancel"
    return "update" if "{session_id}" in path else "create"


def test_order_webhook_sweep_runs_for_acp_alone(monkeypatch) -> None:
    from core.tasks import ucp_order_webhook_tasks as tasks

    ran: list[bool] = []
    monkeypatch.setattr(tasks, "run_async", lambda coro: (coro.close(), ran.append(True), {"delivered": 0})[2])
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    monkeypatch.setattr(settings, "acp_checkout_enabled", False)
    assert tasks.sweep_order_webhooks() == {"skipped": "checkout_disabled"} and ran == []
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    assert tasks.sweep_order_webhooks() == {"delivered": 0} and ran == [True]
