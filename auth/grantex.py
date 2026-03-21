"""Grantex/OAuth2 client for platform and agent token management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from core.config import external_keys


class GrantexClient:
    """Manages OAuth2 tokens via the Grantex authorization server."""

    def __init__(self):
        self.token_server = external_keys.grantex_token_server
        self.client_id = external_keys.grantex_client_id
        self.client_secret = external_keys.grantex_client_secret
        self._platform_token: str | None = None
        self._platform_token_exp: float = 0

    async def get_platform_token(self) -> str:
        """Obtain platform-level token via client_credentials grant."""
        now = datetime.now(UTC).timestamp()
        if self._platform_token and now < self._platform_token_exp - 60:
            return self._platform_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.token_server}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "agenticorg:orchestrate agenticorg:agents:read",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._platform_token = data["access_token"]
            self._platform_token_exp = now + data.get("expires_in", 3600)
            return self._platform_token

    async def delegate_agent_token(
        self, agent_id: str, agent_type: str, scopes: list[str], ttl: int = 3600
    ) -> dict[str, Any]:
        """Obtain scoped agent token via delegation grant."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.token_server}/oauth2/token",
                data={
                    "grant_type": "urn:grantex:agent_delegation",
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                    "delegated_scopes": " ".join(scopes),
                    "ttl": str(ttl),
                },
                headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke_token(self, token: str) -> None:
        """Revoke a specific token."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.token_server}/oauth2/revoke",
                data={"token": token},
                headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
            )


grantex_client = GrantexClient()
