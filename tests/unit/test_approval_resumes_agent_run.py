# SPDX-License-Identifier: Apache-2.0
"""Approving a standalone run resumes it from its checkpoint (PRD F-2, flag approvals.resume_agent_runs)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from core.approvals import agent_run_resume as ar
from core.langgraph.checkpointer import CheckpointerUnavailableError
from core.langgraph.thread_ids import new_thread_id
from core.models.audit import AuditLog
from tests.regression.test_ownership_approvals_20260914 import (
    TENANT,
    _admin,
    _agent,
    _hitl,
    _QueueSession,
    _session_patch,
)

TENANT_UUID = uuid.UUID(TENANT)
OTHER_TENANT = uuid.UUID("0000000b-0000-4000-8000-00000000000b")
SPEC = {
    "confidence_floor": 0.5,
    "hitl_condition": "total > 500000",
    "authorized_tools": ["erp:get_invoice"],
    "connector_names": ["erp"],
    "llm_model": "scripted",
    "llm_provider": None,
    "company_id": None,
    "domain": "finance",
}


def _paused_item(agent: Any, **extra: Any) -> Any:
    fields = {
        "status": "decided",
        "decision": "approve",
        "checkpoint_thread_id": new_thread_id(TENANT_UUID),
        "context": {"output": {"total": 750000}, ar.RESUME_SPEC_KEY: dict(SPEC)},
    }
    fields.update(extra)
    return _hitl(agent, **fields)


class _Sessions:
    """Every get_tenant_session call gets a fresh queue session over the same rows."""

    def __init__(self, item: Any, agent: Any, *, tenant: uuid.UUID = TENANT_UUID) -> None:
        self.item, self.agent, self.tenant = item, agent, tenant
        self.opened: list[uuid.UUID] = []
        self.sessions: list[_QueueSession] = []

    def factory(self):
        outer = self

        @asynccontextmanager
        async def _open(tenant_id: uuid.UUID, *_a: Any, **_k: Any):
            outer.opened.append(tenant_id)
            # RLS double: another tenant's session never sees the row.
            visible = outer.item if tenant_id == outer.tenant else None
            session = _QueueSession(visible, outer.agent)
            outer.sessions.append(session)
            yield session

        return _open

    def audits(self) -> list[AuditLog]:
        return [row for s in self.sessions for row in s.added if isinstance(row, AuditLog)]


async def _resume(
    item: Any, agent: Any, *, tenant: uuid.UUID = TENANT_UUID, result: dict | Exception | None = None
) -> tuple[dict, AsyncMock, AsyncMock, _Sessions]:
    sessions = _Sessions(item, agent)
    runner = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    deleter = AsyncMock(return_value=True)
    with (
        patch.object(ar, "get_tenant_session", sessions.factory()),
        patch("core.langgraph.runner.resume_agent", runner),
        patch.object(ar, "_delete_thread", deleter),
    ):
        outcome = await ar.resume_approved_agent_run(tenant, item.id)
    return outcome, runner, deleter, sessions


# ── Decision mapping and scheduling ────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "decision", "expected"),
    [
        ("decided", "approve", {"action": "approve"}),
        ("decided", "Approved", {"action": "approve"}),
        ("decided", "reject", {"action": "reject", "reason": "over limit"}),
        ("rejected", "approve", {"action": "reject", "reason": "over limit"}),
        ("decided", "override", None),
        ("decided", "defer", None),
        ("pending", "approve", None),
        ("expired", "approve", None),
    ],
)
def test_only_approve_and_reject_decisions_resume(status: str, decision: str, expected: dict | None) -> None:
    assert ar.resume_command(status, decision, "over limit") == expected


async def test_flag_defaults_off_and_is_read_per_tenant() -> None:
    item = _paused_item(_agent())
    skipped = ar.agent_run_resumes_total.labels(outcome="skipped")
    before = skipped._value.get()
    with (
        patch("core.feature_flags.is_enabled", AsyncMock(return_value=False)) as flag,
        patch.object(ar.logger, "warning") as warn,
    ):
        assert await ar.should_resume(item, TENANT_UUID) is False
    flag.assert_awaited_once_with("approvals.resume_agent_runs", tenant_id=TENANT_UUID, default=False)
    # A paused run left paused because the flag is off or unreadable is visible.
    assert skipped._value.get() == before + 1
    assert warn.call_args.args[0] == "agent_run_resume_skipped"
    assert warn.call_args.kwargs["reason"] == "resume_flag_off_or_unavailable"

    with patch("core.feature_flags.is_enabled", AsyncMock(return_value=True)):
        assert await ar.should_resume(item, TENANT_UUID) is True
    assert skipped._value.get() == before + 1


async def test_a_failed_flag_lookup_skips_the_resume_loudly() -> None:
    from core import feature_flags

    feature_flags.clear_cache()
    item = _paused_item(_agent())
    skipped = ar.agent_run_resumes_total.labels(outcome="skipped")
    before = skipped._value.get()

    @asynccontextmanager
    async def _broken_session(*_a: Any, **_k: Any):
        raise ConnectionError("flag store unavailable")
        yield

    try:
        with (
            patch("core.feature_flags.get_tenant_session", _broken_session),
            patch.object(ar.logger, "warning") as warn,
        ):
            assert await ar.should_resume(item, TENANT_UUID) is False
    finally:
        feature_flags.clear_cache()
    assert skipped._value.get() == before + 1
    assert warn.call_args.args[0] == "agent_run_resume_skipped"


@pytest.mark.parametrize(
    "extra",
    [
        {"workflow_run_id": uuid.uuid4()},
        {"checkpoint_thread_id": None},
        {"decision": "override"},
        {"status": "pending"},
    ],
)
async def test_non_resumable_approvals_never_consult_the_flag(extra: dict) -> None:
    with patch("core.feature_flags.is_enabled", AsyncMock(return_value=True)) as flag:
        assert await ar.should_resume(_paused_item(_agent(), **extra), TENANT_UUID) is False
    flag.assert_not_awaited()


def test_resume_parameters_never_leave_the_api() -> None:
    from api.v1.approvals import _hitl_to_dict

    rendered = _hitl_to_dict(_paused_item(_agent()))
    assert ar.RESUME_SPEC_KEY not in rendered["context"]
    assert rendered["context"] == {"output": {"total": 750000}}
    assert "checkpoint_thread_id" not in rendered


async def _decide(item: Any, agent: Any, *, flag: bool, decision: str = "approve") -> tuple[dict, BackgroundTasks]:
    from api.v1.approvals import decide
    from core.schemas.api import HITLDecision

    request = _admin()
    claims = request.state.claims
    tasks = BackgroundTasks()
    with (
        _session_patch("api.v1.approvals.get_tenant_session", _QueueSession(item, agent)),
        patch("core.approvals.resolve_policy", AsyncMock(return_value=None)),
        patch("core.feedback.shadow_learning.capture_hitl_feedback", AsyncMock(return_value={})),
        patch("core.feature_flags.is_enabled", AsyncMock(return_value=flag)),
    ):
        response = await decide(
            hitl_id=item.id,
            body=HITLDecision(decision=decision, notes="checked"),
            background_tasks=tasks,
            request=request,
            tenant_id=TENANT,
            user_claims=claims,
            user_role=claims["role"],
            user_domains=claims["agenticorg:domains"],
        )
    return response, tasks


async def test_approving_schedules_the_resume_of_the_paused_run_when_the_flag_is_on() -> None:
    agent = _agent()
    item = _paused_item(agent, status="pending", decision=None)
    response, tasks = await _decide(item, agent, flag=True)
    assert response["status"] == "decided"
    resumes = [t for t in tasks.tasks if t.func is ar.resume_approved_agent_run]
    assert len(resumes) == 1
    # Only the tenant from the auth context and the row id; no thread from the request.
    assert resumes[0].args == (TENANT_UUID, item.id)


async def test_approving_with_the_flag_off_leaves_the_run_paused() -> None:
    agent = _agent()
    _, tasks = await _decide(_paused_item(agent, status="pending", decision=None), agent, flag=False)
    assert not [t for t in tasks.tasks if t.func is ar.resume_approved_agent_run]


async def test_workflow_approvals_keep_the_workflow_resume_path() -> None:
    agent = _agent()
    item = _paused_item(agent, status="pending", decision=None, workflow_run_id=uuid.uuid4())
    _, tasks = await _decide(item, agent, flag=True)
    assert not [t for t in tasks.tasks if t.func is ar.resume_approved_agent_run]


# ── The resume itself ──────────────────────────────────────────────────────


async def test_approved_run_resumes_from_its_recorded_parameters_and_cleans_up() -> None:
    agent = _agent()
    item = _paused_item(agent)
    outcome, runner, deleter, sessions = await _resume(item, agent, result={"status": "completed", "output": {}})

    assert outcome == {"outcome": "completed", "reason": ""}
    kwargs = runner.await_args.kwargs
    assert kwargs["thread_id"] == item.checkpoint_thread_id
    assert kwargs["tenant_id"] == TENANT
    assert kwargs["decision"] == {"action": "approve"}
    assert kwargs["require_paused"] is True
    assert kwargs["connector_config"] == {}
    assert kwargs["confidence_floor"] == 0.5
    assert kwargs["hitl_condition"] == "total > 500000"
    assert kwargs["authorized_tools"] == ["erp:get_invoice"]
    assert kwargs["connector_names"] == ["erp"]
    assert kwargs["system_prompt"] == agent.system_prompt_text
    deleter.assert_awaited_once_with(item.checkpoint_thread_id)
    assert sessions.opened == [TENANT_UUID, TENANT_UUID]
    state = item.context[ar.RESUME_STATE_KEY]
    assert state["state"] == "completed" and state["checkpoint_deleted"] is True
    (audit,) = sessions.audits()
    assert (audit.event_type, audit.outcome, audit.resource_id) == ("agent.run.resumed", "completed", str(item.id))
    assert audit.tenant_id == TENANT_UUID


async def test_rejected_run_resumes_into_the_rejection() -> None:
    agent = _agent()
    item = _paused_item(agent, status="rejected", decision="reject", decision_notes="over limit")
    result = {"status": "failed", "error": "Rejected by human: over limit"}
    outcome, runner, deleter, _ = await _resume(item, agent, result=result)
    assert outcome == {"outcome": "rejected", "reason": ""}
    assert runner.await_args.kwargs["decision"] == {"action": "reject", "reason": "over limit"}
    deleter.assert_awaited_once()


async def test_a_rejection_the_gate_did_not_apply_is_a_failure_not_a_completion() -> None:
    agent = _agent()
    item = _paused_item(agent, decision="reject")
    outcome, _, deleter, _ = await _resume(item, agent, result={"status": "completed", "output": {}})
    assert outcome == {"outcome": "failed", "reason": "rejection_not_applied"}
    deleter.assert_not_awaited()
    assert item.context[ar.RESUME_STATE_KEY]["state"] == "failed"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "failed", "reason": "checkpoint_not_found"}, ("refused", "checkpoint_not_found")),
        ({"status": "failed", "reason": "checkpoint_decrypt_failed"}, ("refused", "checkpoint_decrypt_failed")),
        ({"status": "failed", "error": "boom", "reason": "resume_failed"}, ("failed", "resume_failed")),
        (CheckpointerUnavailableError("checkpoint_store_unreachable"), ("refused", "checkpoint_store_unreachable")),
        (RuntimeError("unexpected"), ("failed", "resume_failed")),
    ],
)
async def test_unresumable_checkpoints_are_recorded_and_left_in_place(result: Any, expected: tuple[str, str]) -> None:
    agent = _agent()
    item = _paused_item(agent)
    outcome, _, deleter, sessions = await _resume(item, agent, result=result)
    assert (outcome["outcome"], outcome["reason"]) == expected
    deleter.assert_not_awaited()
    assert item.context[ar.RESUME_STATE_KEY]["reason"] == expected[1]
    assert sessions.audits()[0].outcome == expected[0]


# ── Tenant isolation ───────────────────────────────────────────────────────


async def test_tenant_b_cannot_resume_tenant_a_approval_by_id() -> None:
    agent = _agent()
    item = _paused_item(agent)
    outcome, runner, deleter, sessions = await _resume(item, agent, tenant=OTHER_TENANT, result={"status": "completed"})
    assert outcome == {"outcome": "refused", "reason": "approval_not_found"}
    runner.assert_not_awaited()
    deleter.assert_not_awaited()
    assert sessions.opened == [OTHER_TENANT]
    assert ar.RESUME_STATE_KEY not in item.context
    assert sessions.audits() == []


async def test_a_row_holding_another_tenants_thread_is_never_resumed() -> None:
    agent = _agent()
    foreign = new_thread_id(OTHER_TENANT)
    item = _paused_item(agent, checkpoint_thread_id=foreign)
    outcome, runner, deleter, _ = await _resume(item, agent, result={"status": "completed"})
    assert outcome == {"outcome": "refused", "reason": "checkpoint_thread_tenant_mismatch"}
    runner.assert_not_awaited()
    deleter.assert_not_awaited()


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"context": {ar.RESUME_STATE_KEY: {"state": "resuming"}, ar.RESUME_SPEC_KEY: SPEC}}, "already_resumed"),
        ({"workflow_run_id": uuid.uuid4()}, "approval_not_resumable"),
        ({"checkpoint_thread_id": None}, "approval_not_resumable"),
        ({"decision": "override"}, "decision_not_resumable"),
        ({"context": {"output": {}}}, "resume_spec_missing"),
    ],
)
async def test_refusals_before_the_resume(extra: dict, reason: str) -> None:
    agent = _agent()
    outcome, runner, _, _ = await _resume(_paused_item(agent, **extra), agent, result={"status": "completed"})
    assert outcome == {"outcome": "refused", "reason": reason}
    runner.assert_not_awaited()


async def test_missing_agent_is_refused() -> None:
    item = _paused_item(_agent())
    outcome, runner, _, _ = await _resume(item, None, result={"status": "completed"})
    assert outcome == {"outcome": "refused", "reason": "agent_not_found"}
    runner.assert_not_awaited()


async def test_a_second_resume_of_the_same_approval_is_refused() -> None:
    agent = _agent()
    item = _paused_item(agent)
    first, runner, _, _ = await _resume(item, agent, result={"status": "completed", "output": {}})
    second, runner_again, _, _ = await _resume(item, agent, result={"status": "completed", "output": {}})
    assert first["outcome"] == "completed"
    assert second == {"outcome": "refused", "reason": "already_resumed"}
    runner_again.assert_not_awaited()


async def test_runner_requires_a_checkpoint_waiting_at_the_gate() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from core.langgraph import runner

    for snapshot, reason in (
        (SimpleNamespace(values={}, next=()), "checkpoint_not_found"),
        (SimpleNamespace(values={"status": "completed"}, next=()), "checkpoint_not_paused"),
    ):
        compiled = MagicMock()
        compiled.aget_state = AsyncMock(return_value=snapshot)
        compiled.ainvoke = AsyncMock()
        graph = MagicMock()
        graph.compile = MagicMock(return_value=compiled)
        with (
            patch.object(runner, "build_agent_graph", return_value=graph),
            patch.object(runner, "prefetch_llm_credential", new=AsyncMock(return_value=None)),
        ):
            result = await runner.resume_agent(
                agent_id="a",
                thread_id=new_thread_id(TENANT_UUID),
                decision={"action": "approve"},
                system_prompt="s",
                authorized_tools=[],
                tenant_id=TENANT,
                require_paused=True,
            )
        assert result == {"status": "failed", "error": reason, "reason": reason}
        compiled.ainvoke.assert_not_awaited()
