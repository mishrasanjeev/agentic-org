# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""How a merchant gets paid and sees its orders; the Plural return page and webhook.

Merchant routes (``commerce.merchant_config.write`` or tenant admin), under
``/api/v1/commerce/runtime``:

* ``GET``/``PUT /merchant-configs/{merchant_id}/payments`` - the payment
  hand-off mode (``shopify_cart``, ``plural_hosted`` or ``off``), stored as
  ``provider_policy.payments`` (``core.commerce.payment_handoff``). A mode is
  saved only when it can work: a connected Shopify store for ``shopify_cart``,
  saved Plural credentials for ``plural_hosted``.
* ``PUT /merchant-configs/{merchant_id}/payments/plural-credentials`` - the
  merchant's own Plural client id, client secret and webhook secret, encrypted
  in the tenant vault (``core.commerce.plural_hosted``); never returned.
* ``POST /merchant-configs/{merchant_id}/payments/plural-credentials/test`` -
  fetch a Plural access token with them (nothing else is called).
* ``GET /orders`` - the tenant's orders, newest first, paginated, with the
  buyer contact and delivery address as the buyer gave them, the shipping
  choice, items, total, payment status and the surface the buyer used.

Public (no session; the handlers check what they need), under
``/api/v1/payments/plural``:

* ``GET``/``POST /return/{tenant_id}/{checkout_id}`` - where Plural sends the
  buyer after paying: a plain page with the checkout's outcome (it asks Plural
  for the order status first). It shows no buyer data.
* ``POST /webhooks/{tenant_id}/{webhook_key}`` - Plural's signed order webhook,
  verified with the merchant's own webhook secret before anything is applied.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from api.v1.commerce_ucp_publishing import _locked_row
from core.commerce import payment_handoff as ph
from core.commerce import plural_hosted
from core.commerce.ledger import record_commerce_event, record_commerce_event_in_own_transaction
from core.commerce.order_notifications import delivery_view, money, surface_label, total_of
from core.database import get_tenant_session

logger = structlog.get_logger()

merchant_router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Commerce Payments"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)
public_router = APIRouter(prefix="/payments/plural", tags=["Commerce Payments"])

MAX_WEBHOOK_BYTES = 256 * 1024
_CHECKOUT_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
SETTINGS_EVENT = "commerce.payments.settings.write"
CREDENTIALS_EVENT = "commerce.payments.plural.credentials.write"
TEST_EVENT = "commerce.payments.plural.credentials.test"
WEBHOOK_EVENT = "commerce.payments.plural.webhook"


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant context is not a UUID") from exc


# --- merchant: payment settings ---------------------------------------------------------------------


class PaymentSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(shopify_cart|plural_hosted|off)$")
    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)


class PluralCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: str = Field(default="sandbox", pattern="^(sandbox|production)$")
    client_id: str = Field(min_length=1, max_length=200)
    # Blank keeps the stored value (a merchant can rotate one secret at a time).
    client_secret: str | None = Field(default=None, max_length=500, repr=False)
    webhook_secret: str | None = Field(default=None, max_length=500, repr=False)


async def _payments_view(session: Any, tenant_id: str, row: Any) -> dict[str, Any]:
    settings = ph.payment_settings({"provider_policy": row.provider_policy or {}})
    credentials = await plural_hosted.credentials_row(session, tenant_id=tenant_id, merchant_id=row.merchant_id)
    webhook_key = str((credentials.config or {}).get("webhook_key") or "") if credentials is not None else ""
    shop_domain = ph.shopify_shop_domain(row.source_connectors or [])
    return {
        "merchant_id": row.merchant_id,
        "mode": settings.mode if settings is not None else "off",
        "shopify": {"shop_domain": shop_domain, "ready": shop_domain is not None},
        "plural": plural_hosted.credentials_view(
            credentials, webhook_url=ph.webhook_url(tenant_id, webhook_key) if webhook_key else None
        ),
        "links_available": ph.return_url(tenant_id, "probe") is not None,
        "agenticorg_collects_payment": False,
    }


@merchant_router.get("/merchant-configs/{merchant_id}/payments")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
)
async def get_payment_settings(
    merchant_id: str, seller_agent_id: str | None = None, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, lock=False
        )
        return await _payments_view(session, tenant_id, row)


