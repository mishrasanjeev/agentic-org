"""Internal C6Z runtime helpers for seller commerce agent demo flows.

These helpers intentionally model runtime behavior without becoming transaction
authority. External integrations are read-only or capability-check only, and
all persisted records must remain redacted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

import httpx

from core.commerce.oacp_artifacts import (
    OacpPersistentArtifactCacheRecord,
    PersistentCacheActionIntent,
    evaluate_oacp_persistent_artifact_cache_record,
)

SHOPIFY_ENV_VARS: tuple[str, ...] = (
    "SHOPIFY_SHOP_DOMAIN",
    "SHOPIFY_ADMIN_ACCESS_TOKEN",
    "SHOPIFY_API_VERSION",
)
SHOPIFY_WEBHOOK_ENV_VARS: tuple[str, ...] = ("SHOPIFY_WEBHOOK_SECRET",)
GRANTEX_AUTHORITY_ENV_VARS: tuple[str, ...] = (
    "GRANTEX_COMMERCE_BASE_URL",
    "GRANTEX_COMMERCE_INTERNAL_TOKEN",
)
PLURAL_PINE_ENV_VARS: tuple[str, ...] = (
    "PLURAL_PINE_CLIENT_ID",
    "PLURAL_PINE_CLIENT_SECRET",
    "PLURAL_PINE_ENVIRONMENT",
    "PLURAL_PINE_CAPABILITY_URL",
)


C6Z_ARTIFACT_FAMILIES: tuple[str, ...] = (
    "merchant_profile",
    "seller_agent_card",
    "connector_evidence",
    "catalog_snapshot",
    "offer_price_snapshot",
    "inventory_snapshot",
    "policy_scope",
    "authority_request_status",
)
C6Z_ONBOARDING_STATUSES: tuple[str, ...] = (
    "received",
    "pending_sandbox_review",
    "rejected",
    "artifact_issuance_ready",
)
C6Z_CAPABILITY_STATUSES: tuple[str, ...] = (
    "available",
    "unavailable",
    "unknown",
    "blocked_missing_credentials",
    "blocked_provider_error",
)

PRIVATE_KEY_MARKERS: tuple[str, ...] = (
    "access_token",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "jwt",
    "password",
    "private_key",
    "raw_payload",
    "raw_response",
    "secret",
    "shopify_admin_access_token",
    "token",
)
PRIVATE_VALUE_MARKERS: tuple[str, ...] = (
    "-----begin",
    "basic ",
    "bearer ",
    "client_secret=",
    "password=",
    "private_key",
    "shpat_",
    "xoxb-",
)
EXECUTION_MARKERS: tuple[str, ...] = (
    "checkout.create",
    "checkout_session",
    "mandate.create",
    "order.create",
    "payment.create",
    "refund.create",
    "return.create",
    "ship.create",
    "shipping_label",
    "subscription.create",
)
FINAL_COMMITMENT_TERMS: tuple[str, ...] = (
    "buy",
    "checkout",
    "hold",
    "mandate",
    "order",
    "pay",
    "purchase",
    "refund",
    "reserve",
    "return",
    "ship",
    "subscribe",
)


class C6ZRuntimeValidationError(ValueError):
    """Raised when a C6Z record would violate runtime guardrails."""


@dataclass(frozen=True)
class C6ZEnvResolution:
    required: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class ShopifyCredentials:
    shop_domain: str
    admin_access_token: str
    api_version: str


@dataclass(frozen=True)
class C6ZBuyerAnswer:
    status: Literal["answered", "refused", "needs_refresh", "not_found"]
    answer: str
    source_label: str
    freshness_label: str
    refusal_reason: str | None
    matched_products: tuple[Mapping[str, Any], ...]
    allowed_to_execute: bool = False
    non_authoritative_for_transaction: bool = True


@dataclass(frozen=True)
class PluralPineCapabilityEvidence:
    evidence_id: str
    provider: Literal["plural_pine"]
    capability_type: str
    result_status: str
    checked_at: str
    expires_at: str
    redacted_evidence_ref: str
    provider_environment: str
    external_validation_performed: bool
    missing_env_vars: tuple[str, ...]
    raw_payload_stored: bool = False
    allowed_to_execute: bool = False
    no_payment_execution: bool = True
    no_live_provider_enablement: bool = True
    non_authoritative_for_transaction: bool = True


def resolve_env(required: Sequence[str], env: Mapping[str, str] | None = None) -> C6ZEnvResolution:
    source = os.environ if env is None else env
    missing = tuple(name for name in required if not str(source.get(name, "")).strip())
    return C6ZEnvResolution(required=tuple(required), missing=missing)


def resolve_shopify_credentials(env: Mapping[str, str] | None = None) -> ShopifyCredentials:
    source = os.environ if env is None else env
    resolution = resolve_env(SHOPIFY_ENV_VARS, source)
    if not resolution.ready:
        joined = ", ".join(resolution.missing)
        raise C6ZRuntimeValidationError(f"Missing Shopify environment variables: {joined}")
    return ShopifyCredentials(
        shop_domain=_normalize_shopify_domain(source["SHOPIFY_SHOP_DOMAIN"]),
        admin_access_token=source["SHOPIFY_ADMIN_ACCESS_TOKEN"].strip(),
        api_version=source["SHOPIFY_API_VERSION"].strip(),
    )


def build_seller_onboarding_packet(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    merchant_display_name: str,
    public_brand_profile: Mapping[str, Any],
    commerce_categories: Sequence[str],
    requested_grantex_authority_scope: Mapping[str, Any],
    artifact_cache_scope: Mapping[str, Any],
    source_freshness_policy: Mapping[str, Any],
    connector_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    packet = {
        "packet_id": _stable_id("c6z_onboarding", tenant_id, merchant_id, seller_agent_id),
        "tenant_id": _require_id("tenant_id", tenant_id),
        "merchant_id": _require_id("merchant_id", merchant_id),
        "seller_agent_id": _require_id("seller_agent_id", seller_agent_id),
        "merchant_display_name": _require_text("merchant_display_name", merchant_display_name),
        "public_brand_profile": dict(public_brand_profile),
        "commerce_categories": tuple(_require_text("commerce_category", value) for value in commerce_categories),
        "connector_choice": "shopify",
        "connector_mode": "read_only",
        "requested_grantex_authority_scope": dict(requested_grantex_authority_scope),
        "artifact_cache_scope": dict(artifact_cache_scope),
        "source_freshness_policy": dict(source_freshness_policy),
        "connector_metadata_redacted": redact_connector_metadata(connector_metadata or {}),
        "status": "received",
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }
    validate_seller_onboarding_packet(packet)
    return packet


def validate_seller_onboarding_packet(packet: Mapping[str, Any]) -> None:
    for key in ("tenant_id", "merchant_id", "seller_agent_id", "merchant_display_name"):
        _require_text(key, str(packet.get(key, "")))
    if packet.get("connector_choice") != "shopify":
        raise C6ZRuntimeValidationError("connector_choice must be shopify")
    if packet.get("connector_mode") != "read_only":
        raise C6ZRuntimeValidationError("connector_mode must be read_only")
    if not packet.get("commerce_categories"):
        raise C6ZRuntimeValidationError("commerce_categories are required")
    if not packet.get("requested_grantex_authority_scope"):
        raise C6ZRuntimeValidationError("requested_grantex_authority_scope is required")
    if not packet.get("artifact_cache_scope"):
        raise C6ZRuntimeValidationError("artifact_cache_scope is required")
    if packet.get("no_payment_execution") is not True:
        raise C6ZRuntimeValidationError("no_payment_execution must remain true")
    if packet.get("no_public_discovery_enablement") is not True:
        raise C6ZRuntimeValidationError("no_public_discovery_enablement must remain true")
    if packet.get("allowed_to_execute") not in (False, None):
        raise C6ZRuntimeValidationError("allowed_to_execute must remain false")
    if contains_private_or_executable_value(packet):
        raise C6ZRuntimeValidationError("onboarding packet contains private or executable values")


def redact_connector_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if contains_private_or_executable_value(metadata):
        raise C6ZRuntimeValidationError("connector metadata contains private values")
    return {
        "shop_domain": _normalize_shopify_domain(str(metadata.get("shop_domain", "")))
        if metadata.get("shop_domain")
        else None,
        "api_version": str(metadata.get("api_version", "")).strip() or None,
        "credential_ref": "env:shopify-admin-credential:redacted" if metadata.get("credential_ref") else None,
        "mode": "read_only",
    }


class ShopifyAdminGraphQLClient:
    """Small read-only Shopify Admin GraphQL client.

    The client intentionally exposes product reads only. It does not implement
    mutations, order APIs, checkout APIs, or payment APIs.
    """

    def __init__(
        self,
        credentials: ShopifyCredentials,
        *,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def fetch_products(self, *, page_size: int = 50, max_pages: int = 10) -> list[dict[str, Any]]:
        if page_size < 1 or page_size > 100:
            raise C6ZRuntimeValidationError("page_size must be between 1 and 100")
        if max_pages < 1 or max_pages > 20:
            raise C6ZRuntimeValidationError("max_pages must be between 1 and 20")

        products: list[dict[str, Any]] = []
        after_cursor: str | None = None
        async with httpx.AsyncClient(timeout=self._timeout_seconds, transport=self._transport) as client:
            for _ in range(max_pages):
                payload: dict[str, Any] = {
                    "query": SHOPIFY_PRODUCTS_QUERY,
                    "variables": {"first": page_size, "after": after_cursor},
                }
                response = await client.post(
                    self.graphql_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Shopify-Access-Token": self.credentials.admin_access_token,
                    },
                )
                response.raise_for_status()
                body = response.json()
                if body.get("errors"):
                    raise C6ZRuntimeValidationError("Shopify GraphQL returned errors")
                connection = body.get("data", {}).get("products", {})
                nodes = connection.get("nodes") or []
                products.extend(cast(list[dict[str, Any]], nodes))
                page_info = connection.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                after_cursor = str(page_info.get("endCursor") or "")
                if not after_cursor:
                    break
        return products

    @property
    def graphql_url(self) -> str:
        return (
            f"https://{self.credentials.shop_domain}/admin/api/"
            f"{self.credentials.api_version}/graphql.json"
        )


SHOPIFY_PRODUCTS_QUERY = """
query C6ZProducts($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      title
      descriptionHtml
      vendor
      productType
      updatedAt
      media(first: 10) {
        nodes {
          mediaContentType
          preview {
            image {
              url
              altText
            }
          }
        }
      }
      variants(first: 50) {
        nodes {
          id
          sku
          title
          price
          compareAtPrice
          inventoryQuantity
          updatedAt
          selectedOptions {
            name
            value
          }
          inventoryItem {
            id
          }
        }
      }
    }
  }
}
"""


def normalize_shopify_product_node(
    node: Mapping[str, Any],
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    synced_at: str,
    default_currency: str | None = None,
) -> dict[str, Any]:
    product_id = _require_text("shopify_product_id", str(node.get("id", "")))
    title = _require_text("title", str(node.get("title", "")))
    variants = []
    for variant in _connection_nodes(node.get("variants")):
        variants.append(
            {
                "variant_id": _require_text("shopify_variant_id", str(variant.get("id", ""))),
                "sku": _safe_optional_text(variant.get("sku")),
                "title": _safe_optional_text(variant.get("title")) or "Default",
                "price": _normalize_decimal_text(variant.get("price")),
                "compare_at_price": _normalize_decimal_text(variant.get("compareAtPrice")),
                "currency": default_currency,
                "inventory_quantity_snapshot": _safe_int(variant.get("inventoryQuantity")),
                "inventory_item_ref": _redacted_ref(
                    "shopify_inventory_item",
                    variant.get("inventoryItem", {}).get("id"),
                ),
                "updated_at": _safe_optional_text(variant.get("updatedAt")),
                "selected_options": tuple(
                    {
                        "name": _safe_optional_text(option.get("name")),
                        "value": _safe_optional_text(option.get("value")),
                    }
                    for option in (variant.get("selectedOptions") or [])
                    if isinstance(option, Mapping)
                ),
            }
        )
    images = []
    for media in _connection_nodes(node.get("media")):
        image = ((media.get("preview") or {}).get("image") or {}) if isinstance(media, Mapping) else {}
        if image.get("url"):
            images.append(
                {
                    "url": _require_text("image_url", str(image.get("url"))),
                    "alt_text": _safe_optional_text(image.get("altText")),
                }
            )
    normalized = {
        "product_ref": _redacted_ref("shopify_product", product_id),
        "source_product_id": product_id,
        "tenant_id": _require_id("tenant_id", tenant_id),
        "merchant_id": _require_id("merchant_id", merchant_id),
        "seller_agent_id": _require_id("seller_agent_id", seller_agent_id),
        "title": title,
        "description": _safe_optional_text(node.get("descriptionHtml")),
        "vendor": _safe_optional_text(node.get("vendor")),
        "product_type": _safe_optional_text(node.get("productType")),
        "images": tuple(images),
        "variants": tuple(variants),
        "updated_at": _safe_optional_text(node.get("updatedAt")),
        "synced_at": _require_text("synced_at", synced_at),
        "source_system": "shopify",
        "source_mode": "read_only",
    }
    if contains_private_or_executable_value(normalized):
        raise C6ZRuntimeValidationError("normalized Shopify product contains unsafe values")
    return normalized


def build_shopify_connector_evidence(
    *,
    packet: Mapping[str, Any],
    products: Sequence[Mapping[str, Any]],
    synced_at: str,
    source_observed_at: str,
    currency: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    validate_seller_onboarding_packet(packet)
    normalized_products = tuple(
        normalize_shopify_product_node(
            product,
            tenant_id=str(packet["tenant_id"]),
            merchant_id=str(packet["merchant_id"]),
            seller_agent_id=str(packet["seller_agent_id"]),
            synced_at=synced_at,
            default_currency=currency,
        )
        for product in products
    )
    product_count = len(normalized_products)
    variant_count = sum(len(product.get("variants", ())) for product in normalized_products)
    evidence_seed = json.dumps(
        {
            "tenant_id": packet["tenant_id"],
            "merchant_id": packet["merchant_id"],
            "seller_agent_id": packet["seller_agent_id"],
            "synced_at": synced_at,
            "product_refs": [product["product_ref"] for product in normalized_products],
        },
        sort_keys=True,
    )
    evidence_id = _stable_id("c6z_shopify_evidence", evidence_seed)
    evidence = {
        "evidence_id": evidence_id,
        "packet_id": packet["packet_id"],
        "tenant_id": packet["tenant_id"],
        "merchant_id": packet["merchant_id"],
        "seller_agent_id": packet["seller_agent_id"],
        "source_system": "shopify",
        "source_mode": "read_only",
        "source_evidence_ref": f"agenticorg:shopify:evidence:{_sha256(evidence_seed)[:24]}:redacted",
        "source_observed_at": _require_text("source_observed_at", source_observed_at),
        "synced_at": _require_text("synced_at", synced_at),
        "currency": currency,
        "products": normalized_products,
        "product_count": product_count,
        "variant_count": variant_count,
        "idempotency_key": idempotency_key,
        "raw_payload_stored": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }
    validate_connector_evidence(evidence)
    return evidence


def validate_connector_evidence(evidence: Mapping[str, Any]) -> None:
    for key in ("tenant_id", "merchant_id", "seller_agent_id", "source_evidence_ref"):
        _require_text(key, str(evidence.get(key, "")))
    if evidence.get("source_system") != "shopify":
        raise C6ZRuntimeValidationError("source_system must be shopify")
    if evidence.get("source_mode") != "read_only":
        raise C6ZRuntimeValidationError("source_mode must be read_only")
    if evidence.get("raw_payload_stored") is not False:
        raise C6ZRuntimeValidationError("raw connector payloads must not be stored")
    if evidence.get("no_payment_execution") is not True:
        raise C6ZRuntimeValidationError("no_payment_execution must remain true")
    if evidence.get("no_public_discovery_enablement") is not True:
        raise C6ZRuntimeValidationError("no_public_discovery_enablement must remain true")
    if contains_private_or_executable_value(evidence):
        raise C6ZRuntimeValidationError("connector evidence contains private or executable values")


def verify_shopify_webhook_hmac(raw_body: bytes, hmac_header: str, secret: str) -> bool:
    if not raw_body or not hmac_header or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, hmac_header.strip())


def shopify_webhook_idempotency_key(
    *,
    topic: str,
    shop_domain: str,
    webhook_id: str | None,
    raw_body: bytes,
) -> str:
    body_hash = hashlib.sha256(raw_body).hexdigest()[:24]
    safe_webhook_id = webhook_id.strip() if webhook_id else "body"
    return _stable_id("shopify_webhook", topic, _normalize_shopify_domain(shop_domain), safe_webhook_id, body_hash)


def build_grantex_authority_request_payload(
    *,
    onboarding_packet: Mapping[str, Any],
    connector_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    validate_seller_onboarding_packet(onboarding_packet)
    validate_connector_evidence(connector_evidence)
    if connector_evidence["tenant_id"] != onboarding_packet["tenant_id"]:
        raise C6ZRuntimeValidationError("tenant scope mismatch")
    if connector_evidence["merchant_id"] != onboarding_packet["merchant_id"]:
        raise C6ZRuntimeValidationError("merchant scope mismatch")
    if connector_evidence["seller_agent_id"] != onboarding_packet["seller_agent_id"]:
        raise C6ZRuntimeValidationError("seller agent scope mismatch")
    source_products = connector_evidence.get("products") or ()
    return {
        "request": {
            "request_id": _stable_id("grantex_c6z_request", str(onboarding_packet["packet_id"])),
            "tenant_id": onboarding_packet["tenant_id"],
            "merchant_id": onboarding_packet["merchant_id"],
            "seller_agent_id": onboarding_packet["seller_agent_id"],
            "merchant_display_name": onboarding_packet["merchant_display_name"],
            "public_brand_profile": onboarding_packet["public_brand_profile"],
            "commerce_categories": onboarding_packet["commerce_categories"],
            "connector_choice": "shopify",
            "connector_mode": "read_only",
            "requested_grantex_authority_scope": onboarding_packet["requested_grantex_authority_scope"],
            "artifact_cache_scope": onboarding_packet["artifact_cache_scope"],
            "source_freshness_policy": onboarding_packet["source_freshness_policy"],
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
            "requested_at": _utc_now().isoformat().replace("+00:00", "Z"),
        },
        "connector_evidence": {
            "tenant_id": connector_evidence["tenant_id"],
            "merchant_id": connector_evidence["merchant_id"],
            "seller_agent_id": connector_evidence["seller_agent_id"],
            "source_system": "shopify",
            "source_evidence_ref": connector_evidence["source_evidence_ref"],
            "source_observed_at": connector_evidence["source_observed_at"],
            "product_count": connector_evidence["product_count"],
            "variant_count": connector_evidence["variant_count"],
            "catalog_refs": tuple(product["product_ref"] for product in source_products),
            "price_refs": tuple(
                f"{product['product_ref']}:price_snapshot"
                for product in source_products
                if product.get("variants")
            ),
            "inventory_refs": tuple(
                f"{product['product_ref']}:inventory_snapshot"
                for product in source_products
                if product.get("variants")
            ),
            "read_only": True,
            "raw_payload_stored": False,
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
        },
    }


async def send_grantex_authority_request(
    *,
    payload: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    source = os.environ if env is None else env
    resolution = resolve_env(GRANTEX_AUTHORITY_ENV_VARS, source)
    if not resolution.ready:
        return {
            "status": "blocked_missing_grantex_env",
            "missing_env_vars": resolution.missing,
            "artifact_issuance_attempted": False,
            "allowed_to_execute": False,
            "non_authoritative_for_transaction": True,
        }
    base_url = source["GRANTEX_COMMERCE_BASE_URL"].rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
        response = await client.post(
            f"{base_url}/v1/commerce/oacp/c6z/authority-requests",
            json=payload,
            headers={"Authorization": f"Bearer {source['GRANTEX_COMMERCE_INTERNAL_TOKEN']}"},
        )
        response.raise_for_status()
        body = response.json()
        if contains_private_or_executable_value(body):
            raise C6ZRuntimeValidationError("Grantex authority response contains unsafe values")
        return cast(dict[str, Any], body)


def build_cache_record_from_grantex_artifact(
    artifact: Mapping[str, Any],
    *,
    cached_at: str,
    buyer_agent_id: str | None = None,
) -> OacpPersistentArtifactCacheRecord:
    envelope = cast(Mapping[str, Any], artifact.get("envelope") or artifact)
    payload = cast(Mapping[str, Any], envelope.get("artifact_payload") or artifact.get("artifact_payload") or {})
    artifact_id = _require_text("artifact_id", str(envelope.get("artifact_id") or artifact.get("artifact_id") or ""))
    artifact_family = str(payload.get("artifact_family") or envelope.get("artifact_type") or "")
    if artifact_family not in C6Z_ARTIFACT_FAMILIES:
        raise C6ZRuntimeValidationError("unsupported C6Z artifact family")
    issued_at = _require_text("issued_at", str(envelope.get("issued_at") or payload.get("issued_at") or ""))
    expires_at = _require_text("expires_at", str(envelope.get("expires_at") or payload.get("expires_at") or ""))
    tenant_id = _require_id("tenant_id", str(payload.get("tenant_id") or ""))
    merchant_id = _require_id("merchant_id", str(payload.get("merchant_id") or ""))
    seller_agent_id = _require_id("seller_agent_id", str(payload.get("seller_agent_id") or ""))
    if payload.get("allowed_to_execute") is not False:
        raise C6ZRuntimeValidationError("artifact must not allow execution")
    if payload.get("no_payment_execution") is not True:
        raise C6ZRuntimeValidationError("artifact must block payment execution")
    if payload.get("no_public_discovery_enablement") is not True:
        raise C6ZRuntimeValidationError("artifact must block public discovery")
    if contains_private_or_executable_value(payload):
        raise C6ZRuntimeValidationError("artifact payload contains unsafe values")
    source_ref = _require_text("source_evidence_ref", str(payload.get("source_evidence_ref") or ""))
    return OacpPersistentArtifactCacheRecord(
        cache_record_id=_stable_id("c6z_artifact_cache", artifact_id, tenant_id, merchant_id, seller_agent_id),
        artifact_id=artifact_id,
        artifact_type=cast(Any, envelope.get("artifact_type") or "protocol_adapter"),
        issuer=cast(str, envelope.get("issuer") or "grantex.internal.oacp.authority"),
        authority=cast(str, envelope.get("issuer") or "grantex.internal.oacp.authority"),
        scope_kind="seller_agent",
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        source_refs=(source_ref,),
        evidence_refs=(source_ref,),
        generated_at=issued_at,
        cached_at=cached_at,
        expires_at=expires_at,
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_age_seconds=0,
        revocation_snapshot_observed_at=cached_at,
        ttl_policy_seconds=_ttl_seconds(issued_at, expires_at),
        risk_tier="low",
        blocked_capabilities=("checkout", "payment", "order", "mandate", "shipping"),
        unsupported_capabilities=("execution", "public_discovery", "live_provider"),
        verifier_result_ref=str(artifact.get("verifier_result_ref") or f"{artifact_id}:verified"),
        allowed_to_execute=False,
        non_authoritative_for_transaction=True,
        no_checkout_payment_enablement=True,
        no_live_provider_enablement=True,
        no_public_discovery_enablement=True,
    )


def answer_product_question_from_cache(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    products: Sequence[Mapping[str, Any]],
    question: str,
    now_iso: str,
    grantex_available: bool,
    action_intent: PersistentCacheActionIntent = "non_binding_preview",
) -> C6ZBuyerAnswer:
    question_text = _require_text("question", question)
    source_label = "Source: Shopify via Grantex artifact"
    if _looks_like_final_commitment(question_text):
        return C6ZBuyerAnswer(
            status="refused",
            answer=(
                "I can answer from cached product snapshots, but I cannot create an order, "
                "checkout, payment, hold, refund, return, shipment, or mandate."
            ),
            source_label=source_label,
            freshness_label="Freshness: commitment requests require stronger confirmation",
            refusal_reason="final_commitment_refused",
            matched_products=(),
        )
    usable_records = []
    blocked_reasons: list[str] = []
    for record in cache_records:
        result = evaluate_oacp_persistent_artifact_cache_record(
            record=record,
            now_iso=now_iso,
            action_intent=action_intent,
            grantex_available=grantex_available,
            expected_scope={
                "tenant_id": record.tenant_id,
                "merchant_id": record.merchant_id,
                "seller_agent_id": record.seller_agent_id,
                "buyer_agent_id": record.buyer_agent_id,
            },
        )
        if result.get("status") in ("usable_for_non_binding_cache", "prepared_only_for_commitment_boundary"):
            usable_records.append(record)
        else:
            refusal_code = result.get("refusal_code")
            if refusal_code:
                blocked_reasons.append(str(refusal_code))
    if not usable_records:
        return C6ZBuyerAnswer(
            status="needs_refresh",
            answer="I need a fresh internal artifact before answering this product question.",
            source_label=source_label,
            freshness_label="Freshness: expired, revoked, missing, or mismatched cache",
            refusal_reason="cache_not_usable:" + ",".join(sorted(set(blocked_reasons))),
            matched_products=(),
        )
    matches = _match_products(products, question_text)
    if not matches:
        return C6ZBuyerAnswer(
            status="not_found",
            answer="I did not find a matching cached product snapshot.",
            source_label=source_label,
            freshness_label=_freshness_label(usable_records, now_iso),
            refusal_reason=None,
            matched_products=(),
        )
    return C6ZBuyerAnswer(
        status="answered",
        answer=_build_product_answer(matches),
        source_label=source_label,
        freshness_label=_freshness_label(usable_records, now_iso),
        refusal_reason=None,
        matched_products=tuple(matches[:5]),
    )


async def verify_plural_pine_mandate_capability(
    *,
    tenant_id: str,
    merchant_id: str,
    capability_type: str = "mandate_capability",
    seller_agent_id: str | None = None,
    buyer_agent_id: str | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> PluralPineCapabilityEvidence:
    source = os.environ if env is None else env
    checked_at_dt = now or _utc_now()
    checked_at = checked_at_dt.isoformat().replace("+00:00", "Z")
    expires_at = (checked_at_dt + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    resolution = resolve_env(PLURAL_PINE_ENV_VARS, source)
    if not resolution.ready:
        return PluralPineCapabilityEvidence(
            evidence_id=_stable_id("plural_pine_capability", tenant_id, merchant_id, capability_type, checked_at),
            provider="plural_pine",
            capability_type=capability_type,
            result_status="blocked_missing_credentials",
            checked_at=checked_at,
            expires_at=expires_at,
            redacted_evidence_ref="provider:plural_pine:capability:missing-env:redacted",
            provider_environment=str(source.get("PLURAL_PINE_ENVIRONMENT", "unknown") or "unknown"),
            external_validation_performed=False,
            missing_env_vars=resolution.missing,
        )
    payload = {
        "jsonrpc": "2.0",
        "id": "c6z-capability-check",
        "method": "tools/list",
        "params": {},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": source["PLURAL_PINE_CLIENT_ID"],
        "X-Client-Secret": source["PLURAL_PINE_CLIENT_SECRET"],
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            response = await client.post(source["PLURAL_PINE_CAPABILITY_URL"], json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return PluralPineCapabilityEvidence(
            evidence_id=_stable_id("plural_pine_capability", tenant_id, merchant_id, capability_type, checked_at),
            provider="plural_pine",
            capability_type=capability_type,
            result_status="blocked_provider_error",
            checked_at=checked_at,
            expires_at=expires_at,
            redacted_evidence_ref=f"provider:plural_pine:capability:error:{_sha256(str(exc))[:16]}:redacted",
            provider_environment=str(source["PLURAL_PINE_ENVIRONMENT"]),
            external_validation_performed=True,
            missing_env_vars=(),
        )
    if contains_private_or_executable_value(body):
        raise C6ZRuntimeValidationError("provider capability response contains unsafe values")
    tools = body.get("result", {}).get("tools") or body.get("tools") or []
    tool_names = [
        str(tool.get("name", "")).lower()
        for tool in tools
        if isinstance(tool, Mapping) and tool.get("name")
    ]
    has_capability = any("mandate" in name or "subscription" in name for name in tool_names)
    seed = json.dumps({"tools": sorted(tool_names), "checked_at": checked_at}, sort_keys=True)
    return PluralPineCapabilityEvidence(
        evidence_id=_stable_id("plural_pine_capability", tenant_id, merchant_id, capability_type, checked_at),
        provider="plural_pine",
        capability_type=capability_type,
        result_status="available" if has_capability else "unavailable",
        checked_at=checked_at,
        expires_at=expires_at,
        redacted_evidence_ref=f"provider:plural_pine:capability:{_sha256(seed)[:24]}:redacted",
        provider_environment=str(source["PLURAL_PINE_ENVIRONMENT"]),
        external_validation_performed=True,
        missing_env_vars=(),
    )


def contains_private_or_executable_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in PRIVATE_KEY_MARKERS):
                if key_text in {"redacted_evidence_ref", "credential_ref"}:
                    pass
                elif key_text == "raw_payload_stored" and item is False:
                    pass
                else:
                    return True
            if any(marker in key_text for marker in EXECUTION_MARKERS):
                return True
            if contains_private_or_executable_value(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(contains_private_or_executable_value(item) for item in value)
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return False
        if any(marker in text for marker in PRIVATE_VALUE_MARKERS):
            return True
        if any(marker in text for marker in EXECUTION_MARKERS):
            return True
        if _looks_like_jwt(text):
            return True
    return False


def _normalize_shopify_domain(value: str) -> str:
    domain = value.strip().replace("https://", "").replace("http://", "").rstrip("/")
    if not domain:
        raise C6ZRuntimeValidationError("Shopify shop domain is required")
    if "/" in domain or " " in domain:
        raise C6ZRuntimeValidationError("Shopify shop domain must be a host name")
    return domain


def _require_id(name: str, value: str) -> str:
    cleaned = _require_text(name, value)
    if " " in cleaned or "/" in cleaned:
        raise C6ZRuntimeValidationError(f"{name} must be an opaque id")
    return cleaned


def _require_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise C6ZRuntimeValidationError(f"{name} is required")
    if contains_private_or_executable_value(cleaned):
        raise C6ZRuntimeValidationError(f"{name} contains unsafe values")
    return cleaned


def _safe_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if contains_private_or_executable_value(text):
        raise C6ZRuntimeValidationError("unsafe text value")
    return text


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_decimal_text(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise C6ZRuntimeValidationError("invalid decimal value") from exc


def _connection_nodes(connection: Any) -> list[Mapping[str, Any]]:
    if not isinstance(connection, Mapping):
        return []
    nodes = connection.get("nodes") or []
    return [cast(Mapping[str, Any], node) for node in nodes if isinstance(node, Mapping)]


def _redacted_ref(prefix: str, value: Any) -> str:
    text = _require_text(prefix, str(value or ""))
    return f"{prefix}:{_sha256(text)[:24]}:redacted"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}_{_sha256(payload)[:24]}"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 3:
        return False
    return all(len(part) > 8 for part in parts)


def _looks_like_final_commitment(question: str) -> bool:
    text = question.lower()
    return any(term in text for term in FINAL_COMMITMENT_TERMS)


def _ttl_seconds(issued_at: str, expires_at: str) -> int:
    issued = _parse_iso(issued_at)
    expires = _parse_iso(expires_at)
    seconds = int((expires - issued).total_seconds())
    if seconds <= 0:
        raise C6ZRuntimeValidationError("artifact expires before it is issued")
    return seconds


def _parse_iso(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _match_products(products: Sequence[Mapping[str, Any]], question: str) -> list[Mapping[str, Any]]:
    tokens = {token for token in question.lower().replace("?", " ").replace(",", " ").split() if len(token) > 2}
    if not tokens:
        return list(products[:5])
    matches = []
    for product in products:
        searchable = " ".join(
            str(part or "")
            for part in (
                product.get("title"),
                product.get("vendor"),
                product.get("product_type"),
                " ".join(str(variant.get("sku") or "") for variant in product.get("variants", ())),
            )
        ).lower()
        if any(token in searchable for token in tokens):
            matches.append(product)
    return matches


def _build_product_answer(products: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for product in products[:5]:
        variants = list(product.get("variants", ()))
        first_variant = variants[0] if variants else {}
        price = first_variant.get("price") or "unknown"
        currency = first_variant.get("currency") or "snapshot currency unavailable"
        inventory = first_variant.get("inventory_quantity_snapshot")
        inventory_text = "unknown" if inventory is None else str(inventory)
        image_url = ""
        images = list(product.get("images", ()))
        if images:
            image_url = f" Image: {images[0].get('url')}"
        lines.append(
            f"{product.get('title')}: price snapshot {price} {currency}; "
            f"inventory snapshot {inventory_text}; final price requires merchant/source confirmation."
            f"{image_url}"
        )
    return " ".join(lines)


def _freshness_label(records: Sequence[OacpPersistentArtifactCacheRecord], now_iso: str) -> str:
    now = _parse_iso(now_iso)
    ages = []
    for record in records:
        try:
            cached = _parse_iso(record.cached_at)
        except ValueError:
            continue
        ages.append(max(0, int((now - cached).total_seconds())))
    if not ages:
        return "Freshness: synced timestamp unavailable"
    youngest = min(ages)
    if youngest < 60:
        return f"Freshness: synced {youngest}s ago"
    if youngest < 3600:
        return f"Freshness: synced {youngest // 60}m ago"
    return f"Freshness: synced {youngest // 3600}h ago"


def summarize_capability_evidence(evidence: Sequence[PluralPineCapabilityEvidence]) -> dict[str, Any]:
    counts = Counter(item.result_status for item in evidence)
    return {
        "provider": "plural_pine",
        "capability_counts": dict(sorted(counts.items())),
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
        "no_payment_execution": True,
        "raw_payload_stored": False,
    }
