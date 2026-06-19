"""Internal C6Z runtime helpers for seller commerce agent demo flows.

These helpers intentionally model runtime behavior without becoming transaction
authority. External integrations are read-only or capability-check only, and
all persisted records must remain redacted.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
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
)
PLURAL_PINE_OPTIONAL_ENV_VARS: tuple[str, ...] = (
    "PLURAL_PINE_ENVIRONMENT",
    "PLURAL_PINE_CAPABILITY_URL",
    "PLURAL_PINE_BASE_URL",
    "PLURAL_BASE_URL",
)
PLURAL_PINE_SANDBOX_BASE_URL = "https://pluraluat.v2.pinepg.in/api"


C6Z_ARTIFACT_FAMILIES: tuple[str, ...] = (
    "merchant_profile",
    "seller_agent_card",
    "connector_evidence",
    "catalog_snapshot",
    "offer_price_snapshot",
    "inventory_snapshot",
    "policy_scope",
    "public_discovery_state",
    "mandate_capability",
    "protocol_adapter",
    "authority_request_status",
)
C6Z_ONBOARDING_STATUSES: tuple[str, ...] = (
    "draft",
    "received",
    "sync_ready",
    "synced",
    "authority_requested",
    "artifacts_cached",
    "cache_refresh_needed",
    "blocked_missing_credentials",
    "blocked_grantex_unavailable",
    "rejected",
)
C6Z_CAPABILITY_STATUSES: tuple[str, ...] = (
    "available",
    "unavailable",
    "unknown",
    "blocked_missing_credentials",
    "blocked_provider_error",
)
C6Z_BUYER_SURFACE_BRIDGES: tuple[dict[str, Any], ...] = (
    {
        "surface": "web",
        "buyer_surface_label": "AgenticOrg web session",
        "bridge_kind": "web_session",
        "primary_endpoint": "/api/v1/commerce/runtime/bridges/web/ask",
        "fallback_endpoint": "/api/v1/commerce/runtime/buyer-sessions/ask",
        "required_env_vars": (),
        "external_approval_required": False,
    },
    {
        "surface": "chatgpt",
        "buyer_surface_label": "ChatGPT-style agent surface",
        "bridge_kind": "mcp_or_openapi_action",
        "primary_endpoint": "agenticorg-mcp-server seller.* tools",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/openapi/ask",
        "required_env_vars": ("AGENTICORG_API_KEY",),
        "external_approval_required": True,
    },
    {
        "surface": "claude",
        "buyer_surface_label": "Claude / Claude Code MCP surface",
        "bridge_kind": "mcp_stdio_or_remote",
        "primary_endpoint": "agenticorg-mcp-server seller.* tools",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/openapi/ask",
        "required_env_vars": ("AGENTICORG_API_KEY",),
        "external_approval_required": True,
    },
    {
        "surface": "gemini",
        "buyer_surface_label": "Gemini-style function/A2A surface",
        "bridge_kind": "openapi_function_or_a2a",
        "primary_endpoint": "/api/v1/commerce/runtime/bridges/openapi/schema",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/a2a/agent-card",
        "required_env_vars": ("AGENTICORG_API_KEY",),
        "external_approval_required": True,
    },
    {
        "surface": "perplexity",
        "buyer_surface_label": "Perplexity-style answer/action surface",
        "bridge_kind": "openapi_hosted_answer",
        "primary_endpoint": "/api/v1/commerce/runtime/bridges/openapi/schema",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/openapi/ask",
        "required_env_vars": ("AGENTICORG_API_KEY",),
        "external_approval_required": True,
    },
    {
        "surface": "whatsapp",
        "buyer_surface_label": "WhatsApp Business Platform",
        "bridge_kind": "webhook_channel",
        "primary_endpoint": "/api/v1/commerce/runtime/bridges/whatsapp/webhook",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/web/ask",
        "required_env_vars": (
            "WHATSAPP_BUSINESS_ACCESS_TOKEN",
            "WHATSAPP_BUSINESS_PHONE_NUMBER_ID",
            "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            "WHATSAPP_APP_SECRET",
        ),
        "external_approval_required": True,
    },
    {
        "surface": "telegram",
        "buyer_surface_label": "Telegram Bot API",
        "bridge_kind": "webhook_channel",
        "primary_endpoint": "/api/v1/commerce/runtime/bridges/telegram/webhook",
        "fallback_endpoint": "/api/v1/commerce/runtime/bridges/web/ask",
        "required_env_vars": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET_TOKEN"),
        "external_approval_required": True,
    },
)

PROTOCOL_ADAPTER_SURFACES: tuple[str, ...] = (
    "schema_org_product_offer_jsonld",
    "ucp_style_capability_profile",
    "acp_style_commerce_interaction_profile",
    "ap2_style_mandate_payment_evidence_profile",
    "a2a_agent_card_task_metadata",
    "mcp_tool_resource_metadata",
    "openapi_buyer_safe_bridge_schema",
)

PERMITTED_SHOPIFY_SYNC_ACTIONS: frozenset[str] = frozenset(
    {
        "read_products",
        "read_variants",
        "read_product_images",
        "read_prices",
        "read_inventory_snapshot",
        "receive_product_webhooks",
        "receive_inventory_webhooks",
    }
)
BUYER_CHANNEL_SURFACES: frozenset[str] = frozenset(
    {"web", "chatgpt", "claude", "gemini", "perplexity", "whatsapp", "telegram"}
)
MANDATE_RAIL_PREFERENCES: frozenset[str] = frozenset(
    {"plural_pine_p3p", "plural_pine_sandbox", "none"}
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
class C6ZBridgeContractResponse:
    channel: str
    answer: str
    source_label: str
    freshness_label: str
    artifact_refs: tuple[str, ...]
    refusal_reason: str | None
    suggested_next_safe_action: str
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


@dataclass(frozen=True)
class PurchasePreparationResult:
    status: str
    idempotency_key: str
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str | None
    source_label: str
    freshness_label: str
    artifact_refs: tuple[str, ...]
    selected_product: Mapping[str, Any] | None
    selected_variant: Mapping[str, Any] | None
    quantity: int
    prepared_handoff: Mapping[str, Any] | None
    blocker: Mapping[str, Any] | None
    reconciliation_state: Mapping[str, Any]
    allowed_to_execute: bool = False
    no_payment_execution: bool = True
    non_authoritative_for_transaction: bool = True


def resolve_env(required: Sequence[str], env: Mapping[str, str] | None = None) -> C6ZEnvResolution:
    source = os.environ if env is None else env
    missing = tuple(name for name in required if not str(source.get(name, "")).strip())
    return C6ZEnvResolution(required=tuple(required), missing=missing)


def build_buyer_surface_bridge_matrix(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the concrete first-channel bridge contract for OACP commerce.

    This is runtime metadata for operators and channel clients. It does not
    create marketplace approvals, publish public discovery, or enable checkout.
    """

    source = os.environ if env is None else env
    surfaces: list[dict[str, Any]] = []
    for item in C6Z_BUYER_SURFACE_BRIDGES:
        required_env_vars = tuple(str(name) for name in item["required_env_vars"])
        missing_env_vars = tuple(name for name in required_env_vars if not str(source.get(name, "")).strip())
        surfaces.append(
            {
                "surface": item["surface"],
                "buyer_surface_label": item["buyer_surface_label"],
                "bridge_kind": item["bridge_kind"],
                "primary_endpoint": item["primary_endpoint"],
                "fallback_endpoint": item["fallback_endpoint"],
                "status": "bridge_ready" if not missing_env_vars else "blocked_missing_channel_config",
                "required_env_vars": required_env_vars,
                "missing_env_vars": missing_env_vars,
                "external_approval_required": bool(item["external_approval_required"]),
                "supported_actions": (
                    "seller_product_question_answering",
                    "catalog_snapshot_listing",
                    "source_freshness_labeling",
                    "prepared_handoff_explanation",
                ),
                "unsupported_actions": (
                    "checkout_execution",
                    "payment_execution",
                    "order_creation",
                    "inventory_hold_execution",
                    "mandate_creation",
                    "refund_execution",
                    "return_execution",
                    "shipping_execution",
                    "public_discovery_publication",
                ),
                "allowed_to_execute": False,
                "non_authoritative_for_transaction": True,
                "no_payment_execution": True,
                "no_public_discovery_enablement": True,
            }
        )
    return {
        "status": "surface_bridge_matrix_ready",
        "surfaces": surfaces,
        "schema_endpoint": "/api/v1/commerce/runtime/bridges/openapi/schema",
        "a2a_agent_card_endpoint": "/api/v1/commerce/runtime/bridges/a2a/agent-card",
        "mcp_package": "agenticorg-mcp-server",
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


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
    redacted_metadata = redact_connector_metadata(connector_metadata or {})
    cache_scope = dict(artifact_cache_scope)
    cache_scope.setdefault("tenant_id", tenant_id)
    cache_scope.setdefault("merchant_id", merchant_id)
    cache_scope.setdefault("seller_agent_id", seller_agent_id)
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
        "artifact_cache_scope": cache_scope,
        "source_freshness_policy": dict(source_freshness_policy),
        "connector_metadata_redacted": redacted_metadata,
        "permitted_sync_actions": tuple(redacted_metadata["permitted_sync_actions"]),
        "channel_capability_preferences": dict(redacted_metadata["channel_capability_preferences"]),
        "payment_mandate_rail_preference": redacted_metadata["payment_mandate_rail_preference"],
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
    permitted_sync_actions = _normalize_sync_actions(metadata.get("permitted_sync_actions"))
    channel_preferences = _normalize_channel_preferences(metadata.get("channel_capability_preferences"))
    payment_preference = _normalize_payment_rail_preference(metadata.get("payment_mandate_rail_preference"))
    return {
        "shop_domain": _normalize_shopify_domain(str(metadata.get("shop_domain", "")))
        if metadata.get("shop_domain")
        else None,
        "api_version": str(metadata.get("api_version", "")).strip() or None,
        "credential_ref": "env:shopify-admin-credential:redacted" if metadata.get("credential_ref") else None,
        "mode": "read_only",
        "permitted_sync_actions": permitted_sync_actions,
        "channel_capability_preferences": channel_preferences,
        "payment_mandate_rail_preference": payment_preference,
        "artifact_cache_scope": "tenant_merchant_seller_agent",
    }


def _normalize_sync_actions(value: Any) -> tuple[str, ...]:
    if value is None:
        return (
            "read_products",
            "read_variants",
            "read_product_images",
            "read_prices",
            "read_inventory_snapshot",
            "receive_product_webhooks",
            "receive_inventory_webhooks",
        )
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, Sequence):
        candidates = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise C6ZRuntimeValidationError("permitted_sync_actions must be a list or comma separated string")
    if not candidates:
        raise C6ZRuntimeValidationError("permitted_sync_actions cannot be empty")
    unknown = sorted(set(candidates) - set(PERMITTED_SHOPIFY_SYNC_ACTIONS))
    if unknown:
        raise C6ZRuntimeValidationError(f"unsupported Shopify sync actions: {', '.join(unknown)}")
    return tuple(dict.fromkeys(candidates))


