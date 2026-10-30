# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Plural hosted checkout for one merchant: its own credentials, the hosted order, and its signed webhook.

Credentials. Each merchant saves its own Plural client id, client secret and
webhook secret; they are encrypted with the tenant's key
(``core.crypto.tenant_secrets.encrypt_for_tenant``) into a ``connector_configs``
row named ``commerce_plural_<sha256(merchant_id)[:24]>``, exactly like the
Shopify connector credentials. That name is the ``credential_ref`` the
merchant's ``provider_policy.payments.plural`` names. The row's non-secret
config records the merchant, the environment (``sandbox`` or ``production``,
which fixes the API base URL; no caller-chosen host) and a random
``webhook_key`` that addresses the merchant's webhook URL. Secrets are never
returned, logged or ledgered.

Hosted order. ``POST {base}/checkout/v1/orders`` with
``merchant_order_reference``, ``order_amount {value, currency}``,
``pre_auth: false``, ``allowed_payment_methods`` (CARD, UPI, NETBANKING),
``callback_url`` and ``purchase_details {customer, merchant_metadata}``; the
answer carries ``order_id`` and ``redirect_url`` (the page the buyer pays on).
Order status: ``GET {base}/pay/v1/orders/{order_id}``. A token comes from
``POST {base}/auth/v1/token`` (client credentials); every call carries
``Request-ID`` and ``Request-Timestamp`` (``core.commerce.executors.plural.PluralApiClient``).

Webhook. Plural signs ``{webhook-id}.{webhook-timestamp}.{body}`` with
HMAC-SHA256 under the merchant's base64 webhook secret
(``core.billing.pinelabs_client.plural_signature_valid``); a timestamp more
than five minutes away is refused, and a ``webhook-id`` already applied to the
session is a no-op.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from core.commerce.executors.plural import (
    PluralApiClient,
    PluralCredentials,
    PluralRequestError,
    PluralUnavailableError,
    PluralWebhookError,
    PluralWebhookEvent,
    TransportFactory,
    request_id,
    verify_webhook,
)

logger = structlog.get_logger()

CONNECTOR_PREFIX = "commerce_plural_"
AUTH_TYPE = "plural_client_credentials"
ENVIRONMENTS: tuple[str, ...] = ("sandbox", "production")
ALLOWED_METHODS: tuple[str, ...] = ("CARD", "UPI", "NETBANKING")
_WEBHOOK_KEY = re.compile(r"^[0-9a-f]{32}$")
# Tests swap the HTTP transport here (an httpx MockTransport factory); production leaves it None (pinned egress).
transport_override: TransportFactory | None = None

__all__ = [
    "PluralRequestError",
    "PluralUnavailableError",
    "PluralWebhookError",
]


def credential_ref_for(merchant_id: str) -> str:
    return CONNECTOR_PREFIX + hashlib.sha256(merchant_id.encode("utf-8")).hexdigest()[:24]


def base_url_for(environment: str) -> str:
    from core.billing.pinelabs_client import PLURAL_BASE_URLS

    if environment not in ENVIRONMENTS:
        raise ValueError("environment must be sandbox or production")
    return PLURAL_BASE_URLS[environment].rstrip("/")


@dataclass(frozen=True)
class MerchantPluralCredentials:
    merchant_id: str
    credential_ref: str
    environment: str
    webhook_key: str
    credentials: PluralCredentials = field(repr=False)


def _tenant(tenant_id: Any) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


async def _row(session: Any, tenant: uuid.UUID, credential_ref: str) -> Any:
    from core.models.connector_config import ConnectorConfig

    return (
        await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant,
                ConnectorConfig.company_id.is_(None),
                ConnectorConfig.connector_name == credential_ref,
            )
        )
    ).scalar_one_or_none()


async def _decrypt(row: Any) -> dict[str, Any] | None:
    from core.crypto.tenant_secrets import decrypt_for_tenant

    stored = (row.credentials_encrypted or {}).get("_encrypted") if row is not None else None
    if not isinstance(stored, str) or not stored:
        return None
    try:
        data = json.loads(await asyncio.to_thread(decrypt_for_tenant, stored))
    # enterprise-gate: broad-except-ok reason=unreadable-plural-vault-fails-closed-to-no-payment-link
    except Exception:
        logger.warning("plural_hosted_credentials_unreadable")
        return None
    return data if isinstance(data, dict) else None


