"""OACP conformance against a running Grantex: issue -> replay -> verify -> revoke -> refuse.

Skipped unless ``AGENTICORG_GRANTEX_CONFORMANCE_URL`` names a Grantex API
origin (for example a local container at ``http://localhost:3001``). Also
read:

- ``AGENTICORG_GRANTEX_CONFORMANCE_TOKEN``: the internal service token the
  c6z authority route and the OACP artifact routes accept (required);
- ``AGENTICORG_GRANTEX_CONFORMANCE_ISSUER``: the Grantex ``JWT_ISSUER``
  (defaults to the URL).

The Grantex under test needs an active OACP issuer key (``POST
/v1/commerce/oacp/issuer-keys/rotate`` or ``OACP_AUTO_GENERATE_ISSUER_KEY``
outside production), ``OACP_STATUS_LIST_PUBLIC_ENABLED=true`` (otherwise the
list is 404 and every artifact is refused ``revocation_status_unavailable``),
an https ``PUBLIC_BASE_URL`` reachable from this process, and the token's
tenant in ``COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS``.

The verifier is the one the cache endpoint uses, pinned to that issuer and
its ``/.well-known/jwks.json``; ``allow_local_endpoints`` only lets it reach a
loopback http origin for this run.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from core.commerce.c6z_runtime_vertical import (
    C6Z_ARTIFACT_FAMILIES,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
)
from core.commerce.oacp_artifacts import OacpArtifactScope
from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    reset_oacp_trust_caches,
    split_grantex_artifact,
)

BASE_URL = os.getenv("AGENTICORG_GRANTEX_CONFORMANCE_URL", "").strip().rstrip("/")
TOKEN = os.getenv("AGENTICORG_GRANTEX_CONFORMANCE_TOKEN", "").strip()
ISSUER = (os.getenv("AGENTICORG_GRANTEX_CONFORMANCE_ISSUER", "").strip() or BASE_URL).rstrip("/")

pytestmark = pytest.mark.skipif(
    not BASE_URL or not TOKEN,
    reason="set AGENTICORG_GRANTEX_CONFORMANCE_URL and AGENTICORG_GRANTEX_CONFORMANCE_TOKEN to run",
)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _origin(url: str) -> str:
    parts = httpx.URL(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    port = "" if parts.port in (None, default) else f":{parts.port}"
    return f"{parts.scheme}://{parts.host}{port}"


def _verifier() -> OacpGrantexArtifactVerifier:
    return OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=ISSUER,
            jwks_uri=f"{BASE_URL}/.well-known/jwks.json",
            status_list_origins=frozenset({_origin(ISSUER), _origin(BASE_URL)}),
            allow_local_endpoints=True,
        ),
        transport_factory=httpx.AsyncHTTPTransport,
    )


def _authority_payload(tenant_id: str, merchant_id: str, seller_agent_id: str) -> dict[str, Any]:
    now_iso = _iso_now()
    packet = build_seller_onboarding_packet(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        merchant_display_name="Conformance Merchant",
        public_brand_profile={"display_name": "Conformance Merchant"},
        commerce_categories=["conformance"],
        requested_grantex_authority_scope={"artifact_families": list(C6Z_ARTIFACT_FAMILIES)},
        artifact_cache_scope={"tenant_id": tenant_id, "merchant_id": merchant_id, "seller_agent_id": seller_agent_id},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "conformance.merchant.example", "credential_ref": "redacted"},
    )
    product = {
        "id": "gid://shopify/Product/conformance-1",
        "title": "Conformance Tote",
        "descriptionHtml": "Synthetic conformance product.",
        "vendor": "Merchant Example",
        "productType": "Bags",
        "status": "ACTIVE",
        "updatedAt": now_iso,
        "media": {"nodes": []},
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/conformance-1",
                    "sku": "CONF-TOTE",
                    "title": "Default",
                    "price": "100.00",
                    "compareAtPrice": None,
                    "inventoryQuantity": 3,
                    "updatedAt": now_iso,
                    "selectedOptions": [],
                    "inventoryItem": {"id": "gid://shopify/InventoryItem/conformance-1"},
                }
            ]
        },
    }
    evidence = build_shopify_connector_evidence(
        packet=packet,
        products=[product],
        synced_at=now_iso,
        source_observed_at=now_iso,
        currency="INR",
    )
    return build_grantex_authority_request_payload(onboarding_packet=packet, connector_evidence=evidence)


async def _verify_all(verifier: OacpGrantexArtifactVerifier, items: list[dict[str, Any]], tenant_id: str) -> list[dict]:
    results = []
    for item in items:
        envelope, payload = split_grantex_artifact(item)
        results.append(
            await verifier.verify(
                envelope,
                payload,
                expected_scope=OacpArtifactScope(
                    tenant_id=tenant_id,
                    merchant_id=str(payload.get("merchant_id") or ""),
                    seller_agent_id=str(payload.get("seller_agent_id") or ""),
                    buyer_agent_id="",
                ),
                now_iso=_iso_now(),
            )
        )
    return results


async def test_issue_replay_verify_revoke_refuse() -> None:
    reset_oacp_trust_caches()
    tenant_id = str(uuid.uuid4())
    merchant_id = f"merchant_conf_{uuid.uuid4().hex[:8]}"
    seller_agent_id = f"seller_conf_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {TOKEN}"}

    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=20.0) as client:
        issued = await client.post(
            "/v1/commerce/oacp/c6z/authority-requests",
            json=_authority_payload(tenant_id, merchant_id, seller_agent_id),
        )
        issued.raise_for_status()
        artifacts = issued.json()["artifacts"]
        assert artifacts, "Grantex issued no artifacts"

        replayed: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"tenant_id": tenant_id, "merchant_id": merchant_id, "seller_agent_id": seller_agent_id}
            if cursor:
                params["cursor"] = cursor
            page = await client.get("/v1/commerce/oacp/artifacts", params=params)
            page.raise_for_status()
            body = page.json()
            replayed.extend(body["artifacts"])
            cursor = body.get("next_cursor")
            if not cursor:
                break
        issued_ids = {split_grantex_artifact(item)[0].get("artifact_id") for item in artifacts}
        assert all(str(artifact_id).startswith("oacp_art_") for artifact_id in issued_ids)
        replayed_issued = [item for item in replayed if item["envelope"]["artifact_id"] in issued_ids]
        assert {item["envelope"]["artifact_id"] for item in replayed_issued} == issued_ids
        assert all(item["status"] == "issued" for item in replayed_issued)

        verifier = _verifier()
        results = await _verify_all(verifier, artifacts, tenant_id)
        assert all(result["valid"] for result in results), [r.get("refusal_code") for r in results]
        # A replayed copy verifies exactly like the issued one.
        replay_results = await _verify_all(verifier, replayed_issued, tenant_id)
        assert all(result["valid"] for result in replay_results), [r.get("refusal_code") for r in replay_results]

        target = split_grantex_artifact(artifacts[0])[0]["artifact_id"]
        revoked = await client.post(f"/v1/commerce/oacp/artifacts/{target}/revoke", json={"reason_code": "conformance"})
        revoked.raise_for_status()
        revocation = revoked.json()
        assert revocation["artifact_id"] == target
        assert revocation["status"] == "revoked"
        assert revocation["already_revoked"] is False
        revocation_artifact = revocation["revocation_artifact"]
        assert target in revocation_artifact["payload"]["revoked_artifact_ids"]

    reset_oacp_trust_caches()  # do not reuse the status list cached before the revocation
    [after] = await _verify_all(_verifier(), [artifacts[0]], tenant_id)
    assert after["valid"] is False
    assert after["refusal_code"] == "artifact_revoked"
    # The revocation artifact is itself a verifiable OACP artifact.
    [revocation_result] = await _verify_all(_verifier(), [revocation_artifact], tenant_id)
    assert revocation_result["valid"] is True, revocation_result.get("refusal_code")
