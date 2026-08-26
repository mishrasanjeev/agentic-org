"""Voice agent configuration endpoints.

Backs the onboarding wizard in ``ui/src/pages/VoiceSetup.tsx``. The
UI calls these three endpoints:

  * ``POST /voice/test-connection`` — probe SIP provider credentials
    without persisting anything. Session 5 TC-006: UI was getting 404.
  * ``POST /voice/config`` — save the reviewed configuration.
  * ``GET  /voice/config`` — reload a saved configuration.

These endpoints are intentionally thin. Real provider authentication
for Twilio/Vonage/SIP trunk happens in ``connectors/framework/voice/``;
this router only validates shape and reachability so the wizard can
give the user a fast pass/fail answer.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta

router = APIRouter()


def _resolve_and_block_private(host: str) -> str:
    """Resolve a hostname and reject private/reserved/link-local targets.

    SECURITY_AUDIT-2026-04-19 HIGH-06: the voice connection-test endpoint
    was a server-side SSRF primitive. We now resolve the target host and
    reject any address that is private, loopback, link-local, multicast,
    reserved, or unspecified — blocking cloud metadata (169.254.169.254),
    localhost, RFC 1918 ranges, and IPv6 equivalents.

    Returns the resolved IPv4/IPv6 literal to connect to on success.
    Raises HTTPException(400) on unresolved hosts and HTTPException(403)
    on blocked addresses.
    """
    if not host:
        raise HTTPException(400, "SIP endpoint host is empty")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(400, f"Could not resolve SIP host: {exc}") from None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(
                403,
                f"SIP endpoint resolves to a blocked address ({ip}). "
                "Private, loopback, link-local, multicast, reserved, and "
                "unspecified ranges are not allowed.",
            )
    return infos[0][4][0]


def _mask_secret(value: str | None) -> str:
    """Mask a sensitive credential for safe return to the client.

    Keeps the last 4 characters when the secret is long enough; otherwise
    returns a full mask. Empty/None stays empty so the UI can distinguish
    'not configured' from 'configured but hidden'.
    """
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]


def _mask_voice_config(data: dict) -> dict:
    """Return a copy of a saved voice config with credentials masked."""
    masked = dict(data)
    creds = dict(masked.get("credentials") or {})
    creds["account_sid"] = _mask_secret(creds.get("account_sid", ""))
    creds["auth_token"] = _mask_secret(creds.get("auth_token", ""))
    if creds.get("custom_url"):
        # SIP URIs can contain userinfo and secret-like URI parameters. The
        # configuration response only needs to show that a value is present.
        creds["custom_url"] = "***"
    masked["credentials"] = creds
    if masked.get("tts_api_key"):
        masked["tts_api_key"] = _mask_secret(masked["tts_api_key"])
    if masked.get("stt_api_key"):
        masked["stt_api_key"] = _mask_secret(masked["stt_api_key"])
    return masked

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

# ITU-T E.164 — optional leading '+', 1-15 digits. Rejects alphabetics and
# special characters (TC-012 regression).
_PHONE_E164_RE = re.compile(r"^\+?\d{1,15}$")

# SIP URI (RFC 3261 §19.1). Accepts sip:/sips: plus a user@host:port shape.
# Rejects bare words like "invalid_sip_url" (TC-007) and the `<>`/space
# characters that some misconfigured clients emit (TC-009).
_SIP_URI_RE = re.compile(
    r"^sips?:"                      # scheme
    r"(?:[A-Za-z0-9._!~*'()&=+$,;?/%-]+@)?"   # optional userinfo
    r"[A-Za-z0-9.-]+"               # host
    r"(?::\d+)?"                    # optional port
    r"(?:[;?][A-Za-z0-9._!~*'()&=+$,;?/%-]*)?$"  # optional params/headers
)

# Cloud speech engines need explicit credentials. Empty/None is invalid.
_CLOUD_TTS_ENGINES = {"azure"}
_CLOUD_STT_ENGINES = {"deepgram"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class VoiceCredentials(BaseModel):
    account_sid: str = ""
    auth_token: str = ""
    custom_url: str = ""


class VoiceTestRequest(BaseModel):
    provider: Literal["twilio", "vonage", "custom"]
    credentials: VoiceCredentials


class VoiceTestResponse(BaseModel):
    status: Literal["ok", "invalid_credentials", "network_error", "unsupported"]
    message: str


class VoiceConfig(BaseModel):
    agent_id: str | None = None
    sip_provider: Literal["twilio", "vonage", "custom"]
    credentials: VoiceCredentials
    phone_number: str = Field(..., min_length=1, max_length=16)
    stt_engine: Literal["whisper_local", "deepgram"]
    tts_engine: Literal["piper_local", "azure"]
    # Azure TTS / Deepgram STT require their own API key. Carried
    # separately so the UI can show a password-style field for each.
    tts_api_key: str | None = None
    stt_api_key: str | None = None
    runtime_status: Literal["configuration_only"] = "configuration_only"


class VoiceStatus(BaseModel):
    configured: bool
    agent_id: str | None = None
    sip_provider: str | None = None
    phone_number: str | None = None
    stt_engine: str | None = None
    tts_engine: str | None = None
    runtime_status: Literal["not_configured", "configuration_only"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_provider_credentials(provider: str, creds: VoiceCredentials) -> tuple[bool, str]:
    """Return (ok, message) for a provider+creds combination."""
    if provider == "custom":
        if not creds.custom_url.strip():
            return False, "SIP Trunk URL is required for custom provider"
        if not _SIP_URI_RE.match(creds.custom_url.strip()):
            return False, "Invalid SIP endpoint format — use sip:user@host or sips:user@host"
        return True, "SIP endpoint accepted"
    # twilio / vonage share the account_sid + auth_token shape.
    if not creds.account_sid.strip() or not creds.auth_token.strip():
        return False, f"{provider.title()} requires both Account SID and Auth Token"
    return True, "Credentials accepted"


def _validate_phone_number(phone: str) -> tuple[bool, str]:
    trimmed = phone.strip().replace(" ", "")
    if not _PHONE_E164_RE.match(trimmed):
        return False, (
            "Invalid phone number format — use E.164 (digits only, "
            "optional leading '+', 1-15 digits)"
        )
    return True, "Phone number accepted"


def _validate_voice_config(cfg: VoiceConfig) -> None:
    if cfg.agent_id:
        import uuid as _uuid

        try:
            _uuid.UUID(cfg.agent_id)
        except ValueError as exc:
            raise HTTPException(422, "agent_id must be a valid UUID") from exc
    ok, msg = _validate_provider_credentials(cfg.sip_provider, cfg.credentials)
    if not ok:
        raise HTTPException(422, msg)
    ok, msg = _validate_phone_number(cfg.phone_number)
    if not ok:
        raise HTTPException(422, msg)
    # TC-011 — cloud engines need their own credentials.
    if cfg.tts_engine in _CLOUD_TTS_ENGINES and not (cfg.tts_api_key or "").strip():
        raise HTTPException(422, f"{cfg.tts_engine} TTS requires an API key (tts_api_key)")
    if cfg.stt_engine in _CLOUD_STT_ENGINES and not (cfg.stt_api_key or "").strip():
        raise HTTPException(422, f"{cfg.stt_engine} STT requires an API key (stt_api_key)")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/voice/test-connection",
    response_model=VoiceTestResponse,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.external_probe.sensitive.write",
    rate_limit="voice-test-connection",
    idempotency="not_idempotent-external-provider-probe",
    audit_event="voice.test_connection",
)
async def test_connection(
    body: VoiceTestRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Probe SIP provider credentials without persisting.

    HIGH-05/HIGH-06 hardening: admin-only, custom SIP targets are
    resolved and filtered against private/reserved IP ranges before
    any TCP connect.
    """
    ok, msg = _validate_provider_credentials(body.provider, body.credentials)
    if not ok:
        return VoiceTestResponse(status="invalid_credentials", message=msg)

    # Twilio — GET /2010-04-01/Accounts/{sid}.json with basic auth.
    if body.provider == "twilio":
        url = f"https://api.twilio.com/2010-04-01/Accounts/{body.credentials.account_sid}.json"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    auth=(body.credentials.account_sid, body.credentials.auth_token),
                )
            if resp.status_code == 200:
                return VoiceTestResponse(status="ok", message="Twilio credentials verified")
            if resp.status_code in (401, 403):
                return VoiceTestResponse(
                    status="invalid_credentials",
                    message="Twilio rejected the credentials (HTTP 401/403)",
                )
            return VoiceTestResponse(
                status="network_error",
                message=f"Twilio returned HTTP {resp.status_code}",
            )
        except httpx.RequestError:
            return VoiceTestResponse(
                status="network_error",
                message="Could not reach Twilio — check egress/DNS",
            )

    # Vonage — verify the supplied key and secret with the balance API.
    if body.provider == "vonage":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://rest.nexmo.com/account/get-balance",
                    params={
                        "api_key": body.credentials.account_sid,
                        "api_secret": body.credentials.auth_token,
                    },
                )
            if resp.status_code == 200:
                return VoiceTestResponse(
                    status="ok",
                    message="Vonage credentials verified",
                )
            if resp.status_code in (401, 403):
                return VoiceTestResponse(
                    status="invalid_credentials",
                    message="Vonage rejected the credentials (HTTP 401/403)",
                )
            return VoiceTestResponse(
                status="network_error",
                message=f"Vonage returned HTTP {resp.status_code}",
            )
        except httpx.RequestError:
            return VoiceTestResponse(
                status="network_error",
                message="Could not reach Vonage — check egress/DNS",
            )

    # custom SIP — TCP reachability only. HTTP libraries don't speak SIP
    # (UDP/5060 + TLS/5061), so we just confirm the host:port is open.
    # No SSL bypass / no HTTP request — the real SIP handshake happens
    # via the voice connector when the agent starts.
    import asyncio as _asyncio
    import urllib.parse

    target = body.credentials.custom_url.strip()
    # Strip scheme so urlparse-like splitting works on sip:user@host:port.
    scheme_stripped = re.sub(r"^sips?:", "", target)
    scheme_stripped = scheme_stripped.lstrip("/")
    # user@host:port[;params]  ->  host:port
    host_part = scheme_stripped.split("@", 1)[-1]
    host_part = host_part.split(";", 1)[0].split("?", 1)[0]
    if ":" in host_part:
        host, _, port_str = host_part.partition(":")
        try:
            port = int(port_str)
        except ValueError:
            port = 5060
    else:
        host = host_part
        port = 5061 if target.startswith("sips:") else 5060

    host = urllib.parse.unquote(host)
    if not host:
        return VoiceTestResponse(
            status="invalid_credentials",
            message="SIP endpoint could not be parsed — missing host.",
        )

    # SSRF guard: resolve and reject private/reserved ranges before connect.
    safe_addr = _resolve_and_block_private(host)

    loop = _asyncio.get_event_loop()

    def _probe_tcp() -> str:
        family = socket.AF_INET6 if ipaddress.ip_address(safe_addr).version == 6 else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((safe_addr, port))
        return "ok"

    try:
        await loop.run_in_executor(None, _probe_tcp)
        return VoiceTestResponse(
            status="ok",
            message=f"SIP endpoint {host}:{port} is reachable (TCP)",
        )
    except (TimeoutError, socket.gaierror, OSError) as exc:
        return VoiceTestResponse(
            status="network_error",
            message=f"Could not reach SIP endpoint {host}:{port}: {type(exc).__name__}",
        )
    except RuntimeError as exc:
        return VoiceTestResponse(
            status="network_error",
            message=f"SIP probe failed: {type(exc).__name__}",
        )


