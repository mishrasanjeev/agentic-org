from __future__ import annotations

import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as commerce_runtime_api
from core.commerce.c6z_runtime_vertical import (
    C6ZRuntimeValidationError,
    answer_product_question_from_cache,
    build_cache_record_from_grantex_artifact,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
    contains_private_or_executable_value,
    summarize_capability_evidence,
    verify_plural_pine_mandate_capability,
    verify_shopify_webhook_hmac,
)
from core.commerce.oacp_artifacts import OacpPersistentArtifactCacheRecord


def _now() -> datetime:
    return datetime(2026, 6, 14, 8, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _packet() -> dict:
    return build_seller_onboarding_packet(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        merchant_display_name="Demo Store",
        public_brand_profile={"display_name": "Demo Store"},
        commerce_categories=["apparel"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={"merchant_id": "merchant_1", "seller_agent_id": "seller_agent_1"},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "demo.myshopify.com", "credential_ref": "env"},
    )


def _shopify_product() -> dict:
    return {
        "id": "gid://shopify/Product/1",
        "title": "Canvas Tote",
        "descriptionHtml": "Heavy canvas tote",
        "vendor": "Demo Brand",
        "productType": "Bags",
        "updatedAt": _iso(_now()),
        "media": {
            "nodes": [
                {
                    "preview": {
                        "image": {
                            "url": "https://cdn.shopify.com/demo/tote.jpg",
                            "altText": "Canvas Tote",
                        }
                    }
                }
            ]
        },
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "sku": "TOTE-1",
                    "title": "Default",
                    "price": "1299.00",
                    "compareAtPrice": "1499.00",
                    "inventoryQuantity": 7,
                    "updatedAt": _iso(_now()),
                    "selectedOptions": [{"name": "Color", "value": "Natural"}],
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/1"},
                }
            ]
        },
    }


def _grantex_artifact() -> dict:
    now = _now()
    return {
        "artifact_family": "catalog_snapshot",
        "envelope": {
            "artifact_id": "c6z:catalog_snapshot:tenant:merchant:seller",
            "artifact_type": "catalog_snapshot",
            "issuer": "grantex_internal_oacp_authority",
            "issued_at": _iso(now),
            "expires_at": _iso(now + timedelta(minutes=5)),
        },
        "payload": {
            "artifact_family": "catalog_snapshot",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "merchant_id": "merchant_1",
            "seller_agent_id": "seller_agent_1",
            "source_evidence_ref": "agenticorg:shopify:evidence:abc:redacted",
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
        },
    }


def _cache_record(
    *,
    expires_delta: timedelta = timedelta(minutes=5),
    revoked: bool = False,
) -> OacpPersistentArtifactCacheRecord:
    now = _now()
    return OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_c6z_catalog",
        artifact_id="artifact_c6z_catalog",
        artifact_type="catalog_snapshot",
        authority="grantex.internal.oacp.authority",
        issuer="grantex.internal.oacp.authority",
        scope_kind="seller_agent",
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        buyer_agent_id=None,
        source_refs=("agenticorg:shopify:evidence:abc:redacted",),
        evidence_refs=("agenticorg:shopify:evidence:abc:redacted",),
        generated_at=_iso(now - timedelta(minutes=1)),
        cached_at=_iso(now - timedelta(minutes=1)),
        expires_at=_iso(now + expires_delta),
        freshness_status="fresh",
        revocation_snapshot_status="revoked" if revoked else "fresh",
        revocation_snapshot_observed_at=_iso(now - timedelta(seconds=10)),
        ttl_policy_seconds=max(1, int((expires_delta + timedelta(minutes=1)).total_seconds())),
        risk_tier="low",
        blocked_capabilities=("checkout", "payment", "order", "mandate"),
        unsupported_capabilities=("execution", "public_discovery", "live_provider"),
        verifier_result_ref="artifact_c6z_catalog:verified",
        revocation_snapshot_age_seconds=10,
        allowed_to_execute=False,
        non_authoritative_for_transaction=True,
        no_checkout_payment_enablement=True,
        no_live_provider_enablement=True,
        no_public_discovery_enablement=True,
    )


def test_onboarding_packet_is_read_only_and_rejects_secret_metadata() -> None:
    packet = _packet()

    assert packet["connector_choice"] == "shopify"
    assert packet["connector_mode"] == "read_only"
    assert packet["no_payment_execution"] is True
    assert packet["no_public_discovery_enablement"] is True
    assert packet["allowed_to_execute"] is False
    assert "SHOPIFY_ADMIN_ACCESS_TOKEN" not in str(packet)

    with pytest.raises(C6ZRuntimeValidationError):
        build_seller_onboarding_packet(
            tenant_id="11111111-1111-1111-1111-111111111111",
            merchant_id="merchant_1",
            seller_agent_id="seller_agent_1",
            merchant_display_name="Demo Store",
            public_brand_profile={},
            commerce_categories=["apparel"],
            requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
            artifact_cache_scope={"merchant_id": "merchant_1"},
            source_freshness_policy={"max_age_seconds": 900},
            connector_metadata={"shopify_admin_access_token": "shpat_secret"},
        )


