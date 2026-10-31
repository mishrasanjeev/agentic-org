# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
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

import dataclasses
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
from core.commerce.arbiter import RULE_AFA, AfaEvidence, Decision, decide
from core.commerce.arbiter_store import DecisionRefusedError, persist_decision
from core.commerce.ledger import commerce_payload_hash, record_commerce_event
from core.commerce.oacp_mandates import VerifiedMandateChain
from core.commerce.payment_handoff import PaymentSettings, payment_settings
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
    mandate_checkout,
    parse_buyer_authority,
    platform_buyer_agent_id,
    session_buyer_agent_id,
    token_binding_for,
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
    # Set by a binding that may only hand the buyer a payment link (the network gateway): Complete refuses
    # unless the merchant configured a payment hand-off, and never runs an executor.
    handoff_only: bool = False
    # The buyer surface a binding serves (``telegram``, ``mcp``), recorded on the session at create time.
    surface: str | None = None

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
    # policies[] for checkout, cart and order responses (production: the verified OACP policy artifact).
    policies_for: Callable[[MerchantUcp], Awaitable[Sequence[Mapping[str, Any]]]] | None = None
    # The capability names the merchant's profile advertises for a release (production: the profile registry).
    published: Callable[[MerchantUcp, str], set[str]] | None = None
    # Caller-asserted buyer authority for a completion whose request carries none (conformance
    # harness only; None in production). Server-verified mandates come from mandate_request.
    authority_provider: Callable[[MerchantUcp, Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]] | None = None
    # The Grantex service client for mandates and countersignatures (production: from the
    # environment, GRANTEX_COMMERCE_BASE_URL and GRANTEX_COMMERCE_INTERNAL_TOKEN).
    grantex_client: Callable[[], Any] | None = None
    # Whether mandate_request is honoured (production: AGENTICORG_GRANTEX_MANDATES_ENABLED).
    mandates_enabled: Callable[[], bool] | None = None
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
    vendor_out = dict(vendor or {})
    if merchant.grantex_mandates is not None and row.status == "ready_for_complete":
        # What the cart mandate and the buyer's passkey assertion bind (challenge = checkout_hash).
        from core.commerce.oacp_mandates import checkout_hash

        try:
            checkout = mandate_checkout(
                session_id=row.id,
                revision=int(row.revision),
                state=state,
                grantex_merchant_id=merchant.grantex_mandates.merchant_id,
            )
        except (KeyError, StopIteration, TypeError):
            checkout = None
        if checkout is not None:
            vendor_out["mandate_checkout"] = {"checkout": checkout, "checkout_hash": checkout_hash(checkout)}
    if vendor_out:
        body[VENDOR_MEMBER] = vendor_out
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
            # The buyer agent every arbiter decision of this session names (Grantex countersigns to it).
            "buyer_agent_id": platform_buyer_agent_id(negotiated.platform.url),
        }
        if req.surface:
            state["platform"]["surface"] = req.surface
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
        if _payment_link_outstanding(row):
            return _protocol_error(
                409,
                "invalid_state",
                "A payment link was issued for this checkout; cancel it and open a new checkout to change it.",
                negotiated.version,
            )
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
        for member in ("handoff_refs", "handoff_orders", "handoff_events"):
            # A payment on an earlier hand-off link must still find its session (and is checked against it).
            if (row.state or {}).get(member):
                state[member] = (row.state or {})[member]
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


MANDATES_EVENT = "commerce.ucp.checkout.mandates"


@dataclass(frozen=True)
class _MandateOutcome:
    """What became of a ``mandate_request``: ``verified`` (with the chain), ``refused`` or ``unavailable``."""

    status: str
    code: str | None
    chain: VerifiedMandateChain | None = None
    checkout_hash: str | None = None
    revision: int | None = None

    def to_vendor(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "code": self.code}
        if self.chain is not None:
            out.update(
                {"jtis": dict(self.chain.jtis), "hashes": dict(self.chain.hashes), "presence": self.chain.presence}
            )
        return out


def _grantex_client(runtime: UcpRuntime) -> Any:
    if runtime.grantex_client is not None:
        return runtime.grantex_client()
    from core.commerce.grantex_service import grantex_service_client_from_env

    return grantex_service_client_from_env()


def _mandates_enabled(runtime: UcpRuntime) -> bool:
    if runtime.mandates_enabled is not None:
        return bool(runtime.mandates_enabled())
    from core.config import settings

    return bool(getattr(settings, "grantex_mandates_enabled", False))


async def _request_mandates(
    runtime: UcpRuntime,
    req: UcpRequest,
    merchant: MerchantUcp,
    session_id: str,
    request: Mapping[str, Any] | None,
    instruments: Sequence[Instrument],
) -> _MandateOutcome | None:
    """Ask Grantex for the Intent, Cart and Payment mandates of this checkout and verify them locally.

    Runs before the decision's transaction (no HTTP inside one). Every failure
    leaves the authority unverified, so the arbiter refuses it in strict
    runtimes; nothing waits and nothing is retried.
    """
    from core.commerce.mandates_client import (
        MandateChainRequest,
        MandateRequestError,
        MandateRequestRefusedError,
        MandateServiceUnavailableError,
        request_mandate_chain,
    )
    from core.commerce.oacp_mandates import MandateExpectation, checkout_hash, trust_sources, verify_mandate_chain
    from core.database import get_tenant_session

    if request is None:
        return None
    settings = merchant.grantex_mandates
    if not _mandates_enabled(runtime):
        return _MandateOutcome("unavailable", "mandates_disabled")
    if settings is None:
        return _MandateOutcome("unavailable", "grantex_mandates_not_configured")
    if len(instruments) != 1:
        return _MandateOutcome("refused", "mandate_single_instrument_required")
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=False, protocol=runtime.protocol)
        state = dict(row.state or {})
        revision = int(row.revision)
        currency = str(row.currency or "").upper()
        ready = row.status == "ready_for_complete"
    if not ready:
        return None
    checkout = mandate_checkout(
        session_id=session_id, revision=revision, state=state, grantex_merchant_id=settings.merchant_id
    )
    digest = checkout_hash(checkout)
    total = int(checkout["totals"][0]["amount"])
    client = _grantex_client(runtime)
    if client is None:
        return _MandateOutcome("unavailable", "grantex_not_configured")
    cap = request.get("max_amount_minor_units")
    try:
        issued = await request_mandate_chain(
            MandateChainRequest(
                tenant_id=settings.tenant_id,
                passport_jwt=str(request["passport_jwt"]),
                description=str(request["description"]),
                max_amount_minor_units=int(cap) if isinstance(cap, int) else total,
                currency=currency,
                presence=str(request["presence"]),
                checkout=checkout,
                rail=settings.rail,
                token_binding=token_binding_for(instruments[0].credential_sha256, settings.token_type),
                webauthn_assertion=request.get("webauthn_assertion"),
            ),
            client=client,
        )
    except MandateRequestRefusedError as exc:
        return _MandateOutcome("refused", exc.code, checkout_hash=digest, revision=revision)
    except MandateServiceUnavailableError:
        return _MandateOutcome("unavailable", "grantex_unreachable", checkout_hash=digest, revision=revision)
    except MandateRequestError:
        return _MandateOutcome("refused", "mandate_request_invalid", checkout_hash=digest, revision=revision)
    now = runtime.clock()
    jwks, status_reader, local = trust_sources(client.verifier, now)
    chain = await verify_mandate_chain(
        {kind: item.mandate for kind, item in issued.items()},
        issuer=client.issuer,
        jwks=jwks,
        now=now,
        expected=MandateExpectation(
            tenant_id=settings.tenant_id,
            merchant_id=settings.merchant_id,
            checkout_hash=digest,
            amount_minor_units=total,
            currency=currency,
        ),
        status_reader=status_reader,
        allow_local_endpoints=local,
    )
    if not chain.valid:
        logger.warning("ucp_mandate_chain_refused", code=chain.refusal_code, failed_type=chain.failed_type)
    return _MandateOutcome(
        "verified" if chain.valid else "refused",
        None if chain.valid else chain.refusal_code,
        chain=chain,
        checkout_hash=digest,
        revision=revision,
    )


