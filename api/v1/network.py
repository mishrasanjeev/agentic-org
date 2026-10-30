# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The AgenticOrg shopping network ("Sell everywhere"): directory, checkout gateway, merchant opt-in.

Public, under ``/api/v1/network`` (no session; every route answers 404 while
``AGENTICORG_NETWORK_ENABLED`` is off):

* ``GET /network/merchants`` - the listed shops that are servable right now: slug,
  name, categories, the UCP profile URL and the Telegram deep link.
* ``POST /network/shops/{slug}/checkout-sessions``, ``GET``/``PUT
  /network/shops/{slug}/checkout-sessions/{id}`` - open, read and update a
  checkout at a listed shop for the AgenticOrg network platform. Only the
  network service credential (``AGENTICORG_NETWORK_SERVICE_TOKEN``, held by the
  hosted MCP server) is accepted. The UCP handlers run in the shop's tenant on
  its published host, exactly as a platform call to ``https://<host>/ucp``
  would; network checkouts answer to the network platform only.
* ``POST /network/shops/{slug}/checkout-sessions/{id}/complete`` - the payment
  hand-off only (``core.commerce.payment_handoff``): a ready checkout answers
  ``requires_escalation`` with ``continue_url``, the shop's own payment page
  (its Shopify cart or its Plural hosted checkout). It never runs a payment
  executor: a shop without a hand-off configured is refused, and any payment
  instrument, decision or mandate in the body is refused. There is no cancel
  or payment route: the buyer pays on the shop's page.

* ``POST /network/telegram/webhook`` - the public AgenticOrg shopping bot
  (``core.commerce.network_telegram``): Telegram's secret header is checked and the update is
  queued; 404 while ``AGENTICORG_NETWORK_TELEGRAM_ENABLED`` is off.

Merchant (``commerce.merchant_config.write`` or tenant admin), under
``/api/v1/commerce/runtime/merchant-configs/{merchant_id}/sell-everywhere``:
``GET`` where the shop stands on each surface, its Grantex enrolment, its UCP
publication and its artifacts; ``PUT`` turn the network on (accepting the network
terms) or pause it; ``POST .../publish`` run the go-live steps again (Publish /
Retry). A shop with no published host of its own gets a platform subdomain
``<slug>.<apex>`` (``core.commerce.network``).

Turning the network on needs the setup done (``api.v1.commerce_merchant_setup``: a
catalog, policies and order limits; 409 ``setup_incomplete`` names what is
missing). It then goes live in this order, each step after the last one's
transaction: the Grantex service-tenant enrolment
(``core.commerce.grantex_enrolment``; an older Grantex is a waiting state, never a
failure), the listing, the platform subdomain, the UCP profile publication when
``publish_refusal`` allows it (otherwise the exact refusal is shown), and one
artifact refresh, so the Grantex request carries the public-discovery opt-in. No
step's failure undoes an earlier one; the page shows where it stopped.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1 import commerce_runtime as runtime
from api.v1.commerce_merchant_setup import setup_view
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from api.v1.commerce_ucp_publishing import (
    UcpScope,
    _actor,
    _locked_row,
    _provision_or_rotate,
    publish_profile,
    publish_refusal,
)
from core.commerce import grantex_enrolment
from core.commerce import network as net
from core.commerce.c6z_runtime_vertical import DEMO_SOURCE_SYSTEM
from core.commerce.ledger import record_commerce_event_in_own_transaction
from core.commerce.ucp.catalog import verified_catalog_source
from core.commerce.ucp.state import attested_host, is_published, ucp_state
from core.commerce.ucp.store import load_shared_cache_records
from core.config import settings
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow
from core.models.merchant_custom_domain import INACTIVE_STATUSES, MerchantCustomDomainRow

logger = structlog.get_logger()

router = APIRouter(prefix="/network", tags=["Network"])
merchant_router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Network"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)

GATEWAY_OPERATIONS = ("create_checkout", "get_checkout", "update_checkout", "complete_checkout")
# A network Complete may carry nothing that could pay: no instruments, no decision, no mandate.
_PAYING_COMPLETE_FIELDS = ("payment", "ap2", "ai.agenticorg.commerce", "payment_data", "signals")
MAX_GATEWAY_BODY_BYTES = 64 * 1024


def _require_network() -> None:
    if not net.network_enabled():
        raise HTTPException(404, "Not Found")


