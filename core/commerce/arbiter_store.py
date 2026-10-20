# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Arbiter decisions on disk: persist and seal each decision, and the executor-side guard.

``persist_decision`` writes the decision row (``commerce_arbiter_decisions``)
and its ledger row in the caller's transaction. ``require_allow_decision`` is
the guard every executor calls before acting on a ``decision_id``: it consumes
the decision in one conditional UPDATE, so of two concurrent executors exactly
one wins, and it refuses unless the decision is an unexpired, unconsumed
``allow`` of the same tenant, for the same action and the same payload hash.

``escalate_refusal`` is the merchant's ``escalate_to_human`` option: it queues
a review item in ``hitl_queue`` after a refusal was sealed. It never changes
the outcome, nothing waits for it, and a person's later review of the item
does not bind or reverse any decision.

Seam for operator decisions and the Grantex countersignature: ``get_decision``
reads one row by id within the tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.commerce.arbiter import Decision
from core.commerce.ledger import record_commerce_event

logger = structlog.get_logger()

DECISION_EVENT = "commerce.arbiter.decision"
CONSUMED_EVENT = "commerce.arbiter.decision.consumed"
ESCALATION_TRIGGER = "commerce_arbiter_refusal"
ESCALATION_EXPIRES = timedelta(days=7)

DECISION_REFUSAL_CODES: tuple[str, ...] = (
    "decision_id_missing",
    "decision_not_found",
    "decision_not_allow",
    "decision_action_mismatch",
    "decision_payload_mismatch",
    "decision_expired",
    "decision_consumed",
    "grantex_grant_missing",
)


