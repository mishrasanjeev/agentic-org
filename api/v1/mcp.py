"""MCP (Model Context Protocol) server endpoint.

Exposes AgenticOrg agents as MCP tools that ChatGPT, Claude, and other
AI interfaces can call. Each agent type becomes a callable tool with
typed input schema.

MCP flow:
1. Client fetches tool list via GET /mcp/tools
2. Client calls a tool via POST /mcp/call with tool name + arguments
3. Server executes the agent via LangGraph and returns the result

Authentication: Grantex grant token in Authorization header.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_tenant

router = APIRouter(prefix="/mcp", tags=["MCP"])
_log = structlog.get_logger()


# ── Tool List — Discovery ──────────────────────────────────────────────────

@router.get("/tools")
async def list_tools():
    """List all available MCP tools (one per agent type).

    Each tool has a name, description, and input schema that MCP clients
    (ChatGPT, Claude) use to understand what they can call.
    """
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    tools = []
    for agent_type, default_tools in _AGENT_TYPE_DEFAULT_TOOLS.items():
        domain = _get_domain(agent_type)
        tools.append({
            "name": f"agenticorg_{agent_type}",
            "description": (
                f"Run the {agent_type.replace('_', ' ').title()} agent "
                f"({domain} domain, {len(default_tools)} tools). "
                f"Processes tasks with AI reasoning, tool calls, and HITL governance."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action to perform (e.g., 'process', 'analyze', 'validate')",
                        "default": "process",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Task-specific input data (e.g., invoice details, employee data)",
                    },
                    "context": {
                        "type": "object",
                        "description": "Additional context (e.g., org settings, thresholds)",
                    },
                },
                "required": ["inputs"],
            },
        })
    return {"tools": tools}


# ── Tool Call — Execution ──────────────────────────────────────────────────

class MCPCallRequest(BaseModel):
    name: str  # e.g., "agenticorg_ap_processor"
    arguments: dict[str, Any] = {}


@router.post("/call")
async def call_tool(
    body: MCPCallRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Execute an MCP tool call.

    The tool name maps to an agent type (strip the "agenticorg_" prefix).
    Arguments are passed as the task input.
    """
    # Parse agent type from tool name
    if not body.name.startswith("agenticorg_"):
        raise HTTPException(400, f"Unknown tool: {body.name}. Tools must start with 'agenticorg_'")

    agent_type = body.name.removeprefix("agenticorg_")

    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    if agent_type not in _AGENT_TYPE_DEFAULT_TOOLS:
        raise HTTPException(400, f"Unknown agent type: {agent_type}")

    # Load agent prompt
    system_prompt = _load_agent_prompt(agent_type)
    tools = _AGENT_TYPE_DEFAULT_TOOLS[agent_type]
    grant_token = getattr(request.state, "grant_token", "")

    # Execute via LangGraph
    from core.langgraph.runner import run_agent as langgraph_run

    try:
        result = await langgraph_run(
            agent_id=f"mcp_{_uuid.uuid4().hex[:12]}",
            agent_type=agent_type,
            domain=_get_domain(agent_type),
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            authorized_tools=tools,
            task_input={
                "action": body.arguments.get("action", "process"),
                "inputs": body.arguments.get("inputs", body.arguments),
                "context": body.arguments.get("context", {}),
            },
            grant_token=grant_token,
        )
    except Exception as exc:
        _log.error("mcp_call_failed", tool=body.name, error=str(exc))
        return {
            "content": [{"type": "text", "text": "Agent execution failed. Check logs for details."}],
            "isError": True,
        }

    _log.info("mcp_call_completed", tool=body.name, status=result.get("status"))

    # Format as MCP response
    output = result.get("output", {})
    confidence = result.get("confidence", 0.0)
    status = result.get("status", "completed")

    if status == "failed":
        return {
            "content": [{"type": "text", "text": "Agent task failed. Review configuration and retry."}],
            "isError": True,
        }

    # Build human-readable text from structured output
    import json

    text_parts = [f"Agent: {agent_type.replace('_', ' ').title()}"]
    text_parts.append(f"Status: {status}")
    text_parts.append(f"Confidence: {confidence:.0%}")
    if output:
        text_parts.append(f"Result: {json.dumps(output, indent=2, default=str)}")

    hitl_trigger = result.get("hitl_trigger", "")
    if hitl_trigger:
        text_parts.append(f"\nHITL Triggered: {hitl_trigger}")
        text_parts.append("A human reviewer needs to approve this action.")

    return {
        "content": [{"type": "text", "text": "\n".join(text_parts)}],
        "isError": False,
    }


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


def _get_domain(agent_type: str) -> str:
    return _DOMAIN_MAP.get(agent_type, "ops")


def _load_agent_prompt(agent_type: str) -> str:
    try:
        import importlib

        mod = importlib.import_module(f"core.langgraph.agents.{agent_type}")
        load_fn = getattr(mod, "load_prompt", None) or getattr(mod, "load_ap_processor_prompt", None)
        if load_fn:
            return load_fn()
    except (ImportError, AttributeError):
        pass
    return f"You are a {agent_type.replace('_', ' ')} agent. Process the task and return JSON."
