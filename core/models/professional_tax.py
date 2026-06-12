"""Professional Tax models for CA firm client compliance workflows."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class ProfessionalTaxRegistration(BaseModel):
    __tablename__ = "professional_tax_registrations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "state_code",
            name="uq_pt_registration_tenant_company_state",
        ),
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
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    registration_number: Mapped[str] = mapped_column(String(100), nullable=False)
    employer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portal_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    last_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    tenant = relationship("Tenant")
    company = relationship("Company", backref="professional_tax_registrations")


class ProfessionalTaxReturn(BaseModel):
    __tablename__ = "professional_tax_returns"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "state_code",
            "filing_period",
            name="uq_pt_return_tenant_company_state_period",
        ),
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
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("professional_tax_registrations.id"),
        nullable=True,
        index=True,
    )
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    filing_period: Mapped[str] = mapped_column(String(20), nullable=False)
    employer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_wages: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    pt_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interest: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    penalty: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    total_payable: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    challan_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    acknowledgement_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prepared_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    portal_response: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), onupdate=func.now(), nullable=True
    )

    tenant = relationship("Tenant")
    company = relationship("Company", backref="professional_tax_returns")
    registration = relationship("ProfessionalTaxRegistration")
