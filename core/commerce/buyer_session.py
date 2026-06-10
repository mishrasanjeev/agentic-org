"""Channel-neutral buyer discovery session orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.commerce.buyer_discovery import (
    C6G_PREVIEW_ENDPOINT,
    CHECKOUT_PAYMENT_TERMS,
    FULFILLMENT_TERMS,
    LIVE_PROVIDER_TERMS,
    MERCHANT_PRIVATE_API_TERMS,
    REFUND_RETURN_TERMS,
    build_buyer_discovery_response,
)

BUYER_SESSION_CONTRACT = "agenticorg.commerce.buyer_discovery_session.v1"

READ_ONLY_DISCOVERY_TERMS = (
    "merchant",
    "seller",
    "store",
    "catalog",
    "catalogue",
    "product",
    "products",
    "item",
    "items",
    "preview",
    "discover",
    "discovery",
    "show",
    "search",
    "browse",
    "compare",
    "detail",
    "details",
    "brand",
    "category",
    "price",
    "inventory",
    "availability",
)

CHANNEL_NEUTRAL_TARGETS = (
    "chatgpt",
    "claude",
    "gemini",
    "whatsapp",
    "telegram",
    "web_chat",
    "future_channel",
)

READ_ONLY_ALLOWED_CAPABILITIES = (
    "read_only_profile_discovery_preview",
    "read_only_catalog_discovery_preview",
    "buyer_agent_readiness_context",
)

BLOCKED_CAPABILITIES_BY_INTENT = {
    "checkout_payment": (
        "checkout_payment_creation",
        "cart_checkout_handoff",
        "payment_intent_creation",
        "live_payment",
    ),
    "live_provider": (
        "live_provider_access",
        "live_plural",
        "provider_credential_access",
        "provider_private_api",
    ),
    "merchant_private_api": (
        "merchant_private_api",
        "merchant_existing_system",
        "direct_merchant_system_access",
    ),
    "fulfillment": (
        "order_fulfillment",
        "shipping_delivery",
        "dispatch_tracking",
        "order_status_execution",
    ),
    "refund_return": (
        "refunds_returns_execution",
        "replacement_execution",
        "chargeback_execution",
    ),
    "unsupported": ("unsupported_non_discovery_request",),
}


def classify_buyer_session_intent(request_text: str) -> str:
    """Classify buyer intent before any Grantex call or side-effect path."""
    text = str(request_text or "").strip().lower()
    if _contains_any(text, MERCHANT_PRIVATE_API_TERMS):
        return "merchant_private_api"
    if _contains_any(text, LIVE_PROVIDER_TERMS):
        return "live_provider"
    if _contains_any(text, CHECKOUT_PAYMENT_TERMS):
        return "checkout_payment"
    if _contains_any(text, FULFILLMENT_TERMS):
        return "fulfillment"
    if _contains_any(text, REFUND_RETURN_TERMS):
        return "refund_return"
    if not text or _contains_any(text, READ_ONLY_DISCOVERY_TERMS):
        return "read_only_discovery"
    return "unsupported"


async def start_buyer_discovery_session(
    connector: Any,
    *,
    merchant_id: str,
    request_text: str = "",
    channel: str = "future_channel",
) -> dict[str, Any]:
    """Start a safe read-only buyer session around Grantex preview data."""
    intent = classify_buyer_session_intent(request_text)
    if intent != "read_only_discovery":
        return _channel_response(
            _intent_refusal(intent),
            intent=intent,
            channel=channel,
            grantex_call_status="not_attempted",
        )

    raw = await connector.buyer_discovery_preview(merchant_id=merchant_id)
    response = build_buyer_discovery_response(raw, request_text=request_text)
    return _channel_response(
        response,
        intent=intent,
        channel=channel,
        grantex_call_status="completed",
    )


def build_channel_neutral_buyer_response(
    grantex_payload: Mapping[str, Any] | None,
    *,
    request_text: str = "",
    channel: str = "future_channel",
) -> dict[str, Any]:
    """Build the session response shape from an already fetched Grantex payload."""
    intent = classify_buyer_session_intent(request_text)
    response = (
        build_buyer_discovery_response(grantex_payload, request_text=request_text)
        if intent == "read_only_discovery"
        else _intent_refusal(intent)
    )
    return _channel_response(
        response,
        intent=intent,
        channel=channel,
        grantex_call_status="completed" if intent == "read_only_discovery" else "not_attempted",
    )


def _intent_refusal(intent: str) -> dict[str, Any]:
    messages = {
        "checkout_payment": (
            "Checkout and payment are blocked in this read-only buyer discovery session. "
            "I can only show Grantex-grounded preview information."
        ),
        "live_provider": (
            "Live providers, live Plural, provider credentials, and provider APIs are blocked in this session."
        ),
        "merchant_private_api": (
            "Merchant private APIs and merchant existing systems are blocked in this session. "
            "Commerce requests must use Grantex-owned contracts."
        ),
        "fulfillment": (
            "Fulfillment, shipping, delivery, dispatch, tracking, and order-status execution are blocked here."
        ),
        "refund_return": "Refund, return, replacement, and chargeback execution are blocked here.",
        "unsupported": "This buyer session only supports Grantex-grounded read-only merchant and catalog discovery.",
    }
    return {
        "status": "refused",
        "refusal": True,
        "refusal_code": "merchant_private_api_not_allowed"
        if intent == "merchant_private_api"
        else f"{intent}_not_enabled",
        "message": messages[intent],
        "allowed_capabilities": list(READ_ONLY_ALLOWED_CAPABILITIES),
        "blocked_capabilities": list(BLOCKED_CAPABILITIES_BY_INTENT[intent]),
        "source_reference": _default_source_reference(),
    }


def _channel_response(
    response: Mapping[str, Any],
    *,
    intent: str,
    channel: str,
    grantex_call_status: str,
) -> dict[str, Any]:
    return {
        "contract": BUYER_SESSION_CONTRACT,
        "channel": _safe_channel(channel),
        "channel_neutral": True,
        "supported_channel_targets": list(CHANNEL_NEUTRAL_TARGETS),
        "intent": intent,
        "status": _safe_text(response.get("status") or "refused"),
        "message": _safe_text(response.get("message"), limit=800),
        "merchant_preview": _mapping(response.get("merchant")),
        "catalog_samples": _safe_list_of_mappings(response.get("catalog_samples")),
        "allowed_capabilities": _string_list(
            response.get("allowed_capabilities") or READ_ONLY_ALLOWED_CAPABILITIES
        ),
        "blocked_capabilities": _string_list(response.get("blocked_capabilities")),
        "source_reference": _mapping(response.get("source_reference")) or _default_source_reference(),
        "refusal_code": _safe_refusal_code(response.get("refusal_code")),
        "evidence_summary": _evidence_summary(
            response,
            intent=intent,
            grantex_call_status=grantex_call_status,
        ),
    }


def _evidence_summary(
    response: Mapping[str, Any],
    *,
    intent: str,
    grantex_call_status: str,
) -> dict[str, Any]:
    status = _safe_text(response.get("status") or "refused")
    safety_labels = _mapping(response.get("safety_labels"))
    summary: dict[str, Any] = {
        "intent": intent,
        "grantex_call_status": grantex_call_status,
        "grantex_grounded": grantex_call_status == "completed" and intent == "read_only_discovery",
        "read_only": True,
        "preview_only": status in {"preview_only", "blocked", "unavailable", "refused"},
        "non_enabling": True,
    }

    if response.get("refusal_code"):
        summary["refusal_code"] = _safe_refusal_code(response.get("refusal_code"))
    if safety_labels:
        summary["safety_labels"] = safety_labels
    public_discovery_state = _mapping(response.get("public_discovery_state"))
    if public_discovery_state:
        summary["public_discovery_state"] = public_discovery_state
    session_authority = _mapping(response.get("session_authority"))
    if session_authority:
        summary["session_authority"] = session_authority
    for source_key, target_key in (
        ("readiness_summary", "readiness"),
        ("agent_facing_preview_summary", "agent_preview"),
        ("rollout_proposal_summary", "rollout"),
    ):
        value = _mapping(response.get(source_key))
        if value:
            summary[target_key] = value
    checklist = _safe_list_of_mappings(response.get("evidence_checklist"))
    if checklist:
        summary["evidence_checklist"] = checklist
    return summary


def _default_source_reference() -> dict[str, str]:
    return {
        "system": "grantex",
        "endpoint": C6G_PREVIEW_ENDPOINT,
    }


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _safe_channel(channel: str) -> str:
    value = _safe_text(channel, limit=60).lower().replace("-", "_").replace(" ", "_")
    return value or "future_channel"


def _safe_refusal_code(value: Any) -> str | None:
    text = _safe_text(value, limit=120)
    return text or None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): nested for key, nested in value.items() if _is_safe_value(nested)}


def _safe_list_of_mappings(value: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:limit]:
        if isinstance(item, Mapping):
            result.append(_mapping(item))
    return result


def _string_list(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = _safe_text(item, limit=180)
        if text:
            result.append(text)
    return result


def _safe_text(value: Any, *, limit: int = 240) -> str:
    if value in (None, ""):
        return ""
    text = str(value).replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _is_safe_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_safe_text(value))
    if isinstance(value, int | float | bool):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_safe_value(nested) for key, nested in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return all(_is_safe_value(item) for item in value)
    return False
