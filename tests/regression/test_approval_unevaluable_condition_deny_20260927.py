# SPDX-License-Identifier: Apache-2.0
"""An approval step condition that cannot be evaluated can refuse the vote (review H-6 residual).

With the operator flag ``approvals.unevaluable_condition`` off (the default) a
step whose condition cannot be evaluated for an item applies and the vote is
counted against it; only a server log says the condition was unknown. Set to
``deny`` (an enabled ``approvals.unevaluable_condition.deny`` row, global or
for the tenant) a policy with such a step counts no decision that could move
the item forward: an approval, a defer or any other value gets ``409`` with
reason code ``approval_condition_unevaluable``, and the refusal is committed to
the item's ``policy_state`` and the audit log. A rejection is still taken,
since it closes the item and approves nothing. The refused vote is not
counted, so the reviewer can vote once the condition is corrected, and a
decision that is later recorded clears the refusal from ``policy_state``. A
flag store that cannot be read refuses the same way, but never a rejection.
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from core.feature_flags import FeatureFlagLookupError, FlagRows, is_reserved_flag_key
from core.models.audit import AuditLog
from tests.company_scope import TEST_TENANT_ID
from tests.regression.test_approval_policy_single_approver_20260925 import (
    USER_A,
    USER_B,
    _agent,
    _claims,
    _item,
    _patch_session,
    _policy,
    _QueueSession,
    _step,
)

TENANT = str(TEST_TENANT_ID)
DENY_FLAG = "approvals.unevaluable_condition.deny"
REASON = "approval_condition_unevaluable"
ON = {"enabled": True, "rollout_percentage": 100}
OFF = {"enabled": False, "rollout_percentage": 100}
# The item carries ``amount``; the policy names ``output.amount``.
UNKNOWN = "output.amount > 1000000"
KNOWN = "amount > 1000000"
# Evaluates, and is false, for the items below (amount 5,000,000).
SKIPPED = "amount > 10000000"


class _RecordingSession(_QueueSession):
    """The decide endpoint's session: keeps added rows and what each commit made durable."""

    def __init__(self, item: SimpleNamespace, agent: SimpleNamespace, *queued: Any) -> None:
        super().__init__(item, agent, *queued)
        self.item = item
        self.added: list[Any] = []
        self.committed: list[tuple[list[Any], dict[str, Any]]] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed.append((list(self.added), copy.deepcopy(self.item.context)))

    def refusals(self) -> list[AuditLog]:
        return [row for row in self.added if isinstance(row, AuditLog) and row.event_type == "hitl.decision_refused"]


class _Steps:
    """The policy engine's session: every query returns the policy's steps."""

    def __init__(self, *steps: MagicMock) -> None:
        self.steps = list(steps)

    async def execute(self, *_a: Any, **_k: Any) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self.steps)
        return result


def _flag_rows(global_row: dict[str, Any] | None, tenant_row: dict[str, Any] | None):
    async def load(flag_key: str, *, tenant_id: uuid.UUID) -> FlagRows:
        assert tenant_id == TEST_TENANT_ID
        if flag_key == DENY_FLAG:
            return FlagRows(global_row=global_row, tenant_row=tenant_row)
        return FlagRows(global_row=None, tenant_row=None)

    return patch("core.feature_flags.load_flag_rows_strict", load)


def _unreadable_flag_store():
    return patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=FeatureFlagLookupError("down")))


def _deny_mode(store: str):
    """Each way the mode resolves to ``deny``."""
    if store == "unreadable":
        return _unreadable_flag_store()
    return _flag_rows(ON, None) if store == "global-deny" else _flag_rows(None, ON)


def _decided_rows(session: _RecordingSession) -> list[AuditLog]:
    return [row for row in session.added if isinstance(row, AuditLog) and row.event_type == "hitl.decided"]


async def _decide(
    item: SimpleNamespace,
    session: _RecordingSession,
    policy: MagicMock,
    steps: _Steps,
    *,
    user: uuid.UUID = USER_A,
    decision: str = "approve",
) -> Any:
    from api.v1.approvals import decide
    from core.schemas.api import HITLDecision

    claims = _claims(user)
    request = SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=["approvals:read", "approvals:write"], auth_mode="legacy")
    )
    with (
        _patch_session("api.v1.approvals.get_tenant_session", session),
        _patch_session("core.approvals.policy_engine.get_tenant_session", steps),
        patch("core.approvals.resolve_policy", AsyncMock(return_value=policy)),
        patch("core.feedback.shadow_learning.capture_hitl_feedback", AsyncMock(return_value={})),
    ):
        return await decide(
            hitl_id=item.id,
            body=HITLDecision(decision=decision),
            background_tasks=BackgroundTasks(),
            request=request,
            tenant_id=TENANT,
            user_claims=claims,
            user_role="cfo",
            user_domains=["finance"],
        )


