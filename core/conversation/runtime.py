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
    "bill_payment": ("pay_bill", "bill_payment", "create_payment", "create_payment_intent"),
    "loan_enquiry": ("loan_enquiry", "get_loan_offers", "loan_eligibility", "check_loan_eligibility"),
    "dispute_transaction": ("raise_dispute", "create_dispute", "dispute_transaction"),
    "application_status": ("application_status", "get_application_status", "track_application"),
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


def resolve_binding(
    intent_name: str, authorized_tools: list[str] | None, bindings: dict[str, str] | None
) -> str | None:
    """The authorised tool ref an intent runs through: the agent's binding first, then the action's aliases."""
    tools = [str(t) for t in (authorized_tools or [])]
    bound = (bindings or {}).get(intent_name)
    if bound:
        match = next((t for t in tools if t == bound or _bare(t) == _bare(bound)), None)
        if match is not None:
            return match
        logger.warning("conversation_binding_not_authorised", intent=intent_name)
        return None
    intent = INTENTS.get(intent_name)
    aliases = ACTIONS.get(intent.action or "", ()) if intent else ()
    for alias in aliases:
        match = next((t for t in tools if _bare(t) == alias), None)
        if match is not None:
            return match
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

    status = "active" if dialogue.stage not in (dialogue_engine.STAGE_IDLE, dialogue_engine.STAGE_DONE) else "idle"
    agent_uuid: uuid.UUID | None = None
    try:
        agent_uuid = uuid.UUID(str(agent_id)) if agent_id else None
    except ValueError:
        agent_uuid = None
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
        row.agent_id = agent_uuid
        row.status = status
        row.intent = dialogue.intent
        row.state = dialogue.to_dict()
        row.turns = dialogue.turns
        row.updated_at = datetime.now(UTC)


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
    return {
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


async def run_tool(context: ExecutionContext, ref: str, params: dict[str, Any], *, label: str = "") -> dict[str, Any]:
    """Run one authorised tool under the grant with ``params``: the governed path every conversational action takes."""
    from auth.run_grants import direct_tool_call_permitted
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
    try:
        result = await tools[0].ainvoke(params)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("conversation_action_failed", intent=label, error_type=type(exc).__name__)
        return {
            "status": "failed",
            "intent": label,
            "message": "The action could not be completed.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "error"},
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
    }


async def execute(outcome: Outcome, context: ExecutionContext) -> dict[str, Any]:
    """Run a confirmed action: the bound tool, under the grant, with the collected slots. Never before confirmation."""
    intent_name = outcome.intent or ""
    ref = resolve_binding(intent_name, context.authorized_tools, context.bindings)
    if ref is None:
        return {"status": "unbound", "intent": intent_name, "message": "No tool is bound for this request."}
    return await run_tool(context, ref, params_for(intent_name, outcome.slots), label=intent_name)


def answer_for(outcome: Outcome, execution: dict[str, Any] | None) -> str:
    """The text the user sees for an outcome, with the result of an executed action."""
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


def _recognised(text: str) -> bool:
    matches = catalogue.recognise(text)
    return bool(matches and matches[0].confidence >= MIN_CONFIDENCE) or len(catalogue.split_requests(text)) > 1


HELD_ANSWER = "A colleague has joined this conversation and will reply here."


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
    """Save the turn, hand off when the outcome says so, announce it, and shape the answer."""
    from core.conversation import escalation, supervisor

    handoff: dict[str, Any] | None = None
    reason = escalation.REASON_REQUESTED if outcome.intent == "talk_to_agent" else escalation.REASON_SLOTS
    await save_dialogue(tid, key, dialogue, user_id=user_id, agent_id=agent_id or None, channel=channel)
    if outcome.kind == "escalate":
        handoff = await escalation.handoff(
            tid,
            session_key=key,
            dialogue=dialogue,
            user_id=user_id,
            agent_id=agent_id,
            channel=channel,
            reason=reason,
            context=context,
            intent=outcome.intent,
        )
    answer = escalation.handoff_answer(handoff) if handoff is not None else answer_for(outcome, execution)
    payload = outcome.to_dict()
    if execution is not None:
        payload["execution"] = {k: v for k, v in execution.items() if k != "result"}
    if handoff is not None:
        payload["handoff"] = {k: handoff.get(k) for k in ("reason", "intent", "hitl_id", "ticket")}
    stage = dialogue.stage
    await supervisor.announce_turn(tid, key, role="user", text=text, intent=dialogue.intent, stage=stage)
    await supervisor.announce_turn(tid, key, role="assistant", text=answer, intent=dialogue.intent, stage=stage)
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
    outcome = dialogue_engine.advance(dialogue, text)
    execution: dict[str, Any] | None = None
    if outcome.kind == "execute":
        if context is None:
            execution = {"status": "unbound", "intent": outcome.intent, "message": "No agent is available to run this."}
        else:
            execution = await execute(outcome, context)
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
