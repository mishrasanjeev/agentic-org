# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One turn of the customer agent: the conversational services under the autonomy ladder.

The dialogue engine already collects slots, clarifies and asks for
confirmation before any transaction. The customer agent keeps that engine
whole and adds the ladder around its confirmation stage:

- when the dialogue reaches ``confirm`` for a transaction, the ladder decides:
  below level 2 the action is refused with its explanation and the dialogue
  cancelled; at level 4 inside the mandate the confirmation is given at once
  and the action runs; otherwise a prepared action is recorded and the card
  waits for one tap (level 2) or a step-up (level 3);
- when the customer confirms (``yes``, the card's tap, or the confirm
  endpoint), the ladder is asked again before the engine executes: a step-up
  level needs a signed approval bound to the prepared action, verified with
  the bank's assertion keys, or nothing runs;
- reads run as the engine runs them; every answer carries a "why this".

Executed, cancelled, refused and failed actions are recorded in
``customer_actions`` with the explanation shown and the approval given, so a
customer's actions list and the bank's evidence agree.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.conversation import dialogue as dialogue_engine
from core.conversation import runtime
from core.conversation.intents import INTENTS
from core.customer_agent import autonomy, explanations, sessions

logger = structlog.get_logger()

NO_AGENT_MESSAGE = "The bank has not connected an agent for this request yet; a person can help."
CUSTOMER_SESSION_CHANNEL = "customer"
APPROVAL_METHODS: tuple[str, ...] = ("passkey", "biometric", "otp", "bank_step_up")


class ApprovalError(ValueError):
    """The approval evidence does not authorise the action."""


def user_id_for(session_row: Any) -> str:
    return f"customer:{session_row.customer_ref}"


def session_key_for(session_row: Any) -> str:
    """One dialogue per customer across channels: the key names the customer, not the channel."""
    return runtime.session_key(
        CUSTOMER_SESSION_CHANNEL, sessions.company_id(), sessions.agent_id(), user_id_for(session_row)
    )


# ---------------------------------------------------------------------------
# Storage seams (replaced by tests)
# ---------------------------------------------------------------------------


async def load_mandate(tenant_id: uuid.UUID, customer_ref: str, klass: str) -> Any | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerMandate

    async with get_tenant_session(tenant_id) as session:
        return (
            await session.execute(
                select(CustomerMandate)
                .where(
                    CustomerMandate.tenant_id == tenant_id,
                    CustomerMandate.customer_ref == customer_ref,
                    CustomerMandate.task_class == klass,
                    CustomerMandate.status == "active",
                )
                .order_by(CustomerMandate.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def spent_in_period(tenant_id: uuid.UUID, customer_ref: str, klass: str, since: datetime) -> float:
    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    async with get_tenant_session(tenant_id) as session:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(CustomerAction.amount), 0.0)).where(
                    CustomerAction.tenant_id == tenant_id,
                    CustomerAction.customer_ref == customer_ref,
                    CustomerAction.task_class == klass,
                    CustomerAction.status == "executed",
                    CustomerAction.decided_at >= since,
                )
            )
        ).scalar()
    return float(total or 0.0)


async def insert_action(row: Any) -> Any:
    from core.database import get_tenant_session

    async with get_tenant_session(row.tenant_id) as session:
        session.add(row)
    return row


async def supersede_prepared(tenant_id: uuid.UUID, customer_ref: str, intent: str, keep_id: uuid.UUID) -> int:
    """Mark every other prepared action of this intent superseded: one card per intent is confirmable."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    count = 0
    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(CustomerAction).where(
                    CustomerAction.tenant_id == tenant_id,
                    CustomerAction.customer_ref == customer_ref,
                    CustomerAction.intent == intent,
                    CustomerAction.status == "prepared",
                    CustomerAction.id != keep_id,
                )
            )
        ).scalars()
        for row in rows:
            row.status = "superseded"
            row.execution = {"status": "superseded", "reason": "replaced_by_a_newer_card"}
            row.decided_at = datetime.now(UTC)
            count += 1
    return count


def same_parameters(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Whether two slot sets describe the same action (values compared as text, empty values ignored)."""

    def norm(d: dict[str, Any] | None) -> dict[str, str]:
        return {k: str(v) for k, v in (d or {}).items() if v not in (None, "", [], {})}

    return norm(a) == norm(b)


