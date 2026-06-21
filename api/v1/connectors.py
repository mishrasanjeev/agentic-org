"""Connector endpoints."""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from api.deps import get_current_tenant, require_scope, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.marketing.connector_contracts import evaluate_hubspot_crm_read_contract
from core.models.connector import Connector
from core.schemas.api import ConnectorCreate, ConnectorUpdate
from core.security.egress import EgressValidationError, validate_public_url

_log = logging.getLogger(__name__)

_CMO_SANDBOX_CATEGORIES = ("CRM", "Ads", "Analytics", "Email")
_CMO_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "authorization",
)
_CONNECTOR_RETARGET_KEYS = (
    "api_base_url",
    "base_url",
    "bridge_url",
    "domain",
    "host",
    "instance",
    "org",
    "site",
    "subdomain",
)
_CONNECTOR_CREDENTIAL_ROTATION_KEYS = (
    "access_token",
    "api_key",
    "api_token",
    "app_password",
    "auth_token",
    "bot_token",
    "bridge_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
)
_CMO_PLACEHOLDER_MARKERS = (
    "REAL_",
    "PLACEHOLDER",
    "TODO",
    "CHANGEME",
    "REPLACE_ME",
    "example.com",
)
_CMO_VENDOR_SANDBOX_PROVIDERS: dict[str, dict[str, dict[str, Any]]] = {
    "CRM": {
        "hubspot": {
            "aliases": ("hubspot", "hubspot_sandbox"),
            "display_name": "HubSpot Sandbox",
            "auth_type": "oauth2",
            "required_credentials": ("access_token",),
        },
        "salesforce": {
            "aliases": ("salesforce", "salesforce_sandbox"),
            "display_name": "Salesforce Sandbox",
            "auth_type": "oauth2",
            "required_credentials": (
                "instance_url",
                "refresh_token",
                "client_id",
                "client_secret",
            ),
        },
    },
    "Ads": {
        "google_ads": {
            "aliases": ("google_ads", "google_ads_sandbox"),
            "display_name": "Google Ads Test Customer",
            "auth_type": "oauth2",
            "required_credentials": (
                "developer_token",
                "refresh_token",
                "customer_id",
                "client_id",
                "client_secret",
            ),
        },
        "meta_ads": {
            "aliases": ("meta_ads", "meta_ads_sandbox"),
            "display_name": "Meta Ads Sandbox",
            "auth_type": "oauth2",
            "required_credentials": ("access_token", "ad_account_id"),
        },
        "linkedin_ads": {
            "aliases": ("linkedin_ads", "linkedin_ads_sandbox"),
            "display_name": "LinkedIn Ads Sandbox",
            "auth_type": "oauth2",
            "required_credentials": (
                "refresh_token",
                "account_id",
                "client_id",
                "client_secret",
            ),
        },
    },
    "Analytics": {
        "ga4": {
            "aliases": ("ga4", "ga4_sandbox"),
            "display_name": "GA4 Sandbox Property",
            "auth_type": "oauth2",
            "required_credentials": (
                "property_id",
                "refresh_token",
                "client_id",
                "client_secret",
            ),
        },
    },
    "Email": {
        "sendgrid": {
            "aliases": ("sendgrid", "sendgrid_sandbox"),
            "display_name": "SendGrid Sandbox",
            "auth_type": "api_key",
            "required_credentials": ("api_key", "sender_identity"),
        },
        "mailchimp": {
            "aliases": ("mailchimp", "mailchimp_sandbox"),
            "display_name": "Mailchimp Test Account",
            "auth_type": "api_key",
            "required_credentials": ("api_key", "server_prefix", "audience_id"),
        },
    },
}


class CMOVendorSandboxConnectorInput(BaseModel):
    connector_name: str
    display_name: str | None = None
    auth_type: str | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class CMOVendorSandboxConnectorsRequest(BaseModel):
    connectors: dict[str, CMOVendorSandboxConnectorInput] = Field(default_factory=dict)

_ZOHO_IN_BASE = "https://www.zohoapis.in/books/v3"
_ZOHO_GLOBAL_BASE = "https://www.zohoapis.com/books/v3"
_ZOHO_API_BASE_URLS = {
    "in": _ZOHO_IN_BASE,
    "us": _ZOHO_GLOBAL_BASE,
    "eu": "https://www.zohoapis.eu/books/v3",
    "au": "https://www.zohoapis.com.au/books/v3",
    "jp": "https://www.zohoapis.jp/books/v3",
}
_ZOHO_TOKEN_URLS = {
    "in": "https://accounts.zoho.in/oauth/v2/token",
    "us": "https://accounts.zoho.com/oauth/v2/token",
    "eu": "https://accounts.zoho.eu/oauth/v2/token",
    "au": "https://accounts.zoho.com.au/oauth/v2/token",
    "jp": "https://accounts.zoho.jp/oauth/v2/token",
}
_CONNECTOR_CANONICAL_TOKEN_URLS = {
    "ga4": "https://oauth2.googleapis.com/token",
    "gmail": "https://oauth2.googleapis.com/token",
    "google_ads": "https://oauth2.googleapis.com/token",
    "google_calendar": "https://oauth2.googleapis.com/token",
    "youtube": "https://oauth2.googleapis.com/token",
    "hubspot": "https://api.hubapi.com/oauth/v1/token",
    "quickbooks": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
    "banking_aa": "https://aa.finvu.in/api/v1/oauth2/token",
}
_ZOHO_REGION_HOSTS = {
    "in": ("zohoapis.in", "books.zoho.in", "accounts.zoho.in"),
    "eu": ("zohoapis.eu", "accounts.zoho.eu"),
    "au": ("zohoapis.com.au", "accounts.zoho.com.au"),
    "jp": ("zohoapis.jp", "accounts.zoho.jp"),
    "us": ("zohoapis.com", "accounts.zoho.com"),
}


