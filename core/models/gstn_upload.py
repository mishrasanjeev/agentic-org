"""GSTN Upload model -- manual upload tracking for GSTN portal.

When auto-filing is disabled, agents generate the JSON file and
the user manually uploads it to the GSTN portal.  This table
tracks the generated file and its upload status.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class GSTNUpload(BaseModel):
    __tablename__ = "gstn_uploads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True
    )

    upload_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # gstr1_json | gstr3b_json | gstr9_json
    filing_period: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # e.g. "2026-03"
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="generated"
    )  # generated | downloaded | uploaded | acknowledged
    gstn_arn: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # GSTN acknowledgement reference number

    uploaded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    tenant = relationship("Tenant")
    company = relationship("Company", backref="gstn_uploads")
