# SPDX-License-Identifier: Apache-2.0
"""Provider residency attestations: an administrator's record that a provider may process data in a region.

The platform cannot know where a vendor hosts an endpoint or whether the vendor
has committed not to train on the institution's data. Both are facts an
administrator establishes from the contract and records here, per provider and
data region. With residency enforcement on, ``core.governance.residency`` refuses
a provider that has no active attestation for the tenant's region.

Row-level security: tenant-scoped (``v6z33_provider_attestations``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class ProviderAttestation(BaseModel):
    __tablename__ = "provider_residency_attestations"
    __table_args__ = (Index("ix_provider_attestations_tenant_active", "tenant_id", "revoked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Provider id as the credential resolver names it (gemini, openai, ragflow, ...).
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # Data region the attestation is for (IN, EU, US).
    data_region: Mapped[str] = mapped_column(String(8), nullable=False)
    # Processing for this provider stays inside the region.
    in_region: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The provider has committed in writing not to train on the institution's data.
    no_training: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Where the commitment is written down (contract clause, order form, policy reference).
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    attested_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
