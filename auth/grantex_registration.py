"""Auto-register agents on Grantex at creation time.

When an agent is created in AgenticOrg, this module:
1. Registers the agent on Grantex (gets a DID)
2. Maps authorized_tools to Grantex scopes
3. Stores the Grantex agent ID and DID in the agent record
4. Sets up delegation if the agent has a parent in the org hierarchy
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()


def _get_grantex_client():
    """Lazy import to avoid init errors when Grantex isn't configured."""
    try:
        from grantex import Grantex

        api_key = os.getenv("GRANTEX_API_KEY", "")
        base_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev")
        if not api_key:
            return None
        return Grantex(api_key=api_key, base_url=base_url)
    except Exception:
        logger.warning("grantex_client_init_failed")
        return None


def register_agent(
    name: str,
    agent_type: str,
    domain: str,
    authorized_tools: list[str],
) -> dict[str, Any] | None:
    """Register an agent on Grantex synchronously.

    Returns dict with grantex_agent_id and grantex_did, or None if
    Grantex is not configured.
    """
    client = _get_grantex_client()
    if not client:
        logger.info("grantex_registration_skipped", reason="no API key configured")
        return None

    scopes = _tools_to_scopes(authorized_tools, domain)

    try:
        agent = client.agents.register(
            name=f"{name} ({agent_type})",
            scopes=scopes,
            description=f"AgenticOrg {domain} agent: {agent_type}",
        )
        result = {
            "grantex_agent_id": agent.id,
            "grantex_did": getattr(agent, "did", ""),
            "grantex_scopes": scopes,
        }
        logger.info(
            "agent_registered_on_grantex",
            grantex_agent_id=agent.id,
            did=result["grantex_did"],
            scopes_count=len(scopes),
        )
        return result
    except Exception:
        logger.exception("grantex_registration_failed", agent_type=agent_type)
        return None


def setup_delegation(
    parent_grant_token: str,
    child_grantex_agent_id: str,
    child_scopes: list[str],
    expires_in: str = "8h",
) -> dict[str, Any] | None:
    """Delegate a subset of parent's scopes to a child agent.

    Used when creating an agent in an org hierarchy:
    CFO Agent → VP AP Agent → AP Processor Agent
    """
    client = _get_grantex_client()
    if not client:
        return None

    try:
        result = client.grants.delegate(
            parent_grant_token=parent_grant_token,
            sub_agent_id=child_grantex_agent_id,
            scopes=child_scopes,
            expires_in=expires_in,
        )
        logger.info(
            "delegation_created",
            child_agent=child_grantex_agent_id,
            scopes_count=len(child_scopes),
        )
        return result
    except Exception:
        logger.exception("grantex_delegation_failed")
        return None


def _tools_to_scopes(
    tools: list[str],
    domain: str,
    connector_names: list[str] | None = None,
) -> list[str]:
    """Map tool names to Grantex scopes.

    Format: tool:{connector}:execute:{tool_name}
    Also adds domain-level read scope.

    BUG-07 (Uday CA Firms 2026-05-02): tool names like ``list_invoices``
    and ``get_balance_sheet`` are registered by multiple connectors
    (QuickBooks, Zoho Books, Xero, ...). The unscoped ``_build_tool_index``
    returned the alphabetically-first connector that exposed each tool,
    so a Zoho-only agent ended up with ``tool:quickbooks:*`` scopes.
    When ``connector_names`` is supplied, scope resolution prefers a
    match registered by one of those connectors and falls back to the
    global index only if no scoped match exists. Agents that don't
    declare any connector_ids continue to use the unscoped index — for
    them we have no signal that *should* have constrained the choice.
    """
    scopes = [f"agenticorg:{domain}:read"]

    try:
        from core.langgraph.tool_adapter import _build_tool_index

        scoped_index = (
            _build_tool_index(connector_names=connector_names)
            if connector_names
            else {}
        )
        global_index = _build_tool_index()
    except Exception:
        # Fallback: use tool names directly as scopes
        return scopes + [f"tool:agenticorg:execute:{t}" for t in tools]

    for tool_name in tools:
        match = scoped_index.get(tool_name) or global_index.get(tool_name)
        if match:
            connector_name = match[0]
            scopes.append(f"tool:{connector_name}:execute:{tool_name}")
        else:
            scopes.append(f"tool:agenticorg:execute:{tool_name}")

    return scopes