async def latest_prepared(tenant_id: uuid.UUID, customer_ref: str, intent: str) -> Any | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    async with get_tenant_session(tenant_id) as session:
        return (
            await session.execute(
                select(CustomerAction)
                .where(
                    CustomerAction.tenant_id == tenant_id,
                    CustomerAction.customer_ref == customer_ref,
                    CustomerAction.intent == intent,
                    CustomerAction.status == "prepared",
                )
                .order_by(CustomerAction.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def settle_action(tenant_id: uuid.UUID, action_id: uuid.UUID, **fields: Any) -> Any | None:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.customer_agent import CustomerAction

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(CustomerAction).where(CustomerAction.tenant_id == tenant_id, CustomerAction.id == action_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        for name, value in fields.items():
            setattr(row, name, value)
        row.decided_at = datetime.now(UTC)
    return row


# ---------------------------------------------------------------------------
# The ladder around the engine
# ---------------------------------------------------------------------------


async def decide_for(session_row: Any, intent_name: str, slots: dict[str, Any]) -> tuple[autonomy.Decision, Any | None]:
    klass = autonomy.task_class(intent_name)
    mandate = await load_mandate(session_row.tenant_id, session_row.customer_ref, klass)
    spent = 0.0
    if mandate is not None and getattr(mandate, "period_cap", None) is not None:
        spent = await spent_in_period(
            session_row.tenant_id, session_row.customer_ref, klass, autonomy.period_start(mandate)
        )
    return autonomy.decide(intent_name, slots, mandate, spent_in_period=spent), mandate


def _approval_key(token: str) -> Any:
    return sessions._signing_key(token)


def verify_approval(approval: dict[str, Any] | None, *, session_row: Any, action: Any) -> dict[str, Any]:
    """The approval evidence for a step-up, verified: a bank-signed assertion bound to this action and customer."""
    import jwt

    if not isinstance(approval, dict) or not approval:
        raise ApprovalError("this action needs the bank's approval of the specific action before it runs")
    method = str(approval.get("method") or "").lower()
    if method not in APPROVAL_METHODS:
        raise ApprovalError("approval.method must name the step-up the bank performed")
    assertion = str(approval.get("assertion") or "")
    issuer = str(settings.customer_agent_assertion_issuer or "").strip()
    audience = str(settings.customer_agent_assertion_audience or "").strip()
    if not assertion or not issuer or not audience:
        raise ApprovalError("a signed approval assertion is required and the bank's keys must be configured")
    try:
        claims = jwt.decode(
            assertion,
            _approval_key(assertion),
            algorithms=list(sessions.ALGORITHMS),
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub"]},
            leeway=30,
        )
    except jwt.PyJWTError as exc:
        raise ApprovalError(f"approval verification failed: {exc}") from exc
    # enterprise-gate: broad-except-ok reason=jwks-outage-fails-closed-and-logged
    except Exception as exc:
        logger.warning("customer_approval_key_unavailable", error_type=type(exc).__name__)
        raise ApprovalError("the bank's signing keys could not be read") from exc
    if str(claims.get("sub") or "") != session_row.customer_ref:
        raise ApprovalError("the approval names a different customer")
    if str(claims.get("action_id") or "") != str(action.id):
        raise ApprovalError("the approval is bound to a different action")
    claimed_amount = claims.get("amount")
    if claimed_amount is not None and action.amount is not None and float(claimed_amount) != float(action.amount):
        raise ApprovalError("the approval names a different amount")
    return {
        "method": method,
        "subject": claims.get("sub"),
        "action_id": claims.get("action_id"),
        "issued_at": claims.get("iat"),
        "verified_at": datetime.now(UTC).isoformat(),
    }


async def execution_context(tenant_id: uuid.UUID) -> runtime.ExecutionContext | None:
    """The configured customer agent's tools, connectors and run grant; None when the bank has not connected one."""
    agent_id = sessions.agent_id()
    if not agent_id:
        return None
    from sqlalchemy import select

    from api.v1.agents import _resolve_connector_configs
    from auth.run_grants import resolve_run_grant
    from core.database import get_tenant_session
    from core.governance.agent_status import refusal_for
    from core.models.agent import Agent

    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        logger.warning("customer_agent_id_invalid")
        return None
    company = sessions.company_id()
    company_uuid = uuid.UUID(company) if company else None
    async with get_tenant_session(tenant_id, company_uuid) as session:
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == aid,
                    Agent.tenant_id == tenant_id,
                    *([Agent.company_id == company_uuid] if company_uuid else []),
                )
            )
        ).scalar_one_or_none()
        if agent is None or refusal_for(agent.status) is not None:
            return None
        connector_ids = list(agent.connector_ids or [])
        context = runtime.ExecutionContext(
            tenant_id=str(tenant_id),
            agent_id=str(agent.id),
            agent_type=str(agent.agent_type or ""),
            domain=str(agent.domain or ""),
            authorized_tools=list(agent.authorized_tools or []),
            company_id=str(company_uuid) if company_uuid else None,
            bindings=runtime.bindings_of(agent.config),
        )
        grantex_config = (agent.config or {}).get("grantex") or {}
    if connector_ids:
        context.connector_config, context.connector_names = await _resolve_connector_configs(
            tenant_id=str(tenant_id), connector_ids=connector_ids, agent_level_config=None, company_id=company_uuid
        )
    context.run_grant = await resolve_run_grant(
        tenant_id=str(tenant_id), agent_id=str(aid), grantex_config=grantex_config, runtime=runtime.RUNTIME
    )
    return context


@dataclass
class Turn:
    """What a turn hands back to the channel."""

    payload: dict[str, Any]
    card: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    decision: autonomy.Decision | None = None
    refusal: str | None = None
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.payload)
        out["card"] = self.card
        out["explanation"] = self.explanation
        out["autonomy"] = self.decision.to_dict() if self.decision else None
        out["refusal"] = self.refusal
        out["approval_required"] = self.approval_required
        return out


