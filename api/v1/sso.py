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

import time
import uuid

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.auth import _set_session_cookie
from auth.jwt import create_access_token
from auth.sso.oidc import OIDCProvider, new_nonce, new_pkce_pair
from auth.sso.provisioning import jit_provision_user
from auth.sso.state_token import (
    FLOW_COOKIE_NAME,
    SSOStateClaims,
    SSOStateError,
    clear_flow_cookie,
    clear_flow_cookie_header,
    decrypt_flow_cookie,
    issue_state_token,
    set_flow_cookie,
    verify_state_token,
)
from core.config import settings
from core.database import async_session_factory, get_tenant_session
from core.models.sso_config import SSOConfig
from core.models.tenant import Tenant
from core.rbac import get_scopes_for_role

logger = structlog.get_logger()

public_router = APIRouter(prefix="/auth/sso", tags=["SSO"])
admin_router = APIRouter(prefix="/sso", tags=["SSO"], dependencies=[require_tenant_admin])


# ── Login-flow state ──────────────────────────────────────────────
#
# Bug sheet 2026-09-14 row 7: flow state no longer lives in Redis (an outage
# 503'd every SSO login). ``state`` is a signed token and the PKCE verifier
# rides in an encrypted, browser-bound cookie — see auth/sso/state_token.py.
# Redis is used only for the best-effort one-shot replay marker below.

_MAX_RETURN_TO_LENGTH = 1024


async def _redis():
    from core.async_redis import get_async_redis
    return await get_async_redis()


def _replay_marker_key(jti: str) -> str:
    return f"sso:used:{jti}"


def _safe_return_to(value: str | None) -> str:
    target = value or "/dashboard"
    if not target.startswith("/") or target.startswith("//") or len(target) > _MAX_RETURN_TO_LENGTH:
        return "/dashboard"
    return target


async def _mark_state_consumed(claims: SSOStateClaims, provider_key: str) -> None:
    """Best-effort one-shot use of the state ``jti``.

    Redis available: ``SET sso:used:{jti} 1 NX EX <remaining ttl>``; a key
    that already exists means the state+cookie pair was replayed -> 400.

    Redis unavailable or erroring: log ``sso_replay_marker_unavailable`` and
    CONTINUE (documented degraded mode). Only replay *detection* degrades;
    replay stays bounded without Redis because:
      * the flow cookie is HttpOnly, encrypted and bound to this state's jti,
        so a replay needs the original browser's cookie jar, not just a URL;
      * the IdP authorization code is single-use, so re-submitting the same
        callback fails at the token endpoint;
      * PKCE binds the code to the verifier, which never appears in a URL;
      * the state expires 10 minutes after issue.
    Non-Redis errors are not caught here and fail closed as a 500.
    """
    try:
        r = await _redis()
        if r is None:
            logger.warning("sso_replay_marker_unavailable", reason="redis_unavailable", provider_key=provider_key)
            return
        ttl = max(1, claims.exp - int(time.time()))
        first_use = await r.set(_replay_marker_key(claims.jti), "1", nx=True, ex=ttl)
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning(
            "sso_replay_marker_unavailable",
            reason="redis_error",
            error_type=type(exc).__name__,
            provider_key=provider_key,
        )
        return
    if not first_use:
        logger.warning("sso_state_replay_rejected", provider_key=provider_key)
        raise HTTPException(400, "Invalid or expired state")


async def _load_provider(provider_key: str, tenant_id: uuid.UUID) -> tuple[OIDCProvider, SSOConfig]:
    async with get_tenant_session(tenant_id) as session:
        stmt = select(SSOConfig).where(
            SSOConfig.provider_key == provider_key,
            SSOConfig.enabled.is_(True),
            SSOConfig.tenant_id == tenant_id,
        )
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
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="sso.public.providers",
    rate_limit="auth-discovery",
    idempotency="read-only-domain-discovery",
    audit_event="sso.providers.lookup",
    public_reason="public-auth-discovery-email-domain",
)
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
        # Empty ``allowed_domains`` means NOT anonymously discoverable:
        # returning every tenant's providers enumerates other organisations.
        allowed = {str(d).lower() for d in (c.allowed_domains or [])}
        if domain in allowed:
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
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="sso.public.login",
    rate_limit="auth-sso-login-initiation",
    idempotency="one-shot-oidc-state-created",
    audit_event="sso.login.started",
    public_reason="public-auth-route-pkce-state-protected",
)
async def sso_login(
    provider_key: str,
    tenant_id: uuid.UUID,
    return_to: str = "/dashboard",
) -> RedirectResponse:
    """Kick off the OIDC authorization-code flow with PKCE.

    Needs no server-side store: the signed ``state`` carries the tenant,
    provider, nonce and return path; the PKCE verifier goes into the
    encrypted HttpOnly flow cookie, never into a URL.
    """
    provider, _config = await _load_provider(provider_key, tenant_id)

    nonce = new_nonce()
    verifier, challenge = new_pkce_pair()
    state, claims = issue_state_token(
        tenant_id=tenant_id,
        provider_key=provider_key,
        nonce=nonce,
        return_to=_safe_return_to(return_to),
    )

    url = provider.build_authorize_url(state, nonce, challenge)
    response = RedirectResponse(url, status_code=303)
    set_flow_cookie(response, jti=claims.jti, verifier=verifier)
    return response


