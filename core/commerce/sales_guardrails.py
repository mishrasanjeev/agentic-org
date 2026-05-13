"""Deterministic guardrails for the Commerce Sales Agent."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

COMMERCE_AGENT_TYPE = "commerce_sales_agent"
COMMERCE_DISPLAY_NAME = "commerce-sales-agent"

GRANTEX_COMMERCE_TOOL_ALIASES = [
    "merchant_get_profile",
    "catalog_search",
    "catalog_get_item",
    "inventory_check",
    "cart_create",
    "consent_request",
    "consent_exchange",
    "payment_create_intent",
    "checkout_create",
    "payment_get_status",
]

GRANTEX_COMMERCE_DEFAULT_TOOLS = [
    f"grantex_commerce:{alias}" for alias in GRANTEX_COMMERCE_TOOL_ALIASES
]

CONSENT_DENIED_STATUSES = frozenset({"denied", "rejected", "revoked", "withdrawn", "failed"})
DISABLED_STATUSES = frozenset({"disabled", "inactive", "blocked", "suspended", "untrusted", "revoked"})
POLICY_DENIALS = frozenset({"deny", "denied", "blocked", "refused", "rejected"})

PAYMENT_REFUSAL_ERROR_CODES = frozenset(
    {
        "agent_disabled",
        "agent_not_trusted",
        "amount_cap_exceeded",
        "checkout_amount_exceeds_passport",
        "checkout_passport_required",
        "commerce_disabled",
        "consent_denied",
        "consent_expired",
        "consent_not_granted",
        "emergency_disabled",
        "live_provider_blocked",
        "merchant_agentic_commerce_disabled",
        "merchant_disabled",
        "merchant_emergency_disabled",
        "passport_expired",
        "passport_not_yet_valid",
        "passport_required",
        "passport_revoked",
        "passport_scope_missing",
        "policy_decision_deny",
        "policy_denied",
        "provider_blocked",
        "provider_unavailable",
    }
)

UNSUPPORTED_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "emi": ("emi", "installment", "instalment", "no-cost emi", "no cost emi"),
    "discount": ("discount", "coupon", "promo", "cashback", "deal", "sale price"),
    "offer": ("offer", "promotion", "bundle"),
    "return_policy": ("return policy", "returns", "refund", "replacement"),
    "tax": ("tax", "gst", "vat"),
    "warranty": ("warranty", "guarantee"),
}

TOOL_DATA_CLAIM_KEYS: dict[str, tuple[str, ...]] = {
    "emi": ("emi", "installment", "instalment", "financing"),
    "discount": ("discount", "coupon", "promo", "cashback"),
    "offer": ("offer", "promotion", "bundle"),
    "return_policy": ("return", "refund", "replacement"),
    "tax": ("tax", "gst", "vat"),
    "warranty": ("warranty", "guarantee"),
}


def refusal(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the common deterministic refusal shape."""
    return {
        "allowed": False,
        "status": "refused",
        "error": code,
        "message": message,
        "details": details or {},
    }


