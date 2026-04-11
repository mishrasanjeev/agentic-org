"""Workflow variants — A/B testing infrastructure.

A WorkflowVariant is an alternate definition of a workflow. Traffic
is split by weight and the variant a given user sees is deterministic
(same user always hits the same variant, as long as weights don't change).

Example: Variants of "invoice_approval"
  variant_name    weight    definition (JSON workflow YAML)
  control         70        { "steps": [...] }
  faster_path     30        { "steps": [...] }

When a run is triggered, the engine calls
``pick_variant(workflow_id, subject_id)`` to decide which definition
to execute. Metrics roll up per variant so we can declare a winner.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class WorkflowVariant(BaseModel):
    __tablename__ = "workflow_variants"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "variant_name", name="uq_variant_workflow_name"
        ),
        CheckConstraint("weight BETWEEN 0 AND 100", name="ck_variant_weight_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Full workflow definition (same shape as workflow_definitions.definition)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Metrics collected from the runs
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set to False to retire a variant without deleting it.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