_VOICE_CONFIGS_KEY = "voice_configs"
_TENANT_DEFAULT_VOICE_CONFIG = "tenant_default"


# Mapping between the voice engine choice and the AI-credentials
# provider slug. Keep in sync with
# ``core.models.tenant_ai_credential.PROVIDER_ALLOWLIST``.
_STT_ENGINE_TO_PROVIDER: dict[str, str] = {"deepgram": "stt_deepgram"}
_TTS_ENGINE_TO_PROVIDER: dict[str, str] = {"azure": "tts_azure"}


def _voice_config_key(agent_id: str | None) -> str:
    return agent_id or _TENANT_DEFAULT_VOICE_CONFIG


def _voice_config_record(body: VoiceConfig, credentials_ciphertext: str) -> dict:
    """Build the durable record without retaining plaintext credentials."""
    record = body.model_dump(
        exclude={"credentials", "stt_api_key", "tts_api_key"}
    )
    record["credentials_encrypted"] = {"_encrypted": credentials_ciphertext}
    return record


async def _save_voice_settings(tenant_uuid, body: VoiceConfig) -> None:
    """Persist agent-scoped voice settings and encrypted SIP credentials."""
    from sqlalchemy import select

    from core.crypto.tenant_secrets import encrypt_for_tenant
    from core.database import get_tenant_session
    from core.models.tenant import Tenant

    plaintext = json.dumps(body.credentials.model_dump(), separators=(",", ":"))
    ciphertext = await encrypt_for_tenant(plaintext, tenant_uuid)
    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_uuid))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(404, "Tenant not found")
        settings = dict(tenant.settings or {})
        configs = dict(settings.get(_VOICE_CONFIGS_KEY) or {})
        configs[_voice_config_key(body.agent_id)] = _voice_config_record(body, ciphertext)
        settings[_VOICE_CONFIGS_KEY] = configs
        tenant.settings = settings


