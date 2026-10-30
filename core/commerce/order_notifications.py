# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Tell the merchant about a new order: an email to every active administrator of the tenant.

Sent after the order's transaction commits, from a Celery task
(``core.tasks.payment_handoff_tasks.notify_order``), through the platform SMTP
transport (``core.email.send_email``). The email carries the order, the items,
the total, the delivery address and shipping choice, and the buyer contact,
exactly as the buyer gave them to the checkout;
nothing is invented. A failed send is logged and never changes the order.
The ledger records how many administrators were emailed, never their addresses.
"""

from __future__ import annotations

import asyncio
import html
import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy import select

logger = structlog.get_logger()

NOTIFY_EVENT = "commerce.order.notification"
SURFACE_LABELS = {
    "telegram": "Telegram",
    "mcp": "AI assistant (MCP)",
    "acp": "Agentic Commerce Protocol",
    "ucp": "UCP platform",
}


def money(amount: Any, currency: Any) -> str:
    if not isinstance(amount, int) or isinstance(amount, bool):
        return ""
    code = str(currency or "").upper()
    symbol = "Rs " if code == "INR" else f"{code} "
    return f"{symbol}{amount / 100:,.2f}"


def surface_label(state: Mapping[str, Any] | None, platform_profile_url: str | None) -> str:
    platform = (state or {}).get("platform") if isinstance((state or {}).get("platform"), Mapping) else {}
    surface = platform.get("surface") if isinstance(platform, Mapping) else None
    if isinstance(surface, str) and surface in SURFACE_LABELS:
        return SURFACE_LABELS[surface]
    if (state or {}).get("protocol") == "acp":
        return SURFACE_LABELS["acp"]
    return SURFACE_LABELS["ucp"]


# One line per group of address fields, in reading order.
_ADDRESS_LINES = (
    ("first_name", "last_name"),
    ("street_address",),
    ("extended_address",),
    ("address_locality", "address_region", "postal_code"),
    ("address_country",),
    ("phone_number",),
)


def _address_lines(address: Mapping[str, Any]) -> list[str]:
    lines = []
    for keys in _ADDRESS_LINES:
        parts = [str(address[key]).strip() for key in keys if str(address.get(key) or "").strip()]
        if parts:
            lines.append((" " if keys == ("first_name", "last_name") else ", ").join(parts))
    return lines


def delivery_view(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Where the order goes and how, from the order's fulfillment, exactly as the buyer gave it.

    One entry per fulfillment: ``method`` (shipping or pickup), ``option`` (the
    shipping rate or pickup the buyer chose) and ``address`` (the destination as
    lines). The merchant needs this to ship an order paid on its payment page,
    which collects no address.
    """
    out = []
    for expectation in (snapshot.get("fulfillment") or {}).get("expectations") or []:
        if not isinstance(expectation, Mapping):
            continue
        destination = expectation.get("destination")
        out.append(
            {
                "method": str(expectation.get("method_type") or "shipping"),
                "option": expectation.get("description") or None,
                "address": _address_lines(destination) if isinstance(destination, Mapping) else [],
            }
        )
    return out


def delivery_heading(item: Mapping[str, Any], shipping: str) -> str:
    """``Ship - Standard delivery - Rs 49.00`` or ``Store pickup - MG Road store``."""
    parts = ["Store pickup" if item["method"] == "pickup" else "Ship", str(item["option"] or "")]
    if item["method"] != "pickup":
        parts.append(shipping)
    return " - ".join(part for part in parts if part)


def total_of(snapshot: Mapping[str, Any], kind: str) -> Any:
    return next((t.get("amount") for t in snapshot.get("totals") or [] if t.get("type") == kind), None)


