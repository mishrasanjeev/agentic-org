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
    assert "GST auto-file cannot be enabled" in detail_src
    # Asymmetric gate: enabling without creds is blocked, but a row that is
    # already gst_auto_file=true on a tenant with no GSTN cred MUST remain
    # togglable to OFF — otherwise existing unsafe rows are locked into the
    # silent-failure state. The disable predicate must AND not-currently-true.
    assert "!hasActiveGstnCredential && !editForm.gst_auto_file" in detail_src
    assert "filings will silently fail" in detail_src
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


# ----------------------------------------------------------------------
# Issue #440 — BUG-11/17 prompt-runtime residual: every CA pack agent
# must carry an interactive-extraction instruction so the LLM extracts
# already-provided fields (amount/section/period/etc.) and calls the
# tool directly instead of asking the user to repeat them. The TDS
# agent additionally carries a worked example of the canonical tester
# prompt → calculate_tds invocation.
# ----------------------------------------------------------------------


def test_every_ca_pack_agent_has_prompt_file_and_extraction_suffix() -> None:
    """Issue #440: each CA pack agent must wire a prompt_file (so the
    SOP-level prompt is loaded into system_prompt_text by the installer)
    AND carry the INTERACTIVE EXTRACTION instruction in its
    system_prompt_suffix. Without these, BUG-17's 'agent asks for
    fields already in the user prompt' symptom can't close even after
    the tool-binding repair."""
    from core.agents.packs.ca import CA_PACK

    for agent_cfg in CA_PACK["agents"]:
        name = agent_cfg.get("name", "?")
        prompt_file = agent_cfg.get("prompt_file") or ""
        suffix = agent_cfg.get("system_prompt_suffix") or ""
        assert prompt_file.endswith(".prompt.txt"), (
            f"{name} is missing prompt_file — installer's _build_system_prompt "
            "won't pick up the SOP. Issue #440."
        )
        assert "INTERACTIVE EXTRACTION" in suffix, (
            f"{name} is missing INTERACTIVE EXTRACTION clause — LLM will keep "
            "asking the user to repeat already-provided fields. Issue #440."
        )
        # Pin the wording reviewer should not regress.
        assert "issue #440" in suffix.lower() or "440" in suffix, (
            f"{name} suffix should reference issue #440 so a future cleanup "
            "doesn't drop the extraction clause silently."
        )


def test_tds_compliance_suffix_carries_canonical_tester_example() -> None:
    """Issue #440: the TDS Compliance agent's suffix must carry the
    exact canonical tester prompt + the explicit extraction → tool-call
    walkthrough so the LLM has a concrete pattern to mimic, not just an
    abstract instruction."""
    from core.agents.packs.ca import CA_PACK

    tds = next(
        a for a in CA_PACK["agents"] if a.get("name") == "TDS Compliance Agent"
    )
    suffix = tds.get("system_prompt_suffix") or ""
    # Canonical tester prompt fragments
    assert "INR 50,000" in suffix
    assert "Section 194C" in suffix
    assert "April 2026" in suffix
    assert "Form 26Q" in suffix
    # Concrete extraction → tool-call walkthrough
    assert "amount=50000" in suffix
    assert 'section="194C"' in suffix or "section='194C'" in suffix
    assert "calculate_tds" in suffix
    # Explicit anti-pattern callout
    assert "BUG-17" in suffix


def test_tds_compliance_prompt_file_has_interactive_extraction_section() -> None:
    """Issue #440: the SOP prompt itself must carry the extraction
    section so the LLM sees it whether it landed via the prompt_file
    path or the suffix path. Belt-and-braces — the procedural sequence
    that follows is correct for filing flows but was driving BUG-17 for
    ad-hoc calculate-only chat queries."""
    src = (
        ROOT / "core" / "agents" / "packs" / "ca" / "prompts"
        / "tds_compliance.prompt.txt"
    ).read_text(encoding="utf-8")
    assert "<interactive_extraction>" in src
    assert "Issue #440" in src
    # The worked example mirrors the suffix
    assert "INR 50,000" in src
    assert "Section 194C" in src
    assert "calculate_tds(amount=50000" in src
    # Anti-restate-and-ask language
    assert "Never restate-and-ask" in src or "do NOT re-ask" in src.lower()


def test_ca_pack_agent_types_match_existing_db_rows() -> None:
    """Issue #440 wiring guard: ``_agent_type`` is the value the
    installer's lookup key (Agent.agent_type) is compared against.
    Existing prod rows have agent_types derived from the pre-PR-#434
    slugified-name path: ``gst_filing_agent``, ``tds_compliance_agent``,
    ``bank_reconciliation_agent``, ``fp_a_analyst_agent``,
    ``ar_collections_agent``. Adding an explicit ``type:`` to any pack
    agent dict (a tempting refactor) would change ``_agent_type`` output
    and miss every existing row on backfill, creating duplicate agents.
    Pin the derived types to the live DB convention so this stays
    impossible without an explicit migration."""
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import _agent_type

    expected_types = {
        "GST Filing Agent": "gst_filing_agent",
        "TDS Compliance Agent": "tds_compliance_agent",
        "Bank Reconciliation Agent": "bank_reconciliation_agent",
        "FP&A Analyst Agent": "fp_a_analyst_agent",
        "AR Collections Agent": "ar_collections_agent",
    }
    for index, agent_cfg in enumerate(CA_PACK["agents"]):
        name = agent_cfg["name"]
        derived = _agent_type(agent_cfg, index)
        assert derived == expected_types[name], (
            f"_agent_type({name!r}) returned {derived!r}, expected "
            f"{expected_types[name]!r}. This drift would create duplicate "
            "agents on the next idempotent backfill — the Agent.agent_type "
            "lookup key would no longer match existing rows. If you really "
            "need to rename, ship a data migration first."
        )


def test_built_tds_system_prompt_includes_extraction_instruction() -> None:
    """Issue #440 runtime contract: the actual string the installer
    persists to ``agent.system_prompt_text`` (and that the LLM sees
    on every chat / shadow turn) must contain BOTH the suffix's
    INTERACTIVE EXTRACTION clause AND the SOP's worked example. This
    pins the wiring end-to-end — if a refactor breaks _build_system_prompt
    or _read_pack_prompt or the prompt_file path resolution, this fires."""
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import _build_system_prompt, get_pack_detail

    detail = get_pack_detail("ca-firm")
    assert detail is not None, "CA pack must resolve via get_pack_detail"
    tds_index, tds_cfg = next(
        (i, a) for i, a in enumerate(CA_PACK["agents"])
        if a.get("name") == "TDS Compliance Agent"
    )
    built = _build_system_prompt("ca-firm", detail, tds_cfg, tds_index)
    # Suffix signals
    assert "INTERACTIVE EXTRACTION" in built
    assert "calculate_tds" in built
    # SOP signals (proves prompt_file was loaded)
    assert "<interactive_extraction>" in built
    assert "Section 234E" in built  # from the existing <tds_rules> block
    # Tool list still appended (sanity)
    assert "Authorized tools" in built