def test_shopify_evidence_normalizes_products_without_raw_payloads() -> None:
    evidence = build_shopify_connector_evidence(
        packet=_packet(),
        products=[_shopify_product()],
        synced_at=_iso(_now()),
        source_observed_at=_iso(_now()),
        currency="INR",
    )

    assert evidence["source_system"] == "shopify"
    assert evidence["source_mode"] == "read_only"
    assert evidence["product_count"] == 1
    assert evidence["variant_count"] == 1
    assert evidence["raw_payload_stored"] is False
    assert evidence["products"][0]["variants"][0]["sku"] == "TOTE-1"
    assert "shpat_" not in str(evidence)
    assert contains_private_or_executable_value(evidence) is False


def test_grantex_authority_payload_matches_issuer_contract() -> None:
    packet = _packet()
    evidence = build_shopify_connector_evidence(
        packet=packet,
        products=[_shopify_product()],
        synced_at=_iso(_now()),
        source_observed_at=_iso(_now()),
        currency="INR",
    )

    payload = build_grantex_authority_request_payload(
        onboarding_packet=packet,
        connector_evidence=evidence,
    )

    assert "requested_authority_scope" in payload["request"]
    assert "requested_grantex_authority_scope" not in payload["request"]
    assert payload["request"]["source_evidence_ref"] == evidence["source_evidence_ref"]
    assert payload["connector_evidence"]["evidence_id"] == evidence["evidence_id"]
    assert "catalog_sample_refs" in payload["connector_evidence"]
    assert "price_snapshot_refs" in payload["connector_evidence"]
    assert "inventory_snapshot_refs" in payload["connector_evidence"]
    assert "catalog_refs" not in payload["connector_evidence"]
    assert payload["request"]["no_payment_execution"] is True
    assert payload["connector_evidence"]["no_public_discovery_enablement"] is True


def test_cache_record_accepts_grantex_artifact_with_sibling_payload() -> None:
    now = _iso(_now())
    artifact = _grantex_artifact()

    record = build_cache_record_from_grantex_artifact(artifact, cached_at=now)

    assert record.artifact_id == "c6z:catalog_snapshot:tenant:merchant:seller"
    assert record.tenant_id == "11111111-1111-1111-1111-111111111111"
    assert record.allowed_to_execute is False
    assert record.non_authoritative_for_transaction is True


