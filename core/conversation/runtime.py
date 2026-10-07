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


Claim = Callable[[], Awaitable[str | None]]


async def execute(outcome: Outcome, context: ExecutionContext, claim: Claim | None = None) -> dict[str, Any]:
    """Run a confirmed action: the bound tool, under the grant, with the collected slots. Never before confirmation.

    In order: the binding, the grant, the operator overrides on the agent (a
    halt, or an ``agent`` / ``all_agents`` throttle, which is consumed here once
    per action as the agent runner consumes it once per run), the tool build,
    and then ``claim``, which must return an execution key before the tool is
    called (``claim_dialogue``). The key is passed to the tool as
    ``idempotency_key``. Without ``claim`` the tool is called unclaimed.
    """
    from auth.run_grants import direct_tool_call_permitted
    from core.governance.operator_override import check as check_operator_override
    from core.langgraph.tool_adapter import _build_tool_index, _parse_authorized_tool_ref, build_tools_for_agent

    intent_name = outcome.intent or ""
    ref = resolve_binding(intent_name, context.authorized_tools, context.bindings)
    if ref is None:
        return {"status": "unbound", "intent": intent_name, "message": "No tool is bound for this request."}
    parsed = _parse_authorized_tool_ref(ref)
    if parsed is None:
        return {"status": "unbound", "intent": intent_name, "message": "The bound tool reference is malformed."}
    connector_hint, tool_name = parsed
    index = _build_tool_index(context.connector_config, context.connector_names, include_connector_aliases=True)
    match = index.get(f"{connector_hint}:{tool_name}" if connector_hint else tool_name)
    if not match:
        return {"status": "unbound", "intent": intent_name, "message": "The bound tool is not available to this agent."}
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
        logger.warning(
            "conversation_action_refused_grant", intent=intent_name, connector=connector_name, tool=tool_name
        )
        return {
            "status": "refused",
            "intent": intent_name,
            "message": "This action is not permitted under the current grant.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "refused"},
        }
    override = await check_operator_override(
        context.tenant_id, agent_id=context.agent_id or None, throttle_unit="agent"
    )
    if override.blocked:
        logger.warning("conversation_action_refused_operator_override", intent=intent_name, tool=tool_name)
        return {
            "status": "held",
            "intent": intent_name,
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
        return {"status": "unbound", "intent": intent_name, "message": "The bound tool could not be built."}
    params = params_for(intent_name, outcome.slots)
    execution_key: str | None = None
    if claim is not None:
        execution_key = await claim()
        if execution_key is None:
            return {
                "status": "superseded",
                "intent": intent_name,
                "message": "This confirmation is no longer current.",
            }
    invoked = {**params, "idempotency_key": execution_key} if execution_key else params
    try:
        result = await tools[0].ainvoke(invoked)
    except (RuntimeError, TypeError, ValueError, OSError) as exc:
        logger.warning("conversation_action_failed", intent=intent_name, error_type=type(exc).__name__)
        return {
            "status": "failed",
            "intent": intent_name,
            "message": "The action could not be completed.",
            "tool_call": {"connector": connector_name, "tool": tool_name, "status": "error"},
            **({"execution_key": execution_key} if execution_key else {}),
        }
    failed = isinstance(result, dict) and bool(result.get("error"))
    logger.info(
        "conversation_action_executed",
        intent=intent_name,
        connector=connector_name,
        tool=tool_name,
        outcome="error" if failed else "ok",
    )
    return {
        "status": "failed" if failed else "executed",
        "intent": intent_name,
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


def answer_for(outcome: Outcome, execution: dict[str, Any] | None) -> str:
    """The text the user sees for an outcome, with the result of an executed action or of a hand-off."""
    if outcome.kind == "escalate":
        if execution is not None and execution.get("status") == "handed_off":
            return (
                f"{outcome.text} I have put a request for a person to take this over in the team's queue, "
                f"with what you have told me so far (reference {execution.get('reference')})."
            )
        return (
            f"{outcome.text} I cannot pass this to a person from here, so nothing has been handed over. "
            "Please use your usual support channel."
        )
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


HANDOFF_TRIGGER = "conversation_handoff"
HANDOFF_HOURS = 24


async def request_handoff(
    outcome: Outcome, context: ExecutionContext | None, *, user_id: str, channel: str
) -> dict[str, Any]:
    """Raise a real hand-off for an ``escalate`` outcome: an item in the approvals queue and its notification.

    The item carries what a person taking over needs (``Outcome.handoff``). It
    needs the agent the conversation is with; without one, or when the item
    cannot be written, nothing is handed over and the answer says so.
    """
    if context is None or not context.agent_id:
        return {"status": "handoff_unavailable", "intent": outcome.intent}
    try:
        tid = uuid.UUID(str(context.tenant_id))
        aid = uuid.UUID(str(context.agent_id))
    except ValueError:
        return {"status": "handoff_unavailable", "intent": outcome.intent}
    try:
        requester: uuid.UUID | None = uuid.UUID(str(user_id))
    except ValueError:
        requester = None
    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.hitl import HITLQueue
    from core.ownership import agent_ownership_fields

    handoff = dict(outcome.handoff or {})
    intent = INTENTS.get(str(handoff.get("intent") or ""))
    topic = intent.title if intent is not None else "a conversation"
    try:
        async with get_tenant_session(tid) as session:
            agent_row = (
                await session.execute(select(Agent).where(Agent.id == aid, Agent.tenant_id == tid))
            ).scalar_one_or_none()
            if agent_row is None:
                return {"status": "handoff_unavailable", "intent": outcome.intent}
            item = HITLQueue(
                id=uuid.uuid4(),
                tenant_id=tid,
                agent_id=aid,
                workflow_run_id=None,
                title=f"Conversation hand-off: {topic}"[:500],
                trigger_type=HANDOFF_TRIGGER,
                priority="high",
                assignee_role=context.domain or "admin",
                requested_by_user_id=requester,
                decision_options={"options": ["approve", "reject"]},
                context={"source": RUNTIME, "channel": channel, "handoff": handoff},
                expires_at=datetime.now(UTC) + timedelta(hours=HANDOFF_HOURS),
            )
            session.add(item)
            await session.flush()
            item_id = str(item.id)
            push_scope = agent_ownership_fields(agent_row)
        from core.push.sender import notify_approval_created

        await notify_approval_created(
            str(tid),
            item_id=item_id,
            agent_name=str(getattr(agent_row, "name", "") or context.agent_type or ""),
            action=HANDOFF_TRIGGER,
            agent_visibility=push_scope.get("visibility"),
            agent_owner_user_id=push_scope.get("owner_user_id"),
        )
    # enterprise-gate: broad-except-ok reason=handoff-failure-is-logged-and-answered-as-not-handed-over
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversation_handoff_failed", error_type=type(exc).__name__)
        return {"status": "handoff_unavailable", "intent": outcome.intent}
    logger.info("conversation_handoff_raised", reason=handoff.get("reason"))
    return {"status": "handed_off", "intent": outcome.intent, "reference": item_id[:8].upper(), "item_id": item_id}


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
    """Advance a loaded dialogue by one turn, run or hand off what it produces, and keep the session.

    A confirmed action is claimed atomically with the session state before its
    tool is called (``claim_dialogue``); once claimed, the session is already
    stored and is not written again, so a later failure cannot leave it
    confirmable. A turn that runs nothing is saved as before.
    """
    expected = dialogue.to_dict()
    outcome = dialogue_engine.advance(dialogue, text)
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
    elif outcome.kind == "escalate":
        execution = await request_handoff(outcome, context, user_id=user_id, channel=channel)
    if not claimed:
        await save_dialogue(tenant_id, key, dialogue, user_id=user_id, agent_id=agent_id, channel=channel)
    return outcome, execution


def outcome_payload(outcome: Outcome, execution: dict[str, Any] | None) -> dict[str, Any]:
    """The outcome as a response carries it: without the hand-off notes, with the execution minus its result."""
    payload = outcome.to_dict()
    payload.pop("handoff", None)
    if execution is not None:
        payload["execution"] = {k: v for k, v in execution.items() if k not in ("result", "item_id")}
    return payload


def _recognised(text: str) -> bool:
    matches = catalogue.recognise(text)
    return bool(matches and matches[0].confidence >= MIN_CONFIDENCE) or len(catalogue.split_requests(text)) > 1


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
    """
    if not enabled():
        return None
    tid = uuid.UUID(str(tenant_id))
    key = session_key(channel, company_id, agent_id, user_id)
    dialogue = await load_dialogue(tid, key)
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
    answer = answer_for(outcome, execution)
    payload = outcome_payload(outcome, execution)
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