def _bound_mandates(
    outcome: _MandateOutcome | None, row: Any, state: Mapping[str, Any], merchant: MerchantUcp
) -> _MandateOutcome | None:
    """The outcome, refused when the session changed between the mandate request and the decision."""
    from core.commerce.oacp_mandates import checkout_hash

    if outcome is None or outcome.status != "verified" or merchant.grantex_mandates is None:
        return outcome
    current = checkout_hash(
        mandate_checkout(
            session_id=row.id,
            revision=int(row.revision),
            state=state,
            grantex_merchant_id=merchant.grantex_mandates.merchant_id,
        )
    )
    if row.revision != outcome.revision or current != outcome.checkout_hash:
        return _MandateOutcome("refused", "mandate_binding_mismatch", chain=None, revision=outcome.revision)
    return outcome


async def _blocked_response(
    runtime: UcpRuntime,
    session: Any,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    row: Any,
    now: datetime,
    messages: list[dict[str, Any]],
    outcome: str = "blocked",
    **details: Any,
) -> UcpResponse:
    await _record(
        session,
        req,
        merchant,
        runtime.events["complete_checkout"],
        outcome,
        row.id,
        {"reason": messages[0]["code"], **details},
    )
    rendered = await _render_checkout(
        runtime, row, merchant, negotiated, now, extra_messages=messages, vendor=details.get("vendor")
    )
    return UcpResponse(status=200, body=rendered, outcome=outcome, recorded=True, resource_id=row.id)


async def complete_checkout(
    runtime: UcpRuntime, req: UcpRequest, merchant: MerchantUcp, negotiated: Negotiated, session_id: str | None
) -> UcpResponse:
    """Complete: validate payment, need an executor, decide (with verified mandates when requested), then execute.

    A fresh decision is sealed in its own transaction; after that commit it is
    offered to Grantex for countersignature (``countersign_decision``, which
    never waits), and the allow is consumed in a second transaction, so a
    strict runtime that requires the Grantex grant can consume it. A decision
    presented by id (``decide_only`` earlier) is consumed directly.
    """
    from core.commerce.arbiter_grantex import countersign_decision
    from core.config import arbiter_require_verified_authority_enabled
    from core.database import get_tenant_session

    body = _object_body(req)
    handoff_settings = payment_settings(merchant.raw_config)
    if handoff_settings is not None or req.handoff_only:
        # The merchant takes payment on its own page: hand the buyer a link (core.commerce.payment_handoff).
        return await _complete_with_handoff(runtime, req, merchant, negotiated, str(session_id), handoff_settings)
    vendor = vendor_member(body)
    instruments, _term = parse_payment(body.get("payment"))
    version = negotiated.version
    mandates = await _request_mandates(
        runtime, req, merchant, str(session_id), vendor.get("mandate_request"), instruments
    )
    now = runtime.clock()
    decided: Decision | None = None
    decided_revision: int | None = None
    deferred: UcpResponse | None = None
    prepared: Any = None
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, str(session_id), lock=True, protocol=runtime.protocol)
        frozen = _frozen_error(row, now, version, "completed")
        if frozen is not None:
            return frozen
        state = dict(row.state or {})

        async def _blocked(messages: list[dict[str, Any]], outcome: str = "blocked", **details: Any) -> UcpResponse:
            return await _blocked_response(
                runtime, session, req, merchant, negotiated, row, now, messages, outcome, **details
            )

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
        mandates = _bound_mandates(mandates, row, state, merchant)
        if mandates is not None:
            await _record(
                session,
                req,
                merchant,
                runtime.events.get("mandates", MANDATES_EVENT),
                "success" if mandates.status == "verified" else "refused",
                row.id,
                mandates.to_vendor(),
                payload={"checkout_session_id": row.id, **mandates.to_vendor()},
            )
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
        afa_input, afa_step = await _afa_scope(session, req, merchant, row, instruments, total)
        mandate_vendor = mandates.to_vendor() if mandates is not None else None
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
                    afa=afa_input,
                    buyer_agent_id=session_buyer_agent_id(row),
                    verified_mandates=mandates.chain if mandates is not None else None,
                    require_verified_authority=arbiter_require_verified_authority_enabled(),
                ),
            )
            await persist_decision(session, decision)
            decided, decided_revision = decision, int(row.revision)
            decision_public = decision.to_public()
            vendor_out: dict[str, Any] = {"decision": decision_public}
            if mandate_vendor is not None:
                vendor_out["mandates"] = mandate_vendor
            if decision.failing_rule == RULE_AFA and afa_step is not None:
                deferred = await _return_afa_step(
                    runtime, session, req, merchant, negotiated, row, state, afa_step, now, decision_public
                )
            elif not decision.allowed:
                deferred = await _blocked(
                    [msg.error("arbiter_refused", f"The commerce arbiter refused: {decision.failing_rule}.", path="$")],
                    outcome="refused",
                    vendor=vendor_out,
                )
            elif vendor.get("decide_only"):
                deferred = await _blocked(
                    [
                        msg.info(
                            "A sealed allow decision was issued; complete with its decision_id to execute.",
                            code="decision_issued",
                        )
                    ],
                    outcome="allow",
                    vendor=vendor_out,
                )
        else:
            prepared = await _consume_and_prepare(
                session,
                runtime,
                req,
                merchant,
                negotiated,
                row,
                state,
                now,
                body=body,
                instruments=instruments,
                decision_id=str(decision_id),
                payload=payload,
                payload_hash=payload_hash,
                total=total,
                executor=executor,
                afa_input=afa_input,
                afa_step=afa_step,
                vendor_out=None,
            )
    if decided is not None:
        # After the decision's commit, outside any transaction: never waits, never retries.
        client = runtime.grantex_client() if runtime.grantex_client is not None else None
        countersignature = await countersign_decision(decided, client=client)
        if deferred is not None:
            vendor_block = deferred.body.get(VENDOR_MEMBER)
            if isinstance(vendor_block, dict):
                vendor_block["grantex_countersignature"] = countersignature.to_dict()
            return deferred
        async with get_tenant_session(_tenant(req)) as session:
            row = await _load_session(session, req, merchant, str(session_id), lock=True, protocol=runtime.protocol)
            state = dict(row.state or {})
            # A concurrent Complete that consumed its own decision first moved the session on.
            frozen = _frozen_error(row, now, version, "completed")
            if frozen is not None:
                return frozen
            if row.status != "ready_for_complete" or row.revision != decided_revision:
                return await _blocked_response(
                    runtime,
                    session,
                    req,
                    merchant,
                    negotiated,
                    row,
                    now,
                    [msg.error("decision_payload_mismatch", "The checkout changed after the decision.", path="$")],
                    outcome="refused",
                )
            prepared = await _consume_and_prepare(
                session,
                runtime,
                req,
                merchant,
                negotiated,
                row,
                state,
                now,
                body=body,
                instruments=instruments,
                decision_id=decided.decision_id,
                payload=payload,
                payload_hash=payload_hash,
                total=total,
                executor=executor,
                afa_input=afa_input,
                afa_step=afa_step,
                vendor_out={
                    "decision": decided.to_public(),
                    "grantex_countersignature": countersignature.to_dict(),
                    **({"mandates": mandate_vendor} if mandate_vendor is not None else {}),
                },
            )
    if isinstance(prepared, UcpResponse):
        return prepared
    assert prepared is not None
    checkout, consumed, executor = prepared
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


