"""SIP trunk configuration for voice agents.

Supports Twilio, Vonage, and custom SIP providers.  Credentials are
expected to be encrypted at rest (via the platform secrets manager) and
are only decrypted at connection-test time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------
PROVIDER_TWILIO = "twilio"
PROVIDER_VONAGE = "vonage"
PROVIDER_CUSTOM = "custom"
VALID_PROVIDERS = {PROVIDER_TWILIO, PROVIDER_VONAGE, PROVIDER_CUSTOM}

# Required credential keys per provider
_REQUIRED_CREDS: dict[str, list[str]] = {
    PROVIDER_TWILIO: ["account_sid", "auth_token"],
    PROVIDER_VONAGE: ["api_key", "api_secret"],
    PROVIDER_CUSTOM: ["sip_uri"],
}

# E.164 phone pattern (basic validation)
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


# ---------------------------------------------------------------------------
# SIPConfig dataclass
# ---------------------------------------------------------------------------
@dataclass
class SIPConfig:
    """SIP trunk configuration.

    Attributes
    ----------
    provider : str
        One of ``twilio``, ``vonage``, ``custom``.
    credentials : dict
        Provider-specific credentials (encrypted at rest).
    phone_number : str
        Phone number in E.164 format (e.g., ``+919876543210``).
    display_name : str
        Human-readable label for this trunk.
    metadata : dict
        Arbitrary extra metadata (region, tags, etc.).
    """

    provider: str = PROVIDER_TWILIO
    credentials: dict[str, str] = field(default_factory=dict)
    phone_number: str = ""
    display_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_sip_config(config: SIPConfig) -> list[str]:
    """Validate a SIP configuration.

    Returns
    -------
    list[str]
        List of validation error messages.  Empty list means valid.
    """
    errors: list[str] = []

    # Provider check
    if config.provider not in VALID_PROVIDERS:
        errors.append(
            f"Invalid provider '{config.provider}'. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}"
        )
        return errors  # can't validate creds without knowing provider

    # Required credentials
    required_keys = _REQUIRED_CREDS.get(config.provider, [])
    for key in required_keys:
        if not config.credentials.get(key):
            errors.append(f"Missing required credential '{key}' for provider '{config.provider}'")

    # Phone number (must be present and E.164)
    if not config.phone_number:
        errors.append("Phone number is required")
    elif not _E164_RE.match(config.phone_number):
        errors.append(
            f"Phone number '{config.phone_number}' is not valid E.164 format (e.g., +919876543210)"
        )

    return errors


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------
async def test_sip_connection(config: SIPConfig) -> dict[str, Any]:
    """Attempt a connection test against the SIP provider.

    Returns a dict with ``{success: bool, message: str, details: dict}``.

    In test/mock mode (or when provider SDKs are not installed), this
    returns a synthetic success without making real network calls.
    """
    errors = validate_sip_config(config)
    if errors:
        return {
            "success": False,
            "message": "Validation failed",
            "details": {"errors": errors},
        }

    if config.provider == PROVIDER_TWILIO:
        return await _test_twilio(config)
    elif config.provider == PROVIDER_VONAGE:
        return await _test_vonage(config)
    else:
        return await _test_custom(config)


async def _test_twilio(config: SIPConfig) -> dict[str, Any]:
    """Test Twilio SIP trunk connectivity."""
    try:
        from twilio.rest import Client  # type: ignore[import-untyped]

        client = Client(
            config.credentials["account_sid"],
            config.credentials["auth_token"],
        )
        # Lightweight API call to verify credentials
        account = client.api.accounts(config.credentials["account_sid"]).fetch()
        return {
            "success": True,
            "message": f"Twilio connected — account {account.friendly_name}",
            "details": {"status": account.status},
        }
    except ImportError:
        logger.info("twilio_sdk_not_installed", msg="returning mock success")
        return {
            "success": True,
            "message": "Twilio SDK not installed — mock success (credentials format OK)",
            "details": {"mock": True},
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Twilio connection failed: {exc}",
            "details": {},
        }


async def _test_vonage(config: SIPConfig) -> dict[str, Any]:
    """Test Vonage SIP trunk connectivity."""
    try:
        import vonage  # type: ignore[import-untyped]

        client = vonage.Client(
            key=config.credentials["api_key"],
            secret=config.credentials["api_secret"],
        )
        # Lightweight balance check
        balance = client.account.get_balance()
        return {
            "success": True,
            "message": f"Vonage connected — balance {balance}",
            "details": {"balance": str(balance)},
        }
    except ImportError:
        logger.info("vonage_sdk_not_installed", msg="returning mock success")
        return {
            "success": True,
            "message": "Vonage SDK not installed — mock success (credentials format OK)",
            "details": {"mock": True},
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Vonage connection failed: {exc}",
            "details": {},
        }


async def _test_custom(config: SIPConfig) -> dict[str, Any]:
    """Test custom SIP URI reachability."""
    sip_uri = config.credentials.get("sip_uri", "")
    if not sip_uri:
        return {
            "success": False,
            "message": "No sip_uri provided for custom provider",
            "details": {},
        }
    # For custom SIP, we just validate the URI format
    if not sip_uri.startswith("sip:"):
        return {
            "success": False,
            "message": f"Invalid SIP URI format: {sip_uri} (must start with sip:)",
            "details": {},
        }
    return {
        "success": True,
        "message": f"Custom SIP URI '{sip_uri}' format is valid",
        "details": {"mock": True, "sip_uri": sip_uri},
    }
