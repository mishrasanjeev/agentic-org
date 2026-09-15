"""Auto-register agents on Grantex at creation time.

When an agent is created in AgenticOrg, this module:
1. Registers the agent on Grantex (gets a DID)
2. Maps authorized_tools to Grantex scopes
3. Stores the Grantex agent ID and DID in the agent record
4. Sets up delegation if the agent has a parent in the org hierarchy
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Final

import structlog

logger = structlog.get_logger()


# Most scopes one Grantex agent registration carries.
MAX_AGENT_SCOPES: Final = 100


class ScopeLimitExceededError(ValueError):
    """An agent's tools map to more distinct Grantex scopes than a registration can carry."""

    def __init__(self, count: int) -> None:
        super().__init__(
            f"the agent's authorized tools map to {count} distinct Grantex scopes; "
            f"at most {MAX_AGENT_SCOPES} can be registered - remove tools or split the agent"
        )
        self.count = count


def bounded_scopes(scopes: Iterable[str]) -> list[str]:
    """``scopes`` without duplicates (first occurrence kept), at most ``MAX_AGENT_SCOPES``.

    Raises ``ScopeLimitExceededError`` rather than silently dropping scopes.
    """
    unique = list(dict.fromkeys(s for s in scopes if isinstance(s, str) and s))
    if len(unique) > MAX_AGENT_SCOPES:
        raise ScopeLimitExceededError(len(unique))
    return unique


def _get_grantex_client():
    """Lazy import to avoid init errors when Grantex isn't configured."""
    try:
        from grantex import Grantex

        from core.config import grantex_base_url_for_env

        api_key = os.getenv("GRANTEX_API_KEY", "")
        base_url = grantex_base_url_for_env()
        if not api_key:
            return None
        return Grantex(api_key=api_key, base_url=base_url)
    # enterprise-gate: broad-except-ok reason=optional-grantex-sdk-init-falls-back-to-unconfigured
    except Exception:
        logger.warning("grantex_client_init_failed")
        return None


def register_agent(
    name: str,
    agent_type: str,
    domain: str,
    authorized_tools: list[str],
    connector_names: list[str] | None = None,
) -> dict[str, Any] | None:
    """Register an agent on Grantex synchronously.

    Returns dict with grantex_agent_id and grantex_did, or None if
    Grantex is not configured.
    """
    client = _get_grantex_client()
    if not client:
        logger.info("grantex_registration_skipped", reason="no API key configured")
        return None

    scopes = _tools_to_scopes(authorized_tools, domain, connector_names=connector_names)

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
    # enterprise-gate: broad-except-ok reason=optional-grantex-registration-failure-does-not-create-fake-success
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
    # enterprise-gate: broad-except-ok reason=optional-grantex-delegation-failure-does-not-create-fake-success
    except Exception:
        logger.exception("grantex_delegation_failed")
        return None


def _tools_to_scopes(
    tools: list[str],
    domain: str,
    connector_names: list[str] | None = None,
) -> list[str]:
    """Map tool names to Grantex scopes.

    Format: ``tool:{connector}:{permission}:{tool_name}``, plus a domain-level
    read scope. ``permission`` is what ``grantex.enforce`` understands
    (``read < write < delete < admin``): the level the connector's shipped
    Grantex manifest declares for the tool, else a conservative name
    heuristic (``core.langgraph.grantex_auth._tool_permission``). An
    ``execute`` segment - used before - resolves to no permission, so a grant
    carrying only such scopes denied every call.

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
    from core.langgraph.grantex_auth import _tool_permission

    def _scope(connector_name: str, tool: str) -> str:
        return f"tool:{connector_name}:{_tool_permission(connector_name, tool)}:{tool}"

    scopes = [f"agenticorg:{domain}:read"]

    try:
        from core.langgraph.tool_adapter import _build_tool_index, _split_connector_tool_ref

        scoped_index = (
            _build_tool_index(connector_names=connector_names)
            if connector_names
            else {}
        )
        global_index = _build_tool_index()
        # Connector-qualified refs (``jira:create_issue``) resolve against
        # the alias index so they scope to the connector they name.
        qualified_index = _build_tool_index(include_connector_aliases=True)
    # enterprise-gate: broad-except-ok reason=tool-index-failure-falls-back-to-agenticorg-scopes
    except Exception:
        # Fallback: use tool names directly as scopes
        return scopes + [_scope("agenticorg", t) for t in tools]

    for tool_name in tools:
        connector_hint, bare_tool = _split_connector_tool_ref(tool_name)
        if connector_hint:
            # Scope to the named connector when it registers the tool, never
            # to a first-wins match of the bare name.
            qualified = qualified_index.get(f"{connector_hint}:{bare_tool}")
            if qualified:
                scopes.append(_scope(qualified[0], bare_tool))
            else:
                scopes.append(_scope("agenticorg", tool_name))
            continue
        match = scoped_index.get(tool_name) or global_index.get(tool_name)
        if match:
            scopes.append(_scope(match[0], tool_name))
        else:
            scopes.append(_scope("agenticorg", tool_name))

    return scopes
