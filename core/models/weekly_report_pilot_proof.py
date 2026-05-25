"""Weekly Marketing Report pilot-proof ORM model (CMO-PROD-2).

Stores the strict CMO-PROD-1 verdict alongside the redacted evidence bundle
that produced it. Rows are append-only by convention (the persistence helper
inserts a new row per evaluation) so the table is effectively a verdict log
keyed by ``proof_id`` with newest-first lookup by tenant/company.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel, TenantMixin, TimestampMixin


class WeeklyReportPilotProof(BaseModel, TenantMixin, TimestampMixin):
    """Durable record of one weekly-marketing-report pilot-proof evaluation."""

    __tablename__ = "weekly_report_pilot_proofs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proof_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # tenant_id comes from TenantMixin; company_id is optional like
    # ReportSchedule because a tenant may run weekly reports per company.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    environment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    proof_status: Mapped[str] = mapped_column(String(32), nullable=False)
    production_claim_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    real_vendor_claim_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    readiness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluated_at: Mapped[datetime] = mapped_column(nullable=False)
    evidence_bundle: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verdict: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    blockers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    next_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    report_artifact_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    decision_audit_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        Index(
            "ix_weekly_report_pilot_proofs_tenant_evaluated",
            "tenant_id",
            "company_id",
            "evaluated_at",
        ),
    )
