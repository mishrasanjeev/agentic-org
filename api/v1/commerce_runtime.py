"""Internal C6Z Seller Commerce Agent runtime vertical endpoints."""

from __future__ import annotations

import json
import os
import re
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
    ShopifyCredentials,
    answer_product_question_from_cache,
    build_bridge_contract_response,
    build_buyer_surface_bridge_matrix,
    build_cache_record_from_grantex_artifact,
    build_grantex_authority_request_payload,
    build_seller_onboarding_packet,
    build_shopify_connector_evidence,
    generate_protocol_adapter_payloads,
    prepare_purchase_or_mandate_handoff,
    resolve_env,
    resolve_shopify_credentials,
    select_protocol_adapter_payload,
    send_grantex_authority_request,
    shopify_webhook_idempotency_key,
    verify_plural_pine_mandate_capability,
    verify_shopify_webhook_hmac,
    verify_telegram_webhook_secret,
    verify_whatsapp_webhook_signature,
)
from core.commerce.oacp_artifacts import (
    DurableOacpArtifactCacheRepository,
    OacpArtifactCacheRepositoryQuery,
)
from core.commerce.offline_pos_bridge import (
    OfflinePosBridgeError,
    build_offline_pos_confirmation_intake,
    build_offline_pos_handoff_packet,
    reconcile_offline_pos_confirmation,
    simulate_offline_pos_confirmation,
)
from core.crypto import encrypt_for_tenant
from core.crypto.tenant_secrets import decrypt_for_tenant
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import (
    C6ZConnectorEvidenceRow,
    C6ZOfflinePosConfirmationRow,
    C6ZOfflinePosHandoffPacketRow,
    C6ZProviderCapabilityEvidenceRow,
    C6ZSellerOnboardingPacketRow,
)
from core.models.connector_config import ConnectorConfig
from core.security.egress import (
    EgressValidationError,
    build_pinned_async_transport,
    validate_public_url,
)

router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Commerce Runtime"],
    dependencies=[require_tenant_admin],
)

WHATSAPP_BRIDGE_ENV_VARS: tuple[str, ...] = (
    "WHATSAPP_BUSINESS_ACCESS_TOKEN",
    "WHATSAPP_BUSINESS_PHONE_NUMBER_ID",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
)
TELEGRAM_BRIDGE_ENV_VARS: tuple[str, ...] = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET_TOKEN")


class SellerOnboardingPacketCreate(BaseModel):
    merchant_id: str = Field(min_length=1)
    seller_agent_id: str = Field(min_length=1)
    merchant_display_name: str = Field(min_length=1)
    shopify_shop_domain: str | None = None
    public_brand_profile: dict[str, Any] = Field(default_factory=dict)
    commerce_categories: list[str] = Field(min_length=1)
    requested_grantex_authority_scope: dict[str, Any] = Field(default_factory=dict)
    artifact_cache_scope: dict[str, Any] = Field(default_factory=dict)
    source_freshness_policy: dict[str, Any] = Field(default_factory=dict)
    permitted_sync_actions: list[str] = Field(default_factory=list)
    channel_capability_preferences: dict[str, bool] = Field(default_factory=dict)
    payment_mandate_rail_preference: str = "plural_pine_p3p"
    connector_metadata: dict[str, Any] = Field(default_factory=dict)


class ShopifySyncRequest(BaseModel):
    packet_id: str
    page_size: int = Field(default=50, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=20)
    currency: str | None = None


class ShopifyConnectorCredentialRequest(BaseModel):
    merchant_id: str = Field(min_length=1)
    shop_domain: str = Field(min_length=1)
    api_version: str = "2026-04"
    admin_access_token: str | None = None
    oauth_code: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    validate_read: bool = True


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


class BridgeAskRequest(BaseModel):
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str | None = None
    question: str = Field(min_length=1)
    channel: str = "web"
    action_intent: str = "non_binding_preview"
    grantex_available: bool = False


class MandateCapabilityRequest(BaseModel):
    merchant_id: str
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    capability_type: str = "mandate_capability"


class PurchasePreparationRequest(BaseModel):
    merchant_id: str = Field(min_length=1)
    seller_agent_id: str = Field(min_length=1)
    buyer_agent_id: str | None = None
    product_ref_or_query: str = Field(min_length=1)
    variant_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    idempotency_key: str | None = None
    grantex_available: bool = True
    live_execution_approved: bool = False


class OfflinePosHandoffRequest(BaseModel):
    merchant_id: str = Field(min_length=1)
    seller_agent_id: str = Field(min_length=1)
    buyer_agent_id: str | None = None
    buyer_session_ref: str = Field(min_length=1)
    product_ref_or_query: str = Field(min_length=1)
    variant_id: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    store_id: str = Field(min_length=1)
    pos_location: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    grantex_available: bool = True
    expiry_minutes: int = Field(default=15, ge=1, le=60)


class OfflinePosConfirmationRequest(BaseModel):
    packet_id: str = Field(min_length=1)
    confirmation_status: str = Field(min_length=1)
    final_price: str | None = None
    currency: str | None = None
    provider_pos_evidence_ref: str | None = None
    receipt_evidence_ref: str | None = None
    callback_verified: bool = False
    inventory_refresh_required: bool | None = None
    artifact_refresh_required: bool | None = None


class OfflinePosSimulatorRequest(BaseModel):
    packet_id: str = Field(min_length=1)
    confirmation_status: str = "accepted"
    final_price: str | None = None


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
        previous_status = None if row is None else row.status
        if row is None:
            row = C6ZSellerOnboardingPacketRow(packet_id=packet["packet_id"])
            session.add(row)
        _apply_packet(row, packet)
        if previous_status in {
            "sync_ready",
            "synced",
            "authority_requested",
            "artifacts_cached",
            "cache_refresh_needed",
            "blocked_missing_credentials",
            "blocked_grantex_unavailable",
        }:
            row.status = previous_status
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


