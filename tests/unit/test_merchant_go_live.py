# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant go-live: catalog upload, Grantex enrolment, order limits, policy merge, Shopify credentials and webhooks."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as runtime
from core.commerce import c6z_runtime_vertical as c6z
from core.commerce import grantex_enrolment
from core.commerce.arbiter import ArbiterContext, MerchantLimits, merchant_limits_from_config, rule_merchant_limits
from core.commerce.catalog_upload import (
    CatalogUploadError,
    build_catalog_upload_evidence,
    parse_catalog_upload,
    rows_from_products,
    uploaded_at_of,
)
from core.commerce.oacp_merchant_config import (
    MerchantCommerceConfigError,
    arbiter_limits_complete,
    validate_arbiter_limits,
)
from core.config import settings
from core.logging_config import redact_webhook_path_tokens

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
CSV = (
    "sku,title,description,price,currency,quantity,image_url,url\n"
    "TEA-1,Nilgiri green tea,\"<p>Loose leaf, 100 g</p>\",499,INR,12,https://cdn.example/tea-1.png,https://merchant.example/tea-1\n"
    "TEA-2,Masala chai,,350.50,INR,0,,\n"
)


def _packet(source: str = c6z.CATALOG_UPLOAD_SOURCE_SYSTEM, shop_domain: str | None = None) -> dict[str, Any]:
    return c6z.build_seller_onboarding_packet(
        tenant_id=TENANT,
        merchant_id="tea-corner-1a2b3c",
        seller_agent_id="seller_agent_tea_corner_1a2b3c",
        merchant_display_name="Tea Corner",
        public_brand_profile={"display_name": "Tea Corner", "return_policy_summary": "Returns within 7 days."},
        commerce_categories=["tea"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={"merchant_id": "tea-corner-1a2b3c"},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": shop_domain} if shop_domain else {},
        connector_choice=source,
    )


# --- 7: catalog upload --------------------------------------------------------------------


def test_catalog_upload_source_is_always_a_runtime_source() -> None:
    assert c6z.CATALOG_UPLOAD_SOURCE_SYSTEM == "catalog_upload"
    assert c6z.CATALOG_UPLOAD_SOURCE_SYSTEM in c6z.runtime_source_systems()
    c6z.validate_seller_onboarding_packet(_packet())


def test_csv_upload_is_validated_and_normalized() -> None:
    rows = parse_catalog_upload(CSV, "csv")
    assert [row["sku"] for row in rows] == ["TEA-1", "TEA-2"]
    assert rows[0]["description"] == "Loose leaf, 100 g"
    assert rows[0]["price"] == "499.00" and rows[1]["price"] == "350.50"
    assert rows[1]["quantity"] == 0 and rows[1]["image_url"] is None


def test_json_upload_accepts_a_list_or_products_object_and_currency_exponents() -> None:
    item = {"sku": "A-1", "title": "Tea cup", "price": "1200", "currency": "JPY", "quantity": 3}
    assert parse_catalog_upload(json.dumps([item]), "json")[0]["price"] == "1200"
    assert parse_catalog_upload(json.dumps({"products": [item]}), "json")[0]["currency"] == "JPY"
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(json.dumps([{**item, "price": "12.5"}]), "json")
    assert refused.value.errors == [{"row": 1, "column": "price", "message": "has more than 0 decimal places for JPY"}]


def test_upload_errors_name_the_row_and_column() -> None:
    bad = (
        "sku,title,price,currency,quantity,image_url\n"
        "TEA 1,,0,INR,-2,http://cdn.example/x.png\n"
        "TEA-2,Chai,10,USD,1,\n"
        "TEA-2,Chai again,10,INR,1,\n"
    )
    with pytest.raises(CatalogUploadError) as refused:
        parse_catalog_upload(bad, "csv")
    problems = {(item["row"], item["column"]) for item in refused.value.errors}
    assert {(1, "sku"), (1, "title"), (1, "price"), (1, "quantity"), (1, "image_url")} <= problems
    assert (3, "sku") in problems  # repeated sku
    assert (None, "currency") in problems  # mixed currencies


def test_upload_refuses_missing_and_unknown_columns_and_credentials() -> None:
    with pytest.raises(CatalogUploadError, match="missing price"):
        parse_catalog_upload("sku,title,currency,quantity\nA,B,INR,1\n", "csv")
    with pytest.raises(CatalogUploadError, match="unknown columns: cost"):
        parse_catalog_upload("sku,title,price,currency,quantity,cost\nA,B,1,INR,1,2\n", "csv")
    with pytest.raises(CatalogUploadError) as secret:
        parse_catalog_upload("sku,title,price,currency,quantity\nA,shpat_0123456789,1,INR,1\n", "csv")
    assert secret.value.errors[0]["column"] == "title"
    with pytest.raises(CatalogUploadError, match="no products"):
        parse_catalog_upload("sku,title,price,currency,quantity\n", "csv")


def test_upload_evidence_has_honest_lineage_and_a_stable_id() -> None:
    rows = parse_catalog_upload(CSV, "csv")
    first = build_catalog_upload_evidence(
        packet=_packet(), rows=rows, uploaded_at="2026-10-29T08:00:00Z", observed_at="2026-10-29T08:00:00Z"
    )
    again = build_catalog_upload_evidence(
        packet=_packet(), rows=rows, uploaded_at="2026-10-29T08:00:00Z", observed_at="2026-10-29T09:00:00Z"
    )
    assert first["source_system"] == "catalog_upload"
    assert first["merchant_system_source_of_record"] is True
    assert first["evidence_id"] == again["evidence_id"] and first["source_evidence_ref"] == again["source_evidence_ref"]
    assert again["source_observed_at"] == "2026-10-29T09:00:00Z" and again["uploaded_at"] == "2026-10-29T08:00:00Z"
    assert first["product_count"] == 2 and first["currency"] == "INR"
    assert "synthetic_data" not in first
    assert uploaded_at_of(first["products"]) == "2026-10-29T08:00:00Z"
    assert rows_from_products(first["products"]) == rows
    with pytest.raises(CatalogUploadError):
        build_catalog_upload_evidence(
            packet=_packet(c6z.SHOPIFY_SOURCE_SYSTEM, "tea-corner.myshopify.com"),
            rows=rows,
            uploaded_at="2026-10-29T08:00:00Z",
            observed_at="2026-10-29T08:00:00Z",
        )


def test_authority_request_says_the_merchant_is_the_source_of_record() -> None:
    rows = parse_catalog_upload(CSV, "csv")
    evidence = build_catalog_upload_evidence(
        packet=_packet(), rows=rows, uploaded_at="2026-10-29T08:00:00Z", observed_at="2026-10-29T08:00:00Z"
    )
    payload = c6z.build_grantex_authority_request_payload(
        onboarding_packet=_packet(), connector_evidence=evidence, expected_tenant_id=TENANT
    )
    assert payload["request"]["connector_choice"] == "catalog_upload"
    assert payload["request"]["merchant_system_source_of_record"] is True
    assert payload["connector_evidence"]["source_system"] == "catalog_upload"
    assert payload["connector_evidence"]["merchant_system_source_of_record"] is True
    assert "synthetic_data" not in payload["request"]


def test_v6z103_migration_admits_catalog_upload_read_only() -> None:
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z103_catalog_upload_source.py"
    spec = importlib.util.spec_from_file_location("v6_z103_catalog_upload_source", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "v6z103_catalog_upload_source" and len(module.revision) <= 32
    assert module.down_revision == "v6z102_mfa_challenge_generation"
    executed: list[str] = []
    module.op = SimpleNamespace(execute=executed.append)
    module.upgrade()
    assert executed[1].endswith(
        "CHECK (connector_choice IN ('shopify', 'catalog_upload', 'synthetic_demo') AND connector_mode = 'read_only');"
    )
    assert executed[3].endswith(
        "CHECK (source_system IN ('shopify', 'catalog_upload', 'synthetic_demo') AND source_mode = 'read_only');"
    )
    executed.clear()
    module.downgrade()
    assert all("catalog_upload" not in statement for statement in executed)


# --- 1: Grantex enrolment client ------------------------------------------------------------


GRANTEX_ENV = {"GRANTEX_COMMERCE_BASE_URL": "https://grantex.example", "GRANTEX_COMMERCE_INTERNAL_TOKEN": "t" * 40}


@pytest.fixture()
def public_url_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(url: str, **_kwargs: Any) -> None:
        assert url == "https://grantex.example/v1/commerce/oacp/service-tenants"

    monkeypatch.setattr(c6z, "validate_public_url_async", _ok)


def _transport(status: int, body: Any, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_enrol_service_tenant_posts_the_contract_body_and_reads_an_active_enrolment(public_url_ok) -> None:
    seen: list[httpx.Request] = []
    answer = {
        "tenant_id": TENANT,
        "status": "active",
        "merchant_ids": ["tea-corner-1a2b3c"],
        "source_systems": ["catalog_upload"],
        "enrolled_at": "2026-10-29T08:00:00Z",
    }
    result = await c6z.enrol_service_tenant(
        TENANT, "tea-corner-1a2b3c", "catalog_upload", env=GRANTEX_ENV, transport=_transport(200, answer, seen)
    )
    assert result == {
        "status": "active",
        "merchant_ids": ["tea-corner-1a2b3c"],
        "source_systems": ["catalog_upload"],
        "enrolled_at": "2026-10-29T08:00:00Z",
    }
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/v1/commerce/oacp/service-tenants"
    assert json.loads(request.content) == {
        "tenant_id": TENANT,
        "merchant_id": "tea-corner-1a2b3c",
        "source_system": "catalog_upload",
    }
    assert request.headers["authorization"] == "Bearer " + "t" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, {"detail": "Not Found"}, "unavailable"),
        (405, {"detail": "Method Not Allowed"}, "unavailable"),
        (403, {"code": "service_tenant_revoked"}, "revoked"),
        (403, {"error": {"code": "scope_missing"}}, "refused"),
        (500, {"error": "boom"}, "failed"),
        (200, {"status": "pending"}, "failed"),
    ],
)
async def test_enrol_service_tenant_maps_every_grantex_answer_without_raising(
    public_url_ok, status: int, body: Any, expected: str
) -> None:
    result = await c6z.enrol_service_tenant(
        TENANT, "m-1", "shopify", env=GRANTEX_ENV, transport=_transport(status, body)
    )
    assert result["status"] == expected


