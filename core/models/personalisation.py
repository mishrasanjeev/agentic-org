# SPDX-License-Identifier: Apache-2.0
"""Consents, profiles, rules and render events of personalisation (``core/personalisation/service.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class PersonalisationConsent(BaseModel):
    """A subject's consent for one purpose: the current record. Row-level security: tenant-scoped, forced."""

    __tablename__ = "personalisation_consents"
    __table_args__ = (
        Index("ux_personalisation_consents_tenant_subject_purpose", "tenant_id", "subject_ref", "purpose", unique=True),
        CheckConstraint("status IN ('granted', 'withdrawn')", name="ck_personalisation_consents_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # the tenant's customer reference
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="granted")  # granted | withdrawn
    granted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    evidence: Mapped[str] = mapped_column(String(500), nullable=False, default="")  # where it was captured
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class PersonalisationProfile(BaseModel):
    """A subject's attributes, kept encrypted for the tenant. Row-level security: tenant-scoped, forced."""

    __tablename__ = "personalisation_profiles"
    __table_args__ = (Index("ux_personalisation_profiles_tenant_subject", "tenant_id", "subject_ref", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)  # {"_encrypted": ...}
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class PersonalisationRule(BaseModel):
    """A rule: for a purpose, conditions on attributes and the variant they select. Row-level security: forced."""

    __tablename__ = "personalisation_rules"
    __table_args__ = (
        Index("ux_personalisation_rules_tenant_name", "tenant_id", "name", unique=True),
        Index("ix_personalisation_rules_tenant_purpose", "tenant_id", "purpose", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # lower is tried first
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    conditions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    variant: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_attributes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class PersonalisationEvent(BaseModel):
    """One render or refusal: the consent, the rule, the attribute names used, a hash of the output. Forced RLS."""

    __tablename__ = "personalisation_events"
    __table_args__ = (
        Index("ix_personalisation_events_consent", "consent_id"),
        Index("ix_personalisation_events_rule", "rule_id"),
        Index("ix_personalisation_events_tenant_subject", "tenant_id", "subject_ref", "created_at"),
        CheckConstraint("outcome IN ('rendered', 'refused')", name="ck_personalisation_events_outcome"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personalisation_consents.id", ondelete="SET NULL"), nullable=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personalisation_rules.id", ondelete="SET NULL"), nullable=True
    )
    attributes_used: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)  # names, never values
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # rendered | refused
    refusal: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
