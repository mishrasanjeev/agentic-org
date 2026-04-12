"""SSO endpoints — OIDC login flow and provider admin CRUD.

Routes:
  GET  /api/v1/auth/sso/providers               — public: list providers for a tenant
  GET  /api/v1/auth/sso/{provider_key}/login    — public: kick off OIDC flow
  GET  /api/v1/auth/sso/{provider_key}/callback — public: OIDC callback
  GET  /api/v1/sso/configs                          — authed: list tenant's SSO configs
  POST /api/v1/sso/configs                          — authed: create/update config
  DELETE /api/v1/sso/configs/{key}                  — authed
"""

from __future__ import annotations

import json
import uuid

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from auth.jwt import create_access_token
from auth.sso.oidc import OIDCProvider, new_nonce, new_pkce_pair, new_state
from auth.sso.provisioning import jit_provision_user
from core.config import settings
from core.database import async_session_factory
from core.models.sso_config import SSOConfig
from core.models.tenant import Tenant
from core.rbac import get_scopes_for_role

logger = structlog.get_logger()

public_router = APIRouter(prefix="/auth/sso", tags=["SSO"])
admin_router = APIRouter(prefix="/sso", tags=["SSO"], dependencies=[require_tenant_admin])


# ── Redis helpers for the short-lived state store ─────────────────


def _redis():
    try:
        from core.billing.usage_tracker import _get_redis

        return _get_redis()
    except Exception:
        return None


def _state_key(provider_key: str, state: str) -> str:
    return f"sso:state:{provider_key}:{state}"


async def _load_provider(provider_key: str, tenant_id: uuid.UUID | None = None) -> tuple[OIDCProvider, SSOConfig]:
    async with async_session_factory() as session:
        stmt = select(SSOConfig).where(
            SSOConfig.provider_key == provider_key,
            SSOConfig.enabled.is_(True),
        )
        if tenant_id is not None:
            stmt = stmt.where(SSOConfig.tenant_id == tenant_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        if config is None:
            raise HTTPException(404, f"SSO provider {provider_key!r} not found")
        if config.provider_type != "oidc":
            # SAML is planned for v4.8.0 — see
            # docs/adr/0007-saml-via-xmlsec-sidecar.md
            raise HTTPException(
                400,
                f"Only OIDC is supported in v4.7.0 (got {config.provider_type!r}). "
                "SAML 2.0 ships in v4.8.0 via the xmlsec sidecar — see "
                "docs/adr/0007-saml-via-xmlsec-sidecar.md.",
            )

        provider = OIDCProvider(provider_key, config.config)
        await provider.prepare()
        return provider, config


# ── Public flow ───────────────────────────────────────────────────


@public_router.get("/providers")
async def list_providers(email: str = Query(..., description="User email — used to infer tenant")) -> dict:
    """Return the SSO providers a user can use to log in, by looking up
    their tenant via email domain. This lets the login page show the
    right SSO buttons without the user having picked a tenant yet.
    """
    domain = email.split("@", 1)[-1].lower() if "@" in email else ""
    if not domain:
        return {"providers": []}

    async with async_session_factory() as session:
        # Find tenants whose sso_configs have this domain in allowed_domains.
        result = await session.execute(
            select(SSOConfig).where(SSOConfig.enabled.is_(True))
        )
        configs = result.scalars().all()

    out = []
    for c in configs:
        allowed = c.allowed_domains or []
        if not allowed or domain in allowed:
            out.append(
                {
                    "provider_key": c.provider_key,
                    "display_name": c.display_name,
                    "provider_type": c.provider_type,
                    "login_url": f"/api/v1/auth/sso/{c.provider_key}/login?tenant_id={c.tenant_id}",
                }
            )

    return {"providers": out}


@public_router.get("/{provider_key}/login")
async def sso_login(
    provider_key: str,
    tenant_id: uuid.UUID,
    return_to: str = "/",
) -> RedirectResponse:
    """Kick off the OIDC authorization-code flow with PKCE."""
    provider, _config = await _load_provider(provider_key, tenant_id)

    state = new_state()
    nonce = new_nonce()
    verifier, challenge = new_pkce_pair()

    # Persist state for the callback to verify
    payload = {
        "tenant_id": str(tenant_id),
        "nonce": nonce,
        "verifier": verifier,
        "return_to": return_to,
    }
    r = _redis()
    if r is None:
        # Fallback: put it in a signed cookie would be nicer, but for now
        # fail loudly rather than silently accepting state.
        raise HTTPException(503, "SSO state store unavailable (Redis required)")
    try:
        r.setex(_state_key(provider_key, state), 600, json.dumps(payload))
    except Exception as exc:
        logger.exception("sso_state_store_failed")
        raise HTTPException(503, "Failed to persist SSO state") from exc

    url = provider.build_authorize_url(state, nonce, challenge)
    return RedirectResponse(url, status_code=303)


@public_router.get("/{provider_key}/callback")
async def sso_callback(
    provider_key: str,
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,  # noqa: ARG001  (unused placeholder)
) -> RedirectResponse:
    r = _redis()
    if r is None:
        raise HTTPException(503, "SSO state store unavailable")
    raw = r.get(_state_key(provider_key, state))
    if not raw:
        raise HTTPException(400, "Invalid or expired state")

    if isinstance(raw, bytes):
        raw = raw.decode()
    payload = json.loads(raw)
    r.delete(_state_key(provider_key, state))  # one-shot

    tenant_id = uuid.UUID(payload["tenant_id"])
    nonce = payload["nonce"]
    verifier = payload["verifier"]
    return_to = payload.get("return_to", "/")

    provider, config = await _load_provider(provider_key, tenant_id)

    try:
        tokens = await provider.exchange_code(code, verifier, nonce)
    except httpx.HTTPStatusError as exc:
        logger.warning("sso_token_exchange_failed", status=exc.response.status_code)
        raise HTTPException(400, "SSO token exchange failed") from exc
    except Exception:
        logger.exception("sso_verification_failed")
        raise HTTPException(400, "SSO verification failed") from None

    try:
        user = await jit_provision_user(tenant_id, provider_key, tokens.claims)
    except ValueError as exc:
        logger.warning("sso_provisioning_rejected", reason=str(exc))
        raise HTTPException(403, str(exc)) from exc

    # Look up tenant name for the JWT
    async with async_session_factory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one()

    # Mint our own JWT — user's browser now holds an AgenticOrg session.
    # Shape matches the rest of api/v1/auth.py so middleware can decode it.
    from core.rbac import get_allowed_domains

    scopes = get_scopes_for_role(user.role)
    token = create_access_token(
        data={
            "sub": user.email,
            "agenticorg:user_id": str(user.id),
            "agenticorg:tenant_id": str(tenant_id),
            "agenticorg:tenant_name": tenant.name,
            "grantex:scopes": scopes,
            "name": user.name,
            "role": user.role,
            "domain": user.domain,
            "agenticorg:domains": get_allowed_domains(user.role),
            "auth_method": "sso_oidc",
            "sso_provider": provider_key,
        },
        expires_minutes=getattr(settings, "token_ttl_minutes", 60),
    )

    # Redirect to UI with token in fragment (so it never hits server logs)
    ui_base = settings.ui_base_url if hasattr(settings, "ui_base_url") else ""
    target = return_to or "/"
    if not target.startswith("/"):
        target = "/"
    return RedirectResponse(
        f"{ui_base}{target}#token={token}",
        status_code=303,
    )


# ── Admin CRUD ────────────────────────────────────────────────────


class SSOConfigIn(BaseModel):
    provider_key: str = Field(..., min_length=1, max_length=50)
    provider_type: str = Field("oidc", pattern="^(oidc|saml)$")
    display_name: str = Field(..., min_length=1, max_length=100)
    config: dict = Field(..., description="Provider-specific config")
    enabled: bool = True
    jit_provisioning: bool = True
    default_role: str = "analyst"
    allowed_domains: list[str] = Field(default_factory=list)


class SSOConfigOut(BaseModel):
    id: uuid.UUID
    provider_key: str
    provider_type: str
    display_name: str
    enabled: bool
    jit_provisioning: bool
    default_role: str
    allowed_domains: list[str]


@admin_router.get("/configs", response_model=list[SSOConfigOut])
async def list_configs(
    tenant_id: str = Depends(get_current_tenant),
) -> list[SSOConfigOut]:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(SSOConfig).where(SSOConfig.tenant_id == tid)
        )
        rows = result.scalars().all()
        return [
            SSOConfigOut(
                id=c.id,
                provider_key=c.provider_key,
                provider_type=c.provider_type,
                display_name=c.display_name,
                enabled=c.enabled,
                jit_provisioning=c.jit_provisioning,
                default_role=c.default_role,
                allowed_domains=c.allowed_domains or [],
            )
            for c in rows
        ]


