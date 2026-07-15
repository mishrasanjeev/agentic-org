"""OAuth connector authorization-code automation.

Powers provider OAuth handoffs for legacy and re-connect flows. Each request flows
through a provider-isolated ``ProviderSpec`` (see
``core/connectors/provider_registry.py``):

- ``/connectors/oauth/providers`` returns the provider catalog + field
  schema the UI uses to render only the fields each provider needs.
- ``/connectors/oauth/initiate`` validates user fields, builds an
  https-only redirect URI from ``settings.public_api_base_url``, picks
  the right region URLs (Zoho .in vs .com etc.), stores opaque state in
  Redis, and returns the authorize URL.
- ``/oauth/callback`` exchanges the code, persists tokens encrypted,
  and renders the success page (or — if the provider refused to mint a
  refresh_token — surfaces an in-product Reconnect path).
- ``/connectors/oauth/revoke-and-retry`` revokes any existing grant and
  re-initiates the flow with ``prompt=consent`` so the offline scope can
  mint a fresh refresh_token. Replaces the previous manual instruction.

Why we don't compute redirect_uri from the request
--------------------------------------------------
Cloud Run terminates TLS at the edge; inside the container the request
is ``http://``. ``request.url_for`` returns that http URL, which Zoho
rejects with "Invalid Redirect Uri" because the developer console only
has the https value. We use a canonical
``settings.public_api_base_url`` and never trust per-request scheme.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.connectors import _assert_public_base_url, _connector_to_dict
from core.async_redis import get_async_redis
from core.config import settings
from core.connectors.provider_registry import (
    ProviderSpec,
    all_providers,
    authorize_url_for,
    get_provider,
    revoke_url_for,
    supported_oauth_names,
    token_url_for,
)
from core.crypto import decrypt_for_tenant, encrypt_for_tenant
from core.database import get_tenant_session
from core.models.connector import Connector
from core.models.connector_config import ConnectorConfig

logger = logging.getLogger(__name__)

router = APIRouter()

STATE_TTL_SECONDS = 10 * 60
STATE_KEY_PREFIX = "oauth_connector_state:"
RECONNECT_KEY_PREFIX = "oauth_connector_reconnect:"
RECONNECT_TTL_SECONDS = 15 * 60


# ── URL helpers ───────────────────────────────────────────────────────────────


def _public_api_base() -> str:
    """Return the canonical https base URL for this API.

    Falls back to ``http://localhost`` ONLY when settings are unset. The
    ``_canonical_redirect_uri`` helper has a proxy-aware path that uses
    ``X-Forwarded-Host`` + ``X-Forwarded-Proto`` for the (transitional)
    case where the env var hasn't been rolled out to a strict env yet.
    """
    raw = (settings.public_api_base_url or "").strip()
    if raw:
        return raw.rstrip("/")
    return "http://localhost:8000"


def _canonical_redirect_uri(request: Request) -> str:
    """Build the https-only redirect URI for the OAuth callback.

    Three-step preference order:
    1. ``settings.public_api_base_url`` (canonical, set per-env).
    2. ``X-Forwarded-Proto`` + ``Host`` header (proxy-aware, e.g. nginx).
    3. ``request.url_for`` upgraded to https — last resort to keep the
       relaxed-env path working while still refusing to send ``http://``
       to a third-party OAuth provider.
    """
    base = _public_api_base()
    if base.lower().startswith("https://"):
        return f"{base}/api/v1/oauth/callback"

    # Proxy-aware fallback (used inside docker-compose / nginx local).
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host"
    )
    if forwarded_proto == "https" and forwarded_host:
        return f"https://{forwarded_host}/api/v1/oauth/callback"

    # Last resort: take the request URL and force https.
    raw = str(request.url_for("oauth_connector_callback"))
    parts = urlsplit(raw)
    parts = parts._replace(scheme="https")
    return urlunsplit(parts)


# ── Pydantic models ───────────────────────────────────────────────────────────


class OAuthInitiateRequest(BaseModel):
    connector_name: str = Field(..., min_length=1)
    # ``user_fields`` is the provider-driven payload coming from the new
    # dynamic UI. Existing/legacy callers may still send ``client_id`` and
    # ``client_secret`` directly — we accept both shapes.
    user_fields: dict[str, Any] | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    base_url: str | None = None
    category: str | None = None
    extra_config: dict[str, Any] = Field(default_factory=dict)


class OAuthInitiateResponse(BaseModel):
    authorization_url: str
    state: str
    redirect_uri: str
    expires_in: int
    region: str | None = None


class OAuthRevokeRetryRequest(BaseModel):
    connector_name: str = Field(..., min_length=1)


class OAuthProviderFieldSchema(BaseModel):
    key: str
    label: str
    placeholder: str = ""
    help_text: str = ""
    secret: bool = False
    required: bool = True
    options: list[dict[str, str]] = Field(default_factory=list)


class OAuthProviderSchema(BaseModel):
    connector_name: str
    display_name: str
    category: str
    auth_flow: str
    scopes: list[str]
    requires_organization_id: bool
    supports_refresh_token: bool
    documentation_url: str = ""
    regions: list[str]
    user_fields: list[OAuthProviderFieldSchema]


# ── Validation ────────────────────────────────────────────────────────────────


def _coerce_user_fields(
    spec: ProviderSpec, body: OAuthInitiateRequest
) -> dict[str, Any]:
    """Merge legacy + new payload shapes; validate required fields."""
    coerced: dict[str, Any] = {}
    if body.user_fields:
        for k, v in body.user_fields.items():
            if v is None:
                continue
            coerced[str(k)] = v
    if body.client_id:
        coerced.setdefault("client_id", body.client_id.strip())
    if body.client_secret:
        coerced.setdefault("client_secret", body.client_secret.strip())
    # Legacy ``extra_config`` blob — preserve, but let user_fields win.
    for k, v in (body.extra_config or {}).items():
        coerced.setdefault(k, v)
    if body.base_url:
        coerced.setdefault("base_url", body.base_url.strip())

    missing = []
    for field_spec in spec.user_fields:
        if not field_spec.required:
            continue
        value = coerced.get(field_spec.key)
        if value is None or str(value).strip() == "":
            missing.append(field_spec.label)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Missing required fields for {spec.display_name}: "
                + ", ".join(missing)
            ),
        )
    return coerced


def _provider_for(connector_name: str) -> ProviderSpec:
    spec = get_provider(connector_name)
    if not spec:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Automated setup is not configured for "
                f"'{connector_name}'. Supported providers: "
                f"{', '.join(supported_oauth_names())}."
            ),
        )
    return spec


# ── Authorize URL construction ────────────────────────────────────────────────


def _build_authorization_url(
    spec: ProviderSpec,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    extra_config: dict[str, Any],
) -> str:
    """Build the provider-specific authorize URL.

    The base authorize URL is region-aware (see ``ProviderSpec.urls_for``)
    so a Zoho India tenant goes to ``accounts.zoho.in`` and a US tenant
    goes to ``accounts.zoho.com``. Provider quirks like
    ``access_type=offline`` and ``prompt=consent`` come from the spec.
    """
    if spec.auth_flow != "oauth2_authorization_code":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{spec.display_name} does not use the OAuth2 "
                "authorization-code flow — use the dedicated setup "
                "wizard instead."
            ),
        )
    authorize_url = authorize_url_for(spec, extra_config)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(spec.scopes),
        "state": state,
        **spec.authorization_params,
    }
    return f"{authorize_url}?{urlencode(params)}"


# ── Redis state envelope ──────────────────────────────────────────────────────


async def _store_oauth_state(
    state: str, tenant_id: _uuid.UUID, payload: dict[str, Any]
) -> None:
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
    # enterprise-gate: broad-except-ok reason=oauth-state-decode-fails-closed-invalid-state
    except Exception as exc:
        logger.exception("oauth_connector_state_decrypt_failed")
        raise HTTPException(
            status_code=400, detail="OAuth state could not be decoded"
        ) from exc


# ── Reconnect (revoke + retry) state ─────────────────────────────────────────


async def _stash_for_reconnect(
    tenant_id: _uuid.UUID, connector_name: str, payload: dict[str, Any]
) -> None:
    """Remember the last attempt so the Reconnect button can re-run it
    without making the user re-type credentials.
    """
    redis = await get_async_redis()
    if redis is None:
        return
    ciphertext = await encrypt_for_tenant(json.dumps(payload), tenant_id)
    envelope = json.dumps({"tenant_id": str(tenant_id), "payload": ciphertext})
    await redis.setex(
        f"{RECONNECT_KEY_PREFIX}{tenant_id}:{connector_name}",
        RECONNECT_TTL_SECONDS,
        envelope,
    )


async def _pop_reconnect_payload(
    tenant_id: _uuid.UUID, connector_name: str
) -> dict[str, Any] | None:
    redis = await get_async_redis()
    if redis is None:
        return None
    key = f"{RECONNECT_KEY_PREFIX}{tenant_id}:{connector_name}"
    raw = await redis.get(key)
    if not raw:
        return None
    await redis.delete(key)
    try:
        envelope = json.loads(raw)
        return json.loads(decrypt_for_tenant(envelope["payload"]))
    # enterprise-gate: broad-except-ok reason=oauth-reconnect-decode-falls-through-to-fresh-flow
    except Exception:  # noqa: BLE001
        logger.exception("oauth_reconnect_decrypt_failed")
        return None


async def _payload_from_existing_connector_config(
    *,
    tenant_id: _uuid.UUID,
    tenant_id_text: str,
    spec: ProviderSpec,
) -> tuple[dict[str, Any], str] | None:
    """Rebuild a reconnect payload from encrypted connector config."""
    async with get_tenant_session(tenant_id) as session:
        conn_result = await session.execute(
            select(Connector).where(
                Connector.tenant_id == tenant_id,
                Connector.name == spec.connector_name,
            )
        )
        connector = conn_result.scalar_one_or_none()
        cc_result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant_id,
                ConnectorConfig.company_id.is_(None),
                ConnectorConfig.connector_name == spec.connector_name,
            )
        )
        config = cc_result.scalar_one_or_none()

    if not config or not config.credentials_encrypted:
        return None

    creds: dict[str, Any] = {}
    try:
        blob = config.credentials_encrypted
        if isinstance(blob, str):
            blob = json.loads(blob)
        if isinstance(blob, dict) and "_encrypted" in blob:
            creds = json.loads(decrypt_for_tenant(blob["_encrypted"]))
        elif isinstance(blob, dict):
            creds = dict(blob)
    # enterprise-gate: broad-except-ok reason=oauth-reconnect-existing-config-decrypt-fails-closed
    except Exception:  # noqa: BLE001
        logger.info("oauth_reconnect_existing_config_decrypt_failed", exc_info=True)
        return None

    user_fields = {
        field_spec.key: creds.get(field_spec.key)
        for field_spec in spec.user_fields
        if creds.get(field_spec.key)
    }
    if any(
        field.required and not str(user_fields.get(field.key) or "").strip()
        for field in spec.user_fields
    ):
        return None

    extra_config = dict(getattr(config, "config", {}) or {})
    for key in ("region", "organization_id", "base_url"):
        if creds.get(key) and key not in user_fields:
            extra_config.setdefault(key, creds[key])
    base_url = (
        extra_config.get("base_url")
        or getattr(connector, "base_url", None)
        or spec.urls_for({**user_fields, **extra_config}).get("api_base_url")
    )
    if base_url:
        extra_config.setdefault("base_url", base_url)

    payload = {
        "tenant_id": tenant_id_text,
        "connector_name": spec.connector_name,
        "user_fields": user_fields,
        "redirect_uri": "",
        "base_url": base_url,
        "category": getattr(connector, "category", None) or spec.category,
        "extra_config": extra_config,
        "region_urls": spec.urls_for({**user_fields, **extra_config}),
    }
    token = str(creds.get("refresh_token") or creds.get("access_token") or "")
    return payload, token


# ── Token exchange + revoke ──────────────────────────────────────────────────


class OAuthNeedsReconsent(HTTPException):
    """Raised when the provider returned no refresh_token.

    Surfaces a structured error code the UI can match on to render the
    "Reconnect" button instead of a free-text error.
    """

    def __init__(self, provider: str, region: str) -> None:
        super().__init__(
            status_code=409,
            detail={
                "code": "oauth_refresh_token_missing",
                "message": (
                    f"{provider} did not return a refresh_token because "
                    "your account has already granted this app offline "
                    "access. Click Reconnect to revoke the prior grant "
                    "and re-authorize."
                ),
                "provider": provider,
                "region": region,
                "reconnect_endpoint": "/api/v1/connectors/oauth/revoke-and-retry",
            },
        )


async def _exchange_authorization_code(
    spec: ProviderSpec,
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    extra_config: dict[str, Any],
) -> dict[str, Any]:
    token_url = token_url_for(spec, extra_config)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            token_url,
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
        logger.warning(
            "oauth_token_exchange_failed",
            extra={
                "connector": spec.connector_name,
                "status": resp.status_code,
            },
        )
        raise HTTPException(
            status_code=400,
            detail="OAuth provider rejected the authorization code exchange.",
        )
    data: dict[str, Any] = resp.json()
    if not data.get("refresh_token") and spec.supports_refresh_token:
        raise OAuthNeedsReconsent(
            provider=spec.display_name,
            region=spec.resolve_region(extra_config),
        )
    return data


async def _revoke_existing_grant(
    spec: ProviderSpec,
    *,
    token_or_secret: str,
    extra_config: dict[str, Any],
) -> None:
    """Best-effort revoke. Never raises — the provider may have already
    invalidated the grant or may not honor the revoke endpoint.
    """
    revoke_url = revoke_url_for(spec, extra_config)
    if not revoke_url or not token_or_secret:
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Google + Zoho both accept ``token=`` as a form parameter.
            await client.post(
                revoke_url,
                data={"token": token_or_secret},
                headers={"Accept": "application/json"},
            )
    # enterprise-gate: broad-except-ok reason=oauth-provider-revoke-is-best-effort-before-reconsent
    except Exception:  # noqa: BLE001
        logger.info(
            "oauth_revoke_best_effort_failed",
            extra={"connector": spec.connector_name},
            exc_info=True,
        )


# ── Connector persistence ────────────────────────────────────────────────────


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
        # enterprise-gate: broad-except-ok reason=oauth-tool-default-discovery-falls-back-to-empty-list
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
    spec: ProviderSpec,
    payload: dict[str, Any],
    token_data: dict[str, Any],
) -> Connector:
    connector_name = spec.connector_name
    defaults = _connector_defaults(connector_name)
    user_fields = payload.get("user_fields") or {}
    extra_config = payload.get("extra_config") or {}
    # Region-aware API base — Zoho .in / .com / .eu etc.
    region_urls = spec.urls_for({**user_fields, **extra_config})
    fallback_base = region_urls.get("api_base_url") or defaults["base_url"]
    base_url = fallback_base
    _assert_public_base_url(base_url or "")

    credentials: dict[str, Any] = {
        "token_url": region_urls.get("token_url") or "",
        "redirect_uri": payload["redirect_uri"],
        "scope": " ".join(spec.scopes),
        "oauth_authorized_at": datetime.now(UTC).isoformat(),
        "region": spec.resolve_region({**user_fields, **extra_config}),
    }
    for key in ("client_id", "client_secret", "organization_id"):
        if key in user_fields and user_fields[key]:
            credentials[key] = user_fields[key]
    if token_data.get("refresh_token"):
        credentials["refresh_token"] = token_data["refresh_token"]
    if token_data.get("access_token"):
        credentials["access_token"] = token_data["access_token"]
    if token_data.get("expires_in"):
        credentials["expires_in"] = token_data["expires_in"]
    # Any remaining non-secret user fields (e.g. region) come along.
    for key, value in user_fields.items():
        credentials.setdefault(key, value)

    non_secret_config: dict[str, Any] = {
        "base_url": base_url,
        "auth_type": "oauth2",
        "data_schema_ref": None,
        "rate_limit_rpm": defaults["rate_limit_rpm"],
        "region": credentials.get("region", ""),
        "oauth_refresh_token_present": bool(token_data.get("refresh_token")),
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
            connector.category = (
                defaults["category"]
                or connector.category
                or payload.get("category")
                or "comms"
            )
            connector.base_url = base_url
            connector.auth_type = "oauth2"
            connector.auth_config = {}
            connector.tool_functions = (
                defaults["tool_functions"] or connector.tool_functions
            )
            connector.rate_limit_rpm = defaults["rate_limit_rpm"]
            connector.timeout_ms = defaults["timeout_ms"]

        encrypted = await encrypt_for_tenant(json.dumps(credentials), tenant_id)
        cc_result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant_id,
                ConnectorConfig.company_id.is_(None),
                ConnectorConfig.connector_name == connector_name,
            )
        )
        config = cc_result.scalar_one_or_none()
        if config is None:
            session.add(
                ConnectorConfig(
                    tenant_id=tenant_id,
                    company_id=None,
                    connector_name=connector_name,
                    display_name=spec.display_name,
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


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/connectors/oauth/providers",
    response_model=list[OAuthProviderSchema],
)
@route_meta(
    auth_required=True,
    tenant_required=False,
    scope="connectors.oauth.providers.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.oauth.providers.list",
)
async def list_oauth_providers() -> list[OAuthProviderSchema]:
    """Return the catalog the UI uses to render provider-specific forms."""
    return [
        OAuthProviderSchema(**spec.schema_dict()) for spec in all_providers()
    ]


@router.post(
    "/connectors/oauth/initiate",
    response_model=OAuthInitiateResponse,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.oauth.initiate.admin",
    rate_limit="admin-mutating",
    idempotency="oauth-state-nonce",
    audit_event="connectors.oauth.initiate",
)
async def initiate_connector_oauth(
    body: OAuthInitiateRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> OAuthInitiateResponse:
    spec = _provider_for(body.connector_name)
    user_fields = _coerce_user_fields(spec, body)
    tid = _uuid.UUID(tenant_id)
    state = secrets.token_urlsafe(32)
    # Honor an explicit body.redirect_uri ONLY if it is https; otherwise
    # fall back to the canonical setting. This prevents callers from
    # downgrading the URI to http://.
    if body.redirect_uri and body.redirect_uri.lower().startswith("https://"):
        redirect_uri = body.redirect_uri
    else:
        redirect_uri = _canonical_redirect_uri(request)
    if not redirect_uri.lower().startswith("https://"):
        # Relaxed-env (local docker-compose) is the only place we land here.
        # Still allow http://localhost so unit tests work, but log loudly so
        # nobody ships a misconfigured production deploy.
        logger.warning(
            "oauth_redirect_uri_not_https",
            extra={"redirect_uri": redirect_uri},
        )

    extra_config: dict[str, Any] = dict(body.extra_config or {})
    if body.base_url:
        extra_config.setdefault("base_url", body.base_url.strip())
    # Surface region from user_fields into extra_config so the connector
    # row stores it for downstream tools.
    if "region" in user_fields:
        extra_config["region"] = user_fields["region"]
    region_urls = spec.urls_for({**user_fields, **extra_config})

    payload = {
        "tenant_id": tenant_id,
        "connector_name": spec.connector_name,
        "user_fields": user_fields,
        "redirect_uri": redirect_uri,
        "base_url": body.base_url,
        "category": body.category,
        "extra_config": extra_config,
        "region_urls": region_urls,
    }
    await _store_oauth_state(state, tid, payload)
    await _stash_for_reconnect(tid, spec.connector_name, payload)

    return OAuthInitiateResponse(
        authorization_url=_build_authorization_url(
            spec,
            client_id=str(user_fields["client_id"]),
            redirect_uri=redirect_uri,
            state=state,
            extra_config={**user_fields, **extra_config},
        ),
        state=state,
        redirect_uri=redirect_uri,
        expires_in=STATE_TTL_SECONDS,
        region=spec.resolve_region({**user_fields, **extra_config}) or None,
    )


@router.post(
    "/connectors/oauth/revoke-and-retry",
    response_model=OAuthInitiateResponse,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.oauth.reconnect.admin",
    rate_limit="admin-mutating",
    idempotency="oauth-reconnect-state-nonce",
    audit_event="connectors.oauth.reconnect",
)
async def revoke_and_retry(
    body: OAuthRevokeRetryRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
) -> OAuthInitiateResponse:
    """Revoke the existing OAuth grant and re-initiate with force-consent.

    Closes the "provider didn't mint a refresh_token because the app was
    already approved" path that previously surfaced as a 400 with manual
    instructions. The UI Reconnect button posts here; the response is
    identical to ``initiate`` so the client can redirect to the
    authorize URL the same way.
    """
    spec = _provider_for(body.connector_name)
    tid = _uuid.UUID(tenant_id)
    stash = await _pop_reconnect_payload(tid, spec.connector_name)
    existing_refresh = None
    if not stash:
        existing_payload = await _payload_from_existing_connector_config(
            tenant_id=tid,
            tenant_id_text=tenant_id,
            spec=spec,
        )
        if existing_payload:
            stash, existing_refresh = existing_payload
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No prior authorization attempt or encrypted OAuth "
                    "configuration was found for this connector. Start a "
                    "fresh connector authorization flow instead."
                ),
            )
    # Best-effort revoke using whatever token material we have.
    if not existing_refresh:
        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.company_id.is_(None),
                    ConnectorConfig.connector_name == spec.connector_name,
                )
            )
            config = cc_result.scalar_one_or_none()
            if config and config.credentials_encrypted:
                blob = config.credentials_encrypted.get("_encrypted")
                if blob:
                    try:
                        creds = json.loads(decrypt_for_tenant(blob))
                        existing_refresh = creds.get("refresh_token")
                    # enterprise-gate: broad-except-ok reason=oauth-reconnect-existing-token-decrypt-skip-revoke-only
                    except Exception:  # noqa: BLE001
                        logger.info(
                            "oauth_reconnect_decrypt_skipped", exc_info=True
                        )
    await _revoke_existing_grant(
        spec,
        token_or_secret=existing_refresh or "",
        extra_config=stash.get("extra_config") or {},
    )

    state = secrets.token_urlsafe(32)
    redirect_uri = _canonical_redirect_uri(request)
    payload = {
        **stash,
        "redirect_uri": redirect_uri,
    }
    await _store_oauth_state(state, tid, payload)
    await _stash_for_reconnect(tid, spec.connector_name, payload)

    user_fields = stash.get("user_fields") or {}
    extra_config = stash.get("extra_config") or {}
    return OAuthInitiateResponse(
        authorization_url=_build_authorization_url(
            spec,
            client_id=str(user_fields.get("client_id", "")),
            redirect_uri=redirect_uri,
            state=state,
            extra_config={**user_fields, **extra_config},
        ),
        state=state,
        redirect_uri=redirect_uri,
        expires_in=STATE_TTL_SECONDS,
        region=spec.resolve_region({**user_fields, **extra_config}) or None,
    )


@router.get("/oauth/callback", name="oauth_connector_callback")
@route_meta(
    auth_required=False,
    tenant_required=True,
    scope="public:connectors.oauth.callback",
    rate_limit="oauth-callback",
    idempotency="oauth-state-and-provider-code",
    audit_event="connectors.oauth.callback",
    public_reason="oauth-state-validated-authorization-code",
)
async def oauth_connector_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        raise HTTPException(
            status_code=400, detail=f"OAuth authorization failed: {error}"
        )
    if not code or not state:
        raise HTTPException(
            status_code=400, detail="OAuth callback missing code or state"
        )

    payload = await _pop_oauth_state(state)
    spec = _provider_for(str(payload.get("connector_name", "")))
    user_fields = payload.get("user_fields") or {}
    extra_config = payload.get("extra_config") or {}
    token_data = await _exchange_authorization_code(
        spec,
        code=code,
        client_id=str(user_fields["client_id"]),
        client_secret=str(user_fields["client_secret"]),
        redirect_uri=payload["redirect_uri"],
        extra_config={**user_fields, **extra_config},
    )
    connector = await _upsert_oauth_connector(
        tenant_id=_uuid.UUID(payload["tenant_id"]),
        spec=spec,
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
    <script>
      try {{
        if (window.opener) {{
          window.opener.postMessage(
            {{ type: 'oauth_connector_authorized', connector: '{safe_name}' }},
            window.location.origin
          );
        }}
      }} catch (err) {{ /* best effort */ }}
    </script>
  </body>
</html>"""
    )


# Re-export helpers for the unit/regression tests.
__all__ = [
    "OAuthInitiateRequest",
    "OAuthInitiateResponse",
    "OAuthRevokeRetryRequest",
    "OAuthNeedsReconsent",
    "_build_authorization_url",
    "_canonical_redirect_uri",
    "_coerce_user_fields",
    "_payload_from_existing_connector_config",
    "_provider_for",
    "_public_api_base",
    "router",
]
