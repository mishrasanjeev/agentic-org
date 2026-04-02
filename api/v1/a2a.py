"""A2A (Agent-to-Agent) Protocol endpoints.

Implements the A2A spec for agent discovery and task execution:
- GET  /a2a/.well-known/agent.json  — Agent Card (discovery)
- POST /a2a/tasks                    — Execute task via JSON-RPC
- GET  /a2a/tasks/{id}               — Get task status

External agents (ChatGPT, Claude, partner systems) discover our agents
via the Agent Card, then send tasks via JSON-RPC with a Grantex grant
token for authorization.
"""

from __future__ import annotations

import os
import uuid as _uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_tenant

router = APIRouter(prefix="/a2a", tags=["A2A"])
_log = structlog.get_logger()

# In-memory task store (production would use Redis/DB)
_task_store: dict[str, dict[str, Any]] = {}


# ── Agent Card — Discovery endpoint ────────────────────────────────────────


@router.get("/.well-known/agent.json")
@router.get("/agent-card")
async def agent_card():
    """Return the A2A Agent Card for AgenticOrg.

    This is the discovery endpoint — external systems fetch this to learn
    what agents are available and how to authenticate.
    """
    grantex_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev")
    base_url = os.getenv("AGENTICORG_BASE_URL", "https://app.agenticorg.ai")

    return {
        "name": "AgenticOrg Agent Platform",
        "description": "Enterprise AI agents for Finance, HR, Marketing, and Operations. "
        "35 agents, 54 connectors, 340+ tools. HITL governance on every critical decision.",
        "url": f"{base_url}/api/v1/a2a",
        "version": "3.0.0",
        "protocol": "a2a/1.0",
        "capabilities": {
            "tasks": True,
            "streaming": False,
            "pushNotifications": False,
        },
        "authentication": {
            "scheme": "grantex",
            "jwksUri": f"{grantex_url}/.well-known/jwks.json",
            "issuer": grantex_url,
            "requiredScopes": ["agenticorg:agents:execute"],
            "delegationAllowed": True,
        },
        "skills": _build_agent_skills(),
    }


@router.get("/agents")
async def list_available_agents():
    """List all agents available for A2A task execution."""
    return {"agents": _build_agent_skills()}


# ── Task execution — JSON-RPC style ────────────────────────────────────────


class A2ATaskRequest(BaseModel):
    agent_type: str
    action: str = "process"
    inputs: dict[str, Any] = {}
    context: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


@router.post("/tasks")
async def create_task(
    body: A2ATaskRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Create and execute an A2A task.

    The caller must provide a valid Grantex grant token with the required
    scopes for the requested agent type.
    """
    task_id = f"a2a_{_uuid.uuid4().hex[:16]}"

    # Check if the requested agent type exists
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    if body.agent_type not in _AGENT_TYPE_DEFAULT_TOOLS:
        raise HTTPException(400, f"Unknown agent type: {body.agent_type}")

    # Store task as pending
    _task_store[task_id] = {
        "id": task_id,
        "status": "running",
        "agent_type": body.agent_type,
        "result": None,
    }

    # Execute via LangGraph
    try:
        from core.langgraph.runner import run_agent as langgraph_run

        # Load prompt
        system_prompt = ""
        try:
            import importlib

            mod = importlib.import_module(f"core.langgraph.agents.{body.agent_type}")
            load_fn = getattr(mod, "load_prompt", None) or getattr(mod, "load_ap_processor_prompt", None)
            if load_fn:
                system_prompt = load_fn()
        except (ImportError, AttributeError):
            system_prompt = f"You are a {body.agent_type} agent. Process the task and return JSON."

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get(body.agent_type, [])
        grant_token = getattr(request.state, "grant_token", "")

        result = await langgraph_run(
            agent_id=task_id,
            agent_type=body.agent_type,
            domain=_get_domain_for_type(body.agent_type),
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            authorized_tools=tools,
            task_input={
                "action": body.action,
                "inputs": body.inputs,
                "context": body.context,
            },
            grant_token=grant_token,
        )

        _task_store[task_id] = {
            "id": task_id,
            "status": result.get("status", "completed"),
            "agent_type": body.agent_type,
            "result": {
                "output": result.get("output", {}),
                "confidence": result.get("confidence", 0.0),
                "reasoning_trace": result.get("reasoning_trace", []),
            },
        }

        _log.info("a2a_task_completed", task_id=task_id, agent_type=body.agent_type)

    except Exception as exc:
        _log.error("a2a_task_failed", task_id=task_id, error=str(exc))
        _task_store[task_id] = {
            "id": task_id,
            "status": "failed",
            "agent_type": body.agent_type,
            "result": {"error": "Task execution failed"},
        }

    return _task_store[task_id]


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task status and result."""
    task = _task_store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


# ── Helpers ─────────────────────────────────────────────────────────────────

_DOMAIN_MAP = {
    "ap_processor": "finance", "ar_collections": "finance", "recon_agent": "finance",
    "tax_compliance": "finance", "close_agent": "finance", "fpa_agent": "finance",
    "treasury": "finance", "expense_manager": "finance", "rev_rec": "finance", "fixed_assets": "finance",
    "talent_acquisition": "hr", "onboarding_agent": "hr", "payroll_engine": "hr",
    "performance_coach": "hr", "ld_coordinator": "hr", "offboarding_agent": "hr",
    "content_factory": "marketing", "campaign_pilot": "marketing",
    "seo_strategist": "marketing", "crm_intelligence": "marketing", "brand_monitor": "marketing",
    "email_marketing": "marketing", "social_media": "marketing", "abm": "marketing", "competitive_intel": "marketing",
    "support_triage": "ops", "vendor_manager": "ops", "contract_intelligence": "ops",
    "compliance_guard": "ops", "it_operations": "ops",
    "legal_ops": "backoffice", "risk_sentinel": "backoffice", "facilities_agent": "backoffice",
    "email_agent": "comms", "notification_agent": "comms", "chat_agent": "comms",
}


def _get_domain_for_type(agent_type: str) -> str:
    return _DOMAIN_MAP.get(agent_type, "ops")


def _build_agent_skills() -> list[dict[str, Any]]:
    """Build the skills list for the Agent Card."""
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    skills = []
    for agent_type, tools in _AGENT_TYPE_DEFAULT_TOOLS.items():
        domain = _get_domain_for_type(agent_type)
        skills.append({
            "id": agent_type,
            "name": agent_type.replace("_", " ").title(),
            "description": f"{domain.title()} agent with {len(tools)} tools",
            "domain": domain,
            "tools": tools,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "default": "process"},
                    "inputs": {"type": "object"},
                    "context": {"type": "object"},
                },
                "required": ["inputs"],
            },
        })
    return skills