async def _consume_and_prepare(
    session: Any,
    runtime: UcpRuntime,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    row: Any,
    state: dict[str, Any],
    now: datetime,
    *,
    body: Mapping[str, Any],
    instruments: Sequence[Instrument],
    decision_id: str,
    payload: Mapping[str, Any],
    payload_hash: str,
    total: int,
    executor: Any,
    afa_input: AfaEvidence | None,
    afa_step: Any,
    vendor_out: Mapping[str, Any] | None,
) -> tuple[executors.ExecutionCheckout, executors.ConsumedDecision, Any] | UcpResponse:
    """Consume the allow decision for this exact payload and move the session to ``complete_in_progress``."""
    event = runtime.events["complete_checkout"]
    version = negotiated.version
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
        return await _blocked_response(
            runtime,
            session,
            req,
            merchant,
            negotiated,
            row,
            now,
            [msg.error(exc.code, exc.message, path="$")],
            outcome="refused",
            vendor=dict(vendor_out) if vendor_out else None,
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
    # What a later verified provider event needs to finish this execution (no credential).
    state["execution"] = {
        "decision_id": consumed.decision_id,
        "executor": executor.name,
        "instruments": [i.stored() for i in instruments],
    }
    row.state = state
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
        allowance_max_minor_units=merchant.mandate_capability.max_amount_minor_units
        if merchant.mandate_capability
        else None,
        allowance_currency=merchant.mandate_capability.currency if merchant.mandate_capability else None,
        upi_mandate_reference=afa_step.reference if afa_step is not None else None,
        afa_evidence_ref=afa_input.evidence_ref if afa_input is not None else None,
    )
    return checkout, consumed, executor


@dataclass(frozen=True)
class _AfaStep:
    """The RBI AFA step a UPI AutoPay instrument needs (``core.commerce.india_rails``)."""

    reference: str
    instrument_id: str
    terms: Any
    url: str


async def _afa_scope(
    session: Any, req: UcpRequest, merchant: MerchantUcp, row: Any, instruments: Sequence[Instrument], total: int
) -> tuple[AfaEvidence | None, _AfaStep | None]:
    """The arbiter's AFA input for a UPI AutoPay instrument and the step to return when evidence is missing.

    Evidence is read from ``commerce_afa_evidence`` (written only from a
    verified provider event), never from the request. Terms that cannot be
    built (wrong currency, above the mandate limit) leave the input empty, so
    ``afa_evidence_present`` refuses with ``afa_input_missing``.
    """
    from core.commerce import india_rails
    from core.commerce.payment_records import afa_evidence_ref

    upi = [i for i in instruments if i.type == india_rails.INSTRUMENT_UPI_AUTOPAY]
    capability = merchant.mandate_capability.upi_autopay if merchant.mandate_capability else None
    if not upi or capability is None:
        return None, None
    instrument = upi[0]
    reference = india_rails.mandate_reference(str(req.tenant_id), str(row.id), instrument.id)
    started = row.created_at if isinstance(row.created_at, datetime) else datetime.now(UTC)
    try:
        terms = india_rails.mandate_terms(
            capability, amount_minor_units=total, currency=row.currency, start=started.date()
        )
    except india_rails.UpiMandateError:
        return None, None
    evidence = await afa_evidence_ref(
        session, tenant_id=str(req.tenant_id), reference=reference, terms_sha256=terms.sha256
    )
    thresholds = india_rails.afa_thresholds_from_settings()
    afa = AfaEvidence(
        debit_kind="registration",
        amount_minor_units=total,
        currency=row.currency,
        purpose=capability.purpose,
        limit_minor_units=thresholds.limit_minor_units,
        higher_limit_minor_units=thresholds.higher_limit_minor_units,
        evidence_ref=evidence,
    )
    step = _AfaStep(
        reference=reference,
        instrument_id=instrument.id,
        terms=terms,
        url=india_rails.authorization_url(capability, reference),
    )
    return afa, step


