# SPDX-License-Identifier: Apache-2.0
"""Governed business cases and their state transitions (see ``core.cases``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

CASE_JSON = JSONB().with_variant(JSON(), "sqlite")
CASE_STATES = ("submitted", "in_progress", "awaiting_decision", "decided", "withdrawn", "failed")
_STATE_CHECK = "state IN ('submitted', 'in_progress', 'awaiting_decision', 'decided', 'withdrawn', 'failed')"


class GovernedCase(BaseModel):
    """One business application under review, with every document the reference agents produced.

    ``state`` follows ``core.cases.states``; ``version`` guards concurrent transitions. The
    documents (``memo``, ``policy_result``, ``screening_results``, ``screening_dispositions``,
    ``ownership_graph``) are the published domain schemas; ``agent_records`` holds each agent run's
    case record (prompt digests, policy inputs, tool-call hashes) for the evidence package.
    """

    __tablename__ = "governed_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_ref", name="uq_governed_cases_tenant_ref"),
        CheckConstraint(_STATE_CHECK, name="ck_governed_cases_state"),
        CheckConstraint("version >= 1", name="ck_governed_cases_version"),
        Index("ix_governed_cases_tenant_state_updated", "tenant_id", "state", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    case_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application: Mapped[dict[str, Any]] = mapped_column(CASE_JSON, nullable=False)
    subject: Mapped[dict[str, Any] | None] = mapped_column(CASE_JSON, nullable=True)
    memo: Mapped[dict[str, Any] | None] = mapped_column(CASE_JSON, nullable=True)
    policy_result: Mapped[dict[str, Any] | None] = mapped_column(CASE_JSON, nullable=True)
    ownership_graph: Mapped[dict[str, Any] | None] = mapped_column(CASE_JSON, nullable=True)
    screening_results: Mapped[list[Any]] = mapped_column(CASE_JSON, nullable=False, default=list)
    screening_dispositions: Mapped[list[Any]] = mapped_column(CASE_JSON, nullable=False, default=list)
    parties: Mapped[list[Any]] = mapped_column(CASE_JSON, nullable=False, default=list)
    agent_records: Mapped[list[Any]] = mapped_column(CASE_JSON, nullable=False, default=list)
    information_requests: Mapped[list[Any]] = mapped_column(CASE_JSON, nullable=False, default=list)
    decision: Mapped[dict[str, Any] | None] = mapped_column(CASE_JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class GovernedCaseTransition(BaseModel):
    """Append-only record of every state change of a governed case."""

    __tablename__ = "governed_case_transitions"
    __table_args__ = (
        Index("ix_governed_case_transitions_tenant_case", "tenant_id", "case_id", "created_at"),
        Index("ix_governed_case_transitions_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_cases.id", ondelete="CASCADE"), nullable=False
    )
    #: The case's ``version`` after this transition; orders the history.
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
