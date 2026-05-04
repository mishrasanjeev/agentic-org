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


def test_ar_collections_suffix_does_not_recommend_unsupported_filter() -> None:
    """Issue #440 follow-up (Codex P2 on PR #443): the AR Collections
    suffix must NOT instruct the LLM to filter ``list_invoices`` by
    invoice number. The Zoho ``list_invoices`` method only accepts
    status/customer_id/date_start/date_end/page; an invoice-number arg
    gets silently ignored, the LLM receives a broad list, and may act
    on the wrong record. Pin the supported-fields language and the
    fetch-then-filter-client-side fallback for invoice-specific asks."""
    from core.agents.packs.ca import CA_PACK

    ar = next(
        a for a in CA_PACK["agents"] if a.get("name") == "AR Collections Agent"
    )
    suffix = ar.get("system_prompt_suffix") or ""
    # Hard-stop: no language asking the LLM to "filter by invoice number"
    # (regardless of casing) — that's the unsupported-arg trap.
    lower = suffix.lower()
    assert "invoice number" not in lower or "do not pass" in lower or "do NOT pass" in suffix, (
        "AR suffix tells the LLM to use an invoice-number filter without "
        "the explicit 'do NOT pass' caveat. Zoho list_invoices doesn't "
        "support that field; the LLM will pass an unsupported arg and "
        "the connector will silently ignore it. Either remove the "
        "instruction or call out the unsupported arg explicitly."
    )
    # Pin the safer fetch-then-filter-client-side fallback for
    # invoice-specific asks like 'remind for INV-123'.
    assert "client-side" in lower or "client side" in lower
    # Pin the supported-fields list as documentation so a reader can
    # verify against the connector signature.
    assert "customer_id" in suffix


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


# ----------------------------------------------------------------------
# Issue #440 — TDS Compliance Agent deterministic chat route. The
# tester's exact prompt must produce a calculate_tds tool_call without
# asking for amount, section, or period. Filing must fail-closed
# without invoking the real filing API.
# ----------------------------------------------------------------------


def _canonical_tester_prompt() -> str:
    return (
        "Calculate TDS for vendor payment of INR 50,000 under Section 194C "
        "for April 2026 and file Form 26Q"
    )


def test_tds_route_returns_none_for_other_agents() -> None:
    """Issue #440 scope: deterministic route must NEVER fire for non-TDS
    agents. Bank reconciliation, AR collections, FP&A, GST filing all
    keep the existing LLM path."""
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    for other in (
        "gst_filing_agent",
        "bank_reconciliation_agent",
        "fp_a_analyst_agent",
        "ar_collections_agent",
        "campaign_pilot",
        None,
    ):
        result = asyncio.run(
            try_tds_deterministic_route(
                agent_type=other,
                query=_canonical_tester_prompt(),
            )
        )
        assert result is None, (
            f"Deterministic TDS route fired for agent_type={other!r} — "
            "scope leak. The route must only fire for "
            "'tds_compliance_agent'."
        )


def test_tds_route_returns_none_when_signals_missing() -> None:
    """Issue #440: the route must fall through to the LLM whenever a
    required signal is missing. Three negative cases."""
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    cases = [
        # No section
        "Calculate TDS for vendor payment of INR 50,000 for April 2026",
        # No amount
        "Calculate TDS under Section 194C for April 2026",
        # No action verb
        "Vendor payment of INR 50,000 under Section 194C in April 2026",
        # Empty
        "",
    ]
    for query in cases:
        result = asyncio.run(
            try_tds_deterministic_route(
                agent_type="tds_compliance_agent",
                query=query,
            )
        )
        assert result is None, (
            f"Route fired for incomplete query {query!r} — should fall "
            "through to LLM."
        )