@admin_router.post("/configs", response_model=SSOConfigOut, status_code=201)
async def upsert_config(
    body: SSOConfigIn,
    tenant_id: str = Depends(get_current_tenant),
) -> SSOConfigOut:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(SSOConfig).where(
                SSOConfig.tenant_id == tid,
                SSOConfig.provider_key == body.provider_key,
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = SSOConfig(
                tenant_id=tid,
                provider_key=body.provider_key,
                provider_type=body.provider_type,
                display_name=body.display_name,
                config=body.config,
                enabled=body.enabled,
                jit_provisioning=body.jit_provisioning,
                default_role=body.default_role,
                allowed_domains=body.allowed_domains,
            )
            session.add(config)
        else:
            config.provider_type = body.provider_type
            config.display_name = body.display_name
            config.config = body.config
            config.enabled = body.enabled
            config.jit_provisioning = body.jit_provisioning
            config.default_role = body.default_role
            config.allowed_domains = body.allowed_domains
        await session.commit()
        await session.refresh(config)

    logger.info(
        "sso_config_upserted",
        tenant_id=tenant_id,
        provider_key=body.provider_key,
        enabled=body.enabled,
    )
    return SSOConfigOut(
        id=config.id,
        provider_key=config.provider_key,
        provider_type=config.provider_type,
        display_name=config.display_name,
        enabled=config.enabled,
        jit_provisioning=config.jit_provisioning,
        default_role=config.default_role,
        allowed_domains=config.allowed_domains or [],
    )


@admin_router.delete("/configs/{provider_key}", status_code=204)
async def delete_config(
    provider_key: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(SSOConfig).where(
                SSOConfig.tenant_id == tid, SSOConfig.provider_key == provider_key
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise HTTPException(404, "SSO config not found")
        await session.delete(config)
        await session.commit()
    logger.info("sso_config_deleted", tenant_id=tenant_id, provider_key=provider_key)
