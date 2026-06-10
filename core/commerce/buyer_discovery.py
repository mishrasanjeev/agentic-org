"""Buyer-facing read-only discovery responses from Grantex C6G handoff data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.commerce.public_discovery_state import public_discovery_decision_from_payload
from core.commerce.sales_guardrails import inventory_caution
from core.commerce.session_authority import session_authority_from_payload

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
    "direct provider",
    "payment provider",
    "provider api",
    "provider call",
    "provider credential",
    "provider secret",
)
MERCHANT_PRIVATE_API_TERMS = (
    "merchant private api",
    "merchant private endpoint",
    "merchant private system",
    "merchant existing system",
    "merchant backend",
    "merchant internal api",
    "private merchant api",
    "private merchant endpoint",
)
FULFILLMENT_TERMS = (
    "fulfillment",
    "fulfilment",
    "ship",
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

PRIVATE_SOURCE_MARKERS = (
    "private",
    "internal",
    "provider",
    "credential",
    "secret",
    "token",
    "jwt",
    "passport",
    "raw",
    "payload",
    "postgres://",
    "postgresql://",
    "redis://",
    "http://",
    "https://",
)

SOURCE_STATUS_VALUES = {
    "current",
    "fresh",
    "preview",
    "stale",
    "unknown",
    "not_checked",
    "unverified",
    "blocked",
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
    public_discovery_state = safe_preview.get("public_discovery_state", {})
    session_authority = safe_preview.get("session_authority", {})

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

    if safe_preview.get("session_authority_input_present") and session_authority.get("authority_valid") is False:
        return {
            "status": "blocked",
            "refusal": True,
            "refusal_code": _safe_text(session_authority.get("refusal_code")) or "authority_ambiguous",
            "message": _safe_text(
                session_authority.get("reason")
                or "Grantex authority must be refreshed before this buyer session can continue.",
                limit=800,
            ),
            "merchant": safe_preview.get("merchant", {}),
            "blocked_capabilities": _string_list(safe_preview.get("blocked_capabilities")),
            "public_discovery_state": public_discovery_state,
            "session_authority": session_authority,
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
            "public_discovery_state": public_discovery_state,
            "session_authority": session_authority,
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
        "public_discovery_state": public_discovery_state,
        "session_authority": session_authority,
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
    if isinstance(grantex_payload, Mapping) and grantex_payload.get("error") and not grantex_payload.get("data"):
        return {"status": "unavailable", "source_reference": {"system": "grantex", "endpoint": C6G_PREVIEW_ENDPOINT}}

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
    public_discovery_state = public_discovery_decision_from_payload(data)
    session_authority_input_present = _has_session_authority_payload(data)
    session_authority = session_authority_from_payload(data)

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
        "public_discovery_state": public_discovery_state,
        "session_authority_input_present": session_authority_input_present,
        "session_authority": session_authority,
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
        "merchant_private_api": (
            "Merchant private APIs and merchant existing systems are not available to AgenticOrg. "
            "Commerce requests must go through Grantex-owned contracts."
        ),
        "fulfillment": (
            "Fulfillment, shipment, delivery, and order-status execution are not enabled in this slice."
        ),
        "refund_return": "Returns and refunds execution are not enabled in this slice.",
    }
    return {
        "status": "refused",
        "refusal": True,
        "refusal_code": "merchant_private_api_not_allowed"
        if intent == "merchant_private_api"
        else f"{intent}_not_enabled",
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


def _has_session_authority_payload(data: Mapping[str, Any]) -> bool:
    authority_keys = (
        "session_authority",
        "authority",
        "commerce_authority",
        "buyer_session_authority",
        "consent_status",
        "passport_status",
        "session_status",
        "policy_decision",
    )
    return any(key in data for key in authority_keys)


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


def _safe_catalog_samples(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    samples: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, Mapping):
            continue
        sample = _drop_empty(
            {
                "title": _safe_public_text(item.get("title"), limit=160),
                "brand": _safe_public_text(item.get("brand"), limit=120),
                "category": _safe_public_text(item.get("category_preset") or item.get("category"), limit=120),
            }
        )
        commercial_facts = _buyer_safe_commercial_facts(item)
        if commercial_facts:
            sample["commercial_facts"] = commercial_facts
        source_summary = _buyer_safe_source_summary(item)
        if source_summary:
            sample["source_summary"] = source_summary
        samples.append(sample)
    return samples


def _buyer_safe_commercial_facts(item: Mapping[str, Any]) -> dict[str, Any]:
    variant = _first_mapping(item.get("variants"))
    fact_source = variant or item
    price_amount = _first_value(fact_source, item, keys=("price_amount", "price", "amount", "amount_minor_units"))
    currency = _safe_currency(_first_value(fact_source, item, keys=("currency", "price_currency")))
    availability = _safe_status(
        _first_value(
            fact_source,
            item,
            keys=("availability_status", "inventory_status", "stock_status", "freshness"),
        )
    )
    warranty = _safe_public_text(_first_value(fact_source, item, keys=("warranty_summary", "warranty")), limit=240)
    return_policy = _safe_public_text(
        _first_value(fact_source, item, keys=("return_policy_summary", "return_policy")),
        limit=240,
    )
    tax_value = _first_value(
        fact_source,
        item,
        keys=("tax_inclusive", "tax_rate", "tax_amount_minor_units", "gst_slab", "hsn_code", "tax"),
    )

    inventory = inventory_caution({"items": [fact_source]})
    facts: dict[str, Any] = {
        "price": _drop_empty(
            {
                "status": "preview_only" if price_amount not in (None, "") and currency else "unknown",
                "display": f"{currency} {_safe_text(price_amount, limit=60)}"
                if price_amount not in (None, "") and currency
                else "",
                "final_price_confirmed": False,
                "message": (
                    "Preview price only. Final tax, fees, discounts, delivery charges, "
                    "and checkout totals are not confirmed."
                    if price_amount not in (None, "") and currency
                    else "Price is not confirmed by Grantex for this preview."
                ),
            }
        ),
        "tax": _drop_empty(
            {
                "status": "provided_by_grantex" if tax_value not in (None, "") else "unknown",
                "final_tax_confirmed": False,
                "message": "Final tax/GST is not confirmed for checkout."
                if tax_value in (None, "")
                else "Tax metadata is present, but final checkout tax is not confirmed here.",
            }
        ),
        "warranty": _drop_empty(
            {
                "status": "provided_by_grantex" if warranty else "unknown",
                "summary": warranty,
                "message": "Warranty terms are not confirmed by Grantex for this preview." if not warranty else "",
            }
        ),
        "return_policy": _drop_empty(
            {
                "status": "provided_by_grantex" if return_policy else "unknown",
                "summary": return_policy,
                "message": "Return or refund handling is not confirmed by Grantex for this preview."
                if not return_policy
                else "",
            }
        ),
        "inventory": _drop_empty(
            {
                "status": "unknown_or_stale"
                if inventory.get("caution_required")
                else availability or "preview_bucket",
                "preview_availability": availability,
                "caution_required": bool(inventory.get("caution_required")),
                "stock_promise": False,
                "message": inventory.get("message")
                or "Availability is a Grantex preview bucket, not a reserved quantity.",
            }
        ),
        "unsupported": [
            "discounts",
            "coupons",
            "emi",
            "delivery",
            "support",
            "fulfillment",
            "refunds",
            "settlement",
            "payout",
        ],
    }
    return facts


def _buyer_safe_source_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    variant = _first_mapping(item.get("variants"))
    fact_source = variant or item
    source_label = _safe_source_label(
        _first_value(
            fact_source,
            item,
            keys=("source_label", "source_system", "connector_source", "source", "source_type"),
        )
    )
    freshness = _safe_status(
        _first_value(
            fact_source,
            item,
            keys=("freshness", "freshness_status", "sync_status", "health_state"),
        )
    )
    stale = _truthy(_first_value(fact_source, item, keys=("stale", "is_stale")))
    if stale:
        freshness = "stale"
    last_checked_at = _safe_timestamp(
        _first_value(
            fact_source,
            item,
            keys=("last_synced_at", "last_successful_sync_at", "source_snapshot_at", "generated_at"),
        )
    )
    return _drop_empty(
        {
            "source": source_label or "grantex_preview",
            "freshness_status": freshness or ("unknown" if not last_checked_at else "preview"),
            "last_checked_at": last_checked_at,
        }
    )


def _first_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        for item in value:
            if isinstance(item, Mapping):
                return item
    if isinstance(value, Mapping):
        return value
    return {}


def _first_value(*mappings: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return None


def _safe_public_text(value: Any, *, limit: int = 240) -> str:
    text = _safe_text(value, limit=limit)
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in PRIVATE_SOURCE_MARKERS):
        return ""
    return text


def _safe_source_label(value: Any) -> str:
    text = _safe_text(value, limit=80)
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in PRIVATE_SOURCE_MARKERS):
        return "grantex_controlled_source"
    return text


def _safe_currency(value: Any) -> str:
    text = _safe_text(value, limit=3).upper()
    return text if len(text) == 3 and text.isalpha() else ""


def _safe_status(value: Any) -> str:
    text = _safe_text(value, limit=40).lower().replace(" ", "_").replace("-", "_")
    safe_availability = {"in_stock", "out_of_stock", "pre_order", "back_order"}
    return text if text in SOURCE_STATUS_VALUES or text in safe_availability else ""


def _safe_timestamp(value: Any) -> str:
    text = _safe_text(value, limit=40)
    if len(text) >= 10 and "://" not in text and not any(marker in text.lower() for marker in PRIVATE_SOURCE_MARKERS):
        return text
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "stale", "expired", "blocked"}


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
