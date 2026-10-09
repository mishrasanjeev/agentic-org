# SPDX-License-Identifier: Apache-2.0
"""The conversation runtime: sessions, the chat hook, and execution through an agent's governed tools.

A session is one dialogue per channel, company, agent and user, kept in
``conversation_sessions`` and idle-expired. ``chat_turn`` is what the chat
route calls: it answers only when a banking intent is recognised or a dialogue
is in progress, and otherwise hands the turn back to the agent path unchanged.
A confirmed action runs through the agent's own governed tools
(``core/langgraph/tool_adapter.py``): the grant is checked first
(``auth/run_grants.py``), then the tool bound to the intent is invoked with
the collected slots. Nothing runs before the user confirms.

Behind ``AGENTICORG_CONVERSATION_V2_ENABLED`` (off by default).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings
from core.conversation import dialogue as dialogue_engine
from core.conversation import intents as catalogue
from core.conversation.dialogue import Dialogue, Outcome
from core.conversation.intents import INTENTS, MIN_CONFIDENCE

logger = structlog.get_logger()

CONFIG_KEY = "conversation"  # agent.config["conversation"] = {"bindings": {intent: "connector:tool"}}
IDLE_SECONDS = 1800  # a dialogue idle this long starts over
RESULT_CHARS = 2_000
RUNTIME = "conversation"

# The tool names an intent's action binds to when the agent declares none, by preference.
ACTIONS: dict[str, tuple[str, ...]] = {
    "balance_enquiry": ("get_balance", "get_account_balance", "account_balance", "fetch_balance", "check_balance"),
    "mini_statement": (
        "get_statement",
        "mini_statement",
        "fetch_bank_statement",
        "get_transactions",
        "list_transactions",
    ),
    "card_block": ("block_card", "card_block", "freeze_card", "hotlist_card"),
    "fund_transfer": ("transfer_funds", "fund_transfer", "initiate_transfer", "make_transfer", "create_transfer"),
    # Only tools that pay a biller. A generic payment or payment-intent primitive
    # (a provider's ``create_payment_intent``) ignores the biller and reads the
    # amount in its own currency unit, so it is never bound by alias; an agent
    # that really pays bills through one declares the binding explicitly.
    "bill_payment": ("pay_bill", "bill_payment"),
    "loan_enquiry": ("loan_enquiry", "get_loan_offers", "loan_eligibility", "check_loan_eligibility"),
    "dispute_transaction": ("raise_dispute", "create_dispute", "dispute_transaction"),
    "application_status": ("application_status", "get_application_status", "track_application"),
    "loan_application": ("apply_loan", "create_loan_application", "submit_loan_application", "loan_application"),
    "card_replacement": ("request_replacement_card", "replace_card", "card_replacement", "reissue_card"),
}


def enabled() -> bool:
    return bool(getattr(settings, "conversation_v2_enabled", False))


def session_key(channel: str, company_id: str, agent_id: str, user_id: str) -> str:
    return f"{channel or 'web'}:{company_id or '-'}:{agent_id or '-'}:u:{user_id}"


def bindings_of(config: dict[str, Any] | None) -> dict[str, str]:
    """The intent-to-tool bindings an agent's config declares."""
    section = (config or {}).get(CONFIG_KEY) if isinstance(config, dict) else None
    raw = section.get("bindings") if isinstance(section, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}


def _bare(ref: str) -> str:
    """The tool's own name in any spelling (``zoho:tool``, ``zoho__tool``, ``tool:zoho:perm:tool``)."""
    name = str(ref).strip()
    if name.startswith("tool:"):
        name = name.rsplit(":", 1)[-1]
    for sep in (":", "__", "."):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return name.lower()


def _ref_parts(ref: str) -> tuple[str | None, str]:
    """``(connector | None, tool)`` of a tool ref in any spelling, lower-cased, as the tool adapter reads it."""
    from core.langgraph.tool_adapter import _parse_authorized_tool_ref

    parsed = _parse_authorized_tool_ref(ref)
    if parsed is None:
        return None, _bare(ref)
    connector, tool = parsed
    return (connector.lower() if connector else None), _bare(tool)


def _only(candidates: list[str], intent_name: str) -> str | None:
    """The single candidate, or None (logged) when there are several: an ambiguous name never picks one."""
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        logger.warning("conversation_binding_ambiguous", intent=intent_name, candidates=len(candidates))
    return None


