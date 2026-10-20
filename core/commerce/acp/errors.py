# SPDX-License-Identifier: Apache-2.0
"""ACP error objects and the mapping from internal codes (G40).

ACP 2026-04-17 has two error shapes (``schema.agentic_checkout.json``):

* ``Error`` ``{type, code, message, param?, supported_versions?}`` for a
  protocol error, when no valid ``CheckoutSession`` can be returned (HTTP
  4xx/5xx). ``type`` is ``invalid_request``, ``processing_error`` or
  ``service_unavailable``; ``code`` is an open string, with the well-known
  values ``idempotency_key_required``, ``idempotency_conflict``,
  ``idempotency_in_flight``, ``missing_api_version``,
  ``unsupported_api_version``, ``requires_3ds`` and ``invalid_signature``.
* ``MessageError`` / ``MessageWarning`` / ``MessageInfo`` in a session's
  ``messages[]`` for business outcomes (HTTP 200/201). ``MessageError.code``
  and ``MessageWarning.code`` are closed enums; ``param`` is an RFC 9535
  JSONPath into the ACP shape.

The UCP state engine and the ACP binding speak internal codes (UCP message
codes, arbiter and decision refusals, the binding's allowance checks). This
module is the only place they become ACP: every internal error code has a row
in ``MESSAGE_ERROR_MAP`` (an unknown one becomes ``invalid``), a warning
whose code ACP does not define becomes an info message, and UCP protocol
errors map through ``PROTOCOL_ERROR_MAP``. Internal blocker dicts and UCP
envelopes never reach an ACP response.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ERROR_TYPES: tuple[str, ...] = ("invalid_request", "processing_error", "service_unavailable")
MESSAGE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "missing",
        "invalid",
        "out_of_stock",
        "payment_declined",
        "requires_sign_in",
        "requires_3ds",
        "low_stock",
        "quantity_exceeded",
        "coupon_invalid",
        "coupon_expired",
        "minimum_not_met",
        "maximum_exceeded",
        "region_restricted",
        "age_verification_required",
        "approval_required",
        "unsupported",
        "not_found",
        "conflict",
        "rate_limited",
        "expired",
        "intervention_required",
    }
)
MESSAGE_WARNING_CODES: frozenset[str] = frozenset(
    {
        "low_stock",
        "high_demand",
        "shipping_delay",
        "price_change",
        "expiring_promotion",
        "limited_availability",
        "discount_code_expired",
        "discount_code_invalid",
        "discount_code_already_applied",
        "discount_code_combination_disallowed",
        "discount_code_minimum_not_met",
        "discount_code_user_not_logged_in",
        "discount_code_user_ineligible",
        "discount_code_usage_limit_reached",
    }
)

# Internal error message code -> ACP MessageError.code. Sources: the UCP state engine
# (checkout, quantity, fulfillment, payment), completion (AP2, executor, arbiter), decision
# consumption (core.commerce.arbiter_store) and the ACP binding (allowance, currency).
# enterprise-gate: process-local-ok reason=constant-lookup-table-never-mutated
MESSAGE_ERROR_MAP: dict[str, str] = {
    # line items and quantities
    "missing": "missing",
    "not_found": "not_found",
    "out_of_stock": "out_of_stock",
    "item_unavailable": "invalid",
    "invalid_quantity": "invalid",
    "invalid_quantity_unit": "invalid",
    "quantity_unit_mismatch": "invalid",
    # fulfillment
    "unsupported_fulfillment_method": "unsupported",
    "invalid_destination": "invalid",
    "address_undeliverable": "region_restricted",
    "invalid_option": "invalid",
    "multi_destination_unsupported": "unsupported",
    "method_combination_unsupported": "unsupported",
    "invalid_fulfillment": "invalid",
    # payment and completion
    "payment_handler_unavailable": "unsupported",
    "invalid_payment_term": "invalid",
    "payment_failed": "payment_declined",
    "requires_3ds": "requires_3ds",
    "mandate_required": "missing",
    "executor_unavailable": "unsupported",
    "arbiter_refused": "payment_declined",
    # decision consumption
    "decision_id_missing": "missing",
    "decision_not_found": "not_found",
    "decision_not_allow": "payment_declined",
    "decision_action_mismatch": "invalid",
    "decision_payload_mismatch": "conflict",
    "decision_consumed": "conflict",
    "decision_expired": "expired",
    "grantex_grant_missing": "payment_declined",
    # ACP binding
    "currency_mismatch": "invalid",
    "delegate_payment_passport_missing": "missing",
    "delegate_payment_allowance_unavailable": "missing",
    "delegate_payment_allowance_refused": "payment_declined",
    "delegate_payment_allowance_exceeded": "maximum_exceeded",
    "delegate_payment_allowance_expired": "expired",
    "delegate_payment_allowance_currency": "payment_declined",
}
DEFAULT_MESSAGE_ERROR_CODE = "invalid"

# Internal JSONPath prefixes (the UCP request shape) -> the ACP field they concern.
_SELECTED_OPTION = re.compile(r"^\$\.fulfillment\.methods\[\d+\]\.groups\[\d+\]\.selected_option_id$")
_PARAM_PREFIXES: tuple[tuple[str, str], ...] = (
    ("$.fulfillment", "$.fulfillment_details"),
    ("$.payment", "$.payment_data"),
    ("$.buyer", "$.buyer"),
    ("$.status", "$.status"),
    ("$.currency", "$.currency"),
    ("$.totals", "$.totals"),
    ("$.agenticorg_commerce", "$.agenticorg_commerce"),
    ("$.capabilities", "$.capabilities"),
)
_LINE_ITEM = re.compile(r"^\$\.line_items\[(\d+)\]")
_RESOLUTIONS = {"recoverable", "requires_buyer_input", "requires_buyer_review"}


def acp_error(
    error_type: str,
    code: str,
    message: str,
    *,
    param: str | None = None,
    supported_versions: list[str] | None = None,
) -> dict[str, Any]:
    """An ACP ``Error`` object."""
    if error_type not in ERROR_TYPES:
        raise ValueError(f"unknown ACP error type {error_type}")
    out: dict[str, Any] = {"type": error_type, "code": code, "message": message}
    if param:
        out["param"] = param
    if supported_versions:
        out["supported_versions"] = list(supported_versions)
    return out


def map_param(path: Any) -> str:
    """An internal JSONPath rewritten to the ACP request/response field it concerns (``$`` when none)."""
    if not isinstance(path, str) or not path.startswith("$"):
        return "$"
    if _SELECTED_OPTION.match(path):
        return "$.selected_fulfillment_options"
    match = _LINE_ITEM.match(path)
    if match:
        return f"$.line_items[{match.group(1)}]"
    for prefix, target in _PARAM_PREFIXES:
        if path == prefix or path.startswith((prefix + ".", prefix + "[")):
            return target
    return "$"


def message_error_code(code: Any) -> str:
    return MESSAGE_ERROR_MAP.get(str(code or ""), DEFAULT_MESSAGE_ERROR_CODE)


def map_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """One internal message (``type``, ``code``, ``content``, ``severity``, ``path``) as an ACP message."""
    kind = message.get("type")
    content = str(message.get("content") or "")
    out: dict[str, Any] = {"type": "info", "content_type": "plain", "content": content}
    if kind == "error":
        out = {
            "type": "error",
            "code": message_error_code(message.get("code")),
            "content_type": "plain",
            "content": content,
        }
    elif kind == "warning" and message.get("code") in MESSAGE_WARNING_CODES:
        out = {"type": "warning", "code": message["code"], "content_type": "plain", "content": content}
    severity = message.get("severity")
    if severity in _RESOLUTIONS:
        out["resolution"] = severity
    elif severity == "unrecoverable":
        out["severity"] = "critical"
    if message.get("path"):
        out["param"] = map_param(message.get("path"))
    return out


# (UCP REST status, UCP error code) -> (ACP status, Error.type, Error.code). Writes to a session that
# can no longer change (completed, canceled, expired or executing) are 405, as ACP specifies for cancel.
# enterprise-gate: process-local-ok reason=constant-lookup-table-never-mutated
PROTOCOL_ERROR_MAP: dict[tuple[int, str], tuple[int, str, str]] = {
    (400, "invalid_request"): (400, "invalid_request", "invalid"),
    (404, "not_found"): (404, "invalid_request", "not_found"),
    (409, "invalid_state"): (405, "invalid_request", "session_not_modifiable"),
    (503, "idempotency_unavailable"): (503, "service_unavailable", "idempotency_unavailable"),
}


def protocol_error(status: int, body: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    """A UCP protocol error response (``error_response``) as an ACP status and ``Error``."""
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    first = messages[0] if messages and isinstance(messages[0], Mapping) else {}
    code = str(first.get("code") or "")
    content = str(first.get("content") or "The request could not be processed.")
    mapped = PROTOCOL_ERROR_MAP.get((status, code))
    if mapped is None:
        if status >= 500:
            return 500, acp_error("processing_error", "processing_error", "The request could not be processed.")
        return status, acp_error("invalid_request", "invalid", content)
    acp_status, error_type, acp_code = mapped
    return acp_status, acp_error(error_type, acp_code, content)


# Statuses answered before an ACP route runs (authentication, scope and rate-limit checks, routing,
# the feature flag) -> (Error.type, Error.code). The bodies of those answers are generic.
# enterprise-gate: process-local-ok reason=constant-lookup-table-never-mutated
EARLY_STATUS_ERRORS: dict[int, tuple[str, str]] = {
    400: ("invalid_request", "invalid"),
    401: ("invalid_request", "unauthorized"),
    403: ("invalid_request", "forbidden"),
    404: ("invalid_request", "not_found"),
    405: ("invalid_request", "method_not_allowed"),
    413: ("invalid_request", "request_too_large"),
    415: ("invalid_request", "unsupported_media_type"),
    422: ("invalid_request", "invalid"),
    429: ("invalid_request", "rate_limit_exceeded"),
    503: ("service_unavailable", "service_unavailable"),
}


def is_acp_error(body: Any) -> bool:
    return (
        isinstance(body, Mapping)
        and body.get("type") in ERROR_TYPES
        and isinstance(body.get("code"), str)
        and isinstance(body.get("message"), str)
    )


def shape_early_error(status: int, body: Any) -> dict[str, Any]:
    """A non-ACP error body (``{"detail": ...}``) answered on an ACP route, as an ACP ``Error``."""
    error_type, code = EARLY_STATUS_ERRORS.get(
        status, ("processing_error", "processing_error") if status >= 500 else ("invalid_request", "invalid")
    )
    detail = body.get("detail") if isinstance(body, Mapping) else None
    message = detail if isinstance(detail, str) and detail else "The request could not be processed."
    if status >= 500 and status != 503:
        message = "The request could not be processed."
    return acp_error(error_type, code, message[:300])