def _from_row(row: Any, data: Mapping[str, Any]) -> MerchantPluralCredentials | None:
    config = dict(row.config or {})
    environment = str(config.get("environment") or "")
    client_id = str(data.get("client_id") or "")
    client_secret = str(data.get("client_secret") or "")
    if environment not in ENVIRONMENTS or not client_id or not client_secret:
        return None
    return MerchantPluralCredentials(
        merchant_id=str(config.get("merchant_id") or ""),
        credential_ref=str(row.connector_name),
        environment=environment,
        webhook_key=str(config.get("webhook_key") or ""),
        credentials=PluralCredentials(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url_for(environment),
            webhook_secret=str(data.get("webhook_secret") or ""),
        ),
    )


async def load_credentials(
    session: Any, *, tenant_id: Any, credential_ref: str, merchant_id: str
) -> MerchantPluralCredentials | None:
    """The merchant's credentials under ``credential_ref`` in the tenant; None unless they belong to that merchant."""
    row = await _row(session, _tenant(tenant_id), credential_ref)
    if row is None or row.auth_type != AUTH_TYPE or (row.config or {}).get("merchant_id") != merchant_id:
        return None
    data = await _decrypt(row)
    return _from_row(row, data) if data is not None else None


async def load_by_webhook_key(session: Any, *, tenant_id: Any, webhook_key: str) -> MerchantPluralCredentials | None:
    """The credentials whose webhook URL carries ``webhook_key`` in the tenant (None for an unknown key)."""
    from core.models.connector_config import ConnectorConfig

    if not _WEBHOOK_KEY.match(webhook_key or ""):
        return None
    rows = (
        await session.scalars(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == _tenant(tenant_id),
                ConnectorConfig.company_id.is_(None),
                ConnectorConfig.auth_type == AUTH_TYPE,
            )
        )
    ).all()
    for row in rows:
        stored_key = str((row.config or {}).get("webhook_key") or "")
        if stored_key and secrets.compare_digest(stored_key, webhook_key):
            data = await _decrypt(row)
            return _from_row(row, data) if data is not None else None
    return None


async def save_credentials(
    session: Any,
    *,
    tenant_id: Any,
    merchant_id: str,
    environment: str,
    client_id: str,
    client_secret: str | None,
    webhook_secret: str | None,
) -> Any:
    """Insert or replace the merchant's credentials; a blank secret keeps the stored one. Returns the row."""
    from core.crypto.tenant_secrets import encrypt_for_tenant
    from core.models.connector_config import ConnectorConfig

    tenant = _tenant(tenant_id)
    base_url_for(environment)
    ref = credential_ref_for(merchant_id)
    row = await _row(session, tenant, ref)
    previous = await _decrypt(row) if row is not None else None
    secret = client_secret or (previous or {}).get("client_secret")
    hook_secret = webhook_secret or (previous or {}).get("webhook_secret") or ""
    if not secret:
        raise ValueError("client_secret is required")
    encrypted = await encrypt_for_tenant(
        json.dumps({"client_id": client_id, "client_secret": secret, "webhook_secret": hook_secret}), tenant
    )
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    previous_config = dict(row.config or {}) if row is not None else {}
    config = {
        "merchant_id": merchant_id,
        "environment": environment,
        "webhook_key": previous_config.get("webhook_key") or secrets.token_hex(16),
        "webhook_secret_set": bool(hook_secret),
        "configured_at": now,
        "last_test": previous_config.get("last_test") if previous_config.get("environment") == environment else None,
        "no_payment_collection_by_agenticorg": True,
    }
    if row is None:
        row = ConnectorConfig(
            tenant_id=tenant,
            company_id=None,
            connector_name=ref,
            display_name=f"Plural hosted checkout - {merchant_id}"[:255],
            auth_type=AUTH_TYPE,
            credentials_encrypted={"_encrypted": encrypted},
            config=config,
            status="configured",
            health_status="unknown",
        )
        session.add(row)
    else:
        row.auth_type = AUTH_TYPE
        row.credentials_encrypted = {"_encrypted": encrypted}
        row.config = config
        row.status = "configured"
        row.health_status = "unknown"
    await session.flush()
    return row


def credentials_view(row: Any, *, webhook_url: str | None) -> dict[str, Any]:
    """What the merchant may see: never a secret value, only whether each is set."""
    if row is None:
        return {"configured": False}
    config = dict(row.config or {})
    return {
        "configured": True,
        "credential_ref": row.connector_name,
        "environment": config.get("environment"),
        "webhook_secret_set": bool(config.get("webhook_secret_set")),
        "webhook_url": webhook_url,
        "configured_at": config.get("configured_at"),
        "last_test": config.get("last_test"),
        "secret_values_redacted": True,
    }