def allowed(details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the common deterministic allow shape."""
    result: dict[str, Any] = {"allowed": True, "status": "allowed"}
    if details:
        result["details"] = details
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "enabled", "disabled", "deny", "denied"}


def _as_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lookup(mapping: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, ""):
            return current
    return None


def validate_payment_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate checkout/payment guardrails before calling Grantex.

    This does not replace Grantex policy enforcement. It fails closed on
    obvious local inputs so the agent cannot initiate payment-affecting work
    without consent evidence or with a blocked provider choice.
    """
    provider_key = _as_text(_lookup(params, "provider_key", "provider.key"))
    if provider_key and provider_key != "mock":
        return refusal(
            "live_provider_blocked",
            "Only Grantex Commerce internal sandbox provider routing is allowed for this agent.",
        )

    explicit_error = _as_text(_lookup(params, "error.code", "error_code", "code"))
    if explicit_error in PAYMENT_REFUSAL_ERROR_CODES:
        return refusal(
            explicit_error,
            "Grantex Commerce refused this checkout/payment action. Do not continue without remediation.",
        )

    consent_status = _as_text(_lookup(params, "consent_status", "consent.status", "consent_request.status"))
    if consent_status in CONSENT_DENIED_STATUSES:
        return refusal("consent_denied", "Checkout/payment requires granted consent; the current consent was denied.")

    passport_status = _as_text(_lookup(params, "passport_status", "passport.status", "commerce_passport.status"))
    if passport_status in {"revoked", "expired", "not_yet_valid"}:
        return refusal(
            f"passport_{passport_status}",
            "The Commerce Passport is not valid for checkout/payment. Request fresh consent through Grantex.",
        )

    if _truthy(_lookup(params, "emergency_disabled", "merchant.emergency_disabled")):
        return refusal("merchant_emergency_disabled", "Commerce checkout is disabled by merchant emergency controls.")

    merchant_status = _as_text(_lookup(params, "merchant_status", "merchant.status", "merchant.commerce_status"))
    if merchant_status in DISABLED_STATUSES:
        return refusal("merchant_disabled", "The merchant is not enabled for commerce checkout.")

    agent_status = _as_text(_lookup(params, "agent_status", "agent.status", "agent_trust_status", "agent.trust_status"))
    if agent_status in DISABLED_STATUSES:
        return refusal("agent_not_trusted", "The agent is disabled or not trusted for commerce checkout.")

    policy_decision = _as_text(_lookup(params, "policy_decision", "policy.decision", "decision"))
    if policy_decision in POLICY_DENIALS:
        return refusal("policy_denied", "Grantex policy denied this checkout/payment action.")

    if action in {"checkout_create", "payment_create_intent"}:
        passport_jwt = _lookup(params, "passport_jwt", "passport.jwt", "commerce_passport.jwt")
        if not passport_jwt:
            return refusal("consent_required", "Checkout/payment requires a granted Grantex Commerce Passport.")

    amount_minor_units = _as_int(_lookup(params, "amount_minor_units", "amount", "total_amount_minor_units"))
    passport_cap = _as_int(
        _lookup(
            params,
            "passport_max_amount_minor_units",
            "passport.max_amount_minor_units",
            "passport.max_amount",
            "commerce_passport.max_amount_minor_units",
            "amount_cap_minor_units",
        )
    )
    if amount_minor_units is not None and passport_cap is not None and amount_minor_units > passport_cap:
        return refusal(
            "amount_cap_exceeded",
            "Requested amount exceeds the Commerce Passport amount cap.",
            {
                "amount_minor_units": amount_minor_units,
                "passport_max_amount_minor_units": passport_cap,
            },
        )

    requested_currency = _as_text(_lookup(params, "currency"))
    passport_currency = _as_text(
        _lookup(params, "passport_currency", "passport.currency", "commerce_passport.currency")
    )
    if requested_currency and passport_currency and requested_currency != passport_currency:
        return refusal(
            "currency_mismatch",
            "Requested currency does not match the Commerce Passport currency.",
            {"currency": requested_currency, "passport_currency": passport_currency},
        )

    return allowed()


def _inventory_records(inventory: Any) -> list[Mapping[str, Any]]:
    if isinstance(inventory, Mapping):
        for key in ("items", "variants", "inventory", "data", "results"):
            value = inventory.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
            if isinstance(value, Mapping):
                return _inventory_records(value)
        return [inventory]
    if isinstance(inventory, list):
        return [item for item in inventory if isinstance(item, Mapping)]
    return []


def inventory_caution(inventory: Any) -> dict[str, Any]:
    """Return whether inventory must be described cautiously."""
    records = _inventory_records(inventory)
    if not records:
        return {
            "caution_required": True,
            "message": "Inventory status is unknown. Do not guarantee availability until Grantex confirms it.",
        }

    for record in records:
        status = _as_text(
            _lookup(record, "availability_status", "status", "inventory_status", "freshness", "stock_status")
        )
        if status in {"", "unknown", "stale", "pending", "not_checked", "unverified"}:
            return {
                "caution_required": True,
                "message": "Inventory is stale or unknown. Do not guarantee availability until Grantex confirms it.",
            }
        if _truthy(_lookup(record, "stale", "is_stale")):
            return {
                "caution_required": True,
                "message": "Inventory is stale or unknown. Do not guarantee availability until Grantex confirms it.",
            }

    return {"caution_required": False, "message": ""}


def _contains_assertive_term(text: str, synonyms: tuple[str, ...]) -> bool:
    for term in synonyms:
        if term not in text:
            continue
        neutral_markers = (
            f"no {term}",
            f"not {term}",
            f"without {term}",
            f"unknown {term}",
            f"cannot confirm {term}",
            f"do not offer {term}",
            f"does not offer {term}",
        )
        if any(marker in text for marker in neutral_markers):
            continue
        return True
    return False


def validate_claims_against_tool_data(response_text: str, tool_data: Any) -> dict[str, Any]:
    """Refuse unsupported commercial claims unless the claim appears in Grantex data."""
    text = _as_text(response_text)
    if not text:
        return allowed()

    try:
        data_blob = json.dumps(tool_data or {}, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        data_blob = ""

    unsupported = []
    for claim, synonyms in UNSUPPORTED_CLAIM_TERMS.items():
        if not _contains_assertive_term(text, synonyms):
            continue
        data_keys = TOOL_DATA_CLAIM_KEYS[claim]
        if not any(key in data_blob for key in data_keys):
            unsupported.append(claim)

    if unsupported:
        return refusal(
            "unsupported_commerce_claim",
            "The response asserts commerce terms that are not supported by Grantex tool data.",
            {"unsupported_claims": unsupported},
        )
    return allowed()


def normalize_grantex_error(payload: Any, status_code: int | None = None) -> dict[str, Any]:
    """Normalize Grantex REST or JSON-RPC errors without copying raw secrets."""
    error_obj: Mapping[str, Any] = {}
    if isinstance(payload, Mapping):
        candidate = payload.get("error")
        if isinstance(candidate, Mapping):
            data = candidate.get("data")
            if isinstance(data, Mapping) and isinstance(data.get("error"), Mapping):
                error_obj = data["error"]
            else:
                error_obj = candidate
        elif isinstance(payload.get("code"), str):
            error_obj = payload

    raw_code = error_obj.get("code") if isinstance(error_obj, Mapping) else None
    code = str(raw_code).strip() if raw_code else "grantex_commerce_error"
    if code.lstrip("-").isdigit():
        code = "grantex_jsonrpc_error"

    raw_message = error_obj.get("message") if isinstance(error_obj, Mapping) else None
    message = str(raw_message).strip() if raw_message else "Grantex Commerce request failed."

    retryable = bool(error_obj.get("retryable")) if isinstance(error_obj, Mapping) else False
    normalized: dict[str, Any] = {
        "error": code,
        "message": message,
        "status_code": status_code,
        "retryable": retryable,
        "refusal": code in PAYMENT_REFUSAL_ERROR_CODES,
    }

    for field in ("decision_id", "audit_event_id", "remediation"):
        value = error_obj.get(field) if isinstance(error_obj, Mapping) else None
        if value:
            normalized[field] = value

    return normalized
