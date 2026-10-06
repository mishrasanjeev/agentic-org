# SPDX-License-Identifier: Apache-2.0
"""Evaluation datasets and their versions (``core/evals/datasets.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class EvalDataset(BaseModel):
    """A named set of reference cases a tenant keeps, as a sequence of versions.

    ``latest_version`` and ``case_count`` describe the newest version, so the
    list does not read the cases. Archived, never deleted.

    Row-level security: tenant-scoped (``v6z44_eval_datasets``).
    """

    __tablename__ = "eval_datasets"
    __table_args__ = (
        # One live dataset per name in a tenant, whatever the letter case.
        Index(
            "ux_eval_datasets_tenant_name",
            "tenant_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class EvalDatasetVersion(BaseModel):
    """One immutable version of a dataset: its cases and their content hash.

    Row-level security: tenant-scoped (``v6z44_eval_datasets``).
    """

    __tablename__ = "eval_dataset_versions"
    __table_args__ = (
        # Leads with the foreign key: the versions of one dataset, one row per number.
        Index("ux_eval_dataset_versions_dataset_version", "dataset_id", "version", unique=True),
        Index("ix_eval_dataset_versions_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    cases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
