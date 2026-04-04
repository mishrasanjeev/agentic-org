"""Grantex integration for LangGraph agents.

Handles:
  - Agent registration on Grantex (gets DID)
  - Grant token verification and scope checking
  - Budget allocation and debit for payment operations
  - Delegation chain for org hierarchy
  - Audit trail logging
"""

from __future__ import annotations

import importlib
import os
from typing import Any

import structlog
from grantex import Grantex, ToolManifest
from grantex._errors import GrantexApiError, GrantexError
from grantex._types import Agent as GrantexAgent

logger = structlog.get_logger()

# Singleton client — initialized lazily
_grantex_client: Grantex | None = None


def get_grantex_client() -> Grantex:
    """Lazy singleton Grantex client with all manifests pre-loaded."""
    global _grantex_client
    if _grantex_client is None:
        api_key = os.getenv("GRANTEX_API_KEY", "")
        base_url = os.getenv("GRANTEX_BASE_URL", "https://api.grantex.dev")
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


async def debit_budget(
    grant_id: str,
    amount: float,
    currency: str = "INR",
    description: str = "",
) -> dict[str, Any]:
    """Debit from an agent's budget allocation.

    Used for payment operations — the agent's grant has a spending cap,
    and each payment debits from it.

    Returns debit result or raises if insufficient budget.
    """
    client = get_grantex_client()
    from grantex._types import DebitBudgetParams

    result = client.budgets.debit(DebitBudgetParams(  # type: ignore[call-arg]
        grant_id=grant_id,
        amount=amount,
        currency=currency,
        description=description or "Agent tool execution",
    ))
    return {
        "remaining_balance": getattr(result, "remaining_balance", None),
        "transaction_id": getattr(result, "transaction_id", ""),
    }


async def log_audit_entry(
    agent_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an action to the Grantex audit trail (hash-chained, append-only)."""
    try:
        client = get_grantex_client()
        from grantex._types import LogAuditParams

        client.audit.log(LogAuditParams(  # type: ignore[call-arg]
            action=action,
            resource_type=resource_type or "agent_execution",
            resource_id=resource_id or agent_id,
            outcome=outcome,
            metadata=metadata or {},
        ))
    except GrantexError:
        logger.warning("grantex_audit_log_failed", agent_id=agent_id, action=action)


def _tools_to_scopes(tools: list[str]) -> list[str]:
    """Map tool names to Grantex scope format.

    "fetch_bank_statement" -> "tool:banking_aa:execute:fetch_bank_statement"
    """
    from core.langgraph.tool_adapter import _build_tool_index

    index = _build_tool_index()
    scopes: list[str] = []
    for tool_name in tools:
        match = index.get(tool_name)
        if match:
            connector_name = match[0]
            scopes.append(f"tool:{connector_name}:execute:{tool_name}")
    return scopes
