"""C6Z Seller Commerce Agent runtime vertical records.

These tables hold AgenticOrg-owned runtime metadata for the internal vertical
demo. They store read-only Shopify evidence summaries and capability evidence
only; they are not transaction authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, TIMESTAMP, Boolean, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

C6Z_JSON = JSONB().with_variant(JSON(), "sqlite")


class C6ZSellerOnboardingPacketRow(BaseModel):
    __tablename__ = "commerce_c6z_seller_onboarding_packets"
    __table_args__ = (
        Index("ix_c6z_onboarding_tenant_id", "tenant_id"),
        Index("ix_c6z_onboarding_merchant_id", "merchant_id"),
        Index("ix_c6z_onboarding_seller_agent_id", "seller_agent_id"),
        Index("ix_c6z_onboarding_status", "status"),
    )

    packet_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    public_brand_profile: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    commerce_categories: Mapped[list[str]] = mapped_column(C6Z_JSON, nullable=False, default=list)
    connector_choice: Mapped[str] = mapped_column(String(64), nullable=False, default="shopify")
    connector_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="read_only")
    requested_grantex_authority_scope: Mapped[dict[str, Any]] = mapped_column(
        C6Z_JSON,
        nullable=False,
        default=dict,
    )
    artifact_cache_scope: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    source_freshness_policy: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    connector_metadata_redacted: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="received")
    no_payment_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_public_discovery_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


class C6ZConnectorEvidenceRow(BaseModel):
    __tablename__ = "commerce_c6z_connector_evidence_records"
    __table_args__ = (
        Index("ix_c6z_connector_evidence_tenant_id", "tenant_id"),
        Index("ix_c6z_connector_evidence_merchant_id", "merchant_id"),
        Index("ix_c6z_connector_evidence_seller_agent_id", "seller_agent_id"),
        Index("ix_c6z_connector_evidence_packet_id", "packet_id"),
        Index("ix_c6z_connector_evidence_synced_at", "synced_at"),
        Index("ix_c6z_connector_evidence_source_ref", "source_evidence_ref"),
    )

    evidence_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    packet_id: Mapped[str] = mapped_column(String(180), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, default="shopify")
    source_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="read_only")
    source_evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    products: Mapped[list[dict[str, Any]]] = mapped_column(C6Z_JSON, nullable=False, default=list)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    hmac_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_payment_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_public_discovery_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


class C6ZProviderCapabilityEvidenceRow(BaseModel):
    __tablename__ = "commerce_c6z_provider_capability_evidence"
    __table_args__ = (
        Index("ix_c6z_capability_tenant_id", "tenant_id"),
        Index("ix_c6z_capability_merchant_id", "merchant_id"),
        Index("ix_c6z_capability_seller_agent_id", "seller_agent_id"),
        Index("ix_c6z_capability_buyer_agent_id", "buyer_agent_id"),
        Index("ix_c6z_capability_provider", "provider"),
        Index("ix_c6z_capability_result_status", "result_status"),
        Index("ix_c6z_capability_expires_at", "expires_at"),
    )

    evidence_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="plural_pine")
    capability_type: Mapped[str] = mapped_column(String(80), nullable=False)
    result_status: Mapped[str] = mapped_column(String(64), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    redacted_evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provider_environment: Mapped[str] = mapped_column(String(64), nullable=False)
    external_validation_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    missing_env_vars: Mapped[list[str]] = mapped_column(C6Z_JSON, nullable=False, default=list)
    raw_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_payment_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_live_provider_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


class C6ZOfflinePosHandoffPacketRow(BaseModel):
    __tablename__ = "commerce_c6z_offline_pos_handoff_packets"
    __table_args__ = (
        Index("ix_c6z_pos_handoff_tenant_id", "tenant_id"),
        Index("ix_c6z_pos_handoff_merchant_id", "merchant_id"),
        Index("ix_c6z_pos_handoff_seller_agent_id", "seller_agent_id"),
        Index("ix_c6z_pos_handoff_buyer_session_ref", "buyer_session_ref"),
        Index("ix_c6z_pos_handoff_store_id", "store_id"),
        Index("ix_c6z_pos_handoff_status", "status"),
        Index("ix_c6z_pos_handoff_expires_at", "expires_at"),
    )

    packet_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    buyer_session_ref: Mapped[str] = mapped_column(String(180), nullable=False)
    store_id: Mapped[str] = mapped_column(String(160), nullable=False)
    pos_location: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    packet: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pos_handoff_packet_ready")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    raw_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payment_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_payment_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_order_creation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


class C6ZOfflinePosConfirmationRow(BaseModel):
    __tablename__ = "commerce_c6z_offline_pos_confirmations"
    __table_args__ = (
        Index("ix_c6z_pos_confirmation_tenant_id", "tenant_id"),
        Index("ix_c6z_pos_confirmation_packet_id", "packet_id"),
        Index("ix_c6z_pos_confirmation_merchant_id", "merchant_id"),
        Index("ix_c6z_pos_confirmation_status", "confirmation_status"),
        Index("ix_c6z_pos_confirmation_confirmed_at", "confirmed_at"),
    )

    confirmation_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    packet_id: Mapped[str] = mapped_column(String(180), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False)
    store_id: Mapped[str] = mapped_column(String(160), nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(64), nullable=False)
    callback_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulator_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmation: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    reconciliation: Mapped[dict[str, Any]] = mapped_column(C6Z_JSON, nullable=False, default=dict)
    provider_pos_evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    inventory_refresh_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    artifact_refresh_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    raw_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payment_payload_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_payment_execution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
