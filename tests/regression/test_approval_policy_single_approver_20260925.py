"""One person cannot satisfy a multi-person approval policy (review H-6).

The duplicate-vote guard compared a reviewer's past votes with the current
step only, and a step's approval count resets when the item advances, so one
senior user could approve step 1, then step 2, and decide an N-step policy
alone. A step that disappeared from the policy mid-approval, or a condition
that could not be evaluated, likewise let a single vote decide the item.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from core.models.approval_policy import ApprovalPolicy, ApprovalStep
from tests.company_scope import TEST_COMPANY_ID, TEST_TENANT_ID

TENANT = str(TEST_TENANT_ID)
USER_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _QueueSession:
    """Async session double: each execute pops the next value."""

    def __init__(self, *values: Any) -> None:
        self.values = list(values)

    async def execute(self, *_a: Any, **_k: Any) -> MagicMock:
        value = self.values.pop(0) if self.values else None
        res = MagicMock()
        res.scalar_one_or_none.return_value = value
        res.scalar_one.return_value = value
        res.scalar.return_value = value if isinstance(value, int) else 0
        res.scalars.return_value.all.return_value = value if isinstance(value, list) else []
        res.all.return_value = value if isinstance(value, list) else []
        return res

    def add(self, _row: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


def _patch_session(target: str, session: Any):
    @asynccontextmanager
    async def fake(*_a: Any, **_k: Any):
        yield session

    return patch(target, fake)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        company_id=TEST_COMPANY_ID,
        name="finance-agent",
        employee_name="finance agent",
        agent_type="ap_processor",
        domain="finance",
        status="active",
        visibility="tenant",
        owner_user_id=None,
        authorized_tools=[],
        connector_ids=[],
        system_prompt_text="You are an agent.",
        llm_provider=None,
        llm_config={},
    )


def _item(agent: SimpleNamespace, context: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        agent_id=agent.id,
        workflow_run_id=None,
        title="Approve payment",
        trigger_type="policy_condition",
        priority="high",
        status="pending",
        assignee_role="cfo",
        decision_options={"options": ["approve", "reject"]},
        context=context,
        decision=None,
        decision_by=None,
        decision_at=None,
        decision_notes=None,
        requested_by_user_id=None,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
        created_at=datetime.now(UTC),
    )


def _step(sequence: int, *, quorum: int = 1, condition: str | None = None) -> MagicMock:
    step = MagicMock(spec=ApprovalStep)
    step.id = uuid.uuid4()
    step.sequence = sequence
    step.approver_role = "cfo"
    step.quorum_required = quorum
    step.quorum_total = quorum
    step.condition = condition
    step.mode = "sequential"
    step.step_metadata = {}
    return step


def _policy() -> MagicMock:
    policy = MagicMock(spec=ApprovalPolicy)
    policy.id = uuid.uuid4()
    policy.tenant_id = TEST_TENANT_ID
    return policy


def _claims(user: uuid.UUID) -> dict[str, Any]:
    return {
        "sub": f"{user}@example.com",
        "role": "cfo",
        "agenticorg:user_id": str(user),
        "agenticorg:domains": ["finance"],
    }


async def _decide(user: uuid.UUID, item: SimpleNamespace, agent: SimpleNamespace, *queued: Any, **patches: Any) -> Any:
    from api.v1.approvals import decide
    from core.schemas.api import HITLDecision

    claims = _claims(user)
    request = SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=["approvals:read", "approvals:write"], auth_mode="legacy")
    )
    session = _QueueSession(item, agent, *queued)
    with (
        _patch_session("api.v1.approvals.get_tenant_session", session),
        patch("core.approvals.resolve_policy", AsyncMock(return_value=patches["policy"])),
        patch("core.feedback.shadow_learning.capture_hitl_feedback", AsyncMock(return_value={})),
    ):
        return await decide(
            hitl_id=item.id,
            body=HITLDecision(decision="approve"),
            background_tasks=BackgroundTasks(),
            request=request,
            tenant_id=TENANT,
            user_claims=claims,
            user_role="cfo",
            user_domains=["finance"],
        )


@pytest.mark.asyncio
async def test_one_person_cannot_approve_two_steps_of_the_same_item() -> None:
    agent = _agent()
    item = _item(agent, {})
    policy = _policy()
    first, second = _step(1), _step(2)

    with (
        patch("core.approvals.first_applicable_step", AsyncMock(return_value=first)),
        patch("core.approvals.next_step_after", AsyncMock(return_value=second)),
    ):
        await _decide(USER_A, item, agent, policy=policy)
        assert item.status == "pending"
        assert item.context["policy_state"]["current_sequence"] == 2

        with pytest.raises(HTTPException) as exc:
            await _decide(USER_A, item, agent, second, policy=policy)
    assert exc.value.status_code == 409
    assert item.status == "pending"


@pytest.mark.asyncio
async def test_a_second_person_completes_the_second_step() -> None:
    agent = _agent()
    item = _item(agent, {})
    policy = _policy()
    first, second = _step(1), _step(2)

    with patch("core.approvals.first_applicable_step", AsyncMock(return_value=first)):
        with patch("core.approvals.next_step_after", AsyncMock(return_value=second)):
            await _decide(USER_A, item, agent, policy=policy)
        with patch("core.approvals.next_step_after", AsyncMock(return_value=None)):
            await _decide(USER_B, item, agent, second, policy=policy)
    assert item.status == "decided"


@pytest.mark.asyncio
async def test_a_step_missing_from_the_policy_refuses_the_decision() -> None:
    """The policy was edited mid-approval: the recorded step no longer exists."""
    agent = _agent()
    item = _item(agent, {"policy_state": {"current_sequence": 7, "approvals_collected": 0, "approvals": []}})

    with pytest.raises(HTTPException) as exc:
        await _decide(USER_A, item, agent, None, policy=_policy())
    assert exc.value.status_code == 409
    assert item.status == "pending"


@pytest.mark.asyncio
async def test_an_unevaluatable_condition_requires_its_step() -> None:
    """The step names ``output.amount``; the item carries ``amount``. It still needs two approvals."""
    agent = _agent()
    item = _item(agent, {"amount": 5_000_000})
    board = _step(1, quorum=2, condition="output.amount > 1000000")
    steps = _QueueSession([board])

    with _patch_session("core.approvals.policy_engine.get_tenant_session", steps):
        await _decide(USER_A, item, agent, policy=_policy())
    assert item.status == "pending"
    assert item.context["policy_state"]["approvals_collected"] == 1
