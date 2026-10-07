# SPDX-License-Identifier: Apache-2.0
"""Provenance nodes and the steps between them (``core/lineage/provenance.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class LineageNode(BaseModel):
    """One thing kept or used, under its kind, reference and version. Row-level security: tenant-scoped, forced."""

    __tablename__ = "lineage_nodes"
    __table_args__ = (
        Index("ux_lineage_nodes_tenant_key", "tenant_id", "kind", "ref", "version", unique=True),
        Index("ix_lineage_nodes_tenant_kind_ref", "tenant_id", "kind", "ref"),
        Index("ix_lineage_nodes_tenant_source", "tenant_id", "source"),
        Index("ix_lineage_nodes_tenant_observed", "tenant_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ref: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class LineageStep(BaseModel):
    """What was done between two nodes, by which tool and parameters. Row-level security: tenant-scoped, forced."""

    __tablename__ = "lineage_steps"
    __table_args__ = (
        Index("ux_lineage_steps_tenant_edge", "tenant_id", "from_node", "to_node", "step", unique=True),
        Index("ix_lineage_steps_from_node", "from_node"),
        Index("ix_lineage_steps_to_node", "to_node"),
        Index("ix_lineage_steps_tenant_at", "tenant_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_node: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage_nodes.id", ondelete="CASCADE"), nullable=False
    )
    to_node: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lineage_nodes.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