@merchant_router.put("/merchant-configs/{merchant_id}/payments")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="payment-settings-converge",
    audit_event="commerce.payments.settings.write",
)
async def put_payment_settings(
    merchant_id: str, body: PaymentSettingsRequest, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id
        )
        problem: str | None = None
        payments: dict[str, Any] | None = None
        if body.mode == ph.MODE_SHOPIFY_CART:
            if ph.shopify_shop_domain(row.source_connectors or []) is None:
                problem = "shopify_store_not_connected"
            payments = ph.PaymentSettings(mode=ph.MODE_SHOPIFY_CART).to_dict()
        elif body.mode == ph.MODE_PLURAL_HOSTED:
            credentials = await plural_hosted.credentials_row(session, tenant_id=tenant_id, merchant_id=row.merchant_id)
            if credentials is None:
                problem = "plural_credentials_missing"
            elif not (credentials.config or {}).get("webhook_secret_set"):
                problem = "plural_webhook_secret_missing"
            payments = ph.PaymentSettings(
                mode=ph.MODE_PLURAL_HOSTED, credential_ref=plural_hosted.credential_ref_for(row.merchant_id)
            ).to_dict()
        if problem is not None:
            raise HTTPException(422, {"code": problem, "message": _SETUP_MESSAGES[problem]})
        policy = dict(row.provider_policy or {})
        if payments is None:
            policy.pop("payments", None)
        else:
            policy["payments"] = payments
        row.provider_policy = policy
        row.updated_at = datetime.now(UTC)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=SETTINGS_EVENT,
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload={"merchant_id": row.merchant_id, "mode": body.mode},
            resource_id=row.config_id,
        )
        return await _payments_view(session, tenant_id, row)


_SETUP_MESSAGES = {
    "shopify_store_not_connected": "Connect your Shopify store first; buyers pay in its checkout.",
    "plural_credentials_missing": "Save your Plural credentials first.",
    "plural_webhook_secret_missing": "Add your Plural webhook secret so payments can be confirmed.",
}


@merchant_router.put("/merchant-configs/{merchant_id}/payments/plural-credentials")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="merchant-plural-credential-upsert",
    audit_event="commerce.payments.plural.credentials.write",
)
async def put_plural_credentials(
    merchant_id: str, body: PluralCredentialsRequest, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=None, lock=False
        )
        try:
            row = await plural_hosted.save_credentials(
                session,
                tenant_id=tenant_id,
                merchant_id=config.merchant_id,
                environment=body.environment,
                client_id=body.client_id.strip(),
                client_secret=(body.client_secret or "").strip() or None,
                webhook_secret=(body.webhook_secret or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(422, {"code": "plural_credentials_invalid", "message": str(exc)}) from None
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=CREDENTIALS_EVENT,
            outcome="success",
            merchant_id=config.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={
                "merchant_id": config.merchant_id,
                "credential_ref": row.connector_name,
                "environment": body.environment,
                "credential_values_redacted": True,
            },
            resource_id=row.connector_name,
        )
        return await _payments_view(session, tenant_id, config)


@merchant_router.post("/merchant-configs/{merchant_id}/payments/plural-credentials/test")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="credential-test-has-no-side-effect-beyond-last-test",
    audit_event="commerce.payments.plural.credentials.test",
)
async def check_plural_credentials(merchant_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=None, lock=False
        )
        stored = await plural_hosted.load_credentials(
            session,
            tenant_id=tenant_id,
            credential_ref=plural_hosted.credential_ref_for(config.merchant_id),
            merchant_id=config.merchant_id,
        )
    if stored is None:
        raise HTTPException(
            404, {"code": "plural_credentials_missing", "message": _SETUP_MESSAGES["plural_credentials_missing"]}
        )
    ok, code = await plural_hosted.check_credentials(stored.credentials)
    tested_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    async with get_tenant_session(tenant) as session:
        row = await plural_hosted.credentials_row(session, tenant_id=tenant_id, merchant_id=config.merchant_id)
        if row is not None:
            row.config = {**dict(row.config or {}), "last_test": {"ok": ok, "code": code, "at": tested_at}}
            row.health_status = "healthy" if ok else "unhealthy"
            row.last_health_check = datetime.now(UTC)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=TEST_EVENT,
            outcome="success" if ok else "refused",
            merchant_id=config.merchant_id,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"merchant_id": config.merchant_id, "environment": stored.environment, "result": code},
            resource_id=stored.credential_ref,
        )
    return {"ok": ok, "code": code, "environment": stored.environment, "tested_at": tested_at}


