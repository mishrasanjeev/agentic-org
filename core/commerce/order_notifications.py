# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Tell the merchant about a new order, or a payment that needs its attention: an email to its administrators.

Each email has a row in the durable outbox (``core.commerce.merchant_notifications``),
written in the transaction that created the order or recorded the payment; the
row is sent from a Celery task (kicked after commit, and retried by a beat
sweep with backoff until it is sent or dead), through the platform SMTP
transport (``core.email.send_email``). The email carries the order, the items,
the total, the delivery address and shipping choice, and the buyer contact,
exactly as the buyer gave them to the checkout; nothing is invented. A failed
send never changes the order. The ledger records how many administrators were
emailed, never their addresses.

A payment no order matches (``unmatched_payment_email``): a buyer paid on the
merchant's Plural page, but AgenticOrg created no order (the amount differed,
the checkout was canceled or changed, or the payment is for another Plural
order, or a second payment for a checkout already paid through another of its links).
The money is in the merchant's own Plural account, so the email names
the Plural order and asks the merchant to refund the buyer (from the Orders page
of AgenticOrg, or in Plural) or contact them.

Stock short at payment (``out_of_stock_at_payment``): the buyer paid, the order
was created, but the shop's catalog no longer has enough stock for every line.
The email names the order and the short lines and asks the merchant to ship
what it can and refund the rest, or refund the order.
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
UNMATCHED_NOTIFY_EVENT = "commerce.payment.unmatched_notification"
UNMATCHED_REASONS = {
    "amount_mismatch": "the amount paid is not the amount of the checkout",
    "paid_after_cancel": "the checkout had been canceled before the payment arrived",
    "checkout_changed": "the cart changed after the payment link was issued",
    "order_mismatch": "the payment is for a different Plural order than the one this checkout issued",
    "paid_twice": "the buyer had already paid this checkout through another of its payment links",
    "out_of_stock_at_payment": "the shop's stock no longer covers every item of the order",
}
# The reasons whose payment did become an order (the merchant still has to act on it).
ORDER_ATTENTION_REASONS = frozenset({"out_of_stock_at_payment"})
ORDERS_PAGE_PATH = "/dashboard/orders"


def orders_page_url() -> str:
    """The console's Orders page: absolute when ``AGENTICORG_UI_BASE_URL`` is set, else the path."""
    from core.config import settings

    base = str(getattr(settings, "ui_base_url", "") or "").strip().rstrip("/")
    return f"{base}{ORDERS_PAGE_PATH}" if base.startswith("https://") else ORDERS_PAGE_PATH
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


def delivery_heading(item: Mapping[str, Any]) -> str:
    """``Ship - Standard delivery`` or ``Store pickup - MG Road store``: the method and the option chosen.

    No fee here: the order keeps one fulfillment total for all its deliveries
    (``shipping_fee_line`` shows it once), not a fee per delivery.
    """
    parts = ["Store pickup" if item["method"] == "pickup" else "Ship", str(item["option"] or "")]
    return " - ".join(part for part in parts if part)


def shipping_fee_line(deliveries: list[Mapping[str, Any]], shipping: str | None) -> str:
    """The order's shipping fee, once: ``Shipping fee: Rs 49.00``, or ``Shipping fee for all deliveries:
    Rs 98.00`` when the order ships more than once; empty when nothing ships or there is no fee."""
    shipped = sum(1 for item in deliveries if item["method"] != "pickup")
    if not shipping or not shipped:
        return ""
    return f"{'Shipping fee for all deliveries' if shipped > 1 else 'Shipping fee'}: {shipping}"


def total_of(snapshot: Mapping[str, Any], kind: str) -> Any:
    return next((t.get("amount") for t in snapshot.get("totals") or [] if t.get("type") == kind), None)


