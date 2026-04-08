"""Compliance Deadline model -- statutory filing deadlines per company.

Tracks GST/TDS/PF/ESI filing due dates and whether email alerts
have been sent (7-day and 1-day warnings).  The compliance cron
job queries this table daily to send pending alerts.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class ComplianceDeadline(BaseModel):
    __tablename__ = "compliance_deadlines"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "deadline_type",
            "filing_period",
            name="uq_deadline_company_type_period",
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

    deadline_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # gstr1 | gstr3b | gstr9 | tds_26q | tds_24q | pf_ecr | esi_return
    filing_period: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # e.g. "2026-03" or "2026-Q4"
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Alert tracking
    alert_7d_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alert_1d_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Filing status
    filed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    filed_at: Mapped[datetime | None] = mapped_column(
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
    company = relationship("Company", backref="compliance_deadlines")
