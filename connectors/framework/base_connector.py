"""Abstract base connector class."""

from __future__ import annotations

import abc
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class BaseConnector(abc.ABC):
    """Base class for all 42 connectors."""

    name: str = ""
    category: str = ""
    auth_type: str = ""
    base_url: str = ""
    rate_limit_rpm: int = 60
    timeout_ms: int = 10000

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._client: httpx.AsyncClient | None = None
        self._auth_headers: dict[str, str] = {}
        self._tool_registry: dict[str, Any] = {}
        self._register_tools()

    @abc.abstractmethod
    def _register_tools(self) -> None:
        """Register all tool functions for this connector."""

    async def connect(self) -> None:
        """Initialize HTTP client and authenticate."""
        # Authenticate first to populate _auth_headers
        await self._authenticate()
        # Then create client with the auth headers
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_ms / 1000,
            headers=self._auth_headers,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> dict[str, Any]:
        """Test connectivity. Override for connector-specific checks."""
        try:
            if self._client:
                resp = await self._client.get("/")
                return {"status": "healthy", "http_status": resp.status_code}
            return {"status": "not_connected"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered tool function."""
        handler = self._tool_registry.get(tool_name)
        if not handler:
            raise ValueError(f"Tool {tool_name} not registered on {self.name}")
        return await handler(**params)

    def _get_secret(self, key: str) -> str:
        """Retrieve a secret from config or secret_ref. Never hardcode credentials."""
        secret_ref = self.config.get("secret_ref", "")
        # Check config-provided credentials first (injected by platform at runtime)
        if key in self.config:
            return self.config[key]
        # Check environment-style key (e.g., DARWINBOX_API_KEY)
        env_key = f"{self.name.upper()}_{key.upper()}"
        if env_key in self.config:
            return self.config[env_key]
        # In production, this would call Google Secret Manager / Vault / K8s secrets
        # using secret_ref as the path: e.g., "agenticorg/prod/connectors/darwinbox/credentials"
        if secret_ref:
            logger.debug("secret_lookup", ref=secret_ref, key=key)
        return self.config.get("api_key", "") or self.config.get("access_token", "")

    @abc.abstractmethod
    async def _authenticate(self) -> None:
        """Perform authentication. Must set self._auth_headers.
        Use self._get_secret(key) to retrieve credentials — never hardcode tokens."""

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()

    async def _put(self, path: str, data: dict | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.put(path, json=data)
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, path: str, data: dict | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.patch(path, json=data)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.delete(path)
        resp.raise_for_status()
        return resp.json()