def _refusal(exc: net.NetworkError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


def _service_token_ok(request: Request) -> bool:
    expected = (settings.network_service_token or "").strip()
    if len(expected) < 32:
        return False
    presented = request.headers.get("authorization", "")
    if not presented.startswith("Bearer "):
        return False
    return hmac.compare_digest(
        hashlib.sha256(presented[7:].strip().encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


# --- public directory -------------------------------------------------------------------------


@router.get("/merchants")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.directory.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="network.directory.read",
    public_reason="network-directory-lists-only-published-opted-in-shops",
)
async def list_network_merchants() -> JSONResponse:
    _require_network()
    shops = await net.directory()
    return JSONResponse({"shops": shops, "count": len(shops)}, headers={"Cache-Control": "public, max-age=60"})


# --- checkout gateway (network service credential) ----------------------------------------------


def _paying_complete(operation: str, raw: bytes) -> bool:
    """True for a Complete body that tries to pay (anything beyond an empty object or ``{}``)."""
    if operation != "complete_checkout" or not raw.strip():
        return False
    try:
        body = json.loads(raw)
    except ValueError:
        return True
    if not isinstance(body, dict):
        return True
    return any(body.get(name) not in (None, {}, []) for name in _PAYING_COMPLETE_FIELDS)


def _execution_refusal(request_id: str | None) -> JSONResponse:
    headers = {"Request-Id": request_id[:128]} if request_id else {}
    return JSONResponse(
        {
            "code": "payment_execution_not_allowed",
            "message": "Network checkouts never take payment; complete with an empty body to get the payment link.",
        },
        status_code=422,
        headers=headers,
    )


async def _gateway(operation: str, slug: str, request: Request, session_id: str | None = None) -> JSONResponse:
    _require_network()
    if not settings.ucp_checkout_enabled:
        raise HTTPException(404, "Not Found")
    if not _service_token_ok(request):
        return JSONResponse(
            {"code": "unauthorized", "message": "The network service credential is required."},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="agenticorg-network"'},
        )
    raw = await request.body()
    if len(raw) > MAX_GATEWAY_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    if _paying_complete(operation, raw):
        return _execution_refusal(request.headers.get("request-id"))
    try:
        shop = await net.resolve_shop(slug)
    except net.NetworkError as exc:
        raise _refusal(exc) from None
    request_id = request.headers.get("request-id")
    response = await _run_for_network(
        operation,
        shop,
        raw,
        idempotency_key=request.headers.get("idempotency-key"),
        request_id=request_id,
        session_id=session_id,
        surface="mcp",
    )
    headers = dict(response.headers)
    if request_id:
        headers["Request-Id"] = request_id[:128]
    return JSONResponse(status_code=response.status, content=response.body, headers=headers)


async def _run_for_network(
    operation: str,
    shop: net.ShopRef,
    raw: bytes,
    *,
    idempotency_key: str | None,
    request_id: str | None,
    session_id: str | None,
    surface: str | None = None,
) -> Any:
    from api.v1.commerce_ucp import run_ucp_operation  # noqa: PLC0415 - commerce_ucp wires the UCP runtime at import

    if operation not in GATEWAY_OPERATIONS:
        raise ValueError(f"not a network operation: {operation}")
    if _paying_complete(operation, raw):
        raise ValueError("a network complete carries no payment")
    profile = settings.network_agent_profile
    response = await run_ucp_operation(
        operation,
        tenant_id=shop.tenant_id,
        host=shop.host,
        ucp_agent=f'profile="{profile}"',
        raw_body=b"{}" if operation == "complete_checkout" else raw,
        idempotency_key=idempotency_key,
        request_id=request_id,
        resource_id=session_id,
        platform=profile,
        # The network may only hand the buyer the shop's payment link, never run an executor.
        handoff_only=operation == "complete_checkout",
        surface=surface if operation == "create_checkout" else None,
    )
    logger.info("network_checkout_call", operation=operation, slug=shop.slug, status=response.status)
    return response


async def network_checkout(
    slug: str, operation: str, body: dict[str, Any] | None, session_id: str | None = None, surface: str = "telegram"
) -> dict[str, Any]:
    """The network checkout path for in-process callers (the public Telegram bot).

    Same resolution and UCP handling as the gateway routes; returns the shop's answer with its
    HTTP status and the shop slug, or ``{"error": ...}`` when the shop is not available.
    """
    if not (net.network_enabled() and settings.ucp_checkout_enabled):
        return {"error": "checkout_unavailable"}
    try:
        shop = await net.resolve_shop(slug)
    except net.NetworkError as exc:
        return {"error": exc.code, "message": exc.message}
    raw = json.dumps(body).encode() if body is not None else b""
    response = await _run_for_network(
        operation,
        shop,
        raw,
        idempotency_key=str(uuid.uuid4()) if operation != "get_checkout" else None,
        request_id=str(uuid.uuid4()),
        session_id=session_id,
        surface=surface,
    )
    answer = response.body if isinstance(response.body, dict) else {"body": response.body}
    return {"http_status": response.status, "shop": shop.slug, **answer}


# --- public Telegram bot ----------------------------------------------------------------------


def _telegram_secret_ok(request: Request) -> bool:
    expected = (settings.network_telegram_webhook_secret or "").strip()
    presented = request.headers.get("x-telegram-bot-api-secret-token", "")
    if len(expected) < 24 or not presented:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected.encode()).digest())