def _assert_refused(
    exc: HTTPException, item: SimpleNamespace, session: _RecordingSession, policy: MagicMock, sequences: list[int]
) -> None:
    assert exc.status_code == 409
    assert exc.detail["reason_code"] == REASON
    assert exc.detail["unevaluable_steps"] == sequences

    # Nothing was decided and the vote was not counted.
    assert item.status == "pending"
    assert item.decision is None
    assert item.decision_by is None
    state = item.context["policy_state"]
    assert state["last_action"] == "refused"
    assert state["last_reason"] == REASON
    assert state["unevaluable_steps"] == sequences
    assert not state.get("approvals")
    assert "policy_id" not in state  # the item is not bound to a policy it never entered

    [audit] = session.refusals()
    assert audit.outcome == "denied"
    assert audit.resource_type == "hitl_item"
    assert audit.resource_id == str(item.id)
    assert audit.actor_id == str(USER_A)
    assert audit.details["reason_code"] == REASON
    assert audit.details["policy_id"] == str(policy.id)
    assert audit.details["unevaluable_steps"] == sequences

    # The refusal was committed before the error was raised; raising inside
    # the tenant session alone would roll it back.
    assert len(session.committed) == 1
    committed_rows, committed_context = session.committed[0]
    assert audit in committed_rows
    assert committed_context["policy_state"] == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("global_row", "tenant_row"),
    [(None, ON), (ON, None), (ON, OFF)],
    ids=["tenant-deny", "global-deny", "tenant-row-cannot-lift-global-deny"],
)
async def test_an_unevaluatable_condition_denies_with_a_reason_when_enabled(
    global_row: dict[str, Any] | None, tenant_row: dict[str, Any] | None
) -> None:
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    session = _RecordingSession(item, agent)

    with _flag_rows(global_row, tenant_row), pytest.raises(HTTPException) as exc:
        await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=UNKNOWN)))
    _assert_refused(exc.value, item, session, policy, [1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("global_row", "tenant_row"),
    [(None, None), (OFF, None), (None, OFF), (OFF, OFF)],
    ids=["no-rows", "global-off", "tenant-off", "both-off"],
)
async def test_flag_off_keeps_the_step_required(
    global_row: dict[str, Any] | None, tenant_row: dict[str, Any] | None
) -> None:
    """Behaviour unchanged: the step applies and the vote counts toward its quorum of two."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    session = _RecordingSession(item, agent)

    with _flag_rows(global_row, tenant_row):
        result = await _decide(item, session, _policy(), _Steps(_step(1, quorum=2, condition=UNKNOWN)))

    assert result["policy_action"] == "collect"
    assert item.status == "pending"
    state = item.context["policy_state"]
    assert state["current_sequence"] == 1
    assert state["approvals_collected"] == 1
    assert state["last_action"] == "collect"
    assert "unevaluable_steps" not in state
    assert session.refusals() == []
    assert session.committed == []


@pytest.mark.asyncio
async def test_unreadable_flag_store_fails_closed() -> None:
    """The flag's value is unknown, so the vote is refused rather than counted under ``off``."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    session = _RecordingSession(item, agent)

    with _unreadable_flag_store(), pytest.raises(HTTPException) as exc:
        await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=UNKNOWN)))
    _assert_refused(exc.value, item, session, policy, [1])


@pytest.mark.asyncio
async def test_an_evaluator_error_counts_as_a_condition_that_cannot_be_evaluated() -> None:
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    session = _RecordingSession(item, agent)

    with (
        _flag_rows(None, ON),
        patch("workflows.condition_evaluator.evaluate_condition_strict", side_effect=RuntimeError("boom")),
        pytest.raises(HTTPException) as exc,
    ):
        await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=KNOWN)))
    _assert_refused(exc.value, item, session, policy, [1])


@pytest.mark.asyncio
async def test_an_unreadable_flag_store_does_not_block_a_policy_it_can_evaluate() -> None:
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    session = _RecordingSession(item, agent)

    with _unreadable_flag_store():
        result = await _decide(item, session, _policy(), _Steps(_step(1, quorum=2, condition=KNOWN)))

    assert result["policy_action"] == "collect"
    assert item.context["policy_state"]["approvals_collected"] == 1
    assert session.refusals() == []


