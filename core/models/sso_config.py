"""Per-tenant SSO configuration.

Each tenant can plug in its own identity provider. We support OIDC first
(SAML is planned — see docs/adr/0004-sso-oidc-first.md). A single tenant
may have multiple providers configured (e.g., Okta for employees and
Google Workspace for contractors).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import BaseModel


class SSOConfig(BaseModel):
    __tablename__ = "sso_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_key", name="uq_sso_tenant_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Client-facing identifier used in /api/v1/auth/sso/{provider_key}/...
    provider_key: Mapped[str] = mapped_column(String(50), nullable=False)

    # "oidc" or "saml"
    provider_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Display name (shown on the login button)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # OIDC-specific config (issuer URL, client_id, client_secret_ref, scopes)
    # SAML-specific config (idp_metadata_url, sp_entity_id, x509_cert, ...)
    # Full schema documented in docs/SSO_CONFIGURATION.md
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # When enabled=False, the provider is visible in admin but not usable
    # for login — useful for dry-running a migration.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Just-in-time provisioning — auto-create users on first login.
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Default role for JIT-provisioned users ("analyst" is the safest default).
    default_role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="analyst"
    )

    # Restrict login to these email domains (empty list = any domain).
    allowed_domains: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
