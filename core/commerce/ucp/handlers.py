# SPDX-License-Identifier: Apache-2.0
"""Transport-neutral UCP operations: checkout, cart and order.

Every operation takes a ``UcpRequest`` (the authenticated tenant, the request
``Host``, ``UCP-Agent``, ``Idempotency-Key`` and the raw body) and returns a
``UcpResponse`` (HTTP status, JSON body, and the ledger outcome). The REST
binding (``api/v1/commerce_ucp.py``) calls these; an MCP binding calls the
same functions through ``OPERATIONS`` (UCP tool names ``create_checkout``,
``get_checkout``, ``update_checkout``, ``complete_checkout``,
``cancel_checkout``, ``create_cart``, ``get_cart``, ``update_cart``,
``cancel_cart``, ``get_order``).

Order of checks: merchant (Host inside the tenant, else 404), negotiation
(``UCP-Agent``; discovery and version errors are 4xx, an incompatible
capability set is a 200 error body), idempotency (writes), then the
operation. Every write records its ledger event in the transaction of its
business write; an answer with no business write (a refusal, a replay) is
recorded by the binding in its own transaction.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.commerce import executors
from core.commerce.arbiter import decide
from core.commerce.arbiter_store import DecisionRefusedError, persist_decision
from core.commerce.ledger import commerce_payload_hash, record_commerce_event
from core.commerce.ucp import idempotency
from core.commerce.ucp import messages as msg
from core.commerce.ucp import releases as v
from core.commerce.ucp.capabilities import DEFAULT_REGISTRY, Registry, RegistryContext
from core.commerce.ucp.checkout import FROZEN, TERMINAL, EngineDeps, RequestError, evaluate, status_for
from core.commerce.ucp.completion import (
    CHECKOUT_MANDATE,
    VENDOR_MEMBER,
    AuthorityRequestError,
    ap2_mandate_evidence,
    arbiter_context,
    completion_payload,
    parse_buyer_authority,
    vendor_member,
)
from core.commerce.ucp.fulfillment import RatesSource
from core.commerce.ucp.httpsig import MerchantSigner, SigningKeyUnavailableError, detached_jws
from core.commerce.ucp.items import ItemCatalog
from core.commerce.ucp.merchant import MerchantUcp, ServingHostResolver
from core.commerce.ucp.negotiation import Negotiated, NegotiationError, ProfileFetcher, negotiate
from core.commerce.ucp.orders import (
    ORDER_CREATED,
    OrderEvent,
    build_order_snapshot,
    emit_order_event,
    new_order_id,
)
from core.commerce.ucp.payment import Instrument, PaymentRequestError, completion_messages, parse_payment, token_binding
from core.commerce.ucp.renderers import SessionView, render_cart, render_checkout, render_order
from core.commerce.ucp.shopify_reads import ShopifyReads
from core.commerce.ucp.versions import configured_ucp_versions

logger = structlog.get_logger()

CART_TTL = timedelta(days=7)

EVENTS = {
    "create_checkout": "commerce.ucp.checkout.create",
    "update_checkout": "commerce.ucp.checkout.update",
    "complete_checkout": "commerce.ucp.checkout.complete",
    "cancel_checkout": "commerce.ucp.checkout.cancel",
    "create_cart": "commerce.ucp.cart.create",
    "update_cart": "commerce.ucp.cart.update",
    "cancel_cart": "commerce.ucp.cart.cancel",
}
EXECUTION_EVENT = "commerce.ucp.checkout.execution"
ORDER_CREATED_EVENT = "commerce.ucp.order.created"
RISK_SIGNALS_EVENT = "commerce.ucp.payment.risk_signals"
ORDER_UPDATE_EVENT = "commerce.ucp.order.update"
# The ledger events of the checkout operations, keyed as in EVENTS plus the three follow-on events;
# a binding for another protocol (ACP) records its own names (UcpRuntime.events).
CHECKOUT_EVENTS: dict[str, str] = {
    **{op: name for op, name in EVENTS.items() if op.endswith("_checkout")},
    "execution": EXECUTION_EVENT,
    "order_created": ORDER_CREATED_EVENT,
    "risk_signals": RISK_SIGNALS_EVENT,
}


@dataclass
class UcpRequest:
    tenant_id: str
    host: str
    ucp_agent: str | None
    raw_body: bytes = b""
    idempotency_key: str | None = None
    request_id: str | None = None
    # Set by a binding from server-side context, never from the body: a pre-assigned checkout id (the ACP
    # binding asks Grantex for the session's delegated-payment allowance before the row exists) and the
    # platform that owns the session (ACP: the API key; its sessions answer to that platform only).
    session_id: str | None = None
    platform: str | None = None

    def json(self) -> Any:
        if not self.raw_body:
            return {}
        try:
            return json.loads(self.raw_body)
        except ValueError as exc:
            raise RequestError("the body is not JSON") from exc


@dataclass
class UcpResponse:
    status: int
    body: dict[str, Any]
    outcome: str = "success"
    recorded: bool = False
    merchant_id: str | None = None
    seller_agent_id: str | None = None
    resource_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


CheckoutRenderer = Callable[..., Awaitable[dict[str, Any]]]
BodyTranslator = Callable[[Mapping[str, Any] | None, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]
CompletionCheck = Callable[[Mapping[str, Any], int, str | None, datetime], list[dict[str, Any]]]


@dataclass
class UcpRuntime:
    """Injectable dependencies; the REST binding builds the production one."""

    resolver: ServingHostResolver
    catalog_for: Callable[[MerchantUcp], ItemCatalog]
    rates_for: Callable[[MerchantUcp], RatesSource | None] = lambda _m: None
    shopify_reads_for: Callable[[MerchantUcp], Awaitable[ShopifyReads | None]] | None = None
    arbiter_scope: Callable[[MerchantUcp], Awaitable[tuple[Sequence[Any], Mapping[str, Any] | None]]] | None = None
    signer: Callable[[], MerchantSigner | None] = lambda: None
    registry: Registry = DEFAULT_REGISTRY
    fetcher: ProfileFetcher | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    after_commit: Callable[[uuid.UUID], None] | None = None
    # None: the releases AGENTICORG_UCP_VERSIONS enables.
    supported_versions: tuple[str, ...] | None = None
    # Buyer authority for a completion whose request carries none (the mandate work supplies
    # server-verified mandates here). None in production today: the request must carry it.
    # policies[] for checkout, cart and order responses (production: the verified OACP policy artifact).
    policies_for: Callable[[MerchantUcp], Awaitable[Sequence[Mapping[str, Any]]]] | None = None
    # The capability names the merchant's profile advertises for a release (production: the profile registry).
    published: Callable[[MerchantUcp, str], set[str]] | None = None
    authority_provider: Callable[[MerchantUcp, Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]] | None = None
    # The protocol whose sessions this runtime serves. Non-UCP sessions are tagged state["protocol"]; a
    # session of another protocol is not found. The ACP binding (core.commerce.acp) sets the four below.
    protocol: str = "ucp"
    events: Mapping[str, str] = field(default_factory=lambda: dict(CHECKOUT_EVENTS))
    # Renders a checkout session in the protocol's shape (default: the UCP renderers).
    checkout_renderer: CheckoutRenderer | None = None
    # (previous state or None, request body) -> (full-replacement UCP body, state members to keep); runs
    # under the session lock on update, so a partial update merges with the state it replaces.
    body_translator: BodyTranslator | None = None
    # Extra completion checks (state, total, currency, now) -> blocking messages, run before any decision.
    completion_check: CompletionCheck | None = None


class _AbortError(Exception):
    def __init__(self, response: UcpResponse) -> None:
        super().__init__(response.status)
        self.response = response


def _protocol_error(status: int, code: str, content: str, version: str = v.V2026_04_08) -> UcpResponse:
    return UcpResponse(status=status, body=msg.error_response(version, code, content), outcome="refused")


def _discovery_error(exc: NegotiationError, version: str) -> UcpResponse:
    if exc.status == 200:
        return UcpResponse(status=200, body=msg.error_response(version, exc.code, exc.content), outcome="refused")
    return UcpResponse(status=exc.status, body={"code": exc.code, "content": exc.content}, outcome="refused")


async def _merchant(runtime: UcpRuntime, req: UcpRequest) -> MerchantUcp:
    merchant = await runtime.resolver.resolve(host=req.host, tenant_id=req.tenant_id)
    if merchant is None:
        raise _AbortError(_protocol_error(404, "not_found", "No merchant is served on this host."))
    return merchant


def _registry_context(runtime: UcpRuntime, merchant: MerchantUcp) -> RegistryContext:
    return RegistryContext(merchant_signing_available=merchant.signing_available and runtime.signer() is not None)


async def _negotiate(runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, resource: str) -> Negotiated:
    try:
        return await negotiate(
            ucp_agent=req.ucp_agent,
            merchant=merchant,
            registry=runtime.registry,
            context=_registry_context(runtime, merchant),
            resource=resource,
            fetcher=runtime.fetcher,
            supported_versions=runtime.supported_versions,
            published=(lambda version: runtime.published(merchant, version)) if runtime.published else None,
        )
    except NegotiationError as exc:
        versions = runtime.supported_versions or configured_ucp_versions()
        raise _AbortError(_discovery_error(exc, versions[0])) from exc


def _deps(runtime: UcpRuntime, merchant: MerchantUcp, reads: ShopifyReads | None) -> EngineDeps:
    return EngineDeps(
        catalog=runtime.catalog_for(merchant), rates_source=runtime.rates_for(merchant), shopify_reads=reads
    )


async def _reads(runtime: UcpRuntime, merchant: MerchantUcp, negotiated: Negotiated) -> ShopifyReads | None:
    if runtime.shopify_reads_for is None or not negotiated.has(v.LOYALTY):
        return None
    return await runtime.shopify_reads_for(merchant)


def _tenant(req: UcpRequest) -> uuid.UUID:
    return uuid.UUID(str(req.tenant_id))


def _object_body(req: UcpRequest) -> dict[str, Any]:
    body = req.json()
    if not isinstance(body, dict):
        raise RequestError("the body must be a JSON object")
    return body


async def run(operation: str, runtime: UcpRuntime, req: UcpRequest, resource_id: str | None = None) -> UcpResponse:
    """Run one operation with merchant resolution, negotiation, idempotency and error mapping."""
    merchant: MerchantUcp | None = None
    claimed = False
    try:
        merchant = await _merchant(runtime, req)
        resource = "order" if operation == "get_order" else ("cart" if operation.endswith("_cart") else "checkout")
        negotiated = await _negotiate(runtime, req, merchant, resource)
        is_write = operation in EVENTS
        if is_write and req.idempotency_key:
            if not idempotency.valid_key(req.idempotency_key):
                raise _AbortError(_protocol_error(400, "invalid_request", "Idempotency-Key is not a valid key."))
            try:
                outcome = await idempotency.claim(
                    tenant_id=req.tenant_id,
                    merchant_id=merchant.merchant_id,
                    key=req.idempotency_key,
                    operation=f"{operation}:{resource_id or ''}",
                    raw_body=req.raw_body,
                )
            except idempotency.IdempotencyUnavailableError as exc:
                raise _AbortError(
                    _protocol_error(503, "idempotency_unavailable", "Idempotency records are unavailable; retry later.")
                ) from exc
            if outcome.replay is not None:
                status, body = outcome.replay
                return _with_merchant(UcpResponse(status=status, body=body, outcome="success"), merchant, resource_id)
            if outcome.conflict is not None:
                content = (
                    "This Idempotency-Key was used with a different request."
                    if outcome.conflict == "idempotency_key_reused"
                    else "A request with this Idempotency-Key is still in progress."
                )
                raise _AbortError(_protocol_error(409, outcome.conflict, content, negotiated.version))
            claimed = True
        response = await run_operation(operation, runtime, req, merchant, negotiated, resource_id)
    except _AbortError as abort:
        response = abort.response
    response = _with_merchant(response, merchant, resource_id)
    if claimed and merchant is not None and req.idempotency_key:
        await idempotency.store(
            tenant_id=req.tenant_id,
            merchant_id=merchant.merchant_id,
            key=req.idempotency_key,
            status=response.status,
            body=response.body,
        )
    return response


async def run_operation(
    operation: str,
    runtime: UcpRuntime,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    resource_id: str | None = None,
) -> UcpResponse:
    """One operation for an already resolved merchant and negotiated capability set (no idempotency).

    A binding that resolves, negotiates and claims idempotency its own way (ACP) calls this; protocol
    errors come back as UCP error responses, exactly as from ``run``.
    """
    try:
        return await OPERATIONS[operation](runtime, req, merchant, negotiated, resource_id)
    except _AbortError as abort:
        return abort.response
    except (RequestError, AuthorityRequestError, PaymentRequestError) as exc:
        return _protocol_error(400, "invalid_request", str(exc))


def _with_merchant(response: UcpResponse, merchant: MerchantUcp | None, resource_id: str | None) -> UcpResponse:
    if merchant is not None:
        response.merchant_id = response.merchant_id or merchant.merchant_id
        response.seller_agent_id = response.seller_agent_id or merchant.seller_agent_id
    response.resource_id = response.resource_id or resource_id
    return response


async def _record(
    session: Any,
    req: UcpRequest,
    merchant: MerchantUcp,
    event: str,
    outcome: str,
    resource_id: str,
    details: Mapping[str, Any],
    payload: Any = None,
) -> None:
    await record_commerce_event(
        session,
        tenant_id=req.tenant_id,
        event=event,
        outcome=outcome,
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        buyer_agent_id=None,
        payload=payload if payload is not None else {"resource_id": resource_id, **dict(details)},
        resource_id=resource_id,
        details=dict(details),
    )


# --- checkout ---------------------------------------------------------------------------------


def _session_view(row: Any, now: datetime, *, order_permalink: str | None = None) -> SessionView:
    status = row.status
    if status not in TERMINAL and row.expires_at <= now:
        status = "canceled"
    return SessionView(
        id=row.id,
        status=status,
        expires_at=row.expires_at,
        continue_url=(row.state or {}).get("continue_url") if status == "requires_escalation" else None,
        order_id=row.order_id,
        order_permalink_url=order_permalink,
    )


async def _policies(runtime: UcpRuntime, merchant: MerchantUcp, negotiated: Negotiated) -> list[dict[str, Any]]:
    """The merchant's ``policies[]`` (2026-08-25 only; none without a usable verified policy artifact)."""
    if negotiated.version < v.V2026_08_25 or runtime.policies_for is None:
        return []
    return list(await runtime.policies_for(merchant))