async def _return_afa_step(
    runtime: UcpRuntime,
    session: Any,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    row: Any,
    state: dict[str, Any],
    step: _AfaStep,
    now: datetime,
    decision_public: Mapping[str, Any],
) -> UcpResponse:
    """The buyer's out-of-band AFA step: an ``actions[]`` entry when the extension is active, else escalation.

    Nothing waits: the session leaves ``ready_for_complete`` (``incomplete``
    with the Action, or ``requires_escalation`` with ``continue_url``) and
    returns to it when verified AFA evidence is recorded; the next Complete
    gets a fresh decision.
    """
    from core.commerce import india_rails
    from core.commerce.payment_records import record_afa_requested

    await record_afa_requested(
        session,
        tenant_id=str(req.tenant_id),
        merchant_id=merchant.merchant_id,
        seller_agent_id=merchant.seller_agent_id,
        checkout_session_id=row.id,
        instrument_id=step.instrument_id,
        reference=step.reference,
        terms=step.terms.to_dict(),
        terms_sha256=step.terms.sha256,
    )
    messages = [m for m in state.get("messages") or [] if m.get("code") != india_rails.AFA_ERROR_CODE]
    state["afa_pending"] = {
        "mandate_reference": step.reference,
        "instrument_id": step.instrument_id,
        "terms_sha256": step.terms.sha256,
    }
    if negotiated.has(india_rails.UCP_UPI_MANDATE_EXTENSION):
        actions = dict(state.get("actions") or {})
        actions[india_rails.UCP_AFA_ACTION] = [
            india_rails.ucp_afa_action(
                reference=step.reference, instrument_id=step.instrument_id, terms=step.terms, url=step.url
            )
        ]
        state["actions"] = actions
        messages.append(india_rails.ucp_afa_blocking_message())
        state["messages"] = messages
        row.status = "incomplete"
    else:
        state["continue_url"] = step.url
        messages.append(india_rails.ucp_afa_escalation_message())
        state["messages"] = messages
        row.status = status_for(messages, step.url)
    row.state = state
    row.revision = row.revision + 1
    row.updated_at = now
    await _record(
        session,
        req,
        merchant,
        runtime.events["complete_checkout"],
        "refused",
        row.id,
        {"reason": india_rails.AFA_ERROR_CODE, "mandate_reference": step.reference},
    )
    rendered = await _render_checkout(runtime, row, merchant, negotiated, now, vendor={"decision": decision_public})
    return UcpResponse(status=200, body=rendered, outcome="refused", recorded=True, resource_id=row.id)


async def _complete_into_order(
    session: Any,
    req: UcpRequest,
    merchant: MerchantUcp,
    row: Any,
    state: dict[str, Any],
    *,
    version: str,
    instruments: Sequence[Mapping[str, Any]],
    decision_id: str,
    executor_name: str,
    provider_order_ref: str | None,
    policies: Sequence[Mapping[str, Any]],
    now: datetime,
    order_created_event: str = ORDER_CREATED_EVENT,
) -> OrderEvent:
    """Create the order for a session whose payment the provider completed, in ``session``; returns its event."""
    from core.models.commerce_ucp import CommerceOrderRow

    order_id = new_order_id()
    accepted = None
    selected_term = (state.get("payment") or {}).get("selected_term_id")
    if selected_term:
        accepted = next((t for t in state.get("payment_terms") or [] if t["id"] == selected_term), None)
    snapshot = build_order_snapshot(
        order_id=order_id, checkout_id=row.id, merchant=merchant, state=state, accepted_term=accepted
    )
    if policies:
        snapshot["policies"] = list(policies)
    capability = merchant.mandate_capability
    if capability is not None:
        # The handler allowance the payment was captured under; refunds are bounded by it too.
        snapshot["payment_allowance"] = {
            "max_amount_minor_units": capability.max_amount_minor_units,
            "currency": capability.currency,
        }
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
            provider_order_ref=(provider_order_ref or None),
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    state["order_permalink_url"] = snapshot["permalink_url"]
    state["payment"] = {**(state.get("payment") or {}), "instruments": list(instruments)}
    state.pop("actions", None)
    state.pop("execution", None)
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
        order_created_event,
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
    return order_event


RECONCILED_EVENT = "commerce.ucp.checkout.reconciled"