def _amount_of(slots: dict[str, Any]) -> float | None:
    try:
        return float(slots["amount"]) if slots.get("amount") is not None else None
    except (TypeError, ValueError):
        return None


async def _record_prepared(
    session_row: Any, outcome: Any, decision: autonomy.Decision, mandate: Any, channel: str
) -> Any:
    from core.models.customer_agent import CustomerAction

    slots = dict(outcome.slots or {})
    row = CustomerAction(
        id=uuid.uuid4(),
        tenant_id=session_row.tenant_id,
        customer_ref=session_row.customer_ref,
        session_id=session_row.id,
        task_class=decision.task_class,
        intent=str(outcome.intent or ""),
        parameters=slots,
        amount=_amount_of(slots),
        status="prepared",
        level_applied=decision.level,
        requires=decision.requires,
        explanation=explanations.why_this(outcome.intent, slots, decision, mandate),
        channel=channel[:16],
    )
    row = await insert_action(row)
    await supersede_prepared(session_row.tenant_id, session_row.customer_ref, row.intent, row.id)
    return row


async def _record_direct(
    session_row: Any, outcome: Any, decision: autonomy.Decision, mandate: Any, channel: str, status: str, execution: Any
) -> Any:
    row = await _record_prepared(session_row, outcome, decision, mandate, channel)
    return await settle_action(session_row.tenant_id, row.id, status=status, execution=_summary(execution))


def _summary(execution: dict[str, Any] | None) -> dict[str, Any] | None:
    if execution is None:
        return None
    return {k: v for k, v in execution.items() if k in ("status", "intent", "message", "reference")} | (
        {"reference": _reference(execution)} if _reference(execution) else {}
    )


def _reference(execution: dict[str, Any]) -> str | None:
    result = execution.get("result")
    if isinstance(result, dict):
        for key in ("reference", "id", "transaction_id"):
            if result.get(key):
                return str(result[key])
    return None


def _status_of(execution: dict[str, Any] | None) -> str:
    status = (execution or {}).get("status")
    if status == "executed":
        return "executed"
    if status == "superseded":
        return "superseded"
    return "failed"