@router.post("/telegram/webhook")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:network.telegram.webhook",
    rate_limit="provider-webhook",
    idempotency="telegram-update-id-claimed-once",
    audit_event="network.telegram.update",
    public_reason="telegram-webhook-secret-checked-in-handler",
)
async def network_telegram_webhook(request: Request) -> JSONResponse:
    if not (net.network_enabled() and settings.network_telegram_enabled):
        raise HTTPException(404, "Not Found")
    if not _telegram_secret_ok(request):
        raise HTTPException(403, "Forbidden")
    raw = await request.body()
    if len(raw) > MAX_GATEWAY_BODY_BYTES * 4:
        return JSONResponse({"ok": True})
    try:
        update = json.loads(raw)
    except ValueError:
        return JSONResponse({"ok": True})
    from core.tasks.network_tasks import handle_telegram_update  # noqa: PLC0415 - Celery app import on demand

    handle_telegram_update.delay(update)
    return JSONResponse({"ok": True})


@router.post("/shops/{slug}/checkout-sessions", status_code=201)
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="network.checkout.create",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_create_checkout(slug: str, request: Request) -> JSONResponse:
    return await _gateway("create_checkout", slug, request)


@router.get("/shops/{slug}/checkout-sessions/{session_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.read",
    rate_limit="ucp-platform",
    idempotency="read-only",
    audit_event="network.checkout.read",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_get_checkout(slug: str, session_id: str, request: Request) -> JSONResponse:
    return await _gateway("get_checkout", slug, request, session_id)


@router.put("/shops/{slug}/checkout-sessions/{session_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="network.checkout.update",
    public_reason="network-service-credential-checked-in-handler",
)
async def network_update_checkout(slug: str, session_id: str, request: Request) -> JSONResponse:
    return await _gateway("update_checkout", slug, request, session_id)


@router.post("/shops/{slug}/checkout-sessions/{session_id}/complete")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="network.checkout.write",
    rate_limit="ucp-platform",
    idempotency="idempotency-key-header",
    audit_event="network.checkout.complete",
    public_reason="network-service-credential-checked-in-handler-payment-handoff-only-never-executes",
)
async def network_complete_checkout(slug: str, session_id: str, request: Request) -> JSONResponse:
    return await _gateway("complete_checkout", slug, request, session_id)


# --- merchant: Sell everywhere ----------------------------------------------------------------


class SellEverywhereRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listed: bool
    accept_terms: bool = False
    slug: str | None = Field(default=None, min_length=3, max_length=40)
    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)
    claim_subdomain: bool = True