@public_router.get("/{provider_key}/callback")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="sso.public.callback.external_input_sensitive",
    rate_limit="auth-sso-callback",
    idempotency="one-shot-state-token-consumed",
    audit_event="sso.login.callback",
    public_reason="oidc-provider-callback-state-nonce-protected",
)
async def sso_callback(
    request: Request,
    provider_key: str,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    try:
        try:
            claims = verify_state_token(state, provider_key=provider_key)
            verifier = decrypt_flow_cookie(request.cookies.get(FLOW_COOKIE_NAME), expected_jti=claims.jti)
        except SSOStateError as exc:
            logger.warning("sso_state_rejected", reason=exc.reason, provider_key=provider_key)
            raise HTTPException(400, "Invalid or expired state") from None

        await _mark_state_consumed(claims, provider_key)

        # Tenant, nonce and return path come only from the signed state.
        tenant_id = claims.tenant_id
        nonce = claims.nonce
        return_to = claims.return_to

        provider, config = await _load_provider(provider_key, tenant_id)

        try:
            tokens = await provider.exchange_code(code, verifier, nonce)
        except httpx.HTTPStatusError as exc:
            logger.warning("sso_token_exchange_failed", status=exc.response.status_code)
            raise HTTPException(400, "SSO token exchange failed") from exc
        # enterprise-gate: broad-except-ok reason=sso-verification-failure-fails-closed-400
        except Exception:
            logger.exception("sso_verification_failed")
            raise HTTPException(400, "SSO verification failed") from None

        try:
            user = await jit_provision_user(tenant_id, provider_key, tokens.claims)
        except ValueError as exc:
            logger.warning("sso_provisioning_rejected", reason=str(exc))
            raise HTTPException(403, str(exc)) from exc

        # Look up tenant name for the JWT
        async with get_tenant_session(tenant_id) as session:
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
                "agenticorg:domains": get_allowed_domains(user.role, user.domain),
                "auth_method": "sso_oidc",
                "sso_provider": provider_key,
            },
            expires_minutes=getattr(settings, "token_ttl_minutes", 60),
        )
    except HTTPException as exc:
        # The flow is over either way: expire the one-shot flow cookie on
        # error responses too.
        exc.headers = {**(exc.headers or {}), "set-cookie": clear_flow_cookie_header()}
        raise

    # Establish the same cookie-first browser session used by password and
    # Google login. Never expose bearer material to browser JavaScript.
    # AGENTICORG_UI_BASE_URL: the UI may be served from another origin than
    # the API (Cloud Run splits them); empty means same-origin.
    ui_base = (settings.ui_base_url or "").rstrip("/")
    target = return_to or "/dashboard"
    if not target.startswith("/") or target.startswith("//"):
        target = "/dashboard"
    response = RedirectResponse(f"{ui_base}{target}", status_code=303)
    _set_session_cookie(
        response,
        token,
        getattr(settings, "token_ttl_minutes", 60) * 60,
    )
    clear_flow_cookie(response)
    return response


# ── Admin CRUD ────────────────────────────────────────────────────


async def _seal_client_secret(config: dict, tenant_id: uuid.UUID) -> dict:
    """Never persist ``client_secret`` in plaintext JSONB.

    The secret is encrypted with ``core.crypto.encrypt_for_tenant`` and stored
    under ``client_secret_enc``; the plaintext key is dropped from the row.
    """
    from core.crypto import encrypt_for_tenant

    sealed = {k: v for k, v in dict(config).items() if k != "client_secret"}
    plaintext = config.get("client_secret")
    if isinstance(plaintext, str) and plaintext:
        sealed["client_secret_enc"] = await encrypt_for_tenant(plaintext, tenant_id)
    return sealed


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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="sso.config.sensitive.list",
    rate_limit="security-admin-read",
    idempotency="read-only",
    audit_event="sso.config.list",
)
async def list_configs(
    tenant_id: str = Depends(get_current_tenant),
) -> list[SSOConfigOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="sso.config.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-upsert-by-provider-key",
    audit_event="sso.config.upsert",
)
async def upsert_config(
    body: SSOConfigIn,
    tenant_id: str = Depends(get_current_tenant),
) -> SSOConfigOut:
    tid = uuid.UUID(tenant_id)
    if body.provider_type == "oidc":
        try:
            # Validate shape only; the secret is sealed below and must not
            # pass through the legacy-plaintext warning path.
            OIDCProvider(body.provider_key, {k: v for k, v in body.config.items() if k != "client_secret"})
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="OIDC config must use an HTTPS public issuer and required OIDC fields",
            ) from exc
    stored_config = await _seal_client_secret(body.config, tid)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(SSOConfig).where(
                SSOConfig.tenant_id == tid,
                SSOConfig.provider_key == body.provider_key,
            )
        )
        config = result.scalar_one_or_none()
        if config is not None and "client_secret_enc" not in stored_config:
            # Re-saving without a secret keeps the one already sealed.
            existing_enc = (config.config or {}).get("client_secret_enc")
            if existing_enc:
                stored_config["client_secret_enc"] = existing_enc
        if config is None:
            config = SSOConfig(
                tenant_id=tid,
                provider_key=body.provider_key,
                provider_type=body.provider_type,
                display_name=body.display_name,
                config=stored_config,
                enabled=body.enabled,
                jit_provisioning=body.jit_provisioning,
                default_role=body.default_role,
                allowed_domains=body.allowed_domains,
            )
            session.add(config)
        else:
            config.provider_type = body.provider_type
            config.display_name = body.display_name
            config.config = stored_config
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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="sso.config.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete-by-provider-key",
    audit_event="sso.config.delete",
)
async def delete_config(
    provider_key: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
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