async def _render_checkout(
    runtime: UcpRuntime,
    row: Any,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    now: datetime,
    *,
    extra_messages: Sequence[Mapping[str, Any]] = (),
    vendor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if runtime.checkout_renderer is not None:
        return await runtime.checkout_renderer(
            row=row, merchant=merchant, negotiated=negotiated, now=now, extra_messages=extra_messages, vendor=vendor
        )
    state = dict(row.state or {})
    permalink = state.get("order_permalink_url")
    handlers = runtime.registry.payment_handlers(merchant, negotiated.version)
    body = render_checkout(
        policies=await _policies(runtime, merchant, negotiated),
        session=_session_view(row, now, order_permalink=permalink),
        state=state,
        negotiated=negotiated,
        merchant=merchant,
        handlers=handlers,
        extra_messages=extra_messages,
    )
    if vendor:
        body[VENDOR_MEMBER] = dict(vendor)
    if negotiated.has(v.ap2_mandate_name(negotiated.version)):
        signer = runtime.signer()
        if signer is not None:
            try:
                body["ap2"] = {
                    "merchant_authorization": await detached_jws(
                        body,
                        signer=signer,
                        tenant_id=merchant.tenant_id,
                        merchant_id=merchant.merchant_id,
                        seller_agent_id=merchant.seller_agent_id,
                    )
                }
            except SigningKeyUnavailableError:
                logger.warning("ucp_ap2_merchant_key_unavailable")
    return body


async def _load_session(
    session: Any, req: UcpRequest, merchant: MerchantUcp, session_id: str, *, lock: bool, protocol: str = "ucp"
) -> Any:
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    query = select(Row).where(
        Row.tenant_id == _tenant(req), Row.merchant_id == merchant.merchant_id, Row.id == session_id
    )
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if (
        row is None
        or (row.state or {}).get("protocol", "ucp") != protocol
        or (req.platform is not None and row.platform_profile_url != req.platform)
    ):
        raise _AbortError(_protocol_error(404, "not_found", "Checkout session not found."))
    return row


def _frozen_error(row: Any, now: datetime, version: str, operation: str) -> UcpResponse | None:
    status = row.status
    if status not in TERMINAL and row.expires_at <= now:
        status = "canceled"
    if status in FROZEN:
        return _protocol_error(409, "invalid_state", f"The checkout is {status}; it cannot be {operation}.", version)
    return None


def _tag(state: dict[str, Any], runtime: UcpRuntime, kept: Mapping[str, Any]) -> None:
    """Tag a non-UCP session with its protocol and keep the binding's own state members."""
    if runtime.protocol != "ucp":
        state["protocol"] = runtime.protocol
    state.update(kept)


async def create_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, _id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCartRow, CommerceCheckoutSessionRow

    body = _object_body(req)
    kept: dict[str, Any] = {}
    if runtime.body_translator is not None:
        body, kept = runtime.body_translator(None, body)
    now = runtime.clock()
    vendor = vendor_member(body)
    cart_id = body.get("cart_id") if negotiated.has(v.CART) else None
    if cart_id is not None and not isinstance(cart_id, str):
        raise RequestError("cart_id must be a string")
    reads = await _reads(runtime, merchant, negotiated)
    async with get_tenant_session(_tenant(req)) as session:
        if cart_id:
            cart = (
                await session.execute(
                    select(CommerceCartRow).where(
                        CommerceCartRow.tenant_id == _tenant(req),
                        CommerceCartRow.merchant_id == merchant.merchant_id,
                        CommerceCartRow.id == cart_id,
                    )
                )
            ).scalar_one_or_none()
            if cart is None or cart.status != "active":
                return _protocol_error(404, "not_found", "Cart not found.", negotiated.version)
            existing = (
                await session.execute(
                    select(CommerceCheckoutSessionRow).where(
                        CommerceCheckoutSessionRow.tenant_id == _tenant(req),
                        CommerceCheckoutSessionRow.merchant_id == merchant.merchant_id,
                        CommerceCheckoutSessionRow.cart_id == cart_id,
                        CommerceCheckoutSessionRow.status.not_in(("completed", "canceled")),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.expires_at > now:
                await _record(
                    session,
                    req,
                    merchant,
                    runtime.events["create_checkout"],
                    "success",
                    existing.id,
                    {"cart_id": cart_id, "converted": "existing"},
                )
                rendered = await _render_checkout(runtime, existing, merchant, negotiated, now)
                return UcpResponse(status=200, body=rendered, recorded=True, resource_id=existing.id)
            if existing is not None:
                # An expired open session for the cart is canceled so the cart can be checked out again.
                existing.status = "canceled"
                existing.canceled_at = now
                existing.revision = existing.revision + 1
                existing.updated_at = now
                await session.flush()
            body = _cart_as_checkout_body(cart.state, body)
        evaluation = await evaluate(
            resource="checkout",
            merchant=merchant,
            negotiated=negotiated,
            body=body,
            previous=None,
            deps=_deps(runtime, merchant, reads),
            now=now,
        )
        session_id = req.session_id or f"chk_{uuid.uuid4().hex}"
        state = evaluation.state
        state["buyer_authority"] = vendor.get("buyer_authority")
        state["platform"] = {
            "profile_url": negotiated.platform.url,
            "order_webhook_url": negotiated.platform.order_webhook_url(negotiated.version),
        }
        _tag(state, runtime, kept)
        row = CommerceCheckoutSessionRow(
            id=session_id,
            tenant_id=_tenant(req),
            merchant_id=merchant.merchant_id,
            seller_agent_id=merchant.seller_agent_id,
            serving_host=merchant.serving_host,
            ucp_version=negotiated.version,
            status=evaluation.status,
            currency=evaluation.currency,
            platform_profile_url=negotiated.platform.url[:2048],
            cart_id=cart_id,
            state=state,
            revision=1,
            expires_at=now + timedelta(seconds=merchant.checkout_ttl_seconds),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            raise _AbortError(
                _protocol_error(409, "invalid_state", "This cart is already being checked out.", negotiated.version)
            ) from None
        if cart_id:
            await session.execute(
                CommerceCartRow.__table__.update()
                .where(CommerceCartRow.tenant_id == _tenant(req), CommerceCartRow.id == cart_id)
                .values(checkout_session_id=session_id, updated_at=now)
            )
        await _record(
            session,
            req,
            merchant,
            runtime.events["create_checkout"],
            "success",
            session_id,
            {"status": row.status, "cart_id": cart_id, "ucp_version": negotiated.version},
        )
        rendered = await _render_checkout(runtime, row, merchant, negotiated, now)
    return UcpResponse(status=201, body=rendered, recorded=True, resource_id=session_id)


def _cart_as_checkout_body(cart_state: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """Cart contents (line items, buyer, context) replace the overlapping checkout fields."""
    out = {k: val for k, val in body.items() if k not in ("line_items", "buyer", "context", "cart_id")}
    out["line_items"] = [
        {"item": {"id": li["item"]["id"]}, "quantity": li["quantity"]} for li in cart_state.get("line_items") or []
    ]
    if cart_state.get("buyer"):
        out["buyer"] = dict(cart_state["buyer"])
    if cart_state.get("context"):
        out["context"] = dict(cart_state["context"])
    return out


async def get_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, session_id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session

    now = runtime.clock()
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, str(session_id), lock=False, protocol=runtime.protocol)
        rendered = await _render_checkout(runtime, row, merchant, negotiated, now)
    return UcpResponse(status=200, body=rendered)


async def update_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, session_id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session

    body = _object_body(req)
    if body.get("id") not in (None, session_id):
        raise RequestError("id in the body does not match the path")
    now = runtime.clock()
    reads = await _reads(runtime, merchant, negotiated)
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, str(session_id), lock=True, protocol=runtime.protocol)
        frozen = _frozen_error(row, now, negotiated.version, "updated")
        if frozen is not None:
            return frozen
        kept: dict[str, Any] = {}
        if runtime.body_translator is not None:
            body, kept = runtime.body_translator(row.state or {}, body)
        vendor = vendor_member(body)
        evaluation = await evaluate(
            resource="checkout",
            merchant=merchant,
            negotiated=negotiated,
            body=body,
            previous=row.state,
            deps=_deps(runtime, merchant, reads),
            now=now,
        )
        state = evaluation.state
        state["buyer_authority"] = vendor.get("buyer_authority") or (row.state or {}).get("buyer_authority")
        state["platform"] = (row.state or {}).get("platform")
        _tag(state, runtime, kept)
        row.state = state
        row.status = evaluation.status
        row.currency = evaluation.currency
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session,
            req,
            merchant,
            runtime.events["update_checkout"],
            "success",
            row.id,
            {"status": row.status, "revision": row.revision},
        )
        rendered = await _render_checkout(runtime, row, merchant, negotiated, now)
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)


async def cancel_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, session_id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session

    now = runtime.clock()
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, str(session_id), lock=True, protocol=runtime.protocol)
        if row.status in TERMINAL or row.status == "complete_in_progress":
            return _protocol_error(
                409, "invalid_state", f"The checkout is {row.status}; it cannot be canceled.", negotiated.version
            )
        row.status = "canceled"
        row.canceled_at = now
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session, req, merchant, runtime.events["cancel_checkout"], "success", row.id, {"status": "canceled"}
        )
        rendered = await _render_checkout(runtime, row, merchant, negotiated, now)
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)