async def _view(tenant_id: str, merchant_id: str, seller_agent_id: str | None) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, lock=False
        )
        seller = config.seller_agent_id or ""
        listing = await net.get_listing(
            session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
        )
        domain = (
            await session.scalars(
                select(MerchantCustomDomainRow).where(
                    MerchantCustomDomainRow.tenant_id == uuid.UUID(tenant_id),
                    MerchantCustomDomainRow.merchant_id == config.merchant_id,
                    MerchantCustomDomainRow.seller_agent_id == seller,
                    MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
                )
            )
        ).first()
    in_use = await net.platform_subdomains_in_use() if net.platform_apex() else 0
    enrolment = await grantex_enrolment.read_enrolment(tenant_id)
    shared = await load_shared_cache_records(
        tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
    )
    if listing is not None and listing.status == "listed":
        # The catalog facts live minutes and renew when read. Read them as a buyer agent would, so the page
        # says whether Grantex still verifies the catalog, not whether anyone shopped in the last minute.
        from core.commerce.artifact_freshness import CATALOG_TYPES, ensure_fresh

        if await ensure_fresh(
            tenant_id=tenant_id,
            merchant_id=config.merchant_id,
            seller_agent_id=seller,
            records=list(shared),
            required=CATALOG_TYPES,
        ):
            shared = await load_shared_cache_records(
                tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
            )
    artifacts = verified_catalog_source(cache_records=shared, products=[], now_iso=runtime._now_iso())
    setup = await setup_view(tenant_id, config.merchant_id)
    return {
        "enabled": True,
        "terms_version": settings.network_terms_version,
        "suggested_slug": net.slugify(config.merchant_display_name or config.merchant_id),
        "listing": net.listing_view(listing),
        "surfaces": net.surfaces_view(listing, config, domain),
        "platform_subdomains": {
            "apex": net.platform_apex() or None,
            "in_use": in_use,
            "cap": int(settings.platform_subdomain_cap),
        },
        "grantex_enrolment": grantex_enrolment.enrolment_view(enrolment, config.merchant_id),
        "publication": _publication_view(config),
        "artifacts": {
            "status": "ready" if artifacts.unavailable_reason is None else "missing",
            "reason": artifacts.unavailable_reason,
        },
        "setup": {"missing": setup["missing"], "ready_to_turn_on": setup["ready_to_turn_on"]},
    }


def _publication_view(config: Any) -> dict[str, Any]:
    state = ucp_state(config.public_publishing)
    published = is_published(state)
    refusal = None if published else publish_refusal(config, None)[0]
    return {
        "published": published,
        "host": attested_host(state),
        # The exact reason publication is not possible yet (None once nothing stands in the way).
        "refusal": refusal,
    }


async def _catalog_source(tenant_id: str, merchant_id: str, seller_agent_id: str) -> str | None:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        evidence = (
            await session.scalars(
                select(C6ZConnectorEvidenceRow)
                .where(
                    C6ZConnectorEvidenceRow.tenant_id == tenant_id,
                    C6ZConnectorEvidenceRow.merchant_id == merchant_id,
                    C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id,
                )
                .order_by(C6ZConnectorEvidenceRow.synced_at.desc())
            )
        ).first()
    return evidence.source_system if evidence is not None else None


async def _go_live(tenant_id: str, config: Any, actor: dict[str, str]) -> dict[str, Any]:
    """Grantex enrolment, UCP publication and one artifact refresh; each outcome is reported, none raises."""
    seller = config.seller_agent_id or ""
    outcome: dict[str, Any] = {}
    source = await _catalog_source(tenant_id, config.merchant_id, seller)
    if source is not None and source != DEMO_SOURCE_SYSTEM:
        state = await grantex_enrolment.ensure_enrolment(tenant_id, config.merchant_id, source)
        outcome["enrolment"] = state.get("status")
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        current = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller or None, lock=False
        )
        already_published = is_published(ucp_state(current.public_publishing))
        key_missing = not already_published and publish_refusal(current, None)[0] == "signing_key_missing"
    if key_missing:
        # A merchant onboarded before the UCP profile flag has no signing key yet: provision it once.
        await _provision_or_rotate(
            merchant_id=config.merchant_id,
            body=UcpScope(seller_agent_id=seller or None),
            tenant_id=tenant_id,
            rotate=False,
            event="commerce.ucp.keys.provision",
        )
    if not already_published:
        published = await publish_profile(
            tenant_id=tenant_id,
            merchant_id=config.merchant_id,
            seller_agent_id=seller or None,
            namespace=None,
            actor=actor,
        )
        outcome["publication"] = "published" if published.get("published") else published.get("refusal")
    else:
        outcome["publication"] = "published"
    outcome["artifacts"] = await runtime.refresh_scope_artifacts(
        tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
    )
    logger.info("network_go_live", **{key: str(value) for key, value in outcome.items()})
    return outcome