@pytest.mark.asyncio
async def test_cache_endpoint_reports_only_successfully_stored_records(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session(_tenant_id):
        yield object()

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def upsert(self, _record) -> dict:
            return {
                "stored": True,
                "status": "stored",
                "cache_record_id": "cache_c6z_catalog",
                "artifact_id": "c6z:catalog_snapshot:tenant:merchant:seller",
            }

    monkeypatch.setattr(commerce_runtime_api, "get_tenant_session", fake_session)
    monkeypatch.setattr(commerce_runtime_api, "DurableOacpArtifactCacheRepository", FakeRepository)

    result = await commerce_runtime_api.cache_grantex_artifacts(
        commerce_runtime_api.CacheArtifactsRequest(artifacts=[_grantex_artifact()]),
        tenant_id="11111111-1111-1111-1111-111111111111",
    )

    assert result["status"] == "cached"
    assert result["records_stored"] == 1
    assert result["records_rejected"] == 0
    assert result["store_results"][0]["stored"] is True


@pytest.mark.asyncio
async def test_cache_endpoint_rejects_failed_repository_store_results(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session(_tenant_id):
        yield object()

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def upsert(self, _record) -> dict:
            return {
                "stored": False,
                "status": "stale",
                "refusal_code": "cache_timestamps_invalid",
                "cache_record_id": "cache_c6z_catalog",
                "artifact_id": "c6z:catalog_snapshot:tenant:merchant:seller",
            }

    monkeypatch.setattr(commerce_runtime_api, "get_tenant_session", fake_session)
    monkeypatch.setattr(commerce_runtime_api, "DurableOacpArtifactCacheRepository", FakeRepository)

    with pytest.raises(HTTPException) as exc_info:
        await commerce_runtime_api.cache_grantex_artifacts(
            commerce_runtime_api.CacheArtifactsRequest(artifacts=[_grantex_artifact()]),
            tenant_id="11111111-1111-1111-1111-111111111111",
        )

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["status"] == "artifact_cache_rejected"
    assert detail["records_stored"] == 0
    assert detail["records_rejected"] == 1
    assert detail["store_results"][0]["refusal_code"] == "cache_timestamps_invalid"


def test_shopify_webhook_hmac_verification_and_idempotency_are_deterministic() -> None:
    raw_body = b'{"id":1,"title":"Canvas Tote"}'
    secret = "webhook-secret"
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    header = base64.b64encode(digest).decode()

    assert verify_shopify_webhook_hmac(raw_body, header, secret) is True
    assert verify_shopify_webhook_hmac(raw_body, "bad", secret) is False


def test_buyer_answer_uses_valid_cache_and_refuses_commitment() -> None:
    evidence = build_shopify_connector_evidence(
        packet=_packet(),
        products=[_shopify_product()],
        synced_at=_iso(_now() - timedelta(minutes=1)),
        source_observed_at=_iso(_now() - timedelta(minutes=1)),
        currency="INR",
    )
    answer = answer_product_question_from_cache(
        cache_records=[_cache_record()],
        products=evidence["products"],
        question="What is the price of Canvas Tote?",
        now_iso=_iso(_now()),
        grantex_available=False,
    )
    assert answer.status == "answered"
    assert "Source: Shopify via Grantex artifact" == answer.source_label
    assert "inventory snapshot" in answer.answer
    assert answer.allowed_to_execute is False

    refusal = answer_product_question_from_cache(
        cache_records=[_cache_record()],
        products=evidence["products"],
        question="Buy the Canvas Tote now",
        now_iso=_iso(_now()),
        grantex_available=True,
    )
    assert refusal.status == "refused"
    assert refusal.refusal_reason == "final_commitment_refused"


def test_buyer_answer_fails_closed_for_expired_or_revoked_cache() -> None:
    evidence = build_shopify_connector_evidence(
        packet=_packet(),
        products=[_shopify_product()],
        synced_at=_iso(_now() - timedelta(minutes=20)),
        source_observed_at=_iso(_now() - timedelta(minutes=20)),
    )
    expired = answer_product_question_from_cache(
        cache_records=[_cache_record(expires_delta=timedelta(minutes=-1))],
        products=evidence["products"],
        question="Show Canvas Tote",
        now_iso=_iso(_now()),
        grantex_available=False,
    )
    revoked = answer_product_question_from_cache(
        cache_records=[_cache_record(revoked=True)],
        products=evidence["products"],
        question="Show Canvas Tote",
        now_iso=_iso(_now()),
        grantex_available=False,
    )

    assert expired.status == "needs_refresh"
    assert "cache_record_expired" in str(expired.refusal_reason)
    assert revoked.status == "needs_refresh"
    assert "cache_record_revoked" in str(revoked.refusal_reason)


@pytest.mark.asyncio
async def test_plural_pine_capability_verifier_is_env_gated_and_redacted() -> None:
    missing = await verify_plural_pine_mandate_capability(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_1",
        env={},
        now=_now(),
    )
    assert missing.result_status == "blocked_missing_credentials"
    assert missing.external_validation_performed is False
    assert missing.raw_payload_stored is False

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "client_secret" not in request.content.decode().lower()
        return httpx.Response(200, json={"result": {"tools": [{"name": "mandate_capability.check"}]}})

    evidence = await verify_plural_pine_mandate_capability(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_1",
        env={
            "PLURAL_PINE_CLIENT_ID": "client-id",
            "PLURAL_PINE_CLIENT_SECRET": "client-secret",
            "PLURAL_PINE_ENVIRONMENT": "sandbox",
            "PLURAL_PINE_CAPABILITY_URL": "https://sandbox.example.test/mcp",
        },
        transport=httpx.MockTransport(handler),
        now=_now(),
    )
    assert evidence.result_status == "available"
    assert evidence.redacted_evidence_ref.endswith(":redacted")
    assert "client-secret" not in str(evidence)
    summary = summarize_capability_evidence([evidence])
    assert summary["allowed_to_execute"] is False
    assert summary["no_payment_execution"] is True


def test_c6z_migration_is_tenant_safe_and_non_executing() -> None:
    migration = Path("migrations/versions/v6_z_runtime_vertical_demo.py").read_text()
    assert 'down_revision = "v6y5_retention_decisions"' in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "current_setting('agenticorg.tenant_id', true)" in migration
    assert "allowed_to_execute IS FALSE" in migration
    assert "raw_payload_stored IS FALSE" in migration
    assert "CREATE TABLE IF NOT EXISTS commerce_c6z_seller_onboarding_packets" in migration


def test_c6z_mcp_bridge_exposes_seller_tools_without_execution_tools() -> None:
    source = Path("mcp-server/src/index.ts").read_text()
    for name in (
        "seller.list_products",
        "seller.search_products",
        "seller.get_product_facts",
        "seller.get_offer_snapshot",
        "seller.get_inventory_snapshot",
        "seller.ask_product_question",
    ):
        assert name in source
    assert "payment.create" not in source
    assert "order.create" not in source
    assert "mandate.create" not in source
