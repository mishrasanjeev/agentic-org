"""OAuth connector authorization-code automation.

This module covers native OAuth2 connectors whose runtime auth still
uses a refresh_token.  The setup flow automates the browser consent +
authorization-code exchange, then stores the refresh_token in the same
encrypted connector_config vault used by POST /connectors.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.v1.connectors import _assert_public_base_url, _connector_to_dict
from core.async_redis import get_async_redis
from core.crypto import decrypt_for_tenant, encrypt_for_tenant
from core.database import get_tenant_session
from core.models.connector import Connector
from core.models.connector_config import ConnectorConfig

logger = logging.getLogger(__name__)

router = APIRouter()

STATE_TTL_SECONDS = 10 * 60
STATE_KEY_PREFIX = "oauth_connector_state:"


@dataclass(frozen=True)
class OAuthProvider:
    connector_name: str
    authorization_url: str
    token_url: str
    scopes: tuple[str, ...]
    authorization_params: dict[str, str] = field(default_factory=dict)


OAUTH_PROVIDERS: dict[str, OAuthProvider] = {
    "gmail": OAuthProvider(
        connector_name="gmail",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=(
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ),
        authorization_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "google_calendar": OAuthProvider(
        connector_name="google_calendar",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=("https://www.googleapis.com/auth/calendar",),
        authorization_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "youtube": OAuthProvider(
        connector_name="youtube",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=(
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ),
        authorization_params={
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        },
    ),
    "zoho_books": OAuthProvider(
        connector_name="zoho_books",
        authorization_url="https://accounts.zoho.com/oauth/v2/auth",
        token_url="https://accounts.zoho.com/oauth/v2/token",
        scopes=("ZohoBooks.fullaccess.all",),
        authorization_params={
            "access_type": "offline",
            "prompt": "consent",
        },
    ),
}


class OAuthInitiateRequest(BaseModel):
    connector_name: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    redirect_uri: str | None = None
    base_url: str | None = None
    category: str | None = None
    extra_config: dict[str, Any] = Field(default_factory=dict)


class OAuthInitiateResponse(BaseModel):
    authorization_url: str
    state: str
    redirect_uri: str
    expires_in: int


def _normalize_connector_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _provider_for(connector_name: str) -> OAuthProvider:
    normalized = _normalize_connector_name(connector_name)
    provider = OAUTH_PROVIDERS.get(normalized)
    if not provider:
        supported = ", ".join(sorted(OAUTH_PROVIDERS))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Automated OAuth setup is not configured for '{connector_name}'. "
                f"Supported connectors: {supported}."
            ),
        )
    return provider


def _default_redirect_uri(request: Request) -> str:
    return str(request.url_for("oauth_connector_callback"))


def _build_authorization_url(
    provider: OAuthProvider,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        **provider.authorization_params,
    }
    return f"{provider.authorization_url}?{urlencode(params)}"


async def _store_oauth_state(state: str, tenant_id: _uuid.UUID, payload: dict[str, Any]) -> None:
    redis = await get_async_redis()
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth setup requires Redis for a short-lived state handoff. "
                "Refusing to put connector secrets into browser-visible state."
            ),
        )
    ciphertext = await encrypt_for_tenant(json.dumps(payload), tenant_id)
    envelope = json.dumps({"tenant_id": str(tenant_id), "payload": ciphertext})
    await redis.setex(f"{STATE_KEY_PREFIX}{state}", STATE_TTL_SECONDS, envelope)


async def _pop_oauth_state(state: str) -> dict[str, Any]:
    redis = await get_async_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="OAuth state store unavailable")
    key = f"{STATE_KEY_PREFIX}{state}"
    raw = await redis.get(key)
    if not raw:
        raise HTTPException(status_code=400, detail="OAuth state expired or invalid")
    await redis.delete(key)
    try:
        envelope = json.loads(raw)
        return json.loads(decrypt_for_tenant(envelope["payload"]))
    except Exception as exc:
        logger.exception("oauth_connector_state_decrypt_failed")
        raise HTTPException(status_code=400, detail="OAuth state could not be decoded") from exc


async def _exchange_authorization_code(
    provider: OAuthProvider,
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            provider.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail="OAuth provider rejected the authorization code exchange.",
        )
    data = resp.json()
    if not data.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail=(
                "OAuth provider did not return a refresh_token. Revoke the prior "
                "app consent, then retry so the offline consent flow can mint one."
            ),
        )
    return data


def _connector_defaults(connector_name: str) -> dict[str, Any]:
    import connectors  # noqa: F401, F811
    from connectors.registry import ConnectorRegistry

    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {
            "category": "comms",
            "base_url": None,
            "auth_type": "oauth2",
            "tool_functions": [],
            "rate_limit_rpm": 60,
            "timeout_ms": 10000,
        }
    tools = []
    if getattr(connector_cls, "tools", None):
        tools = [
            tool if isinstance(tool, str) else getattr(tool, "name", str(tool))
            for tool in connector_cls.tools
        ]
    elif hasattr(connector_cls, "_register_tools"):
        try:
            instance = connector_cls({})
            tools = sorted(instance._tool_registry.keys())  # noqa: SLF001
        except Exception:
            tools = []
    return {
        "category": getattr(connector_cls, "category", "comms"),
        "base_url": getattr(connector_cls, "base_url", None),
        "auth_type": getattr(connector_cls, "auth_type", "oauth2"),
        "tool_functions": tools,
        "rate_limit_rpm": getattr(connector_cls, "rate_limit_rpm", 60),
        "timeout_ms": getattr(connector_cls, "timeout_ms", 10000),
    }


async def _upsert_oauth_connector(
    *,
    tenant_id: _uuid.UUID,
    provider: OAuthProvider,
    payload: dict[str, Any],
    token_data: dict[str, Any],
) -> Connector:
    connector_name = provider.connector_name
    defaults = _connector_defaults(connector_name)
    base_url = payload.get("base_url") or defaults["base_url"]
    _assert_public_base_url(base_url or "")

    credentials = {
        "client_id": payload["client_id"],
        "client_secret": payload["client_secret"],
        "refresh_token": token_data["refresh_token"],
        "token_url": provider.token_url,
        "redirect_uri": payload["redirect_uri"],
        "scope": " ".join(provider.scopes),
        "oauth_authorized_at": datetime.now(UTC).isoformat(),
    }
    if token_data.get("access_token"):
        credentials["access_token"] = token_data["access_token"]
    if token_data.get("expires_in"):
        credentials["expires_in"] = token_data["expires_in"]
    credentials.update(payload.get("extra_config") or {})

    non_secret_config = {
        "base_url": base_url,
        "auth_type": "oauth2",
        "data_schema_ref": None,
        "rate_limit_rpm": defaults["rate_limit_rpm"],
    }

    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.tenant_id == tenant_id,
                Connector.name == connector_name,
            )
        )
        connector = result.scalar_one_or_none()
        if connector is None:
            connector = Connector(
                tenant_id=tenant_id,
                name=connector_name,
                category=defaults["category"] or payload.get("category") or "comms",
                description=None,
                base_url=base_url,
                auth_type="oauth2",
                auth_config={},
                secret_ref=None,
                tool_functions=defaults["tool_functions"],
                data_schema_ref=None,
                rate_limit_rpm=defaults["rate_limit_rpm"],
                timeout_ms=defaults["timeout_ms"],
                status="active",
            )
            session.add(connector)
            await session.flush()
        else:
            connector.status = "active"
            connector.category = defaults["category"] or connector.category or payload.get("category") or "comms"
            connector.base_url = base_url
            connector.auth_type = "oauth2"
            connector.auth_config = {}
            connector.tool_functions = defaults["tool_functions"] or connector.tool_functions
            connector.rate_limit_rpm = defaults["rate_limit_rpm"]
            connector.timeout_ms = defaults["timeout_ms"]

        encrypted = await encrypt_for_tenant(json.dumps(credentials), tenant_id)
        cc_result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant_id,
                ConnectorConfig.connector_name == connector_name,
            )
        )
        config = cc_result.scalar_one_or_none()
        if config is None:
            session.add(
                ConnectorConfig(
                    tenant_id=tenant_id,
                    connector_name=connector_name,
                    display_name=connector_name,
                    auth_type="oauth2",
                    credentials_encrypted={"_encrypted": encrypted},
                    config=non_secret_config,
                    status="configured",
                    health_status="unknown",
                )
            )
        else:
            config.credentials_encrypted = {"_encrypted": encrypted}
            config.config = non_secret_config
            config.auth_type = "oauth2"
            config.status = "configured"
            config.health_status = "unknown"

        await session.flush()
        await session.refresh(connector)
        return connector


@router.post(
    "/connectors/oauth/initiate",
    response_model=OAuthInitiateResponse,
    dependencies=[require_tenant_admin],
)
async def initiate_connector_oauth(
    body: OAuthInitiateRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> OAuthInitiateResponse:
    provider = _provider_for(body.connector_name)
    tid = _uuid.UUID(tenant_id)
    state = secrets.token_urlsafe(32)
    redirect_uri = body.redirect_uri or _default_redirect_uri(request)

    payload = {
        "tenant_id": tenant_id,
        "connector_name": provider.connector_name,
        "client_id": body.client_id.strip(),
        "client_secret": body.client_secret.strip(),
        "redirect_uri": redirect_uri,
        "base_url": body.base_url,
        "category": body.category,
        "extra_config": body.extra_config,
    }
    await _store_oauth_state(state, tid, payload)

    return OAuthInitiateResponse(
        authorization_url=_build_authorization_url(
            provider,
            client_id=body.client_id.strip(),
            redirect_uri=redirect_uri,
            state=state,
        ),
        state=state,
        redirect_uri=redirect_uri,
        expires_in=STATE_TTL_SECONDS,
    )


@router.get("/oauth/callback", name="oauth_connector_callback")
async def oauth_connector_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth authorization failed: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="OAuth callback missing code or state")

    payload = await _pop_oauth_state(state)
    provider = _provider_for(str(payload.get("connector_name", "")))
    token_data = await _exchange_authorization_code(
        provider,
        code=code,
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        redirect_uri=payload["redirect_uri"],
    )
    connector = await _upsert_oauth_connector(
        tenant_id=_uuid.UUID(payload["tenant_id"]),
        provider=provider,
        payload=payload,
        token_data=token_data,
    )

    connector_summary = _connector_to_dict(connector)
    safe_name = connector_summary["name"]
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>OAuth Connector Authorized</title></head>
  <body style="font-family:system-ui,sans-serif;margin:2rem;line-height:1.5;">
    <h1>OAuth connector authorized</h1>
    <p>{safe_name} is configured with an encrypted refresh token.</p>
    <p>You can close this tab and return to AgenticOrg.</p>
  </body>
</html>"""
    )