def resolve_binding(
    intent_name: str, authorized_tools: list[str] | None, bindings: dict[str, str] | None
) -> str | None:
    """The authorised tool ref an intent runs through: the agent's binding first, then the action's aliases.

    A binding is matched exactly first: the same ref, or the same connector and
    tool in another spelling. A bare-name match is only a fallback, and only
    when it is unambiguous: one authorised tool of that name, not qualified with
    a different connector than the binding names. Two connectors exposing the
    same tool name (``fetch_bank_statement``) never silently pick one.
    """
    tools = [str(t) for t in (authorized_tools or [])]
    bound = (bindings or {}).get(intent_name)
    if bound:
        if bound in tools:
            return bound
        connector, name = _ref_parts(bound)
        parts = {tool: _ref_parts(tool) for tool in tools}
        if connector:
            exact = [tool for tool, (c, n) in parts.items() if c == connector and n == name]
            if exact:
                return exact[0]
        same_name = [tool for tool, (c, n) in parts.items() if n == name]
        if connector:
            # A qualified binding never falls back to another connector's tool.
            if any(parts[tool][0] not in (None, connector) for tool in same_name):
                logger.warning("conversation_binding_ambiguous", intent=intent_name, candidates=len(same_name))
                return None
        match = _only(same_name, intent_name)
        if match is None and not same_name:
            logger.warning("conversation_binding_not_authorised", intent=intent_name)
        return match
    intent = INTENTS.get(intent_name)
    aliases = ACTIONS.get(intent.action or "", ()) if intent else ()
    for alias in aliases:
        candidates = [t for t in tools if _bare(t) == alias]
        if candidates:
            return _only(candidates, intent_name)
    return None


def params_for(intent_name: str, slots: dict[str, Any]) -> dict[str, Any]:
    """The flat parameters a bound tool receives: the slots, plus the intent for tools that take it."""
    params = {key: value for key, value in slots.items() if value not in (None, "")}
    params["intent"] = intent_name
    return params


def _bounded(value: Any) -> Any:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return value if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + "…"


# ── Sessions ──────────────────────────────────────────────────────────────────


def _expired(updated_at: datetime | None) -> bool:
    if updated_at is None:
        return True
    when = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
    return when < datetime.now(UTC) - timedelta(seconds=IDLE_SECONDS)


async def load_dialogue(tenant_id: uuid.UUID, key: str) -> Dialogue:
    """The dialogue stored for a session key, fresh when there is none or it has gone idle."""
    from core.database import get_tenant_session
    from core.models.conversation_session import ConversationSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ConversationSession).where(
                    ConversationSession.tenant_id == tenant_id, ConversationSession.session_key == key
                )
            )
        ).scalar_one_or_none()
    if row is None or _expired(row.updated_at):
        return Dialogue()
    return Dialogue.from_dict(row.state)


