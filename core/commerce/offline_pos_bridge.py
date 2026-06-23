"""Offline POS bridge helpers for OACP-backed commerce handoffs.

The bridge builds internal handoff packets and reconciles POS/provider
confirmations. It never creates an order, captures a payment, stores raw POS
payloads, or turns a simulator result into a production paid state.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

POS_CONFIRMATION_STATUSES: tuple[str, ...] = (
    "accepted",
    "price_changed",
    "out_of_stock",
    "expired",
    "needs_staff_review",
    "unsupported",
    "payment_pending",
    "payment_confirmed",
    "payment_failed",
    "receipt_available",
)

_PAYMENT_SUCCESS_STATUSES = {"payment_confirmed", "receipt_available"}
_PRIVATE_POS_MARKERS = (
    "access_token",
    "authorization",
    "bearer ",
    "card_number",
    "client_secret",
    "cvv",
    "password",
    "private_key",
    "raw_payload",
    "raw_payment",
    "secret",
    "token",
)
_EXECUTION_MARKERS = (
    "agent_order_created",
    "agent_payment_captured",
    "checkout_success",
    "payment_success_without_callback",
)
_DEFAULT_CALLBACK_REPLAY_WINDOW_SECONDS = 5 * 60


class OfflinePosBridgeError(ValueError):
    """Raised when POS bridge input would violate OACP runtime boundaries."""


@dataclass(frozen=True)
class OfflinePosReconciliation:
    status: str
    buyer_safe_status: str
    seller_operator_status: str
    inventory_refresh_required: bool
    artifact_refresh_required: bool
    provider_pos_evidence_ref: str | None
    receipt_evidence_ref: str | None
    raw_payment_payload_stored: bool = False
    allowed_to_execute: bool = False
    no_payment_execution: bool = True
    non_authoritative_for_transaction: bool = True


def build_offline_pos_handoff_packet(
    *,
    purchase_preparation: Mapping[str, Any],
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    store_id: str,
    pos_location: Mapping[str, Any],
    buyer_session_ref: str,
    now_iso: str,
    expiry_minutes: int = 15,
    idempotency_key: str | None = None,
    allowed_action_labels: Sequence[str] | None = None,
    blocked_action_labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a non-executing POS packet from a prepared purchase result."""

    selected_product = _mapping_or_none(purchase_preparation.get("selected_product"))
    selected_variant = _mapping_or_none(purchase_preparation.get("selected_variant"))
    prepared_handoff = _mapping_or_none(purchase_preparation.get("prepared_handoff"))
    if selected_product is None or selected_variant is None or prepared_handoff is None:
        raise OfflinePosBridgeError("purchase preparation must include selected product, variant, and handoff")
    for public_input in (selected_product, selected_variant, prepared_handoff):
        if _contains_private_or_executable_value(public_input):
            raise OfflinePosBridgeError("purchase preparation contains unsafe packet values")
    if purchase_preparation.get("allowed_to_execute") not in (False, None):
        raise OfflinePosBridgeError("purchase preparation must not grant execution authority")
    if purchase_preparation.get("no_payment_execution") not in (True, None):
        raise OfflinePosBridgeError("purchase preparation must keep no_payment_execution true")

    artifact_refs = tuple(str(ref) for ref in purchase_preparation.get("artifact_refs", ()) if str(ref).strip())
    if not artifact_refs:
        raise OfflinePosBridgeError("POS handoff requires OACP artifact refs")
    if expiry_minutes < 1 or expiry_minutes > 60:
        raise OfflinePosBridgeError("POS handoff expiry must be between 1 and 60 minutes")

    now = _parse_iso(now_iso)
    expires_at = (now + timedelta(minutes=expiry_minutes)).isoformat().replace("+00:00", "Z")
    key = (
        _safe_text(idempotency_key)
        if idempotency_key
        else _stable_id(
            "offline_pos_idempotency",
            tenant_id,
            merchant_id,
            seller_agent_id,
            buyer_session_ref,
            str(selected_product.get("product_ref") or ""),
            str(selected_variant.get("variant_ref") or selected_variant.get("sku") or ""),
            str(purchase_preparation.get("quantity") or ""),
        )
    )
    price = selected_variant.get("price")
    currency = selected_variant.get("currency")
    capability_ref = prepared_handoff.get("provider_capability_evidence_ref")
    packet = {
        "packet_id": _stable_id("offline_pos_handoff", key),
        "packet_kind": "offline_pos_bridge_handoff",
        "status": "pos_handoff_packet_ready",
        "tenant_id": _safe_text(tenant_id),
        "merchant_id": _safe_text(merchant_id),
        "seller_agent_id": _safe_text(seller_agent_id),
        "store_id": _safe_text(store_id),
        "pos_location": _public_pos_location(pos_location),
        "buyer_session_ref": _safe_text(buyer_session_ref),
        "product_id": selected_product.get("product_ref"),
        "variant_id": selected_variant.get("variant_ref") or selected_variant.get("sku"),
        "sku": selected_variant.get("sku"),
        "quantity": int(purchase_preparation.get("quantity") or 1),
        "displayed_price": price,
        "currency": currency,
        "catalog_artifact_refs": _refs_matching(artifact_refs, ("catalog", "merchant", "seller")),
        "price_artifact_refs": _refs_matching(artifact_refs, ("price", "offer")),
        "inventory_artifact_refs": _refs_matching(artifact_refs, ("inventory",)),
        "artifact_refs": artifact_refs,
        "source_label": purchase_preparation.get("source_label") or "Source: Shopify catalog",
        "freshness_label": purchase_preparation.get("freshness_label") or "Freshness: unavailable",
        "freshness_timestamps": {
            "generated_at": now_iso,
            "expires_at": expires_at,
        },
        "expiry_window_seconds": expiry_minutes * 60,
        "risk_tier": _risk_tier(price, int(purchase_preparation.get("quantity") or 1), currency),
        "allowed_action_labels": _public_action_labels(allowed_action_labels or (
            "staff_final_price_inventory_check",
            "buyer_present_payment_at_pos",
            "pos_provider_confirmation_callback",
            "non_sensitive_receipt_evidence_ref",
        )),
        "blocked_action_labels": _public_action_labels(blocked_action_labels or (
            "agent_payment_capture",
            "agent_order_success_claim",
            "inventory_reservation_without_pos_confirmation",
            "raw_payment_payload_storage",
            "raw_pos_payload_storage",
        )),
        "non_sensitive_evidence_refs": tuple(
            ref for ref in (capability_ref, *artifact_refs) if isinstance(ref, str) and ref.strip()
        ),
        "idempotency_key": key,
        "callback_verification_required": True,
        "raw_payload_stored": False,
        "raw_payment_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_order_creation": True,
        "non_authoritative_for_transaction": True,
    }
    if _contains_private_or_executable_value(packet):
        raise OfflinePosBridgeError("POS handoff packet contains private or executable values")
    return packet


