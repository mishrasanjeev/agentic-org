"""Regression pins for CA Firms 2026-05-03 reopened bugs.

Source: C:\\Users\\mishr\\Downloads\\CA_FIRMS_TEST_REPORTUDay3May2026.md.
The report reopened earlier fixes because adjacent paths still drifted:
pack provisioning lacked connector bindings, direct chat bypassed the
agent prompt/connector allow-list, and the Scopes tab rendered fabricated
security state.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ca_pack_tools_are_registered_on_real_connectors() -> None:
    """BUG-11/17: CA pack tools must exist on the connector registry."""
    from connectors.finance.income_tax_india import IncomeTaxIndiaConnector
    from connectors.finance.zoho_books import ZohoBooksConnector

    zoho = ZohoBooksConnector(config={})
    income_tax = IncomeTaxIndiaConnector(config={})

    for tool in {
        "calculate_tds",
        "check_account_balance",
        "fetch_bank_statement",
        "generate_gst_report",
        "get_ledger_balance",
        "get_transaction_list",
        "get_trial_balance",
        "list_overdue_invoices",
        "reconcile_bank",
    }:
        assert tool in zoho._tool_registry

    for tool in {"calculate_tds", "file_form_26q", "file_26q_return"}:
        assert tool in income_tax._tool_registry


def test_zoho_books_health_check_requires_real_api_probe() -> None:
    """BUG-12: stale Zoho OAuth must fail health, not pass field checks."""
    src = (ROOT / "connectors" / "finance" / "zoho_books.py").read_text(
        encoding="utf-8"
    )
    health_block = src[src.find("async def health_check") : src.find("#", src.find("async def health_check"))]
    assert 'self._get("/organizations")' in health_block
    assert '"status": "healthy"' in health_block
    assert "organizations" in health_block


def test_ca_pack_has_connector_policy_for_every_authorized_tool() -> None:
    """BUG-11/13/17: pack sync must persist connector_ids and tool map."""
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import (
        _connector_ids_for_tools,
        _tool_connector_map,
    )

    for agent in CA_PACK["agents"]:
        tool_map = _tool_connector_map(agent["tools"])
        connector_ids = _connector_ids_for_tools(agent["tools"])
        normalized_tools = [tool.rsplit(":", 1)[-1] for tool in agent["tools"]]
        assert set(tool_map) == set(normalized_tools)
        assert connector_ids
        assert all(cid.startswith("registry-") for cid in connector_ids)

    legacy = _tool_connector_map(["income_tax:file_26q_return", "email:send"])
    assert legacy["file_26q_return"] == "income_tax_india"
    assert legacy["send"] == "sendgrid"


def test_pack_installer_repairs_existing_company_agents() -> None:
    """BUG-13: company sync cannot be create-only after pack upgrades."""
    src = (ROOT / "core" / "agents" / "packs" / "installer.py").read_text(
        encoding="utf-8"
    )
    assert "agent.connector_ids = connector_ids" in src
    assert 'cfg["tool_connectors"] = tool_connectors' in src
    assert 'cfg["required_connector_ids"] = connector_ids' in src


def test_company_agent_list_self_heals_after_pack_install() -> None:
    """BUG-13: company Agents tab should not stay empty after ca-firm install."""
    src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    block = src[src.find("async def list_agents") : src.find("@router.get(\"/agents/org-tree\"")]
    assert "sync_company_pack_assets_for_session" in block
    assert "is_pack_installed_for_session" in block
    assert "total == 0" in block


def test_gst_auto_file_is_gated_by_active_gstn_credentials() -> None:
    """BUG-14: auto-filing is config-blocked until GSTN creds exist."""
    api_src = (ROOT / "api" / "v1" / "companies.py").read_text(
        encoding="utf-8"
    )
    detail_src = (
        ROOT / "ui" / "src" / "pages" / "CompanyDetail.tsx"
    ).read_text(encoding="utf-8")
    onboard_src = (
        ROOT / "ui" / "src" / "pages" / "CompanyOnboard.tsx"
    ).read_text(encoding="utf-8")

    assert "_has_active_gstn_credential" in api_src
    assert "Add and verify a GSTN credential before enabling auto-file" in api_src
    assert "hasActiveGstnCredential" in detail_src
    assert "GST auto-file is locked" in detail_src
    assert "gst_auto_file: false" in onboard_src
    assert "can be enabled after this company is onboarded" in onboard_src


def test_direct_chat_uses_agent_prompt_and_connector_allowlist() -> None:
    """BUG-17: direct agent chat must not bypass the run-agent contract."""
    src = (ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
    assert "agent_system_prompt" in src
    assert "_resolve_connector_configs" in src
    assert "connector_names=connector_names" in src
    assert 'lg_result.get("tool_calls") or lg_result.get("tool_calls_log")' in src
    assert "for clarification." in src


def test_agent_scopes_tab_has_no_fabricated_security_state() -> None:
    """BUG-15/16: no mock expiry statuses or foreign enforcement logs."""
    src = (
        ROOT / "ui" / "src" / "pages" / "AgentDetail.tsx"
    ).read_text(encoding="utf-8")
    block = src[src.find("function ScopesTab") :]
    assert "Mock enforcement log data" not in block
    assert "Expiring soon" not in block
    assert "Expired" not in block
    assert "salesforce" not in block
    assert "hubspot" not in block
    assert "No enforcement decisions recorded" in block
    assert "tool:${connector || \"agenticorg\"}:execute:${tool}" in src


def test_each_ca_pack_agent_binds_callable_tools_in_zoho_only_env() -> None:
    """BUG-11: in Uday's reported Zoho-only env, every CA pack agent
    must bind at least one callable tool. Prior to the fix the LLM
    received zero tools and shadow_sample runs collapsed to text-only
    responses at 0.24-0.40 confidence with empty tool_calls.

    This pin replays the runtime contract — not a source-grep — by
    constructing the same bound-tool list ``build_tools_for_agent``
    hands to LangGraph when the runner is dispatched.
    """
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import _normalize_tool_names
    from core.langgraph.tool_adapter import build_tools_for_agent

    zoho_only = ["zoho_books"]
    for agent_cfg in CA_PACK["agents"]:
        normalized = _normalize_tool_names(
            [str(t) for t in agent_cfg.get("tools", [])]
        )
        bound = build_tools_for_agent(normalized, {}, zoho_only)
        assert bound, (
            f"{agent_cfg['name']} has no bound tools for a Zoho-only "
            "tenant — shadow runs will return empty tool_calls and "
            "collapse to the LLM-only confidence floor (BUG-11 reopen)."
        )

    # Empty allow-list must still fail-closed (BUG-08 contract).
    for agent_cfg in CA_PACK["agents"]:
        normalized = _normalize_tool_names(
            [str(t) for t in agent_cfg.get("tools", [])]
        )
        bound_closed = build_tools_for_agent(normalized, {}, [])
        assert bound_closed == [], (
            f"{agent_cfg['name']} leaked tools through the empty "
            "allow-list — fail-closed broken."
        )