async def save_dialogue(
    tenant_id: uuid.UUID,
    key: str,
    dialogue: Dialogue,
    *,
    user_id: str,
    agent_id: str | None,
    channel: str,
) -> None:
    from core.database import get_tenant_session
    from core.models.conversation_session import ConversationSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ConversationSession)
                .where(ConversationSession.tenant_id == tenant_id, ConversationSession.session_key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            row = ConversationSession(tenant_id=tenant_id, session_key=key, user_id=user_id[:128], channel=channel[:16])
            session.add(row)
        _store(row, dialogue, agent_id)


def _store(row: Any, dialogue: Dialogue, agent_id: str | None) -> None:
    agent_uuid: uuid.UUID | None = None
    try:
        agent_uuid = uuid.UUID(str(agent_id)) if agent_id else None
    except ValueError:
        agent_uuid = None
    row.agent_id = agent_uuid
    row.status = "active" if dialogue.stage not in (dialogue_engine.STAGE_IDLE, dialogue_engine.STAGE_DONE) else "idle"
    row.intent = dialogue.intent
    row.state = dialogue.to_dict()
    row.turns = dialogue.turns
    row.updated_at = datetime.now(UTC)


async def claim_dialogue(
    tenant_id: uuid.UUID,
    key: str,
    expected: dict[str, Any],
    dialogue: Dialogue,
    *,
    user_id: str,
    agent_id: str | None,
    channel: str,
) -> str | None:
    """Claim a confirmed action for execution, atomically with the session state; None when it cannot be claimed.

    Under a row lock, the stored dialogue must still be ``expected`` (the state
    the turn was advanced from, read exactly as ``load_dialogue`` reads it). The
    advanced dialogue, which no longer confirms anything, is then written with a
    fresh execution key and committed before the tool is called. Two overlapping
    confirmations of one action therefore claim it once: the second sees a
    changed state and is refused. A failure after the claim (the tool, or the
    process) leaves a session that has nothing left to confirm, so a retry
    cannot run the action again. The key goes to the tool as its idempotency key.
    """
    from sqlalchemy.exc import IntegrityError

    from core.database import get_tenant_session
    from core.models.conversation_session import ConversationSession

    execution_key = str(uuid.uuid4())
    try:
        async with get_tenant_session(tenant_id) as session:
            row = (
                await session.execute(
                    select(ConversationSession)
                    .where(ConversationSession.tenant_id == tenant_id, ConversationSession.session_key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            current = (
                Dialogue() if row is None or _expired(row.updated_at) else Dialogue.from_dict(row.state)
            ).to_dict()
            if current != expected:
                logger.warning("conversation_claim_superseded", intent=dialogue.intent)
                return None
            if row is None:
                row = ConversationSession(
                    tenant_id=tenant_id, session_key=key, user_id=user_id[:128], channel=channel[:16]
                )
                session.add(row)
            dialogue.execution_key = execution_key
            _store(row, dialogue, agent_id)
    except IntegrityError:
        # Another turn created the session first.
        dialogue.execution_key = None
        logger.warning("conversation_claim_superseded", intent=dialogue.intent)
        return None
    return execution_key


async def reset_dialogue(tenant_id: uuid.UUID, key: str) -> bool:
    from core.database import get_tenant_session
    from core.models.conversation_session import ConversationSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ConversationSession)
                .where(ConversationSession.tenant_id == tenant_id, ConversationSession.session_key == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.state = Dialogue().to_dict()
        row.status = "idle"
        row.intent = None
        row.updated_at = datetime.now(UTC)
        return True


def dialogue_view(dialogue: Dialogue) -> dict[str, Any]:
    intent = dialogue.current()
    from core.conversation import feedback

    return {
        "rating": dialogue.rating,
        "sentiment": feedback.latest_label(dialogue.sentiment),
        "offer": dict(dialogue.offer) if isinstance(dialogue.offer, dict) else None,
        "stage": dialogue.stage,
        "intent": dialogue.intent,
        "confidence": round(dialogue.confidence, 3),
        "slots": dict(dialogue.slots),
        "missing": dialogue_engine.missing_slots(intent, dialogue.slots) if intent else [],
        "pending": dialogue.pending,
        "options": list(dialogue.options),
        "turns": dialogue.turns,
    }


# ── Execution ─────────────────────────────────────────────────────────────────


@dataclass
class ExecutionContext:
    """What a confirmed action needs to run through the agent's governed tools."""

    tenant_id: str
    agent_id: str
    agent_type: str = ""
    domain: str = ""
    authorized_tools: list[str] = field(default_factory=list)
    connector_config: dict[str, Any] | None = None
    connector_names: list[str] | None = None
    company_id: str | None = None
    run_grant: Any = None
    bindings: dict[str, str] = field(default_factory=dict)


Claim = Callable[[], Awaitable[str | None]]


async def run_tool(
    context: ExecutionContext,
    ref: str,
    params: dict[str, Any],
    *,
    label: str = "",
    claim: Claim | None = None,
) -> dict[str, Any]:
    """Run one authorised tool under the grant with ``params``: the governed path every conversational action takes.

    In order: the tool index, the grant, the operator overrides on the agent (a
    halt, or an ``agent`` / ``all_agents`` throttle, which is consumed here once
    per action as the agent runner consumes it once per run), the tool build,
    and then ``claim``, which must return an execution key before the tool is
    called (``claim_dialogue``). The key is passed to the tool as
    ``idempotency_key``. Without ``claim`` the tool is called unclaimed.
    """
    from auth.run_grants import direct_tool_call_permitted
    from core.governance.operator_override import check as check_operator_override
    from core.langgraph.tool_adapter import _build_tool_index, _parse_authorized_tool_ref, build_tools_for_agent

    parsed = _parse_authorized_tool_ref(ref)
    if parsed is None:
        return {"status": "unbound", "intent": label, "message": "The bound tool reference is malformed."}
    connector_hint, tool_name = parsed
    index = _build_tool_index(context.connector_config, context.connector_names, include_connector_aliases=True)
    match = index.get(f"{connector_hint}:{tool_name}" if connector_hint else tool_name)
    if not match:
        return {"status": "unbound", "intent": label, "message": "The bound tool is not available to this agent."}
    connector_name = match[0]
    if context.run_grant is None or not await direct_tool_call_permitted(
        context.run_grant,
        connector=connector_name,
        tool=tool_name,
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        agent_type=context.agent_type,
        runtime=RUNTIME,
    ):
        logger.warning("conversation_action_refused_grant", intent=label, connector=connector_name, tool=tool_name)
        return {
            "status": "refused",
            "intent": label,
            "message": "This action is not permitted under the current grant.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "refused"},
        }
    override = await check_operator_override(
        context.tenant_id, agent_id=context.agent_id or None, throttle_unit="agent"
    )
    if override.blocked:
        logger.warning("conversation_action_refused_operator_override", intent=label, tool=tool_name)
        return {
            "status": "held",
            "intent": label,
            "message": override.reason or "An operator has paused or limited this agent.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "operator_override"},
        }
    tools = build_tools_for_agent(
        [ref],
        context.connector_config,
        context.connector_names,
        tenant_id=context.tenant_id,
        company_id=context.company_id,
        domain=context.domain or None,
        agent_id=context.agent_id,
    )
    if not tools:
        return {"status": "unbound", "intent": label, "message": "The bound tool could not be built."}
    execution_key: str | None = None
    if claim is not None:
        execution_key = await claim()
        if execution_key is None:
            return {
                "status": "superseded",
                "intent": label,
                "message": "This confirmation is no longer current.",
            }
    invoked = {**params, "idempotency_key": execution_key} if execution_key else params
    try:
        result = await tools[0].ainvoke(invoked)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("conversation_action_failed", intent=label, error_type=type(exc).__name__)
        return {
            "status": "failed",
            "intent": label,
            "message": "The action could not be completed.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "error"},
            **({"execution_key": execution_key} if execution_key else {}),
        }
    failed = isinstance(result, dict) and bool(result.get("error"))
    logger.info(
        "conversation_action_executed",
        intent=label,
        connector=connector_name,
        tool=tool_name,
        outcome="error" if failed else "ok",
    )
    return {
        "status": "failed" if failed else "executed",
        "intent": label,
        "message": str(result.get("message") or result.get("error"))
        if failed and isinstance(result, dict)
        else "Done.",
        "result": _bounded(result),
        "tool_call": {
            "connector": connector_name,
            "tool": tool_name,
            "params": {k: v for k, v in params.items() if k != "intent"},
            "status": "error" if failed else "success",
        },
        **({"execution_key": execution_key} if execution_key else {}),
    }


async def execute(outcome: Outcome, context: ExecutionContext, claim: Claim | None = None) -> dict[str, Any]:
    """Run a confirmed action: the bound tool, under the grant, with the collected slots. Never before confirmation.

    The binding is resolved here; ``run_tool`` checks the grant and the operator
    overrides, builds the tool and claims the action (``claim``) before calling it.
    """
    intent_name = outcome.intent or ""
    ref = resolve_binding(intent_name, context.authorized_tools, context.bindings)
    if ref is None:
        return {"status": "unbound", "intent": intent_name, "message": "No tool is bound for this request."}
    return await run_tool(context, ref, params_for(intent_name, outcome.slots), label=intent_name, claim=claim)


def answer_for(outcome: Outcome, execution: dict[str, Any] | None, handoff: dict[str, Any] | None = None) -> str:
    """The text the user sees for an outcome, with the result of an executed action or of a hand-off.

    An escalation says what the hand-off actually did (``escalation.handoff_answer``):
    without a recorded hand-off it promises nobody.
    """
    if outcome.kind == "escalate":
        from core.conversation import escalation

        return f"{outcome.text} {escalation.handoff_answer(handoff or {})}"
    if outcome.kind != "execute" or execution is None:
        return outcome.text
    status = execution.get("status")
    if status == "executed":
        result = execution.get("result")
        reference = (
            result.get("reference") or result.get("id") or result.get("transaction_id")
            if isinstance(result, dict)
            else None
        )
        tail = f" Reference {reference}." if reference else ""
        if outcome.intent in ("balance_enquiry", "mini_statement", "loan_enquiry", "application_status") and isinstance(
            result, dict
        ):
            return _read_answer(outcome.intent, result)
        return f"{outcome.summary or outcome.text} Done.{tail}"
    if status == "refused":
        return "I cannot do that from here: the action is not permitted under the current grant. Nothing has been done."
    if status == "unbound":
        return "I cannot complete that from chat yet: no tool is set up for it. Nothing has been done."
    if status == "held":
        return f"That cannot run right now: {execution.get('message')} Nothing has been done."
    if status == "superseded":
        return (
            "This confirmation is no longer current: the request was already acted on or changed in another "
            "window, so nothing more has been done."
        )
    return f"That did not go through: {execution.get('message') or 'the action failed'}. Nothing has been changed."


def _read_answer(intent: str, result: dict[str, Any]) -> str:
    if intent == "balance_enquiry" and "balance" in result:
        account = result.get("account") or result.get("account_number") or ""
        where = f" of the account ending {str(account)[-4:]}" if account else ""
        return f"The balance{where} is {dialogue_engine.rupees(result['balance'])}."
    if intent == "application_status" and result.get("status"):
        return f"The application is {result['status']}." + (f" {result['detail']}" if result.get("detail") else "")
    text = json.dumps(_bounded(result), default=str, ensure_ascii=False)
    return f"Here is what I found: {text}"


# ── The chat hook ─────────────────────────────────────────────────────────────


async def run_turn(
    tenant_id: uuid.UUID,
    key: str,
    dialogue: Dialogue,
    text: str,
    context: ExecutionContext | None,
    *,
    user_id: str,
    agent_id: str | None,
    channel: str,
    no_agent_message: str,
) -> tuple[Outcome, dict[str, Any] | None]:
    """Advance a loaded dialogue by one turn, run what it produces, and keep the session.

    A confirmed action is claimed atomically with the session state before its
    tool is called (``claim_dialogue``); once claimed, the session is already
    stored and is not written again, so a later failure cannot leave it
    confirmable. A turn that runs nothing is saved as before. An escalation is
    handed off afterwards by ``finish_turn``, once the session is stored.
    """
    expected = dialogue.to_dict()
    # The tenant business rules (retries, amount ceilings) apply on every entry point.
    rules = await business_rules(tenant_id)
    outcome = dialogue_engine.advance(
        dialogue, text, rules=dialogue_engine.Rules(retries=rules.retries, amount_limits=rules.amount_limits)
    )
    execution: dict[str, Any] | None = None
    claimed = False

    async def _claim() -> str | None:
        nonlocal claimed
        claimed = True
        return await claim_dialogue(
            tenant_id, key, expected, dialogue, user_id=user_id, agent_id=agent_id, channel=channel
        )

    if outcome.kind == "execute":
        if context is None:
            execution = {"status": "unbound", "intent": outcome.intent, "message": no_agent_message}
        else:
            execution = await execute(outcome, context, claim=_claim)
    if not claimed:
        await save_dialogue(tenant_id, key, dialogue, user_id=user_id, agent_id=agent_id, channel=channel)
    return outcome, execution


def outcome_payload(
    outcome: Outcome, execution: dict[str, Any] | None, handoff: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The outcome as a response carries it.

    Never the hand-off notes the dialogue took (``Outcome.handoff``: slots and
    recent turns); the execution without its result; for a hand-off only its
    reason, intent tag, review item and ticket.
    """
    payload = outcome.to_dict()
    payload.pop("handoff", None)
    if execution is not None:
        payload["execution"] = {k: v for k, v in execution.items() if k not in ("result", "item_id")}
    if handoff is not None:
        payload["handoff"] = {k: handoff.get(k) for k in ("reason", "intent", "hitl_id", "ticket")}
    return payload


def _recognised(text: str) -> bool:
    matches = catalogue.recognise(text)
    return bool(matches and matches[0].confidence >= MIN_CONFIDENCE) or len(catalogue.split_requests(text)) > 1


HELD_ANSWER = "A colleague has joined this conversation and will reply here."


async def business_rules(tid: uuid.UUID) -> Any:
    """The tenant's conversation rules from the business console; the catalogue's defaults when it is off."""
    from core.workbench import console

    return await console.conversation_rules(tid)


async def held_turn(tid: uuid.UUID, key: str, text: str, dialogue: Dialogue) -> dict[str, Any] | None:
    """While a supervisor holds the session, the user's message goes to them, not to the runtime."""
    from core.conversation import supervisor

    holder = await supervisor.taken_over(tid, key)
    if not holder:
        return None
    await supervisor.user_message(tid, key, text)
    return {
        "answer": HELD_ANSWER,
        "confidence": 1.0,
        "outcome": {"kind": "handed_over", "text": HELD_ANSWER, "intent": dialogue.intent, "slots": {}, "missing": []},
        "dialogue": dialogue_view(dialogue),
        "tool_calls": None,
        "session_key": key,
    }


async def finish_turn(
    tid: uuid.UUID,
    key: str,
    dialogue: Dialogue,
    outcome: Outcome,
    execution: dict[str, Any] | None,
    *,
    text: str,
    user_id: str,
    agent_id: str,
    channel: str,
    context: ExecutionContext | None,
) -> dict[str, Any]:
    """Offer the next step, hand off when the outcome says so, announce the turn, and shape the answer.

    The session is already stored: ``run_turn`` saves it, or claimed it with a
    confirmed action before the tool ran. What this turn adds afterwards (the
    action record, a scenario's next step, an offer of a person, the rating
    prompt) is written on top; the stored dialogue confirms nothing either way.
    A confirmation that was superseded writes nothing more, so it never
    overwrites the turn that did claim the action.
    """
    from core.conversation import escalation, feedback, scenarios, supervisor

    handoff: dict[str, Any] | None = None
    # The dialogue says why it escalated (an accepted offer after fallbacks is not an unsolicited request).
    reason = outcome.escalation or (
        escalation.REASON_REQUESTED if outcome.intent == "talk_to_agent" else escalation.REASON_SLOTS
    )
    tail = ""
    superseded = (execution or {}).get("status") == "superseded"
    if outcome.kind == "execute" and not superseded:
        # The record of what ran, for the summary; then the scenario's next step, if any.
        dialogue.actions = (
            dialogue.actions
            + [
                {
                    "intent": outcome.intent,
                    "status": (execution or {}).get("status"),
                    "reference": scenarios.reference_of(execution),
                    "at": datetime.now(UTC).isoformat(),
                }
            ]
        )[-dialogue_engine.MAX_ACTIONS :]
        offer = scenarios.follow_up(outcome.intent, outcome.slots, execution)
        if offer is not None:
            dialogue.stage = dialogue_engine.STAGE_OFFERING
            dialogue.offer = offer.to_dict()
            tail = " " + offer.text
    if (
        not superseded
        and dialogue.negative_turns >= (await business_rules(tid)).negative_turns
        and dialogue.stage in (dialogue_engine.STAGE_IDLE, dialogue_engine.STAGE_COLLECTING)
        and outcome.kind not in ("escalate", "execute")
    ):
        # Two negative turns in a row: offer a person (FE-06), whatever the dialogue was doing.
        offer = scenarios.person_offer(
            "I am sorry this has been frustrating. Would you like me to connect you to a person? Reply yes or no."
        )
        dialogue.stage = dialogue_engine.STAGE_OFFERING
        dialogue.offer = offer.to_dict()
        dialogue.negative_turns = 0
        tail = " " + offer.text
    elif (
        not superseded
        and not tail
        and outcome.kind in ("execute", "escalate")
        and not dialogue.rating_asked
        and dialogue.stage == dialogue_engine.STAGE_IDLE
    ):
        dialogue.stage = dialogue_engine.STAGE_RATING
        dialogue.rating_asked = True
        tail = " " + feedback.RATING_PROMPT
    if outcome.kind == "rated" and dialogue.rating is not None:
        await feedback.record_rating(
            tid,
            session_key=key,
            agent_id=agent_id or None,
            user_id=user_id,
            rating=dialogue.rating,
            channel=channel,
            intent=dialogue.last_intent,
            sentiment_label=feedback.latest_label(dialogue.sentiment),
        )
    if not superseded and (tail or outcome.kind == "execute"):
        await save_dialogue(tid, key, dialogue, user_id=user_id, agent_id=agent_id or None, channel=channel)
    if outcome.kind == "escalate":
        # The dialogue has started over by now; the outcome's snapshot carries what was in
        # progress (a transfer the user left for a person), else the intent and slots it had.
        notes = dict(outcome.handoff or {})
        handoff = await escalation.handoff(
            tid,
            session_key=key,
            dialogue=dialogue,
            user_id=user_id,
            agent_id=agent_id,
            channel=channel,
            reason=reason,
            context=context,
            intent=notes.get("intent") or outcome.intent,
            slots=notes.get("slots") or outcome.slots,
            notes=notes,
        )
    answer = answer_for(outcome, execution, handoff) + tail
    payload = outcome_payload(outcome, execution, handoff)
    if dialogue.offer is not None:
        payload["offer"] = dict(dialogue.offer)
    stage = dialogue.stage
    await supervisor.announce_turn(tid, key, role="user", intent=dialogue.intent, stage=stage)
    await supervisor.announce_turn(tid, key, role="assistant", intent=dialogue.intent, stage=stage)
    tool_call = (execution or {}).get("tool_call")
    return {
        "answer": answer,
        "confidence": round(outcome.confidence, 3)
        if outcome.confidence
        else (0.9 if outcome.kind in ("execute", "confirm") else 0.6),
        "outcome": payload,
        "dialogue": dialogue_view(dialogue),
        "tool_calls": [tool_call] if tool_call else None,
        "session_key": key,
    }


async def chat_turn(
    *,
    tenant_id: str,
    company_id: str,
    user_id: str,
    agent_id: str,
    text: str,
    channel: str = "web",
    context: ExecutionContext | None = None,
) -> dict[str, Any] | None:
    """Handle a chat message as a banking turn, or return None so the agent path answers as before.

    A turn is handled when a dialogue is in progress for this session, or when
    the message names a banking intent. A read intent with no tool bound is
    left to the agent, which can answer it from its own tools and knowledge.
    While a supervisor holds the session, every message goes to them.
    """
    if not enabled():
        return None
    tid = uuid.UUID(str(tenant_id))
    key = session_key(channel, company_id, agent_id, user_id)
    dialogue = await load_dialogue(tid, key)
    held = await held_turn(tid, key, text, dialogue)
    if held is not None:
        return held
    active = dialogue.stage not in (dialogue_engine.STAGE_IDLE, dialogue_engine.STAGE_DONE)
    if not active:
        if not _recognised(text):
            return None
        top = catalogue.recognise(text)
        if top and top[0].intent.risk == "read" and context is not None:
            if resolve_binding(top[0].intent.name, context.authorized_tools, context.bindings) is None:
                return None
    outcome, execution = await run_turn(
        tid,
        key,
        dialogue,
        text,
        context,
        user_id=user_id,
        agent_id=agent_id or None,
        channel=channel,
        no_agent_message="No agent is available to run this.",
    )
    return await finish_turn(
        tid,
        key,
        dialogue,
        outcome,
        execution,
        text=text,
        user_id=user_id,
        agent_id=agent_id,
        channel=channel,
        context=context,
    )


async def agent_bindings(tenant_id: str, agent_id: str) -> dict[str, str]:
    """The bindings an agent declares, read once per turn; none when the agent cannot be read."""
    if not agent_id:
        return {}
    from core.database import get_tenant_session
    from core.models.agent import Agent

    try:
        tid = uuid.UUID(str(tenant_id))
        aid = uuid.UUID(str(agent_id))
    except ValueError:
        return {}
    try:
        async with get_tenant_session(tid) as session:
            agent = (
                await session.execute(select(Agent).where(Agent.id == aid, Agent.tenant_id == tid))
            ).scalar_one_or_none()
    except (RuntimeError, OSError) as exc:
        logger.warning("conversation_bindings_unavailable", error_type=type(exc).__name__)
        return {}
    return bindings_of(getattr(agent, "config", None)) if agent is not None else {}