def verify_offline_pos_callback_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    *,
    timestamp_header: str | None = None,
    now: datetime | None = None,
    replay_window_seconds: int = _DEFAULT_CALLBACK_REPLAY_WINDOW_SECONDS,
) -> bool:
    """Verify a provider-neutral POS callback HMAC.

    The signature base is ``<unix_timestamp>.<raw_body>``. The signature header
    may be either a bare hex digest or ``sha256=<hex>``.
    """

    if not raw_body or not signature_header or not secret or not timestamp_header:
        return False
    signature = signature_header.strip()
    if signature.lower().startswith("sha256="):
        signature = signature.split("=", 1)[1].strip()
    if len(signature) != 64:
        return False

    timestamp = _parse_callback_timestamp(timestamp_header)
    if timestamp is None:
        return False
    current = now or datetime.now(UTC)
    skew = abs((current - timestamp).total_seconds())
    if skew > replay_window_seconds:
        return False
    signed_payload = timestamp_header.strip().encode("utf-8") + b"." + raw_body

    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def build_offline_pos_confirmation_intake(
    *,
    packet: Mapping[str, Any],
    confirmation_status: str,
    now_iso: str,
    final_price: str | None = None,
    currency: str | None = None,
    provider_pos_evidence_ref: str | None = None,
    receipt_evidence_ref: str | None = None,
    callback_verified: bool = False,
    simulator_mode: bool = False,
    inventory_refresh_required: bool | None = None,
    artifact_refresh_required: bool | None = None,
) -> dict[str, Any]:
    """Normalize POS confirmation callback data into a safe intake object."""

    status = _safe_text(confirmation_status)
    if status not in POS_CONFIRMATION_STATUSES:
        status = "unsupported"
    if _contains_private_or_executable_value(packet):
        raise OfflinePosBridgeError("POS packet contains unsafe values")
    if provider_pos_evidence_ref and _contains_private_or_executable_value(provider_pos_evidence_ref):
        raise OfflinePosBridgeError("provider POS evidence ref contains unsafe values")
    if receipt_evidence_ref and _contains_private_or_executable_value(receipt_evidence_ref):
        raise OfflinePosBridgeError("receipt evidence ref contains unsafe values")
    if status in _PAYMENT_SUCCESS_STATUSES and (
        simulator_mode or not callback_verified or not provider_pos_evidence_ref
    ):
        status = "needs_staff_review"
    confirmation = {
        "confirmation_id": _stable_id(
            "offline_pos_confirmation",
            str(packet.get("packet_id") or ""),
            confirmation_status,
            now_iso,
        ),
        "packet_id": packet.get("packet_id"),
        "tenant_id": packet.get("tenant_id"),
        "merchant_id": packet.get("merchant_id"),
        "seller_agent_id": packet.get("seller_agent_id"),
        "store_id": packet.get("store_id"),
        "confirmation_status": status,
        "requested_status": confirmation_status,
        "callback_verified": callback_verified,
        "simulator_mode": simulator_mode,
        "final_price": final_price if final_price is not None else packet.get("displayed_price"),
        "currency": currency if currency is not None else packet.get("currency"),
        "provider_pos_evidence_ref": provider_pos_evidence_ref,
        "receipt_evidence_ref": receipt_evidence_ref if status == "receipt_available" else None,
        "confirmed_at": now_iso,
        "inventory_refresh_required": (
            status in {"out_of_stock", "price_changed", "expired"}
            if inventory_refresh_required is None
            else inventory_refresh_required
        ),
        "artifact_refresh_required": (
            status in {"out_of_stock", "price_changed", "expired"}
            if artifact_refresh_required is None
            else artifact_refresh_required
        ),
        "raw_payload_stored": False,
        "raw_payment_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }
    return confirmation


def reconcile_offline_pos_confirmation(
    *,
    packet: Mapping[str, Any],
    confirmation: Mapping[str, Any],
) -> OfflinePosReconciliation:
    """Return buyer-safe and operator-safe reconciliation status."""

    status = str(confirmation.get("confirmation_status") or "unsupported")
    evidence_ref = confirmation.get("provider_pos_evidence_ref")
    receipt_ref = confirmation.get("receipt_evidence_ref")
    if status == "accepted":
        buyer = "POS accepted the handoff. Staff must confirm final price and payment at the store."
        operator = "handoff_accepted_waiting_for_pos_staff_payment_step"
    elif status == "price_changed":
        buyer = "Final price changed at POS. Buyer confirmation is required before payment."
        operator = "price_changed_refresh_artifacts"
    elif status == "out_of_stock":
        buyer = "The POS reported out of stock. The agent cannot complete the purchase."
        operator = "out_of_stock_refresh_inventory"
    elif status == "expired":
        buyer = "The POS handoff expired. Start a fresh handoff after refreshing source facts."
        operator = "expired_rebuild_packet"
    elif status == "payment_pending":
        buyer = "Payment is pending at the POS/provider. No success is confirmed yet."
        operator = "payment_pending_wait_for_verified_callback"
    elif status == "payment_failed":
        buyer = "Payment failed at the POS/provider. No order or paid state is recorded."
        operator = "payment_failed_no_success"
    elif status == "payment_confirmed":
        buyer = "Payment confirmation was received from POS/provider evidence."
        operator = "payment_confirmed_by_verified_pos_provider_callback"
    elif status == "receipt_available":
        buyer = "A POS/provider receipt evidence reference is available."
        operator = "receipt_ref_available_no_raw_payment_payload"
    elif status == "needs_staff_review":
        buyer = "Store staff review is required before the buyer can treat this as final."
        operator = "needs_staff_review"
    else:
        buyer = "This POS confirmation is unsupported. The agent cannot complete the purchase."
        operator = "unsupported_confirmation"

    return OfflinePosReconciliation(
        status=status,
        buyer_safe_status=buyer,
        seller_operator_status=operator,
        inventory_refresh_required=bool(confirmation.get("inventory_refresh_required")),
        artifact_refresh_required=bool(confirmation.get("artifact_refresh_required")),
        provider_pos_evidence_ref=str(evidence_ref) if evidence_ref else None,
        receipt_evidence_ref=str(receipt_ref) if receipt_ref else None,
    )


def simulate_offline_pos_confirmation(
    *,
    packet: Mapping[str, Any],
    now_iso: str,
    confirmation_status: Literal[
        "accepted",
        "price_changed",
        "out_of_stock",
        "expired",
        "needs_staff_review",
        "unsupported",
        "payment_pending",
        "payment_failed",
    ] = "accepted",
    final_price: str | None = None,
) -> dict[str, Any]:
    """Deterministic local POS adapter for tests and demos."""

    return build_offline_pos_confirmation_intake(
        packet=packet,
        confirmation_status=confirmation_status,
        now_iso=now_iso,
        final_price=final_price,
        currency=str(packet.get("currency") or "") or None,
        provider_pos_evidence_ref=f"pos:simulator:{_sha256(str(packet.get('packet_id')))}:redacted",
        callback_verified=False,
        simulator_mode=True,
    )


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _public_pos_location(value: Mapping[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {}
    for key in ("store_id", "location_id", "display_name", "city", "country_code", "pos_provider"):
        item = value.get(key)
        if item is not None and str(item).strip():
            location[key] = _safe_text(str(item))
    return location


def _refs_matching(refs: Sequence[str], needles: Sequence[str]) -> tuple[str, ...]:
    selected = [ref for ref in refs if any(needle in ref.lower() for needle in needles)]
    return tuple(selected or refs)


def _risk_tier(price: Any, quantity: int, currency: Any) -> str:
    try:
        amount = Decimal(str(price or "0")) * Decimal(max(quantity, 1))
    except (InvalidOperation, ValueError):
        return "medium"
    currency_text = str(currency or "").upper()
    if amount <= 0:
        return "medium"
    if currency_text == "INR":
        if amount <= Decimal("2500"):
            return "low"
        if amount <= Decimal("25000"):
            return "medium"
        return "high"
    if amount <= Decimal("30"):
        return "low"
    if amount <= Decimal("300"):
        return "medium"
    return "high"


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise OfflinePosBridgeError("required POS field is empty")
    if _contains_private_or_executable_value(text):
        raise OfflinePosBridgeError("POS value contains unsafe content")
    return text


def _parse_callback_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(int(text), UTC)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _public_action_labels(values: Sequence[str]) -> tuple[str, ...]:
    labels: list[str] = []
    credential_markers = (
        "access_token",
        "authorization",
        "bearer ",
        "card_number",
        "client_secret",
        "cvv",
        "password",
        "private_key",
    )
    for value in values:
        text = str(value or "").strip()
        if not text:
            raise OfflinePosBridgeError("POS action label is empty")
        lowered = text.lower()
        if any(marker in lowered for marker in credential_markers):
            raise OfflinePosBridgeError("POS action label contains private credential wording")
        labels.append(text)
    return tuple(labels)


def _contains_private_or_executable_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"allowed_action_labels", "blocked_action_labels"}:
                _public_action_labels(tuple(str(label) for label in item))
                continue
            if any(marker in key_text for marker in _PRIVATE_POS_MARKERS):
                if key_text.startswith("raw_") and key_text.endswith("_stored") and item is False:
                    pass
                elif key_text in {"provider_pos_evidence_ref", "receipt_evidence_ref"}:
                    pass
                elif "redacted_evidence_ref" in key_text:
                    pass
                else:
                    return True
            if any(marker in key_text for marker in _EXECUTION_MARKERS):
                return True
            if _contains_private_or_executable_value(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_private_or_executable_value(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _PRIVATE_POS_MARKERS + _EXECUTION_MARKERS)
    return False


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{_sha256('|'.join(str(part) for part in parts))[:24]}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