async def reconcile_execution(
    *,
    tenant_id: str,
    session_id: str,
    decision_id: str,
    provider_order_ref: str | None,
    outcome: str,
    now: datetime,
) -> str:
    """Apply a verified provider outcome to a session whose execution is still open; idempotent.

    ``outcome`` ``captured``: the order is created (as Complete would have) if
    the session, executed under ``decision_id``, is not completed yet.
    ``failed``: the session returns to ``ready_for_complete`` with a payment
    message (the decision stays consumed; a new Complete needs a new decision).
    Returns what happened: ``completed``, ``already_completed``,
    ``returned_to_ready``, ``not_applicable`` or ``unknown_session``.
    """
    from core.commerce.ucp.merchant import config_from_row, merchant_ucp_from_config
    from core.commerce.ucp.store import load_merchant_config_row
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    order_event: OrderEvent | None = None
    req = UcpRequest(tenant_id=str(tenant_id), host="", ucp_agent=None)
    async with get_tenant_session(_tenant(req)) as session:
        scope = (
            await session.execute(
                select(Row.merchant_id, Row.seller_agent_id).where(Row.tenant_id == _tenant(req), Row.id == session_id)
            )
        ).first()
    if scope is None:
        return "unknown_session"
    config_row = await load_merchant_config_row(
        tenant_id=str(tenant_id), merchant_id=scope.merchant_id, seller_agent_id=scope.seller_agent_id
    )
    async with get_tenant_session(_tenant(req)) as session:
        row = (
            await session.execute(
                select(Row).where(Row.tenant_id == _tenant(req), Row.id == session_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or config_row is None:
            return "unknown_session"
        merchant = merchant_ucp_from_config(
            config_from_row(config_row), tenant_id=str(tenant_id), serving_host=row.serving_host
        )
        if merchant is None:
            return "unknown_session"
        if row.status == "completed":
            return "already_completed"
        if row.decision_id != decision_id or row.status == "canceled":
            return "not_applicable"
        state = dict(row.state or {})
        execution = state.get("execution") if isinstance(state.get("execution"), Mapping) else {}
        if outcome == "captured":
            order_event = await _complete_into_order(
                session,
                req,
                merchant,
                row,
                state,
                version=row.ucp_version,
                instruments=list(execution.get("instruments") or []),
                decision_id=decision_id,
                executor_name=str(execution.get("executor") or merchant.executor or ""),
                provider_order_ref=provider_order_ref,
                policies=(),
                now=now,
            )
            result = "completed"
        elif outcome == "failed":
            state.pop("actions", None)
            state.pop("execution", None)
            if row.status == "requires_escalation":
                state.pop("continue_url", None)
            state["messages"] = [m for m in state.get("messages") or [] if m.get("code") != "requires_3ds"]
            row.state = state
            row.status = "ready_for_complete"
            result = "returned_to_ready"
        else:
            return "not_applicable"
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session,
            req,
            merchant,
            RECONCILED_EVENT,
            "success" if result == "completed" else "refused",
            row.id,
            {"result": result, "decision_id": decision_id, "provider_order_ref_present": bool(provider_order_ref)},
        )
    if order_event is not None:
        from core.commerce.ucp.webhooks import kick_dispatch

        kick_dispatch(_tenant(req))
    return result


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

    now = runtime.clock()
    version = negotiated.version
    extra: list[dict[str, Any]] = []
    order_event: OrderEvent | None = None
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=True, protocol=runtime.protocol)
        state = dict(row.state or {})
        if result.status == "completed":
            order_event = await _complete_into_order(
                session,
                req,
                merchant,
                row,
                state,
                version=version,
                instruments=[i.stored() for i in instruments],
                decision_id=decision_id,
                executor_name=executor_name,
                provider_order_ref=result.provider_order_ref,
                policies=await _policies(runtime, merchant, negotiated),
                now=now,
                order_created_event=runtime.events["order_created"],
            )
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


# --- payment hand-off -----------------------------------------------------------------------------

HANDOFF_EVENT = "commerce.payments.handoff"
HANDOFF_CONFIRM_EVENT = "commerce.payments.handoff.confirm"


def _payment_link_outstanding(row: Any) -> bool:
    """A payment link is out for the checkout as it stands: changing the checkout would orphan the payment.

    A Plural order exists while its link is being created or is issued, and an earlier Plural link
    stays payable until Plural reports it paid or failed (``payment_handoff.payable``). A Shopify
    cart link stays payable for good (Shopify never reports the payment back), so a checkout that
    handed one out keeps its items, and the daily maximum keeps counting the link for the day it
    was issued.
    """
    from core.commerce import payment_handoff as ph

    state = row.state or {}
    handoff: Mapping[str, Any] = state["handoff"] if isinstance(state.get("handoff"), Mapping) else {}
    if handoff.get("mode") == ph.MODE_SHOPIFY_CART:
        return handoff.get("status") == "issued" and row.status == "requires_escalation"
    if (
        handoff.get("mode") == ph.MODE_PLURAL_HOSTED
        and handoff.get("status") in ("creating", "issued")
        and row.status in ("requires_escalation", "complete_in_progress")
    ):
        return True
    orders = state.get("handoff_orders")
    return isinstance(orders, Mapping) and any(ph.payable(record) for record in orders.values())


@dataclass(frozen=True)
class _HandoffTarget:
    """Where the buyer will pay, resolved before the decision's transaction (reads only, no provider call)."""

    problem: str | None = None
    message: str = ""
    shop_domain: str | None = None
    plural: Any = None


async def _handoff_target(req: UcpRequest, merchant: MerchantUcp, settings: PaymentSettings) -> _HandoffTarget:
    from core.commerce import payment_handoff as ph
    from core.commerce.ucp.store import load_merchant_config_row
    from core.database import get_tenant_session

    if settings.mode == ph.MODE_SHOPIFY_CART:
        row = await load_merchant_config_row(
            tenant_id=str(req.tenant_id), merchant_id=merchant.merchant_id, seller_agent_id=merchant.seller_agent_id
        )
        domain = ph.shopify_shop_domain(getattr(row, "source_connectors", None) if row is not None else None)
        if domain is None:
            return _HandoffTarget(
                problem="payment_setup_incomplete", message="This shop has no connected Shopify store to pay in."
            )
        return _HandoffTarget(shop_domain=domain)
    from core.commerce.plural_hosted import load_credentials

    async with get_tenant_session(_tenant(req)) as session:
        credentials = await load_credentials(
            session,
            tenant_id=req.tenant_id,
            credential_ref=str(settings.credential_ref),
            merchant_id=merchant.merchant_id,
        )
    if credentials is None:
        return _HandoffTarget(problem="payment_setup_incomplete", message="This shop's payment page is not set up.")
    if ph.return_url(str(req.tenant_id), "probe") is None:
        return _HandoffTarget(problem="payment_link_unavailable", message="Payment links are not available here.")
    return _HandoffTarget(plural=credentials)


# Sessions read when summing one day's committed hand-offs; a merchant past this many in a day is
# held to what they add up to (the cap still applies, on a lower bound of the day's total).
DAILY_COMMITTED_SCAN_LIMIT = 2000
# How long after its checkout ended a Plural payment confirmed today still counts as paid today
# (the webhook, or the status poll that runs every minute, normally confirms within minutes).
DAILY_COMMITTED_LATE_PAYMENT = timedelta(days=1)


async def daily_committed_minor_units(
    session: Any, *, tenant_id: Any, merchant: MerchantUcp, currency: str | None, exclude_session_id: str, now: datetime
) -> int:
    """The merchant's money committed in ``currency``, for the arbiter's daily cap.

    Committed means paid today (UTC: ``completed_at`` today), or a payment link that still holds
    the cap (``payment_handoff.holds_daily_cap``): a link still open, whatever day it was issued, or
    one issued today even after it lapsed (Shopify never reports a payment and its cart link never
    expires; a Plural order stays payable after its link lapses). The link counts whether its
    checkout still waits for the payment or was canceled: canceling the checkout does not take the
    link back. Every Plural link the checkout handed out counts from its reference's record until
    Plural reports it paid or failed, and a Plural order being created counts too. A paid link
    counts as paid; a failed one cannot be paid. The checkout being decided is left out; the
    arbiter adds its own amount.

    The read is four ranges of ``ix_commerce_checkout_sessions_status`` (tenant, status, expires_at),
    one per status, so it never reads the merchant's history. A link never outlives its checkout
    (the Shopify cart link ends with it, a Plural link at ``min(issued + PLURAL_LINK_TTL, checkout
    end)``) and is issued before the checkout ends, so an open link or one issued today belongs to
    a checkout that ends after midnight. A checkout paid today ends after midnight too, unless
    Plural confirmed the payment after the checkout ended: ``DAILY_COMMITTED_LATE_PAYMENT`` covers
    that (a payment confirmed later than that is not counted). A session lives at most
    ``MAX_CHECKOUT_TTL_SECONDS``, so the same bounds hold on ``created_at`` one TTL earlier, which
    lets ``ix_commerce_checkout_sessions_scope`` serve the ranges too.
    """
    from sqlalchemy import union_all

    from core.commerce.payment_handoff import holds_daily_cap
    from core.commerce.ucp.merchant import MAX_CHECKOUT_TTL_SECONDS
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = timedelta(seconds=MAX_CHECKOUT_TTL_SECONDS)

    def ending_after(status: str, moment: datetime) -> Any:
        return select(Row.status, Row.state).where(
            Row.tenant_id == tenant_id,
            Row.status == status,
            Row.expires_at > moment,
            Row.created_at > moment - ttl,
            Row.merchant_id == merchant.merchant_id,
            Row.currency == currency,
            Row.id != exclude_session_id,
        )

    paid = ending_after("completed", day_start - DAILY_COMMITTED_LATE_PAYMENT).where(Row.completed_at >= day_start)
    handed_off = [
        ending_after(status, day_start).where(Row.state.has_key("handoff"))
        for status in ("requires_escalation", "canceled")
    ]
    handed_off.append(
        ending_after("complete_in_progress", day_start).where(Row.state["handoff"]["status"].astext == "creating")
    )
    rows = (await session.execute(union_all(paid, *handed_off).limit(DAILY_COMMITTED_SCAN_LIMIT))).all()
    committed = 0
    for status, state in rows:
        if status != "completed" and not holds_daily_cap(state or {}, now=now, day_start=day_start):
            continue
        for line in (state or {}).get("totals") or []:
            if isinstance(line, Mapping) and line.get("type") == "total":
                try:
                    committed += int(line.get("amount") or 0)
                except (TypeError, ValueError):
                    continue
                break
    return committed


async def _complete_with_handoff(
    runtime: UcpRuntime,
    req: UcpRequest,
    merchant: MerchantUcp,
    negotiated: Negotiated,
    session_id: str,
    settings: PaymentSettings | None,
) -> UcpResponse:
    """Complete for a merchant that takes payment on its own page: decide, consume, then hand the link over.

    Transaction 1 seals a ``payment_handoff`` decision (an unchanged valid link
    is returned as is). After its commit the decision is offered to Grantex for
    countersignature; transaction 2 consumes the allow for the exact payload and
    issues the Shopify cart permalink, or reserves the session
    (``complete_in_progress``) for the Plural order, which is created outside any
    transaction; transaction 3 records the Plural link (or returns the session to
    ``ready_for_complete`` when Plural refused). No executor runs and no
    payment credential is read.
    """
    from core.commerce import payment_handoff as ph
    from core.commerce.arbiter_grantex import countersign_decision
    from core.commerce.arbiter_store import require_allow_decision
    from core.database import get_tenant_session

    version = negotiated.version
    event = runtime.events["complete_checkout"]
    now = runtime.clock()
    target = await _handoff_target(req, merchant, settings) if settings is not None else None
    cart_url: str | None = None
    decided: Decision | None = None
    decided_revision = 0
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=True, protocol=runtime.protocol)
        frozen = _frozen_error(row, now, version, "completed")
        if frozen is not None:
            return frozen
        state = dict(row.state or {})

        async def _blocked(messages: list[dict[str, Any]], outcome: str = "blocked", **details: Any) -> UcpResponse:
            return await _blocked_response(
                runtime, session, req, merchant, negotiated, row, now, messages, outcome, **details
            )

        active = ph.active_handoff(state, now)
        if row.status == "requires_escalation" and active is not None:
            # Idempotent: the same link while it is valid.
            await _record(
                session,
                req,
                merchant,
                event,
                "success",
                row.id,
                {"handoff": "reused", "mode": active.get("mode"), "decision_id": active.get("decision_id")},
            )
            rendered = await _render_checkout(runtime, row, merchant, negotiated, now)
            return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)
        if settings is None or target is None:
            return await _blocked(
                [
                    msg.error(
                        "payment_handoff_unavailable",
                        "This shop has no payment link set up for this channel; pay with the shop directly.",
                        severity="unrecoverable",
                    )
                ]
            )
        reissue = row.status == "requires_escalation" and isinstance(state.get("handoff"), Mapping)
        if row.status != "ready_for_complete" and not reissue:
            return await _blocked(
                [msg.error("missing", "The checkout is not ready to complete; see its messages.", path="$.status")]
            )
        if reissue:
            ph.clear_handoff(state, "expired")
            if msg.has_errors(state.get("messages") or []):
                return await _blocked(
                    [msg.error("missing", "The checkout is not ready to complete; see its messages.", path="$.status")]
                )
        if target.problem is not None:
            return await _blocked([msg.error(target.problem, target.message, severity="unrecoverable")])
        if settings.mode == ph.MODE_SHOPIFY_CART:
            cart_url = ph.shopify_cart_url(target.shop_domain, state.get("line_items") or [])
            if cart_url is None:
                return await _blocked(
                    [
                        msg.error(
                            "payment_handoff_unavailable",
                            "These items cannot be paid for in the shop's Shopify checkout.",
                            severity="unrecoverable",
                        )
                    ]
                )
        total = int(next(t["amount"] for t in state["totals"] if t["type"] == "total"))
        payload = ph.handoff_payload(
            session_id=row.id,
            revision=int(row.revision),
            merchant_id=merchant.merchant_id,
            seller_agent_id=merchant.seller_agent_id,
            state=state,
            mode=settings.mode,
        )
        cache_records, merchant_config = (
            await runtime.arbiter_scope(merchant) if runtime.arbiter_scope is not None else ((), None)
        )
        committed = await daily_committed_minor_units(
            session,
            tenant_id=row.tenant_id,
            merchant=merchant,
            currency=row.currency,
            exclude_session_id=row.id,
            now=now,
        )
        decision = decide(
            ph.ACTION,
            dataclasses.replace(
                arbiter_context(
                    merchant=merchant,
                    now=now,
                    payload=payload,
                    amount_minor_units=total,
                    currency=row.currency,
                    quantity=sum(int(li["quantity"]) for li in state["line_items"]),
                    authority=None,
                    cache_records=cache_records,
                    merchant_config=merchant_config,
                    buyer_agent_id=session_buyer_agent_id(row),
                ),
                daily_committed_amount_minor_units=committed,
            ),
        )
        await persist_decision(session, decision)
        if not decision.allowed:
            return await _blocked(
                [msg.error("arbiter_refused", f"The commerce arbiter refused: {decision.failing_rule}.", path="$")],
                outcome="refused",
                vendor={"decision": decision.to_public()},
            )
        decided, decided_revision = decision, int(row.revision)
    # After the decision's commit, outside any transaction: never waits, never retries.
    client = runtime.grantex_client() if runtime.grantex_client is not None else None
    countersignature = await countersign_decision(decided, client=client)
    vendor_out = {"decision": decided.to_public(), "grantex_countersignature": countersignature.to_dict()}
    reservation: dict[str, Any] | None = None
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=True, protocol=runtime.protocol)
        frozen = _frozen_error(row, now, version, "completed")
        if frozen is not None:
            return frozen
        state = dict(row.state or {})

        async def _refused(code: str, content: str) -> UcpResponse:
            return await _blocked_response(
                runtime,
                session,
                req,
                merchant,
                negotiated,
                row,
                now,
                [msg.error(code, content, path="$")],
                outcome="refused",
                vendor=vendor_out,
            )

        if row.revision != decided_revision or row.status not in ("ready_for_complete", "requires_escalation"):
            return await _refused("decision_payload_mismatch", "The checkout changed after the decision.")
        try:
            await require_allow_decision(
                session,
                tenant_id=req.tenant_id,
                decision_id=decided.decision_id,
                payload_hash=decided.payload_hash,
                action=ph.ACTION,
            )
        except DecisionRefusedError as exc:
            return await _refused(exc.code, exc.message)
        if state.get("handoff") and row.status == "requires_escalation":
            replaced = state.get("handoff")
            if (
                isinstance(replaced, Mapping)
                and replaced.get("mode") == ph.MODE_PLURAL_HOSTED
                and replaced.get("status") == "issued"
            ):
                # The lapsed link can still be paid: it stays on its record, counting and locking the items.
                ph.remember_link(state, replaced)
            ph.clear_handoff(state, "expired")
        row.decision_id = decided.decision_id
        row.revision = row.revision + 1
        row.updated_at = now
        if cart_url is not None:
            ph.issue(
                state,
                mode=ph.MODE_SHOPIFY_CART,
                continue_url=cart_url,
                decision_id=decided.decision_id,
                now=now,
                expires_at=row.expires_at,
            )
            row.state = state
            row.status = status_for(state["messages"], state["continue_url"])
            await _record(
                session,
                req,
                merchant,
                event,
                "success",
                row.id,
                {"handoff": "issued", "mode": ph.MODE_SHOPIFY_CART, "decision_id": decided.decision_id},
                payload=decided.to_public(),
            )
            rendered = await _render_checkout(runtime, row, merchant, negotiated, now, vendor=vendor_out)
            return UcpResponse(status=200, body=rendered, recorded=True, resource_id=row.id)
        total = int(next(t["amount"] for t in state["totals"] if t["type"] == "total"))
        reference = ph.handoff_reference(row.id, decided.decision_id)
        state["handoff"] = {
            "mode": ph.MODE_PLURAL_HOSTED,
            "status": "creating",
            "decision_id": decided.decision_id,
            "reference": reference,
            "credential_ref": settings.credential_ref if settings is not None else None,
        }
        ph.remember_reference(
            state,
            reference,
            {
                "decision_id": decided.decision_id,
                "amount": total,
                "currency": str(row.currency or "").upper(),
                "cart_hash": ph.cart_hash(state),
                "credential_ref": settings.credential_ref if settings is not None else None,
            },
        )
        row.state = state
        row.status = "complete_in_progress"
        await _record(
            session,
            req,
            merchant,
            event,
            "success",
            row.id,
            {"handoff": "creating", "mode": ph.MODE_PLURAL_HOSTED, "decision_id": decided.decision_id},
            payload=decided.to_public(),
        )
        reservation = {
            "reference": reference,
            "total": total,
            "currency": str(row.currency or ""),
            "buyer": dict(state.get("buyer") or {}),
            "expires_at": row.expires_at,
        }
    assert target is not None and reservation is not None
    from core.commerce.plural_hosted import PluralRequestError, PluralUnavailableError, create_hosted_order

    created = None
    failure = "payment_link_unavailable"
    try:
        created = await create_hosted_order(
            target.plural.credentials,
            reference=reservation["reference"],
            amount_minor_units=reservation["total"],
            currency=reservation["currency"],
            callback_url=str(ph.return_url(str(req.tenant_id), session_id)),
            buyer=reservation["buyer"],
            metadata={
                "oacp_decision_id": decided.decision_id,
                "oacp_checkout_session_id": session_id,
                "oacp_payload_hash": decided.payload_hash,
            },
        )
    except PluralRequestError as exc:
        logger.warning("plural_hosted_order_refused", code=exc.code, http_status=exc.status_code)
        failure = "payment_link_refused"
    except PluralUnavailableError as exc:
        logger.warning("plural_hosted_order_unavailable", error_type=type(exc).__name__)
    # enterprise-gate: broad-except-ok reason=plural-order-failure-returns-the-session-to-ready-fails-closed-no-link
    except Exception as exc:
        logger.error("plural_hosted_order_failed", error_type=type(exc).__name__)
    later = runtime.clock()
    async with get_tenant_session(_tenant(req)) as session:
        row = await _load_session(session, req, merchant, session_id, lock=True, protocol=runtime.protocol)
        state = dict(row.state or {})
        handoff = state.get("handoff") if isinstance(state.get("handoff"), Mapping) else {}
        if handoff.get("reference") != reservation["reference"] or row.status != "complete_in_progress":
            rendered = await _render_checkout(runtime, row, merchant, negotiated, later)
            return UcpResponse(status=200, body=rendered, resource_id=row.id)
        extra: list[dict[str, Any]] = []
        if created is not None:
            expires = min(later + ph.PLURAL_LINK_TTL, reservation["expires_at"])
            ph.issue(
                state,
                mode=ph.MODE_PLURAL_HOSTED,
                continue_url=created.redirect_url,
                decision_id=decided.decision_id,
                now=later,
                expires_at=expires,
                extra={"provider_order_id": created.order_id},
            )
            ph.remember_reference(state, reservation["reference"], ph.link_record(state["handoff"]))
            row.status = status_for(state["messages"], state["continue_url"])
        else:
            row.status = ph.retire_failed(state)
            extra.append(
                msg.error(failure, "The shop's payment page could not be opened; try again shortly.", path="$")
            )
        row.state = state
        row.revision = row.revision + 1
        row.updated_at = later
        await _record(
            session,
            req,
            merchant,
            HANDOFF_EVENT,
            "success" if created is not None else "refused",
            row.id,
            {
                "result": "issued" if created is not None else failure,
                "mode": ph.MODE_PLURAL_HOSTED,
                "decision_id": decided.decision_id,
                "provider_order_ref_present": created is not None,
            },
        )
        rendered = await _render_checkout(
            runtime, row, merchant, negotiated, later, extra_messages=extra, vendor=vendor_out
        )
    return UcpResponse(status=200, body=rendered, recorded=True, resource_id=session_id)


