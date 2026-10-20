from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

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
from core.commerce.oacp_artifacts import OacpArtifactScope  # noqa: E402
from core.commerce.oacp_grantex_trust import (  # noqa: E402
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    split_grantex_artifact,
)
from core.commerce.oacp_jws import (  # noqa: E402
    OACP_JWS_ALG,
    OACP_JWS_TYP,
    STATUS_LIST_JWT_TYP,
    b64url_encode,
    canonicalize_bytes,
    oacp_payload_hash,
)
from core.commerce.oacp_public_publishing import (  # noqa: E402
    build_public_catalog_html,
    build_public_catalog_snapshot,
    build_public_llms_txt,
    build_public_sitemap_xml,
)
from core.commerce.offline_pos_bridge import (  # noqa: E402
    build_offline_pos_handoff_packet,
    reconcile_offline_pos_confirmation,
    simulate_offline_pos_confirmation,
)


TENANT_ID = "11111111-1111-1111-1111-111111111111"
MERCHANT_ID = os.getenv("OACP_LAUNCH_MERCHANT_ID", "merchant_oacp_launch_evidence")
SELLER_AGENT_ID = os.getenv("OACP_LAUNCH_SELLER_AGENT_ID", "seller_agent_oacp_launch")
BUYER_AGENT_ID = os.getenv("OACP_LAUNCH_BUYER_AGENT_ID", "buyer_agent_oacp_launch")
EXTERNAL_CHECKS_ENABLED = os.getenv("OACP_LAUNCH_EXTERNAL_CHECKS", "").strip().lower() == "true"
WRITE_EVIDENCE = os.getenv("OACP_LAUNCH_WRITE_EVIDENCE", "").strip().lower() == "true"
EVIDENCE_DIR = Path(os.getenv("OACP_LAUNCH_EVIDENCE_DIR", str(REPO_ROOT / "docs" / "reports")))

# The local artifacts are signed with a throwaway ES256 key generated for this
# run and checked through the verifier the cache intake uses
# (OacpGrantexArtifactVerifier: detached JWS, JWKS key, scope, TTL, Token
# Status List). The issuer, JWKS and status list are served in process from a
# synthetic .example origin, not by a Grantex issuer, so a passing local run
# proves the verifier path, not Grantex issuance.
LOCAL_ISSUER = "https://launch-check.issuer.example"
LOCAL_JWKS_URI = f"{LOCAL_ISSUER}/.well-known/jwks.json"
LOCAL_STATUS_LIST_URI = f"{LOCAL_ISSUER}/status/oacp/launch-check"
LOCAL_POLICY_VERSION = "oacp-policy-launch-check"
LOCAL_VERIFICATION_MODE = "local_throwaway_es256_key"
LOCAL_STATUS_LIST_SOURCE = "local_in_process_status_list_all_valid"


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


