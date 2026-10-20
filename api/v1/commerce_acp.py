# SPDX-License-Identifier: Apache-2.0
"""ACP REST binding: checkout sessions at ``https://<merchant serving host>/acp`` and the product feed export.

Checkout sessions (ACP API version ``2026-04-17``), behind
``AGENTICORG_ACP_CHECKOUT_ENABLED`` (default off: every ``/acp`` route answers 404):

* ``POST /acp/checkout_sessions`` (201), ``POST /acp/checkout_sessions/{id}``
  (partial update), ``GET /acp/checkout_sessions/{id}``,
  ``POST /acp/checkout_sessions/{id}/complete``,
  ``POST /acp/checkout_sessions/{id}/cancel`` (405 once it cannot be canceled).

The merchant is resolved from ``Host`` exactly as for the UCP routes (an attested,
published serving host inside the tenant of the credential) and must have opted in
(``provider_policy.acp.checkout_enabled``). The agent platform authenticates with
``Authorization: Bearer`` and the tenant API key the merchant issued it (scopes
``acp:read`` / ``acp:write``). Headers, idempotency, the request schema and the
mapping to the shared UCP session operations live in ``core.commerce.acp``; this
module is the HTTP adapter. Every write records its ledger event; an answer with no
business write (a refusal, a replay) is recorded here in its own transaction.

Product feed (``AGENTICORG_ACP_FEED_ENABLED``, default off: 404), tenant
administrators: ``GET /api/v1/commerce/runtime/acp/feed?merchant_id=&seller_agent_id=&format=``
with ``format`` ``json`` (ACP ``ProductsResponse``), ``jsonl`` (``products.jsonl``),
``metadata`` (``FeedMetadata``) or ``csv`` (the merchant-feed columns). 409 when the
verified artifacts the feed needs are missing or stale.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.commerce.acp import checkout as acp_checkout
from core.commerce.acp import feed as acp_feed
from core.commerce.ledger import commerce_payload_hash, record_commerce_event_in_own_transaction
from core.config import settings

logger = structlog.get_logger()


def require_acp_checkout_enabled() -> None:
    if not settings.acp_checkout_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/acp", tags=["ACP"], dependencies=[Depends(require_acp_checkout_enabled)])
runtime_router = APIRouter(
    prefix="/commerce/runtime/acp",
    tags=["Commerce Runtime"],
    dependencies=[require_tenant_admin],
)


def build_runtime() -> acp_checkout.AcpRuntime:
    """The production wiring: the UCP runtime, the Grantex service client and the signature mode."""
    from api.v1.commerce_ucp import build_runtime as build_ucp_runtime
    from core.commerce.grantex_service import grantex_service_client_from_env

    return acp_checkout.AcpRuntime(
        ucp=build_ucp_runtime(),
        grantex=grantex_service_client_from_env,
        signature_mode=lambda: str(settings.acp_signature_mode or "log").strip().lower(),
    )


runtime_factory = build_runtime


def _platform(request: Request) -> str:
    """The calling agent platform: the API key it authenticated with (``acp:api_key:<key id>``).

    ACP platforms are machine credentials the merchant issued; sessions, idempotency records and order
    webhooks are bound to that key. Any other credential has no platform identity here.
    """
    from core.commerce.ucp.webhooks import acp_platform_id

    key_id = getattr(request.state, "api_key_id", None)
    if getattr(request.state, "auth_mode", None) != "api_key" or not key_id:
        raise HTTPException(status_code=403, detail="ACP checkout needs the API key issued to the agent platform")
    return acp_platform_id(key_id)


async def _call(operation: str, request: Request, tenant_id: str, session_id: str | None = None) -> JSONResponse:
    raw = await request.body()
    req = acp_checkout.AcpRequest(
        operation=operation,
        tenant_id=tenant_id,
        host=request.headers.get("host", ""),
        platform=_platform(request),
        method=request.method.upper(),
        path=request.url.path,
        raw_body=raw,
        headers={k.lower(): v for k, v in request.headers.items()},
        session_id=session_id,
    )
    response = await acp_checkout.run(req, runtime_factory())
    event = acp_checkout.EVENTS.get(acp_checkout.OPERATIONS[operation])
    if event and not response.recorded:
        await record_commerce_event_in_own_transaction(
            tenant_id=tenant_id,
            event=event,
            outcome=response.outcome if response.outcome in ("success", "refused", "blocked") else "refused",
            merchant_id=response.merchant_id,
            seller_agent_id=response.seller_agent_id,
            buyer_agent_id=None,
            payload={
                "operation": operation,
                "status": response.status,
                "body_sha256": commerce_payload_hash(raw.decode("utf-8", "replace")),
            },
            resource_id=response.resource_id or operation,
            details={"http_status": response.status, "operation": operation},
        )
    return JSONResponse(status_code=response.status, content=response.body, headers=response.headers)


@router.post("/checkout_sessions", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="acp.checkout.write",
    rate_limit="acp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.acp.checkout.create",
)
async def create_checkout_session(request: Request, tenant_id: str = Depends(get_current_tenant)) -> JSONResponse:
    return await _call("create", request, tenant_id)


@router.get("/checkout_sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="acp.checkout.read",
    rate_limit="acp-platform",
    idempotency="read-only",
)
async def get_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("get", request, tenant_id, session_id)


@router.post("/checkout_sessions/{session_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="acp.checkout.write",
    rate_limit="acp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.acp.checkout.update",
)
async def update_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("update", request, tenant_id, session_id)


@router.post("/checkout_sessions/{session_id}/complete")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="acp.checkout.write",
    rate_limit="acp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.acp.checkout.complete",
)
async def complete_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("complete", request, tenant_id, session_id)


@router.post("/checkout_sessions/{session_id}/cancel")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="acp.checkout.write",
    rate_limit="acp-platform",
    idempotency="idempotency-key-header",
    audit_event="commerce.acp.checkout.cancel",
)
async def cancel_checkout_session(
    session_id: str, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> JSONResponse:
    return await _call("cancel", request, tenant_id, session_id)


# --- product feed -----------------------------------------------------------------------------


async def load_feed(*, tenant_id: str, merchant_id: str, seller_agent_id: str, now: datetime) -> acp_feed.Feed:
    """Build the merchant's feed from its config, verified policy and verified catalog inputs."""
    from core.commerce.ucp.catalog import usable_artifact_ids
    from core.commerce.ucp.merchant import config_from_row
    from core.commerce.ucp.state import ucp_state
    from core.commerce.ucp.store import load_catalog_inputs, load_merchant_config_row

    row = await load_merchant_config_row(tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id)
    if row is None or str(row.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=404, detail="Merchant config not found")
    config = config_from_row(row)
    records, products = await load_catalog_inputs(
        tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id
    )
    now_iso = now.isoformat().replace("+00:00", "Z")
    state = ucp_state(config.get("public_publishing"))
    snapshot = state.get("verified_policy") if isinstance(state.get("verified_policy"), dict) else {}
    policy = None
    if snapshot.get("artifact_id") in usable_artifact_ids(
        cache_records=records, now_iso=now_iso, artifact_type="policy"
    ) and isinstance(snapshot.get("policy"), dict):
        policy = snapshot["policy"]
    seller = acp_feed.seller_from_config(
        config,
        serving_host=state.get("serving_host") if (state.get("attestation") or {}).get("verified_at") else None,
        verified_policy=policy,
        checkout_flag=bool(settings.acp_checkout_enabled),
    )
    return acp_feed.build_feed(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        cache_records=records,
        products=products,
        seller=seller,
        now_iso=now_iso,
    )


@runtime_router.get("/feed")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.acp.feed.read",
    rate_limit="standard",
    idempotency="read-only",
)
async def export_acp_feed(
    merchant_id: str = Query(min_length=1, max_length=160),
    seller_agent_id: str = Query(default="default", min_length=1, max_length=160),
    feed_format: str = Query(default="json", alias="format", pattern="^(json|jsonl|metadata|csv)$"),
    tenant_id: str = Depends(get_current_tenant),
) -> Response:
    if not settings.acp_feed_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        feed = await load_feed(
            tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id, now=datetime.now(UTC)
        )
    except acp_feed.FeedUnavailableError as exc:
        raise HTTPException(status_code=409, detail=f"feed_unavailable:{exc}") from exc
    headers = {"Cache-Control": "no-store", "AgenticOrg-Feed-Skipped-Rows": str(feed.skipped_rows)}
    if feed_format == "csv":
        return Response(content=feed.csv_text(), media_type="text/csv; charset=utf-8", headers=headers)
    if feed_format == "jsonl":
        return Response(content=feed.products_jsonl(), media_type="application/jsonl", headers=headers)
    content: dict[str, Any] = feed.feed_metadata() if feed_format == "metadata" else feed.products_response()
    return JSONResponse(content=content, headers=headers)
