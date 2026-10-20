# SPDX-License-Identifier: Apache-2.0
"""G33/G34/G40: the /acp HTTP adapter.

Every checkout route answers only an agent platform's API key, ledgers its refusal under its own event, the
feed export is built from the merchant config, the verified policy snapshot (only while its artifact is usable)
and an attested serving host, and the error-shaping middleware leaves successes and non-JSON errors well formed.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from api import deps, route_enforcement
from api.route_enforcement import enforce_route_metadata
from api.v1 import commerce_acp
from core.commerce.acp import checkout as acp
from core.commerce.ledger import current_request_scope
from core.commerce.ucp import store
from core.config import settings
from tests import acp_fixtures as af
from tests import ucp_fixtures

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000f7"))
KEY_ID = "00000000-0000-4000-8000-00000000a0f7"


def _app(
    *, auth_mode: str = "api_key", key_id: str | None = KEY_ID, scopes: list[str] | None = None, tenant: str = TENANT
) -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.tenant_id = tenant
        request.state.scopes = scopes or ["acp:read", "acp:write"]
        request.state.auth_mode = auth_mode
        request.state.api_key_name = "platform-key"
        request.state.api_key_id = key_id
        return await call_next(request)

    app.include_router(commerce_acp.router)
    app.include_router(commerce_acp.runtime_router, prefix="/api/v1")
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    return app


@pytest.fixture()
def ledger(monkeypatch):
    async def _no_rate_limit(_request, _meta):
        return None

    monkeypatch.setattr(route_enforcement, "_check_rate_limit", _no_rate_limit)
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    monkeypatch.setattr(commerce_acp, "runtime_factory", lambda: af.runtime(TENANT))
    events: list[dict] = []

    async def _record_own(**kwargs):
        scope = current_request_scope()
        if scope is not None:
            scope.recorded.add(kwargs["event"])
        events.append(kwargs)

    monkeypatch.setattr(commerce_acp, "record_commerce_event_in_own_transaction", _record_own)
    return events


# --- checkout routes -----------------------------------------------------------------------------------


def test_build_runtime_wires_the_ucp_runtime_grantex_and_signature_mode(monkeypatch) -> None:
    from api.v1 import commerce_ucp
    from core.commerce.grantex_service import grantex_service_client_from_env

    runtime = commerce_acp.build_runtime()
    assert isinstance(runtime, acp.AcpRuntime)
    assert runtime.ucp.after_commit is commerce_ucp.kick_dispatch
    assert runtime.grantex is grantex_service_client_from_env
    monkeypatch.setattr(settings, "acp_signature_mode", " Enforce ")
    assert runtime.signature_mode() == "enforce"
    monkeypatch.setattr(settings, "acp_signature_mode", None)
    assert runtime.signature_mode() == "log"


@pytest.mark.parametrize(
    ("auth_mode", "key_id"),
    [("jwt", KEY_ID), ("api_key", None), ("api_key", "")],
    ids=["user-token", "no-key", "blank"],
)
def test_only_the_platform_api_key_has_a_platform_identity(ledger, auth_mode, key_id) -> None:
    client = TestClient(_app(auth_mode=auth_mode, key_id=key_id))
    response = client.get("/acp/checkout_sessions/chk_1", headers={"Host": af.uf.HOST, "API-Version": "2026-04-17"})
    assert response.status_code == 403
    assert "API key issued to the agent platform" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "operation"),
    [
        ("/acp/checkout_sessions/chk_1", "update"),
        ("/acp/checkout_sessions/chk_1/complete", "complete"),
        ("/acp/checkout_sessions/chk_1/cancel", "cancel"),
    ],
)
def test_session_write_routes_ledger_their_own_refusals(ledger, path, operation) -> None:
    client = TestClient(_app())
    response = client.post(path, json={}, headers={"Host": af.uf.HOST})
    assert response.status_code == 400 and response.json()["code"] == "missing_api_version"
    assert af.errors("Error", response.json()) == []
    assert [e["event"] for e in ledger] == [acp.EVENTS[acp.OPERATIONS[operation]]]
    assert ledger[0]["outcome"] == "refused"
    assert ledger[0]["payload"]["operation"] == operation and ledger[0]["details"]["http_status"] == 400


def test_get_session_is_not_ledgered(ledger) -> None:
    client = TestClient(_app())
    response = client.get("/acp/checkout_sessions/chk_1", headers={"Host": af.uf.HOST})
    assert response.status_code == 400
    assert ledger == []


# --- product feed --------------------------------------------------------------------------------------

POLICY = {
    "return_policy_summary": "Returns within 30 days.",
    "return_policy_url": "https://merchant.example/returns",
    "accepts_returns": True,
    "return_window_days": 30,
}


def _config_row(
    *, tenant_id: str = ucp_fixtures.TENANT_ID, verified: bool = True, policy_artifact: str = "oacp_art_policy"
):
    attestation = {"verified_at": "2026-10-19T09:00:00Z"} if verified else {}
    return SimpleNamespace(
        tenant_id=tenant_id,
        merchant_id=ucp_fixtures.MERCHANT_ID,
        seller_agent_id=ucp_fixtures.SELLER_AGENT_ID,
        merchant_display_name="Example Store",
        offline_pos_stores=[],
        payment_providers=[],
        provider_policy={
            "ucp": {
                "links": [
                    {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
                    {"type": "terms_of_service", "url": "https://merchant.example/terms"},
                    {"type": "refund_policy", "url": "https://merchant.example/refunds"},
                ],
                "permalink_endpoint": "https://merchant.example/buy",
            },
            "acp": {"checkout_enabled": True, "feed": {"target_country": "IN"}},
        },
        public_publishing={
            "ucp": {
                "serving_host": ucp_fixtures.HOST,
                "attestation": attestation,
                "verified_policy": {"artifact_id": policy_artifact, "policy": POLICY},
            }
        },
    )


@pytest.fixture()
def feed_inputs(monkeypatch):
    state: dict = {
        "row": _config_row(),
        "records": [*ucp_fixtures.catalog_records(), ucp_fixtures.cache_record("policy")],
        "calls": [],
    }

    async def _row(**kwargs):
        state["calls"].append(("row", kwargs))
        return state["row"]

    async def _inputs(**kwargs):
        state["calls"].append(("inputs", kwargs))
        return state["records"], ucp_fixtures.synced_products()

    monkeypatch.setattr(store, "load_merchant_config_row", _row)
    monkeypatch.setattr(store, "load_catalog_inputs", _inputs)
    return state


async def _load(**overrides) -> object:
    from datetime import UTC, datetime

    kwargs = {
        "tenant_id": ucp_fixtures.TENANT_ID,
        "merchant_id": ucp_fixtures.MERCHANT_ID,
        "seller_agent_id": ucp_fixtures.SELLER_AGENT_ID,
        "now": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return await commerce_acp.load_feed(**kwargs)


def _seller(feed) -> dict:
    return feed.products_response()["products"][0]["variants"][0]["seller"]


@pytest.mark.asyncio
async def test_feed_uses_the_verified_policy_and_the_attested_host(monkeypatch, feed_inputs) -> None:
    monkeypatch.setattr(settings, "acp_checkout_enabled", True)
    feed = await _load()
    assert feed.target_country == "IN" and feed.products
    seller = _seller(feed)
    assert seller["name"] == "Example Store"
    links = {link["type"]: link["url"] for link in seller["links"]}
    # The verified return policy wins over the configured refund link.
    assert links["refund_policy"] == "https://merchant.example/returns"
    assert feed.metadata == {} and feed.feed_metadata()["target_country"] == "IN"
    scope = {
        "tenant_id": ucp_fixtures.TENANT_ID,
        "merchant_id": ucp_fixtures.MERCHANT_ID,
        "seller_agent_id": ucp_fixtures.SELLER_AGENT_ID,
    }
    assert feed_inputs["calls"] == [("row", scope), ("inputs", scope)]


@pytest.mark.asyncio
async def test_feed_ignores_a_policy_snapshot_without_a_usable_artifact(feed_inputs) -> None:
    feed_inputs["row"] = _config_row(policy_artifact="oacp_art_retired", verified=False)
    feed = await _load()
    links = {link["type"]: link["url"] for link in _seller(feed)["links"]}
    assert links["refund_policy"] == "https://merchant.example/refunds"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, "other-tenant"], ids=["missing", "other-tenant"])
async def test_feed_for_an_unknown_merchant_is_404(feed_inputs, row) -> None:
    feed_inputs["row"] = None if row is None else _config_row(tenant_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as caught:
        await _load()
    assert caught.value.status_code == 404
    assert [name for name, _ in feed_inputs["calls"]] == ["row"]


def _feed_client(monkeypatch, *, enabled: bool = True) -> TestClient:
    async def _no_rate_limit(_request, _meta):
        return None

    monkeypatch.setattr(route_enforcement, "_check_rate_limit", _no_rate_limit)
    monkeypatch.setattr(settings, "acp_feed_enabled", enabled)
    return TestClient(_app(auth_mode="jwt", scopes=["agenticorg:admin"], tenant=ucp_fixtures.TENANT_ID))


def _export(client: TestClient, fmt: str):
    return client.get(
        "/api/v1/commerce/runtime/acp/feed",
        params={
            "merchant_id": ucp_fixtures.MERCHANT_ID,
            "seller_agent_id": ucp_fixtures.SELLER_AGENT_ID,
            "format": fmt,
        },
    )


def test_feed_export_formats(monkeypatch, feed_inputs) -> None:
    client = _feed_client(monkeypatch)
    as_json = _export(client, "json")
    assert as_json.status_code == 200 and as_json.headers["cache-control"] == "no-store"
    products = as_json.json()["products"]
    assert products and set(as_json.json()) == {"products"}
    skipped = as_json.headers["agenticorg-feed-skipped-rows"]
    assert skipped.isdigit()

    jsonl = _export(client, "jsonl")
    assert jsonl.headers["content-type"].startswith("application/jsonl")
    lines = [json.loads(line) for line in jsonl.text.splitlines()]
    assert [p["id"] for p in lines] == [p["id"] for p in products]

    metadata = _export(client, "metadata")
    assert metadata.json()["id"].startswith("feed_") and metadata.json()["target_country"] == "IN"

    as_csv = _export(client, "csv")
    assert as_csv.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(as_csv.text)))
    assert rows and as_csv.headers["agenticorg-feed-skipped-rows"] == skipped


def test_feed_export_is_409_when_the_verified_artifacts_are_missing(monkeypatch, feed_inputs) -> None:
    feed_inputs["records"] = []
    response = _export(_feed_client(monkeypatch), "json")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("feed_unavailable:")


def test_feed_export_rejects_an_unknown_format(monkeypatch, feed_inputs) -> None:
    assert _export(_feed_client(monkeypatch), "xml").status_code == 422
    assert feed_inputs["calls"] == []


# --- the error-shaping middleware ----------------------------------------------------------------------


def _shaped() -> TestClient:
    app = FastAPI()

    @app.get("/acp/ok")
    async def _ok() -> JSONResponse:
        return JSONResponse({"status": "fine"}, headers={"X-Trace": "t1"})

    @app.get("/acp/plain")
    async def _plain() -> PlainTextResponse:
        return PlainTextResponse("upstream exploded", status_code=502, headers={"Retry-After": "5"})

    @app.get("/acp/empty")
    async def _empty() -> PlainTextResponse:
        return PlainTextResponse("", status_code=503)

    app.add_middleware(commerce_acp.AcpErrorShapeMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def test_successful_acp_answers_pass_through_unchanged() -> None:
    response = _shaped().get("/acp/ok")
    assert response.status_code == 200
    assert response.json() == {"status": "fine"} and response.headers["x-trace"] == "t1"


@pytest.mark.parametrize("path", ["/acp/plain", "/acp/empty"])
def test_non_json_error_bodies_become_acp_errors(path) -> None:
    response = _shaped().get(path)
    body = response.json()
    assert response.status_code in (502, 503)
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(response.content)
    assert {"type", "code", "message"} <= set(body) and af.errors("Error", body) == []
    if path == "/acp/plain":
        assert response.headers["retry-after"] == "5"
