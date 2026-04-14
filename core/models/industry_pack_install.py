"""ORM model for industry_pack_installs.

Mirror of the table created in ``core.database.init_db()``. Existed
for a long time in raw SQL only; this model lets ``BaseModel.metadata
.create_all`` produce the same schema for hermetic tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class IndustryPackInstall(BaseModel):
    __tablename__ = "industry_pack_installs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    pack_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    installed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    agent_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    workflow_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