@pytest.mark.asyncio
async def test_enrol_service_tenant_without_grantex_settings_or_reachability(public_url_ok) -> None:
    assert (await c6z.enrol_service_tenant(TENANT, "m-1", "shopify", env={}))["status"] == "not_configured"

    def unreachable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    result = await c6z.enrol_service_tenant(
        TENANT, "m-1", "shopify", env=GRANTEX_ENV, transport=httpx.MockTransport(unreachable)
    )
    assert result["status"] == "failed" and result["reason"] == "grantex_unreachable:ConnectError"
    with pytest.raises(c6z.C6ZRuntimeValidationError):
        await c6z.enrol_service_tenant(TENANT, "m-1", "synthetic_demo", env=GRANTEX_ENV)


def test_enrolment_view_shows_waiting_states_and_whether_the_merchant_is_named() -> None:
    assert grantex_enrolment.enrolment_view(None, "m-1")["status"] == "not_started"
    waiting = grantex_enrolment.enrolment_view({"status": "unavailable", "checked_at": "x"}, "m-1")
    assert waiting["waiting"] is True and waiting["message"].startswith("Waiting")
    active = {"status": "active", "merchant_ids": ["m-1"], "source_systems": ["shopify"]}
    assert grantex_enrolment.enrolment_view(active, "m-1")["merchant_enrolled"] is True
    assert grantex_enrolment.enrolment_view(active, "m-2")["merchant_enrolled"] is False
    assert grantex_enrolment.enrolled_for(active, "m-1", "shopify")
    assert not grantex_enrolment.enrolled_for(active, "m-1", "catalog_upload")


