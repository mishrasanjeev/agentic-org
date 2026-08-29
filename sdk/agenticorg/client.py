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
                "Provide api_key or grantex_token, or set AGENTICORG_API_KEY / AGENTICORG_GRANTEX_TOKEN env var."
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
        self.voice = _VoiceResource(self._http)
        self.rpa = _RPAResource(self._http)
        self.bridges = _BridgesResource(self._http)
        self.commerce = _CommerceResource(self._http)

    def _build_headers(self) -> dict[str, str]:
        # Let httpx select the content type for each request. A client-level
        # application/json header is also inherited by multipart uploads and
        # prevents httpx from adding the required multipart boundary.
        headers: dict[str, str] = {}
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
            resp = self._http.post(
                "/api/v1/a2a/tasks",
                json={
                    "agent_type": agent_id_or_type,
                    **payload,
                },
            )

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

    def health(self, connector_id: str) -> dict[str, Any]:
        """Read the connector's tenant-scoped health state."""
        resp = self._http.get(f"/api/v1/connectors/{connector_id}/health")
        resp.raise_for_status()
        return resp.json()

    def test(self, connector_id: str) -> dict[str, Any]:
        """Run the backend's explicit connector connection test."""
        resp = self._http.post(f"/api/v1/connectors/{connector_id}/test")
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
        resp = self._http.post(
            "/api/v1/sop/parse-text",
            json={
                "text": text,
                "domain_hint": domain_hint,
                "llm_model": llm_model,
            },
        )
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
        resp = self._http.post(
            "/api/v1/mcp/call",
            json={
                "name": tool_name,
                "arguments": arguments or {},
            },
        )
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

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Request cancellation of a workflow run."""
        resp = self._http.post(f"/api/v1/workflows/runs/{run_id}/cancel")
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

    def supported_types(self) -> dict[str, Any]:
        """Return accepted document, image, audio, video, and OCR formats."""
        resp = self._http.get("/api/v1/knowledge/supported-types")
        resp.raise_for_status()
        return resp.json()

    def upload(self, file_path: str, *, duplicate_policy: str = "reject") -> dict[str, Any]:
        """Upload a knowledge document using a real multipart request."""
        if duplicate_policy not in {"reject", "replace", "allow_duplicate"}:
            raise ValueError("duplicate_policy must be reject, replace, or allow_duplicate")
        params = {
            "replace": str(duplicate_policy == "replace").lower(),
            "allow_duplicate": str(duplicate_policy == "allow_duplicate").lower(),
        }
        with open(file_path, "rb") as file_handle:
            resp = self._http.post(
                "/api/v1/knowledge/upload",
                params=params,
                files={"file": file_handle},
            )
        resp.raise_for_status()
        return resp.json()

    def documents(self, *, page: int = 1, per_page: int = 20) -> dict[str, Any]:
        """List tenant knowledge documents and extraction state."""
        resp = self._http.get(
            "/api/v1/knowledge/documents",
            params={"page": page, "per_page": per_page},
        )
        resp.raise_for_status()
        return resp.json()

    def delete(self, document_id: str) -> dict[str, Any]:
        """Delete one tenant knowledge document."""
        resp = self._http.delete(f"/api/v1/knowledge/documents/{document_id}")
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict[str, Any]:
        """Read the public retrieval/OCR backend health summary."""
        resp = self._http.get("/api/v1/knowledge/health")
        resp.raise_for_status()
        return resp.json()

    def stats(self) -> dict[str, Any]:
        """Read tenant knowledge indexing statistics."""
        resp = self._http.get("/api/v1/knowledge/stats")
        resp.raise_for_status()
        return resp.json()


class _VoiceResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def status(self, *, agent_id: str | None = None) -> dict[str, Any]:
        params = {"agent_id": agent_id} if agent_id else None
        resp = self._http.get("/api/v1/voice/status", params=params)
        resp.raise_for_status()
        return resp.json()

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Store an admin-reviewed voice configuration; secrets are masked on return."""
        resp = self._http.post("/api/v1/voice/config", json=config)
        resp.raise_for_status()
        return resp.json()

    def test_connection(self, provider: str, credentials: dict[str, Any]) -> dict[str, Any]:
        """Explicitly probe provider credentials without persisting them."""
        resp = self._http.post(
            "/api/v1/voice/test-connection",
            json={"provider": provider, "credentials": credentials},
        )
        resp.raise_for_status()
        return resp.json()

    def runtime_health(self, agent_id: str) -> dict[str, Any]:
        resp = self._http.get("/api/v1/voice/runtime/health", params={"agent_id": agent_id})
        resp.raise_for_status()
        return resp.json()

    def calls(self, agent_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        resp = self._http.get(
            "/api/v1/voice/calls",
            params={"agent_id": agent_id, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json()

    def place_outbound_call(self, *, agent_id: str, to_number: str) -> dict[str, Any]:
        """Place a real outbound call. This can incur provider charges."""
        resp = self._http.post(
            "/api/v1/voice/calls/outbound",
            json={"agent_id": agent_id, "to_number": to_number},
        )
        resp.raise_for_status()
        return resp.json()


class _RPAResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def scripts(self) -> list[dict[str, Any]]:
        resp = self._http.get("/api/v1/rpa/scripts")
        resp.raise_for_status()
        return resp.json()

    def history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        resp = self._http.get("/api/v1/rpa/history", params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    def run(self, script_id: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an RPA script. The selected script may perform external actions."""
        resp = self._http.post(
            f"/api/v1/rpa/scripts/{script_id}/run",
            json={"params": params or {}},
        )
        resp.raise_for_status()
        return resp.json()


class _BridgesResource:
    def __init__(self, http: httpx.Client):
        self._http = http

    def list(self) -> list[dict[str, Any]]:
        resp = self._http.get("/api/v1/bridge/list")
        resp.raise_for_status()
        return resp.json()

    def register(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post("/api/v1/bridge/register", json=data)
        resp.raise_for_status()
        return resp.json()

    def status(self, bridge_id: str) -> dict[str, Any]:
        resp = self._http.get(f"/api/v1/bridge/{bridge_id}/status")
        resp.raise_for_status()
        return resp.json()

    def deregister(self, bridge_id: str) -> dict[str, Any]:
        resp = self._http.delete(f"/api/v1/bridge/{bridge_id}")
        resp.raise_for_status()
        return resp.json()

    def route(self, connector_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Route an explicit request to a registered local bridge."""
        resp = self._http.post(f"/api/v1/bridge/route/{connector_type}", json=payload)
        resp.raise_for_status()
        return resp.json()


class _CommerceResource:
    _PREFIX = "/api/v1/commerce/runtime"

    def __init__(self, http: httpx.Client):
        self._http = http

    def create_onboarding_packet(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/seller-agents/onboarding-packets", json=data)
        resp.raise_for_status()
        return resp.json()

    def onboarding_packet(self, packet_id: str) -> dict[str, Any]:
        resp = self._http.get(f"{self._PREFIX}/seller-agents/onboarding-packets/{packet_id}")
        resp.raise_for_status()
        return resp.json()

    def configure_shopify(self, data: dict[str, Any]) -> dict[str, Any]:
        """Store Shopify credentials through the server's encrypted connector vault."""
        resp = self._http.post(f"{self._PREFIX}/seller-agents/connectors/shopify/credentials", json=data)
        resp.raise_for_status()
        return resp.json()

    def shopify_status(self, merchant_id: str) -> dict[str, Any]:
        resp = self._http.get(
            f"{self._PREFIX}/seller-agents/connectors/shopify/status",
            params={"merchant_id": merchant_id},
        )
        resp.raise_for_status()
        return resp.json()

    def sync_shopify(self, data: dict[str, Any]) -> dict[str, Any]:
        """Initiate a real read-only Shopify catalog sync."""
        resp = self._http.post(f"{self._PREFIX}/seller-agents/shopify/sync", json=data)
        resp.raise_for_status()
        return resp.json()

    def request_grantex_authority(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/authority/grantex/request", json=data)
        resp.raise_for_status()
        return resp.json()

    def cache_artifacts(self, artifacts: list[dict[str, Any]], *, buyer_agent_id: str | None = None) -> dict[str, Any]:
        resp = self._http.post(
            f"{self._PREFIX}/artifacts/cache",
            json={"artifacts": artifacts, "buyer_agent_id": buyer_agent_id},
        )
        resp.raise_for_status()
        return resp.json()

    def ask(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/buyer-sessions/ask", json=data)
        resp.raise_for_status()
        return resp.json()

    def products(
        self, *, merchant_id: str, seller_agent_id: str | None = None, query: str | None = None
    ) -> dict[str, Any]:
        params = {"merchant_id": merchant_id}
        if seller_agent_id:
            params["seller_agent_id"] = seller_agent_id
        if query:
            params["q"] = query
        resp = self._http.get(f"{self._PREFIX}/products", params=params)
        resp.raise_for_status()
        return resp.json()

    def protocol_adapters(
        self,
        *,
        merchant_id: str,
        seller_agent_id: str,
        buyer_agent_id: str | None = None,
    ) -> dict[str, Any]:
        params = {"merchant_id": merchant_id, "seller_agent_id": seller_agent_id}
        if buyer_agent_id:
            params["buyer_agent_id"] = buyer_agent_id
        resp = self._http.get(f"{self._PREFIX}/protocol-adapters", params=params)
        resp.raise_for_status()
        return resp.json()

    def protocol_adapter(
        self,
        surface: str,
        *,
        merchant_id: str,
        seller_agent_id: str,
        buyer_agent_id: str | None = None,
    ) -> dict[str, Any]:
        params = {"merchant_id": merchant_id, "seller_agent_id": seller_agent_id}
        if buyer_agent_id:
            params["buyer_agent_id"] = buyer_agent_id
        resp = self._http.get(f"{self._PREFIX}/protocol-adapters/{surface}", params=params)
        resp.raise_for_status()
        return resp.json()

    def bridge_surfaces(self) -> dict[str, Any]:
        resp = self._http.get(f"{self._PREFIX}/bridges/surfaces")
        resp.raise_for_status()
        return resp.json()

    def verify_mandate_capability(self, data: dict[str, Any]) -> dict[str, Any]:
        """Verify provider-owned mandate capability without executing payment."""
        resp = self._http.post(f"{self._PREFIX}/providers/plural-pine/mandate-capability/verify", json=data)
        resp.raise_for_status()
        return resp.json()

    def prepare_purchase(self, data: dict[str, Any]) -> dict[str, Any]:
        """Prepare a non-executing purchase handoff."""
        resp = self._http.post(f"{self._PREFIX}/purchase/prepare", json=data)
        resp.raise_for_status()
        return resp.json()

    def offline_pos_readiness(self) -> dict[str, Any]:
        resp = self._http.get(f"{self._PREFIX}/pos/offline/readiness")
        resp.raise_for_status()
        return resp.json()

    def create_offline_pos_handoff(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/pos/offline/handoffs", json=data)
        resp.raise_for_status()
        return resp.json()

    def confirm_offline_pos_handoff(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/pos/offline/confirmations", json=data)
        resp.raise_for_status()
        return resp.json()

    def simulate_offline_pos_confirmation(self, data: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(f"{self._PREFIX}/pos/offline/simulator/confirm", json=data)
        resp.raise_for_status()
        return resp.json()