async def credentials_row(session: Any, *, tenant_id: Any, merchant_id: str) -> Any:
    row = await _row(session, _tenant(tenant_id), credential_ref_for(merchant_id))
    if row is None or (row.config or {}).get("merchant_id") != merchant_id:
        return None
    return row


async def check_credentials(
    credentials: PluralCredentials, *, transport_factory: TransportFactory | None = None
) -> tuple[bool, str]:
    """Fetch a Plural access token with the credentials: ``(ok, code)``; never returns the token."""
    client = PluralApiClient(credentials, transport_factory=transport_factory or transport_override)
    try:
        await client.check_token()
    except PluralRequestError as exc:
        return False, f"plural_refused:{exc.code}"[:80]
    except PluralUnavailableError:
        return False, "plural_unreachable"
    return True, "token_issued"


# --- hosted orders --------------------------------------------------------------------------------


@dataclass(frozen=True)
class HostedOrder:
    order_id: str
    redirect_url: str


def customer_details(buyer: Mapping[str, Any] | None) -> dict[str, str]:
    """Plural ``purchase_details.customer`` from what the buyer gave the checkout (nothing invented)."""
    buyer = buyer or {}
    out: dict[str, str] = {}
    if isinstance(buyer.get("email"), str) and "@" in buyer["email"]:
        out["email_id"] = buyer["email"][:100]
    if isinstance(buyer.get("first_name"), str) and buyer["first_name"].strip():
        out["first_name"] = buyer["first_name"].strip()[:50]
    if isinstance(buyer.get("last_name"), str) and buyer["last_name"].strip():
        out["last_name"] = buyer["last_name"].strip()[:50]
    phone = re.sub(r"[\s()-]", "", str(buyer.get("phone_number") or ""))
    match = re.fullmatch(r"(?:\+?91)?([6-9][0-9]{9})", phone)
    if match:
        out["mobile_number"] = match.group(1)
        out["country_code"] = "91"
    return out


async def create_hosted_order(
    credentials: PluralCredentials,
    *,
    reference: str,
    amount_minor_units: int,
    currency: str,
    callback_url: str,
    buyer: Mapping[str, Any] | None,
    metadata: Mapping[str, str],
    transport_factory: TransportFactory | None = None,
) -> HostedOrder:
    """Create the hosted-checkout order; raises ``PluralRequestError``/``PluralUnavailableError``.

    Never retried: Plural mints a new payable order per call. The Request-ID is
    derived from the reference, so the provider can recognise a duplicate.
    """
    purchase: dict[str, Any] = {"merchant_metadata": {k: str(v)[:255] for k, v in metadata.items()}}
    customer = customer_details(buyer)
    if customer:
        purchase["customer"] = customer
    body = {
        "merchant_order_reference": reference,
        "order_amount": {"value": int(amount_minor_units), "currency": currency.upper()},
        "pre_auth": False,
        "allowed_payment_methods": list(ALLOWED_METHODS),
        "callback_url": callback_url,
        "failure_callback_url": callback_url,
        "purchase_details": purchase,
    }
    client = PluralApiClient(credentials, transport_factory=transport_factory or transport_override)
    data = await client.call("POST", "/checkout/v1/orders", body, request_id=request_id(reference, "hosted_order"))
    order_id = str(data.get("order_id") or "")
    redirect = str(data.get("redirect_url") or "")
    if not order_id or not redirect.startswith("https://"):
        raise PluralUnavailableError("Plural hosted order answer without order_id or an https redirect_url")
    return HostedOrder(order_id=order_id[:120], redirect_url=redirect[:2048])


async def fetch_order(
    credentials: PluralCredentials, order_id: str, *, transport_factory: TransportFactory | None = None
) -> dict[str, Any]:
    """``GET /pay/v1/orders/{order_id}`` (the order's status, amount and reference)."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", order_id or ""):
        raise PluralUnavailableError("not a Plural order id")
    client = PluralApiClient(credentials, transport_factory=transport_factory or transport_override)
    return await client.call("GET", f"/pay/v1/orders/{order_id}", None, request_id=str(uuid.uuid4()))


@dataclass(frozen=True)
class HostedOutcome:
    """A Plural order's state for one of our hand-offs (``aoh-`` reference)."""

    order_id: str
    reference: str
    status: str
    amount_minor_units: int | None
    currency: str | None


def _amount(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, Mapping):
        amount = value.get("value")
        currency = value.get("currency")
        return (
            amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
            str(currency).upper() if isinstance(currency, str) else None,
        )
    return None, None


