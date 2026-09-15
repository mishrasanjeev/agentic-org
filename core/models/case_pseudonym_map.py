# SPDX-License-Identifier: Apache-2.0
"""Encrypted per-case pseudonym maps (see ``core.pii.pseudonymiser``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

PSEUDONYM_JSON = JSONB().with_variant(JSON(), "sqlite")


class CasePseudonymMap(BaseModel):
    """Token-to-value map for one case, stored only as tenant-encrypted ciphertext.

    ``mapping_encrypted`` holds ``{"_encrypted": <encrypt_for_tenant output>}``;
    an empty object means the row was created but nothing has been written yet.
    """

    __tablename__ = "case_pseudonym_maps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", name="uq_case_pseudonym_maps_tenant_case"),
        CheckConstraint("entry_count >= 0", name="ck_case_pseudonym_maps_entry_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(String(200), nullable=False)
    mapping_encrypted: Mapped[dict[str, Any]] = mapped_column(PSEUDONYM_JSON, nullable=False, default=dict)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now())
