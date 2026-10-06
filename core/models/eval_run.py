# SPDX-License-Identifier: Apache-2.0
"""A stored evaluation run (``core/evals/runs.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class EvalRun(BaseModel):
    """What a run measured and what came out.

    The version and its hash, the model, the judges, the outcomes per case id
    and the metrics. Never an answer, an input or a judge's reason.

    Row-level security: tenant-scoped (``v6z45_eval_runs``).
    """

    __tablename__ = "eval_runs"
    __table_args__ = (
        # Leads with the foreign key: the runs of one dataset, newest first.
        Index("ix_eval_runs_dataset_created", "dataset_id", "created_at"),
        Index("ix_eval_runs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    judge_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    judges: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The prompt under test is kept by its hash and an optional label, not its text.
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The answer limit the run was made with: it bounds the answers and so the scores.
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    cases_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cases_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    avg_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
