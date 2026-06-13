"""AgenticOrg SDK client."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class AgentRunResult:
    """Canonical response shape for every agent-execution endpoint.

    Mirrors docs/api/agent-run-contract.md. Both /agents/{id}/run (canonical
    already after PR-A) and /a2a/tasks (legacy shape was {id, result:{…}})
    normalize into this single dataclass via :func:`_to_agent_run_result`.
    """

    run_id: str
    status: str  # completed | failed | hitl_triggered | budget_exceeded
    output: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reasoning_trace: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    runtime: str = ""
    agent_id: str | None = None
    agent_type: str | None = None
    correlation_id: str | None = None
    performance: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    hitl_trigger: str | None = None
    error: str | None = None
    # Raw response dict for power users / legacy fields.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _to_agent_run_result(payload: dict[str, Any]) -> AgentRunResult:
    """Normalize any agent-run response shape (canonical or legacy) into
    :class:`AgentRunResult`. Tolerates:

    - canonical shape (PR-A forward): top-level ``run_id``, ``output``,
      ``confidence``, etc.
    - legacy ``/agents/{id}/run`` shape (pre-PR-A): ``task_id`` instead
      of ``run_id``.
    - legacy ``/a2a/tasks`` shape (pre-PR-A): ``id`` + nested
      ``result: {output, confidence}``.
    """
    # Prefer canonical, fall back to legacy aliases.
    run_id = payload.get("run_id") or payload.get("task_id") or payload.get("id") or ""

    # Output + confidence: top-level first, then unwrap legacy `result` nest.
    if "output" in payload:
        output = payload.get("output") or {}
    else:
        nested = payload.get("result") or {}
        output = nested.get("output") if isinstance(nested, dict) else {} or {}
    if "confidence" in payload:
        confidence = float(payload.get("confidence") or 0.0)
    else:
        nested = payload.get("result") or {}
        confidence = float(nested.get("confidence") or 0.0) if isinstance(nested, dict) else 0.0

    return AgentRunResult(
        run_id=str(run_id),
        status=str(payload.get("status") or ""),
        output=output if isinstance(output, dict) else {},
        confidence=confidence,
        reasoning_trace=list(payload.get("reasoning_trace") or []),
        tool_calls=list(payload.get("tool_calls") or []),
        runtime=str(payload.get("runtime") or ""),
        agent_id=payload.get("agent_id"),
        agent_type=payload.get("agent_type"),
        correlation_id=payload.get("correlation_id"),
        performance=payload.get("performance") or None,
        explanation=payload.get("explanation") or None,
        hitl_trigger=payload.get("hitl_trigger") or None,
        error=payload.get("error") or None,
        raw=payload,
    )


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
        self.workflows = _WorkflowsResource(self._http)
        self.knowledge = _KnowledgeResource(self._http)

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
    ) -> AgentRunResult:
        """Run an agent and return the canonical :class:`AgentRunResult`.

        Args:
            agent_id_or_type: Agent UUID or agent type (e.g. ``"ap_processor"``).
                UUIDs use ``POST /agents/{id}/run``; agent types use
                ``POST /a2a/tasks``. Both shapes normalize into the same
                ``AgentRunResult``.
            action: Action to perform (default ``"process"``).
            inputs: Task input data.
            context: Additional context.

        Returns:
            Canonical :class:`AgentRunResult` — see
            ``docs/api/agent-run-contract.md``. Access ``result.output``,
            ``result.confidence``, ``result.status``, etc. Raw response
            dict available as ``result.raw`` for power users.
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
        return _to_agent_run_result(resp.json())

    def create(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new agent."""
        resp = self._http.post("/api/v1/agents", json=kwargs)
        resp.raise_for_status()
        return resp.json()

    def generate(
        self,
        description: str,
        *,
        deploy: bool = False,
        company_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate an agent config from a plain-English description.

        If ``deploy`` is true, the API creates the top suggestion as a shadow
        agent. This mirrors ``POST /api/v1/agents/generate``.
        """
        payload: dict[str, Any] = {"description": description, "deploy": deploy}
        if company_id:
            payload["company_id"] = company_id
        resp = self._http.post("/api/v1/agents/generate", json=payload)
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


class _WorkflowsResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def templates(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List workflow templates, optionally filtered by domain."""
        params = {"domain": domain} if domain else {}
        resp = self._http.get("/api/v1/workflows/templates", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def list(
        self,
        *,
        page: int = 1,
        per_page: int = 20,
        company_id: str | None = None,
    ) -> dict[str, Any]:
        """List deployed workflows."""
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if company_id:
            params["company_id"] = company_id
        resp = self._http.get("/api/v1/workflows", params=params)
        resp.raise_for_status()
        return resp.json()

    def generate(self, description: str, *, deploy: bool = False) -> dict[str, Any]:
        """Generate a workflow definition from a natural-language description."""
        resp = self._http.post(
            "/api/v1/workflows/generate",
            json={"description": description, "deploy": deploy},
        )
        resp.raise_for_status()
        return resp.json()

    def create(
        self,
        *,
        name: str,
        definition: dict[str, Any],
        version: str = "1.0",
        description: str | None = None,
        domain: str | None = None,
        trigger_type: str | None = None,
        trigger_config: dict[str, Any] | None = None,
        replan_on_failure: bool = False,
        company_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a workflow definition."""
        payload: dict[str, Any] = {
            "name": name,
            "version": version,
            "definition": definition,
            "replan_on_failure": replan_on_failure,
        }
        optional = {
            "description": description,
            "domain": domain,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "company_id": company_id,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        resp = self._http.post("/api/v1/workflows", json=payload)
        resp.raise_for_status()
        return resp.json()

    def get(self, workflow_id: str) -> dict[str, Any]:
        """Get a workflow definition."""
        resp = self._http.get(f"/api/v1/workflows/{workflow_id}")
        resp.raise_for_status()
        return resp.json()

    def run(
        self,
        workflow_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a workflow run."""
        resp = self._http.post(
            f"/api/v1/workflows/{workflow_id}/run",
            json={"payload": payload or {}},
        )
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Get workflow run status and step outputs."""
        resp = self._http.get(f"/api/v1/workflows/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()


class _KnowledgeResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Search the tenant knowledge base."""
        resp = self._http.post(
            "/api/v1/knowledge/search",
            json={"query": query, "top_k": top_k},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", data) if isinstance(data, dict) else data
