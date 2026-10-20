# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G21 wiring: the transaction capabilities and payment handlers appear in /.well-known/ucp only while the
checkout flag is on, negotiation uses what the profile advertises, and Host resolution goes through the
attested serving-host index of a published merchant in the caller's tenant."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core.commerce.ucp import merchant as merchant_mod
from core.commerce.ucp.capabilities import publish_to_profile_registry
from core.commerce.ucp.profile import UcpMerchantServeState, build_profile
from core.commerce.ucp.registry import UcpServeContext, ucp_registry
from core.config import settings
from tests import ucp_txn_fixtures as uf
from tests.ucp_schema_validation import PROFILE_SCHEMAS, assert_ucp_valid

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000c1"))
JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    "kid": "k1",
    "use": "sig",
    "alg": "ES256",
}


def _serve(config=None) -> UcpMerchantServeState:
    return UcpMerchantServeState(
        host=uf.HOST,
        tenant_id=TENANT,
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        namespace=None,
        jwks=(JWK,),
        locations=(),
        ucp={},
        config=config if config is not None else uf.merchant_config(TENANT),
    )


@pytest.fixture(autouse=True)
def _published():
    publish_to_profile_registry()


def test_profile_lists_transaction_capabilities_only_while_the_flag_is_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    off = build_profile(_serve(), version="2026-04-08", versions=("2026-04-08",))
    assert "dev.ucp.shopping.checkout" not in off["ucp"]["capabilities"]
    assert off["ucp"]["payment_handlers"] == {}
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    on = build_profile(_serve(), version="2026-04-08", versions=("2026-04-08",))
    caps = on["ucp"]["capabilities"]
    assert {
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.cart",
        "dev.ucp.shopping.order",
        "dev.ucp.shopping.fulfillment",
        "dev.ucp.shopping.buyer_consent",
        "dev.ucp.common.loyalty",
    } <= set(caps)
    assert caps["dev.ucp.shopping.buyer_consent"][0]["extends"] == "dev.ucp.shopping.checkout"
    handler = on["ucp"]["payment_handlers"]["ai.agenticorg.payment.plural_tokenized"][0]
    assert handler["config"]["allowance"] == {"max_amount": 50000, "currency": "INR"}
    r25 = build_profile(_serve(), version="2026-08-25", versions=("2026-08-25",))
    assert r25["ucp"]["capabilities"]["dev.ucp.shopping.buyer_consent"][0]["extends"] == [
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.cart",
    ]
    assert "dev.ucp.common.payment.split_payments" in r25["ucp"]["capabilities"]


def test_extensions_without_merchant_settings_are_not_advertised(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    config = uf.merchant_config(TENANT, loyalty=None, mandate_capability=None)
    profile = build_profile(_serve(config), version="2026-04-08", versions=("2026-04-08",))
    assert "dev.ucp.common.loyalty" not in profile["ucp"]["capabilities"]
    assert profile["ucp"]["payment_handlers"] == {}


def test_published_names_drive_negotiation(monkeypatch) -> None:
    from api.v1 import commerce_ucp

    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    names = commerce_ucp._published_capabilities(uf.merchant(TENANT), "2026-04-08")
    assert "dev.ucp.shopping.checkout" in names
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    assert "dev.ucp.shopping.checkout" not in commerce_ucp._published_capabilities(uf.merchant(TENANT), "2026-04-08")
    ctx = UcpServeContext(
        host=uf.HOST, version="2026-04-08", tenant_id=TENANT, merchant_id="m", seller_agent_id="s", namespace=None
    )
    assert "dev.ucp.shopping.checkout" not in ucp_registry.served_capability_names(ctx)


@pytest.mark.asyncio
async def test_host_resolution_needs_the_attested_binding_in_the_callers_tenant(monkeypatch) -> None:
    from core.commerce.ucp import profile, store

    binding = SimpleNamespace(
        host=uf.HOST, tenant_id=TENANT, merchant_id="merchant.example", seller_agent_id="seller-01"
    )
    row = SimpleNamespace(
        tenant_id=TENANT,
        **{k: v for k, v in uf.merchant_config(TENANT).items() if k not in ("tenant_id",)},
        public_publishing={},
    )

    async def _binding(host):
        return binding if host == uf.HOST else None

    async def _row(**_kw):
        return row

    async def _records(**_kw):
        return []

    monkeypatch.setattr(store, "find_serving_binding", _binding)
    monkeypatch.setattr(store, "load_merchant_config_row", _row)
    monkeypatch.setattr(store, "load_shared_cache_records", _records)
    monkeypatch.setattr(profile, "merchant_serve_state", lambda r, **_kw: _serve())
    resolver = merchant_mod.PublishedHostResolver()
    resolved = await resolver.resolve(host=f"{uf.HOST.upper()}:443", tenant_id=TENANT)
    assert resolved is not None and resolved.serving_host == uf.HOST and resolved.signing_available
    assert await resolver.resolve(host=uf.HOST, tenant_id=str(uuid.uuid4())) is None
    assert await resolver.resolve(host="other.example", tenant_id=TENANT) is None
    assert await resolver.resolve(host="127.0.0.1", tenant_id=TENANT) is None
    monkeypatch.setattr(profile, "merchant_serve_state", lambda r, **_kw: None)
    assert await resolver.resolve(host=uf.HOST, tenant_id=TENANT) is None


def test_profile_with_transactions_validates_against_the_profile_schema(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    document = build_profile(_serve(), version="2026-08-25", versions=("2026-08-25",))
    assert_ucp_valid(document, PROFILE_SCHEMAS["2026-08-25"])
    leaf = build_profile(_serve(), version="2026-04-08", versions=("2026-08-25", "2026-04-08"))
    assert_ucp_valid(leaf, PROFILE_SCHEMAS["2026-04-08"])
    assert document["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"] == f"https://{uf.HOST}/ucp"


@pytest.mark.asyncio
async def test_checkout_items_resolve_by_the_catalog_variant_id() -> None:
    from core.commerce.ucp.catalog import ucp_variant_id
    from core.commerce.ucp.items import ProductEvidenceCatalog

    products = uf.products(TENANT)
    variant_id = ucp_variant_id(products[0]["variants"][0])

    async def _load(_m):
        return products

    found = await ProductEvidenceCatalog(_load).lookup(uf.merchant(TENANT), [variant_id, uf.SKU])
    assert found[variant_id].price_minor_units == 2500 and found[uf.SKU].sku == uf.SKU