def _host_from_urlish(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    return (parsed.hostname or "").rstrip(".").lower()


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _zoho_region_from_urls(values: tuple[Any, ...]) -> str | None:
    for value in values:
        host = _host_from_urlish(value)
        if not host:
            continue
        for region, domains in _ZOHO_REGION_HOSTS.items():
            if any(_host_matches(host, domain) for domain in domains):
                return region
    return None


def _normalise_connector_base_url(name: str, base_url: str | None) -> str | None:
    """Normalize known provider URL footguns before storing or displaying."""
    value = (base_url or "").strip()
    if (name or "").strip().lower() != "zoho_books":
        return value or None
    region = _zoho_region_from_urls((value,))
    if region:
        return _ZOHO_API_BASE_URLS[region]
    return _ZOHO_GLOBAL_BASE


def _infer_zoho_region(base_url: str | None, auth_config: dict[str, Any] | None) -> str:
    """Infer Zoho data center from explicit config or provider URLs."""
    config = auth_config or {}
    raw = config.get("region") or config.get("data_center") or config.get("zoho_region")
    if raw:
        normalized = str(raw).strip().lower().replace(".", "")
        aliases = {
            "india": "in",
            "in_dc": "in",
            "global": "us",
            "us_dc": "us",
            "europe": "eu",
            "eu_dc": "eu",
            "australia": "au",
            "au_dc": "au",
            "japan": "jp",
            "jp_dc": "jp",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in _ZOHO_TOKEN_URLS:
            return normalized

    return _zoho_region_from_urls(
        (
            base_url,
            config.get("base_url"),
            config.get("api_base_url"),
            config.get("token_url"),
            config.get("authorize_url"),
        )
    ) or "in"


def _clean_auth_config(auth_config: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (auth_config or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            cleaned[str(key)] = stripped
        else:
            cleaned[str(key)] = value
    return cleaned


def _canonical_connector_token_url(name: str, auth_config: dict[str, Any] | None) -> str | None:
    connector_name = str(name or "").strip().lower().replace("-", "_")
    if connector_name == "zoho_books":
        return _ZOHO_TOKEN_URLS[_infer_zoho_region(None, auth_config or {})]
    return _CONNECTOR_CANONICAL_TOKEN_URLS.get(connector_name)


def _canonicalize_connector_secret_urls(name: str, auth_config: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(auth_config)
    cleaned.pop("token_url", None)
    cleaned.pop("base_url", None)
    cleaned.pop("api_base_url", None)
    canonical_token_url = _canonical_connector_token_url(name, cleaned)
    if canonical_token_url and cleaned:
        cleaned["token_url"] = canonical_token_url
    return cleaned


def _normalise_cmo_sandbox_category(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("_", " ")
    for category in _CMO_SANDBOX_CATEGORIES:
        if text == category.lower():
            return category
    return None


def _cmo_provider_for(category: str, connector_name: str) -> tuple[str, dict[str, Any]] | None:
    normalized = str(connector_name or "").strip().lower().replace("-", "_")
    for canonical, provider in _CMO_VENDOR_SANDBOX_PROVIDERS[category].items():
        aliases = {str(alias).lower().replace("-", "_") for alias in provider["aliases"]}
        if normalized in aliases or normalized == canonical:
            return canonical, provider
    return None


def _cmo_safe_non_secret_config(config: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if _cmo_is_secret_key(str(key)):
            continue
        if isinstance(value, dict | list):
            continue
        if value is None:
            continue
        safe[str(key)] = value
    return safe


def _cmo_is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in _CMO_SECRET_KEY_MARKERS)


def _cmo_looks_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    upper = text.upper()
    return any(marker.upper() in upper for marker in _CMO_PLACEHOLDER_MARKERS)


def _cmo_vendor_sandbox_config(
    category: str,
    connector_name: str,
    raw_config: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    public_identifiers = {
        key: credentials[key]
        for key in (
            "account_id",
            "ad_account_id",
            "audience_id",
            "customer_id",
            "instance_url",
            "property_id",
            "sender_identity",
            "server_prefix",
        )
        if credentials.get(key)
    }
    return {
        **public_identifiers,
        **_cmo_safe_non_secret_config(raw_config),
        "cmo_category": category,
        "connector_provider": connector_name,
        "proof_scope": "vendor_sandbox",
        "environment_type": "vendor_sandbox",
        "local_test_only": False,
        "mock_or_test_double": False,
        "sandbox_preflight_ready": True,
        "configured_by": "ui:cmo_vendor_sandbox_connectors",
        "configured_at": datetime.now(UTC).isoformat(),
    }


def _cmo_connector_readiness(row: Any) -> tuple[str, str | None]:
    config = row.config or {}
    status = str(row.status or "").lower()
    health = str(row.health_status or "").lower()
    if status not in {"active", "configured", "connected", "healthy", "ok", "ready"}:
        return "blocked", "status_not_ready"
    if health not in {"", "active", "configured", "connected", "healthy", "ok", "ready", "unknown"}:
        return "blocked", "health_not_ready"
    if config.get("local_test_only") is True:
        return "blocked", "local_test_only"
    if config.get("mock_or_test_double") is True:
        return "blocked", "mock_or_test_double"
    if config.get("proof_scope") != "vendor_sandbox":
        return "blocked", "not_vendor_sandbox"
    if config.get("environment_type") != "vendor_sandbox":
        return "blocked", "not_vendor_sandbox"
    return "ready", None


def _cmo_connector_summary(category: str, row: Any) -> dict[str, Any]:
    readiness, reason = _cmo_connector_readiness(row)
    return {
        "category": category,
        "connector_name": str(row.connector_name or ""),
        "display_name": str(row.display_name or row.connector_name or ""),
        "source": "db",
        "readiness_state": readiness,
        "missing_reason": reason,
        "status": str(row.status or ""),
        "health_status": str(row.health_status or "unknown"),
        "proof_scope": (row.config or {}).get("proof_scope"),
        "environment_type": (row.config or {}).get("environment_type"),
        "local_test_only": bool((row.config or {}).get("local_test_only")),
        "mock_or_test_double": bool((row.config or {}).get("mock_or_test_double")),
        "credential_values_redacted": True,
    }


def _prepare_zoho_books_registration(
    body: ConnectorCreate,
    normalised_base_url: str | None,
) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """Normalize and validate no-redirect Zoho Books registration.

    Client ID + client secret identify the OAuth app, but they are not
    usable Zoho API credentials by themselves. No-redirect registration
    must therefore include refresh token material (or a one-time grant
    code that the backend can exchange) before the connector can be
    marked ready for agent execution.
    """
    secret_fields = _clean_auth_config(body.auth_config)
    region = _infer_zoho_region(normalised_base_url or body.base_url, secret_fields)
    token_url = _ZOHO_TOKEN_URLS[region]
    secret_fields["region"] = region
    secret_fields["token_url"] = token_url

    missing = [
        label
        for key, label in (
            ("client_id", "Client ID"),
            ("client_secret", "Client Secret"),
            ("organization_id", "organization_id"),
        )
        if not str(secret_fields.get(key) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "connector_validation_failed",
                "connector": "zoho_books",
                "message": "Missing required fields for Zoho Books: " + ", ".join(missing),
                "missing": missing,
            },
        )

    if not any(secret_fields.get(key) for key in ("refresh_token", "authorization_code", "grant_token", "code")):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "zoho_token_material_required",
                "connector": "zoho_books",
                "message": (
                    "Zoho Books no-redirect registration needs a refresh_token "
                    "or one-time authorization_code/grant_token in Extra config. "
                    "Client ID and Client Secret alone cannot call Zoho Books "
                    "or prove token refresh capability."
                ),
            },
        )

    non_secret_config = {
        "base_url": normalised_base_url,
        "auth_type": body.auth_type,
        "data_schema_ref": body.data_schema_ref,
        "rate_limit_rpm": body.rate_limit_rpm,
        "region": region,
        "token_url": token_url,
        "oauth_refresh_token_present": bool(secret_fields.get("refresh_token")),
    }
    return normalised_base_url, secret_fields, non_secret_config


async def _exchange_zoho_one_time_code(secret_fields: dict[str, Any], region: str) -> None:
    code = (
        secret_fields.pop("authorization_code", None)
        or secret_fields.pop("grant_token", None)
        or secret_fields.pop("code", None)
    )
    if not code:
        return
    import httpx

    token_url = _ZOHO_TOKEN_URLS[region]
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": secret_fields["client_id"],
                "client_secret": secret_fields["client_secret"],
                "redirect_uri": secret_fields.get("redirect_uri", "https://agenticorg.ai"),
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "zoho_authorization_code_rejected",
                "connector": "zoho_books",
                "message": "Zoho rejected the supplied one-time authorization code.",
                "status_code": resp.status_code,
            },
        )
    token_data = resp.json()
    if token_data.get("refresh_token"):
        secret_fields["refresh_token"] = token_data["refresh_token"]
    if token_data.get("access_token"):
        secret_fields["access_token"] = token_data["access_token"]
    if token_data.get("expires_in"):
        expires_in = int(token_data.get("expires_in") or 3600)
        secret_fields["expires_in"] = expires_in
        secret_fields["expires_at"] = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    if not secret_fields.get("refresh_token") and not secret_fields.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "zoho_token_exchange_missing_token",
                "connector": "zoho_books",
                "message": "Zoho accepted the code but did not return usable token material.",
            },
        )


async def _validate_zoho_books_readiness(secret_fields: dict[str, Any]) -> None:
    """Run the same connector path agents will use before persisting."""
    region = _infer_zoho_region(secret_fields.get("base_url"), secret_fields)
    secret_fields["region"] = region
    secret_fields["token_url"] = _ZOHO_TOKEN_URLS[region]
    await _exchange_zoho_one_time_code(secret_fields, region)
    if not secret_fields.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "zoho_refresh_token_required",
                "connector": "zoho_books",
                "message": (
                    "Zoho Books registration requires refresh_token for production "
                    "readiness. Access tokens expire and are not sufficient for "
                    "agent activation."
                ),
            },
        )

    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(dict(secret_fields))
    try:
        await connector.connect()
        health = await connector.health_check()
    finally:
        await connector.disconnect()

    if health.get("status") != "healthy":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "zoho_readiness_check_failed",
                "connector": "zoho_books",
                "message": "Zoho Books credentials could not be validated against the Books API.",
                "health_status": health.get("status", "unknown"),
            },
        )