class DecisionRefusedError(Exception):
    """The presented ``decision_id`` does not authorise this action; ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _tenant_uuid(tenant_id: Any) -> uuid.UUID:
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


async def persist_decision(session: Any, decision: Decision) -> dict[str, Any]:
    """Insert the decision row (idempotent on ``decision_id``) and seal it to the ledger; returns the ledger ref."""
    from sqlalchemy.dialects.postgresql import insert

    from core.models.commerce_arbiter import CommerceArbiterDecisionRow

    values = {
        "decision_id": decision.decision_id,
        "tenant_id": _tenant_uuid(decision.tenant_id),
        "merchant_id": decision.merchant_id,
        "seller_agent_id": decision.seller_agent_id,
        "buyer_agent_id": decision.buyer_agent_id,
        "action": decision.action,
        "payload_hash": decision.payload_hash,
        "outcome": decision.outcome,
        "rules_evaluated": list(decision.rules_evaluated),
        "failing_rule": decision.failing_rule,
        "rule_results": [result.to_dict() for result in decision.rule_results],
        "evidence": {
            **decision.evidence,
            "buyer_authority_source": decision.buyer_authority_source,
            "mandate_hashes": decision.extra.get("mandate_hashes"),
            "mandate_jtis": decision.extra.get("mandate_jtis"),
            "presence": decision.extra.get("presence"),
            "authority_verification_ref": decision.extra.get("authority_verification_ref"),
        },
        "evidence_hashes": list(decision.evidence_hashes),
        "policy_version": decision.policy_version,
        "decided_at": decision.decided_at,
        "expires_at": decision.expires_at,
    }
    await session.execute(
        insert(CommerceArbiterDecisionRow).values(**values).on_conflict_do_nothing(index_elements=["decision_id"])
    )
    entry = await record_commerce_event(
        session,
        tenant_id=decision.tenant_id,
        event=DECISION_EVENT,
        outcome=decision.outcome,
        merchant_id=decision.merchant_id,
        seller_agent_id=decision.seller_agent_id,
        buyer_agent_id=decision.buyer_agent_id,
        payload_hash=decision.payload_hash,
        resource_id=decision.decision_id,
        details={"decision": decision.to_public()},
    )
    return entry.to_ref()


async def get_decision(session: Any, tenant_id: Any, decision_id: str) -> Any:
    """The decision row with this id in the tenant, or None."""
    from sqlalchemy import select

    from core.models.commerce_arbiter import CommerceArbiterDecisionRow

    return (
        await session.execute(
            select(CommerceArbiterDecisionRow).where(
                CommerceArbiterDecisionRow.decision_id == decision_id,
                CommerceArbiterDecisionRow.tenant_id == _tenant_uuid(tenant_id),
            )
        )
    ).scalar_one_or_none()


async def require_allow_decision(
    session: Any,
    *,
    tenant_id: Any,
    decision_id: str | None,
    payload_hash: str,
    action: str,
    now: datetime | None = None,
    require_grantex_grant: bool | None = None,
) -> Any:
    """Consume ``decision_id`` for ``action`` on ``payload_hash`` or raise ``DecisionRefusedError``.

    Single use: the UPDATE only matches an unconsumed row, so two executors
    presenting the same decision cannot both pass. The consumption is sealed to
    the ledger in the same transaction. With ``AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT``
    on (unset: strict runtimes), only an allow with a verified Grantex grant
    attached matches; one without is refused ``grantex_grant_missing`` at once.
    """
    from sqlalchemy import update

    from core.config import arbiter_require_grantex_grant_enabled
    from core.models.commerce_arbiter import CommerceArbiterDecisionRow

    if not decision_id:
        raise DecisionRefusedError("decision_id_missing", "An allow decision from the commerce arbiter is required.")
    moment = now or datetime.now(UTC)
    tenant = _tenant_uuid(tenant_id)
    table = CommerceArbiterDecisionRow
    grant_required = arbiter_require_grantex_grant_enabled() if require_grantex_grant is None else require_grantex_grant
    conditions = [
        table.decision_id == decision_id,
        table.tenant_id == tenant,
        table.action == action,
        table.payload_hash == payload_hash,
        table.outcome == "allow",
        table.consumed_at.is_(None),
        table.expires_at > moment,
    ]
    if grant_required:
        conditions.append(table.grantex_grant_jti.is_not(None))
    consumed = (
        await session.execute(
            update(table)
            .where(*conditions)
            .values(consumed_at=moment)
            .returning(
                table.decision_id, table.merchant_id, table.seller_agent_id, table.buyer_agent_id, table.evidence
            )
        )
    ).first()
    if consumed is None:
        row = await get_decision(session, tenant, decision_id)
        raise _refusal_for(row, action=action, payload_hash=payload_hash, now=moment, grant_required=grant_required)
    await record_commerce_event(
        session,
        tenant_id=tenant,
        event=CONSUMED_EVENT,
        outcome="consumed",
        merchant_id=consumed.merchant_id,
        seller_agent_id=consumed.seller_agent_id,
        buyer_agent_id=consumed.buyer_agent_id,
        payload_hash=payload_hash,
        resource_id=decision_id,
        details={"decision_id": decision_id, "action": action},
        now=moment,
    )
    return consumed


def _refusal_for(
    row: Any, *, action: str, payload_hash: str, now: datetime, grant_required: bool = False
) -> DecisionRefusedError:
    if row is None:
        return DecisionRefusedError("decision_not_found", "The decision does not exist in this tenant.")
    if row.outcome != "allow":
        return DecisionRefusedError("decision_not_allow", "The commerce arbiter refused this decision.")
    if row.action != action:
        return DecisionRefusedError("decision_action_mismatch", "The decision was made for a different action.")
    if row.payload_hash != payload_hash:
        return DecisionRefusedError("decision_payload_mismatch", "The decision was made for a different payload.")
    if row.consumed_at is not None:
        return DecisionRefusedError("decision_consumed", "The decision was already used.")
    if row.expires_at <= now:
        return DecisionRefusedError("decision_expired", "The decision has expired; request a new one.")
    if grant_required and getattr(row, "grantex_grant_jti", None) is None:
        return DecisionRefusedError(
            "grantex_grant_missing",
            "The allow decision has no verified Grantex countersignature; request a new decision.",
        )
    return DecisionRefusedError("decision_consumed", "The decision was already used.")


def refusal_blocker(decision: Decision, *, unblock_command: str, doc_reference: str) -> dict[str, Any]:
    """A refusal in the runtime's blocker-dict shape."""
    return {
        "code": "arbiter_refused",
        "failing_rule": decision.failing_rule,
        "reason": decision.reason,
        "decision_id": decision.decision_id,
        "owner": "Buyer agent, merchant operator or Grantex consent owner, per the failing rule",
        "action": f"The commerce arbiter refused: {decision.failing_rule} ({decision.reason}).",
        "required_config": _REQUIRED_CONFIG.get(decision.failing_rule or "", "See the arbiter rules"),
        "unblock_command": unblock_command,
        "doc_reference": doc_reference,
    }


