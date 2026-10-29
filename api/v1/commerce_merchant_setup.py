# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant setup: the steps a real merchant takes before turning on Sell everywhere.

Merchant (``commerce.merchant_config.write`` or tenant admin), under ``/api/v1/commerce/runtime``:

* ``GET /merchant-configs`` - the tenant's merchants (pick one; no hand-typed id).
* ``POST /merchant-setup`` - create a merchant from a shop name; the id is generated server-side.
* ``GET /merchant-configs/{merchant_id}/setup`` - where each step stands: catalog, policies,
  order limits, and what is still missing before Sell everywhere can be turned on.
* ``POST /merchant-configs/{merchant_id}/catalog/upload`` - a CSV or JSON catalog
  (``core.commerce.catalog_upload``) becomes the merchant's connector evidence.
* ``POST /merchant-configs/{merchant_id}/catalog/sync`` - read the connected Shopify store
  again (or the stored upload).
* ``PUT /merchant-configs/{merchant_id}/setup/policies`` - returns, shipping and store pickup.
* ``PUT /merchant-configs/{merchant_id}/setup/order-limits`` - the per-order and daily maximums
  the commerce arbiter enforces (``provider_policy.arbiter``); nothing is defaulted silently.

Shopify (public, no session):

* ``POST /webhooks/shopify/{tenant_id}/{path_token}`` - a merchant's product webhooks. The path
  token names the merchant; ``X-Shopify-Hmac-Sha256`` must verify with that merchant's stored
  webhook secret. Every refusal gets the same 401.
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from api.v1 import commerce_runtime as runtime
from api.v1.commerce_runtime import require_merchant_commerce_config_write
from core.commerce.c6z_runtime_vertical import (
    CATALOG_UPLOAD_SOURCE_SYSTEM,
    SHOPIFY_SOURCE_SYSTEM,
    contains_private_or_executable_value,
)
from core.commerce.catalog_upload import CatalogUploadError, currency_exponent, parse_catalog_upload
from core.commerce.ledger import record_commerce_event
from core.commerce.oacp_merchant_config import (
    MerchantCommerceConfigError,
    arbiter_limits_complete,
    merchant_config_id,
    merchant_config_readiness,
    normalize_merchant_commerce_config,
)
from core.database import get_tenant_session
from core.models.commerce_c6z_runtime import (
    C6ZConnectorEvidenceRow,
    C6ZMerchantCommerceConfigRow,
    C6ZSellerOnboardingPacketRow,
)

logger = structlog.get_logger()

router = APIRouter(
    prefix="/commerce/runtime",
    tags=["Merchant Setup"],
    dependencies=[Depends(require_merchant_commerce_config_write)],
)
webhook_router = APIRouter(tags=["Merchant Setup"])

MAX_POLICY_TEXT = 1000
MAX_WEBHOOK_BODY_BYTES = 1_000_000
# Packet states a config save keeps (the same list the config upsert keeps).
_KEEP_PACKET_STATUSES = frozenset(
    {
        "sync_ready",
        "synced",
        "authority_requested",
        "artifacts_cached",
        "cache_refresh_needed",
        "blocked_missing_credentials",
        "blocked_grantex_unavailable",
    }
)


class CreateMerchantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=2, max_length=120)
    categories: list[str] = Field(default_factory=lambda: ["general"], min_length=1, max_length=10)


class CatalogUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["csv", "json"]
    content: str = Field(min_length=1, max_length=1_100_000)


class PoliciesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returns: str = Field(min_length=10, max_length=MAX_POLICY_TEXT)
    shipping: str = Field(min_length=10, max_length=MAX_POLICY_TEXT)
    # Empty: the shop offers no store pickup.
    pickup: str = Field(default="", max_length=MAX_POLICY_TEXT)


class OrderLimitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    # Major units as decimal text ("5000.00"), converted with the currency's minor-unit exponent.
    max_order_amount: str = Field(min_length=1, max_length=20)
    max_daily_amount: str = Field(min_length=1, max_length=20)
    max_quantity_per_order: int = Field(ge=1, le=10_000)


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    return runtime._tenant_uuid(tenant_id)