def _normalize_channel_preferences(value: Any) -> dict[str, bool]:
    if value is None:
        return dict.fromkeys(BUYER_CHANNEL_SURFACES, True)
    if not isinstance(value, Mapping):
        raise C6ZRuntimeValidationError("channel_capability_preferences must be an object")
    normalized: dict[str, bool] = {}
    for key, enabled in value.items():
        surface = str(key).strip().lower()
        if surface not in BUYER_CHANNEL_SURFACES:
            raise C6ZRuntimeValidationError(f"unsupported buyer channel surface: {surface}")
        normalized[surface] = bool(enabled)
    for surface in BUYER_CHANNEL_SURFACES:
        normalized.setdefault(surface, False)
    return dict(sorted(normalized.items()))


def _normalize_payment_rail_preference(value: Any) -> str:
    preference = str(value or "plural_pine_p3p").strip().lower()
    if preference not in MANDATE_RAIL_PREFERENCES:
        raise C6ZRuntimeValidationError(
            "payment_mandate_rail_preference must be plural_pine_p3p, plural_pine_sandbox, or none"
        )
    return preference


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
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
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
                response: httpx.Response | None = None
                for attempt in range(self._retry_attempts):
                    try:
                        response = await client.post(
                            self.graphql_url,
                            json=payload,
                            headers={
                                "Content-Type": "application/json",
                                "X-Shopify-Access-Token": self.credentials.admin_access_token,
                            },
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        if attempt + 1 >= self._retry_attempts:
                            raise
                        await asyncio.sleep(self._retry_backoff_seconds * (2**attempt))
                if response is None:
                    raise C6ZRuntimeValidationError("Shopify GraphQL response was not available")
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
      status
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
        "status": _safe_optional_text(node.get("status")) or "UNKNOWN",
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
            "commerce_categories": onboarding_packet["commerce_categories"],
            "connector_choice": "shopify",
            "connector_mode": "read_only",
            "requested_authority_scope": _authority_scope_list(
                onboarding_packet["requested_grantex_authority_scope"]
            ),
            "artifact_cache_scope": onboarding_packet["artifact_cache_scope"],
            "source_freshness_policy": onboarding_packet["source_freshness_policy"],
            "source_evidence_ref": connector_evidence["source_evidence_ref"],
            "source_observed_at": connector_evidence["source_observed_at"],
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
            "requested_at": _utc_now().isoformat().replace("+00:00", "Z"),
        },
        "connector_evidence": {
            "evidence_id": connector_evidence["evidence_id"],
            "tenant_id": connector_evidence["tenant_id"],
            "merchant_id": connector_evidence["merchant_id"],
            "seller_agent_id": connector_evidence["seller_agent_id"],
            "source_system": "shopify",
            "source_evidence_ref": connector_evidence["source_evidence_ref"],
            "source_observed_at": connector_evidence["source_observed_at"],
            "product_count": connector_evidence["product_count"],
            "variant_count": connector_evidence["variant_count"],
            "currency": connector_evidence.get("currency"),
            "catalog_sample_refs": tuple(product["product_ref"] for product in source_products),
            "price_snapshot_refs": tuple(
                f"{product['product_ref']}:price_snapshot"
                for product in source_products
                if product.get("variants")
            ),
            "inventory_snapshot_refs": tuple(
                f"{product['product_ref']}:inventory_snapshot"
                for product in source_products
                if product.get("variants")
            ),
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
        },
    }