def test_tds_route_canonical_tester_prompt_invokes_calculate_tds() -> None:
    """Issue #440 / BUG-17 closure: the exact tester prompt produces a
    calculate_tds tool_call with the extracted arguments, no asks for
    amount/section/period that were already in the prompt, and a
    fail-closed Form 26Q filing blocker (no real filing call)."""
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query=_canonical_tester_prompt(),
        )
    )
    assert result is not None, "Route must fire for the canonical prompt"

    # tool_calls evidence
    tool_calls = result["tool_calls"]
    assert len(tool_calls) == 1
    tc = tool_calls[0]
    assert tc["tool"] == "calculate_tds"
    assert tc["connector"] == "zoho_books"
    assert tc["arguments"]["amount"] == 50000.0
    assert tc["arguments"]["section"] == "194C"
    # The route's deterministic-marker is what audit/forensics will look
    # for to distinguish runtime-routed calls from LLM-decided ones.
    assert tc.get("deterministic_route") is True
    assert tc.get("route_reason") == "issue_440_tds_runtime_routing"

    # The TDS calculation result is in the tool_call.
    assert tc["result"]["status"] == "calculated"
    assert tc["result"]["tds_amount"] == 500.0  # 50000 * 0.01 (194C individual)
    assert tc["result"]["rate"] == 0.01

    # tools_used flag must be true so downstream audit treats this as a
    # tool-backed answer.
    assert result["tools_used"] is True

    # Confidence must NOT be the LLM-only floor (~0.4). The deterministic
    # path has hard evidence; floor here is much higher.
    assert result["confidence"] >= 0.85

    # Answer text — anti-clarification + fail-closed blocker
    answer = result["answer"]
    answer_lower = answer.lower()

    # Calculation reported
    assert "500" in answer  # tds_amount
    assert "1.00%" in answer or "1.0%" in answer or "1%" in answer
    assert "49,500" in answer or "49500" in answer  # net_payable

    # NO clarification ask for already-provided fields. These exact
    # phrases would be the BUG-17 symptom resurfacing.
    forbidden = (
        "what is the payment amount",
        "please provide the payment amount",
        "please provide the amount",
        "what is the section",
        "please provide the section",
        "please provide the period",
        "what period",
        "amount of payment",
    )
    for phrase in forbidden:
        assert phrase not in answer_lower, (
            f"BUG-17 clarification phrase {phrase!r} appeared in the "
            "deterministic route answer — should never re-ask for "
            "fields that were extracted from the prompt."
        )

    # Form 26Q filing was requested → fail-closed blocker present
    assert "26Q" in answer
    assert "blocked" in answer_lower or "not made" in answer_lower
    # Enumerate the genuinely-missing fields (nothing about amount/section)
    assert "PAN" in answer or "pan" in answer_lower
    assert "TAN" in answer or "tan" in answer_lower
    assert "HITL" in answer or "human-in-the-loop" in answer_lower


def test_tds_route_calculation_only_when_no_filing_requested() -> None:
    """Issue #440: a calculate-only prompt (no Form 26Q) returns the
    calculation cleanly with NO filing blocker noise."""
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query="Calculate TDS for INR 100,000 under Section 194J",
        )
    )
    assert result is not None
    assert result["tool_calls"][0]["arguments"]["amount"] == 100000.0
    assert result["tool_calls"][0]["arguments"]["section"] == "194J"
    answer = result["answer"]
    # Calculation present
    assert "10,000" in answer or "10000" in answer  # 194J = 10%
    # No filing blocker noise when filing wasn't asked for
    assert "26Q" not in answer
    assert "TAN" not in answer
    assert "HITL" not in answer


def test_tds_route_unsupported_section_returns_clear_error() -> None:
    """Issue #440 (Codex P1 on PR #445): if the section is a 194-series
    code we don't have a rate for, the route MUST fire and surface an
    explicit unsupported-section error. Never silently fall through to
    the LLM — that would let the LLM hallucinate a rate.

    Pre-fix: an allowlist regex (``194[ACHIJOQ]``) skipped ``194ZZZ``
    entirely, so ``_extract_section`` returned None and the route
    returned None too. Now the regex matches any ``194<letter>+`` and
    the calculator's "unsupported TDS section" error reaches the user.
    """
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query="Calculate TDS for INR 50,000 under Section 194ZZZ",
        )
    )
    assert result is not None, (
        "Route must fire for any 194-series code (even unsupported ones) "
        "and surface the calculator's error — never silently fall "
        "through to the LLM where a hallucinated rate would slip past."
    )
    answer = result["answer"].lower()
    assert "unsupported" in answer or "unable" in answer
    assert "194a" in answer  # supported list mentioned
    # Tools NOT used (calculator rejected the input)
    assert result["tools_used"] is False
    assert result["tool_calls"] == []


