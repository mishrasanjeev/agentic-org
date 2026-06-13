"""Durable OACP artifact cache record model.

The table stores only public-safe artifact and evidence references used by
AgenticOrg local cache evaluation. It is not transaction authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, TIMESTAMP, Boolean, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

OACP_CACHE_JSON = JSONB().with_variant(JSON(), "sqlite")


class OacpArtifactCacheRecordRow(BaseModel):
    __tablename__ = "oacp_artifact_cache_records"
    __table_args__ = (
        Index("ix_oacp_cache_tenant_id", "tenant_id"),
        Index("ix_oacp_cache_merchant_id", "merchant_id"),
        Index("ix_oacp_cache_seller_agent_id", "seller_agent_id"),
        Index("ix_oacp_cache_buyer_agent_id", "buyer_agent_id"),
        Index("ix_oacp_cache_artifact_id", "artifact_id"),
        Index("ix_oacp_cache_artifact_type", "artifact_type"),
        Index("ix_oacp_cache_expires_at", "expires_at"),
        Index("ix_oacp_cache_freshness_status", "freshness_status"),
        Index("ix_oacp_cache_revocation_status", "revocation_snapshot_status"),
    )

    cache_record_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(160), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_family: Mapped[str] = mapped_column(String(64), nullable=False)
    authority: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    merchant_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    seller_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    buyer_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_refs: Mapped[list[str]] = mapped_column(OACP_CACHE_JSON, nullable=False, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(OACP_CACHE_JSON, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    cached_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    revocation_snapshot_status: Mapped[str] = mapped_column(String(32), nullable=False)
    revocation_snapshot_age_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revocation_snapshot_observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ttl_policy: Mapped[dict[str, Any]] = mapped_column(OACP_CACHE_JSON, nullable=False, default=dict)
    ttl_policy_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    blocked_capabilities: Mapped[list[str]] = mapped_column(
        OACP_CACHE_JSON,
        nullable=False,
        default=list,
    )
    unsupported_capabilities: Mapped[list[str]] = mapped_column(
        OACP_CACHE_JSON,
        nullable=False,
        default=list,
    )
    verifier_result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_to_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    non_authoritative_for_transaction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_checkout_payment_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_live_provider_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    no_public_discovery_enablement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
