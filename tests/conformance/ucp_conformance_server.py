# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Local server for the official UCP conformance suite (test harness only; never deployed).

Runs the real UCP REST binding (``api/v1/commerce_ucp.py``) and handlers on a
real Postgres database with test doubles where the suite has no equivalent:

* authentication: every request acts as one synthetic tenant holding
  ``ucp:read``/``ucp:write`` (the suite sends no credential);
* host resolution: every ``Host`` maps to the one synthetic merchant;
* catalog, stock and shipping rates: the suite's flower shop CSV files;
* platform profile fetch: plain HTTP GET (the suite serves its agent profile on
  ``http://localhost``; production refuses anything but public https);
* buyer authority: a synthetic mandate, consent and passport, so the commerce
  arbiter decides on real rules and verified-looking OACP cache records;
* executor: completes every payment except the suite's ``fail_token``;
* payment handler: ``mock_payment_handler`` accepting handler tokens;
* order webhooks: delivered by a background sweep with a throwaway P-256 key;
* ``/.well-known/ucp``: a minimal business profile drawn from the capability
  registry (the profile publisher owns the real one);
* ``/testing/simulate-shipping/{order_id}``: the suite's simulation hook,
  behind ``Simulation-Secret``.

    AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:test@localhost:55570/agenticorg \\
      python tests/conformance/ucp_conformance_server.py --data <flower_shop dir> --port 8182 --secret <secret>
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hmac
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("AGENTICORG_ENV", "test")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

TENANT = "7f3c9a52-2d41-4e8b-9b1a-0c5d6e7f8a90"
MERCHANT = "merchant.example"
SERVING_HOST = "conformance.merchant.example"
SELLER = "seller-01"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_app(data_dir: Path, secret: str, version: str) -> FastAPI:  # noqa: C901 - one wiring function
    from api import route_enforcement
    from api.route_enforcement import enforce_route_metadata
    from api.v1 import commerce_ucp
    from core.cases.push import PushSettings
    from core.commerce import executors
    from core.commerce.payment_handlers import Allowance, register_payment_handler
    from core.commerce.ucp import handlers, webhooks
    from core.commerce.ucp.capabilities import (
        PaymentHandlerEntry,
        publish_to_profile_registry,
    )
    from core.commerce.ucp.httpsig import LocalEs256Signer
    from core.commerce.ucp.items import CatalogItem
    from core.commerce.ucp.merchant import merchant_ucp_from_config
    from core.commerce.ucp.quantity import EACH_UNIT
    from core.commerce.ucp.shopify_reads import DeliveryRate
    from core.config import settings
    from tests import commerce_arbiter_fixtures as fx

    settings.ucp_checkout_enabled = True
    settings.ucp_profile_enabled = True
    settings.ucp_versions = version
    settings.route_enforcement_mode = "enforce"
    os.environ["AGENTICORG_UCP_VERSIONS"] = version

    async def _no_rate_limit(_request: Any, _meta: Any) -> None:
        return None

    route_enforcement._check_rate_limit = _no_rate_limit  # type: ignore[assignment]

    config = {
        "merchant_id": MERCHANT,
        "seller_agent_id": SELLER,
        "merchant_display_name": "Conformance Flower Shop",
        "offline_pos_stores": [],
        "payment_providers": [],
        "provider_policy": {
            "arbiter": {
                "max_order_amount_minor_units": 10**9,
                "currency_allowlist": ["USD"],
                "max_quantity_per_order": 10**6,
            },
            "ucp": {
                "currency": "USD",
                "executor": "conformance_executor",
                "links": [
                    {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
                    {"type": "terms_of_service", "url": "https://merchant.example/terms"},
                ],
                "fulfillment": {
                    "shipping": True,
                    "pickup": False,
                    "free_shipping_min_subtotal_minor_units": 10000,
                },
            },
        },
    }
    merchant = merchant_ucp_from_config(config, tenant_id=TENANT, serving_host=SERVING_HOST, signing_available=True)
    assert merchant is not None

    prices = {row["id"]: row for row in _rows(data_dir / "products.csv")}
    stock = {row["product_id"]: int(row["quantity"]) for row in _rows(data_dir / "inventory.csv")}
    stock.setdefault("gardenias", 0)
    prices.setdefault("gardenias", {"id": "gardenias", "title": "Gardenias", "price": "2000", "image_url": ""})

    class _Catalog:
        async def lookup(self, _merchant: Any, item_ids: Any) -> dict[str, CatalogItem]:
            found = {}
            for item_id in item_ids:
                row = prices.get(item_id)
                if row is None:
                    continue
                found[item_id] = CatalogItem(
                    id=item_id,
                    title=row["title"],
                    price_minor_units=int(row["price"]),
                    currency="USD",
                    inventory=stock.get(item_id),
                    image_url=row.get("image_url") or None,
                    quantity_unit=EACH_UNIT,
                )
            return found

    rates = []
    for row in _rows(data_dir / "shipping_rates.csv"):
        world = row["country_code"] == "default"
        rates.append(
            DeliveryRate(
                id=row["id"],
                title=row["title"],
                description=None,
                amount_minor_units=int(row["price"]),
                currency="USD",
                countries=() if world else (row["country_code"],),
                rest_of_world=world,
            )
        )

    async def _rates(_merchant: Any) -> list[DeliveryRate]:
        return rates

    class _Resolver:
        async def resolve(self, *, host: str, tenant_id: str) -> Any:
            return merchant if tenant_id == TENANT else None

    async def _fetch(url: str) -> Any:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def _authority(_merchant: Any, _state: Any) -> dict[str, Any]:
        now = datetime.now(UTC)
        later = (now + timedelta(hours=1)).isoformat()
        return {
            "mandate_ref": "conformance-mandate",
            "mandate_hash": "d" * 64,
            "mandate_expires_at": later,
            "consent_ref": "conformance-consent",
            "consent_status": "active",
            "passport_ref": "conformance-passport",
            "passport_max_amount_minor_units": 10**9,
            "passport_currency": "USD",
            "passport_expires_at": later,
        }

    async def _scope(_merchant: Any) -> tuple[list[Any], dict[str, Any]]:
        return fx.cache_records(TENANT), config

    class _MockHandler:
        name = "com.example.mock_payment"

        def entries(self, _merchant: Any, version_: str) -> list[PaymentHandlerEntry]:
            return [
                PaymentHandlerEntry(
                    name=self.name,
                    id="mock_payment_handler",
                    version=version_,
                    schema="https://example.com/ucp/mock-payment/config.json",
                    spec=None,
                    available_instruments=({"type": "card"},),
                )
            ]

        def accepted_credentials(self, _version: str) -> tuple[str, ...]:
            # A test handler in PCI scope: it takes raw cards as well as tokens.
            return ("token", "card", "pan")

        def allowance(self, _merchant: Any) -> Allowance:
            return Allowance(max_amount_minor_units=None, currency=None)

        def request_constraints(self, *_args: Any) -> None:
            return None

    register_payment_handler(_MockHandler())
    publish_to_profile_registry()

    class _Executor:
        name = "conformance_executor"

        async def execute(self, session: Any, decision: Any) -> Any:
            token = str((session.instruments[0].credential or {}).get("token") or "")
            if token == "fail_token":
                return executors.ExecutionResult(
                    status="failed", error_code="payment_failed", error_message="The payment was declined."
                )
            return executors.ExecutionResult(status="completed", provider_order_ref=f"sim-{decision.decision_id}")

    executors.register_executor(_Executor())

    private_key = ec.generate_private_key(ec.SECP256R1())
    from core.commerce.merchant_keys import public_jwk_for as _jwk

    signer = LocalEs256Signer(private_key, kid=_jwk(private_key)["kid"])
    webhooks._validated_url = lambda url: url  # the suite's webhook receiver is http://localhost
    # The suite expects a retry within five seconds: a short backoff and timeout for the harness only.
    dispatcher = webhooks.OrderWebhookDispatcher(
        transport_factory=lambda: httpx.AsyncHTTPTransport(),
        signer=lambda: signer,
        settings=PushSettings(base_delay_seconds=0.5, timeout_seconds=2.0),
    )

    def _runtime() -> handlers.UcpRuntime:
        return handlers.UcpRuntime(
            resolver=_Resolver(),
            catalog_for=lambda _m: _Catalog(),
            rates_for=lambda _m: _rates,
            arbiter_scope=_scope,
            signer=lambda: signer,
            fetcher=_fetch,
            authority_provider=_authority,
            after_commit=lambda tenant: asyncio.get_running_loop().create_task(dispatcher.dispatch_tenant(tenant)),
            supported_versions=(version,),
            published=commerce_ucp._published_capabilities,
        )

    commerce_ucp.runtime_factory = _runtime
    commerce_ucp.kick_dispatch = lambda _tenant: None

    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant_id = TENANT
        request.state.scopes = ["ucp:read", "ucp:write"]
        request.state.auth_mode = "api_key"
        request.state.api_key_name = "conformance-platform"
        return await call_next(request)

    app.include_router(commerce_ucp.router)

    # The real profile route (api/v1/ucp_discovery.py) on a published-merchant serve state built here
    # instead of the attestation, publication and public-discovery artifact flow.
    from api.v1 import ucp_discovery
    from core.commerce.merchant_keys import public_jwk_for
    from core.commerce.ucp.profile import UcpMerchantServeState

    jwk = public_jwk_for(private_key)
    serve = UcpMerchantServeState(
        host=SERVING_HOST,
        tenant_id=TENANT,
        merchant_id=MERCHANT,
        seller_agent_id=SELLER,
        namespace=None,
        jwks=(jwk,),
        locations=(),
        ucp={},
        config=config,
    )

    async def _serving(_request: Any) -> Any:
        return serve

    ucp_discovery._serving_merchant = _serving
    app.include_router(ucp_discovery.well_known_router)

    @app.middleware("http")
    async def _local_endpoint(request: Request, call_next: Any) -> Any:
        # The profile advertises https://<serving host>/ucp; the suite reaches this process over plain HTTP.
        response = await call_next(request)
        if not request.url.path.startswith("/.well-known/ucp"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        local = str(request.base_url).rstrip("/").encode()
        body = body.replace(f"https://{SERVING_HOST}".encode(), local)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag")}
        return Response(content=body, status_code=response.status_code, headers=headers, media_type="application/json")

    @app.post("/testing/simulate-shipping/{order_id}")
    async def simulate_shipping(order_id: str, request: Request) -> JSONResponse:
        offered = request.headers.get("simulation-secret", "")
        if not offered or not hmac.compare_digest(offered.encode(), secret.encode()):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        from core.database import get_tenant_session
        from core.models.commerce_ucp import CommerceOrderRow

        async with get_tenant_session(uuid.UUID(TENANT)) as session:
            row = await session.get(CommerceOrderRow, order_id)
            if row is None:
                return JSONResponse({"detail": "not found"}, status_code=404)
            lines = [{"id": li["id"], "quantity": li["quantity"]["total"]} for li in row.snapshot["line_items"]]
        await handlers.apply_order_change(
            tenant_id=TENANT,
            order_id=order_id,
            fulfillment_event={"type": "shipped", "line_items": lines, "tracking_number": "SIM-TRACK"},
            adjustment=None,
            now=datetime.now(UTC),
        )
        asyncio.get_running_loop().create_task(dispatcher.dispatch_tenant(uuid.UUID(TENANT)))
        return JSONResponse({"status": "shipped"})

    @app.on_event("startup")
    async def _sweeper() -> None:
        async def _loop() -> None:
            while True:
                try:
                    await dispatcher.dispatch_tenant(uuid.UUID(TENANT))
                # enterprise-gate: broad-except-ok reason=test-harness-sweep-keeps-running
                except Exception as exc:  # pragma: no cover - harness diagnostics
                    print("sweep error", type(exc).__name__, exc)
                await asyncio.sleep(0.5)

        asyncio.get_running_loop().create_task(_loop())

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--port", type=int, default=8182)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--version", default="2026-04-08")
    args = parser.parse_args()
    app = build_app(Path(args.data), args.secret, args.version)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