def test_tds_route_section_192_falls_through_to_llm() -> None:
    """Issue #440 (Codex P2 on PR #445): Section 192 (salary TDS) is
    slab-based with HRA / standard-deduction / regime variants that
    the deterministic calculator can't model. The route gate must
    NOT include 192; salary-TDS prompts must reach the LLM which can
    reason about the slabs.
    """
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    for query in (
        "Calculate TDS on salary of INR 50,00,000 under Section 192 for FY26",
        "Compute Section 192 TDS for an employee earning INR 80,000/month",
    ):
        result = asyncio.run(
            try_tds_deterministic_route(
                agent_type="tds_compliance_agent",
                query=query,
            )
        )
        assert result is None, (
            f"Section 192 prompt {query!r} must fall through to the LLM. "
            "The deterministic calculator has no slab support; "
            "intercepting it would surface a useless 'unsupported "
            "section' error instead of letting the LLM walk the user "
            "through salary-TDS slab math."
        )


# ----------------------------------------------------------------------
# Issue #447 — CA shadow fixtures. Each CA pack agent must carry a
# shadow_fixture in its pack manifest so api/v1/agents.py:run_agent
# substitutes a structured task when shadow_sample is dispatched.
# Without these, the runner's generic "exercise ONE tool" hint kept
# gpt-4o picking zero tools → BUG-11 shadow accuracy stuck at 0.24
# floor across 4+ samples on Uday's prod tenant.
# ----------------------------------------------------------------------


_EXPECTED_FIXTURES: dict[str, dict[str, object]] = {
    "GST Filing Agent": {
        # Issue #450: was ``generate_gst_report`` (Zoho India v3 ``/reports/
        # gstsummary`` returns 404 — endpoint missing on the IN region).
        # Now ``list_invoices`` — read-only, structured response on any
        # tenant, aligned with the GST workflow seed-data step.
        "expected_tool": "list_invoices",
        "must_not_contain_write": ("file_gstr", "push_gstr1", "generate_eway_bill"),
    },
    "TDS Compliance Agent": {
        "expected_tool": "calculate_tds",
        "deterministic_route": "tds",
        "must_not_contain_write": ("file_form_26q", "file_26q_return", "pay_tax_challan"),
    },
    "Bank Reconciliation Agent": {
        "expected_tool": "check_account_balance",
        "must_not_contain_write": ("reconcile_bank", "post_voucher"),
    },
    "FP&A Analyst Agent": {
        # Issue #450: was ``get_trial_balance`` (Zoho v3 ``/reports/
        # trialbalance`` returned 400 without explicit date params).
        # Now ``get_profit_loss`` with from_date/to_date in the prompt.
        "expected_tool": "get_profit_loss",
        "must_not_contain_write": ("post_voucher",),
    },
    "AR Collections Agent": {
        "expected_tool": "list_overdue_invoices",
        "must_not_contain_write": ("send_email", "send"),
    },
}


def test_every_ca_agent_has_a_shadow_fixture() -> None:
    from core.agents.packs.ca import CA_PACK

    for agent_cfg in CA_PACK["agents"]:
        name = agent_cfg["name"]
        fixture = agent_cfg.get("shadow_fixture")
        assert isinstance(fixture, dict), (
            f"{name} missing shadow_fixture — shadow_sample will fall "
            "back to the generic exploratory hint and BUG-11's "
            "no-tool-call floor will resurface."
        )
        prompt = fixture.get("prompt")
        expected_tool = fixture.get("expected_tool")
        assert isinstance(prompt, str) and prompt.strip(), (
            f"{name} fixture has no prompt"
        )
        assert isinstance(expected_tool, str) and expected_tool.strip(), (
            f"{name} fixture has no expected_tool"
        )
        spec = _EXPECTED_FIXTURES[name]
        assert expected_tool == spec["expected_tool"], (
            f"{name} expected_tool={expected_tool!r} differs from spec "
            f"{spec['expected_tool']!r}."
        )
        for forbidden in spec.get("must_not_contain_write", ()):
            assert forbidden not in expected_tool, (
                f"{name} shadow fixture names a write tool "
                f"({forbidden}). Shadow samples must be read-only."
            )


