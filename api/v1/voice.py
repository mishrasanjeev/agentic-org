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

import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_current_tenant

router = APIRouter()

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


@router.post("/voice/test-connection", response_model=VoiceTestResponse)
async def test_connection(
    body: VoiceTestRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Probe SIP provider credentials without persisting.

    Session 5 TC-006 root cause: this endpoint did not exist, so the
    wizard's Test Connection button always returned HTTP 404.
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

    # custom SIP — OPTIONS on the trunk URL.
    target = body.credentials.custom_url.strip()
    # Map sip://host -> http://host for the probe, since HTTP libraries
    # don't speak SIP directly. This is just a reachability check.
    probe = re.sub(r"^sips?:", "https://", target)
    probe = re.sub(r"^sip:", "http://", probe)
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:  # noqa: S501 - reachability only
            resp = await client.request("OPTIONS", probe)
        return VoiceTestResponse(
            status="ok",
            message=f"SIP endpoint reachable (HTTP {resp.status_code})",
        )
    except httpx.ConnectError:
        return VoiceTestResponse(
            status="network_error",
            message=f"Could not reach SIP endpoint at {target}",
        )
    except Exception as exc:  # noqa: BLE001 — report shape, not trace
        return VoiceTestResponse(
            status="network_error",
            message=f"SIP probe failed: {type(exc).__name__}",
        )


# In-memory per-tenant config store. Replaced with a DB table when the
# Voice Agent feature graduates from beta.
_VOICE_CONFIG: dict[str, dict] = {}


@router.post("/voice/config", response_model=VoiceConfig)
async def save_voice_config(
    body: VoiceConfig,
    tenant_id: str = Depends(get_current_tenant),
):
    _validate_voice_config(body)
    _VOICE_CONFIG[str(tenant_id)] = body.model_dump()
    return body


@router.get("/voice/config", response_model=VoiceConfig | None)
async def get_voice_config(tenant_id: str = Depends(get_current_tenant)):
    data = _VOICE_CONFIG.get(str(tenant_id))
    if not data:
        return None
    return VoiceConfig(**data)
