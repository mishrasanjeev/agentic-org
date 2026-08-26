"""Provider-neutral helpers for the production voice call runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from typing import Any

import httpx

TERMINAL_CALL_STATUSES = frozenset({"completed", "busy", "failed", "no_answer", "cancelled"})


def verify_twilio_signature(*, url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    """Verify Twilio's HMAC-SHA1 webhook signature without an SDK dependency."""
    if not signature or not auth_token or not url.startswith("https://"):
        return False
    canonical = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature.strip())


def build_conversation_twiml(*, action_url: str, prompt: str, language: str = "en-IN") -> str:
    """Build escaped TwiML that speaks one turn and waits for speech."""
    response = ET.Element("Response")
    gather = ET.SubElement(
        response,
        "Gather",
        {
            "input": "speech",
            "action": action_url,
            "method": "POST",
            "speechTimeout": "auto",
            "timeout": "5",
            "language": language,
        },
    )
    say = ET.SubElement(gather, "Say", {"language": language})
    say.text = prompt[:4000]
    redirect = ET.SubElement(response, "Redirect", {"method": "POST"})
    redirect.text = action_url
    return ET.tostring(response, encoding="unicode", xml_declaration=True)


def build_hangup_twiml(message: str, language: str = "en-IN") -> str:
    """Build an escaped terminal TwiML response."""
    response = ET.Element("Response")
    say = ET.SubElement(response, "Say", {"language": language})
    say.text = message[:4000]
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode", xml_declaration=True)


async def create_twilio_call(
    *,
    account_sid: str,
    auth_token: str,
    to_number: str,
    from_number: str,
    voice_url: str,
    status_callback_url: str,
) -> dict[str, Any]:
    """Create a real outbound Twilio call and return its provider record."""
    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            endpoint,
            auth=(account_sid, auth_token),
            data={
                "To": to_number,
                "From": from_number,
                "Url": voice_url,
                "Method": "POST",
                "StatusCallback": status_callback_url,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": "initiated ringing answered completed",
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Twilio call creation failed with HTTP {response.status_code}")
    payload = response.json()
    call_sid = str(payload.get("sid") or "")
    if not call_sid:
        raise RuntimeError("Twilio call creation response did not include a call SID")
    return payload