def test_tds_fixture_is_deterministic_route() -> None:
    from core.agents.packs.ca import CA_PACK

    tds = next(a for a in CA_PACK["agents"] if a["name"] == "TDS Compliance Agent")
    fixture = tds["shadow_fixture"]
    assert fixture.get("deterministic_route") == "tds"
    import asyncio

    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query=fixture["prompt"],
        )
    )
    assert result is not None, (
        f"TDS shadow fixture prompt {fixture['prompt']!r} does NOT "
        "match the chat deterministic route's detection — bypass "
        "would fall through to LLM and BUG-11 stays open."
    )
    tc = result["tool_calls"][0]
    assert tc["tool"] == "calculate_tds"
    assert tc["arguments"]["amount"] == 50000.0
    assert tc["arguments"]["section"] == "194C"
    assert result["confidence"] >= 0.85


def test_runner_uses_shadow_prompt_when_fixture_supplies_one() -> None:
    from core.langgraph.runner import _build_user_message

    msg = _build_user_message({
        "action": "shadow_sample",
        "inputs": {
            "shadow_prompt": "Fetch the trial balance as of 31 March 2026",
            "shadow_expected_tool": "get_trial_balance",
        },
    })
    assert "Fetch the trial balance as of 31 March 2026" in msg
    assert "get_trial_balance" in msg
    assert "Exercise ONE of the" not in msg


def test_runner_falls_through_to_generic_hint_without_fixture() -> None:
    from core.langgraph.runner import _build_user_message

    msg = _build_user_message({"action": "shadow_sample", "inputs": {}})
    assert "Exercise ONE of the" in msg
    assert "shadow-mode test sample run" in msg


def test_installer_persists_shadow_fixture_on_create_and_repair() -> None:
    src = (
        ROOT / "core" / "agents" / "packs" / "installer.py"
    ).read_text(encoding="utf-8")
    occurrences = src.count("shadow_fixture")
    assert occurrences >= 2, (
        "installer.py should persist shadow_fixture in BOTH create + "
        f"repair branches. Found {occurrences} references."
    )
    repair_block = src[src.find('cfg["pack_install"] = pack_cfg'):]
    assert "shadow_fixture" in repair_block, (
        "Repair branch missing shadow_fixture refresh — existing "
        "agents on tenants backfilled before #447 won't pick up the "
        "new behavior on the next sync."
    )


def test_api_run_agent_substitutes_shadow_fixture() -> None:
    src = (
        ROOT / "api" / "v1" / "agents.py"
    ).read_text(encoding="utf-8")
    assert "shadow_fixture" in src
    assert "shadow_prompt" in src
    assert "deterministic_route" in src
    assert "try_tds_deterministic_route" in src
    assert "#447" in src


def test_api_run_agent_strips_caller_supplied_shadow_prompt() -> None:
    """Codex P1 on PR #449: API callers must NEVER be able to supply
    their own shadow_prompt for a shadow_sample dispatch. The runner
    executes shadow_prompt verbatim, so honoring caller overrides
    would let any client run arbitrary prompts (including ones that
    request mutating tools) under the read-only shadow safety
    guarantee. Pin both the strip + the unconditional fixture set."""
    src = (
        ROOT / "api" / "v1" / "agents.py"
    ).read_text(encoding="utf-8")
    # Caller's shadow_prompt + shadow_expected_tool must be stripped
    # before fixture lookup
    assert 'incoming_inputs.pop("shadow_prompt", None)' in src
    assert 'incoming_inputs.pop("shadow_expected_tool", None)' in src
    # Fixture set is unconditional (no setdefault — server-side wins)
    assert 'incoming_inputs["shadow_prompt"] = str(fixture["prompt"])' in src
    # Codex traceability tag
    assert "Codex P1 on PR #449" in src or "Codex P1 on #449" in src