# --- 5: order limits ------------------------------------------------------------------------


def _context(**overrides: Any) -> ArbiterContext:
    from datetime import UTC, datetime

    base = ArbiterContext(
        tenant_id=TENANT,
        merchant_id="m-1",
        seller_agent_id="s-1",
        buyer_agent_id=None,
        now=datetime(2026, 10, 29, tzinfo=UTC),
        payload={},
        amount_minor_units=40_000,
        currency="INR",
        quantity=1,
        merchant_limits=MerchantLimits(
            max_order_amount_minor_units=50_000,
            currency_allowlist=("INR",),
            max_quantity_per_order=5,
            max_daily_amount_minor_units=100_000,
        ),
    )
    return dataclasses.replace(base, **overrides)


def test_daily_cap_refuses_when_the_days_total_would_pass_it() -> None:
    assert rule_merchant_limits(_context()) == (True, "within_merchant_limits")
    assert rule_merchant_limits(_context(daily_committed_amount_minor_units=60_000)) == (
        True,
        "within_merchant_limits",
    )
    assert rule_merchant_limits(_context(daily_committed_amount_minor_units=60_001)) == (
        False,
        "daily_amount_cap_exceeded",
    )


def test_daily_cap_is_read_from_provider_policy() -> None:
    limits = merchant_limits_from_config(
        {
            "provider_policy": {
                "arbiter": {
                    "max_order_amount_minor_units": 50_000,
                    "max_daily_amount_minor_units": 100_000,
                    "currency_allowlist": ["inr"],
                    "max_quantity_per_order": 5,
                }
            }
        }
    )
    assert limits is not None and limits.max_daily_amount_minor_units == 100_000
    assert limits.currency_allowlist == ("INR",)