async def run_customer_turn(
    session_row: Any, text: str, *, channel: str = "web", approval: dict[str, Any] | None = None
) -> Turn:
    """Advance the customer's dialogue by one turn under the autonomy ladder."""
    tid = session_row.tenant_id
    key = session_key_for(session_row)
    user_id = user_id_for(session_row)
    agent_id = sessions.agent_id() or None
    dialogue = await runtime.load_dialogue(tid, key)
    held = await runtime.held_turn(tid, key, text, dialogue)
    if held is not None:
        return Turn(payload=dict(held))
    context = await execution_context(tid)
    intent_name = dialogue.intent
    current = INTENTS.get(intent_name or "")
    decision: autonomy.Decision | None = None
    mandate: Any = None

    # A refusal of a transaction: the prepared card is settled as cancelled before the engine resets.
    if (
        dialogue.stage == dialogue_engine.STAGE_CONFIRMING
        and current is not None
        and current.risk == "transact"
        and dialogue_engine._NO_RE.match(text)
    ):
        prepared = await latest_prepared(tid, session_row.customer_ref, current.name)
        if prepared is not None:
            prepared = await settle_action(
                tid, prepared.id, status="cancelled", execution={"status": "cancelled", "by": "customer"}
            )
        outcome, execution = await runtime.run_turn(
            tid,
            key,
            dialogue,
            text,
            context,
            user_id=user_id,
            agent_id=agent_id,
            channel=channel,
            no_agent_message=NO_AGENT_MESSAGE,
        )
        payload = await runtime.finish_turn(
            tid,
            key,
            dialogue,
            outcome,
            execution,
            text=text,
            user_id=user_id,
            agent_id=agent_id or "",
            channel=channel,
            context=context,
        )
        card = None
        if prepared is not None:
            decision, _mandate = await decide_for(session_row, current.name, dict(prepared.parameters or {}))
            card = explanations.card_for(prepared, decision, None)
        return Turn(payload=payload, card=card)

    # A confirmation of a transaction: ask the ladder before the engine executes.
    if (
        dialogue.stage == dialogue_engine.STAGE_CONFIRMING
        and current is not None
        and current.risk == "transact"
        and dialogue_engine._YES_RE.match(text)
    ):
        decision, mandate = await decide_for(session_row, current.name, dialogue.slots)
        prepared = await latest_prepared(tid, session_row.customer_ref, current.name)
        if not decision.allowed:
            return await _refuse(session_row, key, dialogue, decision, mandate, context, channel, prepared)
        approval_record: dict[str, Any] | None = None
        if decision.requires == autonomy.REQUIRES_STEP_UP:
            if prepared is None:
                prepared = await _record_prepared(session_row, _as_outcome(dialogue), decision, mandate, channel)
            try:
                approval_record = await asyncio.to_thread(
                    verify_approval, approval, session_row=session_row, action=prepared
                )
            except ApprovalError as exc:
                card = explanations.card_for(prepared, decision, dialogue_engine.summary_of(current, dialogue.slots))
                return Turn(
                    payload={
                        "answer": str(exc),
                        "confidence": 1.0,
                        "outcome": {
                            "kind": "approval_required",
                            "text": str(exc),
                            "intent": current.name,
                            "slots": dict(dialogue.slots or {}),
                            "missing": [],
                        },
                        "dialogue": runtime.dialogue_view(dialogue),
                        "tool_calls": None,
                        "session_key": key,
                    },
                    card=card,
                    explanation=dict(prepared.explanation or {}),
                    decision=decision,
                    approval_required=True,
                )
        outcome, execution = await runtime.run_turn(
            tid,
            key,
            dialogue,
            text,
            context,
            user_id=user_id,
            agent_id=agent_id,
            channel=channel,
            no_agent_message=NO_AGENT_MESSAGE,
        )
        payload = await runtime.finish_turn(
            tid,
            key,
            dialogue,
            outcome,
            execution,
            text=text,
            user_id=user_id,
            agent_id=agent_id or "",
            channel=channel,
            context=context,
        )
        if prepared is not None:
            prepared = await settle_action(
                tid, prepared.id, status=_status_of(execution), execution=_summary(execution), approval=approval_record
            )
        else:
            prepared = await _record_direct(
                session_row, outcome, decision, mandate, channel, _status_of(execution), execution
            )
        card = explanations.card_for(prepared, decision, outcome.summary) if prepared is not None else None
        return Turn(
            payload=payload,
            card=card,
            explanation=dict(getattr(prepared, "explanation", None) or {}),
            decision=decision,
        )

    outcome, execution = await runtime.run_turn(
        tid,
        key,
        dialogue,
        text,
        context,
        user_id=user_id,
        agent_id=agent_id,
        channel=channel,
        no_agent_message=NO_AGENT_MESSAGE,
    )
    resolved = INTENTS.get(outcome.intent or "")
    decision = None
    mandate = None
    card = None
    explanation = None
    if resolved is not None and resolved.risk == "transact" and outcome.kind == "confirm":
        decision, mandate = await decide_for(session_row, resolved.name, outcome.slots)
        if not decision.allowed:
            return await _refuse(session_row, key, dialogue, decision, mandate, context, channel, None)
        if decision.level == 4 and decision.requires == autonomy.REQUIRES_NONE:
            # Within the mandate: the confirmation is given at once and the action runs in this turn.
            outcome, execution = await runtime.run_turn(
                tid,
                key,
                dialogue,
                "yes",
                context,
                user_id=user_id,
                agent_id=agent_id,
                channel=channel,
                no_agent_message=NO_AGENT_MESSAGE,
            )
            payload = await runtime.finish_turn(
                tid,
                key,
                dialogue,
                outcome,
                execution,
                text=text,
                user_id=user_id,
                agent_id=agent_id or "",
                channel=channel,
                context=context,
            )
            row = await _record_direct(
                session_row, outcome, decision, mandate, channel, _status_of(execution), execution
            )
            return Turn(
                payload=payload,
                card=explanations.card_for(row, decision, outcome.summary),
                explanation=dict(row.explanation or {}),
                decision=decision,
            )
        row = await _record_prepared(session_row, outcome, decision, mandate, channel)
        card = explanations.card_for(row, decision, outcome.summary)
        explanation = dict(row.explanation or {})
    elif resolved is not None and outcome.kind in ("execute", "answer", "ask", "clarify"):
        decision, mandate = await decide_for(session_row, resolved.name, outcome.slots)
        explanation = explanations.why_this(resolved.name, outcome.slots, decision, mandate)
    payload = await runtime.finish_turn(
        tid,
        key,
        dialogue,
        outcome,
        execution,
        text=text,
        user_id=user_id,
        agent_id=agent_id or "",
        channel=channel,
        context=context,
    )
    if decision is not None and card is not None and decision.requires == autonomy.REQUIRES_STEP_UP:
        return Turn(payload=payload, card=card, explanation=explanation, decision=decision, approval_required=True)
    return Turn(payload=payload, card=card, explanation=explanation, decision=decision)