@router.post("/seller-agents/connectors/shopify/credentials")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.shopify.credentials.write",
    rate_limit="commerce-runtime-write",
    idempotency="merchant-shopify-credential-upsert",
    audit_event="commerce.runtime.shopify.credentials.upsert",
)
async def upsert_shopify_connector_credentials(
    body: ShopifyConnectorCredentialRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tid = _tenant_uuid(tenant_id)
    credentials, credential_source, granted_scopes = await _resolve_submitted_shopify_credentials(body)
    if body.validate_read:
        await _validate_shopify_read_credentials(credentials)

    secret_fields = {
        "admin_access_token": credentials.admin_access_token,
        "shop_domain": credentials.shop_domain,
        "api_version": credentials.api_version,
        "credential_source": credential_source,
    }
    encrypted = await encrypt_for_tenant(json.dumps(secret_fields), tid)
    config = {
        "merchant_id": body.merchant_id,
        "shop_domain": credentials.shop_domain,
        "api_version": credentials.api_version,
        "connector_type": "shopify",
        "connector_mode": "read_only",
        "credential_source": credential_source,
        "granted_scopes": granted_scopes,
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
        "configured_by": "commerce_runtime",
        "configured_at": _now_iso(),
    }
    connector_name = _shopify_connector_config_name(body.merchant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tid,
                ConnectorConfig.connector_name == connector_name,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ConnectorConfig(
                tenant_id=tid,
                connector_name=connector_name,
                display_name=f"Shopify read-only - {body.merchant_id}",
                auth_type="shopify_admin_api",
                credentials_encrypted={"_encrypted": encrypted},
                config=config,
                status="configured",
                health_status="healthy" if body.validate_read else "unknown",
                last_health_check=datetime.now(tz=UTC) if body.validate_read else None,
            )
            session.add(row)
        else:
            row.display_name = f"Shopify read-only - {body.merchant_id}"
            row.auth_type = "shopify_admin_api"
            row.credentials_encrypted = {"_encrypted": encrypted}
            row.config = config
            row.status = "configured"
            row.health_status = "healthy" if body.validate_read else "unknown"
            row.last_health_check = datetime.now(tz=UTC) if body.validate_read else row.last_health_check
            row.sync_error = None
        packets = (
            await session.scalars(
                select(C6ZSellerOnboardingPacketRow).where(
                    C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
                    C6ZSellerOnboardingPacketRow.merchant_id == body.merchant_id,
                    C6ZSellerOnboardingPacketRow.status.in_(
                        (
                            "draft",
                            "received",
                            "blocked_missing_credentials",
                            "cache_refresh_needed",
                        )
                    ),
                )
            )
        ).all()
        for packet in packets:
            packet.status = "sync_ready"
    return {
        "status": "shopify_connector_configured",
        "merchant_id": body.merchant_id,
        "connector_name": connector_name,
        "shop_domain": credentials.shop_domain,
        "api_version": credentials.api_version,
        "credential_source": credential_source,
        "validated": body.validate_read,
        "credential_values_redacted": True,
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
    }


@router.get("/seller-agents/connectors/shopify/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.shopify.credentials.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.shopify.credentials.read",
)
async def get_shopify_connector_status(
    merchant_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    tid = _tenant_uuid(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await _load_shopify_connector_config_row(session, tid, merchant_id)
    if row is None:
        return {
            "status": "not_configured",
            "merchant_id": merchant_id,
            "credential_values_redacted": True,
            "allowed_to_execute": False,
            "non_authoritative_for_transaction": True,
        }
    config = row.config or {}
    return {
        "status": row.status,
        "health_status": row.health_status or "unknown",
        "merchant_id": merchant_id,
        "connector_name": row.connector_name,
        "shop_domain": config.get("shop_domain"),
        "api_version": config.get("api_version"),
        "connector_mode": config.get("connector_mode", "read_only"),
        "credential_values_redacted": True,
        "raw_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
    }


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
    blocked_exc: HTTPException | None = None
    evidence: dict[str, Any] | None = None
    credentials: ShopifyCredentials | None = None
    credential_source = "unknown"
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        packet_row = await session.get(C6ZSellerOnboardingPacketRow, body.packet_id)
        if packet_row is None or packet_row.tenant_id != tenant_id:
            raise HTTPException(404, "Seller Commerce Agent onboarding packet not found")
        packet = _row_to_packet(packet_row)
        try:
            credentials, credential_source = await _resolve_shopify_credentials_for_packet(
                session=session,
                tenant_id=_tenant_uuid(tenant_id),
                packet=packet,
            )
        except HTTPException as exc:
            packet_row.status = "blocked_missing_credentials"
            blocked_exc = exc
        if blocked_exc is None:
            if credentials is None:
                raise HTTPException(status_code=500, detail="Shopify credentials were not resolved")
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
            packet_row.status = "synced"
    if blocked_exc is not None:
        raise blocked_exc
    if evidence is None:
        raise HTTPException(status_code=500, detail="Shopify sync did not produce evidence")
    return {
        "status": "shopify_sync_stored",
        "credential_source": credential_source,
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
    stale_packets_marked = 0
    if shop_domain:
        normalized_shop = _normalize_shopify_domain_for_api(shop_domain)
        async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
            result = await session.execute(
                select(ConnectorConfig).where(ConnectorConfig.tenant_id == _tenant_uuid(tenant_id))
            )
            connector_rows = result.scalars().all()
            merchant_ids = {
                str((row.config or {}).get("merchant_id"))
                for row in connector_rows
                if (row.config or {}).get("shop_domain") == normalized_shop and (row.config or {}).get("merchant_id")
            }
            for merchant_id in merchant_ids:
                packets = (
                    await session.scalars(
                        select(C6ZSellerOnboardingPacketRow).where(
                            C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
                            C6ZSellerOnboardingPacketRow.merchant_id == merchant_id,
                            C6ZSellerOnboardingPacketRow.status.in_(
                                (
                                    "sync_ready",
                                    "synced",
                                    "authority_requested",
                                    "artifacts_cached",
                                )
                            ),
                        )
                    )
                ).all()
                for packet in packets:
                    packet.status = "cache_refresh_needed"
                    stale_packets_marked += 1
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
        "stale_packets_marked": stale_packets_marked,
        "next_action": "POST /api/v1/commerce/runtime/seller-agents/shopify/sync",
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
        if packet_row.tenant_id != tenant_id or evidence_row.tenant_id != tenant_id:
            raise HTTPException(404, "C6Z packet or connector evidence not found")
        packet = _row_to_packet(packet_row)
        evidence = _row_to_evidence(evidence_row)
        payload = build_grantex_authority_request_payload(
            onboarding_packet=packet,
            connector_evidence=evidence,
            expected_tenant_id=tenant_id,
        )
        env_resolution = resolve_env(GRANTEX_AUTHORITY_ENV_VARS)
        if not env_resolution.ready:
            packet_row.status = "blocked_grantex_unavailable"
            return {
                "status": "blocked_missing_grantex_env",
                "missing_env_vars": list(env_resolution.missing),
                "authority_request_payload": payload,
                "artifact_issuance_attempted": False,
                "allowed_to_execute": False,
                "non_authoritative_for_transaction": True,
            }
        packet_row.status = "authority_requested"
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
        cache_records = []
        for artifact in body.artifacts:
            record = build_cache_record_from_grantex_artifact(
                artifact,
                cached_at=now_iso,
                buyer_agent_id=body.buyer_agent_id,
            )
            if record.tenant_id != tenant_id:
                raise HTTPException(403, "Artifact tenant scope mismatch")
            cache_records.append(record)
            store_results.append(await repo.upsert(record))
        rejected = [result for result in store_results if result.get("stored") is not True]
        if rejected:
            raise HTTPException(
                status_code=422,
                detail={
                    "status": "artifact_cache_rejected",
                    "records_stored": 0,
                    "records_rejected": len(rejected),
                    "store_results": store_results,
                    "allowed_to_execute": False,
                    "non_authoritative_for_transaction": True,
                },
            )
        if hasattr(session, "scalars"):
            for record in cache_records:
                packets = (
                    await session.scalars(
                        select(C6ZSellerOnboardingPacketRow).where(
                            C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
                            C6ZSellerOnboardingPacketRow.merchant_id == record.merchant_id,
                            C6ZSellerOnboardingPacketRow.seller_agent_id == record.seller_agent_id,
                        )
                    )
                ).all()
                for packet in packets:
                    packet.status = "artifacts_cached"
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
    payload, _, _ = await _answer_buyer_question_for_scope(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
        question=body.question,
        action_intent=body.action_intent,
        grantex_available=body.grantex_available,
    )
    return payload


@router.post("/bridges/web/ask")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.web.ask",
    rate_limit="commerce-buyer-session",
    idempotency="read-only-non-binding-bridge-answer",
    audit_event="commerce.runtime.bridge.web.ask",
)
async def ask_web_bridge(
    body: BridgeAskRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    return await _bridge_answer(body, tenant_id, default_channel="web")


@router.post("/bridges/openapi/ask")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.openapi.ask",
    rate_limit="commerce-buyer-session",
    idempotency="read-only-non-binding-bridge-answer",
    audit_event="commerce.runtime.bridge.openapi.ask",
)
async def ask_openapi_bridge(
    body: BridgeAskRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    return await _bridge_answer(body, tenant_id, default_channel="openapi")


@router.get("/bridges/openapi/schema")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.openapi.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.bridge.openapi.schema",
)
async def get_openapi_bridge_schema() -> dict[str, Any]:
    return _openapi_bridge_schema()


@router.get("/bridges/a2a/agent-card")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.a2a.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.bridge.a2a.agent_card",
)
async def get_commerce_a2a_agent_card() -> dict[str, Any]:
    return _commerce_a2a_agent_card()


@router.get("/bridges/surfaces")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.surfaces.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.bridge.surfaces",
)
async def get_buyer_surface_bridge_matrix() -> dict[str, Any]:
    return build_buyer_surface_bridge_matrix()


@router.post("/bridges/whatsapp/webhook")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.whatsapp.webhook",
    rate_limit="commerce-webhook",
    idempotency="whatsapp-message-id-or-body-hash",
    audit_event="commerce.runtime.bridge.whatsapp.webhook",
)
async def receive_whatsapp_bridge_webhook(
    request: Request,
    payload: dict[str, Any],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    env_resolution = resolve_env(WHATSAPP_BRIDGE_ENV_VARS)
    if not env_resolution.ready:
        return _blocked_bridge("whatsapp", env_resolution.missing)
    raw_body = await request.body()
    if not verify_whatsapp_webhook_signature(
        raw_body,
        request.headers.get("x-hub-signature-256", ""),
        os.environ["WHATSAPP_APP_SECRET"],
    ):
        raise HTTPException(status_code=401, detail="WhatsApp webhook signature verification failed")
    body = _bridge_body_from_webhook_payload(payload, channel="whatsapp")
    return await _bridge_answer(body, tenant_id, default_channel="whatsapp")


@router.post("/bridges/telegram/webhook")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.bridges.telegram.webhook",
    rate_limit="commerce-webhook",
    idempotency="telegram-update-id-or-body-hash",
    audit_event="commerce.runtime.bridge.telegram.webhook",
)
async def receive_telegram_bridge_webhook(
    request: Request,
    payload: dict[str, Any],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    env_resolution = resolve_env(TELEGRAM_BRIDGE_ENV_VARS)
    if not env_resolution.ready:
        return _blocked_bridge("telegram", env_resolution.missing)
    if not verify_telegram_webhook_secret(
        request.headers.get("x-telegram-bot-api-secret-token"),
        os.environ["TELEGRAM_WEBHOOK_SECRET_TOKEN"],
    ):
        raise HTTPException(status_code=401, detail="Telegram webhook secret token verification failed")
    body = _bridge_body_from_webhook_payload(payload, channel="telegram")
    return await _bridge_answer(body, tenant_id, default_channel="telegram")


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


@router.get("/protocol-adapters")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.protocol_adapters.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.protocol_adapters.read",
)
async def get_protocol_adapter_payloads(
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    cache_records, products, _capabilities = await _load_runtime_scope(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
    )
    return generate_protocol_adapter_payloads(
        cache_records=cache_records,
        products=products,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        now_iso=_now_iso(),
    )


@router.get("/protocol-adapters/{surface}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.protocol_adapters.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.protocol_adapter.surface.read",
)
async def get_protocol_adapter_surface(
    surface: str,
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    payloads = await get_protocol_adapter_payloads(
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        tenant_id=tenant_id,
    )
    try:
        return select_protocol_adapter_payload(payloads, surface)
    except C6ZRuntimeValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/purchase/prepare")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.purchase.prepare",
    rate_limit="commerce-runtime-write",
    idempotency="buyer-merchant-product-variant-quantity",
    audit_event="commerce.runtime.purchase.prepare",
)
async def prepare_purchase_handoff(
    body: PurchasePreparationRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    cache_records, products, capabilities = await _load_runtime_scope(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
    )
    live_execution_env_enabled = os.environ.get("PLURAL_PINE_LIVE_EXECUTION_ENABLED", "").strip().lower() == "true"
    result = prepare_purchase_or_mandate_handoff(
        cache_records=cache_records,
        products=products,
        capability_evidence=capabilities,
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
        product_ref_or_query=body.product_ref_or_query,
        variant_id=body.variant_id,
        quantity=body.quantity,
        now_iso=_now_iso(),
        idempotency_key=body.idempotency_key,
        grantex_available=body.grantex_available,
        live_execution_enabled=body.live_execution_approved and live_execution_env_enabled,
    )
    return {
        **result.__dict__,
        "live_execution_requested": body.live_execution_approved,
        "live_execution_env_enabled": live_execution_env_enabled,
        "raw_provider_payload_stored": False,
        "allowed_to_execute": False,
        "no_payment_execution": True,
    }


@router.get("/pos/offline/readiness")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.pos.offline.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.pos.offline.readiness",
)
async def get_offline_pos_readiness() -> dict[str, Any]:
    real_provider_env = resolve_env(("OFFLINE_POS_PROVIDER_ID", "OFFLINE_POS_WEBHOOK_SECRET"))
    return {
        "status": "offline_pos_bridge_foundation_ready",
        "simulator": {
            "configured": True,
            "approved": True,
            "testable": True,
            "status": "ready",
        },
        "real_pos_provider": {
            "configured": real_provider_env.ready,
            "approved": False,
            "testable": real_provider_env.ready,
            "status": "configured_pending_approval" if real_provider_env.ready else "blocked_missing_credential",
            "missing_env_vars": list(real_provider_env.missing),
        },
        "supported_runtime_actions": [
            "build_offline_pos_handoff_packet",
            "intake_pos_confirmation_status",
            "reconcile_non_sensitive_pos_evidence_refs",
            "mark_inventory_or_artifact_refresh_required",
        ],
        "unsupported_runtime_actions": [
            "agent_order_creation",
            "agent_payment_capture",
            "raw_pos_payload_storage",
            "raw_payment_payload_storage",
            "universal_pos_vendor_support_claim",
        ],
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


@router.post("/pos/offline/handoffs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.pos.offline.handoffs.write",
    rate_limit="commerce-runtime-write",
    idempotency="buyer-session-store-product-pos",
    audit_event="commerce.runtime.pos.offline.handoff.create",
)
async def create_offline_pos_handoff(
    body: OfflinePosHandoffRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    cache_records, products, capabilities = await _load_runtime_scope(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
    )
    purchase = prepare_purchase_or_mandate_handoff(
        cache_records=cache_records,
        products=products,
        capability_evidence=capabilities,
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
        product_ref_or_query=body.product_ref_or_query,
        variant_id=body.variant_id,
        quantity=body.quantity,
        now_iso=_now_iso(),
        idempotency_key=body.idempotency_key,
        grantex_available=body.grantex_available,
        live_execution_enabled=False,
    )
    purchase_payload = {**purchase.__dict__}
    if purchase.prepared_handoff is None:
        return {
            "status": "blocked",
            "pos_handoff_created": False,
            "purchase_preparation": purchase_payload,
            "blocker": purchase.blocker,
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "non_authoritative_for_transaction": True,
        }
    try:
        packet = build_offline_pos_handoff_packet(
            purchase_preparation=purchase_payload,
            tenant_id=tenant_id,
            merchant_id=body.merchant_id,
            seller_agent_id=body.seller_agent_id,
            store_id=body.store_id,
            pos_location=body.pos_location,
            buyer_session_ref=body.buyer_session_ref,
            now_iso=_now_iso(),
            expiry_minutes=body.expiry_minutes,
            idempotency_key=body.idempotency_key,
        )
    except OfflinePosBridgeError as exc:
        return {
            "status": "blocked",
            "pos_handoff_created": False,
            "blocker": {
                "code": "offline_pos_handoff_packet_blocked",
                "action": str(exc),
            },
            "purchase_preparation": purchase_payload,
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "non_authoritative_for_transaction": True,
        }
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await session.get(C6ZOfflinePosHandoffPacketRow, packet["packet_id"])
        if row is None:
            row = C6ZOfflinePosHandoffPacketRow(packet_id=packet["packet_id"])
            session.add(row)
        _apply_pos_handoff(row, packet, body.buyer_agent_id)
    return {
        "status": "pos_handoff_packet_ready",
        "pos_handoff_created": True,
        "packet": packet,
        "purchase_preparation_status": purchase.status,
        "safe_buyer_wording": (
            "I can prepare this for in-store checkout. Store staff or the POS/provider must confirm final "
            "price, inventory, and payment."
        ),
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


@router.post("/pos/offline/confirmations")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.pos.offline.confirmations.write",
    rate_limit="commerce-webhook",
    idempotency="pos-confirmation-id",
    audit_event="commerce.runtime.pos.offline.confirmation",
)
async def receive_offline_pos_confirmation(
    body: OfflinePosConfirmationRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        packet_row = await session.get(C6ZOfflinePosHandoffPacketRow, body.packet_id)
        if packet_row is None or packet_row.tenant_id != tenant_id:
            raise HTTPException(404, "Offline POS handoff packet not found")
        packet = dict(packet_row.packet or {})
        try:
            confirmation = build_offline_pos_confirmation_intake(
                packet=packet,
                confirmation_status=body.confirmation_status,
                now_iso=_now_iso(),
                final_price=body.final_price,
                currency=body.currency,
                provider_pos_evidence_ref=body.provider_pos_evidence_ref,
                receipt_evidence_ref=body.receipt_evidence_ref,
                callback_verified=body.callback_verified,
                simulator_mode=False,
                inventory_refresh_required=body.inventory_refresh_required,
                artifact_refresh_required=body.artifact_refresh_required,
            )
        except OfflinePosBridgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        reconciliation = reconcile_offline_pos_confirmation(packet=packet, confirmation=confirmation)
        confirmation_row = await session.get(C6ZOfflinePosConfirmationRow, confirmation["confirmation_id"])
        if confirmation_row is None:
            confirmation_row = C6ZOfflinePosConfirmationRow(confirmation_id=confirmation["confirmation_id"])
            session.add(confirmation_row)
        _apply_pos_confirmation(confirmation_row, confirmation, reconciliation.__dict__)
        packet_row.status = "reconciled"
    return {
        "status": "pos_confirmation_reconciled",
        "confirmation": confirmation,
        "reconciliation": reconciliation.__dict__,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


@router.post("/pos/offline/simulator/confirm")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.pos.offline.simulator.write",
    rate_limit="commerce-runtime-write",
    idempotency="pos-simulator-confirmation",
    audit_event="commerce.runtime.pos.offline.simulator.confirm",
)
async def simulate_offline_pos_handoff_confirmation(
    body: OfflinePosSimulatorRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        packet_row = await session.get(C6ZOfflinePosHandoffPacketRow, body.packet_id)
        if packet_row is None or packet_row.tenant_id != tenant_id:
            raise HTTPException(404, "Offline POS handoff packet not found")
        packet = dict(packet_row.packet or {})
        confirmation = simulate_offline_pos_confirmation(
            packet=packet,
            now_iso=_now_iso(),
            confirmation_status=cast(Any, body.confirmation_status),
            final_price=body.final_price,
        )
        reconciliation = reconcile_offline_pos_confirmation(packet=packet, confirmation=confirmation)
        confirmation_row = await session.get(C6ZOfflinePosConfirmationRow, confirmation["confirmation_id"])
        if confirmation_row is None:
            confirmation_row = C6ZOfflinePosConfirmationRow(confirmation_id=confirmation["confirmation_id"])
            session.add(confirmation_row)
        _apply_pos_confirmation(confirmation_row, confirmation, reconciliation.__dict__)
        packet_row.status = "reconciled"
    return {
        "status": "pos_simulator_reconciled",
        "confirmation": confirmation,
        "reconciliation": reconciliation.__dict__,
        "simulator_mode": True,
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
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


async def _answer_buyer_question_for_scope(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
    question: str,
    action_intent: str,
    grantex_available: bool,
) -> tuple[dict[str, Any], Any, list[Any]]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        repo = DurableOacpArtifactCacheRepository(session)
        cache_records = await repo.list_for_scope(
            OacpArtifactCacheRepositoryQuery(
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                seller_agent_id=seller_agent_id,
                buyer_agent_id=buyer_agent_id,
            )
        )
        evidence_query = select(C6ZConnectorEvidenceRow).where(
            C6ZConnectorEvidenceRow.tenant_id == tenant_id,
            C6ZConnectorEvidenceRow.merchant_id == merchant_id,
        )
        if seller_agent_id:
            evidence_query = evidence_query.where(C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id)
        evidence_rows = (await session.scalars(evidence_query.order_by(C6ZConnectorEvidenceRow.synced_at.desc()))).all()
        products: list[dict[str, Any]] = []
        for evidence_row in evidence_rows:
            products.extend(list(evidence_row.products or []))
        answer = answer_product_question_from_cache(
            cache_records=cache_records,
            products=products,
            question=question,
            now_iso=_now_iso(),
            grantex_available=grantex_available,
            action_intent=cast(Any, action_intent),
        )
    return (
        {
            "status": answer.status,
            "answer": answer.answer,
            "source_label": answer.source_label,
            "freshness_label": answer.freshness_label,
            "refusal_reason": answer.refusal_reason,
            "matched_products": list(answer.matched_products),
            "allowed_to_execute": False,
            "non_authoritative_for_transaction": True,
        },
        answer,
        list(cache_records),
    )


async def _load_runtime_scope(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        repo = DurableOacpArtifactCacheRepository(session)
        cache_records = await repo.list_for_scope(
            OacpArtifactCacheRepositoryQuery(
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                seller_agent_id=seller_agent_id,
                buyer_agent_id=buyer_agent_id,
            )
        )
        if not cache_records and buyer_agent_id:
            cache_records = await repo.list_for_scope(
                OacpArtifactCacheRepositoryQuery(
                    tenant_id=tenant_id,
                    merchant_id=merchant_id,
                    seller_agent_id=seller_agent_id,
                    buyer_agent_id=None,
                )
            )
        evidence_query = select(C6ZConnectorEvidenceRow).where(
            C6ZConnectorEvidenceRow.tenant_id == tenant_id,
            C6ZConnectorEvidenceRow.merchant_id == merchant_id,
        )
        capability_query = select(C6ZProviderCapabilityEvidenceRow).where(
            C6ZProviderCapabilityEvidenceRow.tenant_id == tenant_id,
            C6ZProviderCapabilityEvidenceRow.merchant_id == merchant_id,
        )
        if seller_agent_id:
            evidence_query = evidence_query.where(C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id)
            capability_query = capability_query.where(
                C6ZProviderCapabilityEvidenceRow.seller_agent_id == seller_agent_id
            )
        if buyer_agent_id:
            capability_query = capability_query.where(
                (C6ZProviderCapabilityEvidenceRow.buyer_agent_id == buyer_agent_id)
                | (C6ZProviderCapabilityEvidenceRow.buyer_agent_id.is_(None))
            )
        evidence_rows = (await session.scalars(evidence_query.order_by(C6ZConnectorEvidenceRow.synced_at.desc()))).all()
        capability_rows = (
            await session.scalars(capability_query.order_by(C6ZProviderCapabilityEvidenceRow.checked_at.desc()))
        ).all()
        products: list[dict[str, Any]] = []
        for evidence_row in evidence_rows:
            products.extend([dict(product) for product in list(evidence_row.products or [])])
        capabilities = [_row_to_capability_summary(row) for row in capability_rows]
    return list(cache_records), products, capabilities


async def _bridge_answer(
    body: BridgeAskRequest,
    tenant_id: str,
    *,
    default_channel: str,
) -> dict[str, Any]:
    channel = body.channel or default_channel
    payload, answer, cache_records = await _answer_buyer_question_for_scope(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        buyer_agent_id=body.buyer_agent_id,
        question=body.question,
        action_intent=body.action_intent,
        grantex_available=body.grantex_available,
    )
    contract = build_bridge_contract_response(
        channel=channel,
        answer=answer,
        cache_records=cache_records,
    )
    return {
        "status": payload["status"],
        "channel": contract.channel,
        "answer": contract.answer,
        "source_label": contract.source_label,
        "freshness_label": contract.freshness_label,
        "artifact_refs": list(contract.artifact_refs),
        "refusal_reason": contract.refusal_reason,
        "suggested_next_safe_action": contract.suggested_next_safe_action,
        "matched_products": payload["matched_products"],
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


def _blocked_bridge(channel: str, missing_env_vars: tuple[str, ...]) -> dict[str, Any]:
    return {
        "status": "blocked_missing_credentials",
        "channel": channel,
        "missing_env_vars": list(missing_env_vars),
        "credential_values_redacted": True,
        "external_message_sent": False,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


def _bridge_body_from_webhook_payload(payload: Mapping[str, Any], *, channel: str) -> BridgeAskRequest:
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    return BridgeAskRequest(
        merchant_id=str(payload.get("merchant_id") or message.get("merchant_id") or ""),
        seller_agent_id=str(payload.get("seller_agent_id") or message.get("seller_agent_id") or ""),
        buyer_agent_id=(
            None
            if not str(payload.get("buyer_agent_id") or message.get("buyer_agent_id") or "").strip()
            else str(payload.get("buyer_agent_id") or message.get("buyer_agent_id"))
        ),
        question=str(payload.get("question") or payload.get("text") or message.get("text") or ""),
        channel=channel,
        action_intent=str(payload.get("action_intent") or "non_binding_preview"),
        grantex_available=False,
    )


def _openapi_bridge_schema() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AgenticOrg OACP Buyer-Safe Bridge",
            "version": "2026-06-18",
            "description": (
                "Non-binding cached OACP product question bridge. No checkout, payment, order, hold, "
                "refund, return, shipping, mandate, or public discovery execution."
            ),
        },
        "paths": {
            "/api/v1/commerce/runtime/bridges/openapi/ask": {
                "post": {
                    "operationId": "askSellerCommerceAgent",
                    "summary": "Ask a buyer-safe product question from cached OACP artifacts.",
                    "x-oacp-non-enablement": {
                        "allowed_to_execute": False,
                        "non_authoritative_for_transaction": True,
                        "no_payment_execution": True,
                        "no_public_discovery_enablement": True,
                    },
                }
            },
            "/api/v1/commerce/runtime/protocol-adapters/{surface}": {
                "get": {
                    "operationId": "getProtocolAdapterPayload",
                    "summary": "Fetch a buyer-safe protocol adapter payload generated from cached OACP artifacts.",
                }
            },
            "/api/v1/commerce/runtime/purchase/prepare": {
                "post": {
                    "operationId": "preparePurchaseHandoff",
                    "summary": "Prepare a non-executing purchase or mandate handoff with blocker details.",
                }
            }
        },
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }


def _commerce_a2a_agent_card() -> dict[str, Any]:
    return {
        "name": "AgenticOrg Seller Commerce Agent Bridge",
        "protocol": "a2a-compatible-metadata",
        "capabilities": [
            "seller_product_question_answering_from_cached_oacp_artifacts",
            "seller_product_snapshot_listing",
            "source_freshness_labeling",
            "protocol_adapter_payload_read",
            "prepared_purchase_handoff_without_payment_execution",
            "final_commitment_refusal",
        ],
        "unsupported_capabilities": [
            "checkout",
            "payment",
            "order",
            "inventory_hold",
            "refund",
            "return_authorization",
            "shipping_label",
            "mandate_creation",
            "public_discovery_publish",
        ],
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
        "public_discovery_enabled": False,
    }


def _build_packet_from_body(body: SellerOnboardingPacketCreate, tenant_id: str) -> dict[str, Any]:
    connector_metadata = dict(body.connector_metadata)
    if body.shopify_shop_domain and not connector_metadata.get("shop_domain"):
        connector_metadata["shop_domain"] = body.shopify_shop_domain
    connector_metadata.setdefault("permitted_sync_actions", body.permitted_sync_actions or None)
    connector_metadata.setdefault("channel_capability_preferences", body.channel_capability_preferences or None)
    connector_metadata.setdefault("payment_mandate_rail_preference", body.payment_mandate_rail_preference)
    return build_seller_onboarding_packet(
        tenant_id=tenant_id,
        merchant_id=body.merchant_id,
        seller_agent_id=body.seller_agent_id,
        merchant_display_name=body.merchant_display_name,
        public_brand_profile=body.public_brand_profile,
        commerce_categories=body.commerce_categories,
        requested_grantex_authority_scope=body.requested_grantex_authority_scope
        or {
            "artifact_families": [
                "merchant_profile",
                "seller_agent_card",
                "connector_evidence",
                "catalog_snapshot",
                "offer_price_snapshot",
                "inventory_snapshot",
                "policy_scope",
                "public_discovery_state",
                "mandate_capability",
                "protocol_adapter",
                "authority_request_status",
            ]
        },
        artifact_cache_scope=body.artifact_cache_scope
        or {"tenant_id": tenant_id, "merchant_id": body.merchant_id, "seller_agent_id": body.seller_agent_id},
        source_freshness_policy=body.source_freshness_policy or {"max_age_seconds": 900},
        connector_metadata=connector_metadata,
    )


async def _resolve_submitted_shopify_credentials(
    body: ShopifyConnectorCredentialRequest,
) -> tuple[ShopifyCredentials, str, list[str]]:
    token = (body.admin_access_token or "").strip()
    granted_scopes: list[str] = []
    credential_source = "admin_access_token"
    if token and any((body.oauth_code, body.client_id, body.client_secret)):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_credential_mode_ambiguous",
                "message": "Send either admin_access_token or OAuth code material, not both.",
            },
        )
    if not token:
        token_data = await _exchange_shopify_oauth_code(body)
        token = str(token_data.get("access_token") or "").strip()
        granted_scopes = _scope_list(token_data.get("scope"))
        credential_source = "oauth_code_exchange"
    if not token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_token_required",
                "message": "Shopify setup requires an Admin API token or OAuth code exchange material.",
            },
        )
    return (
        ShopifyCredentials(
            shop_domain=_normalize_shopify_domain_for_api(body.shop_domain),
            admin_access_token=token,
            api_version=_validate_shopify_api_version(body.api_version),
        ),
        credential_source,
        granted_scopes,
    )


async def _exchange_shopify_oauth_code(body: ShopifyConnectorCredentialRequest) -> dict[str, Any]:
    missing = [
        label
        for value, label in (
            (body.oauth_code, "oauth_code"),
            (body.client_id, "client_id"),
            (body.client_secret, "client_secret"),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_oauth_material_required",
                "missing": missing,
                "message": "OAuth setup requires oauth_code, client_id, and client_secret.",
            },
        )
    form = {
        "client_id": str(body.client_id).strip(),
        "client_secret": str(body.client_secret).strip(),
        "code": str(body.oauth_code).strip(),
    }
    if body.redirect_uri:
        form["redirect_uri"] = body.redirect_uri.strip()
    shop_domain = _normalize_shopify_domain_for_api(body.shop_domain)
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        validate_public_url(
            token_url,
            allowed_schemes=("https",),
            allowed_domains=("myshopify.com",),
            require_dns=True,
        )
    except EgressValidationError as exc:
        raise HTTPException(status_code=400, detail="Shopify OAuth token endpoint is not allowed") from exc
    async with httpx.AsyncClient(
        timeout=20.0,
        transport=build_pinned_async_transport(require_dns=True),
    ) as client:
        response = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    body_json: dict[str, Any]
    try:
        body_json = cast(dict[str, Any], response.json())
    except ValueError:
        body_json = {"message": response.text[:200]}
    if response.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_oauth_code_rejected",
                "status_code": response.status_code,
                "message": _safe_external_error(body_json),
            },
        )
    if "access_token" not in body_json:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_oauth_token_missing",
                "message": "Shopify accepted the OAuth request but did not return an access token.",
            },
        )
    return body_json


