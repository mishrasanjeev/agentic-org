"""Grantex integration for LangGraph agents.

Handles:
  - Agent registration on Grantex (gets DID)
  - Grant token verification and scope checking
  - Delegation chain for org hierarchy

Budget debit and Grantex audit-trail helpers were removed: nothing in the
runtime called them, and tool-call auditing lives in
``core.tool_gateway.audit_logger`` / the action-policy decision log.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

import structlog
from grantex import Grantex, ToolManifest
from grantex._errors import GrantexApiError
from grantex._types import Agent as GrantexAgent

from core.config import grantex_base_url_for_env

logger = structlog.get_logger()

# Singleton client — initialized lazily
_grantex_client: Grantex | None = None


def get_grantex_client() -> Grantex:
    """Lazy singleton Grantex client with all manifests pre-loaded."""
    global _grantex_client
    if _grantex_client is None:
        api_key = os.getenv("GRANTEX_API_KEY", "")
        base_url = grantex_base_url_for_env()
        if not api_key:
            raise ValueError(
                "GRANTEX_API_KEY is required. Set it in environment or .env file."
            )
        _grantex_client = Grantex(api_key=api_key, base_url=base_url)

        # Load all pre-built manifests
        _load_all_manifests(_grantex_client)

    return _grantex_client


def _load_all_manifests(client: Grantex) -> None:
    """Load all 53 pre-built Grantex manifests + any custom manifests from disk."""

    # All 53 pre-built manifest module paths (shipped with grantex>=0.3.3)
    manifest_modules = [
        "grantex.manifests.ahrefs",
        "grantex.manifests.banking_aa",
        "grantex.manifests.bombora",
        "grantex.manifests.brandwatch",
        "grantex.manifests.buffer",
        "grantex.manifests.confluence",
        "grantex.manifests.darwinbox",
        "grantex.manifests.docusign",
        "grantex.manifests.epfo",
        "grantex.manifests.g2",
        "grantex.manifests.ga4",
        "grantex.manifests.github",
        "grantex.manifests.gmail",
        "grantex.manifests.google_ads",
        "grantex.manifests.google_calendar",
        "grantex.manifests.greenhouse",
        "grantex.manifests.gstn",
        "grantex.manifests.hubspot",
        "grantex.manifests.income_tax_india",
        "grantex.manifests.jira",
        "grantex.manifests.keka",
        "grantex.manifests.langsmith",
        "grantex.manifests.linkedin_ads",
        "grantex.manifests.linkedin_talent",
        "grantex.manifests.mailchimp",
        "grantex.manifests.mca_portal",
        "grantex.manifests.meta_ads",
        "grantex.manifests.mixpanel",
        "grantex.manifests.moengage",
        "grantex.manifests.netsuite",
        "grantex.manifests.okta",
        "grantex.manifests.oracle_fusion",
        "grantex.manifests.pagerduty",
        "grantex.manifests.pinelabs_plural",
        "grantex.manifests.quickbooks",
        "grantex.manifests.s3",
        "grantex.manifests.salesforce",
        "grantex.manifests.sanctions_api",
        "grantex.manifests.sap",
        "grantex.manifests.sendgrid",
        "grantex.manifests.servicenow",
        "grantex.manifests.slack",
        "grantex.manifests.stripe",
        "grantex.manifests.tally",
        "grantex.manifests.trustradius",
        "grantex.manifests.twilio",
        "grantex.manifests.twitter",
        "grantex.manifests.whatsapp",
        "grantex.manifests.wordpress",
        "grantex.manifests.youtube",
        "grantex.manifests.zendesk",
        "grantex.manifests.zoho_books",
        "grantex.manifests.zoom",
    ]

    manifests: list[ToolManifest] = []
    for mod_path in manifest_modules:
        try:
            mod = importlib.import_module(mod_path)
            manifests.append(mod.manifest)
        except ImportError:
            logger.debug("manifest_not_found", module=mod_path)

    if manifests:
        client.load_manifests(manifests)
        logger.info("grantex_manifests_loaded", count=len(manifests))

    # Also load any custom manifests from a directory
    manifests_dir = os.environ.get("GRANTEX_MANIFESTS_DIR", "./manifests")
    if os.path.isdir(manifests_dir):
        client.load_manifests_from_dir(manifests_dir)
        logger.info("grantex_custom_manifests_loaded", dir=manifests_dir)


async def register_agent_on_grantex(
    name: str,
    agent_type: str,
    domain: str,
    authorized_tools: list[str],
) -> GrantexAgent:
    """Register an agent on Grantex and return the agent with its DID.

    Maps authorized_tools to Grantex scopes (e.g., "fetch_bank_statement"
    becomes "tool:banking_aa:execute:fetch_bank_statement").
    """
    client = get_grantex_client()

    # Map tool names to Grantex scopes
    scopes = _tools_to_scopes(authorized_tools)
    # Add domain-level read scope
    scopes.append(f"agenticorg:{domain}:read")

    agent = client.agents.register(
        name=f"{name} ({agent_type})",
        scopes=scopes,
        description=f"AgenticOrg {domain} agent: {agent_type}",
    )

    logger.info(
        "agent_registered_on_grantex",
        agent_id=agent.id,
        did=getattr(agent, "did", None),
        scopes_count=len(scopes),
    )
    return agent


def verify_grant_scopes(
    grant_token: str,
    required_scopes: list[str],
) -> dict[str, Any]:
    """Verify a Grantex grant token and check required scopes.

    Returns the verified grant payload if valid.
    Raises GrantexError if token is invalid or missing scopes.
    """
    client = get_grantex_client()
    result = client.tokens.verify(grant_token)

    # Check required scopes against granted scopes
    granted = set(getattr(result, "scopes", []))
    missing = [s for s in required_scopes if s not in granted]
    if missing:
        raise GrantexApiError(f"Missing required scopes: {missing}")  # type: ignore[call-arg]

    return {
        "grant_id": getattr(result, "grant_id", ""),
        "agent_did": getattr(result, "agent_did", ""),
        "principal_id": getattr(result, "principal_id", ""),
        "scopes": list(granted),
        "expires_at": getattr(result, "expires_at", ""),
    }


async def delegate_to_child_agent(
    parent_grant_token: str,
    child_agent_id: str,
    child_scopes: list[str],
    expires_in: str = "8h",
) -> dict[str, Any]:
    """Delegate a subset of parent's scopes to a child agent.

    Used for org hierarchy: CFO Agent delegates to VP AP Agent
    with a subset of finance scopes.
    """
    client = get_grantex_client()
    result = client.grants.delegate(
        parent_grant_token=parent_grant_token,
        sub_agent_id=child_agent_id,
        scopes=child_scopes,
        expires_in=expires_in,
    )
    logger.info(
        "grant_delegated",
        child_agent=child_agent_id,
        scopes_count=len(child_scopes),
    )
    return result


_WRITE_TOOL_HINTS = (
    "create",
    "post",
    "update",
    "delete",
    "send",
    "file",
    "initiate",
    "queue",
    "pay",
    "publish",
    "schedule",
    "upsert",
    "cancel",
)


def _manifest_permission(connector_name: str, tool_name: str) -> str | None:
    """Return the manifest-declared permission for ``connector.tool`` if shipped."""
    try:
        mod = importlib.import_module(f"grantex.manifests.{connector_name}")
    except ImportError:
        return None
    manifest = getattr(mod, "manifest", None)
    get_permission = getattr(manifest, "get_permission", None)
    if get_permission is None:
        return None
    permission = get_permission(tool_name)
    return str(permission) if permission else None


def _tool_permission(connector_name: str, tool_name: str) -> str:
    """Permission level the SDK understands (``read``/``write``/``delete``/``admin``)."""
    declared = _manifest_permission(connector_name, tool_name)
    if declared:
        return declared
    lowered = tool_name.lower()
    return "write" if any(hint in lowered for hint in _WRITE_TOOL_HINTS) else "read"


def _tools_to_scopes(tools: list[str]) -> list[str]:
    """Map tool names to Grantex scope format.

    ``grantex.enforce`` resolves the granted level from the third scope
    segment and only understands ``read < write < delete < admin``; an
    ``execute`` segment resolves to no permission and every call is denied.
    The permission comes from the shipped manifest when available, else from
    a conservative name heuristic (unknown → ``read``).

    "fetch_bank_statement" -> "tool:banking_aa:read:fetch_bank_statement"
    "create_contact"       -> "tool:hubspot:write:create_contact"
    """
    from core.langgraph.tool_adapter import _actual_tool_name, _build_tool_index

    index = _build_tool_index(include_connector_aliases=True)
    scopes: list[str] = []
    for tool_name in tools:
        match = index.get(tool_name)
        if match:
            connector_name = match[0]
            actual = _actual_tool_name(tool_name)
            scopes.append(f"tool:{connector_name}:{_tool_permission(connector_name, actual)}:{actual}")
    return scopes