def _uuid_from_string(value: str):
    import uuid as _uuid

    return _uuid.UUID(value)


async def _load_voice_record(tenant_uuid, agent_id: str | None) -> dict | None:
    """Load one durable voice record without decrypting credentials."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.tenant import Tenant

    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(select(Tenant.settings).where(Tenant.id == tenant_uuid))
        settings = result.scalar_one_or_none()
    if not isinstance(settings, dict):
        return None
    configs = settings.get(_VOICE_CONFIGS_KEY)
    if not isinstance(configs, dict):
        return None
    record = configs.get(_voice_config_key(agent_id))
    return record if isinstance(record, dict) else None


async def _load_voice_settings(tenant_uuid, agent_id: str | None) -> dict | None:
    """Load and decrypt one tenant/agent voice configuration."""
    from core.crypto.tenant_secrets import decrypt_for_tenant

    record = await _load_voice_record(tenant_uuid, agent_id)
    if record is None:
        return None
    encrypted = record.get("credentials_encrypted")
    ciphertext = encrypted.get("_encrypted") if isinstance(encrypted, dict) else None
    if not ciphertext:
        raise HTTPException(503, "Stored voice credentials are unavailable")
    try:
        credentials = json.loads(decrypt_for_tenant(ciphertext))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Stored voice credentials could not be decrypted") from exc
    data = {key: value for key, value in record.items() if key != "credentials_encrypted"}
    data["credentials"] = credentials
    return data


async def _ensure_voice_agent(tenant_uuid, agent_id: str | None) -> None:
    """Reject cross-tenant or missing agent bindings before storing secrets."""
    if not agent_id:
        return
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.agent import Agent

    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(Agent.id).where(
                Agent.id == _uuid_from_string(agent_id),
                Agent.tenant_id == tenant_uuid,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Agent not found for this tenant")


async def _save_voice_keys(tenant_uuid, body: VoiceConfig) -> None:
    """Persist stt_api_key + tts_api_key in tenant_ai_credentials.

    Upserts a row per (provider, kind). Empty keys are left alone
    (admin can set just one side).
    """
    import uuid as _uuid

    from sqlalchemy import select

    from core.ai_providers.resolver import invalidate_cache, mask_token
    from core.crypto.tenant_secrets import encrypt_for_tenant
    from core.database import get_tenant_session
    from core.models.tenant_ai_credential import TenantAICredential

    pairs: list[tuple[str, str, str]] = []  # (provider, kind, raw)
    if body.stt_api_key and body.stt_api_key.strip():
        provider = _STT_ENGINE_TO_PROVIDER.get(body.stt_engine)
        if provider:
            pairs.append((provider, "stt", body.stt_api_key.strip()))
    if body.tts_api_key and body.tts_api_key.strip():
        provider = _TTS_ENGINE_TO_PROVIDER.get(body.tts_engine)
        if provider:
            pairs.append((provider, "tts", body.tts_api_key.strip()))

    if not pairs:
        return

    for provider, kind, raw in pairs:
        ciphertext = await encrypt_for_tenant(raw, tenant_uuid)
        prefix, suffix = mask_token(raw)
        async with get_tenant_session(tenant_uuid) as session:
            result = await session.execute(
                select(TenantAICredential).where(
                    TenantAICredential.tenant_id == tenant_uuid,
                    TenantAICredential.provider == provider,
                    TenantAICredential.credential_kind == kind,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(
                    TenantAICredential(
                        id=_uuid.uuid4(),
                        tenant_id=tenant_uuid,
                        provider=provider,
                        credential_kind=kind,
                        credentials_encrypted={"_encrypted": ciphertext},
                        status="unverified",
                        display_prefix=prefix,
                        display_suffix=suffix,
                        label=f"voice.{kind}",
                    )
                )
            else:
                existing.credentials_encrypted = {"_encrypted": ciphertext}
                existing.display_prefix = prefix
                existing.display_suffix = suffix
                existing.status = "unverified"
                from datetime import UTC, datetime

                existing.rotated_at = datetime.now(UTC)
        invalidate_cache(tenant_uuid, provider, kind)


async def _voice_key_display(tenant_uuid, provider: str, kind: str) -> str | None:
    """Return a ``prefix…suffix`` masked string for the stored key, or
    ``None`` if none is configured. Never returns raw material.
    """
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.tenant_ai_credential import TenantAICredential

    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.tenant_id == tenant_uuid,
                TenantAICredential.provider == provider,
                TenantAICredential.credential_kind == kind,
            )
        )
        row = result.scalar_one_or_none()
    if row is None:
        return None
    prefix = row.display_prefix or ""
    suffix = row.display_suffix or ""
    if not prefix and not suffix:
        return "***"
    return f"{prefix}...{suffix}"


@router.post(
    "/voice/config",
    response_model=VoiceConfig,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.config.secret_control_plane.write",
    rate_limit="voice-config-write",
    idempotency="idempotent-upsert-by-tenant",
    audit_event="voice.config.save",
)
async def save_voice_config(
    body: VoiceConfig,
    tenant_id: str = Depends(get_current_tenant),
):
    """Save tenant voice config. Admin-only (HIGH-05).

    The ``stt_api_key`` and ``tts_api_key`` fields are extracted and
    persisted through the encrypted ``tenant_ai_credentials`` vault
    (S0-09 closure). The returned body is always masked — the raw
    token never leaves the server again.
    """
    import uuid as _uuid

    _validate_voice_config(body)

    try:
        tenant_uuid = _uuid.UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid tenant_id") from exc

    await _ensure_voice_agent(tenant_uuid, body.agent_id)

    # Persist STT/TTS keys in the provider vault and the SIP credential
    # bundle in tenant settings as envelope ciphertext.
    await _save_voice_keys(tenant_uuid, body)
    await _save_voice_settings(tenant_uuid, body)

    return VoiceConfig(**_mask_voice_config(body.model_dump()))


@router.get("/voice/status", response_model=VoiceStatus)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.config.status.read",
    rate_limit="voice-config-read",
    idempotency="read-only",
    audit_event="voice.config.status",
)
async def get_voice_status(
    agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> VoiceStatus:
    """Return tenant-safe setup state without loading any credentials."""
    import uuid as _uuid

    try:
        tenant_uuid = _uuid.UUID(tenant_id)
        if agent_id:
            _uuid.UUID(agent_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid tenant_id or agent_id") from exc

    record = await _load_voice_record(tenant_uuid, agent_id)
    if record is None:
        return VoiceStatus(
            configured=False,
            agent_id=agent_id,
            runtime_status="not_configured",
        )
    return VoiceStatus(
        configured=True,
        agent_id=agent_id,
        sip_provider=str(record.get("sip_provider") or "") or None,
        phone_number=str(record.get("phone_number") or "") or None,
        stt_engine=str(record.get("stt_engine") or "") or None,
        tts_engine=str(record.get("tts_engine") or "") or None,
        runtime_status="configuration_only",
    )


@router.get(
    "/voice/config",
    response_model=VoiceConfig | None,
    dependencies=[require_tenant_admin],
)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="voice.config.secret_control_plane.read",
    rate_limit="voice-config-read",
    idempotency="read-only",
    audit_event="voice.config.read",
)
async def get_voice_config(
    agent_id: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Return the saved tenant voice config with credentials masked.

    Admin-only: the encrypted SIP bundle is decrypted server-side only
    long enough to produce masked identifiers. Raw values are never
    returned to the client.
    """
    import uuid as _uuid

    try:
        tenant_uuid = _uuid.UUID(tenant_id)
        if agent_id:
            _uuid.UUID(agent_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid tenant_id or agent_id") from exc

    data = await _load_voice_settings(tenant_uuid, agent_id)
    if not data:
        return None

    # Overlay masked key displays from the vault so the UI can show
    # "sk-…abcd" without ever seeing the real body.
    stt_provider = _STT_ENGINE_TO_PROVIDER.get(data.get("stt_engine", ""))
    tts_provider = _TTS_ENGINE_TO_PROVIDER.get(data.get("tts_engine", ""))
    stt_key_display = (
        await _voice_key_display(tenant_uuid, stt_provider, "stt")
        if stt_provider else None
    )
    tts_key_display = (
        await _voice_key_display(tenant_uuid, tts_provider, "tts")
        if tts_provider else None
    )

    merged = _mask_voice_config(data)
    merged["stt_api_key"] = stt_key_display
    merged["tts_api_key"] = tts_key_display
    return VoiceConfig(**merged)