@merchant_router.get("/merchant-configs/{merchant_id}/sell-everywhere")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="network.sell_everywhere.read",
)
async def get_sell_everywhere(
    merchant_id: str,
    seller_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_network()
    return await _view(tenant_id, merchant_id, seller_agent_id)


@merchant_router.put("/merchant-configs/{merchant_id}/sell-everywhere")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="listing-state-converges",
    audit_event="commerce.network.sell_everywhere.write",
)
async def put_sell_everywhere(
    merchant_id: str,
    body: SellEverywhereRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    _require_network()
    if body.listed and not body.accept_terms:
        raise HTTPException(
            422, {"code": "terms_not_accepted", "message": "Accept the network terms to list the shop."}
        )
    actor = _actor(request)
    actor_label = f"{actor.get('actor_type', 'user')}:{actor.get('actor_id', 'unknown')}"
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id, lock=False
        )
    if body.listed:
        setup = await setup_view(tenant_id, config.merchant_id)
        if setup["missing"]:
            await record_commerce_event_in_own_transaction(
                tenant_id=tenant_id,
                event="commerce.network.sell_everywhere.write",
                outcome="refused",
                merchant_id=config.merchant_id,
                seller_agent_id=config.seller_agent_id or "",
                buyer_agent_id=None,
                payload={"listed": True, "reason": "setup_incomplete", "missing": setup["missing"]},
                resource_id=config.config_id,
            )
            raise HTTPException(
                409,
                {
                    "code": "setup_incomplete",
                    "message": "Finish the setup first: " + ", ".join(setup["missing"]).replace("_", " ") + ".",
                    "missing": setup["missing"],
                },
            )
    try:
        listing = await net.save_listing(
            tenant_id=tenant_id, config=config, listed=body.listed, slug=body.slug, actor_label=actor_label
        )
        subdomain_note = None
        if body.listed and body.claim_subdomain and net.published_host(config) is None:
            try:
                await net.claim_platform_subdomain(
                    tenant_id=tenant_id, config=config, listing=listing, actor=actor, actor_label=actor_label
                )
            except net.NetworkError as exc:
                # The listing stands; the page shows why no platform address was given.
                subdomain_note = {"code": exc.code, "message": exc.message}
    except net.NetworkError as exc:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event="commerce.network.sell_everywhere.write",
            outcome="refused",
            merchant_id=config.merchant_id,
            seller_agent_id=config.seller_agent_id or "",
            buyer_agent_id=None,
            payload={"listed": body.listed, "reason": exc.code},
            resource_id=config.config_id,
        )
        raise _refusal(exc) from None
    go_live = await _go_live(tenant_id, config, actor) if body.listed else None
    view = await _view(tenant_id, merchant_id, body.seller_agent_id)
    view["subdomain_note"] = subdomain_note
    view["go_live"] = go_live
    return view


class SellEverywherePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_agent_id: str | None = Field(default=None, min_length=1, max_length=160)


@merchant_router.post("/merchant-configs/{merchant_id}/sell-everywhere/publish")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-external",
    idempotency="go-live-steps-converge",
    audit_event="commerce.network.sell_everywhere.publish",
)
async def publish_sell_everywhere(
    merchant_id: str,
    body: SellEverywherePublishRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Publish / Retry: the go-live steps again for a listed shop (enrolment, address, publication, artifacts)."""
    _require_network()
    actor = _actor(request)
    actor_label = f"{actor.get('actor_type', 'user')}:{actor.get('actor_id', 'unknown')}"
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        config = await _locked_row(
            session, tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=body.seller_agent_id, lock=False
        )
        listing = await net.get_listing(
            session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=config.seller_agent_id or ""
        )
    if listing is None or listing.status != "listed":
        raise HTTPException(409, {"code": "not_listed", "message": "Turn on Sell everywhere first."})
    subdomain_note = None
    if net.published_host(config) is None and attested_host(ucp_state(config.public_publishing)) is None:
        try:
            await net.claim_platform_subdomain(
                tenant_id=tenant_id, config=config, listing=listing, actor=actor, actor_label=actor_label
            )
        except net.NetworkError as exc:
            subdomain_note = {"code": exc.code, "message": exc.message}
    go_live = await _go_live(tenant_id, config, actor)
    await record_commerce_event_in_own_transaction(
        tenant_id=tenant_id,
        event="commerce.network.sell_everywhere.publish",
        outcome="success",
        merchant_id=config.merchant_id,
        seller_agent_id=config.seller_agent_id or "",
        buyer_agent_id=None,
        payload={"go_live": {key: str(value) for key, value in go_live.items()}, "subdomain_note": subdomain_note},
        resource_id=config.config_id,
    )
    view = await _view(tenant_id, merchant_id, body.seller_agent_id)
    view["subdomain_note"] = subdomain_note
    view["go_live"] = go_live
    return view