def order_email(
    *, merchant_name: str, order_id: str, snapshot: Mapping[str, Any], buyer: Mapping[str, Any], surface: str
) -> tuple[str, str]:
    """``(subject, html)`` of the new-order email; every value is escaped."""
    total = total_of(snapshot, "total")
    currency = snapshot.get("currency")
    lines = "".join(
        f"<li>{html.escape(str((li.get('item') or {}).get('title') or ''))} x "
        f"{html.escape(str((li.get('quantity') or {}).get('total', '')))}</li>"
        for li in snapshot.get("line_items") or []
    )
    shipping = money(total_of(snapshot, "fulfillment"), currency)
    delivery = (
        "".join(
            f"<li>{html.escape(delivery_heading(item, shipping))}"
            + "".join(f"<br>{html.escape(line)}" for line in item["address"])
            + "</li>"
            for item in delivery_view(snapshot)
        )
        or "<li>The buyer gave no delivery details.</li>"
    )
    contact = (
        "".join(
            f"<li>{html.escape(label)}: {html.escape(str(buyer[key]))}</li>"
            for key, label in (
                ("first_name", "First name"),
                ("last_name", "Last name"),
                ("email", "Email"),
                ("phone_number", "Phone"),
            )
            if buyer.get(key)
        )
        or "<li>The buyer gave no contact details.</li>"
    )
    subject = f"New order {order_id} - {money(total, currency)}".strip(" -")
    body = (
        f"<p>{html.escape(merchant_name)} has a new paid order.</p>"
        f"<p><strong>Order:</strong> {html.escape(order_id)}<br>"
        f"<strong>Total:</strong> {html.escape(money(total, currency))}<br>"
        f"<strong>Placed through:</strong> {html.escape(surface)}</p>"
        f"<p><strong>Items</strong></p><ul>{lines}</ul>"
        f"<p><strong>Delivery (as given)</strong></p><ul>{delivery}</ul>"
        f"<p><strong>Buyer contact (as given)</strong></p><ul>{contact}</ul>"
        "<p>See every order on the Orders page of your AgenticOrg console.</p>"
    )
    return subject, body


async def notify_merchant_of_order(tenant_id: str, order_id: str) -> int:
    """Email the tenant's active administrators about ``order_id``; returns how many accepted the email."""
    from core.commerce.ledger import record_commerce_event
    from core.database import get_tenant_session
    from core.email import send_email
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow
    from core.models.commerce_ucp import CommerceCheckoutSessionRow, CommerceOrderRow
    from core.models.user import User

    tenant = uuid.UUID(str(tenant_id))
    async with get_tenant_session(tenant) as session:
        order = (
            await session.execute(
                select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
            )
        ).scalar_one_or_none()
        if order is None:
            return 0
        checkout = (
            await session.execute(
                select(CommerceCheckoutSessionRow).where(
                    CommerceCheckoutSessionRow.tenant_id == tenant,
                    CommerceCheckoutSessionRow.id == order.checkout_session_id,
                )
            )
        ).scalar_one_or_none()
        config = (
            await session.execute(
                select(C6ZMerchantCommerceConfigRow.merchant_display_name).where(
                    C6ZMerchantCommerceConfigRow.tenant_id == str(tenant),
                    C6ZMerchantCommerceConfigRow.merchant_id == order.merchant_id,
                )
            )
        ).first()
        recipients = list(
            (
                await session.scalars(
                    select(User.email).where(User.tenant_id == tenant, User.role == "admin", User.status == "active")
                )
            ).all()
        )
        state = dict(checkout.state or {}) if checkout is not None else {}
        snapshot = dict(order.snapshot or {})
        merchant_id, seller = order.merchant_id, order.seller_agent_id
    subject, body = order_email(
        merchant_name=str((config[0] if config else None) or merchant_id),
        order_id=order_id,
        snapshot=snapshot,
        buyer=dict(state.get("buyer") or {}),
        surface=surface_label(state, getattr(checkout, "platform_profile_url", None)),
    )
    sent = 0
    for address in sorted({str(a) for a in recipients if a}):
        try:
            if await asyncio.to_thread(send_email, address, subject, body):
                sent += 1
        # enterprise-gate: broad-except-ok reason=order-email-failure-is-logged-the-order-stays-authoritative
        except Exception as exc:
            logger.warning("order_notification_failed", error_type=type(exc).__name__)
    async with get_tenant_session(tenant) as session:
        await record_commerce_event(
            session,
            tenant_id=str(tenant),
            event=NOTIFY_EVENT,
            outcome="success" if sent else "blocked",
            merchant_id=merchant_id,
            seller_agent_id=seller,
            buyer_agent_id=None,
            payload={"order_id": order_id, "recipients": len(recipients), "sent": sent},
            resource_id=order_id,
            details={"recipients": len(recipients), "sent": sent},
        )
    return sent


def _send_kick(tenant_id: str, order_id: str) -> None:
    try:
        from core.tasks.payment_handoff_tasks import notify_order

        notify_order.delay(str(tenant_id), str(order_id))
    # enterprise-gate: broad-except-ok reason=queue-failure-is-logged-the-order-and-orders-page-stay-authoritative
    except Exception as exc:
        logger.warning("order_notification_not_queued", error_type=type(exc).__name__)


def kick_order_notification(tenant_id: str, order_id: str) -> None:
    """Queue the email after the order's commit, off the event loop (a broker publish may block)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_kick(tenant_id, order_id)
        return
    loop.run_in_executor(None, _send_kick, tenant_id, order_id)