@dataclass
class LaunchCheckSigner:
    """A throwaway P-256 key for one launch-check run; never written anywhere."""

    kid: str = field(default_factory=lambda: f"oacp-launchcheck-{secrets.token_hex(8)}")
    private_key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))

    def public_jwk(self) -> dict[str, Any]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": b64url_encode(numbers.x.to_bytes(32, "big")),
            "y": b64url_encode(numbers.y.to_bytes(32, "big")),
            "kid": self.kid,
            "alg": OACP_JWS_ALG,
            "use": "sig",
        }

    def sign(self, data: bytes) -> bytes:
        # RFC 7518 section 3.4: the ES256 signature is the 64-byte r || s.
        r, s = decode_dss_signature(self.private_key.sign(data, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def sign_envelope(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Detached compact JWS (RFC 7515 Appendix F) over the RFC 8785 envelope."""
        unsigned = {key: value for key, value in envelope.items() if key != "signature"}
        header = {"alg": OACP_JWS_ALG, "kid": self.kid, "typ": OACP_JWS_TYP}
        header_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{b64url_encode(canonicalize_bytes(unsigned))}".encode("ascii")
        return {**unsigned, "signature": f"{header_b64}..{b64url_encode(self.sign(signing_input))}"}

    def status_list_token(self, *, uri: str, size: int, issued_epoch: int, ttl_seconds: int = 600) -> str:
        """A Token Status List token (draft-ietf-oauth-status-list section 5.1), every entry VALID."""
        raw = bytes((size + 3) // 4)  # 2-bit entries, all 0 (VALID)
        claims = {
            "sub": uri,
            "iss": LOCAL_ISSUER,
            "iat": issued_epoch,
            "exp": issued_epoch + ttl_seconds,
            "ttl": ttl_seconds,
            "status_list": {"bits": 2, "lst": b64url_encode(zlib.compress(raw, 9))},
        }
        header = {"alg": OACP_JWS_ALG, "kid": self.kid, "typ": STATUS_LIST_JWT_TYP}
        header_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        claims_b64 = b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signature = self.sign(f"{header_b64}.{claims_b64}".encode("ascii"))
        return f"{header_b64}.{claims_b64}.{b64url_encode(signature)}"


async def verify_local_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    signer: LaunchCheckSigner,
    now_iso: str,
) -> list[dict[str, Any]]:
    """Run each artifact through OacpGrantexArtifactVerifier (steps 1-8) and return its result.

    The verifier fetches the JWKS and the status list itself, over an
    in-process transport that serves only the throwaway key and a VALID list.
    """
    issued_epoch = int(datetime.fromisoformat(now_iso.replace("Z", "+00:00")).timestamp())
    jwks = {"keys": [signer.public_jwk()]}
    status_token = signer.status_list_token(
        uri=LOCAL_STATUS_LIST_URI,
        size=max(len(artifacts), 1),
        issued_epoch=issued_epoch,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == LOCAL_JWKS_URI:
            return httpx.Response(200, json=jwks)
        if url == LOCAL_STATUS_LIST_URI:
            return httpx.Response(200, text=status_token, headers={"Content-Type": "application/statuslist+jwt"})
        return httpx.Response(404)

    verifier = OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=LOCAL_ISSUER,
            jwks_uri=LOCAL_JWKS_URI,
            status_list_origins=frozenset({LOCAL_ISSUER}),
        ),
        transport_factory=lambda: httpx.MockTransport(handler),
    )
    results = []
    for artifact in artifacts:
        envelope, payload = split_grantex_artifact(artifact)
        payload_scope = payload if isinstance(payload, Mapping) else {}
        results.append(
            await verifier.verify(
                envelope,
                payload,
                expected_scope=OacpArtifactScope(
                    tenant_id=str(payload_scope.get("tenant_id") or ""),
                    merchant_id=str(payload_scope.get("merchant_id") or ""),
                    seller_agent_id=str(payload_scope.get("seller_agent_id") or ""),
                    buyer_agent_id="",
                ),
                now_iso=now_iso,
            )
        )
    return results


def summarize_verifier_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts taken from the verifier results, never from the number of artifacts."""
    valid = [result for result in results if result.get("valid") is True]
    refused = [result for result in results if result.get("valid") is not True]
    return {
        "checked": len(results),
        "valid": len(valid),
        "invalid": len(refused),
        "status_list_checked": sum(1 for result in valid if result.get("status_checked") is True),
        "refusal_codes": sorted({str(result.get("refusal_code")) for result in refused}),
        "verification_mode": LOCAL_VERIFICATION_MODE,
        "status_list_source": LOCAL_STATUS_LIST_SOURCE,
        "issuer": LOCAL_ISSUER,
        "grantex_issuer": False,
    }


def _local_artifacts(payload: dict[str, Any], now_iso: str, signer: LaunchCheckSigner) -> list[dict[str, Any]]:
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
    for status_idx, family in enumerate(C6Z_ARTIFACT_FAMILIES):
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
        envelope = signer.sign_envelope(
            {
                "artifact_id": artifact_id,
                "artifact_type": family_type[family],
                "schema_version": "oacp.artifact.v1",
                "issuer": LOCAL_ISSUER,
                "issuer_key_id": signer.kid,
                "subject_type": family,
                "subject_id": f"{request['merchant_id']}:{request['seller_agent_id']}",
                "tenant_id": request["tenant_id"],
                "merchant_id": request["merchant_id"],
                "seller_agent_id": request["seller_agent_id"],
                "buyer_agent_id": "",
                "issued_at": now_iso,
                "expires_at": expires,
                "source_observed_at": evidence["source_observed_at"],
                "freshness_class": "fresh",
                "revocation_status_url": LOCAL_STATUS_LIST_URI,
                "status": {"status_list": {"idx": status_idx, "uri": LOCAL_STATUS_LIST_URI}},
                "policy_version": LOCAL_POLICY_VERSION,
                "evidence_refs": [evidence["source_evidence_ref"]],
                "payload_hash": oacp_payload_hash(artifact_payload),
                "signature_alg": OACP_JWS_ALG,
                "safety": {
                    "public_safe": True,
                    "contains_private_data": False,
                    "commitment_allowed": False,
                    "stale_behavior": "refuse_final_commitment",
                },
            }
        )
        artifacts.append({"artifact_family": family, "envelope": envelope, "payload": artifact_payload})
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


def _public_catalog_evidence(
    *,
    packet: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    base_url = os.getenv("OACP_PUBLIC_BASE_URL", "https://agenticorg.ai")
    snapshot = build_public_catalog_snapshot(
        tenant_id=TENANT_ID,
        merchant_id=MERCHANT_ID,
        seller_agent_id=SELLER_AGENT_ID,
        merchant_display_name=str(packet["merchant_display_name"]),
        public_brand_profile=packet.get("public_brand_profile") or {},
        commerce_categories=packet.get("commerce_categories") or [],
        connector_metadata_redacted=packet.get("connector_metadata_redacted") or {},
        evidence_records=[{**evidence, "base_url": base_url}],
        base_url=base_url,
        public_enabled=True,
    )
    html = build_public_catalog_html(snapshot)
    sitemap = build_public_sitemap_xml(snapshot)
    llms = build_public_llms_txt(snapshot)
    links = snapshot.get("links") or {}
    products = list(snapshot.get("products") or [])
    schema_graph = list((snapshot.get("schema_org_jsonld") or {}).get("@graph") or [])
    return {
        "status": snapshot.get("status"),
        "operator_enabled_for_smoke": True,
        "seller_profile_url": links.get("seller_profile"),
        "catalog_json_url": links.get("catalog_json"),
        "schema_org_jsonld_url": links.get("schema_org_jsonld"),
        "sitemap_xml_url": links.get("sitemap_xml"),
        "llms_txt_url": links.get("llms_txt"),
        "sample_product_url": products[0].get("public_url") if products else None,
        "product_count": len(products),
        "schema_org_product_nodes": sum(1 for item in schema_graph if item.get("@type") == "Product"),
        "schema_org_offer_nodes": sum(len(item.get("offers") or []) for item in schema_graph),
        "html_bytes": len(html.encode("utf-8")),
        "sitemap_bytes": len(sitemap.encode("utf-8")),
        "llms_txt_bytes": len(llms.encode("utf-8")),
        "source_label": snapshot.get("source_label"),
        "freshness_label": snapshot.get("freshness_label"),
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "external_publication_claimed": False,
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    public_catalog = summary.get("public_catalog_generation") or {}
    shopify = summary.get("shopify_external_check") or {}
    grantex = summary.get("grantex_external_check") or {}
    verifier = summary.get("artifact_verifier_summary") or {}
    blocker_lines = [
        f"- Shopify external check: `{shopify.get('status')}`"
        + (f" missing `{', '.join(shopify.get('missing_env_vars') or [])}`" if shopify.get("missing_env_vars") else ""),
        f"- Grantex external check: `{grantex.get('status')}`"
        + (f" missing `{', '.join(grantex.get('missing_env_vars') or [])}`" if grantex.get("missing_env_vars") else ""),
        f"- Plural/Pine capability: `{summary.get('plural_pine_capability_status')}`",
        f"- Purchase prepare: `{summary.get('purchase_prepare_status')}`",
    ]
    return "\n".join(
        [
            "# OACP Runtime Launch Evidence",
            "",
            f"- Generated at: `{summary['generated_at']}`",
            f"- External checks enabled: `{summary['external_checks_enabled']}`",
            f"- Tenant: `{summary['tenant_id']}`",
            f"- Merchant: `{summary['merchant_id']}`",
            f"- Seller agent: `{summary['seller_agent_id']}`",
            f"- Buyer agent: `{BUYER_AGENT_ID}`",
            f"- Packet id: `{summary['packet_id']}`",
            f"- Evidence id: `{summary['evidence_id']}`",
            "",
            "## Runtime Proof",
            "",
            f"- Shopify product/variant counts: `{summary['shopify_product_count']}` / `{summary['shopify_variant_count']}`",
            f"- Artifact families: `{summary['local_artifact_family_count']}`",
            f"- Artifact verifier: `{verifier.get('valid')}` valid / `{verifier.get('invalid')}` invalid of"
            f" `{verifier.get('checked')}` checked; status list checked `{verifier.get('status_list_checked')}`",
            f"- Verifier inputs: `{verifier.get('verification_mode')}`, `{verifier.get('status_list_source')}`,"
            f" Grantex issuer `{verifier.get('grantex_issuer')}`",
            f"- Cache records: `{summary['cache_records_count']}` (verified `{summary.get('cache_records_verified_count')}`)",
            f"- Buyer answer status: `{summary['buyer_answer_status']}`",
            f"- Adapter status: `{summary['protocol_adapter_status']}`",
            f"- Bridge artifact refs: `{summary['bridge_artifact_ref_count']}`",
            "",
            "## Public Catalog Smoke",
            "",
            f"- Status: `{public_catalog.get('status')}`",
            f"- Seller profile: `{public_catalog.get('seller_profile_url')}`",
            f"- Catalog JSON: `{public_catalog.get('catalog_json_url')}`",
            f"- Schema.org JSON-LD: `{public_catalog.get('schema_org_jsonld_url')}`",
            f"- Sitemap: `{public_catalog.get('sitemap_xml_url')}`",
            f"- llms.txt: `{public_catalog.get('llms_txt_url')}`",
            f"- Sample product page: `{public_catalog.get('sample_product_url')}`",
            f"- Schema.org Product nodes: `{public_catalog.get('schema_org_product_nodes')}`",
            "",
            "## External Checks And Blockers",
            "",
            *blocker_lines,
            "",
            "## Safety",
            "",
            f"- Raw payload stored: `{summary['raw_payload_stored']}`",
            f"- Payment execution: `{not summary['no_payment_execution']}`",
            f"- Allowed to execute: `{summary['allowed_to_execute']}`",
            f"- Transaction authority: `{not summary['non_authoritative_for_transaction']}`",
            "",
        ]
    )


def _write_evidence(summary: dict[str, Any]) -> dict[str, str]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    json_path = EVIDENCE_DIR / "oacp-runtime-launch-evidence.local.json"
    md_path = EVIDENCE_DIR / "oacp-runtime-launch-evidence.local.md"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(summary), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


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
    public_catalog = _public_catalog_evidence(packet=packet, evidence=evidence)
    authority_payload = build_grantex_authority_request_payload(
        onboarding_packet=packet,
        connector_evidence=evidence,
    )
    signer = LaunchCheckSigner()
    artifacts = _local_artifacts(authority_payload, now_iso, signer)
    verifier_results = await verify_local_artifacts(artifacts, signer=signer, now_iso=now_iso)
    verifier_summary = summarize_verifier_results(verifier_results)
    cache_records = [
        build_cache_record_from_grantex_artifact(
            artifact,
            cached_at=now_iso,
            buyer_agent_id=BUYER_AGENT_ID,
            verifier_result_ref=result.get("verifier_result_ref") if result.get("valid") is True else None,
        )
        for artifact, result in zip(artifacts, verifier_results, strict=True)
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
    local_capability_fixture_used = capability.result_status != "available"
    local_capability_for_handoff: dict[str, Any] = capability.__dict__
    if local_capability_fixture_used:
        local_capability_for_handoff = {
            "tenant_id": TENANT_ID,
            "merchant_id": MERCHANT_ID,
            "seller_agent_id": SELLER_AGENT_ID,
            "buyer_agent_id": BUYER_AGENT_ID,
            "result_status": "available",
            "provider": "plural_pine_p3p",
            "provider_environment": "local_fixture_non_live",
            "checked_at": now_iso,
            "expires_at": (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) + timedelta(minutes=5))
            .isoformat()
            .replace("+00:00", "Z"),
            "redacted_evidence_ref": "provider:plural_pine:capability:local-fixture:redacted",
            "metadata": {"local_fixture": True, "live_execution_approved": False},
            "raw_payload_stored": False,
            "allowed_to_execute": False,
            "no_payment_execution": True,
        }
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
        capability_evidence=[local_capability_for_handoff],
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
    pos_packet = None
    pos_confirmation = None
    pos_reconciliation = None
    if purchase.prepared_handoff is not None:
        pos_packet = build_offline_pos_handoff_packet(
            purchase_preparation=purchase.__dict__,
            tenant_id=TENANT_ID,
            merchant_id=MERCHANT_ID,
            seller_agent_id=SELLER_AGENT_ID,
            store_id="local_pos_store_1",
            pos_location={
                "display_name": "Local POS Simulator",
                "city": "Bengaluru",
                "country_code": "IN",
                "pos_provider": "local_simulator",
            },
            buyer_session_ref="buyer_session_oacp_launch",
            now_iso=now_iso,
        )
        pos_confirmation = simulate_offline_pos_confirmation(
            packet=pos_packet,
            now_iso=now_iso,
            confirmation_status="accepted",
        )
        pos_reconciliation = reconcile_offline_pos_confirmation(
            packet=pos_packet,
            confirmation=pos_confirmation,
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
        "artifact_verifier_summary": verifier_summary,
        "cache_records_verified_count": sum(1 for record in cache_records if record.verifier_result_ref),
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
        "public_catalog_generation": public_catalog,
        "plural_pine_capability_status": capability.result_status,
        "plural_pine_redacted_evidence_ref": capability.redacted_evidence_ref,
        "plural_pine_local_fixture_used_for_pos_demo": local_capability_fixture_used,
        "plural_pine_local_fixture_evidence_ref": (
            local_capability_for_handoff["redacted_evidence_ref"] if local_capability_fixture_used else None
        ),
        "purchase_prepare_status": purchase.status,
        "purchase_prepare_blocker": purchase.blocker,
        "purchase_idempotency_key": purchase.idempotency_key,
        "offline_pos_packet_status": None if pos_packet is None else pos_packet["status"],
        "offline_pos_packet_id": None if pos_packet is None else pos_packet["packet_id"],
        "offline_pos_confirmation_status": None if pos_confirmation is None else pos_confirmation["confirmation_status"],
        "offline_pos_buyer_safe_status": None if pos_reconciliation is None else pos_reconciliation.buyer_safe_status,
        "offline_pos_raw_payment_payload_stored": False,
        "offline_pos_payment_success_claimed": False,
        "shopify_external_check": await _maybe_shopify_check(),
        "grantex_external_check": await _maybe_grantex_check(authority_payload),
        "public_discovery_flag": False,
        "public_catalog_smoke_operator_enabled": True,
        "allowed_to_execute": False,
        "raw_payload_stored": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }
    if _secret_value_present(summary):
        raise SystemExit("Unsafe launch summary contained a secret-like value")
    if WRITE_EVIDENCE:
        summary["evidence_files"] = _write_evidence(summary)
        if _secret_value_present(summary):
            raise SystemExit("Unsafe launch summary contained a secret-like value after writing evidence paths")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if verifier_summary["invalid"]:
        raise SystemExit(f"OACP verifier refused {verifier_summary['invalid']} local artifact(s)")


if __name__ == "__main__":
    asyncio.run(main())