async def complete_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, session_id: str | None
) -> UcpResponse:
    """Complete: validate payment, need an executor, decide or consume an allow decision, then execute."""
    from core.database import get_tenant_session

    body = _object_body(req)
    vendor = vendor_member(body)
    instruments, _term = parse_payment(body.get("payment"))
    now = runtime.clock()
    version = negotiated.version
    event = runtime.events["complete_checkout"]
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, str(session_id), lock=True, protocol=runtime.protocol)
        frozen = _frozen_error(row, now, version, "completed")
        if frozen is not None:
            return frozen
        state = dict(row.state or {})

        async def _blocked(messages: list[dict[str, Any]], outcome: str = "blocked", **details: Any) -> UcpResponse:
            await _record(session, req, merchant, event, outcome, row.id, {"reason": messages[0]["code"], **details})
            rendered = await _render_checkout(
                runtime, row, merchant, negotiated, now, extra_messages=messages, vendor=details.get("vendor")
            )
            return UcpResponse(status=200, body=rendered, outcome=outcome, recorded=True, resource_id=row.id)

        if row.status != "ready_for_complete":
            return await _blocked(
                [msg.error("missing", "The checkout is not ready to complete; see its messages.", path="$.status")]
            )
        split_active = negotiated.has(v.SPLIT_PAYMENTS)
        total = int(next(t["amount"] for t in state["totals"] if t["type"] == "total"))
        payment_messages = completion_messages(
            instruments,
            merchant=merchant,
            version=version,
            split_active=split_active,
            total=total,
            currency=row.currency,
        )
        if payment_messages:
            return await _blocked(payment_messages)
        if runtime.completion_check is not None:
            extra_blockers = runtime.completion_check(state, total, row.currency, now)
            if extra_blockers:
                return await _blocked(extra_blockers)
        authority = dict(vendor.get("buyer_authority") or state.get("buyer_authority") or {})
        if not authority and runtime.authority_provider is not None:
            authority = dict(await runtime.authority_provider(merchant, state) or {})
        ap2_name = v.ap2_mandate_name(version)
        if negotiated.has(ap2_name):
            ap2 = body.get("ap2") if isinstance(body.get("ap2"), Mapping) else {}
            mandate = ap2.get("checkout_mandate")
            if not isinstance(mandate, str) or not CHECKOUT_MANDATE.match(mandate):
                return await _blocked(
                    [
                        msg.error(
                            "mandate_required", "An AP2 checkout mandate is required.", path="$.ap2.checkout_mandate"
                        )
                    ]
                )
            authority.update(ap2_mandate_evidence(mandate))
        executor = executors.get_executor(merchant.executor)
        if executor is None:
            return await _blocked(
                [
                    msg.error(
                        "executor_unavailable",
                        "This merchant cannot complete checkouts here yet.",
                        severity="unrecoverable",
                    )
                ]
            )
        stored_instruments = [i.stored() for i in instruments]
        payload = completion_payload(
            session_id=row.id,
            revision=row.revision,
            merchant=merchant,
            version=version,
            state=state,
            instruments=stored_instruments,
        )
        payload_hash = commerce_payload_hash(payload)
        decision_id = vendor.get("decision_id")
        decision_public = None
        if not decision_id:
            cache_records, merchant_config = (
                await runtime.arbiter_scope(merchant) if runtime.arbiter_scope is not None else ((), None)
            )
            decision = decide(
                "session_complete",
                arbiter_context(
                    merchant=merchant,
                    now=now,
                    payload=payload,
                    amount_minor_units=total,
                    currency=row.currency,
                    quantity=sum(int(li["quantity"]) for li in state["line_items"]),
                    authority=authority,
                    cache_records=cache_records,
                    merchant_config=merchant_config,
                ),
            )
            await persist_decision(session, decision)
            decision_public = decision.to_public()
            if not decision.allowed:
                return await _blocked(
                    [msg.error("arbiter_refused", f"The commerce arbiter refused: {decision.failing_rule}.", path="$")],
                    outcome="refused",
                    vendor={"decision": decision_public},
                )
            if vendor.get("decide_only"):
                return await _blocked(
                    [
                        msg.info(
                            "A sealed allow decision was issued; complete with its decision_id to execute.",
                            code="decision_issued",
                        )
                    ],
                    outcome="allow",
                    vendor={"decision": decision_public},
                )
            decision_id = decision.decision_id
        try:
            consumed = await executors.consume_decision(
                session,
                tenant_id=req.tenant_id,
                merchant_id=merchant.merchant_id,
                seller_agent_id=merchant.seller_agent_id,
                decision_id=decision_id,
                payload_hash=payload_hash,
            )
        except DecisionRefusedError as exc:
            return await _blocked(
                [msg.error(exc.code, exc.message, path="$")],
                outcome="refused",
                vendor={"decision": decision_public} if decision_public else None,
            )
        if body.get("signals") is not None:
            from core.commerce.ucp.checkout import signal_summary

            signals = signal_summary(body.get("signals"))
            await _record(
                session,
                req,
                merchant,
                runtime.events["risk_signals"],
                "success",
                row.id,
                {
                    "signal_keys": (signals or {}).get("keys"),
                    "signals_sha256": (signals or {}).get("sha256"),
                    "handler_ids": sorted({i.handler_id for i in instruments}),
                },
            )
        row.status = "complete_in_progress"
        row.decision_id = consumed.decision_id
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session,
            req,
            merchant,
            event,
            "success",
            row.id,
            {"decision_id": consumed.decision_id, "payload_hash": payload_hash, "executor": executor.name},
            payload=payload,
        )
        checkout = executors.ExecutionCheckout(
            session_id=row.id,
            tenant_id=req.tenant_id,
            merchant_id=merchant.merchant_id,
            seller_agent_id=merchant.seller_agent_id,
            ucp_version=version,
            currency=str(row.currency),
            total_minor_units=total,
            line_items=[dict(li) for li in state["line_items"]],
            instruments=[
                executors.ExecutionInstrument(
                    id=i.id, handler_id=i.handler_id, type=i.type, credential=i.credential, amount_minor_units=i.amount
                )
                for i in instruments
            ],
            token_binding=token_binding(version, row.id),
            payload_hash=payload_hash,
        )
    try:
        result = await executor.execute(checkout, consumed)
    # enterprise-gate: broad-except-ok reason=executor-failure-returns-the-session-to-ready-nothing-was-charged-by-us
    except Exception as exc:
        logger.error("ucp_executor_failed", executor=executor.name, error_type=type(exc).__name__)
        result = executors.ExecutionResult(
            status="failed", error_code="payment_failed", error_message="The payment could not be processed."
        )
    return await _apply_execution(
        runtime, req, merchant, negotiated, str(session_id), instruments, result, consumed.decision_id, executor.name
    )


