"""Deterministic guardrails for the Commerce Sales Agent."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from core.commerce.session_authority import session_authority_from_payload

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
    "buyer_discovery_preview",
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
        "checkout_not_enabled",
        "checkout_payment_not_enabled",
        "checkout_payment_not_enabled_by_c6u6",
        "commerce_disabled",
        "agent_status_missing",
        "authority_ambiguous",
        "authority_freshness_missing",
        "authority_stale",
        "buyer_mismatch",
        "consent_denied",
        "consent_expired",
        "consent_missing",
        "consent_not_granted",
        "consent_revoked",
        "emergency_disabled",
        "live_payment_not_enabled",
        "live_provider_blocked",
        "live_provider_not_enabled",
        "merchant_private_api_not_allowed",
        "merchant_agentic_commerce_disabled",
        "merchant_disabled",
        "merchant_emergency_disabled",
        "merchant_mismatch",
        "merchant_status_missing",
        "passport_expired",
        "passport_invalid",
        "passport_missing",
        "passport_not_yet_valid",
        "passport_required",
        "passport_revoked",
        "passport_scope_missing",
        "policy_decision_ambiguous",
        "policy_decision_missing",
        "policy_decision_deny",
        "policy_denied",
        "provider_blocked",
        "provider_call_not_allowed",
        "provider_unavailable",
        "public_discovery_disabled",
        "public_discovery_not_enabled",
        "session_expired",
        "session_mismatch",
        "session_missing",
        "session_revoked",
        "stale_inventory",
    }
)

SENSITIVE_ERROR_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.I | re.S),
    re.compile(r"\b(?:postgresql?|redis)://[^\s'\"<>]+", re.I),
    re.compile(r"\bhttps?://[^\s'\"<>]*(?:private|internal|merchant|provider)[^\s'\"<>]*", re.I),
    re.compile(
        r"\b(?:bearer|token|jwt|passport|secret|api[_-]?key|webhook[_-]?secret|"
        r"client[_-]?secret|password)\s*[:=]\s*[^\s,'\"{}]+",
        re.I,
    ),
    re.compile(
        r"\b(?:raw[_-]?payload|provider[_-]?(?:payload|metadata|credential|credentials))"
        r"\b\s*[:=]\s*\{[^{}]*\}",
        re.I,
    ),
)

UNSUPPORTED_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "final_price": ("final price", "final payable", "payable amount", "checkout total", "all taxes included"),
    "emi": ("emi", "installment", "instalment", "no-cost emi", "no cost emi"),
    "discount": ("discount", "coupon", "promo", "cashback", "deal", "sale price"),
    "offer": ("offer", "promotion", "bundle"),
    "inventory": ("guaranteed in stock", "in stock", "available now", "reserved stock"),
    "delivery": ("delivery", "deliver", "shipping", "ship by", "delivery tomorrow"),
    "fulfillment": ("fulfillment", "dispatch", "tracking", "order status"),
    "refund": ("refund", "chargeback"),
    "settlement": ("settlement", "payout", "reconciliation"),
    "support": ("support promise", "service center", "customer support"),
    "return_policy": ("return policy", "returns", "refund", "replacement"),
    "tax": ("tax", "gst", "vat"),
    "warranty": ("warranty", "guarantee"),
}

TOOL_DATA_CLAIM_KEYS: dict[str, tuple[str, ...]] = {
    "final_price": ("final_price", "final_amount", "payable_amount", "checkout_total"),
    "emi": ("emi", "installment", "instalment", "financing"),
    "discount": ("discount", "coupon", "promo", "cashback"),
    "offer": ("offer", "promotion", "bundle"),
    "inventory": ("inventory_confirmed", "reserved_stock"),
    "delivery": ("delivery", "serviceability", "shipping"),
    "fulfillment": ("fulfillment", "dispatch", "tracking", "order_status"),
    "refund": ("refund", "chargeback"),
    "settlement": ("settlement", "payout", "reconciliation"),
    "support": ("support", "service_center"),
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

    authority_payload = _lookup(params, "session_authority", "authority", "commerce_authority")
    if authority_payload is not None:
        if not isinstance(authority_payload, Mapping):
            return refusal(
                "authority_ambiguous",
                "Grantex authority state is ambiguous, so this checkout/payment action is refused.",
            )
        authority = session_authority_from_payload(
            authority_payload,
            expected_merchant_id=_as_optional_text(_lookup(params, "merchant_id")),
            expected_agent_id=_as_optional_text(_lookup(params, "agent_id")),
            expected_buyer_id=_as_optional_text(_lookup(params, "buyer_id", "subject")),
            expected_session_id=_as_optional_text(_lookup(params, "buyer_session_id", "session_id")),
        )
        if not authority.get("authority_valid"):
            return refusal(
                _as_text(authority.get("refusal_code")) or "authority_ambiguous",
                _as_message(authority.get("reason")),
                {"authority": authority},
            )
        if authority.get("protected_action_allowed") is not True:
            return refusal(
                _as_text(authority.get("refusal_code")) or "checkout_payment_not_enabled_by_c6u6",
                _as_message(authority.get("reason")),
                {"authority": authority},
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

    if action in {"checkout_create", "payment_create_intent", "payment_get_status"}:
        passport_jwt = _lookup(params, "passport_jwt", "passport.jwt", "commerce_passport.jwt")
        if not passport_jwt:
            message = (
                "Payment status requires a Grantex Commerce Passport."
                if action == "payment_get_status"
                else "Checkout/payment requires a granted Grantex Commerce Passport."
            )
            return refusal("consent_required", message)

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

    authority_context_present = any(
        _lookup(params, path) not in (None, "")
        for path in ("agent_id", "agent.id", "buyer_id", "subject", "buyer_session_id", "session_id")
    )
    if (
        action in {"checkout_create", "payment_create_intent"}
        and authority_payload is None
        and authority_context_present
    ):
        authority = session_authority_from_payload(
            None,
            expected_merchant_id=_as_optional_text(_lookup(params, "merchant_id")),
            expected_agent_id=_as_optional_text(_lookup(params, "agent_id")),
            expected_buyer_id=_as_optional_text(_lookup(params, "buyer_id", "subject")),
            expected_session_id=_as_optional_text(_lookup(params, "buyer_session_id", "session_id")),
        )
        return refusal(
            _as_text(authority.get("refusal_code")) or "authority_freshness_missing",
            _as_message(authority.get("reason")),
            {"authority": authority},
        )

    return allowed()


def _sanitize_grantex_error_message(value: Any) -> str:
    message = str(value or "").replace("\x00", "").strip()
    if not message:
        return "Grantex Commerce request failed."
    for pattern in SENSITIVE_ERROR_PATTERNS:
        message = pattern.sub("[redacted]", message)
    if "[redacted]" in message:
        return (
            "Grantex Commerce refused the request. Private provider, merchant, "
            "credential, or raw payload details were redacted."
        )
    return message[:500].rstrip()


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _as_message(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:500].rstrip() if text else "Grantex authority state requires refresh before continuing."


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
    message = _sanitize_grantex_error_message(raw_message)

    retryable = bool(error_obj.get("retryable")) if isinstance(error_obj, Mapping) else False
    normalized: dict[str, Any] = {
        "error": code,
        "message": message,
        "status_code": status_code,
        "retryable": retryable,
        "refusal": code in PAYMENT_REFUSAL_ERROR_CODES,
    }

    for field in ("decision_id", "audit_event_id"):
        value = error_obj.get(field) if isinstance(error_obj, Mapping) else None
        if value:
            normalized[field] = value

    remediation = error_obj.get("remediation") if isinstance(error_obj, Mapping) else None
    if isinstance(remediation, str):
        normalized["remediation"] = _sanitize_grantex_error_message(remediation)

    return normalized
