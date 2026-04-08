"""Company model -- sub-tenant entity within a CA firm tenant.

A CA firm (tenant) manages multiple client companies.  Each company
has its own GSTIN, PAN, DSC, bank details, Tally bridge config, and
compliance settings.  Row-Level Security isolates companies within a
tenant, and the nullable company_id FK on agents / workflows / audit
lets every record be scoped to a specific client company.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class Company(BaseModel):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "gstin", name="uq_company_tenant_gstin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Tax / registration identifiers
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)  # nullable for unregistered
    pan: Mapped[str] = mapped_column(String(10), nullable=False)
    tan: Mapped[str | None] = mapped_column(String(10), nullable=True)  # for TDS
    cin: Mapped[str | None] = mapped_column(String(21), nullable=True)  # registered cos
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)  # GST jurisdiction

    registered_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Financial year (India default: Apr-Mar)
    fy_start_month: Mapped[str] = mapped_column(String(2), nullable=False, default="04")
    fy_end_month: Mapped[str] = mapped_column(String(2), nullable=False, default="03")

    # Authorized signatory
    signatory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signatory_designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    signatory_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    compliance_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # DSC (Digital Signature Certificate)
    dsc_serial: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dsc_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Statutory registration numbers
    pf_registration: Mapped[str | None] = mapped_column(String(50), nullable=True)
    esi_registration: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pt_registration: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Primary bank details
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bank_ifsc: Mapped[str | None] = mapped_column(String(11), nullable=True)
    bank_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Tally bridge connection config: {bridge_url, bridge_id, company_name}
    tally_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # GST auto-file flag (default OFF for safety)
    gst_auto_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # v4.2.0: CA paid add-on fields
    subscription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="trial"
    )  # trial | active | expired
    client_health_score: Mapped[int | None] = mapped_column(
        nullable=True, default=100
    )  # 0-100 compliance health score
    document_vault_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    compliance_alerts_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # v4.3.0: GSTN auto-upload flag (flip when credentials are stored+verified)
    gstn_auto_upload: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Per-company user-role mapping: {"<user_id>": "<role>", ...}
    user_roles: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    tenant = relationship("Tenant", backref="companies")