async def _validate_shopify_read_credentials(credentials: ShopifyCredentials) -> None:
    client = ShopifyAdminGraphQLClient(credentials)
    try:
        await client.fetch_products(page_size=1, max_pages=1)
    except (httpx.HTTPError, C6ZRuntimeValidationError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "shopify_read_validation_failed",
                "message": "Shopify rejected the read-only product sync validation.",
                "external_validation_performed": True,
                "credential_values_redacted": True,
            },
        ) from exc


async def _resolve_shopify_credentials_for_packet(
    *,
    session: Any,
    tenant_id: UUID,
    packet: Mapping[str, Any],
) -> tuple[ShopifyCredentials, str]:
    row = await _load_shopify_connector_config_row(session, tenant_id, str(packet["merchant_id"]))
    if row is not None and row.credentials_encrypted:
        enc = row.credentials_encrypted.get("_encrypted") if isinstance(row.credentials_encrypted, dict) else None
        if not isinstance(enc, str) or not enc:
            raise HTTPException(status_code=424, detail="Shopify connector credential vault is empty")
        try:
            credential_data = json.loads(decrypt_for_tenant(enc))
        # enterprise-gate: broad-except-ok reason=shopify-credential-decrypt-failure-fails-closed-redacted
        except Exception as exc:
            raise HTTPException(
                status_code=424,
                detail={
                    "status": "blocked_shopify_credential_decrypt_failed",
                    "credential_values_redacted": True,
                    "allowed_to_execute": False,
                },
            ) from exc
        token = str(
            credential_data.get("admin_access_token")
            or credential_data.get("access_token")
            or "",
        ).strip()
        if not token:
            raise HTTPException(status_code=424, detail="Shopify connector token is missing")
        return (
            ShopifyCredentials(
                shop_domain=_normalize_shopify_domain_for_api(
                    credential_data.get("shop_domain")
                    or (row.config or {}).get("shop_domain")
                    or ((packet.get("connector_metadata_redacted") or {}).get("shop_domain"))
                    or "",
                ),
                admin_access_token=token,
                api_version=_validate_shopify_api_version(
                    str(credential_data.get("api_version") or (row.config or {}).get("api_version") or "2026-04")
                ),
            ),
            "tenant_connector_config",
        )

    env_resolution = resolve_env(SHOPIFY_ENV_VARS)
    if not env_resolution.ready:
        raise HTTPException(
            status_code=424,
            detail={
                "status": "blocked_missing_shopify_credentials",
                "missing_env_vars": list(env_resolution.missing),
                "expected_connector_config": _shopify_connector_config_name(str(packet["merchant_id"])),
                "external_validation_performed": False,
            },
        )
    return resolve_shopify_credentials(), "environment"


