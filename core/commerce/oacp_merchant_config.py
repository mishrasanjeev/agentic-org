"""Merchant-scoped OACP commerce configuration helpers.

The settings in this module are tenant and merchant scoped. They describe
which source systems, buyer channels, payment providers, and POS stores a
merchant has configured without storing raw secrets or claiming execution.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from core.commerce.c6z_runtime_vertical import (
    EXECUTION_MARKERS,
    PRIVATE_KEY_MARKERS,
    PRIVATE_VALUE_MARKERS,
    contains_private_or_executable_value,
)

SOURCE_CONNECTOR_TYPES: frozenset[str] = frozenset(
    {
        "shopify",
        "woocommerce",
        "erp",
        "pim",
        "oms",
        "wms",
        "custom_api",
    }
)
SOURCE_CONNECTOR_MODES: frozenset[str] = frozenset({"read_only", "external_reference"})
PAYMENT_PROVIDER_TYPES: frozenset[str] = frozenset(
    {
        "plural_pine",
        "bank",
        "fintech_rail",
        "custom_provider",
        "none",
    }
)
BUYER_CHANNELS: frozenset[str] = frozenset(
    {"web", "chatgpt", "claude", "gemini", "perplexity", "whatsapp", "telegram"}
)
EXTERNAL_APPROVAL_STATUSES: frozenset[str] = frozenset(
    {"not_required", "not_started", "pending", "approved", "rejected"}
)
CREDENTIAL_CUSTODY_OPTIONS: frozenset[str] = frozenset(
    {
        "agenticorg_vault",
        "merchant_owned_integration",
        "external_integration_provider",
        "provider_owned",
        "not_required",
    }
)
SUPPORTED_RUNTIME_SOURCE_CONNECTORS: frozenset[str] = frozenset({"shopify"})
SUPPORTED_RUNTIME_PAYMENT_PROVIDERS: frozenset[str] = frozenset({"plural_pine"})


class MerchantCommerceConfigError(ValueError):
    """Raised when merchant commerce configuration is unsafe or invalid."""


def normalize_merchant_commerce_config(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    merchant_display_name: str,
    public_brand_profile: Mapping[str, Any] | None,
    commerce_categories: Sequence[str],
    source_connectors: Sequence[Mapping[str, Any]],
    buyer_channels: Mapping[str, Mapping[str, Any] | bool],
    payment_providers: Sequence[Mapping[str, Any]],
    offline_pos_stores: Sequence[Mapping[str, Any]],
    public_publishing: Mapping[str, Any] | None,
    source_freshness_policy: Mapping[str, Any] | None,
    provider_policy: Mapping[str, Any] | None,
    status: str | None = None,
) -> dict[str, Any]:
    """Return a normalized, redacted tenant/merchant commerce config."""

    merchant = _safe_id(merchant_id, "merchant_id")
    seller = _safe_optional_id(seller_agent_id, "seller_agent_id")
    sources = [_normalize_source_connector(item) for item in source_connectors]
    providers = [_normalize_payment_provider(item) for item in payment_providers]
    channels = _normalize_channels(buyer_channels)
    pos_stores = [_normalize_pos_store(item) for item in offline_pos_stores]
    config = {
        "tenant_id": _safe_text(tenant_id),
        "merchant_id": merchant,
        "seller_agent_id": seller,
        "merchant_display_name": _safe_text(merchant_display_name, fallback="Seller Commerce Agent"),
        "public_brand_profile": _safe_mapping(public_brand_profile or {}),
        "commerce_categories": [_safe_text(item) for item in commerce_categories if _safe_text(item)],
        "source_connectors": sources,
        "buyer_channels": channels,
        "payment_providers": providers,
        "offline_pos_stores": pos_stores,
        "public_publishing": _normalize_public_publishing(public_publishing or {}),
        "source_freshness_policy": _safe_mapping(source_freshness_policy or {"max_age_seconds": 900}),
        "provider_policy": _safe_mapping(provider_policy or {}),
        "status": status or "configured",
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }
    if not sources:
        raise MerchantCommerceConfigError("at least one source connector must be configured")
    if not config["commerce_categories"]:
        raise MerchantCommerceConfigError("commerce_categories are required")
    if _contains_unsafe_config_value(config):
        raise MerchantCommerceConfigError("merchant commerce config contains private or executable values")
    return config


def merchant_config_readiness(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build merchant-facing readiness from a normalized config."""

    source_states = []
    for connector in config.get("source_connectors") or []:
        if not isinstance(connector, Mapping):
            continue
        connector_type = str(connector.get("connector_type") or "")
        runtime_supported = connector_type in SUPPORTED_RUNTIME_SOURCE_CONNECTORS
        source_states.append(
            {
                "connector_key": connector.get("connector_key"),
                "connector_type": connector_type,
                "store_id": connector.get("store_id"),
                "status": (
                    "runtime_ready"
                    if runtime_supported and connector.get("enabled") is True
                    else "configured_pending_adapter"
                ),
                "runtime_supported": runtime_supported,
                "credential_ref_present": bool(connector.get("credential_ref")),
                "source_of_record": connector.get("source_of_record"),
            }
        )

    channel_states = []
    for channel, value in (config.get("buyer_channels") or {}).items():
        if not isinstance(value, Mapping):
            continue
        enabled = value.get("enabled") is True
        approval = str(value.get("external_approval_status") or "not_started")
        channel_states.append(
            {
                "channel": channel,
                "enabled": enabled,
                "status": _channel_status(channel, enabled, approval, bool(value.get("credential_ref"))),
                "external_approval_status": approval,
                "credential_ref_present": bool(value.get("credential_ref")),
            }
        )

    provider_states = []
    for provider in config.get("payment_providers") or []:
        if not isinstance(provider, Mapping):
            continue
        provider_type = str(provider.get("provider_type") or "")
        supported = provider_type in SUPPORTED_RUNTIME_PAYMENT_PROVIDERS or provider_type == "bank"
        provider_states.append(
            {
                "provider_key": provider.get("provider_key"),
                "provider_type": provider_type,
                "provider_display_name": provider.get("provider_display_name"),
                "status": "configured_provider_owned" if supported else "configured_pending_adapter",
                "runtime_supported": provider_type in SUPPORTED_RUNTIME_PAYMENT_PROVIDERS,
                "bank_owned_provider": provider_type == "bank",
                "credential_ref_present": bool(provider.get("credential_ref")),
                "owns_execution": provider.get("owns_execution") is True,
            }
        )

    pos_states = []
    for store in config.get("offline_pos_stores") or []:
        if not isinstance(store, Mapping):
            continue
        pos_states.append(
            {
                "store_id": store.get("store_id"),
                "display_name": store.get("display_name"),
                "pos_provider": store.get("pos_provider"),
                "status": "configured" if store.get("enabled") is True else "disabled",
                "webhook_secret_ref_present": bool(store.get("webhook_secret_ref")),
            }
        )

    public_publishing = config.get("public_publishing") or {}
    public_enabled = isinstance(public_publishing, Mapping) and public_publishing.get("enabled") is True
    return {
        "status": "merchant_config_ready",
        "tenant_id": config.get("tenant_id"),
        "merchant_id": config.get("merchant_id"),
        "seller_agent_id": config.get("seller_agent_id"),
        "source_connectors": source_states,
        "buyer_channels": channel_states,
        "payment_providers": provider_states,
        "offline_pos_stores": pos_states,
        "public_publishing": {
            "enabled": public_enabled,
            "status": "enabled" if public_enabled else "disabled",
            "requires_source_evidence": True,
            "allowed_to_execute": False,
        },
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


def merchant_config_id(tenant_id: str, merchant_id: str, seller_agent_id: str | None) -> str:
    raw = f"{tenant_id}:{merchant_id}:{seller_agent_id or 'default'}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"commerce_config_{digest}"


def integration_credential_config_name(
    *,
    merchant_id: str,
    integration_kind: str,
    provider_key: str,
    store_id: str | None = None,
) -> str:
    raw = f"{integration_kind}:{merchant_id}:{provider_key}:{store_id or 'default'}"
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()[:72]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"commerce_{suffix}_{digest}"


def _normalize_source_connector(item: Mapping[str, Any]) -> dict[str, Any]:
    connector_type = _one_of(item.get("connector_type") or item.get("source_system"), SOURCE_CONNECTOR_TYPES)
    mode = _one_of(item.get("mode") or "read_only", SOURCE_CONNECTOR_MODES)
    custody = _one_of(item.get("credential_custody") or "merchant_owned_integration", CREDENTIAL_CUSTODY_OPTIONS)
    connector = {
        "connector_key": _safe_optional_id(item.get("connector_key"), "connector_key")
        or f"{connector_type}:{_safe_text(item.get('store_id'), fallback='default')}",
        "connector_type": connector_type,
        "store_id": _safe_optional_id(item.get("store_id"), "store_id"),
        "mode": mode,
        "enabled": bool(item.get("enabled", True)),
        "credential_custody": custody,
        "credential_ref": _safe_optional_ref(item.get("credential_ref")),
        "source_of_record": _safe_text(item.get("source_of_record"), fallback=connector_type),
        "shop_domain": _safe_text(item.get("shop_domain")),
        "base_url": _safe_text(item.get("base_url")),
        "api_version": _safe_text(item.get("api_version"), fallback=""),
        "sync_enabled": bool(item.get("sync_enabled", connector_type == "shopify")),
        "adapter_status": (
            "runtime_supported" if connector_type in SUPPORTED_RUNTIME_SOURCE_CONNECTORS else "pending_adapter"
        ),
        "raw_payload_stored": False,
        "allowed_to_execute": False,
    }
    if connector_type == "shopify" and mode != "read_only":
        raise MerchantCommerceConfigError("Shopify connector mode must be read_only")
    return connector


def _normalize_payment_provider(item: Mapping[str, Any]) -> dict[str, Any]:
    provider_type = _one_of(item.get("provider_type") or item.get("provider"), PAYMENT_PROVIDER_TYPES)
    custody = _one_of(item.get("credential_custody") or "provider_owned", CREDENTIAL_CUSTODY_OPTIONS)
    provider_key = _safe_optional_id(item.get("provider_key"), "provider_key") or provider_type
    return {
        "provider_key": provider_key,
        "provider_type": provider_type,
        "provider_display_name": _safe_text(item.get("provider_display_name"), fallback=provider_key),
        "environment": _safe_text(item.get("environment"), fallback="sandbox"),
        "enabled": bool(item.get("enabled", provider_type != "none")),
        "credential_custody": custody,
        "credential_ref": _safe_optional_ref(item.get("credential_ref")),
        "capability_types": [
            _safe_text(value)
            for value in item.get("capability_types", ["mandate_capability"])
            if _safe_text(value)
        ],
        "owns_execution": True,
        "agenticorg_executes_payment": False,
        "grantex_executes_payment": False,
        "adapter_status": (
            "runtime_supported" if provider_type in SUPPORTED_RUNTIME_PAYMENT_PROVIDERS else "provider_owned"
        ),
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
    }


def _normalize_channels(items: Mapping[str, Mapping[str, Any] | bool]) -> dict[str, dict[str, Any]]:
    channels: dict[str, dict[str, Any]] = {}
    for channel in sorted(BUYER_CHANNELS):
        raw = items.get(channel, False)
        if isinstance(raw, bool):
            enabled = raw
            data: Mapping[str, Any] = {}
        elif isinstance(raw, Mapping):
            enabled = bool(raw.get("enabled", False))
            data = raw
        else:
            raise MerchantCommerceConfigError(f"buyer channel {channel} must be an object or boolean")
        approval = _one_of(data.get("external_approval_status") or "not_required", EXTERNAL_APPROVAL_STATUSES)
        if channel in {"chatgpt", "claude", "gemini", "perplexity", "whatsapp", "telegram"} and enabled:
            if approval == "not_required":
                approval = "not_started"
        channels[channel] = {
            "enabled": enabled,
            "credential_ref": _safe_optional_ref(data.get("credential_ref")),
            "external_approval_status": approval,
            "public_surface": channel in {"web", "perplexity"},
            "allowed_to_execute": False,
        }
    return channels


def _normalize_pos_store(item: Mapping[str, Any]) -> dict[str, Any]:
    store_id = _safe_id(item.get("store_id"), "store_id")
    return {
        "store_id": store_id,
        "display_name": _safe_text(item.get("display_name"), fallback=store_id),
        "pos_provider": _safe_text(item.get("pos_provider"), fallback="merchant_pos"),
        "enabled": bool(item.get("enabled", True)),
        "city": _safe_text(item.get("city")),
        "country_code": _safe_text(item.get("country_code"), fallback="IN")[:3].upper(),
        "webhook_secret_ref": _safe_optional_ref(item.get("webhook_secret_ref")),
        "staff_review_required": bool(item.get("staff_review_required", True)),
        "raw_payload_stored": False,
        "allowed_to_execute": False,
    }


def _normalize_public_publishing(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(item.get("enabled", False)),
        "base_url": _safe_text(item.get("base_url"), fallback=""),
        "publish_schema_org": bool(item.get("publish_schema_org", True)),
        "publish_llms_txt": bool(item.get("publish_llms_txt", True)),
        "publish_sitemap": bool(item.get("publish_sitemap", True)),
        "external_approval_status": _one_of(
            item.get("external_approval_status") or "not_required",
            EXTERNAL_APPROVAL_STATUSES,
        ),
        "no_certification_claim": True,
        "allowed_to_execute": False,
    }


def _channel_status(channel: str, enabled: bool, approval: str, credential_ref_present: bool) -> str:
    if not enabled:
        return "disabled"
    if channel == "web":
        return "ready"
    if channel in {"chatgpt", "claude", "gemini", "perplexity"}:
        return "configured_pending_external_approval" if approval != "approved" else "ready"
    if channel in {"whatsapp", "telegram"} and not credential_ref_present:
        return "blocked_missing_channel_credential_ref"
    return "ready" if approval in {"approved", "not_required"} else "configured_pending_external_approval"


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = _safe_text(key)
        if isinstance(item, str | int | float | bool) or item is None:
            clean[clean_key] = item
        elif isinstance(item, Mapping):
            clean[clean_key] = _safe_mapping(item)
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            clean[clean_key] = [_safe_text(element) for element in item if _safe_text(element)]
    if _contains_unsafe_config_value(clean):
        raise MerchantCommerceConfigError("metadata contains private or executable values")
    return clean


def _safe_id(value: Any, name: str) -> str:
    text = _safe_text(value)
    if not text:
        raise MerchantCommerceConfigError(f"{name} is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", text):
        raise MerchantCommerceConfigError(f"{name} must be an opaque identifier")
    return text


def _safe_optional_id(value: Any, name: str) -> str | None:
    text = _safe_text(value)
    return _safe_id(text, name) if text else None


def _safe_optional_ref(value: Any) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    if contains_private_or_executable_value(text):
        raise MerchantCommerceConfigError("credential_ref contains unsafe values")
    return text[:240]


def _safe_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text[:500] if text else fallback


def _one_of(value: Any, allowed: frozenset[str]) -> str:
    text = _safe_text(value).lower()
    if text not in allowed:
        raise MerchantCommerceConfigError(f"unsupported value {text or '<empty>'}")
    return text


def _contains_unsafe_config_value(value: Any) -> bool:
    allowed_ref_keys = {
        "credential_custody",
        "credential_ref",
        "webhook_secret_ref",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text == "raw_payload_stored" and item is False:
                continue
            if key_text in allowed_ref_keys:
                continue
            if _text_has_marker(key_text, PRIVATE_KEY_MARKERS) or _text_has_marker(key_text, EXECUTION_MARKERS):
                return True
            if _contains_unsafe_config_value(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_unsafe_config_value(item) for item in value)
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            return _public_url_has_unsafe_markers(value)
        return contains_private_or_executable_value(value)
    return False


def _public_url_has_unsafe_markers(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True
    text = value.lower()
    if _text_has_marker(text, PRIVATE_VALUE_MARKERS) or _text_has_marker(text, EXECUTION_MARKERS):
        return True
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        key_text = key.lower()
        item_text = item.lower()
        if _text_has_marker(key_text, PRIVATE_KEY_MARKERS) or _text_has_marker(key_text, EXECUTION_MARKERS):
            return True
        if _text_has_marker(item_text, PRIVATE_VALUE_MARKERS) or _text_has_marker(item_text, EXECUTION_MARKERS):
            return True
    return False


def _text_has_marker(value: str, markers: Sequence[str]) -> bool:
    return any(marker in value for marker in markers)