def outcome_from_order(order: Mapping[str, Any]) -> HostedOutcome | None:
    """The outcome an order object (webhook ``data`` or the status GET) carries, for our references only."""
    nested = order.get("order") if isinstance(order.get("order"), Mapping) else order
    reference = str(nested.get("merchant_order_reference") or "")
    order_id = str(nested.get("order_id") or "")
    status = str(nested.get("status") or "").upper()
    if not reference.startswith("aoh-") or not order_id or not status:
        return None
    amount, currency = _amount(nested.get("order_amount"))
    return HostedOutcome(
        order_id=order_id[:120],
        reference=reference[:64],
        status=status[:40],
        amount_minor_units=amount,
        currency=currency,
    )


def verify_hosted_webhook(
    raw_body: bytes, headers: Mapping[str, str], *, secret: str, now: float | None = None
) -> PluralWebhookEvent:
    """Signature and timestamp window; raises ``PluralWebhookError`` (``secret_missing`` without a saved secret)."""
    return verify_webhook(raw_body, headers, secret=secret, now=now)


# --- confirmation: webhook, return page and status poll ---------------------------------------------


async def apply_webhook(
    *, tenant_id: str, webhook_key: str, raw_body: bytes, headers: Mapping[str, str], now: float | None = None
) -> dict[str, Any]:
    """Verify a Plural webhook with the merchant's own secret and apply what it proves.

    Raises ``PluralWebhookError`` (``unknown_endpoint`` for a key no merchant
    holds, ``secret_missing``, ``signature_invalid``, ``timestamp_out_of_window``
    and the like). A verified event for an order that is not one of our
    hand-offs is acknowledged and not applied.
    """
    from core.commerce.ucp.handlers import confirm_handoff_payment
    from core.database import get_tenant_session

    async with get_tenant_session(_tenant(tenant_id)) as session:
        credentials = await load_by_webhook_key(session, tenant_id=tenant_id, webhook_key=webhook_key)
    if credentials is None:
        raise PluralWebhookError("unknown_endpoint")
    event = verify_hosted_webhook(raw_body, headers, secret=credentials.credentials.webhook_secret, now=now)
    outcome = outcome_from_order(event.data)
    if outcome is None:
        return {
            "applied": False,
            "result": "not_a_handoff",
            "event_type": event.event_type,
            "body_sha256": event.body_sha256,
        }
    if outcome.amount_minor_units is None:
        # The event names the order but not the amount: read it from Plural before deciding anything.
        try:
            fetched = outcome_from_order(await fetch_order(credentials.credentials, outcome.order_id))
        except (PluralRequestError, PluralUnavailableError):
            fetched = None
        if fetched is None or fetched.reference != outcome.reference:
            return {
                "applied": False,
                "result": "amount_unverified",
                "event_type": event.event_type,
                "body_sha256": event.body_sha256,
            }
        outcome = fetched
    result, order_id = await confirm_handoff_payment(
        tenant_id=str(tenant_id),
        reference=outcome.reference,
        provider_order_id=outcome.order_id,
        status=outcome.status,
        amount_minor_units=outcome.amount_minor_units,
        currency=outcome.currency,
        source="webhook",
        event_id=event.webhook_id,
        now=datetime.now(UTC),
    )
    return {
        "applied": result in ("completed", "link_retired"),
        "result": result,
        "order_id": order_id,
        "event_type": event.event_type,
        "body_sha256": event.body_sha256,
    }