async def _apply_execution(
    runtime: UcpRuntime,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    session_id: str,
    instruments: Sequence[Instrument],
    result: executors.ExecutionResult,
    decision_id: str,
    executor_name: str,
) -> UcpResponse:
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    now = runtime.clock()
    version = negotiated.version
    extra: list[dict[str, Any]] = []
    order_event: OrderEvent | None = None
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=True, protocol=runtime.protocol)
        state = dict(row.state or {})
        if result.status == "completed":
            order_id = new_order_id()
            accepted = None
            selected_term = (state.get("payment") or {}).get("selected_term_id")
            if selected_term:
                accepted = next((t for t in state.get("payment_terms") or [] if t["id"] == selected_term), None)
            order_policies = await _policies(runtime, merchant, negotiated)
            snapshot = build_order_snapshot(
                order_id=order_id, checkout_id=row.id, merchant=merchant, state=state, accepted_term=accepted
            )
            if order_policies:
                snapshot["policies"] = order_policies
            session.add(
                CommerceOrderRow(
                    id=order_id,
                    tenant_id=_tenant(req),
                    merchant_id=merchant.merchant_id,
                    seller_agent_id=merchant.seller_agent_id,
                    checkout_session_id=row.id,
                    serving_host=merchant.serving_host,
                    ucp_version=version,
                    platform_profile_url=row.platform_profile_url,
                    currency=str(row.currency),
                    total_minor_units=int(next(t["amount"] for t in state["totals"] if t["type"] == "total")),
                    snapshot=snapshot,
                    decision_id=decision_id,
                    executor=executor_name,
                    provider_order_ref=(result.provider_order_ref or None),
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            state["order_permalink_url"] = snapshot["permalink_url"]
            state["payment"] = {**(state.get("payment") or {}), "instruments": [i.stored() for i in instruments]}
            state.pop("actions", None)
            row.state = state
            row.status = "completed"
            row.order_id = order_id
            row.completed_at = now
            order_event = OrderEvent(
                event_type=ORDER_CREATED,
                tenant_id=req.tenant_id,
                merchant_id=merchant.merchant_id,
                order_id=order_id,
                revision=1,
                snapshot=snapshot,
                ucp_version=version,
            )
            await _record(
                session,
                req,
                merchant,
                runtime.events["order_created"],
                "success",
                order_id,
                {"checkout_id": row.id, "decision_id": decision_id},
                payload=snapshot,
            )
            webhook_url = (state.get("platform") or {}).get("order_webhook_url")
            from core.commerce.ucp.webhooks import enqueue_order_deliveries, ensure_platform_subscription

            if webhook_url and row.platform_profile_url:
                await ensure_platform_subscription(
                    session,
                    tenant_id=_tenant(req),
                    merchant_id=merchant.merchant_id,
                    webhook_url=webhook_url,
                    platform_profile_url=row.platform_profile_url,
                    now=now,
                )
            await enqueue_order_deliveries(session, order_event, platform_profile_url=row.platform_profile_url, now=now)
            await emit_order_event(session, order_event)
        elif result.status == "action_required":
            if negotiated.has(v.PAYMENT_AUTHENTICATION) and result.actions:
                state["actions"] = _validated_actions(result.actions, merchant)
                row.status = "complete_in_progress" if state["actions"] else "ready_for_complete"
                if not state["actions"]:
                    extra.append(msg.error("payment_failed", "The payment authentication step was not usable."))
            elif result.escalation_url and result.escalation_url.startswith("https://"):
                state["continue_url"] = result.escalation_url
                state["messages"] = list(state.get("messages") or []) + [
                    msg.error(
                        "requires_3ds",
                        "The buyer must authenticate this payment.",
                        severity="requires_buyer_input",
                        path="$.payment",
                    )
                ]
                row.status = status_for(state["messages"], state["continue_url"])
            else:
                row.status = "ready_for_complete"
                extra.append(msg.error("payment_failed", "The payment needs a buyer step this platform cannot show."))
            row.state = state
        else:
            row.status = "ready_for_complete"
            extra.append(
                msg.error(
                    result.error_code or "payment_failed",
                    result.error_message or "The payment was declined.",
                    path="$.payment",
                )
            )
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session,
            req,
            merchant,
            runtime.events["execution"],
            "success"
            if result.status == "completed"
            else ("blocked" if result.status == "action_required" else "refused"),
            row.id,
            {
                "result": result.status,
                "decision_id": decision_id,
                "executor": executor_name,
                "provider_order_ref_present": bool(result.provider_order_ref),
            },
        )
        rendered = await _render_checkout(runtime, row, merchant, negotiated, now, extra_messages=extra)
    if order_event is not None and runtime.after_commit is not None:
        runtime.after_commit(_tenant(req))
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=session_id)