# --- merchant: orders -------------------------------------------------------------------------------


def _order_view(order: Any, checkout: Any) -> dict[str, Any]:
    snapshot = dict(order.snapshot or {})
    state = dict(checkout.state or {}) if checkout is not None else {}
    buyer = {
        k: v
        for k, v in dict(state.get("buyer") or {}).items()
        if k in ("first_name", "last_name", "email", "phone_number")
    }
    provider = "plural" if order.executor in ("plural_hosted", "plural_pine") else order.executor
    return {
        "id": order.id,
        "merchant_id": order.merchant_id,
        "checkout_id": order.checkout_session_id,
        "created_at": order.created_at.isoformat().replace("+00:00", "Z") if order.created_at else None,
        "currency": order.currency,
        "total_minor_units": int(order.total_minor_units),
        "total": money(int(order.total_minor_units), order.currency),
        "items": [
            {
                "title": (li.get("item") or {}).get("title"),
                "quantity": (li.get("quantity") or {}).get("total"),
                "unit_price": money((li.get("item") or {}).get("price"), order.currency),
                "status": li.get("status"),
            }
            for li in snapshot.get("line_items") or []
        ],
        "buyer": buyer,
        # Each delivery names its method and the option chosen; the fee is the order's one fulfillment
        # total, for all its deliveries together, so it is shown once and never per delivery.
        "delivery": delivery_view(snapshot),
        "shipping": money(total_of(snapshot, "fulfillment"), order.currency) or None,
        "payment": {
            "status": "paid",
            "provider": provider,
            "provider_order_ref_present": bool(order.provider_order_ref),
        },
        "surface": surface_label(state, order.platform_profile_url),
        "permalink_url": snapshot.get("permalink_url"),
    }


