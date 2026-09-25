# SPDX-License-Identifier: Apache-2.0
"""An approval step whose condition cannot be evaluated applies (review H-6).

Skipping a step removes approvals, so an unevaluatable condition must never
skip one. The shared workflow evaluator answers "no match" for a missing
field, and so ``NOT <missing>`` answered "match" — both wrong for an
authority decision. Approval policies use a three-valued evaluator instead:
``None`` means "cannot decide", and the step then applies.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.approvals.policy_engine import _condition_matches, first_applicable_step
from core.models.approval_policy import ApprovalPolicy, ApprovalStep
from workflows.condition_evaluator import evaluate_condition, evaluate_condition_strict


@pytest.mark.parametrize(
    ("expression", "context", "expected"),
    [
        ("amount > 100", {"amount": 500}, True),
        ("amount > 100", {"amount": 5}, False),
        ("amount > 100", {}, None),
        ("output.amount > 100", {"amount": 500}, None),
        ("NOT amount > 100", {}, None),
        ("NOT amount > 100", {"amount": 5}, True),
        ("a > 1 OR b > 1", {"a": 5}, True),
        ("a > 1 OR b > 1", {"a": 0}, None),
        ("a > 1 OR b > 1", {"a": 0, "b": 0}, False),
        ("a > 1 AND b > 1", {"a": 0}, False),
        ("a > 1 AND b > 1", {"a": 5}, None),
        ("a > 1 AND b > 1", {"a": 5, "b": 5}, True),
        ("plan in ['enterprise']", {"plan": "enterprise"}, True),
        ("plan in ['enterprise']", {}, None),
        ("plan in [unterminated", {"plan": "enterprise"}, None),
        ("plan not in ['free']", {}, None),
        ("amount > high", {"amount": 5}, None),
        ("status == mismatch", {"status": "mismatch"}, True),
        ("status != mismatch", {"status": "mismatch"}, False),
        ("urgent", {"urgent": True}, True),
        ("urgent", {"urgent": False}, False),
        ("urgent", {}, None),
        ("", {}, None),
        # A keyword inside a quoted string must not split it into halves one of
        # which is definitely false (FINDINGS A-63).
        ("region == 'NORTH AND SOUTH'", {"region": "EAST"}, None),
        ("region == 'NORTH OR SOUTH'", {"region": "NORTH OR SOUTH"}, None),
        # A malformed operand is unknown, never a definite "no match".
        ("status ==", {"status": "ok"}, None),
        ("status === ok", {"status": "ok"}, None),
        ("status == 'ok", {"status": "ok"}, None),
        ("status == ok'", {"status": "ok"}, None),
        ("status == 'o'k'", {"status": "ok"}, None),
        ("amount > ", {"amount": 5}, None),
        ("amount >> 1", {"amount": 5}, None),
        ("== ok", {"status": "ok"}, None),
        ("status == two words", {"status": "ok"}, None),
        ("amount in ", {"amount": 5}, None),
        # Well-formed operands still decide.
        ("status == 'two words'", {"status": "two words"}, True),
        ('name == "O\'Brien"', {"name": "O'Brien"}, True),
        ('name == "A OR B"', {"name": "A OR B"}, None),
        ("status == ok", {"status": "ok"}, True),
        ("amount >= 1.5", {"amount": 2}, True),
        ("amount >= limit", {"amount": 2, "limit": 3}, False),
        # Malformed quoted operands on either side are unknown.
        ("'st'atus' == ok", {}, None),
        ("'ad'min' in roles", {"roles": ["x"]}, None),
        # Quote-aware splitting: an apostrophe in a double-quoted value is not a split point.
        ('name == "O\'Brien" OR x > 1', {"name": "O'Brien"}, True),
        ('name == "O\'Brien" AND x > 1', {"name": "O'Brien", "x": 0}, False),
        # Valid forms the grammar has always accepted still decide.
        ("'admin' in roles", {"roles": ["admin"]}, True),
        ('"admin" not in roles', {"roles": ["admin"]}, False),
        ("risk-level == high", {"risk-level": "high"}, True),
        ("région == nord", {"région": "nord"}, True),
        ("owner == a@b.com", {"owner": "a@b.com"}, True),
        ("path == /api/v1", {"path": "/api/v1"}, True),
        ("delta > +5", {"delta": 7}, True),
        ("created_at > '2026-01-01'", {"created_at": "2026-03-02"}, True),
        ("created_at < '2026-01-01'", {"created_at": "2026-03-02"}, False),
        ("tier >= b", {"tier": "c"}, None),
        # A boolean field compared with true/false decides, rather than never matching.
        ("flag == true", {"flag": True}, True),
        ("flag == false", {"flag": True}, False),
        ("flag != true", {"flag": False}, True),
    ],
)
def test_strict_evaluator_answers_none_when_it_cannot_decide(
    expression: str, context: dict[str, Any], expected: bool | None
) -> None:
    assert evaluate_condition_strict(expression, context) is expected


def test_the_shared_evaluator_would_have_matched_a_negated_missing_field() -> None:
    """Why approval policies no longer use it: a missing field turned ``NOT`` into a match."""
    assert evaluate_condition("NOT amount > 100", {}) is True
    assert evaluate_condition_strict("NOT amount > 100", {}) is None


@pytest.mark.parametrize(
    ("condition", "context"),
    [
        ("output.amount > 1000000", {"amount": 5_000_000}),
        ("NOT amount < 1000", {}),
        ("amount > high", {"amount": 5}),
    ],
)
def test_an_unevaluatable_condition_makes_its_step_apply(condition: str, context: dict[str, Any]) -> None:
    assert _condition_matches(condition, context) is True


def test_a_condition_that_is_false_still_skips_its_step() -> None:
    assert _condition_matches("amount > 1000000", {"amount": 100}) is False


def _step(sequence: int, condition: str | None) -> MagicMock:
    step = MagicMock(spec=ApprovalStep)
    step.id = uuid.uuid4()
    step.sequence = sequence
    step.condition = condition
    step.approver_role = "cfo"
    step.quorum_required = 1
    step.quorum_total = 1
    return step


@pytest.mark.asyncio
async def test_first_applicable_step_picks_a_step_whose_field_is_missing() -> None:
    """The review's case: the policy names ``output.amount``, the item carries ``amount``."""
    policy = MagicMock(spec=ApprovalPolicy)
    policy.id = uuid.uuid4()
    policy.tenant_id = uuid.uuid4()
    board = _step(1, "output.amount > 1000000")

    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [board]
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def fake_session(*_a: Any, **_k: Any):
        yield session

    with patch("core.approvals.policy_engine.get_tenant_session", fake_session):
        assert await first_applicable_step(policy, {"amount": 5_000_000}) is board