def _validated_actions(actions: Mapping[str, Sequence[Mapping[str, Any]]], merchant: MerchantUcp) -> dict[str, Any]:
    """Payment authentication actions: known types, https ``config.url`` on an allowed origin, no userinfo."""
    from urllib.parse import urlsplit

    allowed = {str(o).rstrip("/") for o in (merchant.payment_authentication or {}).get("allowed_origins") or []}
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in (v.ACTION_DEVICE_DATA_COLLECTION, v.ACTION_THREE_DS_CHALLENGE):
        for instance in actions.get(kind) or []:
            config = instance.get("config") or {}
            url = str(config.get("url") or "")
            parts = urlsplit(url)
            origin = f"{parts.scheme}://{parts.netloc}"
            if parts.scheme != "https" or "@" in parts.netloc or origin not in allowed:
                continue
            if not instance.get("id") or not config.get("payment_instrument_id"):
                continue
            out.setdefault(kind, []).append(
                {
                    "id": str(instance["id"]),
                    "config": {"payment_instrument_id": str(config["payment_instrument_id"]), "url": url},
                }
            )
    return out


# --- cart -------------------------------------------------------------------------------------


async def _load_cart(session: Any, req: UcpRequest, merchant: MerchantUcp, cart_id: str, *, lock: bool) -> Any:
    from core.models.commerce_ucp import CommerceCartRow as Row

    query = select(Row).where(Row.tenant_id == _tenant(req), Row.merchant_id == merchant.merchant_id, Row.id == cart_id)
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).scalar_one_or_none()
    if row is None or row.status != "active" or row.expires_at <= datetime.now(UTC):
        raise _AbortError(_protocol_error(404, "not_found", "Cart not found."))
    return row


