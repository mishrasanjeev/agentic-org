# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk records: leads, per-channel consents, suppressions, sources, policy, attempts.

All tenant-scoped under forced row-level security (``v6z96_onboarding_desk``).
A lead's contact details are encrypted under the tenant key in
``contact_ciphertext``; ``email_hash`` and ``phone_hash`` are keyed hashes used
to match opt-outs, and the masked forms are what lists show.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

LEAD_STATUSES = ("new", "contacted", "engaged", "applying", "submitted", "closed", "opted_out")
CONSENT_STATUSES = ("granted", "withdrawn")
ATTEMPT_STATUSES = ("queued", "sent", "failed", "refused")


class OnboardingLeadSource(BaseModel):
    __tablename__ = "onboarding_desk_lead_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('webhook','form','csv','crm','manual')", name="ck_onboarding_sources_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class OnboardingLead(BaseModel):
    __tablename__ = "onboarding_desk_leads"
    __table_args__ = (
        Index("ix_onboarding_leads_tenant_created", "tenant_id", text("created_at DESC")),
        Index("ix_onboarding_leads_tenant_email_hash", "tenant_id", "email_hash"),
        Index("ix_onboarding_leads_tenant_phone_hash", "tenant_id", "phone_hash"),
        Index(
            "ix_onboarding_leads_tenant_next_contact",
            "tenant_id",
            "next_contact_at",
            postgresql_where=text("next_contact_at IS NOT NULL"),
        ),
        Index(
            "ux_onboarding_leads_source_ref",
            "tenant_id",
            "source_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
        ),
        CheckConstraint("country IN ('US','CA')", name="ck_onboarding_leads_country"),
        CheckConstraint("locale IN ('en','fr')", name="ck_onboarding_leads_locale"),
        CheckConstraint(
            "status IN ('new','contacted','engaged','applying','submitted','closed','opted_out')",
            name="ck_onboarding_leads_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    locale: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    merchant_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    contact_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_masked: Mapped[str | None] = mapped_column(String(260), nullable=True)
    phone_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    next_contact_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    contacted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class OnboardingConsent(BaseModel):
    __tablename__ = "onboarding_desk_consents"
    __table_args__ = (
        Index("ix_onboarding_consents_lead_channel", "tenant_id", "lead_id", "channel"),
        CheckConstraint("channel IN ('sms','email','whatsapp','voice')", name="ck_onboarding_consents_channel"),
        CheckConstraint("status IN ('granted','withdrawn')", name="ck_onboarding_consents_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="onboarding")
    wording: Mapped[str] = mapped_column(Text, nullable=False)
    wording_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    written: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="granted")
    withdrawn_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    withdrawn_via: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class OnboardingSuppression(BaseModel):
    __tablename__ = "onboarding_desk_suppressions"
    __table_args__ = (
        Index("ux_onboarding_suppressions_hash_channel", "tenant_id", "contact_hash", "channel", unique=True),
        CheckConstraint(
            "channel IN ('all','sms','email','whatsapp','voice')", name="ck_onboarding_suppressions_channel"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class OnboardingPolicy(BaseModel):
    __tablename__ = "onboarding_desk_policies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class OnboardingContactAttempt(BaseModel):
    __tablename__ = "onboarding_desk_contact_attempts"
    __table_args__ = (
        Index("ix_onboarding_attempts_lead_created", "tenant_id", "lead_id", text("created_at DESC")),
        CheckConstraint("status IN ('queued','sent','failed','refused')", name="ck_onboarding_attempts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="first_contact")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    refusal_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


APPLICATION_STATUSES = ("in_progress", "submitted", "withdrawn")
HANDOFF_STATUSES = ("open", "resolved")


class OnboardingApplication(BaseModel):
    """One application per lead (``v6z98_onboarding_applications``); answers and transcript are encrypted."""

    __tablename__ = "onboarding_desk_applications"
    __table_args__ = (
        Index("ux_onboarding_applications_lead", "tenant_id", "lead_id", unique=True),
        Index("ix_onboarding_applications_status", "tenant_id", "status", text("created_at DESC")),
        Index(
            "ux_onboarding_applications_case",
            "tenant_id",
            "case_ref",
            unique=True,
            postgresql_where=text("case_ref IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('in_progress','submitted','withdrawn')", name="ck_onboarding_applications_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    merchant_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    locale: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    answers_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_items: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    pending_item: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    #: The governed case opened from this application (``v6z99_onboarding_cases``).
    case_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    case_opened_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    case_error: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OnboardingDocument(BaseModel):
    """An applicant's uploaded document, encrypted, with the values it suggests (also encrypted)."""

    __tablename__ = "onboarding_desk_documents"
    __table_args__ = (Index("ix_onboarding_documents_application", "tenant_id", "application_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(100), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(16), nullable=False)
    suggestions_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class OnboardingHandoff(BaseModel):
    """A request to talk to a person; at most one open per application."""

    __tablename__ = "onboarding_desk_handoffs"
    __table_args__ = (
        Index(
            "ux_onboarding_handoffs_open",
            "tenant_id",
            "application_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_onboarding_handoffs_status", "tenant_id", "status", text("created_at DESC")),
        CheckConstraint("status IN ('open','resolved')", name="ck_onboarding_handoffs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    message_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
