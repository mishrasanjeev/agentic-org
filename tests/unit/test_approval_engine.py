"""Tests for the approval policy engine.

Covers the pure-function bits (apply_decision, _condition_matches) and
the DB-touching helpers (resolve_policy, first_applicable_step,
next_step_after) using mocked sessions. A real Postgres fixture E2E
suite is tracked separately for v4.8.0.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.approvals.policy_engine import (
    PolicyDecision,
    _condition_matches,
    apply_decision,
    first_applicable_step,
    next_step_after,
    resolve_policy,
)
from core.models.approval_policy import ApprovalPolicy, ApprovalStep

# ── _condition_matches ──────────────────────────────────────────────


class TestConditionMatches:
    def test_empty_condition_matches(self):
        """No condition string == always-applicable step."""
        assert _condition_matches(None, {}) is True
        assert _condition_matches("", {}) is True

    def test_condition_with_matching_context(self):
        # The real workflows.condition_evaluator handles `>` etc.
        # We mock it here so this test stays pure.
        with patch(
            "workflows.condition_evaluator.evaluate_condition", return_value=True
        ):
            assert _condition_matches("amount > 1000", {"amount": 5000}) is True

    def test_condition_with_non_matching_context(self):
        with patch(
            "workflows.condition_evaluator.evaluate_condition", return_value=False
        ):
            assert _condition_matches("amount > 1000", {"amount": 100}) is False

    def test_condition_evaluator_failure_returns_false(self):
        """A broken expression should fail closed (deny)."""
        with patch(
            "workflows.condition_evaluator.evaluate_condition",
            side_effect=Exception("bad expression"),
        ):
            assert _condition_matches("garbage", {}) is False


# ── apply_decision ──────────────────────────────────────────────────


def _make_step(quorum_required=1, quorum_total=1):
    """Return a mock that quacks like ApprovalStep — no DB attached.

    SQLAlchemy 2.0 instrumented attributes can't be set on instances
    created via ``__new__``, so we use a MagicMock with the same
    surface area instead. ``apply_decision`` only reads
    ``quorum_required`` so this is enough.
    """
    step = MagicMock(spec=ApprovalStep)
    step.id = uuid.uuid4()
    step.policy_id = uuid.uuid4()
    step.sequence = 1
    step.approver_role = "cfo"
    step.quorum_required = quorum_required
    step.quorum_total = quorum_total
    step.mode = "sequential"
    step.condition = None
    step.step_metadata = {}
    return step


class TestApplyDecision:
    def test_single_approver_approve_advances(self):
        step = _make_step(quorum_required=1, quorum_total=1)
        result = apply_decision(step, prior_approvals=0, decision="approve")
        assert result.action == "advance"
        assert result.current_step_approvals == 1

    def test_quorum_2_of_3_first_approval_collects(self):
        step = _make_step(quorum_required=2, quorum_total=3)
        result = apply_decision(step, prior_approvals=0, decision="approve")
        assert result.action == "collect"
        assert result.current_step_approvals == 1
        assert "1/2" in result.reason

    def test_quorum_2_of_3_second_approval_advances(self):
        step = _make_step(quorum_required=2, quorum_total=3)
        result = apply_decision(step, prior_approvals=1, decision="approve")
        assert result.action == "advance"
        assert result.current_step_approvals == 2
        # Reason format is "quorum {required}/{total} reached"
        assert "2/3" in result.reason

    def test_reject_short_circuits_regardless_of_quorum(self):
        step = _make_step(quorum_required=2, quorum_total=3)
        result = apply_decision(step, prior_approvals=1, decision="reject")
        assert result.action == "reject"
        assert result.current_step_approvals == 1

    def test_unknown_decision_raises(self):
        step = _make_step()
        with pytest.raises(ValueError, match="Unknown decision"):
            apply_decision(step, prior_approvals=0, decision="maybe")


# ── DB-backed helpers (mocked session) ──────────────────────────────


def _patch_session(session):
    """Return a patch that makes async_session_factory yield `session`."""

    @asynccontextmanager
    async def _ctx():
        yield session

    return patch(
        "core.approvals.policy_engine.async_session_factory",
        side_effect=lambda: _ctx(),
    )


def _scalar_result(value):
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _scalars_result(values):
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=values)
    res = MagicMock()
    res.scalars = MagicMock(return_value=scalars)
    return res


class TestResolvePolicy:
    @pytest.mark.asyncio
    async def test_explicit_name_wins(self):
        """If a policy_name is given, look it up first."""
        target = MagicMock(spec=ApprovalPolicy)
        target.id = uuid.uuid4()
        target.tenant_id = uuid.uuid4()
        target.name = "high_value"

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalar_result(target))

        with _patch_session(session):
            result = await resolve_policy(
                tenant_id=target.tenant_id,
                policy_name="high_value",
            )

        assert result is target
        session.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_workflow_scope_fallback(self):
        """If no name, try workflow-scoped policy."""
        target = MagicMock(spec=ApprovalPolicy)
        target.id = uuid.uuid4()

        session = MagicMock()
        # First call returns None (no name lookup), second returns the workflow policy
        session.execute = AsyncMock(
            side_effect=[
                _scalar_result(target),  # workflow lookup
            ]
        )

        with _patch_session(session):
            result = await resolve_policy(
                tenant_id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
            )

        assert result is target


class TestFirstApplicableStep:
    @pytest.mark.asyncio
    async def test_first_unconditional_step_picked(self):
        policy = MagicMock(spec=ApprovalPolicy)
        policy.id = uuid.uuid4()

        s1 = _make_step()
        s1.sequence = 1
        s1.condition = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalars_result([s1]))

        with _patch_session(session):
            step = await first_applicable_step(policy, {"amount": 0})
        assert step is s1

    @pytest.mark.asyncio
    async def test_skips_steps_with_failing_conditions(self):
        policy = MagicMock(spec=ApprovalPolicy)
        policy.id = uuid.uuid4()

        s1 = _make_step()
        s1.sequence = 1
        s1.condition = "amount > 1000000"  # won't match

        s2 = _make_step()
        s2.sequence = 2
        s2.condition = None  # matches

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalars_result([s1, s2]))

        with _patch_session(session):
            with patch(
                "workflows.condition_evaluator.evaluate_condition",
                side_effect=lambda c, ctx: c is None or False,
            ):
                step = await first_applicable_step(policy, {"amount": 100})

        assert step is s2


class TestNextStepAfter:
    @pytest.mark.asyncio
    async def test_returns_next_in_sequence(self):
        policy = MagicMock(spec=ApprovalPolicy)
        policy.id = uuid.uuid4()

        s2 = _make_step()
        s2.sequence = 2
        s2.condition = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalars_result([s2]))

        with _patch_session(session):
            step = await next_step_after(policy, current_sequence=1, context={})
        assert step is s2

    @pytest.mark.asyncio
    async def test_returns_none_when_no_more_steps(self):
        policy = MagicMock(spec=ApprovalPolicy)
        policy.id = uuid.uuid4()

        session = MagicMock()
        session.execute = AsyncMock(return_value=_scalars_result([]))

        with _patch_session(session):
            step = await next_step_after(policy, current_sequence=99, context={})
        assert step is None


# ── State machine integration (no DB) ───────────────────────────────


class TestStateMachineIntegration:
    """Walk a full 2-of-3 approval scenario through apply_decision."""

    def test_two_of_three_quorum_flow(self):
        step = _make_step(quorum_required=2, quorum_total=3)

        # First approval — collect
        first = apply_decision(step, prior_approvals=0, decision="approve")
        assert first.action == "collect"
        assert first.current_step_approvals == 1

        # Second approval — advance
        second = apply_decision(step, prior_approvals=first.current_step_approvals, decision="approve")
        assert second.action == "advance"
        assert second.current_step_approvals == 2

    def test_reject_after_first_approval_kills_it(self):
        step = _make_step(quorum_required=2, quorum_total=3)

        first = apply_decision(step, prior_approvals=0, decision="approve")
        assert first.action == "collect"

        second = apply_decision(step, prior_approvals=first.current_step_approvals, decision="reject")
        assert second.action == "reject"


# ── PolicyDecision dataclass shape ──────────────────────────────────


def test_policy_decision_dataclass_fields():
    pd = PolicyDecision(action="advance", next_step=None, current_step_approvals=2, reason="ok")
    assert pd.action == "advance"
    assert pd.current_step_approvals == 2
    assert pd.reason == "ok"