def _connector_tool_functions(conn: Connector) -> list[str]:
    raw_tools = getattr(conn, "tool_functions", None)
    if isinstance(raw_tools, list) and raw_tools:
        return [str(tool) for tool in raw_tools]
    try:
        import connectors  # noqa: F401, PLC0415
        from connectors.registry import ConnectorRegistry

        connector_cls = ConnectorRegistry.get(str(conn.name or ""))
        class_tools = getattr(connector_cls, "tools", None) if connector_cls else None
        if class_tools:
            return [
                tool if isinstance(tool, str) else getattr(tool, "name", str(tool))
                for tool in class_tools
            ]
        if connector_cls and hasattr(connector_cls, "_register_tools"):
            instance = connector_cls.__new__(connector_cls)
            instance.config = {}
            instance._tool_registry = {}
            instance._register_tools()
            return sorted(instance._tool_registry.keys())
    # enterprise-gate: broad-except-ok reason=connector-tool-discovery-read-only-fallback
    except Exception:
        _log.debug("connector_tool_discovery_failed", exc_info=True)
    return []


async def _connector_config_credentials(
    config: Any,
    tenant_id: _uuid.UUID,
) -> dict[str, Any]:
    encrypted = getattr(config, "credentials_encrypted", None)
    if not encrypted:
        return {}
    try:
        import json as _cjson

        creds = encrypted
        if isinstance(creds, str):
            creds = _cjson.loads(creds)
        if isinstance(creds, dict) and "_encrypted" in creds:
            from core.crypto import decrypt_for_tenant

            return _cjson.loads(decrypt_for_tenant(creds["_encrypted"]))
        return creds if isinstance(creds, dict) else {}
    # enterprise-gate: broad-except-ok reason=connector-contract-secret-decrypt-fails-closed-empty
    except Exception:
        _log.debug(
            "connector_contract_credentials_decrypt_failed tenant_id=%s",
            tenant_id,
            exc_info=True,
        )
        return {}


def _assert_public_base_url(base_url: str) -> None:
    """Reject connector base_urls that can become server-side SSRF targets.

    SECURITY_AUDIT-2026-04-19 MEDIUM-12: admins could previously set any
    base_url on a connector, turning connector test/health flows into an
    SSRF primitive against the cluster's internal network (including
    169.254.169.254 cloud metadata).

    Runtime connector requests repeat this check before attaching
    credentials so stale DNS and rebinding are blocked in strict runtimes.
    """
    if not base_url:
        return
    try:
        validate_public_url(base_url, allowed_schemes=("https",), require_dns=True)
    except EgressValidationError as exc:
        if exc.reason == "scheme":
            parsed = urlparse(base_url)
            raise HTTPException(400, f"Connector base_url must be https: got '{parsed.scheme}'") from exc
        if exc.reason == "missing_host":
            raise HTTPException(400, "Connector base_url is missing a host") from exc
        raise HTTPException(
            403,
            "Connector base_url must be a public HTTPS host. Private, loopback, link-local, multicast, "
            "reserved, unspecified, and direct-IP destinations are not allowed.",
        ) from exc

router = APIRouter()


def _connector_to_dict(conn: Connector, has_encrypted_credentials: bool | None = None) -> dict:
    # Return a boolean flag for whether credentials are configured —
    # NEVER return the actual auth_config (secrets) in the API response.
    auth_config = getattr(conn, "auth_config", None)
    has_creds = (
        bool(auth_config)
        or bool(getattr(conn, "secret_ref", None))
        or bool(has_encrypted_credentials)
    )
    health_check_at = getattr(conn, "health_check_at", None)
    created_at = getattr(conn, "created_at", None)
    return {
        "id": str(conn.id),
        "connector_id": str(conn.id),  # kept for backward compat
        "name": str(conn.name or ""),
        "category": str(conn.category or ""),
        "description": conn.description or "",
        "base_url": _normalise_connector_base_url(conn.name, conn.base_url) or "",
        "auth_type": str(conn.auth_type or ""),
        "has_credentials": has_creds,
        "tool_functions": _connector_tool_functions(conn),
        "data_schema_ref": conn.data_schema_ref or "",
        "rate_limit_rpm": int(conn.rate_limit_rpm or 0),
        "timeout_ms": int(conn.timeout_ms or 0),
        "status": str(conn.status or "active"),
        "health_check_at": (
            health_check_at.isoformat()
            if hasattr(health_check_at, "isoformat")
            else None
        ),
        "created_at": (
            created_at.isoformat()
            if hasattr(created_at, "isoformat")
            else None
        ),
    }


# ── GET /connectors/registry ────────────────────────────────────────────────
@router.get("/connectors/registry", dependencies=[require_scope("connectors.registry.read")])
@route_meta(
    auth_required=True,
    tenant_required=False,
    scope="connectors.registry.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.registry.list",
)
async def list_registry_connectors(category: str | None = None):
    """Return the native connector catalog from the runtime registry.

    Each item combines the registry truth (class-level `name`, `category`,
    `auth_type`, `tools`, etc.) with display metadata from
    `connectors/catalog_meta.py`. The UI consumes this via PR-B2 instead
    of a hardcoded `NATIVE_CONNECTOR_CATALOG` array — adding / renaming /
    re-categorising a native connector no longer requires a UI code edit.
    """
    import connectors  # noqa: F401, F811
    from connectors.catalog_meta import get_meta
    from connectors.registry import ConnectorRegistry

    if category:
        classes = ConnectorRegistry.by_category(category)
    else:
        classes = [
            ConnectorRegistry.get(name)
            for name in ConnectorRegistry.all_names()
            if ConnectorRegistry.get(name) is not None
        ]

    items = []
    for cls in classes:
        tools = []
        if hasattr(cls, "tools") and cls.tools:
            tools = [t if isinstance(t, str) else getattr(t, "name", str(t)) for t in cls.tools]
        meta = get_meta(cls.name)
        items.append({
            "id": f"registry-{cls.name}",
            "connector_id": f"registry-{cls.name}",
            "name": cls.name,
            "display_name": meta["display_name"],
            "category": cls.category,
            "description": meta["description"],
            "base_url": cls.base_url,
            "auth_type": cls.auth_type,
            "tool_functions": tools,
            "rate_limit_rpm": cls.rate_limit_rpm,
            "timeout_ms": cls.timeout_ms,
            "status": "available",
            "health_check_at": None,
            "created_at": None,
        })

    # Deterministic ordering so the UI is stable across renders.
    items.sort(key=lambda x: x["display_name"].lower())

    return {"items": items, "total": len(items)}


