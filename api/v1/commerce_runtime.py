"""Internal C6Z Seller Commerce Agent runtime vertical endpoints."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.commerce.c6z_runtime_vertical import (
    GRANTEX_AUTHORITY_ENV_VARS,
    PLURAL_PINE_ENV_VARS,
    SHOPIFY_ENV_VARS,
    SHOPIFY_WEBHOOK_ENV_VARS,
    C6ZRuntimeValidationError,
    ShopifyAdminGraphQLClient,
    answer_product_question_from_cache,
    build_cache_record_from_grantex_artifact,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
    resolve_env,
    resolve_shopify_credentials,
    send_grantex_authority_request,
    shopify_webhook_idempotency_key,
    verify_plural_pine_mandate_capability,
    verify_shopify_webhook_hmac,
)
from core.commerce.oacp_artifacts import (
    DurableOacpArtifactCacheRepository,
    OacpArtifactCacheRepositoryQuery,
)
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import (
    C6ZConnectorEvidenceRow,
    C6ZProviderCapabilityEvidenceRow,
    C6ZSellerOnboardingPacketRow,
)

router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Commerce Runtime"],
    dependencies=[require_tenant_admin],
)


class SellerOnboardingPacketCreate(BaseModel):
    merchant_id: str = Field(min_length=1)
    seller_agent_id: str = Field(min_length=1)
    merchant_display_name: str = Field(min_length=1)
    public_brand_profile: dict[str, Any] = Field(default_factory=dict)
    commerce_categories: list[str] = Field(min_length=1)
    requested_grantex_authority_scope: dict[str, Any] = Field(default_factory=dict)
    artifact_cache_scope: dict[str, Any] = Field(default_factory=dict)
    source_freshness_policy: dict[str, Any] = Field(default_factory=dict)
    connector_metadata: dict[str, Any] = Field(default_factory=dict)


class ShopifySyncRequest(BaseModel):
    packet_id: str
    page_size: int = Field(default=50, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=20)
    currency: str | None = None


class GrantexAuthorityRequest(BaseModel):
    packet_id: str
    evidence_id: str


class CacheArtifactsRequest(BaseModel):
    artifacts: list[dict[str, Any]]
    buyer_agent_id: str | None = None


class BuyerQuestionRequest(BaseModel):
    merchant_id: str
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    question: str = Field(min_length=1)
    action_intent: str = "non_binding_preview"
    grantex_available: bool = True


class MandateCapabilityRequest(BaseModel):
    merchant_id: str
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    capability_type: str = "mandate_capability"


@router.post("/seller-agents/onboarding-packets")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.seller_agents.write",
    rate_limit="commerce-runtime-write",
    idempotency="deterministic-seller-onboarding-packet-id",
    audit_event="commerce.runtime.seller_onboarding_packet.create",
)
async def create_seller_onboarding_packet(
    body: SellerOnboardingPacketCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    packet = _build_packet_from_body(body, tenant_id)
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await session.get(C6ZSellerOnboardingPacketRow, packet["packet_id"])
        if row is None:
            row = C6ZSellerOnboardingPacketRow(packet_id=packet["packet_id"])
            session.add(row)
        _apply_packet(row, packet)
    return {"packet": packet, "stored": True}


@router.get("/seller-agents/onboarding-packets/{packet_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.seller_agents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.seller_onboarding_packet.read",
)
async def get_seller_onboarding_packet(
    packet_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await session.get(C6ZSellerOnboardingPacketRow, packet_id)
        if row is None or row.tenant_id != tenant_id:
            raise HTTPException(404, "Seller Commerce Agent onboarding packet not found")
        return {"packet": _row_to_packet(row)}


@router.post("/seller-agents/shopify/sync")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.shopify.sync",
    rate_limit="connector-bulk",
    idempotency="packet-id-and-shopify-source-snapshot",
    audit_event="commerce.runtime.shopify.sync",
)
async def sync_shopify_read_only(
    body: ShopifySyncRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    env_resolution = resolve_env(SHOPIFY_ENV_VARS)
    if not env_resolution.ready:
        raise HTTPException(
            status_code=424,
            detail={
                "status": "blocked_missing_shopify_env",
                "missing_env_vars": list(env_resolution.missing),
                "external_validation_performed": False,
            },
        )
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        packet_row = await session.get(C6ZSellerOnboardingPacketRow, body.packet_id)
        if packet_row is None or packet_row.tenant_id != tenant_id:
            raise HTTPException(404, "Seller Commerce Agent onboarding packet not found")
        packet = _row_to_packet(packet_row)
        credentials = resolve_shopify_credentials()
        client = ShopifyAdminGraphQLClient(credentials)
        try:
            products = await client.fetch_products(page_size=body.page_size, max_pages=body.max_pages)
        except (httpx.HTTPError, C6ZRuntimeValidationError) as exc:
            raise HTTPException(status_code=502, detail=f"Shopify read-only sync failed: {exc}") from exc
        now_iso = _now_iso()
        evidence = build_shopify_connector_evidence(
            packet=packet,
            products=products,
            synced_at=now_iso,
            source_observed_at=now_iso,
            currency=body.currency,
        )
        row = await session.get(C6ZConnectorEvidenceRow, evidence["evidence_id"])
        if row is None:
            row = C6ZConnectorEvidenceRow(evidence_id=evidence["evidence_id"])
            session.add(row)
        _apply_evidence(row, evidence, hmac_verified=False)
        packet_row.status = "artifact_issuance_ready"
    return {
        "status": "shopify_sync_stored",
        "evidence_id": evidence["evidence_id"],
        "product_count": evidence["product_count"],
        "variant_count": evidence["variant_count"],
        "raw_payload_stored": False,
        "allowed_to_execute": False,
    }


@router.post("/shopify/webhooks/product-update")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.shopify.webhook",
    rate_limit="commerce-webhook",
    idempotency="shopify-webhook-id-or-body-hash",
    audit_event="commerce.runtime.shopify.product_update_webhook",
)
async def receive_shopify_product_webhook(
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    env_resolution = resolve_env(SHOPIFY_WEBHOOK_ENV_VARS)
    if not env_resolution.ready:
        raise HTTPException(
            status_code=424,
            detail={
                "status": "blocked_missing_shopify_webhook_env",
                "missing_env_vars": list(env_resolution.missing),
            },
        )
    raw_body = await request.body()
    hmac_header = request.headers.get("x-shopify-hmac-sha256", "")
    topic = request.headers.get("x-shopify-topic", "products/update")
    shop_domain = request.headers.get("x-shopify-shop-domain", "")
    webhook_id = request.headers.get("x-shopify-webhook-id")
    verified = verify_shopify_webhook_hmac(raw_body, hmac_header, os.environ["SHOPIFY_WEBHOOK_SECRET"])
    if not verified:
        raise HTTPException(status_code=401, detail="Shopify webhook HMAC verification failed")
    return {
        "status": "webhook_verified",
        "tenant_id": tenant_id,
        "idempotency_key": shopify_webhook_idempotency_key(
            topic=topic,
            shop_domain=shop_domain,
            webhook_id=webhook_id,
            raw_body=raw_body,
        ),
        "hmac_verified": True,
        "raw_payload_stored": False,
        "allowed_to_execute": False,
    }


@router.post("/authority/grantex/request")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.grantex.authority_request",
    rate_limit="commerce-runtime-external",
    idempotency="packet-id-and-evidence-id",
    audit_event="commerce.runtime.grantex.authority_request",
)
async def request_grantex_authority_artifacts(
    body: GrantexAuthorityRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        packet_row = await session.get(C6ZSellerOnboardingPacketRow, body.packet_id)
        evidence_row = await session.get(C6ZConnectorEvidenceRow, body.evidence_id)
        if packet_row is None or evidence_row is None:
            raise HTTPException(404, "C6Z packet or connector evidence not found")
        packet = _row_to_packet(packet_row)
        evidence = _row_to_evidence(evidence_row)
        payload = build_grantex_authority_request_payload(
            onboarding_packet=packet,
            connector_evidence=evidence,
        )
    env_resolution = resolve_env(GRANTEX_AUTHORITY_ENV_VARS)
    if not env_resolution.ready:
        return {
            "status": "blocked_missing_grantex_env",
            "missing_env_vars": list(env_resolution.missing),
            "authority_request_payload": payload,
            "artifact_issuance_attempted": False,
            "allowed_to_execute": False,
            "non_authoritative_for_transaction": True,
        }
    return await send_grantex_authority_request(payload=payload)


@router.post("/artifacts/cache")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.artifacts.cache",
    rate_limit="commerce-runtime-write",
    idempotency="artifact-id-upsert",
    audit_event="commerce.runtime.artifacts.cache",
)
async def cache_grantex_artifacts(
    body: CacheArtifactsRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    now_iso = _now_iso()
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        repo = DurableOacpArtifactCacheRepository(session)
        store_results: list[dict[str, Any]] = []
        for artifact in body.artifacts:
            record = build_cache_record_from_grantex_artifact(
                artifact,
                cached_at=now_iso,
                buyer_agent_id=body.buyer_agent_id,
            )
            if record.tenant_id != tenant_id:
                raise HTTPException(403, "Artifact tenant scope mismatch")
            store_results.append(await repo.upsert(record))
        rejected = [result for result in store_results if result.get("stored") is not True]
        if rejected:
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "artifact_cache_rejected",
                    "records_stored": len(store_results) - len(rejected),
                    "records_rejected": len(rejected),
                    "store_results": store_results,
                    "allowed_to_execute": False,
                    "non_authoritative_for_transaction": True,
                },
            )
    return {
        "status": "cached",
        "records_stored": len(store_results),
        "records_rejected": 0,
        "store_results": store_results,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


@router.post("/buyer-sessions/ask")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.buyer_sessions.ask",
    rate_limit="commerce-buyer-session",
    idempotency="read-only-non-binding-answer",
    audit_event="commerce.runtime.buyer_session.ask",
)
async def ask_buyer_product_question(
    body: BuyerQuestionRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        repo = DurableOacpArtifactCacheRepository(session)
        cache_records = await repo.list_for_scope(
            OacpArtifactCacheRepositoryQuery(
                tenant_id=tenant_id,
                merchant_id=body.merchant_id,
                seller_agent_id=body.seller_agent_id,
                buyer_agent_id=body.buyer_agent_id,
            )
        )
        evidence_query = select(C6ZConnectorEvidenceRow).where(
            C6ZConnectorEvidenceRow.tenant_id == tenant_id,
            C6ZConnectorEvidenceRow.merchant_id == body.merchant_id,
        )
        if body.seller_agent_id:
            evidence_query = evidence_query.where(C6ZConnectorEvidenceRow.seller_agent_id == body.seller_agent_id)
        evidence_rows = (await session.scalars(evidence_query.order_by(C6ZConnectorEvidenceRow.synced_at.desc()))).all()
        products: list[dict[str, Any]] = []
        for evidence_row in evidence_rows:
            products.extend(list(evidence_row.products or []))
        answer = answer_product_question_from_cache(
            cache_records=cache_records,
            products=products,
            question=body.question,
            now_iso=_now_iso(),
            grantex_available=body.grantex_available,
            action_intent=cast(Any, body.action_intent),
        )
    return {
        "status": answer.status,
        "answer": answer.answer,
        "source_label": answer.source_label,
        "freshness_label": answer.freshness_label,
        "refusal_reason": answer.refusal_reason,
        "matched_products": list(answer.matched_products),
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


@router.post("/providers/plural-pine/mandate-capability/verify")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.providers.plural_pine.verify",
    rate_limit="commerce-runtime-external",
    idempotency="merchant-capability-check-window",
    audit_event="commerce.runtime.providers.plural_pine.verify",
)
async def verify_plural_pine_capability(
    body: MandateCapabilityRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    evidence = await verify_plural_pine_mandate_capability(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
        capability_type=body.capability_type,
    )
    if evidence.external_validation_performed:
        async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
            row = await session.get(C6ZProviderCapabilityEvidenceRow, evidence.evidence_id)
            if row is None:
                row = C6ZProviderCapabilityEvidenceRow(evidence_id=evidence.evidence_id)
                session.add(row)
            _apply_capability(row, evidence, tenant_id, body.merchant_id, body.seller_agent_id, body.buyer_agent_id)
    return {
        "evidence": evidence.__dict__,
        "env_required": list(PLURAL_PINE_ENV_VARS),
        "stored": evidence.external_validation_performed,
    }


@router.get("/products")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.products.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.products.list",
)
async def list_cached_products(
    merchant_id: str,
    seller_agent_id: str | None = None,
    q: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        query = select(C6ZConnectorEvidenceRow).where(
            C6ZConnectorEvidenceRow.tenant_id == tenant_id,
            C6ZConnectorEvidenceRow.merchant_id == merchant_id,
        )
        if seller_agent_id:
            query = query.where(C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id)
        rows = (await session.scalars(query.order_by(C6ZConnectorEvidenceRow.synced_at.desc()))).all()
    products = [product for row in rows for product in list(row.products or [])]
    if q:
        q_lower = q.lower()
        products = [product for product in products if q_lower in str(product.get("title", "")).lower()]
    return {
        "products": products[:50],
        "source_label": "Source: Shopify via Grantex artifact",
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


def _build_packet_from_body(body: SellerOnboardingPacketCreate, tenant_id: str) -> dict[str, Any]:
    return build_seller_onboarding_packet(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        merchant_display_name=body.merchant_display_name,
        public_brand_profile=body.public_brand_profile,
        commerce_categories=body.commerce_categories,
        requested_grantex_authority_scope=body.requested_grantex_authority_scope
        or {"artifact_families": ["merchant_profile", "seller_agent_card", "catalog_snapshot"]},
        artifact_cache_scope=body.artifact_cache_scope
        or {"tenant_id": tenant_id, "merchant_id": body.merchant_id, "seller_agent_id": body.seller_agent_id},
        source_freshness_policy=body.source_freshness_policy or {"max_age_seconds": 900},
        connector_metadata=body.connector_metadata,
    )


def _apply_packet(row: C6ZSellerOnboardingPacketRow, packet: Mapping[str, Any]) -> None:
    row.tenant_id = str(packet["tenant_id"])
    row.merchant_id = str(packet["merchant_id"])
    row.seller_agent_id = str(packet["seller_agent_id"])
    row.merchant_display_name = str(packet["merchant_display_name"])
    row.public_brand_profile = dict(packet["public_brand_profile"])
    row.commerce_categories = list(packet["commerce_categories"])
    row.connector_choice = "shopify"
    row.connector_mode = "read_only"
    row.requested_grantex_authority_scope = dict(packet["requested_grantex_authority_scope"])
    row.artifact_cache_scope = dict(packet["artifact_cache_scope"])
    row.source_freshness_policy = dict(packet["source_freshness_policy"])
    row.connector_metadata_redacted = dict(packet["connector_metadata_redacted"])
    row.status = str(packet["status"])
    row.no_payment_execution = True
    row.no_public_discovery_enablement = True
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True


def _apply_evidence(row: C6ZConnectorEvidenceRow, evidence: Mapping[str, Any], *, hmac_verified: bool) -> None:
    row.packet_id = str(evidence["packet_id"])
    row.tenant_id = str(evidence["tenant_id"])
    row.merchant_id = str(evidence["merchant_id"])
    row.seller_agent_id = str(evidence["seller_agent_id"])
    row.source_system = "shopify"
    row.source_mode = "read_only"
    row.source_evidence_ref = str(evidence["source_evidence_ref"])
    row.source_observed_at = _parse_dt(str(evidence["source_observed_at"]))
    row.synced_at = _parse_dt(str(evidence["synced_at"]))
    row.currency = None if evidence.get("currency") is None else str(evidence["currency"])
    row.products = [dict(product) for product in evidence["products"]]
    row.product_count = int(evidence["product_count"])
    row.variant_count = int(evidence["variant_count"])
    row.idempotency_key = None if evidence.get("idempotency_key") is None else str(evidence["idempotency_key"])
    row.hmac_verified = hmac_verified
    row.raw_payload_stored = False
    row.no_payment_execution = True
    row.no_public_discovery_enablement = True
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True


def _apply_capability(
    row: C6ZProviderCapabilityEvidenceRow,
    evidence: Any,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
) -> None:
    row.tenant_id = tenant_id
    row.merchant_id = merchant_id
    row.seller_agent_id = seller_agent_id
    row.buyer_agent_id = buyer_agent_id
    row.provider = evidence.provider
    row.capability_type = evidence.capability_type
    row.result_status = evidence.result_status
    row.checked_at = _parse_dt(evidence.checked_at)
    row.expires_at = _parse_dt(evidence.expires_at)
    row.redacted_evidence_ref = evidence.redacted_evidence_ref
    row.provider_environment = evidence.provider_environment
    row.external_validation_performed = evidence.external_validation_performed
    row.missing_env_vars = list(evidence.missing_env_vars)
    row.raw_payload_stored = False
    row.no_payment_execution = True
    row.no_live_provider_enablement = True
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True


def _row_to_packet(row: C6ZSellerOnboardingPacketRow) -> dict[str, Any]:
    return {
        "packet_id": row.packet_id,
        "tenant_id": row.tenant_id,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "merchant_display_name": row.merchant_display_name,
        "public_brand_profile": row.public_brand_profile or {},
        "commerce_categories": row.commerce_categories or [],
        "connector_choice": row.connector_choice,
        "connector_mode": row.connector_mode,
        "requested_grantex_authority_scope": row.requested_grantex_authority_scope or {},
        "artifact_cache_scope": row.artifact_cache_scope or {},
        "source_freshness_policy": row.source_freshness_policy or {},
        "connector_metadata_redacted": row.connector_metadata_redacted or {},
        "status": row.status,
        "no_payment_execution": row.no_payment_execution,
        "no_public_discovery_enablement": row.no_public_discovery_enablement,
        "allowed_to_execute": row.allowed_to_execute,
        "non_authoritative_for_transaction": row.non_authoritative_for_transaction,
    }


def _row_to_evidence(row: C6ZConnectorEvidenceRow) -> dict[str, Any]:
    return {
        "evidence_id": row.evidence_id,
        "packet_id": row.packet_id,
        "tenant_id": row.tenant_id,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "source_system": row.source_system,
        "source_mode": row.source_mode,
        "source_evidence_ref": row.source_evidence_ref,
        "source_observed_at": row.source_observed_at.isoformat().replace("+00:00", "Z"),
        "synced_at": row.synced_at.isoformat().replace("+00:00", "Z"),
        "currency": row.currency,
        "products": row.products or [],
        "product_count": row.product_count,
        "variant_count": row.variant_count,
        "idempotency_key": row.idempotency_key,
        "raw_payload_stored": row.raw_payload_stored,
        "no_payment_execution": row.no_payment_execution,
        "no_public_discovery_enablement": row.no_public_discovery_enablement,
        "allowed_to_execute": row.allowed_to_execute,
        "non_authoritative_for_transaction": row.non_authoritative_for_transaction,
    }


def _tenant_uuid(tenant_id: str) -> UUID:
    try:
        return UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(400, "Tenant context must be a UUID") from exc


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
