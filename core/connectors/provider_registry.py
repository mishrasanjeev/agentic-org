"""Multi-provider OAuth + setup registry.

Source of truth for which OAuth/non-OAuth flow each connector uses, which
URLs it talks to, which region variants it supports, and which fields the
client UI must collect from the tenant admin.

Why this exists
---------------
Pre-2026-05-14 the OAuth handler held a flat ``OAUTH_PROVIDERS`` dict with
hardcoded ``accounts.zoho.com`` URLs and a generic UI form that collected a
``client_id``/``client_secret`` pair plus a raw JSON blob for everything
else. That broke for Zoho India accounts (wrong DC for the authorize URL),
broke for Banking AA / GSTN / DSC-signed flows entirely (no OAuth2 at all),
and gave the user nowhere to declare ``organization_id``, ``fiu_id``, or
``region`` as first-class inputs.

The registry isolates per-provider quirks behind a ``ProviderSpec`` so the
HTTP handler stays generic. Adding a provider means a new
``register_provider(spec)`` call; nothing else in the OAuth handler needs
to change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# ── Region helpers ────────────────────────────────────────────────────────────

ZOHO_REGIONS: dict[str, dict[str, str]] = {
    "us": {
        "authorize_url": "https://accounts.zoho.com/oauth/v2/auth",
        "token_url": "https://accounts.zoho.com/oauth/v2/token",
        "revoke_url": "https://accounts.zoho.com/oauth/v2/token/revoke",
        "api_base_url": "https://www.zohoapis.com/books/v3",
    },
    "in": {
        "authorize_url": "https://accounts.zoho.in/oauth/v2/auth",
        "token_url": "https://accounts.zoho.in/oauth/v2/token",
        "revoke_url": "https://accounts.zoho.in/oauth/v2/token/revoke",
        "api_base_url": "https://books.zoho.in/api/v3",
    },
    "eu": {
        "authorize_url": "https://accounts.zoho.eu/oauth/v2/auth",
        "token_url": "https://accounts.zoho.eu/oauth/v2/token",
        "revoke_url": "https://accounts.zoho.eu/oauth/v2/token/revoke",
        "api_base_url": "https://www.zohoapis.eu/books/v3",
    },
    "au": {
        "authorize_url": "https://accounts.zoho.com.au/oauth/v2/auth",
        "token_url": "https://accounts.zoho.com.au/oauth/v2/token",
        "revoke_url": "https://accounts.zoho.com.au/oauth/v2/token/revoke",
        "api_base_url": "https://www.zohoapis.com.au/books/v3",
    },
    "jp": {
        "authorize_url": "https://accounts.zoho.jp/oauth/v2/auth",
        "token_url": "https://accounts.zoho.jp/oauth/v2/token",
        "revoke_url": "https://accounts.zoho.jp/oauth/v2/token/revoke",
        "api_base_url": "https://www.zohoapis.jp/books/v3",
    },
}
ZOHO_REGION_HOSTS: dict[str, tuple[str, ...]] = {
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
        for region, domains in ZOHO_REGION_HOSTS.items():
            if any(_host_matches(host, domain) for domain in domains):
                return region
    return None


def _zoho_region(extra: dict[str, Any]) -> str:
    """Pick the Zoho data-center suffix from user-supplied config.

    Accepts any of ``region``, ``data_center``, or ``zoho_region`` and
    falls back to the Zoho API/account host when no explicit region is
    provided. India remains the default for AgenticOrg's primary CA-firms
    market.
    """
    raw = (
        extra.get("region")
        or extra.get("data_center")
        or extra.get("zoho_region")
    )
    if raw:
        normalized = str(raw).strip().lower().replace(".", "")
        aliases = {
            "india": "in",
            "in_dc": "in",
            "us_dc": "us",
            "global": "us",
            "eu_dc": "eu",
            "europe": "eu",
            "au_dc": "au",
            "australia": "au",
            "jp_dc": "jp",
            "japan": "jp",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in ZOHO_REGIONS:
            return normalized

    return _zoho_region_from_urls(
        tuple(extra.get(key) for key in ("base_url", "api_base_url", "token_url", "authorize_url"))
    ) or "in"


# ── Spec dataclass ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderField:
    """One field the UI must collect from the tenant admin.

    ``secret`` controls whether the input is rendered as a password field
    and whether its value is encrypted in the connector_config vault.
    ``required`` is enforced both client-side and server-side.
    """

    key: str
    label: str
    placeholder: str = ""
    help_text: str = ""
    secret: bool = False
    required: bool = True
    options: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProviderSpec:
    """OAuth + setup metadata for a single connector.

    Attributes
    ----------
    connector_name : registry key (lowercase, underscore separated)
    display_name   : human-readable label
    category       : matches ``Connector.category`` (finance, comms, etc.)
    auth_flow      : "oauth2_authorization_code" | "client_credentials" |
                     "api_key" | "dsc_signed" | "aa_consent"
    scopes         : OAuth scope strings (joined with spaces)
    authorization_params : provider quirks (access_type, prompt, etc.)
    region_resolver: callable ``extra_config -> region_key`` (Zoho-style)
    region_url_map : ``region_key -> {authorize_url, token_url, revoke_url,
                     api_base_url}`` — used when region_resolver is set.
    authorize_url, token_url, revoke_url, api_base_url:
                     fallbacks when there's no region split.
    user_fields    : ordered list rendered by the UI.
    requires_organization_id : Zoho-style external id post-auth.
    supports_refresh_token  : false for AA/GSTN.
    documentation_url : provider docs surfaced in the UI.
    """

    connector_name: str
    display_name: str
    category: str
    auth_flow: str
    scopes: tuple[str, ...] = ()
    authorization_params: dict[str, str] = field(default_factory=dict)
    region_resolver: Callable[[dict[str, Any]], str] | None = None
    region_url_map: dict[str, dict[str, str]] = field(default_factory=dict)
    authorize_url: str = ""
    token_url: str = ""
    revoke_url: str = ""
    api_base_url: str = ""
    user_fields: tuple[ProviderField, ...] = ()
    requires_organization_id: bool = False
    supports_refresh_token: bool = True
    documentation_url: str = ""

    # ── Convenience accessors ────────────────────────────────────────────

    def resolve_region(self, extra_config: dict[str, Any]) -> str:
        if self.region_resolver is None:
            return ""
        return self.region_resolver(extra_config or {})

    def urls_for(self, extra_config: dict[str, Any]) -> dict[str, str]:
        """Return ``authorize_url``/``token_url``/``revoke_url``/``api_base_url``
        for the region implied by *extra_config*. Falls back to the
        non-region defaults when no region resolver is configured.
        """
        if self.region_resolver and self.region_url_map:
            region = self.resolve_region(extra_config)
            if region in self.region_url_map:
                return dict(self.region_url_map[region])
        return {
            "authorize_url": self.authorize_url,
            "token_url": self.token_url,
            "revoke_url": self.revoke_url,
            "api_base_url": self.api_base_url,
        }

    def schema_dict(self) -> dict[str, Any]:
        """Serializable form for ``GET /connectors/oauth/providers``."""
        return {
            "connector_name": self.connector_name,
            "display_name": self.display_name,
            "category": self.category,
            "auth_flow": self.auth_flow,
            "scopes": list(self.scopes),
            "requires_organization_id": self.requires_organization_id,
            "supports_refresh_token": self.supports_refresh_token,
            "documentation_url": self.documentation_url,
            "regions": (
                sorted(self.region_url_map.keys())
                if self.region_url_map
                else []
            ),
            "user_fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "placeholder": f.placeholder,
                    "help_text": f.help_text,
                    "secret": f.secret,
                    "required": f.required,
                    "options": (
                        [{"value": v, "label": label} for v, label in f.options]
                        if f.options
                        else []
                    ),
                }
                for f in self.user_fields
            ],
        }


# ── Registry storage ──────────────────────────────────────────────────────────

# enterprise-gate: process-local-ok reason=static-built-in-provider-spec-registry
_REGISTRY: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> None:
    """Register *spec* under its normalized connector_name."""
    key = _normalize_name(spec.connector_name)
    _REGISTRY[key] = spec


def get_provider(connector_name: str) -> ProviderSpec | None:
    return _REGISTRY.get(_normalize_name(connector_name))


def all_providers() -> list[ProviderSpec]:
    return list(_REGISTRY.values())


def supported_oauth_names() -> list[str]:
    """OAuth-only names — the legacy endpoint surface."""
    return sorted(
        spec.connector_name
        for spec in _REGISTRY.values()
        if spec.auth_flow == "oauth2_authorization_code"
    )


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower().replace("-", "_")


# ── Built-in providers ────────────────────────────────────────────────────────

# Common Google fields used by gmail / google_calendar / youtube.
_GOOGLE_OAUTH_FIELDS: tuple[ProviderField, ...] = (
    ProviderField(
        key="client_id",
        label="OAuth Client ID",
        placeholder="123456789-abcdef.apps.googleusercontent.com",
        help_text="Google Cloud Console → APIs & Services → Credentials.",
    ),
    ProviderField(
        key="client_secret",
        label="OAuth Client Secret",
        placeholder="GOCSPX-…",
        secret=True,
        help_text="Same Credentials screen — kept encrypted at rest.",
    ),
)


def _bootstrap() -> None:
    """Idempotent registration of the initial provider set."""
    if _REGISTRY:
        return

    register_provider(
        ProviderSpec(
            connector_name="gmail",
            display_name="Gmail",
            category="comms",
            auth_flow="oauth2_authorization_code",
            scopes=(
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ),
            authorization_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            },
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            revoke_url="https://oauth2.googleapis.com/revoke",
            api_base_url="https://gmail.googleapis.com/gmail/v1",
            user_fields=_GOOGLE_OAUTH_FIELDS,
            documentation_url=(
                "https://developers.google.com/identity/protocols/oauth2"
            ),
        )
    )

    register_provider(
        ProviderSpec(
            connector_name="google_calendar",
            display_name="Google Calendar",
            category="comms",
            auth_flow="oauth2_authorization_code",
            scopes=("https://www.googleapis.com/auth/calendar",),
            authorization_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            },
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            revoke_url="https://oauth2.googleapis.com/revoke",
            api_base_url="https://www.googleapis.com/calendar/v3",
            user_fields=_GOOGLE_OAUTH_FIELDS,
        )
    )

    register_provider(
        ProviderSpec(
            connector_name="youtube",
            display_name="YouTube Data",
            category="marketing",
            auth_flow="oauth2_authorization_code",
            scopes=(
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly",
            ),
            authorization_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            },
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            revoke_url="https://oauth2.googleapis.com/revoke",
            api_base_url="https://www.googleapis.com/youtube/v3",
            user_fields=_GOOGLE_OAUTH_FIELDS,
        )
    )

    register_provider(
        ProviderSpec(
            connector_name="zoho_books",
            display_name="Zoho Books",
            category="finance",
            auth_flow="oauth2_authorization_code",
            scopes=("ZohoBooks.fullaccess.all",),
            authorization_params={
                "access_type": "offline",
                "prompt": "consent",
            },
            region_resolver=_zoho_region,
            region_url_map=ZOHO_REGIONS,
            requires_organization_id=True,
            documentation_url=(
                "https://www.zoho.com/books/api/v3/introduction/#oauth"
            ),
            user_fields=(
                ProviderField(
                    key="client_id",
                    label="Client ID",
                    placeholder="1000.XXXXXXXX",
                    help_text=(
                        "Zoho API Console → Self Client / Server-based App."
                    ),
                ),
                ProviderField(
                    key="client_secret",
                    label="Client Secret",
                    placeholder="••••••",
                    secret=True,
                ),
                ProviderField(
                    key="region",
                    label="Zoho Region",
                    help_text=(
                        "Optional override. When omitted, AgenticOrg "
                        "infers the region from the Zoho API base URL."
                    ),
                    required=False,
                    options=(
                        ("in", "India (zoho.in)"),
                        ("us", "United States / Global (zoho.com)"),
                        ("eu", "Europe (zoho.eu)"),
                        ("au", "Australia (zoho.com.au)"),
                        ("jp", "Japan (zoho.jp)"),
                    ),
                ),
                ProviderField(
                    key="organization_id",
                    label="Organization ID",
                    placeholder="60069102279",
                    help_text=(
                        "Zoho Books → Settings → Organizations. Required "
                        "for every API call."
                    ),
                ),
            ),
        )
    )

    register_provider(
        ProviderSpec(
            connector_name="banking_aa",
            display_name="Banking — Account Aggregator",
            category="finance",
            auth_flow="client_credentials",
            scopes=(),
            supports_refresh_token=False,
            documentation_url="https://api.rebit.org.in/",
            api_base_url="https://aa.finvu.in/api/v1",
            user_fields=(
                ProviderField(
                    key="client_id",
                    label="FIU Client ID",
                    placeholder="Issued by your AA provider",
                ),
                ProviderField(
                    key="client_secret",
                    label="FIU Client Secret",
                    placeholder="••••••",
                    secret=True,
                ),
                ProviderField(
                    key="fiu_id",
                    label="FIU ID",
                    placeholder="FIU-AGENTICORG-01",
                    help_text="Your Financial Information User identifier.",
                ),
                ProviderField(
                    key="customer_vua",
                    label="Customer VUA",
                    placeholder="customer@aa-provider",
                    required=False,
                    help_text=(
                        "Optional Virtual User Address used as the default "
                        "subject of consent requests."
                    ),
                ),
            ),
        )
    )

    register_provider(
        ProviderSpec(
            connector_name="gstn",
            display_name="GSTN (GST Network)",
            category="finance",
            auth_flow="api_key",
            scopes=(),
            supports_refresh_token=False,
            documentation_url="https://developer.gstsystem.co.in/",
            api_base_url="https://gsp.adaequare.com/gsp",
            user_fields=(
                ProviderField(
                    key="client_id",
                    label="GSP App ID (gspappid)",
                    placeholder="gspappid from Adaequare GSP",
                ),
                ProviderField(
                    key="client_secret",
                    label="GSP App Secret (gspappsecret)",
                    placeholder="gspappsecret from Adaequare GSP",
                    secret=True,
                ),
                ProviderField(
                    key="gstin",
                    label="GSTIN",
                    placeholder="09AABCU9355J1ZS",
                ),
                ProviderField(
                    key="dsc_path",
                    label="DSC Certificate Path (optional)",
                    required=False,
                    help_text=(
                        "Server-side path to the Digital Signature "
                        "Certificate file used to sign GSTR-3B/9 filings."
                    ),
                ),
            ),
        )
    )


_bootstrap()


# ── Helpers used by the OAuth router ──────────────────────────────────────────


def authorize_url_for(
    spec: ProviderSpec, extra_config: dict[str, Any]
) -> str:
    return spec.urls_for(extra_config)["authorize_url"]


def token_url_for(
    spec: ProviderSpec, extra_config: dict[str, Any]
) -> str:
    return spec.urls_for(extra_config)["token_url"]


def revoke_url_for(
    spec: ProviderSpec, extra_config: dict[str, Any]
) -> str:
    return spec.urls_for(extra_config)["revoke_url"]


def api_base_url_for(
    spec: ProviderSpec, extra_config: dict[str, Any]
) -> str:
    return spec.urls_for(extra_config)["api_base_url"]