@router.get("/connectors/contracts/marketing", dependencies=[require_scope("connectors.contracts.read")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.contracts.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.contracts.marketing.read",
)
async def marketing_connector_contracts(
    tenant_id: str = Depends(get_current_tenant),
):
    """Return connector readiness contracts used by CMO workflows."""
    from core.models.connector_config import ConnectorConfig

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        conn_result = await session.execute(
            select(Connector).where(
                Connector.tenant_id == tid,
                Connector.name == "hubspot",
                Connector.status != "deleted",
            )
        )
        hubspot = conn_result.scalar_one_or_none()
        cfg_result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tid,
                ConnectorConfig.connector_name == "hubspot",
            )
        )
        hubspot_config = cfg_result.scalar_one_or_none()

    contracts = []
    if hubspot is not None:
        credentials = (
            await _connector_config_credentials(hubspot_config, tid)
            if hubspot_config is not None
            else {}
        )
        contract = evaluate_hubspot_crm_read_contract(
            connector_status=hubspot.status,
            health_status=getattr(hubspot_config, "health_status", None),
            tool_functions=_connector_tool_functions(hubspot),
            credentials=credentials,
            config=getattr(hubspot_config, "config", {}) if hubspot_config else {},
        )
        contracts.append(contract.to_dict())

    return {
        "contracts": contracts,
        "ready": all(item.get("status") == "ready" for item in contracts),
    }


# ── GET /tools — Function-level tool names ─────────────────────────────────
@router.get("/tools", dependencies=[require_scope("connectors.tools.read")])
@route_meta(
    auth_required=True,
    tenant_required=False,
    scope="connectors.tools.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.tools.list",
)
async def list_tools(
    category: str | None = None,
    connectors: str | None = None,
):
    """Return all function-level tool names from the connector registry.

    Unlike ``GET /mcp/tools`` which returns agent-level MCP tool names
    (e.g., ``agenticorg_email_agent``), this returns the actual function-level
    tool names (e.g., ``send_email``, ``read_inbox``) that agents use.

    Query params
    ------------
    ``category``
        Optional — only return tools whose connector's category matches.
    ``connectors``
        Optional — comma-separated list of connector names. Tools are
        restricted to those belonging to the named connectors. Used by
        the agent creation UI (UR-Bug-2) so ``authorized_tools`` picker
        matches the connectors the user actually selected.
    """
    connector_filter: list[str] | None = None
    if connectors:
        connector_filter = [
            part.strip() for part in connectors.split(",") if part.strip()
        ] or None

    try:
        from core.langgraph.tool_adapter import _build_tool_index

        tool_index = _build_tool_index(connector_names=connector_filter)
        tools: list[str] = []
        tool_details: list[dict] = []
        for tool_name, (connector_name, description) in sorted(tool_index.items()):
            if category:
                # Filter by connector category if requested
                from connectors.registry import ConnectorRegistry

                cls = ConnectorRegistry.get(connector_name)
                if cls and cls.category != category:
                    continue
            tools.append(tool_name)
            tool_details.append({
                "name": tool_name,
                "connector": connector_name,
                "description": description or f"{tool_name} on {connector_name}",
            })
        return {
            "tools": tools,
            "details": tool_details,
            "total": len(tools),
        }
    # enterprise-gate: broad-except-ok reason=connector-tool-list-read-only-static-fallback
    except Exception:
        # Fallback: extract from _AGENT_TYPE_DEFAULT_TOOLS
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        all_tools: set[str] = set()
        for tools_list in _AGENT_TYPE_DEFAULT_TOOLS.values():
            all_tools.update(tools_list)
        sorted_tools = sorted(all_tools)
        return {
            "tools": sorted_tools,
            "details": [{"name": t, "connector": "unknown", "description": t} for t in sorted_tools],
            "total": len(sorted_tools),
        }


