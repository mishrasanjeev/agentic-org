"""Approval policy — configurable multi-step approval chains.

An ApprovalPolicy defines a named approval flow that can be attached to
a workflow, agent, or HITL item. Each policy has N ApprovalStep rows;
a step can be sequential or parallel, single-approver or quorum-based.

Example: "High-value invoice approval"
  Step 1 (sequential): role=cfo, 1 of 1
  Step 2 (parallel quorum): role=audit_committee, 2 of 3
  Step 3 (sequential): role=ceo, 1 of 1, condition="amount > 500000"

Policies are resolved at HITL creation time: we walk the steps, pick
the first whose condition matches (or has no condition), and set the
HITL item's assignee_role + quorum + step_index. When decide() is
called, we advance the state machine.

See docs/adr/0005-approval-policies.md for the full design.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import BaseModel


class ApprovalPolicy(BaseModel):
    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_approval_policy_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Scope: a policy can be tenant-global, workflow-specific, or agent-specific.
    # Only one of workflow_id / agent_id should be set.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        String(10), nullable=False, default="true"
    )  # kept as string for legacy-DB compat

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    steps = relationship(
        "ApprovalStep",
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="ApprovalStep.sequence",
    )


class ApprovalStep(BaseModel):
    __tablename__ = "approval_steps"
    __table_args__ = (
        UniqueConstraint("policy_id", "sequence", name="uq_step_policy_sequence"),
        CheckConstraint("quorum_required >= 1", name="ck_step_quorum_min"),
        CheckConstraint(
            "quorum_required <= quorum_total", name="ck_step_quorum_ordering"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_role: Mapped[str] = mapped_column(String(50), nullable=False)

    # 1-of-1 = single approver, 2-of-3 = quorum, etc.
    quorum_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quorum_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # "sequential" — this step must complete before the next starts.
    # "parallel"  — multiple parallel steps at the same sequence.
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="sequential"
    )

    # Optional condition — e.g., "amount > 500000".  Evaluated via the
    # workflow condition evaluator.  None means "always applies".
    condition: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Free-form metadata (e.g., notification template, SLA hours)
    step_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    policy = relationship("ApprovalPolicy", back_populates="steps")