def test_api_run_agent_gates_fixture_on_authorized_tools() -> None:
    """Codex P2 on PR #449: every fixture path (deterministic bypass
    AND structured-prompt injection) must verify the fixture's
    expected_tool is currently in agent.authorized_tools. Otherwise a
    removed/disabled tool would still get scored synthetically with
    high confidence, masking real config drift and inflating
    shadow_accuracy_current."""
    src = (
        ROOT / "api" / "v1" / "agents.py"
    ).read_text(encoding="utf-8")
    # The gate variable + its in-authorized check
    assert "fixture_tool_authorized" in src
    assert "in (authorized_tools or [])" in src
    # Drift-warning log so operators see the fall-through
    assert "agent_run_shadow_fixture_tool_not_authorized" in src
    # When tool not authorized, fall through to generic hint
    assert "fixture = {}" in src
    # Codex traceability tag
    assert "Codex P2 on PR #449" in src or "Codex P2 on #449" in src


# ----------------------------------------------------------------------
# Issue #450 — structured tool-result failure detection. The previous
# ``"error" in msg_content.lower()`` substring scan in
# core/langgraph/agent_graph.py:218 capped confidence to 0.5 on
# legitimately-empty tool responses (e.g. AR list_overdue_invoices on
# a tenant with zero overdue invoices). The fix replaces it with
# ``_tool_message_indicates_failure`` which classifies based on
# structured signals.
# ----------------------------------------------------------------------


def test_empty_list_response_is_not_a_failure() -> None:
    """Issue #450: AR Collections returned ``{"invoices": [], ...}`` on
    Uday's tenant (no overdue invoices). Pre-fix: substring scan saw
    no "error"/"failed" but other paths still capped. Post-fix: this
    response classifies as success and the tool's empty result is
    treated as legitimate data."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    # AR Collections actual response from prod 2026-05-04
    ar_resp = (
        '{"invoices": [], "page_context": {"page": 1, "per_page": 200, '
        '"has_more_page": false, "report_name": "Invoices", '
        '"applied_filter": "Status.All", "search_criteria": '
        '[{"column_name": "status", "search_text": "overdue", '
        '"comparator": "equal"}]}}'
    )
    assert _tool_message_indicates_failure(ar_resp) is False

    # Empty dict — also success
    assert _tool_message_indicates_failure("{}") is False
    # Empty list — also success
    assert _tool_message_indicates_failure("[]") is False
    # Bank Reconciliation's actual successful response
    bank_resp = (
        '{"bankaccounts": [{"account_id": "3711099000000000459", '
        '"account_name": "Petty Cash", "balance": -30000.0}]}'
    )
    assert _tool_message_indicates_failure(bank_resp) is False


def test_response_with_word_error_in_field_name_is_not_failure() -> None:
    """Issue #450: a response that happens to contain the word
    "error" or "failed" in normal field names (validation_errors,
    failed_attempts as a counter, etc.) was misclassified as failure
    by the prior substring scan. Structured detection only flags
    actual error signals."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    # Common Zoho-style response with empty validation_errors list
    resp1 = '{"invoices": [{"id": 1}], "validation_errors": []}'
    assert _tool_message_indicates_failure(resp1) is False

    # Counter field named with "failed"
    resp2 = '{"data": {"total_attempts": 10, "failed_attempts": 0}}'
    assert _tool_message_indicates_failure(resp2) is False

    # Documentation string mentioning errors in a successful payload
    resp3 = (
        '{"status": "calculated", "tds_amount": 500.0, '
        '"notes": "Section 206AA error rate would apply if PAN missing"}'
    )
    assert _tool_message_indicates_failure(resp3) is False