def test_order_limits_are_validated_and_never_defaulted() -> None:
    complete = {
        "max_order_amount_minor_units": 50_000,
        "max_daily_amount_minor_units": 100_000,
        "max_quantity_per_order": 5,
        "currency_allowlist": ["INR"],
    }
    validate_arbiter_limits(complete)
    assert arbiter_limits_complete({"arbiter": complete})
    assert not arbiter_limits_complete({})
    assert not arbiter_limits_complete({"arbiter": {**complete, "max_daily_amount_minor_units": None}})
    for bad in (
        {**complete, "max_daily_amount_minor_units": 10_000},
        {**complete, "max_order_amount_minor_units": 0},
        {**complete, "max_order_amount_minor_units": True},
        {**complete, "max_quantity_per_order": "many"},
        {**complete, "currency_allowlist": "INR"},
        {**complete, "currency_allowlist": ["RUPEES"]},
    ):
        with pytest.raises(MerchantCommerceConfigError):
            validate_arbiter_limits(bad)


# --- 4: provider_policy merge ---------------------------------------------------------------


def test_provider_policy_save_merges_and_null_removes() -> None:
    stored = {
        "arbiter": {"max_order_amount_minor_units": 50_000, "currency_allowlist": ["INR"]},
        "ucp": {"executor": "shopify_permalink", "permalink_endpoint": "https://merchant.example/cart"},
        "provider_owned_execution": True,
        "acp": {"checkout_enabled": True},
    }
    incoming = {
        "provider_owned_execution": True,
        "grantex_executes_payment": False,
        "ucp": {"currency": "INR"},
        "acp": None,
    }
    merged = runtime.merge_provider_policy(stored, incoming)
    assert merged["arbiter"] == stored["arbiter"]
    assert merged["ucp"] == {
        "executor": "shopify_permalink",
        "permalink_endpoint": "https://merchant.example/cart",
        "currency": "INR",
    }
    assert merged["grantex_executes_payment"] is False
    assert "acp" not in merged
    assert stored["ucp"] == {"executor": "shopify_permalink", "permalink_endpoint": "https://merchant.example/cart"}


# --- 3: no deployment-wide Shopify credentials for tenants ----------------------------------


class _NoConnectorSession:
    async def execute(self, _statement: Any) -> Any:
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


SHOPIFY_ENV = {
    "SHOPIFY_SHOP_DOMAIN": "operator-store.myshopify.com",
    "SHOPIFY_ADMIN_ACCESS_TOKEN": "fixture-operator-token",
    "SHOPIFY_API_VERSION": "2026-04",
}


@pytest.mark.asyncio
async def test_a_tenant_without_its_own_connector_never_reads_the_deployment_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in SHOPIFY_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(settings, "shopify_env_operator_tenant_id", OTHER_TENANT)
    with pytest.raises(HTTPException) as blocked:
        await runtime._resolve_shopify_credentials_for_packet(
            session=_NoConnectorSession(),
            tenant_id=runtime._tenant_uuid(TENANT),
            packet=_packet(c6z.SHOPIFY_SOURCE_SYSTEM, "operator-store.myshopify.com"),
        )
    assert blocked.value.status_code == 424
    assert blocked.value.detail["status"] == "blocked_missing_shopify_credentials"
    assert "missing_env_vars" not in blocked.value.detail