def _iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


# --- merchants ------------------------------------------------------------------------------


@router.get("/merchant-configs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.merchant_config.list",
)
async def list_merchant_configs(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        rows = (
            await session.scalars(
                select(C6ZMerchantCommerceConfigRow)
                .where(C6ZMerchantCommerceConfigRow.tenant_id == tenant_id)
                .order_by(C6ZMerchantCommerceConfigRow.created_at.desc())
                .limit(200)
            )
        ).all()
    merchants = [
        {
            "merchant_id": row.merchant_id,
            "seller_agent_id": row.seller_agent_id,
            "display_name": row.merchant_display_name,
            "catalog_source": _primary_source_type(row.source_connectors or []),
            "status": row.status,
        }
        for row in rows
    ]
    return {"merchants": merchants, "count": len(merchants)}


def _merchant_id_for(display_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")[:40].strip("-") or "shop"
    return f"{base}-{secrets.token_hex(3)}"


@router.post("/merchant-setup", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="new-merchant-id-per-call",
    audit_event="commerce.runtime.merchant_setup.create",
)
async def create_merchant(
    body: CreateMerchantRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    merchant_id = _merchant_id_for(body.display_name)
    seller_agent_id = runtime._default_seller_agent_id(merchant_id)
    try:
        config = normalize_merchant_commerce_config(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            merchant_display_name=body.display_name.strip(),
            public_brand_profile={"display_name": body.display_name.strip()},
            commerce_categories=[item.strip() for item in body.categories if item.strip()] or ["general"],
            source_connectors=[_upload_source(enabled=True)],
            buyer_channels={"web": True},
            payment_providers=[],
            offline_pos_stores=[],
            public_publishing={"enabled": False},
            source_freshness_policy={"max_age_seconds": 900},
            provider_policy={},
            status="configured",
        )
    except MerchantCommerceConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    config["config_id"] = merchant_config_id(tenant_id, merchant_id, seller_agent_id)
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = C6ZMerchantCommerceConfigRow(config_id=config["config_id"])
        session.add(row)
        runtime._apply_merchant_config(row, config, merchant_config_readiness(config))
        await runtime._provision_ucp_key_at_onboarding(row, tenant_id)
        await record_commerce_event(
            session,
            tenant_id=tenant_id,
            event="commerce.runtime.merchant_setup.create",
            outcome="success",
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=None,
            payload={"display_name": config["merchant_display_name"], "categories": config["commerce_categories"]},
            resource_id=config["config_id"],
        )
    return await setup_view(tenant_id, merchant_id)


# --- the setup view -------------------------------------------------------------------------


def _upload_source(*, enabled: bool) -> dict[str, Any]:
    return {
        "connector_key": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}:catalog",
        "connector_type": CATALOG_UPLOAD_SOURCE_SYSTEM,
        "store_id": "uploaded_catalog",
        "mode": "read_only",
        "enabled": enabled,
        "credential_custody": "not_required",
        "source_of_record": "merchant catalog upload",
        "sync_enabled": False,
    }


def _primary_source_type(sources: Sequence[Any]) -> str | None:
    for item in sources:
        if isinstance(item, Mapping) and item.get("enabled") is True:
            return str(item.get("connector_type") or "") or None
    return None


async def _config_row(session: Any, tenant_id: str, merchant_id: str) -> C6ZMerchantCommerceConfigRow:
    row = (
        await session.scalars(
            select(C6ZMerchantCommerceConfigRow)
            .where(
                C6ZMerchantCommerceConfigRow.tenant_id == tenant_id,
                C6ZMerchantCommerceConfigRow.merchant_id == merchant_id,
            )
            .order_by(C6ZMerchantCommerceConfigRow.created_at.desc())
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Merchant not found")
    return cast(C6ZMerchantCommerceConfigRow, row)


def policies_view(provider_policy: Mapping[str, Any] | None) -> dict[str, Any]:
    stored = (provider_policy or {}).get("merchant_policies")
    stored = stored if isinstance(stored, Mapping) else {}
    returns = str(stored.get("returns") or "")
    shipping = str(stored.get("shipping") or "")
    return {
        "status": "set" if returns and shipping else "missing",
        "returns": returns,
        "shipping": shipping,
        "pickup": str(stored.get("pickup") or ""),
    }


def order_limits_view(provider_policy: Mapping[str, Any] | None) -> dict[str, Any]:
    arbiter = (provider_policy or {}).get("arbiter")
    arbiter = arbiter if isinstance(arbiter, Mapping) else {}
    allowlist = arbiter.get("currency_allowlist")
    currency = str(allowlist[0]).upper() if isinstance(allowlist, list) and allowlist else None

    def major(name: str) -> str | None:
        value = arbiter.get(name)
        if currency is None or isinstance(value, bool) or not isinstance(value, int | str):
            return None
        try:
            minor = int(value)
        except ValueError:
            return None
        exponent = currency_exponent(currency)
        return str((Decimal(minor) / (Decimal(10) ** exponent)).quantize(Decimal(1).scaleb(-exponent)))

    return {
        "status": "set" if arbiter_limits_complete(provider_policy) else "missing",
        "currency": currency,
        "max_order_amount": major("max_order_amount_minor_units"),
        "max_daily_amount": major("max_daily_amount_minor_units"),
        "max_quantity_per_order": arbiter.get("max_quantity_per_order"),
    }


async def setup_view(tenant_id: str, merchant_id: str) -> dict[str, Any]:
    """Where each setup step stands, from the stored config, connector and evidence."""
    tid = _tenant_uuid(tenant_id)
    async with get_tenant_session(tid) as session:
        row = (
            await session.scalars(
                select(C6ZMerchantCommerceConfigRow)
                .where(
                    C6ZMerchantCommerceConfigRow.tenant_id == tenant_id,
                    C6ZMerchantCommerceConfigRow.merchant_id == merchant_id,
                )
                .order_by(C6ZMerchantCommerceConfigRow.created_at.desc())
            )
        ).first()
        if row is None:
            raise HTTPException(404, "Merchant not found")
        evidence = (
            await session.scalars(
                select(C6ZConnectorEvidenceRow)
                .where(
                    C6ZConnectorEvidenceRow.tenant_id == tenant_id,
                    C6ZConnectorEvidenceRow.merchant_id == merchant_id,
                    C6ZConnectorEvidenceRow.seller_agent_id == row.seller_agent_id,
                )
                .order_by(C6ZConnectorEvidenceRow.synced_at.desc())
            )
        ).first()
        connector = await runtime._load_shopify_connector_config_row(session, tid, merchant_id)
        packet = (
            await session.scalars(
                select(C6ZSellerOnboardingPacketRow)
                .where(
                    C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
                    C6ZSellerOnboardingPacketRow.merchant_id == merchant_id,
                    C6ZSellerOnboardingPacketRow.seller_agent_id == row.seller_agent_id,
                )
                .order_by(C6ZSellerOnboardingPacketRow.created_at.desc())
            )
        ).first()
    source = _primary_source_type(row.source_connectors or [])
    connector_config = (connector.config or {}) if connector is not None else {}
    evidence_matches = evidence is not None and evidence.source_system == source
    if source == SHOPIFY_SOURCE_SYSTEM and connector is None:
        catalog_status = "not_connected"
    elif not evidence_matches:
        catalog_status = "not_synced" if source == SHOPIFY_SOURCE_SYSTEM else "not_uploaded"
    elif packet is not None and packet.status == "cache_refresh_needed":
        catalog_status = "changed_in_store"
    else:
        catalog_status = "ready"
    catalog = {
        "status": catalog_status,
        "source": source,
        "shop_domain": connector_config.get("shop_domain") if source == SHOPIFY_SOURCE_SYSTEM else None,
        "product_count": evidence.product_count if evidence_matches and evidence is not None else 0,
        "last_synced_at": _iso(evidence.synced_at) if evidence_matches and evidence is not None else None,
        "currency": evidence.currency if evidence_matches and evidence is not None else None,
        "last_error": connector.sync_error if connector is not None else None,
        "webhook_status": (
            "configured" if connector_config.get("webhook_secret_configured") else "blocked_missing_webhook_secret"
        )
        if source == SHOPIFY_SOURCE_SYSTEM
        else None,
        "webhook_path": runtime.shopify_webhook_path(tenant_id, merchant_id)
        if source == SHOPIFY_SOURCE_SYSTEM
        else None,
    }
    policies = policies_view(row.provider_policy)
    limits = order_limits_view(row.provider_policy)
    missing = [
        name
        for name, done in (
            ("catalog", catalog_status in ("ready", "changed_in_store")),
            ("policies", policies["status"] == "set"),
            ("order_limits", limits["status"] == "set"),
        )
        if not done
    ]
    return {
        "merchant": {
            "merchant_id": row.merchant_id,
            "seller_agent_id": row.seller_agent_id,
            "display_name": row.merchant_display_name,
            "categories": list(row.commerce_categories or []),
        },
        "catalog": catalog,
        "policies": policies,
        "order_limits": limits,
        "missing": missing,
        "ready_to_turn_on": not missing,
    }


@router.get("/merchant-configs/{merchant_id}/setup")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="commerce.runtime.merchant_setup.read",
)
async def get_merchant_setup(merchant_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    return await setup_view(tenant_id, merchant_id)


# --- saving a config change (and the onboarding packet that follows the config) --------------


async def _save_config(
    session: Any,
    *,
    tenant_id: str,
    row: C6ZMerchantCommerceConfigRow,
    changes: Mapping[str, Any],
    event: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply ``changes`` to the stored config, validate it, and keep the onboarding packet in step."""
    config = runtime._row_to_merchant_config(row)
    config.update(changes)
    try:
        normalized = normalize_merchant_commerce_config(
            tenant_id=tenant_id,
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            merchant_display_name=str(config.get("merchant_display_name") or row.merchant_id),
            public_brand_profile=cast(Mapping[str, Any], config.get("public_brand_profile") or {}),
            commerce_categories=cast(Sequence[str], config.get("commerce_categories") or ["general"]),
            source_connectors=cast(Sequence[Mapping[str, Any]], config.get("source_connectors") or []),
            buyer_channels=cast(Mapping[str, Any], config.get("buyer_channels") or {"web": True}),
            payment_providers=cast(Sequence[Mapping[str, Any]], config.get("payment_providers") or []),
            offline_pos_stores=cast(Sequence[Mapping[str, Any]], config.get("offline_pos_stores") or []),
            public_publishing=cast(Mapping[str, Any], config.get("public_publishing") or {"enabled": False}),
            source_freshness_policy=cast(Mapping[str, Any], config.get("source_freshness_policy") or {}),
            provider_policy=cast(Mapping[str, Any], config.get("provider_policy") or {}),
            status=str(config.get("status") or "configured"),
        )
    except MerchantCommerceConfigError as exc:
        raise HTTPException(422, {"code": "invalid_setup", "message": str(exc)}) from exc
    normalized["config_id"] = row.config_id
    runtime._apply_merchant_config(row, normalized, merchant_config_readiness(normalized))
    packet: dict[str, Any] | None = None
    if _primary_source_type(normalized["source_connectors"]) in (SHOPIFY_SOURCE_SYSTEM, CATALOG_UPLOAD_SOURCE_SYSTEM):
        packet = runtime._build_packet_from_merchant_config(normalized)
        packet_row = await session.get(C6ZSellerOnboardingPacketRow, packet["packet_id"])
        previous_status = None if packet_row is None else packet_row.status
        if packet_row is None:
            packet_row = C6ZSellerOnboardingPacketRow(packet_id=packet["packet_id"])
            session.add(packet_row)
        runtime._apply_packet(packet_row, packet)
        if previous_status in _KEEP_PACKET_STATUSES:
            packet_row.status = previous_status
    await record_commerce_event(
        session,
        tenant_id=tenant_id,
        event=event,
        outcome="success",
        merchant_id=row.merchant_id,
        seller_agent_id=row.seller_agent_id,
        buyer_agent_id=None,
        payload=dict(payload),
        resource_id=row.config_id,
    )
    return {"config": normalized, "packet": packet}


def _with_source(sources: Sequence[Any], chosen: str) -> list[dict[str, Any]]:
    """The connectors with ``chosen`` first and enabled, every other catalog source disabled."""
    kept = [dict(item) for item in sources if isinstance(item, Mapping)]
    chosen_items = [item for item in kept if item.get("connector_type") == chosen]
    others = [item for item in kept if item.get("connector_type") != chosen]
    if not chosen_items:
        if chosen != CATALOG_UPLOAD_SOURCE_SYSTEM:
            raise HTTPException(409, {"code": "catalog_not_connected", "message": "Connect the store first."})
        chosen_items = [_upload_source(enabled=True)]
    for item in chosen_items:
        item["enabled"] = True
    for item in others:
        if item.get("connector_type") in (SHOPIFY_SOURCE_SYSTEM, CATALOG_UPLOAD_SOURCE_SYSTEM):
            item["enabled"] = False
    return chosen_items + others


# --- catalog --------------------------------------------------------------------------------


class ShopifyConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_domain: str = Field(min_length=3, max_length=255)
    # The Admin API access token of the merchant's own custom app (read_products, read_inventory).
    admin_access_token: str = Field(min_length=10, max_length=512)
    api_version: str = Field(default="2026-04", pattern=r"^\d{4}-\d{2}$")
    webhook_secret: str | None = Field(default=None, min_length=16, max_length=256)


@router.post("/merchant-configs/{merchant_id}/catalog/shopify")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="credential-write",
    idempotency="merchant-shopify-credential-upsert",
    audit_event="commerce.runtime.shopify.credentials.upsert",
)
async def connect_shopify(
    merchant_id: str,
    body: ShopifyConnectRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Store the merchant's own Shopify custom-app token (validated with one read), then read the catalog."""
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        await _config_row(session, tenant_id, merchant_id)
    connected = await runtime.upsert_shopify_connector_credentials(
        runtime.ShopifyConnectorCredentialRequest(
            merchant_id=merchant_id,
            shop_domain=body.shop_domain,
            api_version=body.api_version,
            admin_access_token=body.admin_access_token,
            webhook_secret=body.webhook_secret,
            validate_read=True,
        ),
        tenant_id=tenant_id,
    )
    sync_error: Any = None
    try:
        synced: Any = await sync_catalog(merchant_id, tenant_id=tenant_id)
    except HTTPException as exc:
        synced, sync_error = None, exc.detail
    return {
        "connection": {
            key: connected.get(key)
            for key in ("status", "shop_domain", "api_version", "webhook_status", "webhook_path")
        },
        "sync": synced["sync"] if synced else None,
        "sync_error": sync_error,
        "setup": await setup_view(tenant_id, merchant_id),
    }


@router.post("/merchant-configs/{merchant_id}/catalog/upload")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="connector-bulk",
    idempotency="catalog-digest-evidence-upsert",
    audit_event="commerce.runtime.catalog_upload.store",
)
async def upload_catalog(
    merchant_id: str,
    body: CatalogUploadRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    try:
        rows = parse_catalog_upload(body.content, body.format)
    except CatalogUploadError as exc:
        raise HTTPException(
            422, {"code": "catalog_upload_invalid", "message": str(exc), "errors": exc.errors}
        ) from exc
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _config_row(session, tenant_id, merchant_id)
        saved = await _save_config(
            session,
            tenant_id=tenant_id,
            row=row,
            changes={"source_connectors": _with_source(row.source_connectors or [], CATALOG_UPLOAD_SOURCE_SYSTEM)},
            event="commerce.runtime.merchant_setup.catalog_source",
            payload={"source": CATALOG_UPLOAD_SOURCE_SYSTEM},
        )
    packet = saved["packet"]
    assert packet is not None
    stored = await runtime.store_catalog_upload(tenant_id=tenant_id, packet=packet, rows=rows)
    return {"upload": stored, "setup": await setup_view(tenant_id, merchant_id)}


@router.post("/merchant-configs/{merchant_id}/catalog/sync")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="connector-bulk",
    idempotency="packet-id-and-source-snapshot",
    audit_event="commerce.runtime.merchant_setup.catalog_source",
)
async def sync_catalog(merchant_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _config_row(session, tenant_id, merchant_id)
        tid = _tenant_uuid(tenant_id)
        connector = await runtime._load_shopify_connector_config_row(session, tid, merchant_id)
        source = _primary_source_type(row.source_connectors or [])
        if connector is not None and source != SHOPIFY_SOURCE_SYSTEM:
            # The merchant connected Shopify after starting with an upload: Shopify becomes the source.
            source = SHOPIFY_SOURCE_SYSTEM
        if source is None:
            raise HTTPException(409, {"code": "catalog_not_connected", "message": "Connect a catalog first."})
        saved = await _save_config(
            session,
            tenant_id=tenant_id,
            row=row,
            changes={"source_connectors": _with_source(row.source_connectors or [], source)},
            event="commerce.runtime.merchant_setup.catalog_source",
            payload={"source": source},
        )
        evidence = (
            await session.scalars(
                select(C6ZConnectorEvidenceRow)
                .where(
                    C6ZConnectorEvidenceRow.tenant_id == tenant_id,
                    C6ZConnectorEvidenceRow.merchant_id == merchant_id,
                    C6ZConnectorEvidenceRow.source_system == CATALOG_UPLOAD_SOURCE_SYSTEM,
                )
                .order_by(C6ZConnectorEvidenceRow.synced_at.desc())
            )
        ).first()
        evidence_id = evidence.evidence_id if evidence is not None else None
    packet = saved["packet"]
    assert packet is not None
    if source == SHOPIFY_SOURCE_SYSTEM:
        result = await runtime._sync_shopify_packet(
            tenant_id=tenant_id, packet_id=str(packet["packet_id"]), page_size=50, max_pages=10, currency=None
        )
    elif evidence_id is not None:
        result = await runtime.reread_uploaded_catalog(tenant_id=tenant_id, evidence_id=evidence_id)
    else:
        raise HTTPException(409, {"code": "catalog_not_uploaded", "message": "Upload a catalog first."})
    return {"sync": result, "setup": await setup_view(tenant_id, merchant_id)}


# --- policies and order limits --------------------------------------------------------------


def _policy_text(value: str, field: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"<[^<>]*>", " ", value)).strip()
    if contains_private_or_executable_value(text):
        raise HTTPException(
            422,
            {
                "code": "invalid_setup",
                "message": f"The {field} text looks like it holds a credential, a token or a long link; "
                "write it as plain sentences.",
            },
        )
    return text


@router.put("/merchant-configs/{merchant_id}/setup/policies")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="replaces-merchant-policies",
    audit_event="commerce.runtime.merchant_setup.policies",
)
async def put_policies(
    merchant_id: str,
    body: PoliciesRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    returns = _policy_text(body.returns, "returns")
    shipping = _policy_text(body.shipping, "shipping")
    pickup = _policy_text(body.pickup, "pickup") if body.pickup.strip() else ""
    fulfillment = f"Shipping: {shipping}" + (f" Store pickup: {pickup}" if pickup else " No store pickup.")
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _config_row(session, tenant_id, merchant_id)
        brand = dict(row.public_brand_profile or {})
        brand["return_policy_summary"] = returns
        brand["fulfillment_policy_summary"] = fulfillment
        policy = runtime.merge_provider_policy(
            row.provider_policy or {},
            {
                "merchant_policies": {"returns": returns, "shipping": shipping, "pickup": pickup},
                "ucp": {"fulfillment": {"shipping": True, "pickup": bool(pickup)}},
            },
        )
        await _save_config(
            session,
            tenant_id=tenant_id,
            row=row,
            changes={"public_brand_profile": brand, "provider_policy": policy},
            event="commerce.runtime.merchant_setup.policies",
            payload={"pickup_offered": bool(pickup)},
        )
    return await setup_view(tenant_id, merchant_id)


def _minor_units(value: str, currency: str, field: str) -> int:
    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            422, {"code": "invalid_setup", "message": f"{field} must be an amount such as 5000.00"}
        ) from exc
    exponent = currency_exponent(currency)
    raw_exponent = amount.as_tuple().exponent
    if not amount.is_finite() or amount <= 0 or (isinstance(raw_exponent, int) and -raw_exponent > exponent):
        raise HTTPException(
            422,
            {"code": "invalid_setup", "message": f"{field} must be above zero with at most {exponent} decimals"},
        )
    return int((amount * (Decimal(10) ** exponent)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@router.put("/merchant-configs/{merchant_id}/setup/order-limits")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="commerce.runtime.merchant_config.write",
    rate_limit="commerce-runtime-write",
    idempotency="replaces-order-limits",
    audit_event="commerce.runtime.merchant_setup.order_limits",
)
async def put_order_limits(
    merchant_id: str,
    body: OrderLimitsRequest,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    currency = body.currency.upper()
    per_order = _minor_units(body.max_order_amount, currency, "The per-order maximum")
    daily = _minor_units(body.max_daily_amount, currency, "The daily maximum")
    if daily < per_order:
        raise HTTPException(
            422, {"code": "invalid_setup", "message": "The daily maximum must be at least the per-order maximum."}
        )
    limits = {
        "max_order_amount_minor_units": per_order,
        "max_daily_amount_minor_units": daily,
        "max_quantity_per_order": body.max_quantity_per_order,
        "currency_allowlist": [currency],
    }
    async with get_tenant_session(_tenant_uuid(tenant_id)) as session:
        row = await _config_row(session, tenant_id, merchant_id)
        policy = runtime.merge_provider_policy(row.provider_policy or {}, {"arbiter": limits})
        await _save_config(
            session,
            tenant_id=tenant_id,
            row=row,
            changes={"provider_policy": policy},
            event="commerce.runtime.merchant_setup.order_limits",
            payload=limits,
        )
    return await setup_view(tenant_id, merchant_id)


# --- Shopify webhooks (public) --------------------------------------------------------------


@webhook_router.post("/webhooks/shopify/{tenant_id}/{path_token}")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public:webhooks.shopify",
    rate_limit="provider-webhook",
    idempotency="shopify-webhook-id-or-body-hash",
    audit_event="commerce.runtime.shopify.product_update_webhook",
    public_reason="per-merchant-path-token-plus-shopify-hmac-verified-with-the-merchant-stored-secret",
)
async def receive_shopify_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    path_token: str = Path(pattern=r"^[0-9a-f]{32}$"),
) -> JSONResponse:
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY_BYTES:
        return JSONResponse(status_code=413, content={"status": "too_large"})
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        return JSONResponse(status_code=413, content={"status": "too_large"})
    result = await runtime.receive_shopify_webhook_for_path(
        tenant_id=str(tenant_id), path_token=path_token, raw_body=raw_body, headers=request.headers
    )
    if result is None:
        # One answer for every refusal: the caller learns nothing about the tenant or merchant.
        logger.info("shopify_webhook_not_verified")
        return JSONResponse(status_code=401, content={"status": "not_verified"})
    return JSONResponse(
        status_code=200,
        content={"status": "received", "stale_packets_marked": result["stale_packets_marked"]},
    )