def decision_refusal_blocker(exc: DecisionRefusedError, *, unblock_command: str, doc_reference: str) -> dict[str, Any]:
    return {
        "code": exc.code,
        "owner": "Buyer agent or calling channel",
        "action": exc.message,
        "required_config": "decision_id of an unexpired, unused allow decision for this exact payload",
        "unblock_command": unblock_command,
        "doc_reference": doc_reference,
    }


_REQUIRED_CONFIG: dict[str, str] = {
    "mandate_present_unexpired": "buyer_authority.mandate_ref, mandate_hash, mandate_expires_at",
    "consent_active": "buyer_authority.consent_ref and an active consent_status",
    "passport_amount_cap": (
        "buyer_authority.passport_ref, passport_max_amount_minor_units, passport_currency, passport_expires_at"
    ),
    "buyer_authority_verified": (
        "Intent, Cart and Payment mandates from Grantex, verified by the server (UCP Complete with "
        "ai.agenticorg.commerce.mandate_request); AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false "
        "accepts caller-asserted authority"
    ),
    "human_not_present_limits": (
        "merchant config provider_policy.arbiter.human_not_present.max_order_amount_minor_units, and an "
        "intent mandate max_amount covering the order total"
    ),
    "oacp_artifacts_valid": "Verified, fresh, unrevoked OACP artifacts for every required family",
    "merchant_limits": (
        "merchant config provider_policy.arbiter: max_order_amount_minor_units, currency_allowlist, "
        "max_quantity_per_order"
    ),
}


async def escalate_refusal(
    *,
    tenant_id: Any,
    decision: Decision,
    escalate_to_human: bool,
    review_agent_id: str | None,
) -> dict[str, Any] | None:
    """Queue a refused decision for review when the merchant asked for it; None when not asked.

    The decision is already sealed and returned as it is: this only leaves an
    item in ``hitl_queue`` (owned by the merchant's review agent) and returns
    its reference. A failure to queue is logged and reported, never raised.
    """
    if not escalate_to_human or decision.outcome != "refuse":
        return None
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.hitl import HITLQueue

    tenant = _tenant_uuid(tenant_id)
    try:
        agent_uuid = uuid.UUID(str(review_agent_id))
    except (TypeError, ValueError):
        logger.warning("commerce_arbiter_escalation_review_agent_missing", decision_id=decision.decision_id)
        return {"queued": False, "reason": "review_agent_missing", "hitl_ref": None}
    try:
        async with get_tenant_session(tenant) as session:
            agent = (
                await session.execute(select(Agent.id).where(Agent.id == agent_uuid, Agent.tenant_id == tenant))
            ).scalar_one_or_none()
            if agent is None:
                return {"queued": False, "reason": "review_agent_not_found", "hitl_ref": None}
            item = HITLQueue(
                id=uuid.uuid4(),
                tenant_id=tenant,
                agent_id=agent_uuid,
                workflow_run_id=None,
                title=f"Commerce arbiter refused {decision.action}: {decision.failing_rule}"[:500],
                trigger_type=ESCALATION_TRIGGER,
                priority="normal",
                assignee_role="commerce_operator",
                decision_options={"options": ["acknowledge"]},
                context={
                    "decision": decision.to_public(),
                    "merchant_id": decision.merchant_id,
                    "seller_agent_id": decision.seller_agent_id,
                    "buyer_agent_id": decision.buyer_agent_id,
                    "review_only": True,
                    "outcome_binding": False,
                },
                expires_at=datetime.now(UTC) + ESCALATION_EXPIRES,
            )
            session.add(item)
            await record_commerce_event(
                session,
                tenant_id=tenant,
                event="commerce.arbiter.decision.escalated",
                outcome="escalated",
                merchant_id=decision.merchant_id,
                seller_agent_id=decision.seller_agent_id,
                buyer_agent_id=decision.buyer_agent_id,
                payload_hash=decision.payload_hash,
                resource_id=decision.decision_id,
                details={"hitl_ref": str(item.id), "decision_id": decision.decision_id},
            )
            hitl_ref = str(item.id)
    # enterprise-gate: broad-except-ok reason=escalation-is-an-optional-review-copy-the-sealed-refusal-stands
    except Exception as exc:
        logger.warning("commerce_arbiter_escalation_failed", error_type=type(exc).__name__)
        return {"queued": False, "reason": "queue_unavailable", "hitl_ref": None}
    return {"queued": True, "reason": "escalate_to_human", "hitl_ref": hitl_ref}