def _contact_items(buyer: Mapping[str, Any]) -> str:
    """The buyer's contact as ``<li>`` items, exactly as given (escaped)."""
    return (
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
    deliveries = delivery_view(snapshot)
    delivery = (
        "".join(
            f"<li>{html.escape(delivery_heading(item))}"
            + "".join(f"<br>{html.escape(line)}" for line in item["address"])
            + "</li>"
            for item in deliveries
        )
        or "<li>The buyer gave no delivery details.</li>"
    )
    fee = shipping_fee_line(deliveries, money(total_of(snapshot, "fulfillment"), currency))
    fee_html = f"<p>{html.escape(fee)}</p>" if fee else ""
    contact = _contact_items(buyer)
    subject = f"New order {order_id} - {money(total, currency)}".strip(" -")
    body = (
        f"<p>{html.escape(merchant_name)} has a new paid order.</p>"
        f"<p><strong>Order:</strong> {html.escape(order_id)}<br>"
        f"<strong>Total:</strong> {html.escape(money(total, currency))}<br>"
        f"<strong>Placed through:</strong> {html.escape(surface)}</p>"
        f"<p><strong>Items</strong></p><ul>{lines}</ul>"
        f"<p><strong>Delivery (as given)</strong></p><ul>{delivery}</ul>{fee_html}"
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


def unmatched_payment_email(
    *,
    merchant_name: str,
    checkout_id: str,
    result: str,
    amount_minor_units: int | None,
    currency: str | None,
    provider_order_id: str,
    buyer: Mapping[str, Any],
    surface: str,
) -> tuple[str, str]:
    """``(subject, html)`` of the email about a payment no order matches; every value is escaped."""
    paid = money(amount_minor_units, currency) or "an amount"
    reason = UNMATCHED_REASONS.get(result, "it does not match the checkout")
    subject = f"Action needed: a payment of {paid} has no order"
    body = (
        f"<p>A buyer paid {html.escape(paid)} on {html.escape(merchant_name)}'s Plural payment page, "
        f"but AgenticOrg created no order, because {html.escape(reason)}. No order was confirmed to the "
        "buyer, so nothing needs to ship.</p>"
        f"<p><strong>Plural order:</strong> {html.escape(provider_order_id or 'not given')}<br>"
        f"<strong>Checkout:</strong> {html.escape(checkout_id)}<br>"
        f"<strong>Placed through:</strong> {html.escape(surface)}</p>"
        f"<p><strong>Buyer contact (as given)</strong></p><ul>{_contact_items(buyer)}</ul>"
        "<p>The money is in your own Plural account. Refund it from AgenticOrg: open the Orders page "
        f"({html.escape(orders_page_url())}) and use Refund under Payments with no order. Or find this "
        "Plural order in your Plural dashboard and refund it there, or contact the buyer to sort it out.</p>"
    )
    return subject, body


def stock_short_email(
    *,
    merchant_name: str,
    order_id: str,
    lines: Any,
    amount_minor_units: int | None,
    currency: str | None,
    provider_order_id: str,
    buyer: Mapping[str, Any],
    surface: str,
) -> tuple[str, str]:
    """``(subject, html)`` of the email about an order paid while stock was short; every value is escaped."""
    paid = money(amount_minor_units, currency) or "an amount"
    short = "".join(
        f"<li>{html.escape(str(line.get('title') or line.get('item_id') or 'An item'))}: ordered "
        f"{html.escape(str(line.get('quantity', '')))}, in stock {html.escape(str(line.get('in_stock', 0)))}</li>"
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, Mapping)
    ) or "<li>The catalog no longer covers every item.</li>"
    subject = f"Action needed: order {order_id} was paid but stock is short"
    body = (
        f"<p>A buyer paid {html.escape(paid)} on {html.escape(merchant_name)}'s Plural payment page and "
        f"AgenticOrg created order {html.escape(order_id)}, but the shop's catalog no longer has enough "
        "stock for every item. The money was already taken.</p>"
        f"<p><strong>Short items</strong></p><ul>{short}</ul>"
        f"<p><strong>Plural order:</strong> {html.escape(provider_order_id or 'not given')}<br>"
        f"<strong>Placed through:</strong> {html.escape(surface)}</p>"
        f"<p><strong>Buyer contact (as given)</strong></p><ul>{_contact_items(buyer)}</ul>"
        "<p>Ship what you can and refund the rest, or refund the order. Refund it from AgenticOrg: open the "
        f"Orders page ({html.escape(orders_page_url())}) and use Refund on this order. Or refund it in your "
        "Plural dashboard, and contact the buyer.</p>"
    )
    return subject, body


async def notify_merchant_of_unmatched_payment(
    tenant_id: str,
    checkout_session_id: str,
    *,
    result: str,
    amount_minor_units: int | None,
    currency: str | None,
    provider_order_id: str,
    order_id: str | None = None,
    lines: Any = None,
) -> int:
    """Email the tenant's active administrators about a payment that needs them; returns how many accepted."""
    from core.commerce.ledger import record_commerce_event
    from core.database import get_tenant_session
    from core.email import send_email
    from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow
    from core.models.commerce_ucp import CommerceCheckoutSessionRow
    from core.models.user import User

    tenant = uuid.UUID(str(tenant_id))
    async with get_tenant_session(tenant) as session:
        checkout = (
            await session.execute(
                select(CommerceCheckoutSessionRow).where(
                    CommerceCheckoutSessionRow.tenant_id == tenant,
                    CommerceCheckoutSessionRow.id == checkout_session_id,
                )
            )
        ).scalar_one_or_none()
        if checkout is None:
            return 0
        config = (
            await session.execute(
                select(C6ZMerchantCommerceConfigRow.merchant_display_name).where(
                    C6ZMerchantCommerceConfigRow.tenant_id == str(tenant),
                    C6ZMerchantCommerceConfigRow.merchant_id == checkout.merchant_id,
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
        state = dict(checkout.state or {})
        merchant_id, seller = checkout.merchant_id, checkout.seller_agent_id
        profile_url = checkout.platform_profile_url
        checkout_order_id = checkout.order_id
    if result in ORDER_ATTENTION_REASONS:
        subject, body = stock_short_email(
            merchant_name=str((config[0] if config else None) or merchant_id),
            order_id=str(order_id or checkout_order_id or ""),
            lines=lines,
            amount_minor_units=amount_minor_units,
            currency=currency,
            provider_order_id=provider_order_id,
            buyer=dict(state.get("buyer") or {}),
            surface=surface_label(state, profile_url),
        )
    else:
        subject, body = unmatched_payment_email(
            merchant_name=str((config[0] if config else None) or merchant_id),
            checkout_id=checkout_session_id,
            result=result,
            amount_minor_units=amount_minor_units,
            currency=currency,
            provider_order_id=provider_order_id,
            buyer=dict(state.get("buyer") or {}),
            surface=surface_label(state, profile_url),
        )
    sent = 0
    for address in sorted({str(a) for a in recipients if a}):
        try:
            if await asyncio.to_thread(send_email, address, subject, body):
                sent += 1
        # enterprise-gate: broad-except-ok reason=unmatched-payment-email-failure-is-logged-ledger-stays
        except Exception as exc:
            logger.warning("unmatched_payment_notification_failed", error_type=type(exc).__name__)
    async with get_tenant_session(tenant) as session:
        await record_commerce_event(
            session,
            tenant_id=str(tenant),
            event=UNMATCHED_NOTIFY_EVENT,
            outcome="success" if sent else "blocked",
            merchant_id=merchant_id,
            seller_agent_id=seller,
            buyer_agent_id=None,
            payload={"checkout_session_id": checkout_session_id, "result": result, "recipients": len(recipients)},
            resource_id=checkout_session_id,
            details={"result": result, "recipients": len(recipients), "sent": sent},
        )
    return sent


def kick_unmatched_payment_notification(
    tenant_id: str,
    checkout_session_id: str,
    *,
    result: str,
    amount_minor_units: int | None,
    currency: str | None,
    provider_order_id: str,
) -> None:
    """After the confirmation's commit: send the payment-attention email now (best effort, off the event loop).

    The email's outbox row was written in the confirmation's transaction; the beat sweep sends it if
    this kick is lost or the send fails.
    """
    from core.commerce.merchant_notifications import kick

    kick(str(tenant_id))


def kick_order_notification(tenant_id: str, order_id: str) -> None:
    """After the order's commit: send the new-order email now (best effort; the outbox sweep backs it up)."""
    from core.commerce.merchant_notifications import kick

    kick(str(tenant_id))
