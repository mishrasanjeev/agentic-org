# SPDX-License-Identifier: Apache-2.0
"""A-7 Business Onboarding Underwriter: runs to completion, degrades gracefully, cites everything, fails closed."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from connectors.framework.verification_provider import Capability
from connectors.providers.mock import FaultKind, MockConfig, MockHttpProvider, MockProvider
from connectors.providers.mock.service import serve_in_thread
from core.agents.business_underwriter import TOOL_SET
from core.agents.business_underwriter.memo import iter_memo_evidence
from core.agents.business_underwriter.prompts import PINNED, PromptIntegrityError, load_prompt
from core.domain_schemas import validate
from core.policy import EXAMPLES_DIR, Policy, evaluate, load_policy
from core.test_doubles.scripted_model import final
from core.tool_gateway.provider_gateway import READ_TOOLS, ToolDecision
from tests.unit.business_underwriter.conftest import ALL_FIXTURES

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def frozen() -> datetime:
    return FROZEN


def policy_for(key: str) -> Policy:
    name = "business_onboarding_uk.yaml" if key.startswith("gb-") else "business_onboarding_us.yaml"
    return load_policy(EXAMPLES_DIR / name)


def narrative_step(messages: list[BaseMessage]) -> AIMessage:
    context = json.loads(str(messages[-1].content))
    summaries = [
        {"section_id": s["section_id"], "summary": "The section is summarised.", "citations": [0]}
        for s in context["sections"]
        if s["status"] in ("complete", "partial") and s["evidence_count"] > 0
    ]
    return final({"summaries": summaries, "confidence": 0.61})


def _codes(memo: dict[str, Any], section_id: str) -> list[str]:
    [section] = [s for s in memo["sections"] if s["section_id"] == section_id]
    return [finding["code"] for finding in section["findings"]]


def _section(memo: dict[str, Any], section_id: str) -> dict[str, Any]:
    return next(s for s in memo["sections"] if s["section_id"] == section_id)


# --- runs to completion ---------------------------------------------------------------------------


async def test_clean_case_runs_to_completion_in_process_with_a_cited_memo(run_case, narrative) -> None:
    model = narrative()
    outcome = await run_case("gb-clean-brightwater")

    assert outcome.status == "completed", outcome.failure_reason
    memo = outcome.memo
    validate("underwriting_memo", memo)
    assert [s["section_id"] for s in memo["sections"]] == [
        "identity", "registry", "ownership", "screening", "web_presence", "activity",
    ]  # fmt: skip
    assert all(s["status"] in ("complete", "partial") for s in memo["sections"])
    assert memo["recommendation"]["basis"] == "policy_result"
    assert memo["recommendation"]["requires_human_decision"] is True
    assert memo["policy_result"]["policy"] == {
        "policy_id": "business_onboarding_uk", "version": "2.0.0", "example": True, "reviewed_by": None,
    }  # fmt: skip
    assert "narrative_summary" in _codes(memo, "identity")
    assert len(model.calls) == 1


async def test_verification_is_started_then_polled_until_the_pending_result_completes(run_case, narrative) -> None:
    narrative()
    outcome = await run_case("gb-clean-brightwater", config=MockConfig(clock=frozen, polls_until_ready=3))
    verify_calls = [(c["tool"], c["outcome"]) for c in outcome.tool_calls if c["capability"] == "verify"]
    assert verify_calls == [
        ("verify_business", "ok"),
        ("verification_result", "pending"),
        ("verification_result", "pending"),
        ("verification_result", "pending"),
        ("verification_result", "ok"),
    ]
    assert _codes(outcome.memo, "registry")[:1] == ["current_officers"] or "registry_active" in _codes(
        outcome.memo, "registry"
    )


async def test_verification_that_never_completes_becomes_a_timeout_error_section(run_case, narrative) -> None:
    narrative()
    backend = MockProvider(MockConfig(clock=frozen, polls_until_ready=100))
    from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter

    outcome = await run_underwriter(
        tenant_id="",
        case_id="case-timeout",
        application=backend.fixture("gb-clean-brightwater").application,
        config=UnderwriterConfig(
            policy=policy_for("gb-"), require_os_isolation=False, verification_timeout_s=0.05, llm_model="scripted"
        ),
        deps=UnderwriterDependencies(provider=backend, clock=frozen),
    )
    assert outcome.status == "completed"
    registry = _section(outcome.memo, "registry")
    assert registry["status"] == "error" and registry["error_reason"] == "provider_timeout"
    assert any(item["item"] == "registry_verification" for item in outcome.memo["missing_items"])


@pytest.fixture(scope="module")
def mock_service() -> Iterator[tuple[str, MockProvider]]:
    backend = MockProvider(MockConfig(clock=frozen, polls_until_ready=1))
    with serve_in_thread(backend, port=int(os.getenv("AGENTICORG_TEST_MOCK_PROVIDER_PORT", "0"))) as base_url:
        yield base_url, backend


async def test_runs_to_completion_against_the_mock_provider_over_http(run_case, narrative, mock_service) -> None:
    base_url, backend = mock_service
    backend.reset()
    narrative()
    over_http = MockHttpProvider(base_url, config=MockConfig(clock=frozen))
    outcome = await run_case("gb-missing-owner-marlpit", provider=over_http)

    assert outcome.status == "completed", outcome.failure_reason
    validate("underwriting_memo", outcome.memo)
    assert "missing_owner" in _codes(outcome.memo, "ownership")
    narrative()
    in_process = await run_case("gb-missing-owner-marlpit", case_id="case-gb-missing-owner-marlpit-local")
    assert outcome.policy_result["fired_rules"] == in_process.policy_result["fired_rules"]
    assert [c["tool"] for c in outcome.tool_calls] == [c["tool"] for c in in_process.tool_calls]


@pytest.mark.parametrize("key", ALL_FIXTURES)
async def test_every_mock_fixture_produces_a_schema_valid_memo(run_case, narrative, key: str) -> None:
    narrative()
    outcome = await run_case(key)
    assert outcome.status == "completed", outcome.failure_reason
    validate("underwriting_memo", outcome.memo)


# --- ownership reconciliation in the memo -------------------------------------------------------


async def test_declared_owner_absent_from_the_graph_raises_missing_owner(run_case, narrative) -> None:
    narrative()
    outcome = await run_case("us-missing-owner-cinderpath")
    assert _codes(outcome.memo, "ownership") == ["missing_owner", "narrative_summary"]
    assert outcome.policy_evidence["ownership"] == {"missing_owners": 1, "undeclared_owners": 0}
    assert "ownership_missing_owner" in outcome.policy_result["fired_rules"]


async def test_graph_owner_the_applicant_did_not_declare_raises_undeclared_owner(run_case, narrative) -> None:
    narrative()
    outcome = await run_case("us-undeclared-owner-larkspur")
    assert "undeclared_owner" in _codes(outcome.memo, "ownership")
    assert outcome.policy_evidence["ownership"] == {"missing_owners": 0, "undeclared_owners": 1}


# --- citations ------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ALL_FIXTURES)
async def test_every_memo_assertion_traces_to_a_record_the_provider_returned(run_case, narrative, key: str) -> None:
    narrative()
    outcome = await run_case(key)
    memo = outcome.memo
    recorded_ids = {record_id for call in outcome.tool_calls for record_id in call["record_ids"]}
    for section in memo["sections"]:
        if section["status"] in ("complete", "partial"):
            assert section["evidence"], section["section_id"]
        else:
            assert not section["findings"], section["section_id"]
        section_keys = {(e["provider"], e["record_id"], e["field"]) for e in section["evidence"]}
        for finding in section["findings"]:
            assert finding["evidence"], (section["section_id"], finding["code"])
            for item in finding["evidence"]:
                assert (item["provider"], item["record_id"], item["field"]) in section_keys
    for where, item in iter_memo_evidence(memo):
        assert item["provider"] == "mock", where
        assert item["record_id"] in recorded_ids, where
        assert item["retrieved_at"], where
    # Every excerpt reference the memo's evidence cites is attached to the memo, whether the
    # sandboxed extractor produced it (exc_...) or the provider cited one on a record it returned.
    attached = {excerpt["excerpt_ref"] for excerpt in memo["excerpts"]}
    cited = {item["excerpt_ref"] for _, item in iter_memo_evidence(memo) if item.get("excerpt_ref")}
    assert cited <= attached, sorted(cited - attached)
    for excerpt in memo["excerpts"]:
        assert excerpt["provider"] == "mock"
        assert excerpt["record_id"]
        assert excerpt["sha256"].startswith("sha256:")


async def test_a_memo_citing_a_record_never_retrieved_fails_the_run_closed(
    run_case, scripted_model, monkeypatch
) -> None:
    from core.agents.business_underwriter import agent as underwriter_agent

    real_build = underwriter_agent.build_sections

    def forged(inv: Any) -> list[dict[str, Any]]:
        sections = real_build(inv)
        sections[0]["findings"][0]["evidence"][0] = {**sections[0]["evidence"][0], "record_id": "mock:company:forged"}
        return sections

    monkeypatch.setattr(underwriter_agent, "build_sections", forged)
    scripted_model([final({"summaries": []})])
    outcome = await run_case("gb-clean-brightwater")
    assert outcome.status == "failed" and outcome.failure_reason == "memo_evidence_untraced"
    assert outcome.memo is None


# --- graceful degradation -----------------------------------------------------------------------


async def test_provider_with_only_resolve_and_verify_yields_not_available_sections(run_case, narrative) -> None:
    narrative()
    outcome = await run_case(
        "gb-missing-owner-marlpit",
        config=MockConfig(clock=frozen, capabilities=frozenset({Capability.RESOLVE, Capability.VERIFY})),
    )
    assert outcome.status == "completed", outcome.failure_reason
    memo = outcome.memo
    validate("underwriting_memo", memo)
    for section_id in ("ownership", "screening", "web_presence", "activity"):
        section = _section(memo, section_id)
        assert section["status"] == "not_available", section_id
        assert section["not_available_reason"] == "capability_not_supported"
    assert _section(memo, "identity")["status"] == "complete"
    assert _section(memo, "registry")["status"] == "complete"
    assert {"ownership_information", "screening_results", "web_presence_information"} <= {
        i["item"] for i in memo["missing_items"]
    }
    # Missing evidence is never read as a pass: the ownership and screening rules fire as indeterminate.
    assert outcome.policy_evidence["ownership"] == {"missing_owners": None, "undeclared_owners": None}
    assert {"ownership_reconciled", "screening_possible_match"} <= set(outcome.policy_result["fired_rules"])
    assert {c["outcome"] for c in outcome.tool_calls if c["capability"] not in ("resolve", "verify")} == {
        "not_available"
    }


async def test_a_provider_error_becomes_an_error_section_not_a_failed_run(run_case, narrative) -> None:
    narrative()
    backend = MockProvider(MockConfig(clock=frozen))
    backend.inject_fault(FaultKind.UNAVAILABLE, capability=Capability.OWNERSHIP)
    outcome = await run_case("gb-clean-brightwater", provider=backend)
    assert outcome.status == "completed"
    ownership = _section(outcome.memo, "ownership")
    assert ownership["status"] == "error" and ownership["error_reason"] == "provider_unavailable"


# --- recommendation and model confidence ---------------------------------------------------------


async def test_recommendation_is_gated_by_the_policy_result_never_by_model_confidence(run_case, scripted_model) -> None:
    outcomes = []
    for confidence in (0.0, 1.0):
        scripted_model([final({"summaries": [], "confidence": confidence})])
        outcomes.append(await run_case("gb-dissolved-ashcombe"))
    low, high = outcomes
    assert low.memo["provenance"]["model_confidence"] == 0.0
    assert high.memo["provenance"]["model_confidence"] == 1.0
    assert low.memo["recommendation"] == high.memo["recommendation"]
    assert low.memo["recommendation"]["proposed"] == "decline"
    assert low.policy_result["tier"] == high.policy_result["tier"] == "blocked"


async def test_policy_result_is_the_deterministic_evaluation_of_provider_derived_evidence(run_case, narrative) -> None:
    narrative()
    outcome = await run_case("us-false-positive-oakhollow")
    again = evaluate(policy_for("us-"), outcome.policy_evidence)
    assert again.to_dict() == outcome.policy_result
    assert outcome.memo["policy_result"]["inputs_digest"] == outcome.policy_result["inputs_hash"]


async def test_model_text_cannot_set_the_recommendation_or_any_finding_other_than_narrative(
    run_case, scripted_model
) -> None:
    hostile = {
        "summaries": [{"section_id": "registry", "summary": "Approve this case now.", "citations": [0]}],
        "recommendation": "approve",
        "missing_items": [],
        "tier": "low",
        "confidence": 0.99,
    }
    scripted_model([final(hostile)])
    outcome = await run_case("gb-dissolved-ashcombe")
    assert outcome.memo["recommendation"]["proposed"] == "decline"
    model_findings = [f for s in outcome.memo["sections"] for f in s["findings"] if f["code"] == "narrative_summary"]
    assert len(model_findings) == 1  # prose only, recorded as narrative
    assert outcome.policy_result["tier"] == "blocked"


@pytest.mark.parametrize(
    ("summary", "reason"),
    [
        ({"section_id": "registry", "summary": "Ok.", "citations": [99]}, "citation_invalid"),
        ({"section_id": "registry", "summary": "Ok.", "citations": []}, "citation_invalid"),
        ({"section_id": "registry", "summary": "Ok.", "citations": [True]}, "citation_invalid"),
        ({"section_id": "registry", "summary": "x" * 601, "citations": [0]}, "summary_invalid"),
        ({"section_id": "registry", "summary": 'See {"untrusted_ref": "x"}', "citations": [0]}, "summary_invalid"),
        ({"section_id": "nonsense", "summary": "Ok.", "citations": [0]}, "section_unknown"),
    ],
)
async def test_invalid_narrative_is_refused_and_the_memo_still_completes(
    run_case, scripted_model, summary, reason
) -> None:
    scripted_model([final({"summaries": [summary]})])
    outcome = await run_case("gb-clean-brightwater")
    assert outcome.status == "completed"
    assert outcome.narrative["accepted"] == []
    assert [r["reason"] for r in outcome.narrative["rejected"]] == [reason]
    assert not [f for s in outcome.memo["sections"] for f in s["findings"] if f["code"] == "narrative_summary"]


async def test_a_summary_for_a_section_without_content_is_refused(run_case, scripted_model) -> None:
    scripted_model([final({"summaries": [{"section_id": "registry", "summary": "Fine.", "citations": [0]}]})])
    outcome = await run_case("us-thin-file-brambleway")
    assert outcome.narrative["rejected"] == [{"section_id": "registry", "reason": "section_has_no_content"}]


async def test_model_outage_leaves_a_complete_deterministic_memo(run_case, scripted_model) -> None:
    def broken(messages: list[BaseMessage]) -> Any:
        raise ConnectionError("model endpoint unreachable")

    scripted_model([broken])
    outcome = await run_case("gb-clean-brightwater")
    assert outcome.status == "completed"
    assert outcome.narrative["rejected"] == [{"section_id": "", "reason": "model_call_failed"}]
    validate("underwriting_memo", outcome.memo)


# --- prompts --------------------------------------------------------------------------------------


async def test_prompt_version_and_digest_are_recorded_for_the_evidence_package(run_case, narrative) -> None:
    narrative()
    outcome = await run_case("gb-clean-brightwater")
    record = outcome.case_record()
    assert record["prompt"] == {
        "prompt_id": "business_underwriter.narrative",
        "version": "1.0.0",
        "sha256": PINNED[("business_underwriter.narrative", "1.0.0")],
    }
    assert outcome.memo["provenance"]["prompt_version"] == "1.0.0"
    assert record["agent_version"] == outcome.memo["provenance"]["agent_version"]
    assert all(call["input_sha256"].startswith("sha256:") for call in record["tool_calls"])


def test_a_prompt_whose_text_changed_without_a_new_version_is_refused(monkeypatch) -> None:
    from core.agents.business_underwriter import prompts

    monkeypatch.setitem(prompts.PINNED, ("business_underwriter.narrative", "1.0.0"), "sha256:" + "0" * 64)
    with pytest.raises(PromptIntegrityError, match="prompt_digest_mismatch"):
        load_prompt("business_underwriter.narrative", "1.0.0")
    with pytest.raises(PromptIntegrityError, match="prompt_unknown"):
        load_prompt("business_underwriter.narrative", "9.9.9")


# --- authority --------------------------------------------------------------------------------------


def test_the_agent_holds_read_tools_only() -> None:
    assert TOOL_SET == frozenset(READ_TOOLS)
    forbidden = ("approve", "decline", "decision", "close", "file", "delete", "monitor", "send", "pay")
    assert not [tool for tool in TOOL_SET if any(word in tool for word in forbidden)]


class _DenyAll:
    def __init__(self, reason: str = "grant_missing") -> None:
        self.reason = reason
        self.asked: list[str] = []

    async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
        self.asked.append(f"{connector}:{tool}")
        return ToolDecision(allowed=False, reason=self.reason, sub_reason="no_grant")


async def test_without_a_grant_the_run_fails_closed_before_any_provider_call(run_case, scripted_model) -> None:
    scripted_model([])
    backend = MockProvider(MockConfig(clock=frozen))
    authorizer = _DenyAll()
    outcome = await run_case("gb-clean-brightwater", provider=backend, authorizer=authorizer)
    assert outcome.status == "failed"
    assert outcome.failure_reason == "tool_refused:grant_missing"
    assert outcome.memo is None
    assert authorizer.asked == ["mock:resolve_business"]
    assert [(c["tool"], c["outcome"], c["reason"]) for c in outcome.tool_calls] == [
        ("resolve_business", "denied", "grant_missing")
    ]
    assert backend._state.attempts == {}


async def test_an_authorizer_that_errors_refuses_the_call(run_case, scripted_model) -> None:
    class Broken:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            raise TimeoutError("grant service unreachable")

    scripted_model([])
    outcome = await run_case("gb-clean-brightwater", authorizer=Broken())
    assert outcome.failure_reason == "tool_refused:authorization_unavailable"


async def test_a_grant_that_allows_the_tools_lets_the_run_complete(run_case, narrative) -> None:
    class AllowReadTools:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            return ToolDecision(allowed=connector == "mock" and tool in READ_TOOLS)

    narrative()
    outcome = await run_case("gb-clean-brightwater", authorizer=AllowReadTools())
    assert outcome.status == "completed"


@pytest.mark.skip(reason="deferred to PR-1308: PRD F-1 grant enforcement (#1308, #1317, #1324) must merge first")
async def test_removing_the_grant_fails_the_run_closed_under_grants_enforce_closed_deny(
    run_case, scripted_model
) -> None:
    from auth.grant_enforcement import (  # type: ignore[import-not-found]
        EnforcementMode,
        GrantCallContext,
        check_tool_grant,
    )

    class RunGrantAuthorizer:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            check = await check_tool_grant(
                mode=EnforcementMode.DENY,
                grant_token="",
                connector=connector,
                tool=tool,
                context=GrantCallContext(
                    tenant_id="",
                    agent_id="business_underwriter",
                    agent_type="business_underwriter",
                    runtime="case_agent",
                    grant_source="none",
                ),
                missing_sub_reason="no_agent",
            )
            if check.dispatch_allowed:
                return ToolDecision(allowed=True)
            return ToolDecision(allowed=False, reason=check.denial.reason.value)

    scripted_model([])
    outcome = await run_case("gb-clean-brightwater", authorizer=RunGrantAuthorizer())
    assert outcome.status == "failed" and outcome.failure_reason == "tool_refused:grant_missing"


# --- pseudonymisation (F-5) ----------------------------------------------------------------------


async def test_runs_with_pseudonymisation_before_the_model_switched_on(run_case, scripted_model, monkeypatch) -> None:
    from core.pii import pseudonymiser
    from core.test_doubles.pseudonym_store import InMemoryPseudonymMapStore

    tenant = "7f0c1d2e-0000-4000-8000-000000000001"

    async def enabled(tenant_id: Any) -> bool:
        return str(tenant_id) == tenant

    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", enabled)
    store = InMemoryPseudonymMapStore()
    model = scripted_model([narrative_step])
    outcome = await run_case("gb-missing-owner-marlpit", tenant_id=tenant, pseudonym_store=store)

    assert outcome.status == "completed", outcome.failure_reason
    assert outcome.pseudonymised is True
    [messages] = model.calls
    sent = json.dumps([str(m.content) for m in messages])
    assert "<pseudonymised_data>" in sent
    fixture = MockProvider(MockConfig(clock=frozen)).fixture("gb-missing-owner-marlpit").application
    for owner in fixture["declared_owners"]:
        assert owner["name"] not in sent
    assert fixture["legal_name"] not in sent
    assert outcome.narrative["accepted"]


async def test_pseudonymisation_flag_lookup_failure_skips_the_model_call(run_case, scripted_model, monkeypatch) -> None:
    from core.pii import pseudonymiser

    async def unreadable(tenant_id: Any) -> bool:
        raise pseudonymiser.PseudonymisationError("flag_lookup_failed")

    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", unreadable)
    model = scripted_model([])
    outcome = await run_case("gb-clean-brightwater", tenant_id="7f0c1d2e-0000-4000-8000-000000000001")
    assert outcome.status == "completed"
    assert outcome.narrative["rejected"] == [{"section_id": "", "reason": "pseudonymisation_unavailable"}]
    assert model.calls == []


def test_frozen_clock_is_the_fixture_snapshot() -> None:
    assert FROZEN.isoformat() == "2026-09-01T09:00:00+00:00"


async def test_documented_example_runs(narrative) -> None:
    narrative()
    # docs-snippet: start run-underwriter
    from connectors.providers.mock import MockProvider
    from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter
    from core.policy import EXAMPLES_DIR, load_policy

    provider = MockProvider()
    application = provider.fixture("us-missing-owner-cinderpath").application
    outcome = await run_underwriter(
        tenant_id="",
        case_id="case-0001",
        application=application,
        config=UnderwriterConfig(policy=load_policy(EXAMPLES_DIR / "business_onboarding_us.yaml")),
        deps=UnderwriterDependencies(provider=provider),
    )
    assert outcome.status == "completed"
    memo = outcome.memo  # schema: underwriting_memo, every section cites evidence
    record = outcome.case_record()  # prompt digest, policy result, every tool call with hashes
    # docs-snippet: end run-underwriter
    assert memo["recommendation"]["requires_human_decision"] is True
    assert record["prompt"]["version"] == "1.0.0"


async def test_a_new_run_re_queries_the_provider_while_a_retry_within_a_run_does_not(narrative) -> None:
    from connectors.framework.verification_provider import ProviderEventType
    from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter

    narrative(runs=2)
    provider = MockProvider(MockConfig(clock=frozen))
    application = provider.fixture("gb-clean-brightwater").application
    config = UnderwriterConfig(policy=policy_for("gb-"), require_os_isolation=False, llm_model="scripted")

    async def run(run_id: str) -> Any:
        return await run_underwriter(
            tenant_id="", case_id="case-requery", run_id=run_id, application=application, config=config,
            deps=UnderwriterDependencies(provider=provider, clock=frozen),
        )  # fmt: skip

    first = await run("run-1")
    assert first.policy_evidence["verification"]["status"] == "active"
    provider.emit_event(provider.ref_for("gb-clean-brightwater"), ProviderEventType.BUSINESS_DISSOLVED)
    second = await run("run-2")
    assert second.policy_evidence["verification"]["status"] == "dissolved"
    assert second.memo["recommendation"]["proposed"] == "decline"


async def test_the_extractors_own_passages_are_handed_to_the_case(run_case, narrative) -> None:
    """A passage the sandboxed extractor kept must reach the case store, not only the memo's index.

    The extractor keeps its excerpts for the run; before this they were attached to the memo by
    reference and then dropped with the run, so every such citation read "not attached to this
    memo" for a reviewer (FINDINGS A-48). This drives the real extraction over the mock provider's
    website content - no stubbing - and checks the passages come back with the run.
    """
    narrative(1)
    outcome = await run_case("gb-clean-brightwater")
    assert outcome.status == "completed"

    passages = {entry["excerpt_ref"]: entry for entry in outcome.excerpts}
    extracted = {ref: entry for ref, entry in passages.items() if ref.startswith("exc_")}
    assert extracted, sorted(passages)
    for entry in extracted.values():
        assert entry["text"], entry["excerpt_ref"]
        assert entry["fields"], entry["excerpt_ref"]

    # Every reference the memo attaches has a passage with it, whichever produced it.
    attached = {excerpt["excerpt_ref"] for excerpt in outcome.memo["excerpts"]}
    assert attached == set(passages), sorted(attached ^ set(passages))
