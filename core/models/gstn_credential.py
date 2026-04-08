"""GSTN Credential Vault -- encrypted portal login storage.

Stores GSTN portal credentials per company with Fernet encryption
(AES-128-CBC) for the password field.  The encryption key is referenced
by name so it can be rotated via GCP Secret Manager without re-encrypting
all rows at once.

When gstn_auto_upload is enabled on the company, agents use these
credentials to auto-upload generated JSON files to the GSTN portal.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class GSTNCredential(BaseModel):
    __tablename__ = "gstn_credentials"
    __table_args__ = (
        UniqueConstraint("company_id", "portal_type", name="uq_gstn_cred_company"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True
    )

    gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_ref: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default"
    )

    portal_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="gstn"
    )  # gstn | income_tax | epfo

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    tenant = relationship("Tenant")
    company = relationship("Company", backref="gstn_credentials")