@pytest.mark.asyncio
async def test_only_the_named_operator_tenant_uses_the_env_store_and_only_for_that_shop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in SHOPIFY_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(settings, "shopify_env_operator_tenant_id", TENANT)
    credentials, source = await runtime._resolve_shopify_credentials_for_packet(
        session=_NoConnectorSession(),
        tenant_id=runtime._tenant_uuid(TENANT),
        packet=_packet(c6z.SHOPIFY_SOURCE_SYSTEM, "operator-store.myshopify.com"),
    )
    assert source == "environment_operator" and credentials.shop_domain == "operator-store.myshopify.com"
    for packet in (
        _packet(c6z.SHOPIFY_SOURCE_SYSTEM, "someone-else.myshopify.com"),
        _packet(c6z.SHOPIFY_SOURCE_SYSTEM),
    ):
        with pytest.raises(HTTPException) as mismatch:
            await runtime._resolve_shopify_credentials_for_packet(
                session=_NoConnectorSession(), tenant_id=runtime._tenant_uuid(TENANT), packet=packet
            )
        assert mismatch.value.status_code == 409
        assert mismatch.value.detail["status"] == "blocked_shop_domain_mismatch"


@pytest.mark.asyncio
async def test_stored_credentials_for_another_store_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    row = SimpleNamespace(
        connector_name="commerce_shopify_tea_corner_1a2b3c",
        config={"merchant_id": "tea-corner-1a2b3c", "shop_domain": "old-store.myshopify.com"},
        credentials_encrypted={"_encrypted": "ciphertext"},
    )

    class _Session:
        async def execute(self, _statement: Any) -> Any:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

    monkeypatch.setattr(
        runtime,
        "decrypt_for_tenant",
        lambda _value: json.dumps({"admin_access_token": "fixture", "shop_domain": "old-store.myshopify.com"}),
    )
    with pytest.raises(HTTPException) as mismatch:
        await runtime._resolve_shopify_credentials_for_packet(
            session=_Session(),
            tenant_id=runtime._tenant_uuid(TENANT),
            packet=_packet(c6z.SHOPIFY_SOURCE_SYSTEM, "new-store.myshopify.com"),
        )
    assert mismatch.value.status_code == 409


# --- 8: per-merchant Shopify webhooks -------------------------------------------------------


def test_webhook_path_token_is_per_tenant_and_merchant_and_redacted_in_access_logs() -> None:
    token = runtime.shopify_webhook_path_token(TENANT, "m-1")
    assert len(token) == 32 and token == runtime.shopify_webhook_path_token(TENANT, "m-1")
    assert token != runtime.shopify_webhook_path_token(TENANT, "m-2")
    assert token != runtime.shopify_webhook_path_token(OTHER_TENANT, "m-1")
    path = runtime.shopify_webhook_path(TENANT, "m-1")
    assert path == f"/api/v1/webhooks/shopify/{TENANT}/{token}"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s "%s %s"', ("1.2.3.4", "POST", path), None)
    redact_webhook_path_tokens(record)
    assert token not in str(record.args) and f"/api/v1/webhooks/shopify/{TENANT}/[redacted]" in str(record.args)