@pytest.mark.asyncio
async def test_deny_mode_leaves_a_policy_it_can_evaluate_alone() -> None:
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    session = _RecordingSession(item, agent)

    with _flag_rows(None, ON):
        result = await _decide(item, session, _policy(), _Steps(_step(1, quorum=2, condition=KNOWN), _step(2)))

    assert result["policy_action"] == "collect"
    assert item.context["policy_state"]["approvals_collected"] == 1
    assert session.refusals() == []


@pytest.mark.asyncio
async def test_a_later_step_that_cannot_be_evaluated_refuses_the_first_vote() -> None:
    """Step 1 is decided by one vote; step 2 cannot be evaluated. The item must not advance into it."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    first, board = _step(1, condition=KNOWN), _step(2, quorum=2, condition=UNKNOWN)
    session = _RecordingSession(item, agent)

    with (
        _flag_rows(None, ON),
        patch("core.approvals.next_step_after", AsyncMock(return_value=board)),
        pytest.raises(HTTPException) as exc,
    ):
        await _decide(item, session, policy, _Steps(first, board))
    _assert_refused(exc.value, item, session, policy, [2])


@pytest.mark.asyncio
@pytest.mark.parametrize("store", ["tenant-deny", "global-deny", "unreadable"])
async def test_rejection_is_accepted_when_condition_unevaluable_in_deny_mode(store: str) -> None:
    """Rejecting closes the item and approves nothing, so deny mode still takes it.

    An unreadable flag store does not block it either: a rejection never reads the flag.
    """
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    session = _RecordingSession(item, agent)

    with _deny_mode(store):
        result = await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=UNKNOWN)), decision="reject")

    assert result["policy_action"] == "reject"
    assert result["status"] == "rejected"
    assert item.status == "rejected"
    assert item.decision == "reject"
    assert item.decision_by == USER_A
    state = item.context["policy_state"]
    assert state["policy_id"] == str(policy.id)
    assert state["last_action"] == "reject"
    assert "unevaluable_steps" not in state
    assert [(vote["user_id"], vote["decision"]) for vote in state["approvals"]] == [(str(USER_A), "reject")]
    assert session.refusals() == []
    # Written with the rest of the decision, not committed early like a refusal.
    assert session.committed == []
    [decided] = _decided_rows(session)
    assert decided.outcome == "success"
    assert decided.details["policy_action"] == "reject"


@pytest.mark.asyncio
async def test_a_rejection_after_a_refusal_closes_an_item_part_way_through() -> None:
    """One approval already counted, then deny mode: the next approval is refused, a rejection is taken."""
    agent = _agent()
    policy = _policy()
    first_vote = {"user_id": str(USER_B), "identities": [str(USER_B)], "decision": "approve", "sequence": 1}
    item = _item(
        agent,
        {
            "amount": 5_000_000,
            "policy_state": {
                "policy_id": str(policy.id),
                "current_sequence": 1,
                "approvals_collected": 1,
                "approvals": [first_vote],
            },
        },
    )
    board = _step(1, quorum=2, condition=UNKNOWN)

    with _flag_rows(None, ON):
        with pytest.raises(HTTPException) as exc:
            await _decide(item, _RecordingSession(item, agent), policy, _Steps(board))
        assert exc.value.detail["reason_code"] == REASON
        assert item.context["policy_state"]["last_action"] == "refused"

        # The item is part-way through, so its current step is read by sequence.
        session = _RecordingSession(item, agent, board)
        result = await _decide(item, session, policy, _Steps(board), decision="reject")

    assert result["policy_action"] == "reject"
    assert item.status == "rejected"
    assert item.decision == "reject"
    state = item.context["policy_state"]
    assert state["policy_id"] == str(policy.id)
    assert state["last_action"] == "reject"
    assert state["last_reason"] == "decision=reject"
    assert "unevaluable_steps" not in state
    assert [(vote["user_id"], vote["decision"]) for vote in state["approvals"]] == [
        (str(USER_B), "approve"),
        (str(USER_A), "reject"),
    ]
    assert session.refusals() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["defer", "Reject", ""], ids=["defer", "other-value", "empty-means-approve"])
async def test_every_decision_but_a_rejection_is_refused(decision: str) -> None:
    """A defer ends an item as decided where no policy applies, and the engine has no rule for it or any other value."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    session = _RecordingSession(item, agent)

    with _flag_rows(None, ON), pytest.raises(HTTPException) as exc:
        await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=UNKNOWN)), decision=decision)
    _assert_refused(exc.value, item, session, policy, [1])
    assert session.refusals()[0].details["decision"] == decision


