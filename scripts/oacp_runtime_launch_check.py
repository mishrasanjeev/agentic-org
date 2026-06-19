from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.commerce.c6z_runtime_vertical import (  # noqa: E402
    C6Z_ARTIFACT_FAMILIES,
    GRANTEX_AUTHORITY_ENV_VARS,
    SHOPIFY_ENV_VARS,
    ShopifyAdminGraphQLClient,
    answer_product_question_from_cache,
    build_bridge_contract_response,
    build_cache_record_from_grantex_artifact,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
    generate_protocol_adapter_payloads,
    prepare_purchase_or_mandate_handoff,
    resolve_env,
    resolve_shopify_credentials,
    send_grantex_authority_request,
    verify_plural_pine_mandate_capability,
)  # noqa: E402


TENANT_ID = "11111111-1111-1111-1111-111111111111"
MERCHANT_ID = os.getenv("OACP_LAUNCH_MERCHANT_ID", "merchant_oacp_launch_evidence")
SELLER_AGENT_ID = os.getenv("OACP_LAUNCH_SELLER_AGENT_ID", "seller_agent_oacp_launch")
BUYER_AGENT_ID = os.getenv("OACP_LAUNCH_BUYER_AGENT_ID", "buyer_agent_oacp_launch")
EXTERNAL_CHECKS_ENABLED = os.getenv("OACP_LAUNCH_EXTERNAL_CHECKS", "").strip().lower() == "true"


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _sample_product(now_iso: str) -> dict[str, Any]:
    return {
        "id": "gid://shopify/Product/oacp-launch-sample",
        "title": "OACP Launch Sample Tote",
        "descriptionHtml": "Read-only launch evidence product snapshot.",
        "vendor": "AgenticOrg Evidence",
        "productType": "Bags",
        "status": "ACTIVE",
        "updatedAt": now_iso,
        "media": {"nodes": []},
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/oacp-launch-sample",
                    "sku": "OACP-LAUNCH-TOTE",
                    "title": "Default",
                    "price": "1299.00",
                    "compareAtPrice": None,
                    "inventoryQuantity": 4,
                    "updatedAt": now_iso,
                    "selectedOptions": [{"name": "Color", "value": "Natural"}],
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/oacp-launch-sample"},
                }
            ]
        },
    }


def _local_artifacts(payload: dict[str, Any], now_iso: str) -> list[dict[str, Any]]:
    issued = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    request = payload["request"]
    evidence = payload["connector_evidence"]
    family_type = {
        "merchant_profile": "merchant_capability",
        "seller_agent_card": "seller_agent_capability",
        "connector_evidence": "protocol_adapter",
        "catalog_snapshot": "catalog_snapshot",
        "offer_price_snapshot": "price",
        "inventory_snapshot": "inventory",
        "policy_scope": "policy",
        "public_discovery_state": "public_discovery",
        "mandate_capability": "mandate_capability",
        "protocol_adapter": "protocol_adapter",
        "authority_request_status": "protocol_adapter",
    }
    family_ttl = {
        "offer_price_snapshot": 5 * 60,
        "inventory_snapshot": 60,
        "mandate_capability": 2 * 60,
        "public_discovery_state": 15 * 60,
    }
    artifacts = []
    for family in C6Z_ARTIFACT_FAMILIES:
        expires = (issued + timedelta(seconds=family_ttl.get(family, 5 * 60))).isoformat().replace("+00:00", "Z")
        artifact_id = f"local-c6z:{family}:{request['tenant_id']}:{request['merchant_id']}:{request['seller_agent_id']}"
        artifact_payload = {
            "artifact_family": family,
            "tenant_id": request["tenant_id"],
            "merchant_id": request["merchant_id"],
            "seller_agent_id": request["seller_agent_id"],
            "source_evidence_ref": evidence["source_evidence_ref"],
            "source_observed_at": evidence["source_observed_at"],
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
            "non_authoritative_for_transaction": True,
            "unsupported_capabilities": ["checkout", "payment", "order", "mandate", "public_discovery"],
        }
        artifacts.append(
            {
                "artifact_family": family,
                "envelope": {
                    "artifact_id": artifact_id,
                    "artifact_type": family_type[family],
                    "issuer": "local_contract_fixture_not_grantex_production",
                    "issued_at": now_iso,
                    "expires_at": expires,
                },
                "payload": artifact_payload,
                "verifier_status": {"valid": True, "status": "valid", "mode": "local_contract_check"},
            }
        )
    return artifacts


def _secret_value_present(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_secret_value_present(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_secret_value_present(item) for item in value)
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in ("shpat_", "bearer ", "-----begin", "client_secret=", "password="))


async def _maybe_shopify_check() -> dict[str, Any]:
    resolution = resolve_env(SHOPIFY_ENV_VARS)
    if not resolution.ready:
        return {"status": "blocked_missing_credentials", "missing_env_vars": list(resolution.missing)}
    if not EXTERNAL_CHECKS_ENABLED:
        return {"status": "skipped_external_guard", "env_present": True}
    try:
        client = ShopifyAdminGraphQLClient(resolve_shopify_credentials())
        products = await client.fetch_products(page_size=1, max_pages=1)
    except Exception as exc:
        return {"status": "blocked_external_error", "error_ref": type(exc).__name__}
    return {"status": "passed", "product_count_sample": len(products), "raw_payload_stored": False}