# ── GET /connectors ─────────────────────────────────────────────────────────
@router.get("/connectors", dependencies=[require_scope("connectors.read")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.list",
)
async def list_connectors(
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
):
    from sqlalchemy import func

    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Uday 2026-04-23: soft-deleted connectors ("archived") must
        # stay out of the default listing, otherwise the Connectors
        # page continues to show them and the user can't distinguish
        # "archived, can recreate" from "active". Filter on status to
        # hide them by default; they re-enter on re-registration (the
        # POST /connectors auto-restore path).
        query = select(Connector).where(
            Connector.tenant_id == tid,
            func.coalesce(Connector.status, "active") != "deleted",
        )
        count_query = (
            select(func.count())
            .select_from(Connector)
            .where(
                Connector.tenant_id == tid,
                func.coalesce(Connector.status, "active") != "deleted",
            )
        )
        if category:
            query = query.where(Connector.category == category)
            count_query = count_query.where(Connector.category == category)
        total = (await session.execute(count_query)).scalar() or 0
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        connectors = result.scalars().all()
        encrypted_names: set[str] = set()
        if connectors:
            from core.models.connector_config import ConnectorConfig

            names = [str(c.name) for c in connectors if c.name]
            cc_result = await session.execute(
                select(ConnectorConfig.connector_name).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name.in_(names),
                    ConnectorConfig.credentials_encrypted != {},
                )
            )
            encrypted_names = {str(name) for name in cc_result.scalars().all()}
    return {
        "items": [_connector_to_dict(c, c.name in encrypted_names) for c in connectors],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── POST /connectors ────────────────────────────────────────────────────────
@router.post("/connectors", status_code=201, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.create",
    rate_limit="admin-mutating",
    idempotency="tenant-connector-name-create-or-conflict",
    audit_event="connectors.create",
)
async def register_connector(
    body: ConnectorCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    normalised_base_url = _normalise_connector_base_url(body.name, body.base_url)

    # MEDIUM-12: reject SSRF-capable base_urls before persisting.
    _assert_public_base_url(normalised_base_url or "")

    # Separate secret material from non-secret config.
    # auth_config on the Connector model is deprecated for secrets —
    # new writes go to connector_configs.credentials_encrypted via
    # tenant-aware encryption.
    secret_fields = _clean_auth_config(body.auth_config)
    non_secret_config = {
        "base_url": normalised_base_url,
        "auth_type": body.auth_type,
        "data_schema_ref": body.data_schema_ref,
        "rate_limit_rpm": body.rate_limit_rpm,
    }
    if (body.name or "").strip().lower() == "zoho_books":
        normalised_base_url, secret_fields, non_secret_config = _prepare_zoho_books_registration(
            body,
            normalised_base_url,
        )
        await _validate_zoho_books_readiness(secret_fields)
        _log.info(
            "connector_registration_validated",
            extra={"connector": "zoho_books"},
        )
    else:
        secret_fields = _canonicalize_connector_secret_urls(body.name, secret_fields)

    async with get_tenant_session(tid) as session:
        connector = Connector(
            tenant_id=tid,
            name=body.name,
            category=body.category,
            base_url=normalised_base_url,
            auth_type=body.auth_type,
            auth_config={},  # no longer store secrets here
            secret_ref=body.secret_ref,
            tool_functions=body.tool_functions,
            data_schema_ref=body.data_schema_ref,
            rate_limit_rpm=body.rate_limit_rpm,
            status="active",
        )
        session.add(connector)
        try:
            await session.flush()
        except IntegrityError:
            # Uday 2026-04-23: a row with the same (tenant_id, name) may
            # already exist. If that row is status="deleted" (soft-delete
            # via DELETE /connectors/{id}), the prior flow surfaced a
            # hard 409 "already exists" — confusing, because the user
            # had just archived it and expected re-registration to
            # work. Instead, reactivate the soft-deleted twin in place
            # so audit history is preserved on a single row. If the
            # existing row is still active, we still 409 honestly.
            existing = None
            try:
                await session.rollback()
                existing_result = await session.execute(
                    select(Connector).where(
                        Connector.tenant_id == tid,
                        Connector.name == body.name,
                    )
                )
                candidate = existing_result.scalar_one_or_none()
                # Guard: real ORM row will have a .status string; a
                # broken / mock Result may return a coroutine-like
                # object. Only treat as an existing row when we can
                # safely read .status.
                if hasattr(candidate, "status") and isinstance(candidate.status, str | type(None)):
                    existing = candidate
            except (AttributeError, TypeError):
                existing = None
            except SQLAlchemyError as exc:
                _log.exception(
                    "connector_reactivation_lookup_failed conn_name=%s",
                    body.name,
                )
                raise HTTPException(
                    500, "Connector reactivation lookup failed"
                ) from exc

            if existing is not None and (existing.status or "").lower() == "deleted":
                existing.status = "active"
                existing.category = body.category
                existing.base_url = normalised_base_url
                existing.auth_type = body.auth_type
                existing.auth_config = {}
                existing.secret_ref = body.secret_ref
                existing.tool_functions = body.tool_functions
                existing.data_schema_ref = body.data_schema_ref
                existing.rate_limit_rpm = body.rate_limit_rpm
                connector = existing
                await session.flush()
            else:
                raise HTTPException(
                    409, f"Connector '{body.name}' already exists"
                ) from None

        # Store secrets in the encrypted connector_configs table
        if secret_fields:
            from core.crypto import encrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            encrypted_creds = await encrypt_for_tenant(json.dumps(secret_fields), tid)
            # Uday 2026-04-26 (BUG 1): connector_configs has its own
            # UniqueConstraint(tenant_id, connector_name). When the
            # Connector was reactivated above, an old ConnectorConfig
            # row for the same name still exists — adding a new one
            # raises IntegrityError unhandled and the API returns 500
            # (UI shows "Failed to register connector. Please try
            # again."). Upsert in place: if a config exists, replace
            # its credentials + config + auth_type; otherwise add new.
            cc_existing_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == body.name,
                )
            )
            cc_existing = cc_existing_result.scalar_one_or_none()
            if cc_existing is not None:
                cc_existing.credentials_encrypted = {"_encrypted": encrypted_creds}
                cc_existing.config = non_secret_config
                cc_existing.auth_type = body.auth_type or "api_key"
                cc_existing.status = "configured"
                cc_existing.health_status = (
                    "healthy"
                    if (body.name or "").strip().lower() == "zoho_books"
                    else cc_existing.health_status
                )
            else:
                cc = ConnectorConfig(
                    tenant_id=tid,
                    connector_name=body.name,
                    display_name=body.name,
                    auth_type=body.auth_type or "api_key",
                    credentials_encrypted={"_encrypted": encrypted_creds},
                    config=non_secret_config,
                    status="configured",
                    health_status=(
                        "healthy"
                        if (body.name or "").strip().lower() == "zoho_books"
                        else "unknown"
                    ),
                )
                session.add(cc)
            await session.flush()

    return _connector_to_dict(connector, bool(secret_fields))


@router.get("/connectors/cmo-vendor-sandbox", dependencies=[require_scope("connectors.read")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.cmo_vendor_sandbox.read",
)
async def get_cmo_vendor_sandbox_connectors(
    tenant_id: str = Depends(get_current_tenant),
):
    from core.models.connector_config import ConnectorConfig

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(ConnectorConfig).where(ConnectorConfig.tenant_id == tid)
        )
        rows = result.scalars().all()

    by_category: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = _normalise_cmo_sandbox_category((row.config or {}).get("cmo_category"))
        if category is None:
            continue
        summary = _cmo_connector_summary(category, row)
        existing = by_category.get(category)
        if existing is None or (
            summary["readiness_state"] == "ready"
            and existing.get("readiness_state") != "ready"
        ):
            by_category[category] = summary

    return {
        "status": "ready"
        if all(category in by_category for category in _CMO_SANDBOX_CATEGORIES)
        else "blocked",
        "categories": [
            by_category[category]
            for category in _CMO_SANDBOX_CATEGORIES
            if category in by_category
        ],
        "missing_categories": [
            category for category in _CMO_SANDBOX_CATEGORIES if category not in by_category
        ],
        "providers": {
            category: [
                {
                    "connector_name": canonical,
                    "display_name": str(provider["display_name"]),
                    "auth_type": str(provider["auth_type"]),
                    "required_credentials": list(provider["required_credentials"]),
                }
                for canonical, provider in providers.items()
            ]
            for category, providers in _CMO_VENDOR_SANDBOX_PROVIDERS.items()
        },
    }