@pytest.mark.asyncio
@pytest.mark.parametrize("afterwards", ["no-step-applies", "no-policy"])
async def test_the_refusal_is_cleared_once_the_item_is_decided(afterwards: str) -> None:
    """The policy is recreated so that no step applies to the item, or removed; the next approval decides it.

    Neither path rebuilds ``policy_state``, so the refusal's markers must be removed explicitly, or the
    decided item and its ``hitl.decided`` audit row would still read as refused.
    """
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})

    with _flag_rows(None, ON):
        with pytest.raises(HTTPException):
            await _decide(
                item, _RecordingSession(item, agent), _policy(), _Steps(_step(1, quorum=2, condition=UNKNOWN))
            )
        assert item.context["policy_state"]["last_action"] == "refused"

        recreated = _policy() if afterwards == "no-step-applies" else None
        session = _RecordingSession(item, agent)
        result = await _decide(item, session, recreated, _Steps(_step(1, quorum=2, condition=SKIPPED)))

    assert result["policy_action"] == "advance"
    assert item.status == "decided"
    assert item.decision == "approve"
    assert item.decision_by == USER_A
    assert item.context["amount"] == 5_000_000
    [decided] = _decided_rows(session)
    if recreated is None:
        assert "policy_state" not in item.context
        assert decided.details["policy_state"] is None
    else:
        assert item.context["policy_state"] == {}
        assert decided.details["policy_state"] == {}
    assert result["policy_state"] is None
    assert session.refusals() == []


@pytest.mark.asyncio
async def test_a_refusal_keeps_the_votes_already_collected() -> None:
    """The flag was switched to deny while the item was part-way through its policy."""
    agent = _agent()
    policy = _policy()
    first_vote = {"user_id": str(USER_B), "identities": [str(USER_B)], "decision": "approve", "sequence": 1}
    item = _item(
        agent,
        {
            "amount": 5_000_000,
            "policy_state": {
                "policy_id": str(policy.id),
                "current_sequence": 1,
                "approvals_collected": 1,
                "approvals": [first_vote],
            },
        },
    )
    session = _RecordingSession(item, agent)

    with _flag_rows(None, ON), pytest.raises(HTTPException) as exc:
        await _decide(item, session, policy, _Steps(_step(1, quorum=2, condition=UNKNOWN)))

    assert exc.value.status_code == 409
    assert exc.value.detail["reason_code"] == REASON
    state = item.context["policy_state"]
    assert state["policy_id"] == str(policy.id)
    assert state["approvals_collected"] == 1
    assert state["approvals"] == [first_vote]
    assert state["last_action"] == "refused"
    assert state["unevaluable_steps"] == [1]
    assert item.status == "pending"
    assert len(session.refusals()) == 1
    assert session.committed[0][1]["policy_state"] == state


@pytest.mark.asyncio
async def test_a_refused_vote_is_not_counted_against_the_reviewer() -> None:
    """Once the condition is corrected the same reviewer votes normally, not as a second vote."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    policy = _policy()
    board = _step(1, quorum=2, condition=UNKNOWN)

    with _flag_rows(None, ON):
        with pytest.raises(HTTPException):
            await _decide(item, _RecordingSession(item, agent), policy, _Steps(board))
        board.condition = KNOWN
        result = await _decide(item, _RecordingSession(item, agent), policy, _Steps(board))

    assert result["policy_action"] == "collect"
    state = item.context["policy_state"]
    assert state["approvals_collected"] == 1
    assert [vote["user_id"] for vote in state["approvals"]] == [str(USER_A)]
    assert "unevaluable_steps" not in state


def test_the_flag_is_reserved_for_operators() -> None:
    """Tenant admins cannot set or clear it through the feature-flag API; operators use scripts/authority_flags.py."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from api.v1 import feature_flags as flags_api

    assert is_reserved_flag_key("approvals.unevaluable_condition")
    assert is_reserved_flag_key(DENY_FLAG)

    app = FastAPI()

    @app.middleware("http")
    async def _tenant_admin(request: Request, call_next):
        request.state.claims = {"sub": "admin@tenant.example.com", "role": "admin"}
        request.state.scopes = ["agenticorg:admin"]
        request.state.tenant_id = TENANT
        return await call_next(request)

    app.include_router(flags_api.router, prefix="/api/v1")
    session = AsyncMock(side_effect=AssertionError("the database must not be touched"))
    with patch("api.v1.feature_flags.get_tenant_session", session), TestClient(app) as client:
        responses = [
            client.post("/api/v1/feature-flags", json={"flag_key": DENY_FLAG, "enabled": False}),
            client.delete(f"/api/v1/feature-flags/{DENY_FLAG}"),
        ]
    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "flag_key_reserved"
    session.assert_not_called()
