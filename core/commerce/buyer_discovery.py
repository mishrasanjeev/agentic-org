"""Buyer-facing read-only discovery responses from Grantex C6G handoff data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

C6G_PREVIEW_ENDPOINT = "/v1/commerce/merchants/{merchant_id}/agenticorg-buyer-discovery-preview"

CHECKOUT_PAYMENT_TERMS = (
    "checkout",
    "pay",
    "payment",
    "payment link",
    "buy now",
    "place order",
    "purchase",
)
LIVE_PROVIDER_TERMS = (
    "live payment",
    "live provider",
    "live commerce",
    "plural",
    "pine labs",
    "provider credential",
    "provider secret",
)
FULFILLMENT_TERMS = (
    "fulfillment",
    "fulfilment",
    "shipping",
    "delivery",
    "deliver",
    "dispatch",
    "tracking",
    "order status",
)
REFUND_RETURN_TERMS = ("refund", "return", "replacement", "cancel order", "chargeback")

PRIVATE_OUTPUT_KEYS = {
    "legal_name",
    "legal_entity_name",
    "contract",
    "contracts",
    "private_contact",
    "private_contacts",
    "provider_account",
    "provider_account_refs",
    "provider_" + "credentials",
    "token",
    "jwt",
    "passport",
    "db_url",
    "redis_url",
    "raw_payload",
    "allowlist",
}

NON_ENABLING_CONTROL_FIELDS = (
    "sandbox_only",
    "handoff_request_is_approval",
    "buyer_agent_discovery_is_public",
    "agenticorg_public_discovery_enabled",
    "public_discovery_enabled",
    "checkout_payment_enabled",
    "live_provider_enabled",
    "live_plural_enabled",
    "production_allowlist_written",
    "live_mode_status",
    "production_approval_status",
    "rollout_status",
)


def classify_buyer_discovery_request(request_text: str) -> str:
    """Classify requests that C6H must refuse before any write-like behavior."""
    text = str(request_text or "").strip().lower()
    if _contains_any(text, LIVE_PROVIDER_TERMS):
        return "live_provider"
    if _contains_any(text, CHECKOUT_PAYMENT_TERMS):
        return "checkout_payment"
    if _contains_any(text, FULFILLMENT_TERMS):
        return "fulfillment"
    if _contains_any(text, REFUND_RETURN_TERMS):
        return "refund_return"
    return "read_only_discovery"


def build_buyer_discovery_response(
    grantex_payload: Mapping[str, Any] | None,
    *,
    request_text: str = "",
) -> dict[str, Any]:
    """Build a safe buyer response grounded only in Grantex preview payloads."""
    intent = classify_buyer_discovery_request(request_text)
    safe_preview = sanitize_buyer_discovery_preview(grantex_payload)
    source_reference = safe_preview.get("source_reference", {})

    if intent != "read_only_discovery":
        return _refusal_for_intent(intent, source_reference)

    if safe_preview.get("status") == "unavailable":
        return {
            "status": "unavailable",
            "refusal": True,
            "refusal_code": "grantex_discovery_unavailable",
            "message": "Grantex did not return buyer discovery data, so I cannot describe this merchant.",
            "source_reference": source_reference,
        }

    blockers = _string_list(safe_preview.get("blockers"))
    integration_status = _safe_text(safe_preview.get("integration_status"))
    if integration_status != "sandbox_handoff_requested" or blockers:
        return {
            "status": "blocked",
            "refusal": True,
            "refusal_code": "buyer_discovery_blocked",
            "message": "Grantex has not cleared this sandbox buyer discovery preview for read-only use.",
            "merchant": safe_preview.get("merchant", {}),
            "blockers": blockers,
            "remediation_items": _string_list(safe_preview.get("remediation_items")),
            "blocked_capabilities": _string_list(safe_preview.get("blocked_capabilities")),
            "safety_labels": safe_preview.get("safety_labels", {}),
            "source_reference": source_reference,
        }

    safety_labels = _mapping(safe_preview.get("safety_labels"))
    preview_only = (
        safety_labels.get("public_discovery_enabled") is False
        or safety_labels.get("agenticorg_public_discovery_enabled") is False
        or safety_labels.get("buyer_agent_discovery_is_public") is False
        or safety_labels.get("production_approval_status") != "approved"
        or safety_labels.get("live_mode_status") != "live"
    )
    status = "preview_only" if preview_only else "available"
    message = (
        "This is a Grantex-grounded read-only sandbox preview. It is not public discovery, "
        "production approval, checkout/payment, fulfillment, refunds, or live provider access."
        if preview_only
        else "This merchant discovery response is grounded in Grantex read-only data."
    )

    return {
        "status": status,
        "refusal": False,
        "message": message,
        "merchant": safe_preview.get("merchant", {}),
        "catalog_samples": safe_preview.get("catalog_samples", []),
        "readiness_summary": safe_preview.get("readiness_summary", {}),
        "agent_facing_preview_summary": safe_preview.get("agent_facing_preview_summary", {}),
        "rollout_proposal_summary": safe_preview.get("rollout_proposal_summary", {}),
        "evidence_checklist": safe_preview.get("evidence_checklist", []),
        "allowed_capabilities": safe_preview.get("allowed_capabilities", []),
        "blocked_capabilities": safe_preview.get("blocked_capabilities", []),
        "safety_labels": safety_labels,
        "source_reference": source_reference,
    }


async def run_buyer_discovery_preview(
    connector: Any,
    *,
    merchant_id: str,
    request_text: str = "",
) -> dict[str, Any]:
    """Call the Grantex preview route and return a safe buyer-facing response."""
    raw = await connector.buyer_discovery_preview(merchant_id=merchant_id)
    return build_buyer_discovery_response(raw, request_text=request_text)


def sanitize_buyer_discovery_preview(grantex_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Select public-safe C6G fields and ignore anything private or raw."""
    data = _data_object(grantex_payload)
    if not data:
        return {"status": "unavailable", "source_reference": {"system": "grantex", "endpoint": C6G_PREVIEW_ENDPOINT}}

    merchant = _mapping(data.get("merchant"))
    merchant_reference = _safe_text(data.get("merchant_reference") or merchant.get("merchant_reference"))
    safe_merchant = _drop_empty(
        {
            "merchant_reference": merchant_reference,
            "display_name": _safe_text(data.get("display_name") or merchant.get("display_name")),
            "category": _safe_text(merchant.get("category_preset")),
            "country": _safe_text(merchant.get("country_code")),
            "currency": _safe_text(merchant.get("default_currency")),
            "discovery_description": _safe_text(merchant.get("public_discovery_description_draft"), limit=600),
        }
    )

    safety_labels = {
        key: data.get(key)
        for key in NON_ENABLING_CONTROL_FIELDS
        if key in data and _is_scalar(data.get(key))
    }

    source_reference = _drop_empty(
        {
            "system": "grantex",
            "endpoint": C6G_PREVIEW_ENDPOINT,
            "merchant_reference": merchant_reference,
            "generated_at": _safe_text(data.get("generated_at")),
            "audit_event_id": _safe_text(data.get("audit_event_id")),
        }
    )

    return {
        "status": "available",
        "integration_status": _safe_text(data.get("integration_status")),
        "merchant": safe_merchant,
        "readiness_summary": _safe_mapping(data.get("readiness_summary")),
        "agent_facing_preview_summary": _safe_mapping(data.get("agent_facing_preview_summary")),
        "rollout_proposal_summary": _safe_mapping(data.get("rollout_proposal_summary")),
        "evidence_checklist": _safe_evidence_checklist(data.get("evidence_checklist")),
        "catalog_samples": _safe_catalog_samples(data.get("sample_products")),
        "allowed_capabilities": _string_list(data.get("allowed_buyer_agent_capabilities")),
        "blocked_capabilities": _string_list(data.get("blocked_buyer_agent_capabilities")),
        "blockers": _string_list(data.get("blockers")),
        "remediation_items": _string_list(data.get("remediation_items")),
        "safety_labels": safety_labels,
        "source_reference": source_reference,
    }


