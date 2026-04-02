"""Authentication adapters for various connector auth types."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger()


class OAuth2Adapter:
    """OAuth2 client credentials or authorization code flow."""

    def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str = ""):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._token: str | None = None

    async def get_headers(self) -> dict[str, str]:
        if not self._token:
            await self.refresh()
        return {"Authorization": f"Bearer {self._token}"}

    async def refresh(self) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": self.scope,
                },
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]


class APIKeyAdapter:
    """Simple API key auth via header or query param."""

    def __init__(self, api_key: str, header_name: str = "X-API-Key"):
        self.api_key = api_key
        self.header_name = header_name

    async def get_headers(self) -> dict[str, str]:
        return {self.header_name: self.api_key}


class DSCAdapter:
    """Digital Signature Certificate adapter for Indian government portals.

    Loads a .pfx/.p12 certificate and signs request payloads using
    RSA-SHA256 (PKCS#1 v1.5).  Used by GSTN, EPFO, MCA Portal, and
    Income Tax connectors for filing operations that require DSC.
    """

    def __init__(self, dsc_path: str, dsc_password: str = ""):
        self.dsc_path = dsc_path
        self.dsc_password = dsc_password
        self._private_key = None
        self._certificate = None
        self._chain = None
        self._loaded = False

    def _load_pfx(self) -> None:
        """Load the PFX/P12 certificate file."""
        if self._loaded:
            return

        path = Path(self.dsc_path)
        if not path.exists():
            raise FileNotFoundError(f"DSC certificate not found: {self.dsc_path}")

        from cryptography.hazmat.primitives.serialization.pkcs12 import (
            load_key_and_certificates,
        )

        pfx_data = path.read_bytes()
        password = self.dsc_password.encode("utf-8") if self.dsc_password else None

        try:
            self._private_key, self._certificate, self._chain = (
                load_key_and_certificates(pfx_data, password)
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Failed to load DSC certificate — wrong password or corrupt file: {exc}"
            ) from exc

        if self._private_key is None:
            raise ValueError("DSC file does not contain a private key")
        if self._certificate is None:
            raise ValueError("DSC file does not contain a certificate")

        self._loaded = True
        logger.info("dsc_loaded", path=self.dsc_path)

    async def get_headers(self) -> dict[str, str]:
        """Return headers indicating DSC signing is available."""
        return {"X-DSC-Signed": "true"}

    async def sign_request(self, data: bytes) -> bytes:
        """Sign data with the DSC private key using RSA-SHA256 (PKCS#1 v1.5).

        Returns the base64-encoded signature.
        """
        self._load_pfx()

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        signature = self._private_key.sign(
            data,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        return base64.b64encode(signature)

    async def sign_and_get_headers(self, data: bytes) -> dict[str, str]:
        """Sign data and return headers with the signature attached."""
        signature_b64 = await self.sign_request(data)
        return {
            "X-DSC-Signed": "true",
            "X-DSC-Signature": signature_b64.decode("ascii"),
        }

    def verify_certificate(self) -> dict:
        """Return certificate details for inspection.

        Useful for checking expiry before filing season.
        """
        self._load_pfx()

        cert = self._certificate
        now = datetime.now(UTC)
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc

        subject_parts = {
            attr.oid._name: attr.value
            for attr in cert.subject
        }
        issuer_parts = {
            attr.oid._name: attr.value
            for attr in cert.issuer
        }

        return {
            "subject": subject_parts,
            "issuer": issuer_parts,
            "serial_number": str(cert.serial_number),
            "not_valid_before": not_before.isoformat(),
            "not_valid_after": not_after.isoformat(),
            "is_expired": now > not_after,
            "days_until_expiry": (not_after - now).days,
        }


class SCIMAdapter:
    """SCIM 2.0 adapter for identity providers like Okta."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    async def get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/scim+json",
        }
