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
import re
import socket
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, require_tenant_admin

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

# TC-011 — Google TTS needs explicit credentials. Empty/None is invalid.
_CLOUD_TTS_ENGINES = {"google"}
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
    sip_provider: Literal["twilio", "vonage", "custom"]
    credentials: VoiceCredentials
    phone_number: str = Field(..., min_length=1, max_length=16)
    stt_engine: Literal["whisper_local", "deepgram"]
    tts_engine: Literal["piper_local", "google"]
    # TC-011 — Google TTS / Deepgram STT require their own API key. Carried
    # separately so the UI can show a password-style field for each.
    tts_api_key: str | None = None
    stt_api_key: str | None = None


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
        except httpx.ConnectError:
            return VoiceTestResponse(
                status="network_error",
                message="Could not reach Twilio — check egress/DNS",
            )

    # Vonage — GET /account/get-balance with signed query. Kept thin: we
    # only verify the credentials aren't empty and that api.vonage.com is
    # reachable. The real connector does the signed call.
    if body.provider == "vonage":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://rest.nexmo.com/account/get-balance")
            if resp.status_code in (200, 401, 403):
                return VoiceTestResponse(
                    status="ok",
                    message="Vonage endpoint reachable — credentials will be verified on first call",
                )
            return VoiceTestResponse(
                status="network_error",
                message=f"Vonage returned HTTP {resp.status_code}",
            )
        except httpx.ConnectError:
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
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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
    except Exception as exc:  # noqa: BLE001 — user-facing probe response
        return VoiceTestResponse(
            status="network_error",
            message=f"SIP probe failed: {type(exc).__name__}",
        )


# In-memory per-tenant config store. Replaced with a DB table when the
# Voice Agent feature graduates from beta.
_VOICE_CONFIG: dict[str, dict] = {}


@router.post(
    "/voice/config",
    response_model=VoiceConfig,
    dependencies=[require_tenant_admin],
)
async def save_voice_config(
    body: VoiceConfig,
    tenant_id: str = Depends(get_current_tenant),
):
    """Save tenant voice config. Admin-only (HIGH-05)."""
    _validate_voice_config(body)
    _VOICE_CONFIG[str(tenant_id)] = body.model_dump()
    # Return masked copy so secrets aren't echoed back in the response.
    return VoiceConfig(**_mask_voice_config(body.model_dump()))


@router.get(
    "/voice/config",
    response_model=VoiceConfig | None,
    dependencies=[require_tenant_admin],
)
async def get_voice_config(tenant_id: str = Depends(get_current_tenant)):
    """Return the saved tenant voice config with credentials masked.

    HIGH-05 fix — pre-fix any authenticated tenant user could read the
    full credentials. Now admin-only, and secrets are masked on return.
    """
    data = _VOICE_CONFIG.get(str(tenant_id))
    if not data:
        return None
    return VoiceConfig(**_mask_voice_config(data))