async def _load_shopify_connector_config_row(
    session: Any,
    tenant_id: UUID,
    merchant_id: str,
) -> ConnectorConfig | None:
    names = (_shopify_connector_config_name(merchant_id), "shopify")
    result = await session.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tenant_id,
            ConnectorConfig.connector_name.in_(names),
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return None
    merchant_specific = _shopify_connector_config_name(merchant_id)
    for row in rows:
        if row.connector_name == merchant_specific:
            return row
    for row in rows:
        config = row.config or {}
        if not config.get("merchant_id") or config.get("merchant_id") == merchant_id:
            return row
    return None


def _shopify_connector_config_name(merchant_id: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", merchant_id.strip()).strip("_").lower()
    if not suffix:
        suffix = "merchant"
    return f"commerce_shopify_{suffix[:72]}"


def _normalize_shopify_domain_for_api(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.removeprefix("https://").removeprefix("http://").strip("/")
    if not text or "/" in text or "." not in text or ":" in text:
        raise HTTPException(status_code=400, detail="Shopify shop_domain must be a host name")
    labels = text.split(".")
    if (
        len(labels) != 3
        or labels[1:] != ["myshopify", "com"]
        or not all(labels)
        or labels[0].startswith("-")
        or labels[0].endswith("-")
    ):
        raise HTTPException(
            status_code=400,
            detail="Shopify shop_domain must be a public *.myshopify.com host",
        )
    try:
        validate_public_url(
            f"https://{text}",
            allowed_schemes=("https",),
            require_dns=False,
            allowed_domains=("myshopify.com",),
        )
    except EgressValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="Shopify shop_domain must be a public *.myshopify.com host",
        ) from exc
    return text


def _validate_shopify_api_version(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        raise HTTPException(status_code=400, detail="Shopify api_version must look like YYYY-MM")
    return text


def _scope_list(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _safe_external_error(value: Any) -> str:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    lowered = text.lower()
    sensitive_markers = (
        "access_token",
        "authorization",
        "bearer ",
        "client_secret",
        "password",
        "secret",
        "shpat_",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return "<redacted external error body contained sensitive marker>"
    return text[:500]


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


def _apply_pos_handoff(
    row: C6ZOfflinePosHandoffPacketRow,
    packet: Mapping[str, Any],
    buyer_agent_id: str | None,
) -> None:
    row.tenant_id = str(packet["tenant_id"])
    row.merchant_id = str(packet["merchant_id"])
    row.seller_agent_id = str(packet["seller_agent_id"])
    row.buyer_agent_id = buyer_agent_id
    row.buyer_session_ref = str(packet["buyer_session_ref"])
    row.store_id = str(packet["store_id"])
    row.pos_location = dict(packet.get("pos_location") or {})
    row.packet = dict(packet)
    row.status = str(packet["status"])
    row.expires_at = _parse_dt(str((packet.get("freshness_timestamps") or {})["expires_at"]))
    row.idempotency_key = str(packet["idempotency_key"])
    row.raw_payload_stored = False
    row.raw_payment_payload_stored = False
    row.no_payment_execution = True
    row.no_order_creation = True
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True


def _apply_pos_confirmation(
    row: C6ZOfflinePosConfirmationRow,
    confirmation: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
) -> None:
    row.packet_id = str(confirmation["packet_id"])
    row.tenant_id = str(confirmation["tenant_id"])
    row.merchant_id = str(confirmation["merchant_id"])
    row.seller_agent_id = str(confirmation["seller_agent_id"])
    row.store_id = str(confirmation["store_id"])
    row.confirmation_status = str(confirmation["confirmation_status"])
    row.callback_verified = bool(confirmation["callback_verified"])
    row.simulator_mode = bool(confirmation["simulator_mode"])
    row.confirmation = dict(confirmation)
    row.reconciliation = dict(reconciliation)
    row.provider_pos_evidence_ref = (
        None
        if confirmation.get("provider_pos_evidence_ref") is None
        else str(confirmation["provider_pos_evidence_ref"])
    )
    row.receipt_evidence_ref = (
        None if confirmation.get("receipt_evidence_ref") is None else str(confirmation["receipt_evidence_ref"])
    )
    row.inventory_refresh_required = bool(confirmation["inventory_refresh_required"])
    row.artifact_refresh_required = bool(confirmation["artifact_refresh_required"])
    row.confirmed_at = _parse_dt(str(confirmation["confirmed_at"]))
    row.raw_payload_stored = False
    row.raw_payment_payload_stored = False
    row.no_payment_execution = True
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True


def _row_to_packet(row: C6ZSellerOnboardingPacketRow) -> dict[str, Any]:
    connector_metadata = row.connector_metadata_redacted or {}
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
        "connector_metadata_redacted": connector_metadata,
        "shopify_shop_domain": connector_metadata.get("shop_domain"),
        "permitted_sync_actions": connector_metadata.get("permitted_sync_actions") or [],
        "channel_capability_preferences": connector_metadata.get("channel_capability_preferences") or {},
        "payment_mandate_rail_preference": (
            connector_metadata.get("payment_mandate_rail_preference") or "plural_pine_p3p"
        ),
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


def _row_to_capability_summary(row: C6ZProviderCapabilityEvidenceRow) -> dict[str, Any]:
    return {
        "evidence_id": row.evidence_id,
        "tenant_id": row.tenant_id,
        "merchant_id": row.merchant_id,
        "seller_agent_id": row.seller_agent_id,
        "buyer_agent_id": row.buyer_agent_id,
        "provider": row.provider,
        "capability_type": row.capability_type,
        "result_status": row.result_status,
        "checked_at": row.checked_at.isoformat().replace("+00:00", "Z"),
        "expires_at": row.expires_at.isoformat().replace("+00:00", "Z"),
        "redacted_evidence_ref": row.redacted_evidence_ref,
        "provider_environment": row.provider_environment,
        "external_validation_performed": row.external_validation_performed,
        "missing_env_vars": row.missing_env_vars or [],
        "raw_payload_stored": row.raw_payload_stored,
        "no_payment_execution": row.no_payment_execution,
        "no_live_provider_enablement": row.no_live_provider_enablement,
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