def _refusal_for_intent(intent: str, source_reference: Mapping[str, Any]) -> dict[str, Any]:
    messages = {
        "checkout_payment": (
            "Checkout and payment are not enabled in this read-only discovery slice. "
            "I can only show Grantex-grounded preview information."
        ),
        "live_provider": (
            "Live payment/provider access is not enabled in this read-only discovery slice. "
            "I cannot request provider access or live routing."
        ),
        "fulfillment": (
            "Fulfillment, shipment, delivery, and order-status execution are not enabled in this slice."
        ),
        "refund_return": "Returns and refunds execution are not enabled in this slice.",
    }
    return {
        "status": "refused",
        "refusal": True,
        "refusal_code": f"{intent}_not_enabled",
        "message": messages[intent],
        "source_reference": dict(source_reference),
    }


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _data_object(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    if isinstance(data, Mapping):
        return data
    return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, nested in value.items():
        key_text = _safe_text(key, limit=80)
        if not key_text or _looks_private_key(key_text):
            continue
        if _is_scalar(nested):
            result[key_text] = nested if isinstance(nested, bool | int | float) else _safe_text(nested, limit=500)
        elif isinstance(nested, Sequence) and not isinstance(nested, str):
            result[key_text] = _string_list(nested, limit=10)
    return result


def _safe_evidence_checklist(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    entries: list[dict[str, str]] = []
    for item in value[:12]:
        if not isinstance(item, Mapping):
            continue
        entries.append(
            _drop_empty(
                {
                    "key": _safe_text(item.get("key"), limit=80),
                    "label": _safe_text(item.get("label"), limit=160),
                    "status": _safe_text(item.get("status"), limit=40),
                }
            )
        )
    return entries


def _safe_catalog_samples(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    samples: list[dict[str, str]] = []
    for item in value[:3]:
        if not isinstance(item, Mapping):
            continue
        samples.append(
            _drop_empty(
                {
                    "title": _safe_text(item.get("title"), limit=160),
                    "brand": _safe_text(item.get("brand"), limit=120),
                    "category": _safe_text(item.get("category_preset") or item.get("category"), limit=120),
                }
            )
        )
    return samples


def _string_list(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    result: list[str] = []
    for item in value[:limit]:
        if not _is_scalar(item):
            continue
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


def _drop_empty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: nested for key, nested in value.items() if nested not in ("", None, [], {})}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) or value is None


def _looks_private_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(marker in lowered for marker in PRIVATE_OUTPUT_KEYS)