def test_exception_wrapped_response_is_a_failure() -> None:
    """Issue #450: real failures still cap correctly. Tool that raised
    is wrapped by LangGraph's ToolNode with an Error: prefix; that
    must be detected."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    # Direct exception class name (LangGraph ToolNode exception wrap)
    assert _tool_message_indicates_failure(
        "Error: HTTPStatusError('Client error \\'400 \\' for url ...')"
    ) is True
    assert _tool_message_indicates_failure(
        "HTTPStatusError: Client error '404' for url ..."
    ) is True
    # Python traceback
    assert _tool_message_indicates_failure(
        "Traceback (most recent call last):\n  File \"x\", line 1\n..."
    ) is True
    # Generic Exception: prefix
    assert _tool_message_indicates_failure(
        "Exception: tenant has no active connector"
    ) is True


def test_langgraph_toolnode_invocation_errors_are_failures() -> None:
    """Issue #450 / Codex P1 on PR #452: LangGraph's ToolNode emits
    plain-text wrappers like ``Error invoking tool ... with error: …``
    when an LLM-supplied arg fails schema validation BEFORE the tool
    body runs. Initial prefix list (\"Error: \", ...) missed these
    exact strings and would classify them as success, capping nothing
    and inflating shadow scoring.
    """
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    # Canonical LangGraph ToolNode patterns
    assert _tool_message_indicates_failure(
        "Error invoking tool calculate_tds with error: amount is required"
    ) is True
    assert _tool_message_indicates_failure(
        "Error executing tool list_invoices: connection timeout"
    ) is True
    assert _tool_message_indicates_failure(
        "Error in tool call get_trial_balance: invalid date format"
    ) is True
    # langchain-core exception classes
    assert _tool_message_indicates_failure(
        "ToolException: rate limit exceeded"
    ) is True
    assert _tool_message_indicates_failure(
        "ToolInvocationError: bad args"
    ) is True
    # Pydantic validation failures (surface when LLM passes wrong
    # arg shape — common with structured tool calls)
    assert _tool_message_indicates_failure(
        "ValidationError: 1 validation error for CalculateTdsArgs"
    ) is True


def test_structured_status_error_is_failure() -> None:
    """Issue #450: a connector that returned a structured dict with
    ``status="error"`` is the canonical "tool failed cleanly" signal.
    Connector authors set this to communicate failure without raising."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    assert _tool_message_indicates_failure(
        '{"status": "error", "message": "missing required field"}'
    ) is True
    assert _tool_message_indicates_failure(
        '{"status": "ERROR"}'  # case insensitive
    ) is True
    # ``error`` field with a non-empty string value
    assert _tool_message_indicates_failure(
        '{"error": "rate limit exceeded"}'
    ) is True
    # ``error`` field with truthy dict
    assert _tool_message_indicates_failure(
        '{"error": {"code": 429, "message": "rate limit"}}'
    ) is True


def test_blank_tool_message_is_failure() -> None:
    """Issue #450: a tool that returned literally nothing didn't
    communicate a result. Treat as failure so we don't silently score
    blank responses as success."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    assert _tool_message_indicates_failure("") is True
    assert _tool_message_indicates_failure("   \n  ") is True
    assert _tool_message_indicates_failure(None) is True


def test_python_repr_of_dict_is_handled() -> None:
    """Issue #450: ToolNode sometimes str()'s a dict response which
    produces a Python repr (single-quoted keys). Both JSON-style and
    Python-style dict reprs must classify the same way."""
    from core.langgraph.agent_graph import _tool_message_indicates_failure

    # Python repr of empty success dict
    assert _tool_message_indicates_failure(
        "{'invoices': [], 'page_context': {'page': 1}}"
    ) is False
    # Python repr of structured error
    assert _tool_message_indicates_failure(
        "{'status': 'error', 'message': 'no creds'}"
    ) is True


def test_agent_graph_uses_structured_detection() -> None:
    """Issue #450 wiring guard: the ToolMessage classification path in
    agent_graph.py must call ``_tool_message_indicates_failure`` and
    NOT use the old substring scan. Source-pin so a future cleanup
    doesn't reintroduce the brittle heuristic."""
    src = (
        ROOT / "core" / "langgraph" / "agent_graph.py"
    ).read_text(encoding="utf-8")
    # Helper exists
    assert "def _tool_message_indicates_failure" in src
    # Wired into the ToolMessage loop
    assert "is_error = _tool_message_indicates_failure(msg_content)" in src
    # The old substring scan must NOT be present — that was the bug
    assert (
        '"error" in msg_content.lower() or "failed" in msg_content.lower()'
        not in src
    )
    # Issue traceability
    assert "Issue #450" in src or "issue #450" in src.lower()
