"""Public-safe OACP commerce publishing endpoints."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy import select

from api.route_metadata import route_meta
from core.commerce.oacp_public_publishing import (
    OacpPublicPublishingError,
    build_public_catalog_html,
    build_public_catalog_snapshot,
    build_public_llms_txt,
    build_public_sitemap_xml,
    build_schema_org_jsonld,
    find_public_product,
    public_catalog_enabled,
)
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow, C6ZSellerOnboardingPacketRow

public_router = APIRouter(prefix="/public/commerce", tags=["Public Commerce"])


@public_router.get("/sellers/{merchant_id}", response_class=HTMLResponse)
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.seller_profile.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.seller_profile",
)
async def public_seller_profile_page(
    merchant_id: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> HTMLResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    return HTMLResponse(build_public_catalog_html(snapshot))


@public_router.get("/sellers/{merchant_id}/catalog.json")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.catalog.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.catalog_json",
)
async def public_seller_catalog_json(
    merchant_id: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> JSONResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    return JSONResponse(snapshot)


@public_router.get("/sellers/{merchant_id}/products/{product_slug}", response_class=HTMLResponse)
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.product.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.product_page",
)
async def public_product_detail_page(
    merchant_id: str,
    product_slug: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> HTMLResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    if find_public_product(snapshot, product_slug) is None:
        raise HTTPException(status_code=404, detail="Public-safe product not found")
    return HTMLResponse(build_public_catalog_html(snapshot, product_slug=product_slug))


@public_router.get("/sellers/{merchant_id}/products/{product_slug}.json")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.product.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.product_json",
)
async def public_product_detail_json(
    merchant_id: str,
    product_slug: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> JSONResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    product = find_public_product(snapshot, product_slug)
    if product is None:
        raise HTTPException(status_code=404, detail="Public-safe product not found")
    return JSONResponse(
        {
            "status": "public_product_ready",
            "source_label": snapshot["source_label"],
            "freshness_label": snapshot["freshness_label"],
            "product": product,
            "schema_org_jsonld": build_schema_org_jsonld(snapshot, product_slug=product_slug),
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "non_authoritative_for_transaction": True,
        }
    )


@public_router.get("/sellers/{merchant_id}/schema-org.jsonld")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.schema_org.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.schema_org",
)
async def public_schema_org_jsonld(
    merchant_id: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> JSONResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    return JSONResponse(snapshot["schema_org_jsonld"])


@public_router.get("/sellers/{merchant_id}/sitemap.xml")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.sitemap.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.sitemap",
)
async def public_seller_sitemap_xml(
    merchant_id: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> Response:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    return Response(content=build_public_sitemap_xml(snapshot), media_type="application/xml")


@public_router.get("/sellers/{merchant_id}/llms.txt")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public.commerce.llms.read",
    rate_limit="public-read",
    idempotency="read-only",
    audit_event="commerce.public.llms",
)
async def public_seller_llms_txt(
    merchant_id: str,
    tenant_id: str = Query(..., min_length=36, max_length=36),
    seller_agent_id: str | None = None,
) -> PlainTextResponse:
    snapshot = await _load_public_catalog_snapshot(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
    )
    _raise_if_blocked(snapshot)
    return PlainTextResponse(build_public_llms_txt(snapshot), media_type="text/plain; charset=utf-8")


async def _load_public_catalog_snapshot(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
) -> dict[str, Any]:
    tid = _tenant_uuid(tenant_id)
    async with get_tenant_session(tid) as session:
        packet_query = select(C6ZSellerOnboardingPacketRow).where(
            C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
            C6ZSellerOnboardingPacketRow.merchant_id == merchant_id,
        )
        evidence_query = select(C6ZConnectorEvidenceRow).where(
            C6ZConnectorEvidenceRow.tenant_id == tenant_id,
            C6ZConnectorEvidenceRow.merchant_id == merchant_id,
        )
        if seller_agent_id:
            packet_query = packet_query.where(C6ZSellerOnboardingPacketRow.seller_agent_id == seller_agent_id)
            evidence_query = evidence_query.where(C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id)
        packet_row = (
            await session.scalars(packet_query.order_by(C6ZSellerOnboardingPacketRow.created_at.desc()))
        ).first()
        evidence_rows = (
            await session.scalars(evidence_query.order_by(C6ZConnectorEvidenceRow.synced_at.desc()))
        ).all()
    if packet_row is None:
        raise HTTPException(status_code=404, detail="Seller Commerce Agent public profile not found")
    evidence_records = [_evidence_mapping(row) for row in evidence_rows[:8]]
    try:
        return build_public_catalog_snapshot(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id or packet_row.seller_agent_id,
            merchant_display_name=packet_row.merchant_display_name,
            public_brand_profile=packet_row.public_brand_profile or {},
            commerce_categories=packet_row.commerce_categories or [],
            connector_metadata_redacted=packet_row.connector_metadata_redacted or {},
            evidence_records=evidence_records,
            base_url=os.getenv("AGENTICORG_PUBLIC_BASE_URL", "https://agenticorg.ai"),
            public_enabled=public_catalog_enabled(),
        )
    except OacpPublicPublishingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _evidence_mapping(row: C6ZConnectorEvidenceRow) -> Mapping[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "source_evidence_ref": row.source_evidence_ref,
        "source_observed_at": row.source_observed_at.isoformat(),
        "synced_at": row.synced_at.isoformat(),
        "currency": row.currency,
        "products": row.products or [],
        "product_count": row.product_count,
        "variant_count": row.variant_count,
    }


def _raise_if_blocked(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("status") != "public_catalog_ready":
        raise HTTPException(
            status_code=423,
            detail={
                "reason": snapshot.get("reason"),
                "message": snapshot.get("message"),
                "allowed_to_execute": False,
                "no_payment_execution": True,
            },
        )


def _tenant_uuid(tenant_id: str) -> UUID:
    try:
        return UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant_id must be a UUID") from exc