async def refresh_handoff(
    *,
    tenant_id: str,
    session_row: Any,
    source: str,
    transport_factory: TransportFactory | None = None,
    report_untouched: bool = False,
) -> str:
    """Ask Plural for the current hand-off order of one session and apply it (the poll and the return page).

    With ``report_untouched``, an order with no payment in progress answers ``not_attempted``
    instead of ``pending``: nobody has started to pay it (``CREATED``), or the last attempt on it
    failed and it waits for another (``ATTEMPTED``).
    """
    from core.commerce.ucp.handlers import confirm_handoff_payment
    from core.database import get_tenant_session

    state = dict(session_row.state or {})
    handoff = state.get("handoff") if isinstance(state.get("handoff"), Mapping) else {}
    reference = str(handoff.get("reference") or "")
    record = dict((state.get("handoff_orders") or {}).get(reference) or {})
    provider_order = str(record.get("provider_order_id") or handoff.get("provider_order_id") or "")
    credential_ref = str(record.get("credential_ref") or handoff.get("credential_ref") or "")
    if not reference or not provider_order or not credential_ref:
        return "not_applicable"
    async with get_tenant_session(_tenant(tenant_id)) as session:
        credentials = await load_credentials(
            session, tenant_id=tenant_id, credential_ref=credential_ref, merchant_id=session_row.merchant_id
        )
    if credentials is None:
        return "credentials_missing"
    try:
        outcome = outcome_from_order(
            await fetch_order(credentials.credentials, provider_order, transport_factory=transport_factory)
        )
    except (PluralRequestError, PluralUnavailableError) as exc:
        logger.warning("plural_hosted_status_unavailable", error_type=type(exc).__name__)
        return "status_unavailable"
    if outcome is None or outcome.reference != reference or outcome.order_id != provider_order:
        return "status_unusable"
    result, _order_id = await confirm_handoff_payment(
        tenant_id=str(tenant_id),
        reference=reference,
        provider_order_id=provider_order,
        status=outcome.status,
        amount_minor_units=outcome.amount_minor_units,
        currency=outcome.currency,
        source=source,
        event_id=None,
        now=datetime.now(UTC),
    )
    if report_untouched and result == "pending" and outcome.status in NO_PAYMENT_IN_PROGRESS:
        return "not_attempted"
    return result


POLL_BATCH = 50
# Plural order states with no payment in progress: none started yet, or the last attempt failed.
NO_PAYMENT_IN_PROGRESS = frozenset({"CREATED", "ATTEMPTED"})


async def open_handoffs(tenant_id: str, *, limit: int = POLL_BATCH) -> list[Any]:
    """The tenant's checkouts waiting on a Plural payment link (issued, not completed)."""
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    async with get_tenant_session(_tenant(tenant_id)) as session:
        return list(
            (
                await session.scalars(
                    select(Row)
                    .where(
                        Row.tenant_id == _tenant(tenant_id),
                        Row.status == "requires_escalation",
                        Row.state.contains({"handoff": {"mode": "plural_hosted", "status": "issued"}}),
                    )
                    .order_by(Row.updated_at)
                    .limit(limit)
                )
            ).all()
        )


async def poll_open_handoffs(
    *, tenant_ids: list[Any] | None = None, transport_factory: TransportFactory | None = None
) -> dict[str, int]:
    """The webhook fallback: read every open Plural hand-off's order status once and apply it."""
    from core.commerce.ucp.webhooks import _tenants_with_pending

    counts: dict[str, int] = {}
    for tenant in tenant_ids if tenant_ids is not None else await _tenants_with_pending():
        released = await release_stale_reservations(str(tenant))
        if released:
            counts["reservation_released"] = counts.get("reservation_released", 0) + released
        for row in await open_handoffs(str(tenant)):
            result = await refresh_handoff(
                tenant_id=str(tenant), session_row=row, source="poll", transport_factory=transport_factory
            )
            counts[result] = counts.get(result, 0) + 1
    return counts


STALE_RESERVATION = timedelta(minutes=5)


async def release_stale_reservations(tenant_id: str, *, now: datetime | None = None) -> int:
    """Return checkouts whose Plural order creation never finished (a crash mid-Complete) to ``ready_for_complete``.

    The reference stays remembered, so a payment on an order that was created
    after all still finds its session (and is held to the amount it asked for).
    An earlier link that can still be paid comes back (``payment_handoff.retire_failed``),
    and the checkout waits for that payment again.
    """
    from core.commerce import payment_handoff as ph
    from core.commerce.ledger import record_commerce_event
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    moment = now or datetime.now(UTC)
    released = 0
    async with get_tenant_session(_tenant(tenant_id)) as session:
        rows = (
            await session.scalars(
                select(Row)
                .where(
                    Row.tenant_id == _tenant(tenant_id),
                    Row.status == "complete_in_progress",
                    Row.updated_at < moment - STALE_RESERVATION,
                    Row.state.contains({"handoff": {"mode": "plural_hosted", "status": "creating"}}),
                )
                .limit(POLL_BATCH)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            state = dict(row.state or {})
            row.status = ph.retire_failed(state)
            row.state = state
            row.revision = row.revision + 1
            row.updated_at = moment
            released += 1
            await record_commerce_event(
                session,
                tenant_id=str(tenant_id),
                event="commerce.payments.handoff",
                outcome="refused",
                merchant_id=row.merchant_id,
                seller_agent_id=row.seller_agent_id,
                buyer_agent_id=None,
                payload={"resource_id": row.id, "result": "reservation_released"},
                resource_id=row.id,
                details={"result": "reservation_released"},
            )
    return released
