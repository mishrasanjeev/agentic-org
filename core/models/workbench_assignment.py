# SPDX-License-Identifier: Apache-2.0
"""A workbench assigned to a user by an administrator (``core/workbench/assignments.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class WorkbenchAssignment(BaseModel):
    """One user, one workbench. Row-level security: tenant-scoped (``v6z65_workbench_assignments``)."""

    __tablename__ = "workbench_assignments"
    __table_args__ = (
        Index("ux_workbench_assignments_tenant_user_bench", "tenant_id", "user_id", "workbench", unique=True),
        Index("ix_workbench_assignments_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workbench: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