def _signed(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


@pytest.mark.asyncio
async def test_public_webhook_verifies_with_the_merchants_own_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "merchant-webhook-secret-0001"
    row = SimpleNamespace(
        config={"merchant_id": "m-1", "shop_domain": "tea-corner.myshopify.com", "webhook_secret_configured": True},
        credentials_encrypted={"_encrypted": "ciphertext"},
    )

    async def rows(_session: Any, _tid: Any) -> list[Any]:
        return [row]

    recorded: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> dict[str, Any]:
        recorded.append(kwargs)
        return {"stale_packets_marked": 1}

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(runtime, "get_tenant_session", lambda _tid: _Ctx())
    monkeypatch.setattr(runtime, "_shopify_connector_rows", rows)
    monkeypatch.setattr(runtime, "decrypt_for_tenant", lambda _v: json.dumps({"webhook_secret": secret}))
    monkeypatch.setattr(runtime, "_record_shopify_product_webhook", record)
    body = b'{"id": 1}'
    token = runtime.shopify_webhook_path_token(TENANT, "m-1")
    headers = {"x-shopify-shop-domain": "tea-corner.myshopify.com", "x-shopify-hmac-sha256": _signed(body, secret)}

    assert await runtime.receive_shopify_webhook_for_path(
        tenant_id=TENANT, path_token=token, raw_body=body, headers=headers
    ) == {"stale_packets_marked": 1}
    assert recorded[0]["merchant_ids"] == ["m-1"]
    # A deployment-wide secret, another merchant's token, another shop or a bad signature all fail.
    for path_token, hdrs in (
        (token, {**headers, "x-shopify-hmac-sha256": _signed(body, "deployment-wide-secret")}),
        (runtime.shopify_webhook_path_token(TENANT, "m-2"), headers),
        (runtime.shopify_webhook_path_token(OTHER_TENANT, "m-1"), headers),
        (token, {**headers, "x-shopify-shop-domain": "other.myshopify.com"}),
        ("not-a-token", headers),
    ):
        assert (
            await runtime.receive_shopify_webhook_for_path(
                tenant_id=TENANT, path_token=path_token, raw_body=body, headers=hdrs
            )
            is None
        )
    assert len(recorded) == 1


# --- Shopify: add the webhook secret without the token Shopify shows once ------------------------

SHOP = "tea-corner.myshopify.com"
MERCHANT = "tea-corner-1a2b3c"


class _Ctx:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _saved_row(**overrides: Any) -> SimpleNamespace:
    values = {
        "connector_name": runtime._shopify_connector_config_name(MERCHANT),
        "config": {
            "merchant_id": MERCHANT,
            "shop_domain": SHOP,
            "credential_source": "admin_access_token",
            "granted_scopes": [],
            "webhook_secret_configured": False,
        },
        "credentials_encrypted": {"_encrypted": "ciphertext"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _saved_vault(monkeypatch: pytest.MonkeyPatch, row: Any, vault: Any) -> None:
    async def load(_session: Any, _tid: Any, merchant_id: str) -> Any:
        assert merchant_id == MERCHANT
        return row

    def decrypt(_value: str) -> str:
        if isinstance(vault, Exception):
            raise vault
        return json.dumps(vault)

    monkeypatch.setattr(runtime, "get_tenant_session", lambda _tid: _Ctx())
    monkeypatch.setattr(runtime, "_load_shopify_connector_config_row", load)
    monkeypatch.setattr(runtime, "decrypt_for_tenant", decrypt)


def _request(shop: str = SHOP, **extra: Any) -> runtime.ShopifyConnectorCredentialRequest:
    return runtime.ShopifyConnectorCredentialRequest(merchant_id=MERCHANT, shop_domain=shop, **extra)


@pytest.mark.asyncio
async def test_a_blank_token_reuses_the_saved_token_for_the_same_store(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {**_saved_row().config, "credential_source": "oauth_code_exchange", "granted_scopes": ["read_products"]}
    row = _saved_row(config=config)
    _saved_vault(monkeypatch, row, {"admin_access_token": "fixture-saved-token-0001", "shop_domain": SHOP})
    credentials, source, scopes = await runtime._saved_shopify_credentials(
        runtime._tenant_uuid(TENANT), _request(f"https://{SHOP.upper()}/")
    )
    assert (credentials.shop_domain, credentials.admin_access_token) == (SHOP, "fixture-saved-token-0001")
    # The saved connection's own facts stay as they were.
    assert source == "oauth_code_exchange" and scopes == ["read_products"]


@pytest.mark.parametrize(
    ("row", "vault", "shop"),
    [
        # No saved connection.
        (None, {}, SHOP),
        # Another store than the one saved.
        ("own", {"admin_access_token": "fixture-saved-token-0001", "shop_domain": SHOP}, "another-store.myshopify.com"),
        # The tenant-wide legacy row is never this merchant's saved token.
        ("legacy", {"admin_access_token": "fixture-saved-token-0001", "shop_domain": SHOP}, SHOP),
        # The vault names another store than the row, holds no token, or cannot be read.
        ("own", {"admin_access_token": "fixture-saved-token-0001", "shop_domain": "old-store.myshopify.com"}, SHOP),
        ("own", {"shop_domain": SHOP}, SHOP),
        ("own", ValueError("unreadable"), SHOP),
    ],
)
@pytest.mark.asyncio
async def test_without_a_saved_token_for_that_store_a_token_is_required(
    monkeypatch: pytest.MonkeyPatch, row: Any, vault: Any, shop: str
) -> None:
    saved = {"own": _saved_row(), "legacy": _saved_row(connector_name="shopify"), None: None}[row]
    _saved_vault(monkeypatch, saved, vault)
    with pytest.raises(HTTPException) as required:
        await runtime._saved_shopify_credentials(runtime._tenant_uuid(TENANT), _request(shop))
    assert required.value.status_code == 400
    assert required.value.detail["error"] == "shopify_token_required"
    assert "fixture-saved-token-0001" not in json.dumps(required.value.detail)


@pytest.mark.asyncio
async def test_only_the_setup_page_keeps_a_saved_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the saved token is read only when the caller asks to keep it")

    monkeypatch.setattr(runtime, "_saved_shopify_credentials", refuse)
    # The runtime credentials route is unchanged: a blank token means the OAuth code exchange.
    with pytest.raises(HTTPException) as oauth:
        await runtime.upsert_shopify_connector_credentials(_request(), tenant_id=TENANT)
    assert oauth.value.detail["error"] == "shopify_oauth_material_required"
    # OAuth material alongside keep_saved_token is not a blank token either.
    with pytest.raises(HTTPException) as still_oauth:
        await runtime.save_shopify_connector_credentials(
            _request(oauth_code="code-example"), tenant_id=TENANT, keep_saved_token=True
        )
    assert still_oauth.value.detail["error"] == "shopify_oauth_material_required"


def test_the_setup_request_takes_a_blank_token_and_refuses_a_short_one() -> None:
    from pydantic import ValidationError

    from api.v1 import commerce_merchant_setup as setup_api

    for blank in (None, "", "   "):
        assert setup_api.ShopifyConnectRequest(shop_domain=SHOP, admin_access_token=blank).admin_access_token is None
    assert setup_api.ShopifyConnectRequest(shop_domain=SHOP).admin_access_token is None
    token = setup_api.ShopifyConnectRequest(shop_domain=SHOP, admin_access_token=" fixture-admin-token-0001 ")
    assert token.admin_access_token == "fixture-admin-token-0001"
    assert "fixture-admin-token-0001" not in repr(token)
    with pytest.raises(ValidationError):
        setup_api.ShopifyConnectRequest(shop_domain=SHOP, admin_access_token="short")


@pytest.mark.asyncio
async def test_connect_shopify_asks_to_keep_the_saved_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import commerce_merchant_setup as setup_api

    saved: list[tuple[Any, str, bool]] = []

    async def save(body: Any, *, tenant_id: str, keep_saved_token: bool = False) -> dict[str, Any]:
        saved.append((body, tenant_id, keep_saved_token))
        return {"status": "shopify_connector_configured", "shop_domain": SHOP, "webhook_status": "configured"}

    async def config_row(_session: Any, _tenant: str, _merchant: str) -> Any:
        return object()

    async def sync(_merchant: str, tenant_id: str) -> dict[str, Any]:
        return {"sync": {"product_count": 1}}

    async def view(_tenant: str, _merchant: str) -> dict[str, Any]:
        return {"catalog": {"status": "ready"}}

    monkeypatch.setattr(setup_api, "get_tenant_session", lambda _tid: _Ctx())
    monkeypatch.setattr(setup_api, "_config_row", config_row)
    monkeypatch.setattr(setup_api, "sync_catalog", sync)
    monkeypatch.setattr(setup_api, "setup_view", view)
    monkeypatch.setattr(runtime, "save_shopify_connector_credentials", save)
    secret = "merchant-webhook-secret-0001"
    result = await setup_api.connect_shopify(
        MERCHANT, setup_api.ShopifyConnectRequest(shop_domain=SHOP, webhook_secret=secret), TENANT
    )
    body, tenant_id, keep = saved[0]
    assert keep is True and tenant_id == TENANT
    assert (body.shop_domain, body.admin_access_token, body.webhook_secret) == (SHOP, None, secret)
    assert result["connection"]["webhook_status"] == "configured" and secret not in json.dumps(result)
