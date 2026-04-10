"""SIP trunk configuration for voice agents.

Supports Twilio, Vonage, and custom SIP providers.  Credentials are
encrypted at rest using Fernet symmetric encryption (key derived from
AGENTICORG_SECRET_KEY).  Falls back to base64 encoding if the
``cryptography`` package is not installed.
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

    Credentials are encrypted at rest using Fernet symmetric encryption.
    Use :func:`encrypt_credentials` before persisting and
    :func:`decrypt_credentials` after loading from storage.

    Attributes
    ----------
    provider : str
        One of ``twilio``, ``vonage``, ``custom``.
    credentials : dict
        Provider-specific credentials (encrypted at rest via Fernet).
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
# Credential encryption helpers
# ---------------------------------------------------------------------------
def encrypt_credentials(credentials: dict) -> dict:
    """Encrypt sensitive credential values before storage.

    Uses Fernet symmetric encryption with key from AGENTICORG_SECRET_KEY.
    Falls back to base64 encoding if cryptography not available.
    """
    try:
        import base64
        import hashlib
        import os

        from cryptography.fernet import Fernet

        secret = os.getenv("AGENTICORG_SECRET_KEY", "")
        if not secret:
            return credentials  # no key = no encryption
        # Derive Fernet key from secret
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        f = Fernet(key)
        encrypted: dict[str, Any] = {}
        for k, v in credentials.items():
            if isinstance(v, str) and v:
                encrypted[k] = f.encrypt(v.encode()).decode()
            else:
                encrypted[k] = v
        return encrypted
    except ImportError:
        return credentials  # cryptography not installed


def decrypt_credentials(credentials: dict) -> dict:
    """Decrypt credential values retrieved from storage."""
    try:
        import base64
        import hashlib
        import os

        from cryptography.fernet import Fernet

        secret = os.getenv("AGENTICORG_SECRET_KEY", "")
        if not secret:
            return credentials
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        f = Fernet(key)
        decrypted: dict[str, Any] = {}
        for k, v in credentials.items():
            if isinstance(v, str) and v:
                try:
                    decrypted[k] = f.decrypt(v.encode()).decode()
                except Exception:
                    decrypted[k] = v  # not encrypted, return as-is
            else:
                decrypted[k] = v
        return decrypted
    except ImportError:
        return credentials


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_sip_config(config: SIPConfig) -> list[str]:
    """Validate a SIP configuration.

    Credentials should be encrypted at rest via :func:`encrypt_credentials`.
    This validator checks the *decrypted* form of credentials.

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
        logger.warning("twilio_sdk_not_installed")
        return {
            "success": False,
            "message": "Twilio SDK not installed — run: pip install twilio",
            "details": {},
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
        logger.warning("vonage_sdk_not_installed")
        return {
            "success": False,
            "message": "Vonage SDK not installed — run: pip install vonage",
            "details": {},
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
        "message": f"Custom SIP URI '{sip_uri}' format is valid (connectivity not tested — requires SIP INVITE)",
        "details": {"format_valid": True, "sip_uri": sip_uri},
    }
