# SPDX-License-Identifier: Apache-2.0
"""A merchant's own domain for its agent surfaces (UCP profile and catalog).

Row-level security: tenant-scoped with ENABLE + FORCE RLS and a tenant policy;
a SELECT-only platform-context policy lets the status task and the operator
queue list rows across tenants (``v6z91_merchant_custom_domains``). Writes
always run in the owning tenant's session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel

CUSTOM_DOMAIN_STATUSES: tuple[str, ...] = (
    "pending_dns",
    "pending_mapping",
    "pending_certificate",
    "live",
    "failed",
    "removed",
)
# A verified host belongs to one row across every tenant.
CLAIMED_STATUSES: tuple[str, ...] = ("pending_mapping", "pending_certificate", "live")


class MerchantCustomDomainRow(BaseModel):
    __tablename__ = "merchant_custom_domains"
    __table_args__ = (
        Index(
            "uq_merchant_custom_domains_scope_active",
            "tenant_id",
            "merchant_id",
            "seller_agent_id",
            unique=True,
            postgresql_where=text("status <> 'removed'"),
        ),
        Index(
            "uq_merchant_custom_domains_claimed_host",
            "host",
            unique=True,
            postgresql_where=text("status IN ('pending_mapping', 'pending_certificate', 'live')"),
        ),
        Index("ix_merchant_custom_domains_status", "status", "updated_at"),
        Index("ix_merchant_custom_domains_tenant", "tenant_id", text("created_at DESC")),
        CheckConstraint(
            "status IN ('pending_dns', 'pending_mapping', 'pending_certificate', 'live', 'failed', 'removed')",
            name="ck_merchant_custom_domains_status",
        ),
        CheckConstraint(
            "mapping_source IS NULL OR mapping_source IN ('operator', 'cloud_run_api')",
            name="ck_merchant_custom_domains_mapping_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(160), nullable=False)
    seller_agent_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_dns")
    cname_target: Mapped[str] = mapped_column(String(253), nullable=False)
    mapping_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # When the merchant confirmed it added the mapping account as a Search
    # Console owner of its domain (Google requires it before a mapping exists).
    search_console_owner_confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    check_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    mapping_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    operator_notified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    mapping_created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    live_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
