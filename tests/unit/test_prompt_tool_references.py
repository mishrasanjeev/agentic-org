# SPDX-License-Identifier: Apache-2.0
"""Prompts call only tools the agent has; defaults name only registered tools.

PRD §7 F-3: every tool a built-in prompt names must be registered by a
connector and present in that agent type's default tools. Runs
``scripts/check_prompt_tools.py`` against the repository (no baseline) and
pins the parser and rules on synthetic prompts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_prompt_tools import (
    REASON_DEFAULT_UNREGISTERED,
    REASON_NO_DEFAULTS,
    REASON_NOT_IN_DEFAULTS,
    REASON_UNREGISTERED,
    CheckSetupError,
    agent_type_for_prompt,
    check,
    check_repository,
    extract_tool_refs,
    main,
)


def test_repository_prompts_and_defaults_have_no_violations():
    violations = check_repository()
    assert violations == [], "\n".join(str(v) for v in violations)


def test_script_exit_code_is_zero_for_the_repository(capsys):
    assert main() == 0
    assert "prompt tool check: ok" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _names(text: str) -> list[str]:
    return [ref.name for ref in extract_tool_refs(text)]


def test_parser_finds_call_empty_and_snake_case_forms():
    text = (
        "1. ONBOARD — call sanctions_screen(). call gstn_validate(gstin).\n"
        "2. Call create_tweet() only after approval; then query().\n"
        "3. call payment_link(invoice_id, amount) and jira:create_issue().\n"
    )
    assert _names(text) == [
        "sanctions_screen",
        "gstn_validate",
        "create_tweet",
        "query",
        "payment_link",
        "jira:create_issue",
    ]


def test_parser_skips_token_scope_block_including_continuation_lines():
    text = (
        "Token scope: oracle_fusion(r/w:journal,r:po) gstn(r:validate)\n"
        "             banking_api(w:queue_payment:capped:{{x}})\n"
        "             email(w:remittance) ocr_service(r:extract)\n"
        "\n"
        "<processing_sequence>\n"
        "  call list_invoices().\n"
    )
    refs = extract_tool_refs(text)
    assert [(r.line, r.name) for r in refs] == [(6, "list_invoices")]


def test_parser_ignores_formulas_prose_and_permission_lists():
    text = (
        "match_delta = abs(invoice.total - po.amount).\n"
        "Classify by days overdue (30/60/90+).\n"
        "zendesk(r:ticket,w:update_ticket) on a non-scope line.\n"
        "vendor.email(x) is data, not a tool.\n"
    )
    assert _names(text) == []


@pytest.mark.parametrize(
    ("stem", "expected"),
    [("ap_processor", "ap_processor"), ("abm_agent", "abm"), ("close_agent", "close_agent"), ("sales_agent", None)],
)
def test_prompt_stem_maps_to_agent_type(stem, expected):
    defaults = {"ap_processor": [], "abm": [], "close_agent": []}
    assert agent_type_for_prompt(stem, defaults) == expected


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

BARE = {"list_invoices": ("zoho_books", ""), "create_issue": ("github", ""), "get_ticket": ("zendesk", "")}
ALIASES = {
    **BARE,
    "jira:create_issue": ("jira", ""),
    "github:create_issue": ("github", ""),
    "zoho_books:list_invoices": ("zoho_books", ""),
}


def _run(tmp_path: Path, prompts: dict[str, str], agent_defaults, domain_defaults=None):
    for stem, text in prompts.items():
        (tmp_path / f"{stem}.prompt.txt").write_text(text, encoding="utf-8")
    return check(
        prompts_dir=tmp_path,
        agent_defaults=agent_defaults,
        domain_defaults=domain_defaults or {},
        bare_index=BARE,
        alias_index=ALIASES,
    )


def test_unregistered_prompt_tool_is_a_violation(tmp_path):
    violations = _run(tmp_path, {"ap_processor": "call erp_get_po(ref)."}, {"ap_processor": ["list_invoices"]})
    assert [(v.name, v.reason) for v in violations] == [("erp_get_po", REASON_UNREGISTERED)]


def test_registered_tool_missing_from_agent_defaults_is_a_violation(tmp_path):
    violations = _run(tmp_path, {"support_triage": "call get_ticket()."}, {"support_triage": ["list_invoices"]})
    assert [(v.where, v.name, v.reason) for v in violations] == [
        ("support_triage.prompt.txt:1", "get_ticket", REASON_NOT_IN_DEFAULTS)
    ]


def test_prompt_without_default_tool_list_may_not_call_tools(tmp_path):
    violations = _run(tmp_path, {"sales_agent": "call list_invoices()."}, {"ap_processor": ["list_invoices"]})
    assert [(v.name, v.reason) for v in violations] == [("list_invoices", REASON_NO_DEFAULTS)]


def test_bare_prompt_reference_matches_a_qualified_default(tmp_path):
    assert _run(tmp_path, {"vendor_manager": "call create_issue()."}, {"vendor_manager": ["jira:create_issue"]}) == []


def test_qualified_prompt_reference_must_match_the_same_connector(tmp_path):
    violations = _run(tmp_path, {"vendor_manager": "call jira:create_issue()."}, {"vendor_manager": ["create_issue"]})
    assert [(v.name, v.reason) for v in violations] == [("jira:create_issue", REASON_NOT_IN_DEFAULTS)]


def test_unregistered_or_misattributed_defaults_are_violations(tmp_path):
    violations = _run(
        tmp_path,
        {"ap_processor": "no tools"},
        {"ap_processor": ["list_invoices", "slack_send_message", "nowhere:create_issue"]},
        {"comms": ["slack_send_message"]},
    )
    assert {(v.where, v.name, v.reason) for v in violations} == {
        ("agent_type:ap_processor", "slack_send_message", REASON_DEFAULT_UNREGISTERED),
        ("agent_type:ap_processor", "nowhere:create_issue", REASON_DEFAULT_UNREGISTERED),
        ("domain:comms", "slack_send_message", REASON_DEFAULT_UNREGISTERED),
    }


def test_missing_prompts_directory_fails_closed(tmp_path):
    with pytest.raises(CheckSetupError):
        check(
            prompts_dir=tmp_path / "missing",
            agent_defaults={},
            domain_defaults={},
            bare_index=BARE,
            alias_index=ALIASES,
        )


def test_empty_registry_fails_closed(tmp_path):
    (tmp_path / "ap_processor.prompt.txt").write_text("call list_invoices()", encoding="utf-8")
    with pytest.raises(CheckSetupError):
        check(prompts_dir=tmp_path, agent_defaults={}, domain_defaults={}, bare_index={}, alias_index={})


def test_setup_failure_exits_two(monkeypatch, capsys):
    import scripts.check_prompt_tools as module

    def _boom():
        raise CheckSetupError("registry unavailable")

    monkeypatch.setattr(module, "check_repository", _boom)
    assert module.main() == 2
    assert "could not run" in capsys.readouterr().err
