"""Branding API — tenant-level white-label configuration.

Public GET for the login page (unauthenticated — we look up by host).
Authenticated GET/PATCH for admins.

Hardening (v4.7.0):
  - Public GET is rate-limited per IP (30 req/min)
  - Successful lookups are cached for 60 seconds in-process
  - Public response only includes fields a browser actually needs
    (product name, logo, colors, footer text). Internal fields like
    tenant ID, custom-domain mapping, and admin email are stripped.
"""

from __future__ import annotations

import time
import uuid
from collections import deque

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import async_session_factory, get_tenant_session
from core.models.branding import TenantBranding

logger = structlog.get_logger()

public_router = APIRouter(prefix="/branding", tags=["Branding"])
admin_router = APIRouter(prefix="/admin/branding", tags=["Branding"], dependencies=[require_tenant_admin])


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


# ── Rate limit + cache for the public endpoint ─────────────────────
#
# We can't use the existing tool-gateway rate limiter here because the
# request is unauthenticated and the tenant context isn't established
# yet. A small in-process token bucket per source IP is enough — the
# endpoint is read-only and the worst case is a noisy NAT triggering
# 429s for legitimate users sharing the IP.

_BRANDING_RPM = 30
_branding_window: dict[str, deque[float]] = {}
_branding_cache: dict[str, tuple[BrandingOut, float]] = {}
_BRANDING_CACHE_TTL = 60.0


def _check_branding_rate(ip: str) -> bool:
    """Token-bucket: 30 req per rolling 60s per IP."""
    now = time.time()
    window = _branding_window.setdefault(ip, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= _BRANDING_RPM:
        return False
    window.append(now)
    return True


def _public_view(branding: BrandingOut) -> BrandingOut:
    """Strip fields we never want unauthenticated callers to see.

    Only product name, logo, colors, and the marketing footer go out.
    Custom-domain mapping reveals tenant→domain bindings (recon hint),
    so it's withheld from the public response. Authenticated admins
    still see it via /admin/branding.
    """
    return BrandingOut(
        product_name=branding.product_name,
        logo_url=branding.logo_url,
        favicon_url=branding.favicon_url,
        primary_color=branding.primary_color,
        accent_color=branding.accent_color,
        support_email=None,
        footer_text=branding.footer_text,
        custom_domain=None,
    )


@public_router.get("", response_model=BrandingOut)
async def get_public_branding(
    request: Request,
    host: str | None = Query(None, description="Host header — used for custom-domain routing"),
    tenant_slug: str | None = Query(None, description="Tenant slug fallback"),
) -> BrandingOut:
    """Return branding for the login page — no auth required.

    Lookup order:
      1. custom_domain matches the Host header
      2. tenant_slug explicitly passed
      3. default AgenticOrg branding

    Hardening:
      - Per-IP rate limit (30 req/min)
      - 60s in-process cache
      - Public view strips internal-only fields
    """
    # Per-IP rate limit
    client_ip = request.client.host if request.client else "unknown"
    if not _check_branding_rate(client_ip):
        logger.warning("branding_rate_limited", ip=client_ip)
        raise HTTPException(429, "Too many branding lookups, slow down")

    cache_key = f"{host or ''}:{tenant_slug or ''}"
    now = time.time()
    cached = _branding_cache.get(cache_key)
    if cached is not None and cached[1] > now:
        return cached[0]

    found = False
    public: BrandingOut = _public_view(_DEFAULT_BRANDING)

    async with async_session_factory() as session:
        if host:
            result = await session.execute(
                select(TenantBranding).where(TenantBranding.custom_domain == host)
            )
            branding = result.scalar_one_or_none()
            if branding is not None:
                public = _public_view(_to_out(branding))
                found = True

        if not found and tenant_slug:
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
                    public = _public_view(_to_out(branding))

    _branding_cache[cache_key] = (public, now + _BRANDING_CACHE_TTL)
    return public


def _clear_branding_cache() -> None:
    """Test helper — flushes the in-process branding cache."""
    _branding_cache.clear()
    _branding_window.clear()


@admin_router.get("", response_model=BrandingOut)
async def get_tenant_branding(
    tenant_id: str = Depends(get_current_tenant),
) -> BrandingOut:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
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
    async with get_tenant_session(tid) as session:
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
    async with get_tenant_session(tid) as session:
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
