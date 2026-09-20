# SPDX-License-Identifier: Apache-2.0
"""Recording the human decision on a governed case.

A case reaches ``decided`` only through :func:`record_decision`, and only when the configured
:class:`DecisionVerifier` confirms a valid decision grant for that exact semantic action (PRD G-3):
the case, the action ``case_decision``, the outcome and the subject. Until decision grants are
wired in, the default verifier refuses every decision with ``decision_required`` - the platform
fails closed rather than accepting an approval nobody can prove a human made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.cases.states import CaseError, CaseState
from core.cases.store import business_case_document, transition
from core.domain_schemas import DomainSchemaError, validate
from core.models.governed_case import GovernedCase

logger = structlog.get_logger()

DECISION_OUTCOMES = frozenset({"approve", "decline"})


@dataclass(frozen=True, slots=True)
class DecisionCheck:
    allowed: bool
    reason: str = ""
    #: Approver identity and decision grant id per consumed grant, as verified.
    approvers: tuple[tuple[str, str], ...] = ()


class DecisionVerifier(Protocol):
    async def verify(self, *, tenant_id: str, case: GovernedCase, outcome: str, grants: list[str]) -> DecisionCheck: ...


class RequireDecisionGrant:
    """The default: no decision grant can be verified yet, so every decision is refused."""

    async def verify(self, *, tenant_id: str, case: GovernedCase, outcome: str, grants: list[str]) -> DecisionCheck:
        return DecisionCheck(allowed=False, reason="decision_required")


def semantic_action(case: GovernedCase, outcome: str) -> dict[str, Any]:
    """The action a decision grant must be bound to (hashed by the grant issuer, never by this module)."""
    subject = case.subject or {}
    return {
        "case_id": case.case_ref,
        "action": "case_decision",
        "decision": outcome,
        "subject": f"{subject.get('provider', '')}:{subject.get('provider_ref', '')}",
    }


async def record_decision(
    session: AsyncSession,
    case: GovernedCase,
    *,
    outcome: str,
    grants: list[str],
    verifier: DecisionVerifier,
    actor: str,
    now: datetime | None = None,
) -> GovernedCase:
    if outcome not in DECISION_OUTCOMES:
        raise CaseError("decision_outcome_invalid", outcome, status=422)
    if case.state != CaseState.AWAITING_DECISION:
        raise CaseError("transition_not_allowed", f"{case.state} -> decided")
    try:
        check = await verifier.verify(tenant_id=str(case.tenant_id), case=case, outcome=outcome, grants=list(grants))
    # enterprise-gate: broad-except-ok reason=verifier-failure-fails-closed-as-decision-invalid
    except Exception as exc:
        logger.error("case_decision_verifier_failed", case_ref=case.case_ref, error=type(exc).__name__)
        raise CaseError("decision_invalid", "verification_unavailable", status=403) from exc
    if not isinstance(check, DecisionCheck) or not check.allowed or not check.approvers:
        reason = check.reason if isinstance(check, DecisionCheck) and check.reason else "decision_required"
        logger.warning("case_decision_refused", case_ref=case.case_ref, reason=reason)
        raise CaseError(reason, status=403)
    at = now or datetime.now(UTC)
    decision = {
        "outcome": outcome,
        "approvers": [{"approver": approver, "decision_grant_id": grant} for approver, grant in check.approvers],
        "decided_at": at.isoformat(),
    }
    try:
        validate("business_case", {**business_case_document(case), "state": "decided", "decision": decision})
    except DomainSchemaError as exc:
        raise CaseError("decision_document_invalid", "; ".join(exc.errors[:3]), status=422) from exc
    case.decision = decision
    await transition(session, case, CaseState.DECIDED, actor=actor, reason=f"decision_{outcome}", now=at)
    return case
