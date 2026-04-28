"""Abstract base connector class."""

from __future__ import annotations

import abc
import re
from typing import Any
from xml.etree.ElementTree import Element

import httpx
import structlog
from defusedxml.ElementTree import fromstring as xml_fromstring

logger = structlog.get_logger()

# Lazy-loaded Secret Manager client (one per process)
_sm_client = None


def _get_sm_client():
    """Lazily initialise the Google Cloud Secret Manager client."""
    global _sm_client
    if _sm_client is None:
        from google.cloud import secretmanager  # noqa: F811

        _sm_client = secretmanager.SecretManagerServiceClient()
    return _sm_client


# Pattern: gcp://projects/{project}/secrets/{name}/versions/{version}
_GCP_SECRET_RE = re.compile(
    r"^gcp://projects/(?P<project>[^/]+)/secrets/(?P<secret>[^/]+)/versions/(?P<version>[^/]+)$"
)


class BaseConnector(abc.ABC):
    """Base class for all 54 connectors."""

    name: str = ""
    category: str = ""
    auth_type: str = ""
    base_url: str = ""
    rate_limit_rpm: int = 60
    timeout_ms: int = 10000

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        # Allow config to override the class-level base_url at runtime
        if "base_url" in self.config and self.config["base_url"]:
            self.base_url = self.config["base_url"]
        self._client: httpx.AsyncClient | None = None
        self._auth_headers: dict[str, str] = {}
        self._tool_registry: dict[str, Any] = {}
        self._register_tools()

    @abc.abstractmethod
    def _register_tools(self) -> None:
        """Register all tool functions for this connector."""

    def _has_credentials(self) -> bool:
        """Check whether meaningful auth credentials are configured.

        Returns False when config is empty or contains only blank strings,
        which prevents "Illegal header value b'Bearer '" errors when
        connectors attempt to authenticate with missing credentials.
        """
        if not self.config:
            return False
        # Check common credential keys
        cred_keys = [
            "api_key", "access_token", "client_id", "client_secret",
            "secret_ref", "email", "password", "token", "refresh_token",
        ]
        for key in cred_keys:
            val = self.config.get(key, "")
            if val and str(val).strip():
                return True
        # Also check for any secret_ref_* keys
        for key in self.config:
            if key.startswith("secret_ref") and self.config[key]:
                return True
        return False

    async def connect(self) -> None:
        """Initialize HTTP client and authenticate.

        Skips authentication when no credentials are configured,
        preventing 'Illegal header value' errors from empty Bearer tokens.
        """
        if self._has_credentials():
            await self._authenticate()
        else:
            logger.info(
                "connector_skip_auth_no_credentials",
                connector=self.name,
            )

        # Foundation #7 PR-D: hermetic-CI seam. When the env flag
        # is set, route every connector HTTP call through the
        # fake-connectors MockTransport so PR CI never touches a
        # real third-party API. See docs/hermetic_test_doubles.md.
        from core.test_doubles import fake_connectors  # noqa: PLC0415 — lazy keeps prod cold-path lean

        transport = (
            fake_connectors.build_transport()
            if fake_connectors.is_active()
            else None
        )

        # Create client with whatever auth headers were set (may be empty)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_ms / 1000,
            headers=self._auth_headers,
            transport=transport,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> dict[str, Any]:
        """Test connectivity. Override for connector-specific checks.

        Returns 'not_configured' when credentials are missing instead of
        attempting a request that would fail with empty Bearer tokens.

        Uday/Ramesh 2026-04-24 (UI-HEALTH-404): Gmail connector test used
        to return ``status=healthy`` when the root path returned HTTP
        404, because the base check was only proving "the network round-
        trip worked". That's a false positive — authentication + resource
        reachability both failed, but the UI flashed green. Treat only
        2xx responses as healthy; 3xx redirects are healthy too (the
        endpoint exists), but 4xx / 5xx surface as ``unhealthy`` with
        an actionable message + the HTTP status preserved for debugging.
        """
        if not self._has_credentials():
            return {
                "status": "not_configured",
                "reason": "No credentials configured for this connector",
            }
        try:
            if self._client:
                resp = await self._client.get("/")
                sc = resp.status_code
                if 200 <= sc < 400:
                    return {"status": "healthy", "http_status": sc}
                if sc in (401, 403):
                    reason = (
                        "Authentication was rejected by the upstream API "
                        f"(HTTP {sc}). Re-check credentials: OAuth2 "
                        "connectors need a valid, non-expired refresh "
                        "token; API-key connectors need the live key."
                    )
                elif sc == 404:
                    reason = (
                        "Base URL returned HTTP 404. Verify the configured "
                        "base URL targets the real API root (e.g. for Gmail "
                        "use 'https://gmail.googleapis.com' and scope the "
                        "path per tool, not '/gmail/v1')."
                    )
                elif 500 <= sc < 600:
                    reason = (
                        f"Upstream returned HTTP {sc}. The API is reachable "
                        "but reporting a server error; retry shortly or check "
                        "the provider's status page."
                    )
                else:
                    reason = f"Upstream returned HTTP {sc}, which is not a success response."
                return {"status": "unhealthy", "http_status": sc, "reason": reason}
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
        """Retrieve a secret from config, GCP Secret Manager, or fallback.

        Resolution order:
        1. Directly in config (e.g., injected by platform at runtime).
        2. Environment-style key in config (e.g., DARWINBOX_API_KEY).
        3. GCP Secret Manager via ``secret_ref`` or per-key ``secret_ref_{key}``.
           Supported format: ``gcp://projects/{project}/secrets/{name}/versions/latest``
        4. Generic fallback to config["api_key"] / config["access_token"].
        """
        # 1. Direct config lookup
        if key in self.config:
            return self.config[key]

        # 2. Environment-style key
        env_key = f"{self.name.upper()}_{key.upper()}"
        if env_key in self.config:
            return self.config[env_key]

        # 3. GCP Secret Manager — per-key ref takes priority over global ref
        per_key_ref = self.config.get(f"secret_ref_{key}", "")
        global_ref = self.config.get("secret_ref", "")
        secret_ref = per_key_ref or global_ref

        if secret_ref:
            value = self._resolve_gcp_secret(secret_ref, key)
            if value:
                return value

        # 4. Generic fallback
        return self.config.get("api_key", "") or self.config.get("access_token", "")

    @staticmethod
    def _resolve_gcp_secret(secret_ref: str, key: str) -> str:
        """Resolve a secret from Google Cloud Secret Manager.

        Args:
            secret_ref: A URI like ``gcp://projects/my-proj/secrets/my-secret/versions/latest``
                        or a plain Secret Manager resource name.
            key: The credential key being looked up (used for structured JSON secrets).

        Returns:
            The secret payload as a string, or empty string on failure.
        """
        match = _GCP_SECRET_RE.match(secret_ref)
        if not match:
            # Not a recognised GCP secret URI — skip
            logger.debug("secret_ref_not_gcp", ref=secret_ref, key=key)
            return ""

        resource_name = (
            f"projects/{match.group('project')}"
            f"/secrets/{match.group('secret')}"
            f"/versions/{match.group('version')}"
        )

        try:
            client = _get_sm_client()
            response = client.access_secret_version(request={"name": resource_name})
            payload = response.payload.data.decode("UTF-8")

            # If the payload is JSON, try to extract the specific key
            import json

            try:
                data = json.loads(payload)
                if isinstance(data, dict) and key in data:
                    return data[key]
            except (json.JSONDecodeError, TypeError):
                pass

            # Return raw payload (single-value secret)
            return payload
        except Exception as exc:
            logger.error(
                "secret_manager_fetch_failed",
                ref=secret_ref,
                key=key,
                error=str(exc),
            )
            return ""

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

    async def _post_form(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """POST with form-encoded body (application/x-www-form-urlencoded).

        Required by APIs like Stripe that reject JSON bodies.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(path, data=data)
        resp.raise_for_status()
        return resp.json()

    async def _odata_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET for OData APIs (SAP S/4HANA, Dynamics 365).

        Adds $format=json and unwraps OData response envelope.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        params = params or {}
        params.setdefault("$format", "json")
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        body = resp.json()
        # OData wraps results in d.results (v2) or value (v4)
        if "d" in body:
            return body["d"].get("results", body["d"]) if isinstance(body["d"], dict) else body["d"]
        if "value" in body:
            return body["value"] if isinstance(body["value"], list) else body
        return body

    async def _post_xml(self, xml_body: str) -> Element:
        """POST an XML body and return the parsed XML response.

        Used by connectors that speak XML over HTTP (e.g. Tally TDL).
        Posts to the base_url root with Content-Type: application/xml.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(
            "",
            content=xml_body.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )
        resp.raise_for_status()
        return xml_fromstring(resp.text)

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