@router.post(
    "/connectors/cmo-vendor-sandbox",
    dependencies=[require_scope("connectors.cmo_vendor_sandbox.write")],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.cmo_vendor_sandbox.write",
    rate_limit="admin-mutating",
    idempotency="tenant-cmo-vendor-sandbox-upsert",
    audit_event="connectors.cmo_vendor_sandbox.upsert",
)
async def upsert_cmo_vendor_sandbox_connectors(
    body: CMOVendorSandboxConnectorsRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    from core.crypto import encrypt_for_tenant
    from core.models.connector_config import ConnectorConfig

    tid = _uuid.UUID(tenant_id)
    inputs_by_category: dict[str, CMOVendorSandboxConnectorInput] = {}
    for raw_category, connector_input in body.connectors.items():
        category = _normalise_cmo_sandbox_category(raw_category)
        if category is None:
            raise HTTPException(400, f"Unsupported CMO connector category: {raw_category}")
        inputs_by_category[category] = connector_input

    missing_categories = [
        category for category in _CMO_SANDBOX_CATEGORIES if category not in inputs_by_category
    ]
    if missing_categories:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_cmo_vendor_sandbox_categories",
                "missing_categories": missing_categories,
                "message": "Configure exactly one CRM, Ads, Analytics, and Email sandbox connector.",
            },
        )

    prepared: dict[str, dict[str, Any]] = {}
    for category in _CMO_SANDBOX_CATEGORIES:
        connector_input = inputs_by_category[category]
        provider_match = _cmo_provider_for(category, connector_input.connector_name)
        if provider_match is None:
            allowed = sorted(_CMO_VENDOR_SANDBOX_PROVIDERS[category])
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported_cmo_vendor_sandbox_provider",
                    "category": category,
                    "allowed": allowed,
                    "message": f"Unsupported {category} provider.",
                },
            )
        connector_name, provider = provider_match
        credentials = _clean_auth_config(connector_input.credentials)
        missing_credentials = [
            name
            for name in provider["required_credentials"]
            if not credentials.get(name)
        ]
        placeholder_credentials = [
            name
            for name in provider["required_credentials"]
            if _cmo_looks_placeholder(credentials.get(name))
        ]
        if missing_credentials or placeholder_credentials:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_cmo_vendor_sandbox_credentials",
                    "category": category,
                    "connector_name": connector_name,
                    "missing_credentials": missing_credentials,
                    "placeholder_credentials": placeholder_credentials,
                    "message": "Real vendor-sandbox credential values are required.",
                },
            )
        auth_type = connector_input.auth_type or str(provider["auth_type"])
        prepared[category] = {
            "connector_name": connector_name,
            "display_name": connector_input.display_name or str(provider["display_name"]),
            "auth_type": auth_type,
            "credentials": credentials,
            "config": _cmo_vendor_sandbox_config(
                category,
                connector_name,
                connector_input.config,
                credentials,
            ),
        }

    summaries: list[dict[str, Any]] = []
    async with get_tenant_session(tid) as session:
        for category in _CMO_SANDBOX_CATEGORIES:
            row = prepared[category]
            encrypted = await encrypt_for_tenant(
                json.dumps(row["credentials"], sort_keys=True),
                tid,
            )

            connector_result = await session.execute(
                select(Connector).where(
                    Connector.tenant_id == tid,
                    Connector.name == row["connector_name"],
                )
            )
            connector = connector_result.scalar_one_or_none()
            if connector is None:
                session.add(
                    Connector(
                        tenant_id=tid,
                        name=row["connector_name"],
                        category="marketing",
                        auth_type=row["auth_type"],
                        auth_config={},
                        status="active",
                        description=row["display_name"],
                    )
                )
            else:
                connector.category = "marketing"
                connector.auth_type = row["auth_type"]
                connector.auth_config = {}
                connector.status = "active"
                connector.description = row["display_name"]

            config_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == row["connector_name"],
                )
            )
            existing = config_result.scalar_one_or_none()
            action = "updated" if existing is not None else "inserted"
            if existing is None:
                session.add(
                    ConnectorConfig(
                        tenant_id=tid,
                        connector_name=row["connector_name"],
                        display_name=row["display_name"],
                        auth_type=row["auth_type"],
                        credentials_encrypted={"_encrypted": encrypted},
                        config=row["config"],
                        status="configured",
                        health_status="healthy",
                    )
                )
            else:
                existing.display_name = row["display_name"]
                existing.auth_type = row["auth_type"]
                existing.credentials_encrypted = {"_encrypted": encrypted}
                existing.config = row["config"]
                existing.status = "configured"
                existing.health_status = "healthy"
                existing.sync_error = None
            summaries.append(
                {
                    "category": category,
                    "connector_name": row["connector_name"],
                    "display_name": row["display_name"],
                    "action": action,
                    "source": "db",
                    "readiness_state": "ready",
                    "credential_keys_present": sorted(row["credentials"]),
                    "credential_values_redacted": True,
                    "proof_scope": "vendor_sandbox",
                    "environment_type": "vendor_sandbox",
                    "local_test_only": False,
                    "mock_or_test_double": False,
                }
            )
        await session.commit()

    return {
        "status": "ready",
        "message": "CMO vendor-sandbox ConnectorConfig rows configured.",
        "categories": summaries,
        "missing_categories": [],
    }


# ── GET /connectors/{conn_id} ────────────────────────────────────────────────
@router.get("/connectors/{conn_id}", dependencies=[require_scope("connectors.read")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="connectors.read",
)
async def get_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        has_encrypted_credentials = False
        if connector is not None:
            from core.models.connector_config import ConnectorConfig

            cc_result = await session.execute(
                select(ConnectorConfig.id).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                    ConnectorConfig.credentials_encrypted != {},
                )
            )
            has_encrypted_credentials = cc_result.scalar_one_or_none() is not None
    if not connector:
        raise HTTPException(404, "Connector not found")
    return _connector_to_dict(connector, has_encrypted_credentials)


