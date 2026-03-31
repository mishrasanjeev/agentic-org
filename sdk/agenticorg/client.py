"""AgenticOrg SDK client."""

from __future__ import annotations

import os
from typing import Any

import httpx


class AgenticOrg:
    """AgenticOrg Python SDK client.

    Usage:
        client = AgenticOrg(api_key="your-key")
        result = client.agents.run("ap_processor", inputs={"invoice_id": "INV-001"})
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        grantex_token: str | None = None,
        timeout: float = 60.0,
    ):
        self._api_key = api_key or os.getenv("AGENTICORG_API_KEY", "")
        self._base_url = (base_url or os.getenv("AGENTICORG_BASE_URL", "https://app.agenticorg.ai")).rstrip("/")
        self._grantex_token = grantex_token or os.getenv("AGENTICORG_GRANTEX_TOKEN", "")
        self._timeout = timeout

        if not self._api_key and not self._grantex_token:
            raise ValueError(
                "Provide api_key or grantex_token, or set "
                "AGENTICORG_API_KEY / AGENTICORG_GRANTEX_TOKEN env var."
            )

        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers=self._build_headers(),
        )

        self.agents = _AgentsResource(self._http)
        self.connectors = _ConnectorsResource(self._http)
        self.sop = _SOPResource(self._http)
        self.a2a = _A2AResource(self._http)
        self.mcp = _MCPResource(self._http)

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._grantex_token:
            headers["Authorization"] = f"Bearer {self._grantex_token}"
        elif self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _AgentsResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def list(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List all agents."""
        params = {}
        if domain:
            params["domain"] = domain
        resp = self._http.get("/api/v1/agents", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def get(self, agent_id: str) -> dict[str, Any]:
        """Get agent details."""
        resp = self._http.get(f"/api/v1/agents/{agent_id}")
        resp.raise_for_status()
        return resp.json()

    def run(
        self,
        agent_id_or_type: str,
        *,
        action: str = "process",
        inputs: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run an agent.

        Args:
            agent_id_or_type: Agent UUID or agent type (e.g., "ap_processor").
                If a type is given, uses the A2A task endpoint.
            action: Action to perform (default: "process").
            inputs: Task input data.
            context: Additional context.

        Returns:
            Execution result with status, output, confidence, trace.
        """
        payload: dict[str, Any] = {
            "action": action,
            "inputs": inputs or {},
            "context": context or {},
        }

        # If it looks like a UUID, use the direct agent run endpoint
        if "-" in agent_id_or_type and len(agent_id_or_type) > 30:
            resp = self._http.post(f"/api/v1/agents/{agent_id_or_type}/run", json=payload)
        else:
            # Use A2A task endpoint for agent type
            resp = self._http.post("/api/v1/a2a/tasks", json={
                "agent_type": agent_id_or_type,
                **payload,
            })

        resp.raise_for_status()
        return resp.json()

    def create(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new agent."""
        resp = self._http.post("/api/v1/agents", json=kwargs)
        resp.raise_for_status()
        return resp.json()


class _ConnectorsResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def list(self, category: str | None = None) -> list[dict[str, Any]]:
        params = {}
        if category:
            params["category"] = category
        resp = self._http.get("/api/v1/connectors", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def get(self, connector_id: str) -> dict[str, Any]:
        resp = self._http.get(f"/api/v1/connectors/{connector_id}")
        resp.raise_for_status()
        return resp.json()


class _SOPResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def parse_text(
        self,
        text: str,
        domain_hint: str = "",
        llm_model: str = "",
    ) -> dict[str, Any]:
        """Parse SOP text and return draft agent config."""
        resp = self._http.post("/api/v1/sop/parse-text", json={
            "text": text,
            "domain_hint": domain_hint,
            "llm_model": llm_model,
        })
        resp.raise_for_status()
        return resp.json()

    def upload(
        self,
        file_path: str,
        domain_hint: str = "",
    ) -> dict[str, Any]:
        """Upload a PDF/markdown file and parse it."""
        with open(file_path, "rb") as f:
            resp = self._http.post(
                "/api/v1/sop/upload",
                files={"file": f},
                data={"domain_hint": domain_hint},
                headers={},  # Let httpx set multipart headers
            )
        resp.raise_for_status()
        return resp.json()

    def deploy(self, config: dict[str, Any]) -> dict[str, Any]:
        """Deploy a reviewed SOP config as a shadow agent."""
        resp = self._http.post("/api/v1/sop/deploy", json={"config": config})
        resp.raise_for_status()
        return resp.json()


class _A2AResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def agent_card(self) -> dict[str, Any]:
        """Get the A2A agent discovery card."""
        resp = self._http.get("/api/v1/a2a/agent-card")
        resp.raise_for_status()
        return resp.json()

    def agents(self) -> list[dict[str, Any]]:
        """List available agent types for A2A."""
        resp = self._http.get("/api/v1/a2a/agents")
        resp.raise_for_status()
        return resp.json().get("agents", [])


class _MCPResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def tools(self) -> list[dict[str, Any]]:
        """List MCP tools."""
        resp = self._http.get("/api/v1/mcp/tools")
        resp.raise_for_status()
        return resp.json().get("tools", [])

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an MCP tool."""
        resp = self._http.post("/api/v1/mcp/call", json={
            "name": tool_name,
            "arguments": arguments or {},
        })
        resp.raise_for_status()
        return resp.json()
