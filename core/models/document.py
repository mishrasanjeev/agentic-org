"""Document ORM model with pgvector embedding."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import TIMESTAMP, BigInteger, Date, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class Document(BaseModel):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    # Legacy display name — kept for backward compat with S3-era rows.
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Legacy S3 pointers — nullable since v484_docs_kb_upload_cols: KB uploads
    # are metadata-only and carry no S3 artifact.
    s3_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # KB upload fields (added v484_docs_kb_upload_cols).
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready")
    # embedding column handled via raw SQL / pgvector extension
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
