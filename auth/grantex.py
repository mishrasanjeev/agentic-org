"""Grantex/OAuth2 client for platform and agent token management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from core.config import external_keys
from core.http_retry import DEFAULT_HTTP_TIMEOUT_SECONDS, retry_http_async
from core.security.egress import EgressValidationError, validate_public_url

_TOKEN_TIMEOUT = httpx.Timeout(DEFAULT_HTTP_TIMEOUT_SECONDS)


class GrantexClient:
    """Manages OAuth2 tokens via the Grantex authorization server."""

    def __init__(self):
        self.token_server = external_keys.grantex_token_server
        self.client_id = external_keys.grantex_client_id
        self.client_secret = external_keys.grantex_client_secret
        self._platform_token: str | None = None
        self._platform_token_exp: float = 0

    def _validated_token_server(self) -> str:
        if not self.token_server:
            raise ValueError("Grantex token server is not configured")
        try:
            validated = validate_public_url(
                self.token_server.rstrip("/"),
                allowed_schemes=("https",),
                require_dns=False,
            )
        except EgressValidationError as exc:
            raise ValueError("Grantex token server must be a public HTTPS URL") from exc
        return validated.url.rstrip("/")

    @retry_http_async(max_attempts=3, base_delay=0.25, cap=2.0)
    async def _post_token(
        self,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
        path: str = "/oauth2/token",
        expect_json: bool = True,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
            resp = await client.post(
                f"{self._validated_token_server()}{path}",
                data=data,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json() if expect_json else {}

    async def get_platform_token(self) -> str:
        """Obtain platform-level token via client_credentials grant."""
        now = datetime.now(UTC).timestamp()
        if self._platform_token and now < self._platform_token_exp - 60:
            return self._platform_token

        data = await self._post_token(
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "agenticorg:orchestrate agenticorg:agents:read",
            },
        )
        self._platform_token = data["access_token"]
        self._platform_token_exp = now + data.get("expires_in", 3600)
        return self._platform_token

    async def delegate_agent_token(
        self, agent_id: str, agent_type: str, scopes: list[str], ttl: int = 3600
    ) -> dict[str, Any]:
        """Obtain scoped agent token via delegation grant."""
        return await self._post_token(
            data={
                "grant_type": "urn:grantex:agent_delegation",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "delegated_scopes": " ".join(scopes),
                "ttl": str(ttl),
            },
            headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
        )

    async def revoke_token(self, token: str) -> None:
        """Revoke a specific token."""
        await self._post_token(
            data={"token": token},
            headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
            path="/oauth2/revoke",
            expect_json=False,
        )


grantex_client = GrantexClient()