async def create_cart(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, _id: str | None
) -> UcpResponse:
    """``POST /carts``: the single call a platform makes at cart transfer; ``continue_url`` hands the buyer over."""
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCartRow

    body = _object_body(req)
    now = runtime.clock()
    reads = await _reads(runtime, merchant, negotiated)
    evaluation = await evaluate(
        resource="cart",
        merchant=merchant,
        negotiated=negotiated,
        body=body,
        previous=None,
        deps=_deps(runtime, merchant, reads),
        now=now,
    )
    cart_id = f"cart_{uuid.uuid4().hex}"
    async with get_tenant_session(_tenant(req)) as session:
        row = CommerceCartRow(
            id=cart_id,
            tenant_id=_tenant(req),
            merchant_id=merchant.merchant_id,
            seller_agent_id=merchant.seller_agent_id,
            serving_host=merchant.serving_host,
            ucp_version=negotiated.version,
            status="active",
            currency=evaluation.currency,
            platform_profile_url=negotiated.platform.url[:2048],
            state=evaluation.state,
            revision=1,
            expires_at=now + CART_TTL,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await _record(
            session,
            req,
            merchant,
            EVENTS["create_cart"],
            "success",
            cart_id,
            {"line_items": len(evaluation.state["line_items"])},
        )
    body_out = render_cart(
        policies=await _policies(runtime, merchant, negotiated),
        cart_id=cart_id,
        state=evaluation.state,
        negotiated=negotiated,
        merchant=merchant,
        expires_at=row.expires_at,
    )
    return UcpResponse(status=201, body=body_out, recorded=True, resource_id=cart_id)


async def get_cart(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, cart_id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session

    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_cart(session, req, merchant, str(cart_id), lock=False)
    return UcpResponse(
        status=200,
        body=render_cart(
            policies=await _policies(runtime, merchant, negotiated),
            cart_id=row.id,
            state=row.state,
            negotiated=negotiated,
            merchant=merchant,
            expires_at=row.expires_at,
        ),
    )


async def update_cart(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, cart_id: str | None
) -> UcpResponse:
    """``PUT /carts/{id}``: full replacement; the body's ``id`` is omitted (2026-08-25) and must match when sent."""
    from core.database import get_tenant_session

    body = _object_body(req)
    if body.get("id") not in (None, cart_id):
        raise RequestError("id in the body does not match the path")
    now = runtime.clock()
    reads = await _reads(runtime, merchant, negotiated)
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_cart(session, req, merchant, str(cart_id), lock=True)
        evaluation = await evaluate(
            resource="cart",
            merchant=merchant,
            negotiated=negotiated,
            body=body,
            previous=row.state,
            deps=_deps(runtime, merchant, reads),
            now=now,
        )
        row.state = evaluation.state
        row.currency = evaluation.currency
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(session, req, merchant, EVENTS["update_cart"], "success", row.id, {"revision": row.revision})
        rendered = render_cart(
            policies=await _policies(runtime, merchant, negotiated),
            cart_id=row.id,
            state=row.state,
            negotiated=negotiated,
            merchant=merchant,
            expires_at=row.expires_at,
        )
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)


async def cancel_cart(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, cart_id: str | None
) -> UcpResponse:
    from core.database import get_tenant_session

    now = runtime.clock()
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_cart(session, req, merchant, str(cart_id), lock=True)
        row.status = "canceled"
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(session, req, merchant, EVENTS["cancel_cart"], "success", row.id, {"status": "canceled"})
        rendered = render_cart(
            policies=await _policies(runtime, merchant, negotiated),
            cart_id=row.id,
            state=row.state,
            negotiated=negotiated,
            merchant=merchant,
            expires_at=row.expires_at,
        )
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)


