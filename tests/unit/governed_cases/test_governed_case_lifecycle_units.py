# SPDX-License-Identifier: Apache-2.0
"""Governed case lifecycle rules, decision refusal and workflow step refusals without a database."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from core.cases import TERMINAL, TRANSITIONS, CaseError, CaseState, check_transition
from core.cases import decisions as case_decisions
from core.cases.decisions import DecisionCheck, RequireDecisionGrant, record_decision, semantic_action
from core.cases.runtime import CaseRuntime, default_policy_id, load_case_policies, run_case_step
from core.policy import PolicyLoadError

ROOT = Path(__file__).resolve().parents[3]
CASE_REF = "case_" + "0" * 24


def test_decided_and_withdrawn_are_the_only_terminal_states() -> None:
    assert TERMINAL == {CaseState.DECIDED, CaseState.WITHDRAWN}


def test_only_awaiting_decision_can_become_decided() -> None:
    assert [s for s, targets in TRANSITIONS.items() if CaseState.DECIDED in targets] == [CaseState.AWAITING_DECISION]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("submitted", CaseState.DECIDED),
        ("in_progress", CaseState.DECIDED),
        ("decided", CaseState.IN_PROGRESS),
        ("withdrawn", CaseState.SUBMITTED),
    ],
)
def test_illegal_transitions_are_refused(current: str, target: CaseState) -> None:
    with pytest.raises(CaseError) as refused:
        check_transition(current, target)
    assert refused.value.reason == "transition_not_allowed" and refused.value.status == 409


def test_an_unknown_stored_state_is_refused() -> None:
    with pytest.raises(CaseError, match="case_state_unknown"):
        check_transition("approved", CaseState.DECIDED)


def test_every_state_is_reachable_from_submitted() -> None:
    seen, frontier = {CaseState.SUBMITTED}, [CaseState.SUBMITTED]
    while frontier:
        for target in TRANSITIONS[frontier.pop()]:
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(CaseState)


# --- decisions ------------------------------------------------------------------------------------


class _Case:
    def __init__(self, state: str = "awaiting_decision") -> None:
        self.state = state
        self.case_ref = CASE_REF
        self.tenant_id = uuid.uuid4()
        self.subject = {"provider": "mock", "provider_ref": "mock-gb-00000001", "jurisdiction": "GB"}
        self.decision = None
        self.purpose = "aml.cdd.onboarding"
        self.application = {"legal_name": "Example Ltd", "jurisdiction": "GB", "identifiers": [], "declared_owners": []}
        self.created_at = self.updated_at = None
        self.version = 3


async def _decide(case: Any, outcome: str, verifier: Any, grants: list[str] | None = None) -> Any:
    return await record_decision(None, case, outcome=outcome, grants=grants or ["g"], verifier=verifier, actor="user:a")  # type: ignore[arg-type]


async def test_the_default_verifier_refuses_every_decision() -> None:
    with pytest.raises(CaseError) as refused:
        await _decide(_Case(), "approve", RequireDecisionGrant())
    assert refused.value.reason == "decision_required" and refused.value.status == 403


@pytest.mark.parametrize(
    ("verifier_result", "reason"),
    [
        (DecisionCheck(allowed=False, reason="decision_invalid"), "decision_invalid"),
        (DecisionCheck(allowed=True), "decision_required"),
        (True, "decision_required"),
    ],
)
async def test_a_verifier_must_positively_confirm_named_approvers(verifier_result: Any, reason: str) -> None:
    class Verifier:
        async def verify(self, **kwargs: Any) -> Any:
            return verifier_result

    with pytest.raises(CaseError) as refused:
        await _decide(_Case(), "decline", Verifier())
    assert refused.value.reason == reason


async def test_a_verifier_that_errors_fails_closed() -> None:
    class Broken:
        async def verify(self, **kwargs: Any) -> Any:
            raise TimeoutError("grant service unreachable")

    with pytest.raises(CaseError) as refused:
        await _decide(_Case(), "approve", Broken())
    assert refused.value.reason == "decision_invalid"


async def test_decisions_need_a_known_outcome_and_a_case_awaiting_decision() -> None:
    with pytest.raises(CaseError, match="decision_outcome_invalid"):
        await _decide(_Case(), "close", RequireDecisionGrant())
    with pytest.raises(CaseError, match="transition_not_allowed"):
        await _decide(_Case("in_progress"), "approve", RequireDecisionGrant())


async def test_a_confirmed_decision_is_validated_then_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    moved: list[Any] = []

    async def fake_transition(session: Any, case: Any, target: CaseState, **kwargs: Any) -> Any:
        moved.append(target)
        case.state = target.value
        return case

    monkeypatch.setattr(case_decisions, "transition", fake_transition)

    class Allow:
        async def verify(self, **kwargs: Any) -> DecisionCheck:
            return DecisionCheck(allowed=True, approvers=(("user:approver-a", "grant-1"),))

    case = _Case()
    await _decide(case, "approve", Allow(), ["grant-1"])
    assert moved == [CaseState.DECIDED]
    assert case.decision["approvers"] == [{"approver": "user:approver-a", "decision_grant_id": "grant-1"}]


def test_semantic_action_names_case_action_decision_and_subject() -> None:
    assert semantic_action(_Case(), "decline") == {  # type: ignore[arg-type]
        "case_id": CASE_REF,
        "action": "case_decision",
        "decision": "decline",
        "subject": "mock:mock-gb-00000001",
    }


# --- workflow step refusals -----------------------------------------------------------------------


def _runtime(enabled: bool = True) -> CaseRuntime:
    async def flag(tenant_id: uuid.UUID) -> bool:
        return enabled

    return CaseRuntime(flag=flag, policies=lambda: {}, llm_model="scripted")


@pytest.mark.parametrize(
    ("step", "payload", "reason"),
    [
        ({"id": "s", "action": "approve_case"}, {"case_ref": CASE_REF}, "case_action_unknown"),
        ({"id": "s", "action": "investigate"}, {"case_ref": "../other"}, "case_ref_invalid"),
        ({"id": "s", "action": "investigate"}, {}, "case_ref_invalid"),
        (
            {"id": "s", "action": "record_decision", "decision_step": "h"},
            {"case_ref": CASE_REF},
            "decision_not_recorded",
        ),
    ],
)
async def test_case_agent_step_refusals(step: dict[str, Any], payload: dict[str, Any], reason: str) -> None:
    state = {"tenant_id": str(uuid.uuid4()), "trigger_payload": payload, "step_results": {}}
    result = await run_case_step(step, state, runtime=_runtime())
    assert result["status"] == "failed" and result["error"] == reason and result["type"] == "case_agent"


async def test_case_agent_step_is_refused_while_the_flag_is_off() -> None:
    state = {"tenant_id": str(uuid.uuid4()), "trigger_payload": {"case_ref": CASE_REF}}
    step = {"id": "s", "action": "investigate", "case_ref": "$case_ref"}
    result = await run_case_step(step, state, runtime=_runtime(enabled=False))
    assert result["status"] == "failed" and result["error"] == "governed_cases_disabled"


async def test_the_workflow_engine_dispatches_case_agent_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    from workflows import step_types

    seen: list[str] = []

    async def fake(step: dict[str, Any], state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.append(step["action"])
        return {"step_id": step["id"], "type": "case_agent", "status": "completed", "output": {}}

    monkeypatch.setattr("core.cases.runtime.run_case_step", fake)
    result = await step_types.execute_step({"id": "s", "type": "case_agent", "action": "investigate"}, {})
    assert result["status"] == "completed" and seen == ["investigate"]


# --- configuration ---------------------------------------------------------------------------------


def test_default_policy_by_jurisdiction() -> None:
    assert default_policy_id("US-DE") == "business_onboarding_us"
    assert default_policy_id("GB") == "business_onboarding_uk"
    with pytest.raises(CaseError, match="policy_not_configured"):
        default_policy_id("FR")


def test_strict_runtimes_refuse_the_example_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config

    monkeypatch.setattr(config.settings, "env", "production")
    with pytest.raises(PolicyLoadError):
        load_case_policies()
    monkeypatch.setattr(config.settings, "env", "test")
    assert set(load_case_policies()) == {"business_onboarding_uk", "business_onboarding_us"}


def test_the_migration_is_the_single_head_with_a_short_revision_id() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == ["v6z25_governed_cases"]
    revision = script.get_revision("v6z25_governed_cases")
    assert revision.down_revision == "v6z24_case_pseudonym_maps" and len(revision.revision) <= 32
