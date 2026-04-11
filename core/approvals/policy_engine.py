"""Approval policy engine — resolves a HITL item against a policy's steps.

Used by the /api/v1/approvals/decide endpoint to answer:
  - Who is the next approver? (role + quorum)
  - Have we collected enough decisions at the current step to advance?
  - Are we done (all steps complete)?

The engine is deliberately stateless — callers pass in the current
HITL item + its decision history, and get back a new state to persist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from core.database import async_session_factory
from core.models.approval_policy import ApprovalPolicy, ApprovalStep

logger = structlog.get_logger()


@dataclass
class PolicyDecision:
    """Result of feeding a decision through the policy engine."""

    action: str  # "advance" | "collect" | "reject" | "complete"
    next_step: ApprovalStep | None
    current_step_approvals: int
    reason: str


def _condition_matches(condition: str | None, context: dict[str, Any]) -> bool:
    """Evaluate a simple condition expression against a context dict.

    Supported grammar (extended gradually):
      amount > 100000
      amount >= 50000 and domain == "finance"
      plan in ["enterprise", "pro"]

    We delegate to the existing workflows.condition_evaluator which
    already has a tested expression parser. Empty condition = match.
    """
    if not condition:
        return True
    try:
        from workflows.condition_evaluator import evaluate_condition

        return bool(evaluate_condition(condition, context))
    except Exception:
        logger.warning("approval_policy_condition_eval_failed", condition=condition)
        return False


async def resolve_policy(
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    policy_name: str | None = None,
) -> ApprovalPolicy | None:
    """Find the policy that applies to a given scope.

    Precedence: explicit name > workflow-scoped > agent-scoped > tenant-global default.
    """
    async with async_session_factory() as session:
        if policy_name:
            result = await session.execute(
                select(ApprovalPolicy).where(
                    ApprovalPolicy.tenant_id == tenant_id,
                    ApprovalPolicy.name == policy_name,
                )
            )
            policy = result.scalar_one_or_none()
            if policy is not None:
                return policy

        if workflow_id is not None:
            result = await session.execute(
                select(ApprovalPolicy).where(
                    ApprovalPolicy.tenant_id == tenant_id,
                    ApprovalPolicy.workflow_id == workflow_id,
                )
            )
            policy = result.scalar_one_or_none()
            if policy is not None:
                return policy

        if agent_id is not None:
            result = await session.execute(
                select(ApprovalPolicy).where(
                    ApprovalPolicy.tenant_id == tenant_id,
                    ApprovalPolicy.agent_id == agent_id,
                )
            )
            policy = result.scalar_one_or_none()
            if policy is not None:
                return policy

        # Tenant-global default (not scoped to workflow/agent)
        result = await session.execute(
            select(ApprovalPolicy).where(
                ApprovalPolicy.tenant_id == tenant_id,
                ApprovalPolicy.workflow_id.is_(None),
                ApprovalPolicy.agent_id.is_(None),
                ApprovalPolicy.name == "default",
            )
        )
        return result.scalar_one_or_none()


async def first_applicable_step(
    policy: ApprovalPolicy, context: dict[str, Any]
) -> ApprovalStep | None:
    """Return the lowest-sequence step whose condition matches."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.policy_id == policy.id)
            .order_by(ApprovalStep.sequence)
        )
        steps = result.scalars().all()

    for step in steps:
        if _condition_matches(step.condition, context):
            return step
    return None


async def next_step_after(
    policy: ApprovalPolicy,
    current_sequence: int,
    context: dict[str, Any],
) -> ApprovalStep | None:
    """Return the next applicable step after ``current_sequence``."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ApprovalStep)
            .where(
                ApprovalStep.policy_id == policy.id,
                ApprovalStep.sequence > current_sequence,
            )
            .order_by(ApprovalStep.sequence)
        )
        steps = result.scalars().all()

    for step in steps:
        if _condition_matches(step.condition, context):
            return step
    return None


def apply_decision(
    step: ApprovalStep,
    prior_approvals: int,
    decision: str,
) -> PolicyDecision:
    """Advance the state machine for a single decision.

    decision ∈ {"approve", "reject"}

    Returns:
      - action="reject" if the decision was a rejection (whole HITL fails)
      - action="collect" if we still need more approvals at this step
      - action="advance" if the step is satisfied and we should move on
    """
    if decision == "reject":
        return PolicyDecision(
            action="reject",
            next_step=None,
            current_step_approvals=prior_approvals,
            reason="decision=reject",
        )
    if decision != "approve":
        raise ValueError(f"Unknown decision {decision!r}")

    new_approvals = prior_approvals + 1
    if new_approvals >= step.quorum_required:
        return PolicyDecision(
            action="advance",
            next_step=None,
            current_step_approvals=new_approvals,
            reason=f"quorum {step.quorum_required}/{step.quorum_total} reached",
        )
    return PolicyDecision(
        action="collect",
        next_step=None,
        current_step_approvals=new_approvals,
        reason=f"{new_approvals}/{step.quorum_required} approvals collected",
    )