@merchant_router.get("/orders")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.orders.read",
    rate_limit="standard",
    idempotency="read-only",
)
async def list_orders(
    merchant_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The tenant's orders, newest first (``limit`` 1-100, ``offset`` from 0)."""
    from core.models.commerce_ucp import CommerceCheckoutSessionRow, CommerceOrderRow

    if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
        raise HTTPException(422, "limit must be 1-100 and offset 0-100000")
    tenant = _tenant_uuid(tenant_id)
    async with get_tenant_session(tenant) as session:
        query = select(CommerceOrderRow).where(CommerceOrderRow.tenant_id == tenant)
        if merchant_id:
            query = query.where(CommerceOrderRow.merchant_id == merchant_id)
        rows = list(
            (
                await session.scalars(
                    query.order_by(CommerceOrderRow.created_at.desc(), CommerceOrderRow.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        )
        page = rows[:limit]
        ids = [r.checkout_session_id for r in page]
        checkouts = (
            {
                c.id: c
                for c in (
                    await session.scalars(
                        select(CommerceCheckoutSessionRow).where(
                            CommerceCheckoutSessionRow.tenant_id == tenant, CommerceCheckoutSessionRow.id.in_(ids)
                        )
                    )
                ).all()
            }
            if ids
            else {}
        )
    return {
        "orders": [_order_view(r, checkouts.get(r.checkout_session_id)) for r in page],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if len(rows) > limit else None,
    }


# --- public: the buyer's return page and Plural's webhook ---------------------------------------------


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:48px 16px;
background:#f7f7f5;color:#1d1d1b}}
main{{max-width:480px;margin:0 auto;background:#fff;border:1px solid #e3e3df;border-radius:12px;padding:28px}}
h1{{font-size:1.35rem;margin:0 0 12px}}p{{line-height:1.5;margin:0 0 10px}}</style></head>
<body><main><h1>{title}</h1>{body}</main></body></html>"""


def _page(title: str, *paragraphs: str, status: int = 200) -> HTMLResponse:
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=body),
        status_code=status,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"},
    )


async def _load_checkout(tenant: uuid.UUID, checkout_id: str) -> Any:
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    async with get_tenant_session(tenant) as session:
        return (
            await session.execute(select(Row).where(Row.tenant_id == tenant, Row.id == checkout_id))
        ).scalar_one_or_none()


async def _return_page(tenant_id: str, checkout_id: str) -> HTMLResponse:
    try:
        tenant = uuid.UUID(tenant_id)
    except ValueError:
        tenant = None
    if tenant is None or not _CHECKOUT_ID.fullmatch(checkout_id):
        return _page("Checkout not found", "We could not find this checkout.", status=404)
    row = await _load_checkout(tenant, checkout_id)
    handoff = (row.state or {}).get("handoff") if row is not None else None
    if row is None or not isinstance(handoff, dict) and not (row.state or {}).get("handoff_refs"):
        return _page("Checkout not found", "We could not find this checkout.", status=404)
    refreshed = None
    if row.status == "requires_escalation":
        refreshed = await plural_hosted.refresh_handoff(
            tenant_id=str(tenant), session_row=row, source="return", report_untouched=True
        )
        row = await _load_checkout(tenant, checkout_id) or row
    if row.status == "completed":
        return _page(
            "Payment received",
            f"Thank you. Your order {row.order_id} is confirmed with the shop.",
            "You can close this page and go back to your chat.",
        )
    if row.status == "canceled":
        return _page("Checkout canceled", "This checkout was canceled. Nothing more is needed here.")
    # Declined on the newest link while an earlier one, which nobody has started to pay, waits again. While the
    # earlier link is being paid, or Plural cannot say, the buyer is asked to wait instead.
    declined = refreshed in ("link_retired", "not_attempted") and ph.newest_link_failed(row.state or {})
    if row.status == "ready_for_complete" or declined:
        return _page(
            "Payment not completed",
            "The payment did not go through.",
            "Go back to your chat and ask for a new payment link to try again.",
        )
    return _page(
        "Confirming your payment",
        "We are confirming your payment with the shop. Refresh this page in a minute to see the result.",
    )


@public_router.get("/return/{tenant_id}/{checkout_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:payments.plural.return",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.payments.plural.return",
    public_reason="buyer-return-page-shows-only-checkout-status-no-pii",
)
async def plural_return(tenant_id: str, checkout_id: str) -> HTMLResponse:
    return await _return_page(tenant_id, checkout_id)


@public_router.post("/return/{tenant_id}/{checkout_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:payments.plural.return",
    rate_limit="public-read",
    idempotency="return-page-applies-only-provider-confirmed-status",
    audit_event="commerce.payments.plural.return",
    public_reason="buyer-return-page-ignores-the-body-and-reads-status-from-the-provider",
)
async def plural_return_post(tenant_id: str, checkout_id: str) -> HTMLResponse:
    # Plural may send the buyer back with a form POST; its fields are ignored (the status comes from Plural's API).
    return await _return_page(tenant_id, checkout_id)


@public_router.post("/webhooks/{tenant_id}/{webhook_key}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:payments.plural.webhook",
    rate_limit="provider-webhook",
    idempotency="plural-webhook-id-applied-once-per-checkout",
    audit_event=WEBHOOK_EVENT,
    public_reason="plural-webhook-hmac-signature-verified-with-the-merchant-secret",
)
async def plural_webhook(tenant_id: str, webhook_key: str, request: Request) -> JSONResponse:
    try:
        tenant = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(404, "Not Found") from None
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "event too large")
    names = ("webhook-id", "webhook-timestamp", "webhook-signature")
    headers = {name: request.headers.get(name, "") for name in names}
    try:
        result = await plural_hosted.apply_webhook(
            tenant_id=str(tenant), webhook_key=webhook_key, raw_body=raw, headers=headers
        )
    except plural_hosted.PluralWebhookError as exc:
        if exc.code == "unknown_endpoint":
            raise HTTPException(404, "Not Found") from None
        await record_commerce_event_in_own_transaction(
            tenant_id=str(tenant),
            event=WEBHOOK_EVENT,
            outcome="refused",
            merchant_id=None,
            seller_agent_id=None,
            buyer_agent_id=None,
            payload={"reason": exc.code},
            details={"reason": exc.code},
        )
        raise HTTPException(400, exc.code) from None
    await record_commerce_event_in_own_transaction(
        tenant_id=str(tenant),
        event=WEBHOOK_EVENT,
        outcome="success",
        merchant_id=None,
        seller_agent_id=None,
        buyer_agent_id=None,
        payload={"body_sha256": result["body_sha256"], "event_type": result["event_type"], "result": result["result"]},
        details={"applied": result["applied"], "result": result["result"]},
    )
    return JSONResponse({"received": True, "applied": result["applied"], "result": result["result"]})