def _authority_scope_list(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        families = value.get("artifact_families")
        if isinstance(families, Sequence) and not isinstance(families, (str, bytes)):
            return [_require_text("requested_authority_scope", str(item)) for item in families]
        return [_require_text("requested_authority_scope", str(key)) for key in value.keys()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_require_text("requested_authority_scope", str(item)) for item in value]
    return [_require_text("requested_authority_scope", str(value))]


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
    payload = cast(
        Mapping[str, Any],
        envelope.get("artifact_payload") or artifact.get("artifact_payload") or artifact.get("payload") or {},
    )
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


def build_bridge_contract_response(
    *,
    channel: str,
    answer: C6ZBuyerAnswer,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
) -> C6ZBridgeContractResponse:
    clean_channel = _require_text("channel", channel)
    if answer.status == "answered":
        suggested_next = "continue_buyer_safe_product_questions"
    elif answer.status == "refused":
        suggested_next = "ask_non_binding_product_question_or_request_human_confirmation"
    else:
        suggested_next = "refresh_shopify_sync_and_grantex_artifacts"
    artifact_refs = tuple(sorted({record.artifact_id for record in cache_records if record.artifact_id}))
    return C6ZBridgeContractResponse(
        channel=clean_channel,
        answer=answer.answer,
        source_label=answer.source_label,
        freshness_label=answer.freshness_label,
        artifact_refs=artifact_refs,
        refusal_reason=answer.refusal_reason,
        suggested_next_safe_action=suggested_next,
        allowed_to_execute=False,
        non_authoritative_for_transaction=True,
    )


def generate_protocol_adapter_payloads(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    products: Sequence[Mapping[str, Any]],
    merchant_id: str,
    seller_agent_id: str,
    now_iso: str,
    buyer_agent_id: str | None = None,
    grantex_available: bool = False,
) -> dict[str, Any]:
    """Generate buyer-safe protocol adapter payloads from validated OACP cache records."""

    usable_records, blocked_reasons = _usable_cache_records(
        cache_records=cache_records,
        action_intent="non_binding_preview",
        now_iso=now_iso,
        grantex_available=grantex_available,
    )
    if not usable_records:
        return {
            "status": "blocked_missing_valid_oacp_artifacts",
            "surfaces": {},
            "missing_or_blocked_reasons": blocked_reasons,
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "non_authoritative_for_transaction": True,
        }

    adapter_context = _adapter_context(
        usable_records=usable_records,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        now_iso=now_iso,
    )
    graph_products = [_schema_org_product(product) for product in products[:100]]
    surfaces: dict[str, Any] = {
        "schema_org_product_offer_jsonld": {
            **adapter_context,
            "@context": "https://schema.org",
            "@graph": graph_products,
            "compatibility": "schema.org_product_offer_jsonld_compatibility_mapping",
            "certification_status": "not_certified_not_published",
        },
        "ucp_style_capability_profile": {
            **adapter_context,
            "profile": "ucp_style_capability_profile",
            "capabilities": [
                "seller_product_question_answering",
                "catalog_snapshot_listing",
                "source_freshness_labeling",
                "prepared_purchase_handoff_explanation",
            ],
            "required_evidence": ["catalog_snapshot", "price", "inventory", "policy", "mandate_capability"],
            "unsupported_capabilities": _unsupported_adapter_capabilities(),
            "certification_status": "compatibility_mapping_only_not_ucp_certified",
        },
        "acp_style_commerce_interaction_profile": {
            **adapter_context,
            "profile": "acp_style_commerce_interaction_profile",
            "interaction_modes": [
                "ask_product_question",
                "list_catalog_snapshot",
                "prepare_non_executing_purchase_handoff",
                "explain_blocker",
            ],
            "commitment_policy": (
                "final commitments require fresh OACP plus provider evidence and remain provider-owned"
            ),
            "unsupported_capabilities": _unsupported_adapter_capabilities(),
            "certification_status": "compatibility_mapping_only_not_acp_certified",
        },
        "ap2_style_mandate_payment_evidence_profile": {
            **adapter_context,
            "profile": "ap2_style_mandate_payment_evidence_profile",
            "mandate_rail_owner": "pine_labs_plural_p3p",
            "agenticorg_role": "provider_capability_verifier_and_prepared_handoff_boundary",
            "grantex_role": "trust_policy_artifact_authority_not_payment_rail",
            "live_payment_execution": "not_enabled_by_adapter_payload",
            "required_provider_evidence": [
                "provider_capability_status",
                "redacted_provider_evidence_ref",
                "checked_at",
                "expires_at",
                "webhook_signature_verification",
                "idempotency_key",
                "reconciliation_state",
            ],
            "certification_status": "compatibility_mapping_only_not_ap2_certified",
        },
        "a2a_agent_card_task_metadata": {
            **adapter_context,
            "name": "AgenticOrg Seller Commerce Agent",
            "protocol": "a2a-compatible-task-metadata",
            "tasks": [
                "answer_product_question_from_oacp_cache",
                "list_public_safe_catalog_snapshot",
                "prepare_purchase_or_mandate_handoff",
                "refuse_unsafe_or_stale_commitment",
            ],
            "limitations": _unsupported_adapter_capabilities(),
            "public_discovery_enabled": False,
        },
        "mcp_tool_resource_metadata": {
            **adapter_context,
            "server": "agenticorg-mcp-server",
            "tools": [
                "seller.list_products",
                "seller.search_products",
                "seller.get_product_facts",
                "seller.get_offer_snapshot",
                "seller.get_inventory_snapshot",
                "seller.ask_product_question",
            ],
            "resources": [
                "oacp://seller/{merchant_id}/{seller_agent_id}/catalog",
                "oacp://seller/{merchant_id}/{seller_agent_id}/freshness",
            ],
            "not_exposed": ["payment creation", "order creation", "mandate creation", "checkout creation"],
        },
        "openapi_buyer_safe_bridge_schema": {
            **adapter_context,
            "openapi": "3.1.0",
            "info": {
                "title": "AgenticOrg OACP Buyer-Safe Bridge",
                "version": "2026-06-19",
            },
            "paths": {
                "/api/v1/commerce/runtime/bridges/openapi/ask": {
                    "post": {
                        "operationId": "askSellerCommerceAgent",
                        "summary": "Ask a product question from cached OACP artifacts.",
                    }
                },
                "/api/v1/commerce/runtime/purchase/prepare": {
                    "post": {
                        "operationId": "preparePurchaseHandoff",
                        "summary": "Prepare a non-executing purchase or mandate handoff.",
                    }
                },
            },
            "x-oacp-non-enablement": {
                "allowed_to_execute": False,
                "no_payment_execution": True,
                "no_public_discovery_enablement": True,
            },
        },
    }
    result = {
        "status": "adapter_payloads_ready",
        "generated_at": now_iso,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "surface_names": list(PROTOCOL_ADAPTER_SURFACES),
        "source_label": "Source: Shopify via Grantex artifact",
        "freshness_label": _freshness_label(usable_records, now_iso),
        "generated_from_artifact_ids": sorted({record.artifact_id for record in usable_records}),
        "surfaces": surfaces,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }
    if contains_private_or_executable_value(result):
        raise C6ZRuntimeValidationError("protocol adapter payload contains private or executable values")
    return result


def select_protocol_adapter_payload(payloads: Mapping[str, Any], surface: str) -> dict[str, Any]:
    surface_name = _require_text("surface", surface)
    surfaces = payloads.get("surfaces") if isinstance(payloads, Mapping) else None
    if surface_name not in PROTOCOL_ADAPTER_SURFACES or not isinstance(surfaces, Mapping):
        raise C6ZRuntimeValidationError("unsupported protocol adapter surface")
    payload = surfaces.get(surface_name)
    if not isinstance(payload, Mapping):
        raise C6ZRuntimeValidationError("protocol adapter surface was not generated")
    return {
        "status": payloads.get("status", "adapter_payloads_ready"),
        "surface": surface_name,
        "payload": dict(payload),
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


def prepare_purchase_or_mandate_handoff(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    products: Sequence[Mapping[str, Any]],
    capability_evidence: Sequence[Mapping[str, Any]],
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None,
    product_ref_or_query: str,
    variant_id: str | None,
    quantity: int,
    now_iso: str,
    idempotency_key: str | None = None,
    grantex_available: bool = True,
    live_execution_enabled: bool = False,
) -> PurchasePreparationResult:
    if quantity < 1:
        return _blocked_purchase_result(
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
            source_label="Source: Shopify via Grantex artifact",
            freshness_label="Freshness: unavailable",
            artifact_refs=(),
            selected_product=None,
            selected_variant=None,
            code="quantity_invalid",
            owner="Buyer agent or calling channel",
            action="Send a positive integer quantity before purchase preparation.",
            unblock_config="request.quantity",
            unblock_command="POST /api/v1/commerce/runtime/purchase/prepare",
        )

    usable_records, blocked_reasons = _usable_cache_records(
        cache_records=cache_records,
        action_intent="prepare_only",
        now_iso=now_iso,
        grantex_available=grantex_available,
    )
    artifact_refs = tuple(sorted({record.artifact_id for record in usable_records}))
    source_label = "Source: Shopify via Grantex artifact"
    freshness_label = (
        _freshness_label(usable_records, now_iso)
        if usable_records
        else "Freshness: missing valid OACP cache"
    )
    required_types = {"catalog_snapshot", "price", "inventory", "policy", "mandate_capability", "protocol_adapter"}
    present_types = {record.artifact_type for record in usable_records}
    missing_types = sorted(required_types - present_types)
    selected_product = _select_product(products, product_ref_or_query)
    selected_variant = _select_variant(selected_product, variant_id) if selected_product is not None else None

    if missing_types:
        return _blocked_purchase_result(
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
            source_label=source_label,
            freshness_label=freshness_label,
            artifact_refs=artifact_refs,
            selected_product=selected_product,
            selected_variant=selected_variant,
            code="oacp_artifacts_missing_or_stale",
            owner="AgenticOrg operator with Grantex tenant allowlist owner",
            action=(
                "Refresh Shopify read-only sync, request Grantex C6Z authority artifacts, "
                "and cache all required families before preparing a commitment."
            ),
            unblock_config="GRANTEX_COMMERCE_BASE_URL, GRANTEX_COMMERCE_INTERNAL_TOKEN, tenant allowlist",
            unblock_command=(
                "POST /api/v1/commerce/runtime/authority/grantex/request then "
                "POST /api/v1/commerce/runtime/artifacts/cache"
            ),
            extra={"missing_artifact_types": missing_types, "blocked_reasons": blocked_reasons},
        )
    if selected_product is None or selected_variant is None:
        return _blocked_purchase_result(
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
            source_label=source_label,
            freshness_label=freshness_label,
            artifact_refs=artifact_refs,
            selected_product=selected_product,
            selected_variant=selected_variant,
            code="product_or_variant_not_found",
            owner="Buyer agent or merchant operator",
            action="Ask for a product and variant present in the latest cached Shopify-backed OACP catalog.",
            unblock_config="product_ref_or_query, variant_id",
            unblock_command="GET /api/v1/commerce/runtime/products?merchant_id=<merchant_id>",
        )
    inventory = selected_variant.get("inventory_quantity_snapshot")
    if isinstance(inventory, int) and inventory < quantity:
        return _blocked_purchase_result(
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
            source_label=source_label,
            freshness_label=freshness_label,
            artifact_refs=artifact_refs,
            selected_product=selected_product,
            selected_variant=selected_variant,
            code="inventory_snapshot_insufficient",
            owner="Merchant Shopify operator",
            action="Refresh inventory in Shopify and re-sync AgenticOrg before purchase preparation.",
            unblock_config="Shopify Admin API read_inventory scope and product availability",
            unblock_command="POST /api/v1/commerce/runtime/seller-agents/shopify/sync",
        )

    latest_capability = _latest_available_capability(capability_evidence, now_iso)
    if latest_capability is None:
        return _blocked_purchase_result(
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
            quantity=quantity,
            idempotency_key=idempotency_key,
            source_label=source_label,
            freshness_label=freshness_label,
            artifact_refs=artifact_refs,
            selected_product=selected_product,
            selected_variant=selected_variant,
            code="plural_pine_capability_missing_or_stale",
            owner="Merchant payment owner and AgenticOrg operator",
            action="Configure Pine Labs Plural/P3P capability credentials and run the capability verifier.",
            unblock_config=(
                "PLURAL_PINE_CLIENT_ID, PLURAL_PINE_CLIENT_SECRET, "
                "PLURAL_PINE_ENVIRONMENT=sandbox, optional PLURAL_PINE_CAPABILITY_URL"
            ),
            unblock_command="POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify",
        )

    key = _purchase_idempotency_key(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        product_ref=str(selected_product.get("product_ref")),
        variant_id=str(selected_variant.get("variant_id")),
        quantity=quantity,
        provided=idempotency_key,
    )
    handoff = {
        "handoff_id": _stable_id("purchase_handoff", key),
        "handoff_mode": "provider_owned_plural_pine_p3p_preparation",
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "product_ref": selected_product.get("product_ref"),
        "variant_ref": _redacted_ref("shopify_variant", selected_variant.get("variant_id")),
        "sku": selected_variant.get("sku"),
        "quantity": quantity,
        "price_snapshot": selected_variant.get("price"),
        "currency": selected_variant.get("currency"),
        "provider": "plural_pine",
        "provider_environment": latest_capability.get("provider_environment"),
        "provider_capability_status": latest_capability.get("result_status"),
        "provider_capability_evidence_ref": latest_capability.get("redacted_evidence_ref"),
        "idempotency_key": key,
        "callback_verification_required": True,
        "rollback_void_refusal_behavior": (
            "refuse_or_void_provider_owned_handoff_if_callback_or_reconciliation_mismatch"
        ),
        "live_execution_enabled": live_execution_enabled,
        "allowed_to_execute": False,
        "no_payment_execution": True,
    }
    blocker = None
    status = "prepared_provider_handoff"
    if not live_execution_enabled:
        status = "prepared_handoff_blocked_live_execution_disabled"
        blocker = {
            "code": "live_provider_execution_not_enabled",
            "owner": "Merchant payment owner, Pine Labs Plural/P3P owner, and AgenticOrg operator",
            "action": "Approve and configure provider-owned live execution before routing this handoff to Plural/Pine.",
            "required_config": (
                "PLURAL_PINE_LIVE_EXECUTION_ENABLED=true plus provider-approved merchant/live secret refs"
            ),
            "unblock_command": (
                "POST /api/v1/commerce/runtime/purchase/prepare with live_execution_approved=true "
                "after operator approval"
            ),
            "doc_reference": "docs/runbooks/oacp-shopify-merchant-onboarding.md#pluralpine-p3p-setup",
        }
    return PurchasePreparationResult(
        status=status,
        idempotency_key=key,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        source_label=source_label,
        freshness_label=freshness_label,
        artifact_refs=artifact_refs,
        selected_product=_public_product_summary(selected_product),
        selected_variant=_public_variant_summary(selected_variant),
        quantity=quantity,
        prepared_handoff=handoff,
        blocker=blocker,
        reconciliation_state={
            "state": (
                "prepared_pending_provider_handoff"
                if live_execution_enabled
                else "blocked_before_provider_execution"
            ),
            "idempotency_key": key,
            "callback_required": True,
            "raw_provider_payload_stored": False,
            "rollback_supported": True,
        },
        allowed_to_execute=False,
        no_payment_execution=True,
        non_authoritative_for_transaction=True,
    )


def verify_whatsapp_webhook_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    if not raw_body or not signature_header or not app_secret:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def verify_telegram_webhook_secret(header_value: str | None, expected_secret: str) -> bool:
    return bool(expected_secret) and hmac.compare_digest(str(header_value or ""), expected_secret)


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
    provider_environment = _plural_pine_environment(source)
    if provider_environment != "sandbox":
        return PluralPineCapabilityEvidence(
            evidence_id=_stable_id("plural_pine_capability", tenant_id, merchant_id, capability_type, checked_at),
            provider="plural_pine",
            capability_type=capability_type,
            result_status="blocked_provider_error",
            checked_at=checked_at,
            expires_at=expires_at,
            redacted_evidence_ref="provider:plural_pine:capability:non-sandbox-blocked:redacted",
            provider_environment=provider_environment,
            external_validation_performed=False,
            missing_env_vars=(),
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
    explicit_capability_url = str(source.get("PLURAL_PINE_CAPABILITY_URL", "") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
            if explicit_capability_url:
                response = await client.post(explicit_capability_url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
            else:
                response = await client.post(
                    f"{_plural_pine_base_url(source)}/auth/v1/token",
                    json={
                        "client_id": source["PLURAL_PINE_CLIENT_ID"],
                        "client_secret": source["PLURAL_PINE_CLIENT_SECRET"],
                        "grant_type": "client_credentials",
                    },
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                token_body = response.json()
                access_token = token_body.get("access_token") if isinstance(token_body, Mapping) else None
                body = {
                    "result": {
                        "tools": [{"name": "mandate_capability.token_verification"}] if access_token else []
                    }
                }
    except (httpx.HTTPError, ValueError) as exc:
        return PluralPineCapabilityEvidence(
            evidence_id=_stable_id("plural_pine_capability", tenant_id, merchant_id, capability_type, checked_at),
            provider="plural_pine",
            capability_type=capability_type,
            result_status="blocked_provider_error",
            checked_at=checked_at,
            expires_at=expires_at,
            redacted_evidence_ref=f"provider:plural_pine:capability:error:{_sha256(str(exc))[:16]}:redacted",
            provider_environment=provider_environment,
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
        provider_environment=provider_environment,
        external_validation_performed=True,
        missing_env_vars=(),
    )


def _plural_pine_environment(source: Mapping[str, str]) -> str:
    value = str(source.get("PLURAL_PINE_ENVIRONMENT", "sandbox") or "sandbox").strip().lower()
    if value in {"uat", "test"}:
        return "sandbox"
    return value


def _plural_pine_base_url(source: Mapping[str, str]) -> str:
    configured = str(source.get("PLURAL_PINE_BASE_URL") or source.get("PLURAL_BASE_URL") or "").strip()
    return configured.rstrip("/") or PLURAL_PINE_SANDBOX_BASE_URL


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


def _usable_cache_records(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    action_intent: PersistentCacheActionIntent,
    now_iso: str,
    grantex_available: bool,
) -> tuple[list[OacpPersistentArtifactCacheRecord], list[str]]:
    usable_records: list[OacpPersistentArtifactCacheRecord] = []
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
        elif result.get("refusal_code"):
            blocked_reasons.append(str(result["refusal_code"]))
    return usable_records, sorted(set(blocked_reasons))


def _adapter_context(
    *,
    usable_records: Sequence[OacpPersistentArtifactCacheRecord],
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None,
    now_iso: str,
) -> dict[str, Any]:
    return {
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "generated_at": now_iso,
        "source_label": "Source: Shopify via Grantex artifact",
        "freshness_label": _freshness_label(usable_records, now_iso),
        "source_freshness": [
            {
                "artifact_id": record.artifact_id,
                "artifact_type": record.artifact_type,
                "issuer": record.issuer,
                "cached_at": record.cached_at,
                "expires_at": record.expires_at,
                "freshness_status": record.freshness_status,
                "verifier_result_ref": record.verifier_result_ref,
                "source_refs": list(record.source_refs),
            }
            for record in usable_records
        ],
        "generated_from_artifact_ids": sorted({record.artifact_id for record in usable_records}),
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
    }


def _schema_org_product(product: Mapping[str, Any]) -> dict[str, Any]:
    offers = []
    for variant in product.get("variants", ()) or ():
        if not isinstance(variant, Mapping):
            continue
        inventory = variant.get("inventory_quantity_snapshot")
        availability = "https://schema.org/InStock" if isinstance(inventory, int) and inventory > 0 else "https://schema.org/OutOfStock"
        offer = {
            "@type": "Offer",
            "@id": _redacted_ref("schema_offer", f"{product.get('product_ref')}:{variant.get('variant_id')}"),
            "sku": variant.get("sku"),
            "price": variant.get("price"),
            "priceCurrency": variant.get("currency"),
            "availability": availability,
            "inventoryLevel": inventory,
            "validFrom": variant.get("updated_at") or product.get("updated_at") or product.get("synced_at"),
            "url": None,
        }
        offers.append({key: value for key, value in offer.items() if value is not None})
    images = [
        image.get("url")
        for image in product.get("images", ())
        if isinstance(image, Mapping) and image.get("url")
    ]
    schema_product = {
        "@type": "Product",
        "@id": product.get("product_ref"),
        "name": product.get("title"),
        "description": _plain_text(product.get("description")),
        "brand": product.get("vendor"),
        "category": product.get("product_type"),
        "image": images,
        "offers": offers,
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "shopify_status", "value": product.get("status")},
            {"@type": "PropertyValue", "name": "source_mode", "value": product.get("source_mode")},
            {"@type": "PropertyValue", "name": "synced_at", "value": product.get("synced_at")},
        ],
    }
    return {key: value for key, value in schema_product.items() if value not in (None, [], "")}


def _plain_text(value: Any) -> str | None:
    text = _safe_optional_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip() or None


def _unsupported_adapter_capabilities() -> list[str]:
    return [
        "checkout_execution",
        "payment_execution",
        "order_creation",
        "inventory_hold_execution",
        "mandate_creation",
        "refund_execution",
        "return_execution",
        "shipping_execution",
        "public_discovery_publication",
        "certification_or_standardization_claim",
    ]


def _select_product(products: Sequence[Mapping[str, Any]], product_ref_or_query: str) -> Mapping[str, Any] | None:
    query = _require_text("product_ref_or_query", product_ref_or_query).lower()
    for product in products:
        candidates = (
            product.get("product_ref"),
            product.get("source_product_id"),
            product.get("title"),
            product.get("vendor"),
            product.get("product_type"),
        )
        if any(query == str(value or "").lower() for value in candidates):
            return product
    matches = _match_products(products, query)
    return matches[0] if matches else None


def _select_variant(product: Mapping[str, Any] | None, variant_id: str | None) -> Mapping[str, Any] | None:
    if product is None:
        return None
    variants = [variant for variant in product.get("variants", ()) if isinstance(variant, Mapping)]
    if not variants:
        return None
    if not variant_id:
        return variants[0]
    query = variant_id.lower()
    for variant in variants:
        if query in {
            str(variant.get("variant_id") or "").lower(),
            str(variant.get("sku") or "").lower(),
            str(variant.get("title") or "").lower(),
        }:
            return variant
    return None


def _latest_available_capability(
    capability_evidence: Sequence[Mapping[str, Any]],
    now_iso: str,
) -> Mapping[str, Any] | None:
    now = _parse_iso(now_iso)
    candidates: list[Mapping[str, Any]] = []
    for item in capability_evidence:
        if item.get("result_status") != "available":
            continue
        try:
            expires_at = _parse_iso(str(item.get("expires_at") or ""))
        except ValueError:
            continue
        if expires_at <= now:
            continue
        if item.get("raw_payload_stored") not in (False, None):
            continue
        candidates.append(item)
    return (
        sorted(candidates, key=lambda item: str(item.get("checked_at") or ""), reverse=True)[0]
        if candidates
        else None
    )


def _blocked_purchase_result(
    *,
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None,
    quantity: int,
    idempotency_key: str | None,
    source_label: str,
    freshness_label: str,
    artifact_refs: Sequence[str],
    selected_product: Mapping[str, Any] | None,
    selected_variant: Mapping[str, Any] | None,
    code: str,
    owner: str,
    action: str,
    unblock_config: str,
    unblock_command: str,
    extra: Mapping[str, Any] | None = None,
) -> PurchasePreparationResult:
    blocker = {
        "code": code,
        "owner": owner,
        "action": action,
        "required_config": unblock_config,
        "unblock_command": unblock_command,
        "doc_reference": "docs/runbooks/oacp-shopify-merchant-onboarding.md",
    }
    if extra:
        blocker.update(dict(extra))
    key = idempotency_key or _stable_id("purchase_prepare", merchant_id, seller_agent_id, buyer_agent_id or "", code)
    return PurchasePreparationResult(
        status="blocked",
        idempotency_key=key,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        source_label=source_label,
        freshness_label=freshness_label,
        artifact_refs=tuple(artifact_refs),
        selected_product=_public_product_summary(selected_product) if selected_product else None,
        selected_variant=_public_variant_summary(selected_variant) if selected_variant else None,
        quantity=quantity,
        prepared_handoff=None,
        blocker=blocker,
        reconciliation_state={
            "state": "blocked_before_provider_execution",
            "reason": code,
            "raw_provider_payload_stored": False,
            "rollback_supported": True,
        },
        allowed_to_execute=False,
        no_payment_execution=True,
        non_authoritative_for_transaction=True,
    )


def _purchase_idempotency_key(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None,
    product_ref: str,
    variant_id: str,
    quantity: int,
    provided: str | None,
) -> str:
    if provided and provided.strip():
        return _require_text("idempotency_key", provided)
    return _stable_id(
        "purchase_prepare",
        tenant_id,
        merchant_id,
        seller_agent_id,
        buyer_agent_id or "",
        product_ref,
        variant_id,
        quantity,
    )


def _public_product_summary(product: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "product_ref": product.get("product_ref"),
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "status": product.get("status"),
        "updated_at": product.get("updated_at"),
        "synced_at": product.get("synced_at"),
        "source_system": product.get("source_system"),
        "source_mode": product.get("source_mode"),
    }


def _public_variant_summary(variant: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "variant_ref": _redacted_ref("shopify_variant", variant.get("variant_id")),
        "sku": variant.get("sku"),
        "title": variant.get("title"),
        "price": variant.get("price"),
        "currency": variant.get("currency"),
        "inventory_quantity_snapshot": variant.get("inventory_quantity_snapshot"),
        "updated_at": variant.get("updated_at"),
    }


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
