"""Branding API — tenant-level white-label configuration.

Public GET for the login page (unauthenticated — we look up by host).
Authenticated GET/PATCH for admins.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import async_session_factory
from core.models.branding import TenantBranding

logger = structlog.get_logger()

public_router = APIRouter(prefix="/branding", tags=["Branding"])
admin_router = APIRouter(prefix="/admin/branding", tags=["Branding"])


class BrandingOut(BaseModel):
    product_name: str
    logo_url: str | None = None
    favicon_url: str | None = None
    primary_color: str
    accent_color: str
    support_email: str | None = None
    footer_text: str | None = None
    custom_domain: str | None = None


class BrandingIn(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=100)
    logo_url: str | None = Field(None, max_length=500)
    favicon_url: str | None = Field(None, max_length=500)
    primary_color: str = Field("#7c3aed", pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str = Field("#1e293b", pattern=r"^#[0-9a-fA-F]{6}$")
    support_email: str | None = Field(None, max_length=255)
    footer_text: str | None = Field(None, max_length=500)
    custom_domain: str | None = Field(None, max_length=255)


_DEFAULT_BRANDING = BrandingOut(
    product_name="AgenticOrg",
    logo_url=None,
    favicon_url=None,
    primary_color="#7c3aed",
    accent_color="#1e293b",
    support_email="sanjeev@agenticorg.ai",
    footer_text=None,
    custom_domain=None,
)


@public_router.get("", response_model=BrandingOut)
async def get_public_branding(
    host: str | None = Query(None, description="Host header — used for custom-domain routing"),
    tenant_slug: str | None = Query(None, description="Tenant slug fallback"),
) -> BrandingOut:
    """Return branding for the login page — no auth required.

    Lookup order:
      1. custom_domain matches the Host header
      2. tenant_slug explicitly passed
      3. default AgenticOrg branding
    """
    async with async_session_factory() as session:
        if host:
            result = await session.execute(
                select(TenantBranding).where(TenantBranding.custom_domain == host)
            )
            branding = result.scalar_one_or_none()
            if branding is not None:
                return _to_out(branding)

        if tenant_slug:
            from core.models.tenant import Tenant

            result = await session.execute(
                select(Tenant).where(Tenant.slug == tenant_slug)
            )
            tenant = result.scalar_one_or_none()
            if tenant is not None:
                result = await session.execute(
                    select(TenantBranding).where(
                        TenantBranding.tenant_id == tenant.id
                    )
                )
                branding = result.scalar_one_or_none()
                if branding is not None:
                    return _to_out(branding)

    return _DEFAULT_BRANDING


@admin_router.get("", response_model=BrandingOut)
async def get_tenant_branding(
    tenant_id: str = Depends(get_current_tenant),
) -> BrandingOut:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantBranding).where(TenantBranding.tenant_id == tid)
        )
        branding = result.scalar_one_or_none()
        if branding is None:
            return _DEFAULT_BRANDING
        return _to_out(branding)


@admin_router.put("", response_model=BrandingOut)
async def upsert_tenant_branding(
    body: BrandingIn,
    tenant_id: str = Depends(get_current_tenant),
) -> BrandingOut:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantBranding).where(TenantBranding.tenant_id == tid)
        )
        branding = result.scalar_one_or_none()
        if branding is None:
            branding = TenantBranding(tenant_id=tid, **body.model_dump())
            session.add(branding)
        else:
            for k, v in body.model_dump().items():
                setattr(branding, k, v)
        await session.commit()
        await session.refresh(branding)

    logger.info(
        "branding_upserted",
        tenant_id=tenant_id,
        product_name=body.product_name,
        custom_domain=body.custom_domain,
    )
    return _to_out(branding)


@admin_router.delete("", status_code=204)
async def reset_branding(
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantBranding).where(TenantBranding.tenant_id == tid)
        )
        branding = result.scalar_one_or_none()
        if branding is None:
            raise HTTPException(404, "No custom branding configured")
        await session.delete(branding)
        await session.commit()
    logger.info("branding_reset", tenant_id=tenant_id)


def _to_out(b: TenantBranding) -> BrandingOut:
    return BrandingOut(
        product_name=b.product_name,
        logo_url=b.logo_url,
        favicon_url=b.favicon_url,
        primary_color=b.primary_color,
        accent_color=b.accent_color,
        support_email=b.support_email,
        footer_text=b.footer_text,
        custom_domain=b.custom_domain,
    )