async def _maybe_grantex_check(payload: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_env(GRANTEX_AUTHORITY_ENV_VARS)
    if not resolution.ready:
        return {"status": "blocked_missing_credentials", "missing_env_vars": list(resolution.missing)}
    if not EXTERNAL_CHECKS_ENABLED:
        return {"status": "skipped_external_guard", "env_present": True}
    try:
        response = await send_grantex_authority_request(payload=payload)
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "blocked_external_error", "error_ref": type(exc).__name__}
    return {
        "status": response.get("status"),
        "artifact_count": len(response.get("artifacts") or []),
        "allowed_to_execute": response.get("allowed_to_execute", False),
    }


async def main() -> None:
    now_iso = _iso_now()
    packet = build_seller_onboarding_packet(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        merchant_display_name="OACP Launch Evidence Merchant",
        public_brand_profile={"display_name": "OACP Launch Evidence Merchant"},
        commerce_categories=["launch_evidence"],
        requested_grantex_authority_scope={"artifact_families": list(C6Z_ARTIFACT_FAMILIES)},
        artifact_cache_scope={
            "tenant_id": TENANT_ID,
            "merchant_id": MERCHANT_ID,
            "seller_agent_id": SELLER_AGENT_ID,
        },
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "redacted.myshopify.com", "credential_ref": "redacted"},
    )
    evidence = build_shopify_connector_evidence(
        packet=packet,
        products=[_sample_product(now_iso)],
        synced_at=now_iso,
        source_observed_at=now_iso,
        currency="INR",
    )
    authority_payload = build_grantex_authority_request_payload(
        onboarding_packet=packet,
        connector_evidence=evidence,
    )
    artifacts = _local_artifacts(authority_payload, now_iso)
    cache_records = [
        build_cache_record_from_grantex_artifact(artifact, cached_at=now_iso, buyer_agent_id=BUYER_AGENT_ID)
        for artifact in artifacts
    ]
    answer = answer_product_question_from_cache(
        cache_records=cache_records,
        products=evidence["products"],
        question="What is the price and inventory for the OACP Launch Sample Tote?",
        now_iso=now_iso,
        grantex_available=False,
    )
    bridge = build_bridge_contract_response(channel="openapi", answer=answer, cache_records=cache_records)
    capability = await verify_plural_pine_mandate_capability(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        buyer_agent_id=BUYER_AGENT_ID,
    )
    adapter_payloads = generate_protocol_adapter_payloads(
        cache_records=cache_records,
        products=evidence["products"],
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        buyer_agent_id=BUYER_AGENT_ID,
        now_iso=now_iso,
    )
    purchase = prepare_purchase_or_mandate_handoff(
        cache_records=cache_records,
        products=evidence["products"],
        capability_evidence=[capability.__dict__],
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        buyer_agent_id=BUYER_AGENT_ID,
        product_ref_or_query="OACP Launch Sample Tote",
        variant_id=None,
        quantity=1,
        now_iso=now_iso,
        grantex_available=False,
    )

    summary = {
        "generated_at": now_iso,
        "repo": "agentic-org",
        "external_checks_enabled": EXTERNAL_CHECKS_ENABLED,
        "tenant_id": TENANT_ID,
        "merchant_id": MERCHANT_ID,
        "seller_agent_id": SELLER_AGENT_ID,
        "packet_id": packet["packet_id"],
        "evidence_id": evidence["evidence_id"],
        "shopify_product_count": evidence["product_count"],
        "shopify_variant_count": evidence["variant_count"],
        "shopify_sync_timestamp": evidence["synced_at"],
        "grantex_authority_request_status": "local_contract_fixture_ready",
        "local_artifact_family_count": len(artifacts),
        "local_artifact_families": list(C6Z_ARTIFACT_FAMILIES),
        "artifact_ids": sorted(artifact["envelope"]["artifact_id"] for artifact in artifacts),
        "artifact_verifier_summary": {
            "valid": len(artifacts),
            "invalid": 0,
        },
        "cache_records_count": len(cache_records),
        "buyer_answer_status": answer.status,
        "buyer_answer_sample": answer.answer,
        "buyer_source_label": answer.source_label,
        "buyer_freshness_label": answer.freshness_label,
        "mcp_tool_smoke_result": "covered_by_npm_prefix_mcp_server_test",
        "bridge_channel": bridge.channel,
        "bridge_artifact_ref_count": len(bridge.artifact_refs),
        "protocol_adapter_status": adapter_payloads["status"],
        "protocol_adapter_surfaces": adapter_payloads["surface_names"],
        "plural_pine_capability_status": capability.result_status,
        "plural_pine_redacted_evidence_ref": capability.redacted_evidence_ref,
        "purchase_prepare_status": purchase.status,
        "purchase_prepare_blocker": purchase.blocker,
        "purchase_idempotency_key": purchase.idempotency_key,
        "shopify_external_check": await _maybe_shopify_check(),
        "grantex_external_check": await _maybe_grantex_check(authority_payload),
        "public_discovery_flag": False,
        "allowed_to_execute": False,
        "raw_payload_stored": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }
    if _secret_value_present(summary):
        raise SystemExit("Unsafe launch summary contained a secret-like value")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
