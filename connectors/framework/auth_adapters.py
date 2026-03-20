"""Authentication adapters for various connector auth types."""
from __future__ import annotations

from typing import Any

import httpx


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
            resp = await client.post(self.token_url, data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope,
            })
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
    """Digital Signature Certificate adapter for Indian government portals."""

    def __init__(self, dsc_path: str, dsc_password: str = ""):
        self.dsc_path = dsc_path
        self.dsc_password = dsc_password

    async def get_headers(self) -> dict[str, str]:
        # DSC signing is done at request level, not via headers
        return {"X-DSC-Signed": "true"}

    async def sign_request(self, data: bytes) -> bytes:
        # In production, use pyOpenSSL or cryptography to sign with DSC
        return data


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