# --- order ------------------------------------------------------------------------------------


async def get_order(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, order_id: str | None
) -> UcpResponse:
    """``GET /orders/{id}``: the full snapshot, to the platform that originated the order only."""
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    async with get_tenant_session(_tenant(req)) as session:
        row = (
            await session.execute(
                select(CommerceOrderRow).where(
                    CommerceOrderRow.tenant_id == _tenant(req),
                    CommerceOrderRow.merchant_id == merchant.merchant_id,
                    CommerceOrderRow.id == str(order_id),
                )
            )
        ).scalar_one_or_none()
    if row is None or (row.platform_profile_url and row.platform_profile_url != negotiated.platform.url):
        return _protocol_error(404, "not_found", "Order not found.", negotiated.version)
    body = render_order(
        snapshot=row.snapshot, version=negotiated.version, capabilities=negotiated.response_capabilities("order")
    )
    return UcpResponse(status=200, body=body)


class OrderChangeError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def apply_order_change(
    *,
    tenant_id: str,
    order_id: str,
    fulfillment_event: Mapping[str, Any] | None,
    adjustment: Mapping[str, Any] | None,
    now: datetime,
) -> tuple[int, dict[str, Any], str]:
    """Record a fulfillment event or an adjustment on an order and queue the full snapshot to every subscriber.

    Returns ``(revision, snapshot, ucp_version)``; raises ``OrderChangeError`` (404 unknown order, 422 a change
    that does not apply). The ledger event, the outbox rows and the order event listeners share one transaction.
    """
    from core.commerce.ucp.orders import ORDER_UPDATED, OrderUpdateError, apply_adjustment, apply_fulfillment_event
    from core.commerce.ucp.webhooks import enqueue_order_deliveries
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceOrderRow

    if (fulfillment_event is None) == (adjustment is None):
        raise OrderChangeError(422, "Send exactly one of fulfillment_event or adjustment")
    tenant = uuid.UUID(str(tenant_id))
    async with get_tenant_session(tenant) as session:
        row = (
            await session.execute(
                select(CommerceOrderRow)
                .where(CommerceOrderRow.tenant_id == tenant, CommerceOrderRow.id == order_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise OrderChangeError(404, "Order not found")
        snapshot = json.loads(json.dumps(row.snapshot))
        try:
            if fulfillment_event is not None:
                entry = apply_fulfillment_event(snapshot, fulfillment_event, now=now)
            else:
                entry = apply_adjustment(snapshot, adjustment or {}, now=now)
        except OrderUpdateError as exc:
            raise OrderChangeError(422, str(exc)) from exc
        row.snapshot = snapshot
        row.revision = row.revision + 1
        row.updated_at = now
        event = OrderEvent(
            event_type=ORDER_UPDATED,
            tenant_id=str(tenant_id),
            merchant_id=row.merchant_id,
            order_id=row.id,
            revision=row.revision,
            snapshot=snapshot,
            ucp_version=row.ucp_version,
        )
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event=ORDER_UPDATE_EVENT,
            outcome="success",
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=None,
            payload=snapshot,
            resource_id=row.id,
            details={"revision": row.revision, "entry_id": entry["id"]},
        )
        await enqueue_order_deliveries(session, event, platform_profile_url=row.platform_profile_url, now=now)
        await emit_order_event(session, event)
        return row.revision, snapshot, row.ucp_version


Operation = Callable[[UcpRuntime, UcpRequest, MerchantUcp, Negotiated, str | None], Awaitable[UcpResponse]]

OPERATIONS: dict[str, Operation] = {
    "create_checkout": create_checkout,
    "get_checkout": get_checkout,
    "update_checkout": update_checkout,
    "complete_checkout": complete_checkout,
    "cancel_checkout": cancel_checkout,
    "create_cart": create_cart,
    "get_cart": get_cart,
    "update_cart": update_cart,
    "cancel_cart": cancel_cart,
    "get_order": get_order,
}

__all__ = ["OPERATIONS", "UcpRequest", "UcpResponse", "UcpRuntime", "parse_buyer_authority", "run", "run_operation"]