# ── PUT /connectors/{conn_id} ──────────────────────────────────────────────
@router.put("/connectors/{conn_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.update",
    rate_limit="admin-mutating",
    idempotency="connector-id-field-update",
    audit_event="connectors.update",
)
async def update_connector(
    conn_id: UUID,
    body: ConnectorUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")

        # Prevent blind setattr on secret-bearing or internal fields.
        # auth_config is deprecated for new writes — secrets go via
        # connector_configs.credentials_encrypted.
        _blocked_fields = {"id", "tenant_id", "auth_config", "secret_ref"}
        updates = body.model_dump(exclude_none=True)
        # MEDIUM-12: same SSRF guard on update paths.
        if "base_url" in updates:
            updates["base_url"] = _normalise_connector_base_url(
                connector.name,
                updates["base_url"],
            )
            _assert_public_base_url(updates["base_url"] or "")
        for field, value in updates.items():
            if field in _blocked_fields:
                continue
            setattr(connector, field, value)

        # If auth_config was provided in the update, MERGE it into the
        # existing encrypted credentials (PR #305 P0 fix 2026-04-24).
        #
        # Regression this guards against: the 24-Apr sweep added an
        # "Extra config (JSON)" textarea so admins could set Zoho Books
        # `organization_id`. A user editing only that field sent a
        # minimal `auth_config={"organization_id": "12345"}`. The old
        # code REPLACED `credentials_encrypted` with the encrypted
        # version of that small dict — wiping client_id / client_secret /
        # refresh_token. Next test-connection failed with "no creds".
        #
        # Fix: decrypt what's there, shallow-merge the new keys in
        # (new wins on collision), re-encrypt. Callers that genuinely
        # want to replace all creds still can — they just have to send
        # every key they intend to keep.
        raw_new_secrets = body.model_dump(exclude_none=True).get("auth_config")
        new_secrets = (
            _canonicalize_connector_secret_urls(
                connector.name,
                _clean_auth_config(raw_new_secrets),
            )
            if raw_new_secrets
            else None
        )
        credential_surface_changed = bool(new_secrets) or any(
            field in updates for field in ("base_url", "auth_type")
        )
        raw_secret_keys = (
            {str(key).strip().lower() for key in raw_new_secrets or {}}
            if isinstance(raw_new_secrets, dict)
            else set()
        )
        retarget_requested = "base_url" in updates or bool(
            raw_secret_keys.intersection(_CONNECTOR_RETARGET_KEYS)
        )
        new_secret_keys = {str(key).strip().lower() for key in new_secrets or {}}
        credential_rotated = bool(
            new_secret_keys.intersection(_CONNECTOR_CREDENTIAL_ROTATION_KEYS)
        )
        if retarget_requested and not credential_rotated:
            raise HTTPException(
                status_code=400,
                detail="Changing connector endpoint/host requires rotating credentials in the same request",
            )
        if new_secrets:
            from core.crypto import encrypt_for_tenant
            from core.crypto.tenant_secrets import decrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()

            # Endpoint/host changes are a credential boundary. Do not carry
            # old encrypted provider credentials to a new destination.
            merged_secrets: dict = {}
            if not retarget_requested and cc and cc.credentials_encrypted:
                enc = cc.credentials_encrypted.get("_encrypted") if isinstance(cc.credentials_encrypted, dict) else None
                if isinstance(enc, str) and enc:
                    # Codex PR #305 review (P1): if the stored blob
                    # exists but we cannot decrypt it (key drift,
                    # corruption, transient error), MUST NOT fall back
                    # to treating the request as a full replacement.
                    # That was the original wipe bug. Fail-closed: refuse
                    # the write, keep credentials_encrypted untouched,
                    # return a 500 with a pointer to re-registration.
                    try:
                        existing_plain = decrypt_for_tenant(enc)
                        existing = __import__("json").loads(existing_plain)
                        if isinstance(existing, dict):
                            merged_secrets.update(existing)
                        else:
                            raise ValueError(
                                "decrypted credentials are not a JSON object"
                            )
                    # enterprise-gate: broad-except-ok reason=connector-update-decrypt-fails-closed-no-credential-wipe
                    except Exception as exc:
                        _log.exception(
                            "connector_update_decrypt_failed_refusing_wipe conn_id=%s",
                            conn_id,
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                "Could not decrypt the existing connector "
                                "credentials. Refusing the update rather than "
                                "risk wiping them. Re-register the connector "
                                "via POST /connectors with the full credential "
                                "set (client_id, client_secret, refresh_token, "
                                "organization_id, etc.) to recover."
                            ),
                        ) from exc
            # New keys win on collision so admins can rotate creds.
            merged_secrets.update(new_secrets)
            zoho_non_secret_config: dict[str, Any] | None = None
            if (connector.name or "").strip().lower() == "zoho_books":
                zoho_body = ConnectorCreate(
                    name=connector.name,
                    category=connector.category,
                    base_url=connector.base_url,
                    auth_type=connector.auth_type,
                    auth_config=merged_secrets,
                    data_schema_ref=connector.data_schema_ref,
                    rate_limit_rpm=connector.rate_limit_rpm,
                )
                (
                    connector.base_url,
                    merged_secrets,
                    zoho_non_secret_config,
                ) = _prepare_zoho_books_registration(
                    zoho_body,
                    _normalise_connector_base_url(
                        connector.name,
                        connector.base_url,
                    ),
                )
                await _validate_zoho_books_readiness(merged_secrets)

            encrypted = await encrypt_for_tenant(
                __import__("json").dumps(merged_secrets), tid
            )
            if cc:
                cc.credentials_encrypted = {"_encrypted": encrypted}
                cc.auth_type = connector.auth_type or cc.auth_type
                cc.config = (
                    zoho_non_secret_config
                    if zoho_non_secret_config is not None
                    else {
                        **(cc.config or {}),
                        "base_url": connector.base_url,
                        "auth_type": connector.auth_type,
                    }
                )
                cc.status = "configured"
                cc.health_status = (
                    "healthy"
                    if zoho_non_secret_config is not None
                    else "unknown"
                )
                cc.sync_error = None
            else:
                new_cc = ConnectorConfig(
                    tenant_id=tid,
                    connector_name=connector.name,
                    display_name=connector.name,
                    auth_type=connector.auth_type or "api_key",
                    credentials_encrypted={"_encrypted": encrypted},
                    config=zoho_non_secret_config or {},
                    status="configured",
                    health_status=(
                        "healthy"
                        if zoho_non_secret_config is not None
                        else "unknown"
                    ),
                )
                session.add(new_cc)
        elif credential_surface_changed:
            from core.models.connector_config import ConnectorConfig

            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                next_config = {
                    **(cc.config or {}),
                    "base_url": connector.base_url,
                    "auth_type": connector.auth_type,
                }
                if (connector.name or "").strip().lower() == "zoho_books":
                    region = _infer_zoho_region(connector.base_url, next_config)
                    next_config["region"] = region
                    next_config["token_url"] = _ZOHO_TOKEN_URLS[region]
                cc.config = next_config
                cc.auth_type = connector.auth_type or cc.auth_type
                cc.health_status = "unknown"
                cc.sync_error = "credential_surface_changed"

        await session.commit()
        await session.refresh(connector)

    return _connector_to_dict(connector)


# ── DELETE /connectors/{conn_id} ────────────────────────────────────────────
#
# Uday 2026-04-22: the connectors page listed instances but offered no
# way to remove a stale or misconfigured one. Callers were forced to
# issue raw SQL against the DB. Expose a soft-delete (sets status to
# "deleted") so the tool listing filters them out while preserving
# audit history. Hard delete is intentionally not supported — a
# deleted connector can be restored via PUT /connectors/{id}
# (status=active).
@router.delete("/connectors/{conn_id}", status_code=200, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.delete",
    rate_limit="admin-mutating",
    idempotency="connector-id-soft-delete",
    audit_event="connectors.delete",
)
async def delete_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")
        connector.status = "deleted"

    return {
        "connector_id": str(conn_id),
        "status": "deleted",
        "message": "Connector archived. Restore via PUT with status=active.",
    }