async def confirm_handoff_payment(
    *,
    tenant_id: str,
    reference: str,
    provider_order_id: str,
    status: str,
    amount_minor_units: int | None,
    currency: str | None,
    source: str,
    event_id: str | None,
    now: datetime,
) -> tuple[str, str | None]:
    """Apply a verified Plural hosted-order state to the hand-off it belongs to; idempotent.

    ``paid`` (``PROCESSED``/``CAPTURED``) creates the order (as an executed
    Complete would: order entity, ledger, order webhooks) when the amount and
    currency are those the hand-off asked for and the checkout is still the one
    the buyer paid for; ``failed`` retires the current link so the next Complete
    issues a new one. Returns ``(result, order_id)``; results:
    ``completed``, ``already_completed``, ``pending``, ``link_retired``,
    ``unknown_reference``, ``order_mismatch``, ``amount_mismatch``,
    ``checkout_changed``, ``paid_after_cancel``, ``duplicate_event``, ``not_applicable``.
    """
    from core.commerce import payment_handoff as ph
    from core.commerce.ucp.merchant import config_from_row, merchant_ucp_from_config
    from core.commerce.ucp.store import load_merchant_config_row
    from core.database import get_tenant_session
    from core.models.commerce_ucp import CommerceCheckoutSessionRow as Row

    tenant = uuid.UUID(str(tenant_id))
    contains = Row.state.contains({"handoff_refs": [reference]})
    async with get_tenant_session(tenant) as session:
        scope = (
            await session.execute(
                select(Row.id, Row.merchant_id, Row.seller_agent_id).where(Row.tenant_id == tenant, contains)
            )
        ).first()
    if scope is None:
        return "unknown_reference", None
    config_row = await load_merchant_config_row(
        tenant_id=str(tenant), merchant_id=scope.merchant_id, seller_agent_id=scope.seller_agent_id
    )
    req = UcpRequest(tenant_id=str(tenant), host="", ucp_agent=None)
    order_event: OrderEvent | None = None
    order_id: str | None = None
    async with get_tenant_session(tenant) as session:
        row = (
            await session.execute(select(Row).where(Row.tenant_id == tenant, Row.id == scope.id).with_for_update())
        ).scalar_one_or_none()
        if row is None or config_row is None:
            return "unknown_reference", None
        merchant = merchant_ucp_from_config(
            config_from_row(config_row), tenant_id=str(tenant), serving_host=row.serving_host
        )
        if merchant is None:
            return "unknown_reference", None
        state = dict(row.state or {})
        record = dict((state.get("handoff_orders") or {}).get(reference) or {})
        payment = ph.payment_state(status)
        result: str
        if payment == "pending" and row.status != "completed":
            return "pending", None
        if ph.seen_event(state, event_id):
            return "duplicate_event", row.order_id
        if record.get("provider_order_id") and record["provider_order_id"] != provider_order_id:
            result = "order_mismatch"
        elif row.status == "completed":
            result, order_id = "already_completed", row.order_id
        elif payment == "failed":
            handoff = state.get("handoff") if isinstance(state.get("handoff"), Mapping) else {}
            if handoff.get("reference") == reference and row.status in ("requires_escalation", "complete_in_progress"):
                row.status = ph.retire_failed(state)
                result = "link_retired"
            elif handoff.get("reference") == reference and row.status == "canceled":
                # The checkout stays canceled; its link no longer holds the daily maximum.
                ph.set_reference_status(state, reference, "failed")
                ph.clear_handoff(state, "failed")
                result = "link_retired"
            elif ph.payable(record):
                # An earlier link of the checkout: it no longer counts or locks the items.
                ph.set_reference_status(state, reference, "failed")
                result = "link_retired"
            else:
                result = "not_applicable"
        elif (
            amount_minor_units is None
            or amount_minor_units != record.get("amount")
            or (str(currency or "").upper() != str(record.get("currency") or ""))
        ):
            result = "amount_mismatch"
        elif row.status == "canceled" or (row.canceled_at is not None):
            result = "paid_after_cancel"
        elif ph.cart_hash(state) != record.get("cart_hash"):
            result = "checkout_changed"
        else:
            if state.get("protocol") == "acp":
                from core.commerce.acp.checkout import EVENTS as ACP_EVENTS

                created_event = ACP_EVENTS["order_created"]
            else:
                created_event = ORDER_CREATED_EVENT
            ph.set_reference_status(state, reference, "paid")
            ph.clear_handoff(state, "paid")
            order_event = await _complete_into_order(
                session,
                req,
                merchant,
                row,
                state,
                version=row.ucp_version,
                instruments=[],
                decision_id=str(record.get("decision_id") or row.decision_id or ""),
                executor_name=ph.ORDER_EXECUTOR[ph.MODE_PLURAL_HOSTED],
                provider_order_ref=provider_order_id,
                policies=(),
                now=now,
                order_created_event=created_event,
            )
            order_id = order_event.order_id
            result = "completed"
        row.state = state
        row.revision = row.revision + 1
        row.updated_at = now
        await _record(
            session,
            req,
            merchant,
            HANDOFF_CONFIRM_EVENT,
            "success" if result in ("completed", "already_completed", "link_retired") else "refused",
            row.id,
            {
                "result": result,
                "source": source,
                "payment_state": payment,
                "decision_id": record.get("decision_id"),
                "provider_order_ref_present": bool(provider_order_id),
            },
        )
    if result in ("amount_mismatch", "paid_after_cancel", "checkout_changed", "order_mismatch"):
        # The merchant's Plural account holds money no order here matches: the ledger event and this log say so.
        logger.error("plural_hosted_payment_unmatched", result=result, source=source)
    if order_event is not None:
        from core.commerce.ucp.webhooks import kick_dispatch

        kick_dispatch(tenant)
        from core.commerce.order_notifications import kick_order_notification

        kick_order_notification(str(tenant), order_event.order_id)
    return result, order_id


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
    event_id: str | None = None,
) -> tuple[int, dict[str, Any], str]:
    """Record a fulfillment event or an adjustment on an order and queue the full snapshot to every subscriber.

    Returns ``(revision, snapshot, ucp_version)``; raises ``OrderChangeError`` (404 unknown order, 422 a change
    that does not apply, 409 a fulfillment ``event_id`` already recorded). The ledger event, the outbox rows and
    the order event listeners share one transaction.
    """
    from core.commerce.ucp.orders import (
        ORDER_UPDATED,
        DuplicateOrderEventError,
        OrderUpdateError,
        apply_adjustment,
        apply_fulfillment_event,
    )
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
                entry = apply_fulfillment_event(snapshot, fulfillment_event, now=now, event_id=event_id)
            else:
                entry = apply_adjustment(snapshot, adjustment or {}, now=now)
        except DuplicateOrderEventError as exc:
            raise OrderChangeError(409, "event_already_recorded") from exc
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
