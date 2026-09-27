"""Approval policy engine — resolves a HITL item against a policy's steps.

Used by the /api/v1/approvals/decide endpoint to answer:
  - Who is the next approver? (role + quorum)
  - Have we collected enough decisions at the current step to advance?
  - Are we done (all steps complete)?
  - Which steps' conditions cannot be evaluated for the item, and does the
    tenant's ``approvals.unevaluable_condition`` mode refuse the decision?

The engine is deliberately stateless — callers pass in the current
HITL item + its decision history, and get back a new state to persist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from core.database import get_tenant_session
from core.models.approval_policy import ApprovalPolicy, ApprovalStep

logger = structlog.get_logger()

# Operator-managed authority flag (reserved in ``core.feature_flags``): what a
# decision does when a step's condition cannot be evaluated for the item.
# ``off`` (no enabling row) applies the step; ``deny`` refuses every decision
# but a rejection, which closes the item and approves nothing.
# The flag store holds booleans, so ``deny`` is a row of its own, as for
# ``grants.enforce_closed.deny``.
UNEVALUABLE_CONDITION_FLAG = "approvals.unevaluable_condition"
UNEVALUABLE_CONDITION_DENY_FLAG = f"{UNEVALUABLE_CONDITION_FLAG}.deny"
UNEVALUABLE_CONDITION_OFF = "off"
UNEVALUABLE_CONDITION_DENY = "deny"
REASON_CONDITION_UNEVALUABLE = "approval_condition_unevaluable"


@dataclass
class PolicyDecision:
    """Result of feeding a decision through the policy engine."""

    action: str  # "advance" | "collect" | "reject" | "complete"
    next_step: ApprovalStep | None
    current_step_approvals: int
    reason: str


def _condition_matches(condition: str | None, context: dict[str, Any]) -> bool:
    """Whether a step applies to an item. Empty condition = applies.

    Supported grammar (that of ``workflows.condition_evaluator``):
      amount > 100000
      amount >= 50000 and domain == "finance"
      plan in ["enterprise", "pro"]

    Skipping a step removes approvals, so a condition that cannot be
    evaluated - a field the item does not carry, an unparseable expression,
    an evaluator error - makes the step apply rather than skipping it.
    """
    if not condition:
        return True
    result = _evaluate_condition(condition, context)
    return True if result is None else result


def _evaluate_condition(condition: str, context: dict[str, Any]) -> bool | None:
    """Three-valued: ``None`` when the condition cannot be evaluated for this item."""
    try:
        from workflows.condition_evaluator import evaluate_condition_strict

        result = evaluate_condition_strict(condition, context)
    # enterprise-gate: broad-except-ok reason=approval-policy-condition-failure-is-unknown-never-a-skip-fail-closed
    except Exception:
        logger.warning("approval_policy_condition_eval_failed", condition=condition)
        return None
    if result is None:
        logger.warning("approval_policy_condition_unevaluable", condition=condition)
    return result


async def unevaluable_condition_mode(tenant_id: uuid.UUID) -> str:
    """The tenant's ``approvals.unevaluable_condition`` mode: ``off`` (the default) or ``deny``.

    ``deny`` when the global row or the tenant's row of
    ``approvals.unevaluable_condition.deny`` enables it, each evaluated on its
    own, so a tenant row can make a tenant stricter but never lift an
    operator's global setting.
    """
    from core.feature_flags import FeatureFlagLookupError, load_flag_rows_strict, row_enabled

    try:
        rows = await load_flag_rows_strict(UNEVALUABLE_CONDITION_DENY_FLAG, tenant_id=tenant_id)
    except FeatureFlagLookupError:
        # The mode is unknown, and reading it as ``off`` would count a vote
        # against a step nobody could evaluate. A refused vote never approves
        # anything, so an unreadable store resolves to ``deny``.
        logger.error(
            "approval_unevaluable_condition_mode_lookup_failed",
            reason_code="flag_store_unreadable",
            tenant_id=str(tenant_id),
            effective_mode=UNEVALUABLE_CONDITION_DENY,
        )
        return UNEVALUABLE_CONDITION_DENY
    subject = str(tenant_id)
    if row_enabled(UNEVALUABLE_CONDITION_DENY_FLAG, rows.global_row, subject_id=subject) or row_enabled(
        UNEVALUABLE_CONDITION_DENY_FLAG, rows.tenant_row, subject_id=subject
    ):
        return UNEVALUABLE_CONDITION_DENY
    return UNEVALUABLE_CONDITION_OFF


async def resolve_policy(
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    policy_name: str | None = None,
) -> ApprovalPolicy | None:
    """Find the policy that applies to a given scope.

    Precedence: explicit name > workflow-scoped > agent-scoped > tenant-global default.

    ``approval_policies`` is FORCE-RLS: the lookup runs in the tenant's
    session, otherwise a raw session sees no policy and the HITL item falls
    back to the legacy single-step path.
    """
    async with get_tenant_session(tenant_id) as session:
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
    async with get_tenant_session(policy.tenant_id) as session:
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
    async with get_tenant_session(policy.tenant_id) as session:
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


async def unevaluable_steps(policy: ApprovalPolicy, context: dict[str, Any]) -> list[ApprovalStep]:
    """Every step of ``policy``, in sequence, whose condition cannot be evaluated for ``context``.

    All steps, not only the next one: a later step that cannot be evaluated
    decides where the item goes once the current step is satisfied.
    """
    async with get_tenant_session(policy.tenant_id) as session:
        result = await session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.policy_id == policy.id)
            .order_by(ApprovalStep.sequence)
        )
        steps = result.scalars().all()

    return [step for step in steps if step.condition and _evaluate_condition(step.condition, context) is None]


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