def _as_outcome(dialogue: Any) -> Any:
    return dialogue_engine.Outcome(kind="confirm", text="", intent=dialogue.intent, slots=dict(dialogue.slots or {}))


async def _refuse(
    session_row: Any,
    key: str,
    dialogue: Any,
    decision: autonomy.Decision,
    mandate: Any,
    context: Any,
    channel: str,
    prepared: Any,
) -> Turn:
    """Below level 2: refused with its explanation and the dialogue cancelled, so nothing stays confirmable."""
    tid = session_row.tenant_id
    user_id = user_id_for(session_row)
    agent_id = sessions.agent_id() or None
    intent = dialogue.intent
    slots = dict(dialogue.slots or {})
    explanation = explanations.why_this(intent, slots, decision, mandate)
    outcome, execution = await runtime.run_turn(
        tid,
        key,
        dialogue,
        "no",
        context,
        user_id=user_id,
        agent_id=agent_id,
        channel=channel,
        no_agent_message=NO_AGENT_MESSAGE,
    )
    payload = await runtime.finish_turn(
        tid,
        key,
        dialogue,
        outcome,
        execution,
        text="no",
        user_id=user_id,
        agent_id=agent_id or "",
        channel=channel,
        context=context,
    )
    if prepared is not None:
        await settle_action(
            tid, prepared.id, status="refused", execution={"status": "refused", "reason": decision.reason}
        )
    else:
        from core.models.customer_agent import CustomerAction

        await insert_action(
            CustomerAction(
                id=uuid.uuid4(),
                tenant_id=tid,
                customer_ref=session_row.customer_ref,
                session_id=session_row.id,
                task_class=decision.task_class,
                intent=str(intent or ""),
                parameters=slots,
                amount=_amount_of(slots),
                status="refused",
                level_applied=decision.level,
                requires=decision.requires,
                explanation=explanation,
                execution={"status": "refused", "reason": decision.reason},
                channel=channel[:16],
                decided_at=datetime.now(UTC),
            )
        )
    payload["answer"] = (
        f"I can explain this, but I am not set to prepare it for you: {decision.reason}. "
        "You can raise the level for this task in your permissions, or ask for a person."
    )
    return Turn(payload=payload, explanation=explanation, decision=decision, refusal=decision.reason)
