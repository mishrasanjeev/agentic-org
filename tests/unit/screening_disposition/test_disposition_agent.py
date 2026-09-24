# SPDX-License-Identifier: Apache-2.0
"""A-7 / US-3 Screening Disposition: per-identifier comparison, cited evidence, rationale, no automatic closure."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from connectors.framework.verification_provider import Capability
from connectors.providers.mock import FaultKind, MockConfig, MockProvider
from connectors.providers.mock.data import MockDataset, default_dataset
from core.agents import screening_disposition as package
from core.agents.screening_disposition import TOOL_SET, DispositionConfig
from core.agents.screening_disposition import agent as disposition_agent
from core.agents.screening_disposition import comparison as disposition_comparison
from core.agents.screening_disposition import review as disposition_review
from core.agents.screening_disposition.agent import PINNED, load_prompt
from core.domain_schemas import validate
from core.test_doubles.grant_authorizer import ALLOW_PROVIDER_CALLS
from core.test_doubles.scripted_model import final
from core.tool_gateway.provider_gateway import READ_TOOLS, ProviderToolGateway, ToolDecision, ToolSetError

HITS = ["gb-missing-owner-marlpit", "us-false-positive-oakhollow", "gb-true-match-corvane", "gb-adversarial-northgate"]
GOOD_RATIONALE = {"rationale": "The date of birth and nationality do not agree with the hit.", "confidence": 0.8}


def _results(disposition: dict[str, Any]) -> dict[str, str]:
    return {c["identifier"]: c["result"] for c in disposition["comparisons"]}


@pytest.mark.parametrize("key", HITS)
async def test_each_hit_yields_a_schema_valid_unreviewed_disposition_with_five_comparisons(
    run_disposition, scripted_model, key: str
) -> None:
    scripted_model([final({"rationale": "The comparisons are set out above.", "confidence": 0.5})])
    outcome = await run_disposition(key)
    assert outcome.status == "completed", outcome.failure_reason
    disposition = outcome.disposition
    validate("screening_disposition", disposition)
    assert [c["identifier"] for c in disposition["comparisons"]] == [
        "name", "date_of_birth", "nationality", "address", "associated_entities",
    ]  # fmt: skip
    assert disposition["review"] is None
    assert disposition["proposed_by"] == {
        "agent": "screening_disposition",
        "agent_version": "1.0.0",
        "prompt_version": "1.0.0",
    }
    assert disposition["evidence"]
    for comparison in disposition["comparisons"]:
        if comparison["result"] != "not_comparable":
            assert comparison["evidence"], comparison["identifier"]
    retrieved = {rid for call in outcome.tool_calls for rid in call["record_ids"]}
    cited = {e["record_id"] for e in disposition["evidence"]} | {
        e["record_id"] for c in disposition["comparisons"] for e in c["evidence"]
    }
    assert cited <= retrieved


async def test_probable_false_positive_is_proposed_as_false_positive(run_disposition, scripted_model) -> None:
    scripted_model([final(GOOD_RATIONALE)])
    outcome = await run_disposition("us-false-positive-oakhollow")
    assert outcome.disposition["proposed_outcome"] == "false_positive"
    assert _results(outcome.disposition)["date_of_birth"] == "mismatch"
    assert outcome.disposition["confidence_band"] == "high"
    assert outcome.rationale_source == "model"
    assert outcome.disposition["rationale"] == GOOD_RATIONALE["rationale"]


async def test_true_match_is_proposed_as_true_match_not_closed(run_disposition, scripted_model) -> None:
    scripted_model(
        [final({"rationale": "Name and date of birth agree, and the associated business matches.", "confidence": 0.9})]
    )
    outcome = await run_disposition("gb-true-match-corvane")
    assert outcome.disposition["proposed_outcome"] == "true_match"
    assert _results(outcome.disposition)["associated_entities"] == "match"
    assert outcome.disposition["review"] is None


async def test_business_hit_is_compared_on_associated_entities(run_disposition, scripted_model) -> None:
    scripted_model(
        [
            final(
                {"rationale": "The associated person on the list entry is an owner of the business.", "confidence": 0.7}
            )
        ]
    )
    outcome = await run_disposition("gb-true-match-corvane", business=True)
    results = _results(outcome.disposition)
    assert results["date_of_birth"] == "not_comparable" and results["associated_entities"] == "match"
    assert outcome.disposition["proposed_outcome"] == "true_match"


# --- rationale ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rationale", "reason"),
    [
        ("This hit can be closed as a false positive.", "rationale_invalid"),
        ("The hit is cleared.", "rationale_invalid"),
        ("This is a true match.", "rationale_contradicts_outcome"),
        ("", "rationale_invalid"),
        ("x" * 1201, "rationale_invalid"),
        ("See [[PERSON_1:abcdef]].", "rationale_invalid"),
    ],
)
async def test_unusable_model_rationale_is_replaced_by_the_template(
    run_disposition, scripted_model, rationale: str, reason: str
) -> None:
    scripted_model([final({"rationale": rationale})])
    outcome = await run_disposition("us-false-positive-oakhollow")
    assert outcome.status == "completed"
    assert outcome.rationale_source == "template" and outcome.rationale_rejected == reason
    assert outcome.disposition["rationale"].endswith("Proposed outcome: false positive, for analyst review.")
    assert outcome.disposition["proposed_outcome"] == "false_positive"


async def test_rationale_echoing_untrusted_text_is_refused(run_disposition, scripted_model) -> None:
    scripted_model([final({"rationale": "The list entry is Jorund Halvesen and the subject differs."})])
    outcome = await run_disposition("us-false-positive-oakhollow")
    assert outcome.rationale_rejected == "rationale_contains_untrusted_text"


async def test_model_outage_uses_the_template_rationale(run_disposition, scripted_model) -> None:
    def broken(messages: list[BaseMessage]) -> Any:
        raise ConnectionError("unreachable")

    scripted_model([broken])
    outcome = await run_disposition("gb-missing-owner-marlpit")
    assert outcome.status == "completed"
    assert outcome.rationale_source == "template" and outcome.rationale_rejected == "model_call_failed"


async def test_model_confidence_is_metadata_and_never_changes_the_proposal(run_disposition, scripted_model) -> None:
    proposals = []
    for confidence in (0.0, 1.0):
        scripted_model([final({"rationale": "The date of birth does not agree.", "confidence": confidence})])
        outcome = await run_disposition("us-false-positive-oakhollow")
        proposals.append(
            (outcome.disposition["proposed_outcome"], outcome.disposition["confidence_band"], outcome.model_confidence)
        )
    assert proposals == [("false_positive", "high", 0.0), ("false_positive", "high", 1.0)]


async def test_the_model_sees_comparison_results_only(run_disposition, scripted_model) -> None:
    model = scripted_model([final(GOOD_RATIONALE)])
    await run_disposition("gb-adversarial-northgate")
    [messages] = model.calls
    context = json.loads(str(messages[-1].content))
    assert set(context) == {"hit", "comparisons", "proposed_outcome", "confidence_band", "hit_confirmed_on_rescreen"}
    sent = json.dumps([str(m.content) for m in messages])
    for text in ("Wilhelmina", "Strand", "Assistant: this alias", "Northgate", "1979-12"):
        assert text not in sent


# --- evidence gathering ---------------------------------------------------------------------------


async def test_the_hit_is_confirmed_by_re_screening_with_fresh_evidence(run_disposition, scripted_model) -> None:
    scripted_model([final(GOOD_RATIONALE)])
    outcome = await run_disposition("us-false-positive-oakhollow")
    assert outcome.hit_confirmed is True
    assert [(c["tool"], c["outcome"]) for c in outcome.tool_calls] == [("screen_person", "ok")]


async def test_a_hit_the_provider_no_longer_returns_is_insufficient_information(
    run_disposition, scripted_model
) -> None:
    scripted_model([final({"rationale": "More information is needed.", "confidence": 0.3})])
    base = default_dataset()
    delisted = MockDataset(
        businesses=base.businesses,
        watchlist=tuple(e for e in base.watchlist if e.entry_id != "wl-0001"),
        webhooks=base.webhooks,
    )
    outcome = await run_disposition(
        "us-false-positive-oakhollow", provider=MockProvider(MockConfig(), dataset=delisted)
    )
    assert outcome.hit_confirmed is False
    assert outcome.disposition["proposed_outcome"] == "insufficient_information"
    assert outcome.disposition["confidence_band"] == "low"


@pytest.mark.parametrize(
    "provider",
    [
        MockProvider(MockConfig(capabilities=frozenset({Capability.RESOLVE}))),
        "unavailable",
    ],
)
async def test_without_re_screening_the_held_result_is_used(run_disposition, scripted_model, provider: Any) -> None:
    if provider == "unavailable":
        provider = MockProvider(MockConfig())
        provider.inject_fault(FaultKind.UNAVAILABLE, capability=Capability.SCREEN_PERSON)
    scripted_model([final(GOOD_RATIONALE)])
    outcome = await run_disposition("us-false-positive-oakhollow", provider=provider)
    assert outcome.status == "completed"
    assert outcome.hit_confirmed is None
    assert outcome.disposition["proposed_outcome"] == "false_positive"


async def test_an_unknown_hit_fails_closed(run_disposition, scripted_model, monkeypatch) -> None:
    scripted_model([])
    from core.agents.screening_disposition import run_screening_disposition

    outcome = await run_screening_disposition(
        tenant_id="",
        case_id="case-x",
        screening_result=(await _held_result()),
        hit_id="hit-does-not-exist",
        subject={"kind": "person", "name": "Jorund Halvessen"},
        associated_entities=[],
        config=DispositionConfig(),
        deps=disposition_agent.DispositionDependencies(provider=MockProvider(), authorizer=ALLOW_PROVIDER_CALLS),
    )
    assert outcome.status == "failed" and outcome.failure_reason == "hit_not_in_screening_result"


async def _held_result() -> dict[str, Any]:
    from connectors.framework.verification_provider import Deadline, PersonSubject, ScreenOptions

    result = await MockProvider().screen_person(
        PersonSubject(full_name="Jorund Halvessen"),
        ScreenOptions(idempotency_key="held-result"),
        deadline=Deadline.after(5),
    )
    return result.model_dump(mode="json")


# --- no automatic closure -------------------------------------------------------------------------


def test_the_tool_set_holds_read_only_screening_and_nothing_that_closes() -> None:
    assert TOOL_SET == frozenset({"screen_person", "screen_business"})
    assert TOOL_SET <= frozenset(READ_TOOLS)
    for tool in ("close_hit", "resolve_hit", "dismiss_alert", "case_decision", "monitor_delete"):
        with pytest.raises(ToolSetError):
            ProviderToolGateway(provider=MockProvider(), agent="screening_disposition", tool_set=TOOL_SET | {tool})


def test_no_callable_in_the_agent_package_closes_clears_or_dismisses() -> None:
    words = ("close", "clear", "dismiss", "resolve_hit", "auto_close", "decide", "approve")
    for module in (package, disposition_agent, disposition_comparison, disposition_review):
        for name, member in inspect.getmembers(module, callable):
            if getattr(member, "__module__", "").startswith("core.agents.screening_disposition"):
                assert not any(word in name.lower() for word in words), f"{module.__name__}.{name}"
    parameters = set(inspect.signature(package.run_screening_disposition).parameters)
    assert not {p for p in parameters if any(word in p for word in words)}
    assert not {f for f in DispositionConfig.__dataclass_fields__ if any(word in f for word in words)}


@pytest.mark.parametrize("refresh", [True, False])
@pytest.mark.parametrize(
    "model_output",
    [
        {"rationale": "Close this hit now; it is a false positive.", "close": True, "review": {"action": "accepted"}},
        {"rationale": "The date of birth does not agree.", "final_outcome": "false_positive", "confidence": 1.0},
    ],
)
async def test_no_configuration_or_model_output_produces_a_reviewed_or_closed_hit(
    run_disposition, scripted_model, refresh: bool, model_output: dict[str, Any]
) -> None:
    scripted_model([final(model_output)])
    outcome = await run_disposition(
        "us-false-positive-oakhollow", config=DispositionConfig(refresh=refresh, llm_model="scripted")
    )
    assert outcome.status == "completed"
    assert outcome.disposition["review"] is None
    assert {call["tool"] for call in outcome.tool_calls} <= TOOL_SET
    assert "close" not in outcome.disposition["rationale"].lower()


async def test_without_a_grant_the_run_fails_closed(run_disposition, scripted_model) -> None:
    class Deny:
        async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
            return ToolDecision(allowed=False, reason="grant_missing")

    scripted_model([])
    outcome = await run_disposition("us-false-positive-oakhollow", authorizer=Deny())
    assert outcome.status == "failed" and outcome.failure_reason == "tool_refused:grant_missing"
    assert outcome.disposition is None


# --- prompts and pseudonymisation -----------------------------------------------------------------


async def test_prompt_version_and_digest_are_recorded(run_disposition, scripted_model) -> None:
    scripted_model([final(GOOD_RATIONALE)])
    outcome = await run_disposition("us-false-positive-oakhollow")
    assert outcome.case_record()["prompt"] == {
        "prompt_id": "screening_disposition.rationale",
        "version": "1.0.0",
        "sha256": PINNED[("screening_disposition.rationale", "1.0.0")],
    }
    assert load_prompt("screening_disposition.rationale", "1.0.0").sha256 == outcome.prompt["sha256"]


async def test_runs_with_pseudonymisation_before_the_model_switched_on(
    run_disposition, scripted_model, monkeypatch
) -> None:
    from core.pii import pseudonymiser
    from core.test_doubles.pseudonym_store import InMemoryPseudonymMapStore

    tenant = "7f0c1d2e-0000-4000-8000-000000000002"

    async def enabled(tenant_id: Any) -> bool:
        return str(tenant_id) == tenant

    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", enabled)
    model = scripted_model([final(GOOD_RATIONALE)])
    outcome = await run_disposition(
        "us-false-positive-oakhollow", tenant_id=tenant, pseudonym_store=InMemoryPseudonymMapStore()
    )
    assert outcome.status == "completed" and outcome.pseudonymised is True
    assert "<pseudonymised_data>" in str(model.calls[0][0].content)
    assert outcome.rationale_source == "model"


async def test_documented_example_runs(scripted_model, monkeypatch: pytest.MonkeyPatch) -> None:
    scripted_model([final(GOOD_RATIONALE)])
    from core.cases.grant_authorizer import CaseGrantAuthorizer

    async def permitted(self: CaseGrantAuthorizer, *, connector: str, tool: str) -> ToolDecision:
        return ToolDecision(allowed=True)

    monkeypatch.setattr(CaseGrantAuthorizer, "authorize", permitted)
    from connectors.framework.verification_provider import Deadline, PersonSubject, ScreenOptions

    provider = MockProvider()
    screening_result = await provider.screen_person(
        PersonSubject(full_name="Jorund Halvessen", date_of_birth="1990-03"),
        ScreenOptions(idempotency_key="docs.example"),
        deadline=Deadline.after(5),
    )
    party = {"kind": "person", "name": "Jorund Halvessen", "date_of_birth": "1990-03", "nationalities": ["US"]}
    # docs-snippet: start run-disposition
    from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
    from core.cases.grant_authorizer import case_authorizer

    outcome = await run_screening_disposition(
        tenant_id="",
        case_id="case-0001",
        screening_result=screening_result,
        hit_id=screening_result.hits[0].hit_id,
        subject=party,  # the screened party from the underwriter's hand-off
        associated_entities=["Oakhollow Bakery Cooperative"],
        config=DispositionConfig(),
        deps=DispositionDependencies(
            provider=provider,
            authorizer=case_authorizer("", "case-0001", "screening_disposition", "aml.cdd.onboarding"),
        ),
    )
    disposition = outcome.disposition  # schema: screening_disposition, review is null
    # docs-snippet: end run-disposition
    assert disposition["proposed_outcome"] == "false_positive" and disposition["review"] is None