# ── GET /connectors/{conn_id}/health ─────────────────────────────────────────
@router.get("/connectors/{conn_id}/health", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.health.read",
    rate_limit="connector-test",
    idempotency="live-health-probe-status-refresh",
    audit_event="connectors.health.read",
)
async def connector_health(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    probe = await test_connector(conn_id, tenant_id)
    health = probe.get("health") if isinstance(probe, dict) else None
    status = (
        health.get("status")
        if isinstance(health, dict)
        else ("error" if probe.get("error") else connector.status)
    )
    healthy = bool(probe.get("tested") and isinstance(health, dict) and status == "healthy")

    async with get_tenant_session(tid) as session:
        refreshed = (
            await session.execute(
                select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
            )
        ).scalar_one_or_none()
        if refreshed is not None:
            connector = refreshed

    return {
        "connector_id": str(connector.id),
        "name": connector.name,
        "status": status,
        "health_check_at": connector.health_check_at.isoformat()
        if connector.health_check_at
        else None,
        "healthy": healthy,
        "tested": bool(probe.get("tested")),
        "health": health or {},
        "error": probe.get("error"),
    }


@router.post("/connectors/{conn_id}/test", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.test",
    rate_limit="connector-test",
    idempotency="live-test-updates-health-status",
    audit_event="connectors.test",
)
async def test_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Run a live connectivity test against the connector's API.

    Uses the stored credentials to connect, run a lightweight health
    probe, and return the result. Useful for verifying credentials
    after initial setup.
    """
    import asyncio

    from connectors.registry import ConnectorRegistry

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    connector_cls = ConnectorRegistry.get(connector.name)
    if not connector_cls:
        return {"tested": False, "error": f"No connector class for '{connector.name}'"}

    # MEDIUM-12: even if the stored base_url was valid when saved, re-check
    # at test time so a stale DNS record flipped to a private IP can't be
    # used as an SSRF primitive.
    effective_base_url = _normalise_connector_base_url(
        connector.name,
        connector.base_url or getattr(connector_cls, "base_url", "") or "",
    )
    _assert_public_base_url(effective_base_url)

    # Load credentials from ENCRYPTED connector_configs first,
    # falling back to legacy plaintext auth_config for backward compat.
    config: dict = {}
    try:
        from core.models.connector_config import ConnectorConfig

        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc and cc.credentials_encrypted:
                import json as _cjson

                creds = cc.credentials_encrypted
                if isinstance(creds, str):
                    creds = _cjson.loads(creds)
                if isinstance(creds, dict) and "_encrypted" in creds:
                    from core.crypto import decrypt_for_tenant

                    creds = _cjson.loads(decrypt_for_tenant(creds["_encrypted"]))
                config = {**(cc.config or {}), **(creds or {})}
    # enterprise-gate: broad-except-ok reason=connector-test-encrypted-credential-load-falls-through-unhealthy
    except Exception:
        _log.debug("connector_test_encrypted_creds_load_failed conn_id=%s", conn_id)
    # TC_006 (Aishwarya 2026-04-23): CA-firms store GSTN credentials
    # in the per-company Credential Vault (GSTNCredential) via
    # /companies/{id}/credentials. That vault exists for multi-company
    # firms and shows "Verified" in the UI. Historically the connector
    # test only looked in ConnectorConfig.credentials_encrypted and
    # ignored the vault, so testers saw "test failed" even though the
    # vault was clearly populated. Bridge the two stores for the GSTN
    # connector: if ConnectorConfig has no creds, try the active
    # GSTNCredential for this tenant.
    if not config and connector.name.lower() in ("gstn", "gst"):
        try:
            from core.crypto import decrypt_for_tenant
            from core.models.gstn_credential import GSTNCredential

            async with get_tenant_session(tid) as session:
                gstn_result = await session.execute(
                    select(GSTNCredential)
                    .where(
                        GSTNCredential.tenant_id == tid,
                        GSTNCredential.is_active.is_(True),
                    )
                    .order_by(GSTNCredential.last_verified_at.desc())
                    .limit(1)
                )
                gstn_cred = gstn_result.scalar_one_or_none()
            if gstn_cred and gstn_cred.password_encrypted:
                decrypted_pw = decrypt_for_tenant(gstn_cred.password_encrypted)
                config = {
                    "gstin": gstn_cred.gstin,
                    "username": gstn_cred.username,
                    "password": decrypted_pw,
                    "portal_type": gstn_cred.portal_type or "gstn",
                }
        # enterprise-gate: broad-except-ok reason=connector-test-gstn-vault-fallback-falls-through-unhealthy
        except Exception:
            _log.debug("connector_test_gstn_vault_bridge_failed conn_id=%s", conn_id)

    if not config:
        # Codex 2026-04-23 re-verification blocker G: runtime execution
        # (core/tool_gateway/gateway.py) refuses to fall back to the
        # plaintext Connector.auth_config. Historically this endpoint
        # DID fall back, which gave attackers a parity gap — credentials
        # that the runtime wouldn't accept could still be exercised via
        # the test endpoint. Close the gap: refuse the test when no
        # encrypted credentials are present. Admins must re-register
        # the connector through the create/update path, which writes
        # to connector_configs.credentials_encrypted via encrypt_for_tenant.
        hint = (
            "Connector has no encrypted credentials. Re-register the "
            "connector via POST/PUT /connectors so credentials land "
            "in the encrypted vault (connector_configs.credentials_"
            "encrypted). Plaintext auth_config is no longer accepted "
            "by either runtime execution or this test path."
        )
        if connector.name.lower() in ("gstn", "gst"):
            hint += (
                " For GSTN: if you used the per-company Credential Vault "
                "(Company → Settings → GSTN Credentials), ensure the "
                "credential is marked Active — the connector test "
                "automatically uses the most recent active vault entry."
            )
        from datetime import UTC, datetime

        from core.models.connector_config import ConnectorConfig

        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                cc.last_health_check = datetime.now(UTC)
                cc.health_status = "unhealthy"
                cc.sync_error = "missing_encrypted_credentials"
        return {
            "tested": False,
            "name": connector.name,
            "error": hint,
        }
    try:
        instance = connector_cls(config)
        await asyncio.wait_for(instance.connect(), timeout=10)
        health = await asyncio.wait_for(instance.health_check(), timeout=10)
        await instance.disconnect()

        # Update last health check time
        from datetime import UTC, datetime

        async with get_tenant_session(tid) as session:
            result = await session.execute(
                select(Connector).where(Connector.id == conn_id)
            )
            db_conn = result.scalar_one_or_none()
            if db_conn:
                db_conn.health_check_at = datetime.now(UTC)
                db_conn.status = "active" if health.get("status") == "healthy" else "error"
            from core.models.connector_config import ConnectorConfig

            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                cc.last_health_check = datetime.now(UTC)
                cc.health_status = str(health.get("status") or "unknown")
                cc.sync_error = (
                    None
                    if health.get("status") == "healthy"
                    else str(
                        health.get("reason")
                        or health.get("error")
                        or "health_check_failed"
                    )
                )

        return {
            "tested": True,
            "name": connector.name,
            "health": health,
        }
    except TimeoutError:
        from datetime import UTC, datetime

        from core.models.connector_config import ConnectorConfig

        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                cc.last_health_check = datetime.now(UTC)
                cc.health_status = "unhealthy"
                cc.sync_error = "TimeoutError"
        # TC_007 (Aishwarya 2026-04-23): "Connection timed out (10s)" is
        # specific; return a hint about what to check.
        return {
            "tested": False,
            "name": connector.name,
            "error": (
                "Connection timed out after 10s. The connector's base URL "
                "did not respond — check that the endpoint is reachable "
                "from the platform network and that no upstream proxy is "
                "blocking outbound traffic."
            ),
        }
    # enterprise-gate: broad-except-ok reason=connector-live-test-records-unhealthy-no-success
    except Exception as exc:
        from datetime import UTC, datetime

        from core.models.connector_config import ConnectorConfig

        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                cc.last_health_check = datetime.now(UTC)
                cc.health_status = "unhealthy"
                cc.sync_error = type(exc).__name__
        # TC_007 (Aishwarya 2026-04-23): the route used to always return
        # "Connector test failed" regardless of the actual cause (401,
        # SSL, DNS, auth token expired, etc.). Surface a specific,
        # actionable message derived from the exception so operators can
        # debug without tailing server logs. Still suppresses secrets by
        # never echoing the raw request payload.
        _log.exception("connector_test_failed conn_id=%s name=%s", conn_id, connector.name)
        err_name = type(exc).__name__
        err_msg = str(exc)[:240]
        # Use the lowered string only for routing — the response
        # below uses ONLY ``err_name`` + a hand-mapped hint.
        lowered = (err_name + " " + err_msg).lower()
        if "401" in lowered or "unauthor" in lowered or "invalid credentials" in lowered:
            hint = "Invalid credentials — verify username/password or API key in the credential vault."
        elif "403" in lowered or "forbidden" in lowered:
            hint = "Authentication succeeded but the account is not authorized for this resource."
        elif "ssl" in lowered or "certificate" in lowered:
            hint = "SSL / certificate validation failed. Check base URL protocol and peer certificate."
        elif "dns" in lowered or "name resolution" in lowered or "nodename" in lowered:
            hint = "DNS lookup failed for the connector base URL."
        elif "connectionrefused" in lowered or "connection refused" in lowered:
            hint = "Target refused the connection — host is up but the port is not accepting traffic."
        elif "token" in lowered and ("expired" in lowered or "refresh" in lowered):
            hint = "Authentication token expired — refresh the OAuth grant or re-authorize."
        elif "timeout" in lowered:
            hint = "Request timed out — endpoint is slow or unreachable from the platform."
        else:
            # CodeQL py/stack-trace-exposure (alert #68, 2026-05-02):
            # was f"{err_name}: {err_msg}" which leaked str(exc) — the
            # exception message can carry request URLs, header
            # fragments, or stack-trace lines on common driver errors.
            # The exception class alone is enough signal for the
            # operator; full traceback is in server logs via
            # ``_log.exception`` above.
            hint = f"{err_name} (see server logs for details)"
        return {
            "tested": False,
            "name": connector.name,
            "error": hint,
            "error_class": err_name,
        }
