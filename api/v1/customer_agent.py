# SPDX-License-Identifier: Apache-2.0
"""The customer agent's routes: the bank's own agent, reached by the bank's own customers.

Off by default (``AGENTICORG_CUSTOMER_AGENT_ENABLED``). Nothing here takes an
operator credential: the bank's backend opens a session with a signed customer
assertion, the channel holds the opaque session token it gets back, and every
other route authenticates that token itself (``require_customer_session``).
The auth middleware exempts the prefix for that reason; the routes are
public to the middleware and never to a caller without a live session.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.route_metadata import route_meta
from core.conversation import dialogue as dialogue_engine
from core.conversation import runtime
from core.conversation.intents import CATALOGUE
from core.customer_agent import actions, autonomy, permissions, sessions, turns

logger = structlog.get_logger()
router = APIRouter(prefix="/customer-agent", tags=["Customer Agent"])

PUBLIC_REASON = "bank-signed-customer-assertion-or-the-session-token-it-issued-authenticates-the-customer"


class SessionIn(BaseModel):
    model_config = {"extra": "forbid"}

    assertion: str = Field(..., min_length=20, max_length=8000)
    channel: str = Field("web", max_length=16)


class TurnIn(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1, max_length=2000)
    approval: dict[str, Any] | None = None


class ApprovalIn(BaseModel):
    model_config = {"extra": "forbid"}

    approval: dict[str, Any] | None = None


class MandateIn(BaseModel):
    model_config = {"extra": "forbid"}

    task_class: str = Field(..., max_length=64)
    autonomy_level: int = Field(2, ge=0, le=4)
    per_action_cap: float | None = None
    period_cap: float | None = None
    period: str = Field("month", max_length=8)
    counterparties: list[str] = Field(default_factory=list, max_length=50)
    window: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "customer_agent_disabled",
            "message": "The customer agent is off for this deployment (AGENTICORG_CUSTOMER_AGENT_ENABLED).",
        },
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


async def require_customer_session(request: Request) -> Any:
    """The live customer session behind the bearer token, or 401; 404 while the agent is off."""
    if not sessions.enabled():
        raise _off()
    row = await sessions.authenticate(_bearer(request))
    if row is None:
        raise HTTPException(
            401,
            detail={"error": "customer_session_invalid", "message": "Sign in to your bank again to continue."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.customer_session = row
    return row


def _acknowledged(row: Any) -> None:
    if row.disclosure_acknowledged_at is None:
        raise HTTPException(
            409,
            detail={
                "error": "disclosure_required",
                "message": "The AI disclosure must be acknowledged before the assistant can help.",
            },
        )


def _channel(row: Any) -> str:
    return str(getattr(row, "channel", "") or "web")


@router.post("/sessions")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.session",
    rate_limit="customer-agent-public",
    idempotency="not_idempotent-opens-a-customer-session",
    audit_event="customer_agent.session.open",
    public_reason=PUBLIC_REASON,
)
async def open_session(body: SessionIn) -> dict[str, Any]:
    """Exchange the bank's signed customer assertion for a customer session token."""
    if not sessions.enabled():
        raise _off()
    try:
        row, token = await sessions.open_session(body.assertion, channel=body.channel)
    except sessions.CustomerAgentDisabledError:
        raise _off() from None
    except sessions.CustomerAssertionError as exc:
        logger.info("customer_session_refused", reason=str(exc)[:160])
        raise HTTPException(401, detail={"error": "assertion_rejected", "message": str(exc)}) from None
    return sessions.session_view(row, token=token)


@router.get("/session")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.session",
    rate_limit="customer-agent",
    idempotency="idempotent-read",
    public_reason=PUBLIC_REASON,
)
async def get_session(row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    return sessions.session_view(row)


@router.post("/session/acknowledge")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.session",
    rate_limit="customer-agent",
    idempotency="idempotent-records-the-first-acknowledgement-only",
    audit_event="customer_agent.disclosure.acknowledged",
    public_reason=PUBLIC_REASON,
)
async def acknowledge(row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    """Record that the customer read the AI disclosure; turns run only after this."""
    return sessions.session_view(await sessions.acknowledge_disclosure(row))


@router.delete("/session")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.session",
    rate_limit="customer-agent",
    idempotency="idempotent-revokes-the-session",
    audit_event="customer_agent.session.end",
    public_reason=PUBLIC_REASON,
)
async def end_session(row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    await sessions.revoke_session(row)
    return {"status": "ended", "session_id": str(row.id)}


@router.get("/intents")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.read",
    rate_limit="customer-agent",
    idempotency="idempotent-read",
    public_reason=PUBLIC_REASON,
)
async def list_intents(row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    """What the assistant can do for the customer, with the task class and default level of each."""
    items = []
    for intent in CATALOGUE:
        if intent.risk == "smalltalk":
            continue
        klass = autonomy.task_class(intent)
        items.append(
            {
                "name": intent.name,
                "title": intent.title,
                "risk": intent.risk,
                "task_class": klass,
                "default_level": autonomy.default_level(klass),
                "confirm": bool(intent.confirm),
            }
        )
    return {"intents": items, "autonomy_ceiling": autonomy.ceiling()}


@router.post("/turns")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.turn",
    rate_limit="customer-agent",
    idempotency="not_idempotent-advances-the-dialogue-and-may-run-a-confirmed-action",
    audit_event="customer_agent.turn",
    public_reason=PUBLIC_REASON,
)
async def post_turn(body: TurnIn, row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    """One turn: recognise, collect, explain, prepare, and run what the customer's autonomy allows."""
    _acknowledged(row)
    turn = await turns.run_customer_turn(row, body.text, channel=_channel(row), approval=body.approval)
    return turn.to_dict()


@router.get("/actions")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.read",
    rate_limit="customer-agent",
    idempotency="idempotent-read",
    public_reason=PUBLIC_REASON,
)
async def list_actions(
    row: Any = Depends(require_customer_session), status: str | None = None, limit: int = 20
) -> dict[str, Any]:
    rows = await actions.list_actions(row.tenant_id, row.customer_ref, limit=limit, status=status)
    return {"actions": [actions.action_view(r) for r in rows]}


async def _prepared_action(row: Any, action_id: str) -> Any:
    try:
        aid = uuid.UUID(action_id)
    except ValueError:
        raise HTTPException(404, detail={"error": "action_not_found"}) from None
    action = await actions.get_action(row.tenant_id, row.customer_ref, aid)
    if action is None:
        raise HTTPException(404, detail={"error": "action_not_found"})
    if action.status != "prepared":
        raise HTTPException(
            409,
            detail={"error": "action_settled", "status": action.status, "message": "This action was already decided."},
        )
    return action


async def _decide(row: Any, action: Any, text: str, approval: dict[str, Any] | None) -> dict[str, Any]:
    """Carry the card's tap into the dialogue; a card whose dialogue moved on is marked superseded, never run."""
    dialogue = await runtime.load_dialogue(row.tenant_id, turns.session_key_for(row))
    if dialogue.stage != dialogue_engine.STAGE_CONFIRMING or dialogue.intent != action.intent:
        await turns.settle_action(
            row.tenant_id,
            action.id,
            status="superseded",
            execution={"status": "superseded", "reason": "dialogue_moved_on"},
        )
        raise HTTPException(
            409,
            detail={
                "error": "action_stale",
                "message": "The conversation has moved on since this was prepared; ask for it again.",
            },
        )
    turn = await turns.run_customer_turn(row, text, channel=_channel(row), approval=approval)
    return turn.to_dict()


@router.post("/actions/{action_id}/confirm")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.turn",
    rate_limit="customer-agent",
    idempotency="idempotent-per-prepared-action-a-settled-action-is-refused",
    audit_event="customer_agent.action.confirm",
    public_reason=PUBLIC_REASON,
)
async def confirm_action(
    action_id: str, body: ApprovalIn | None = None, row: Any = Depends(require_customer_session)
) -> dict[str, Any]:
    """The card's one tap: the same confirmation as replying yes, with the step-up approval when needed."""
    _acknowledged(row)
    action = await _prepared_action(row, action_id)
    return await _decide(row, action, "yes", body.approval if body else None)


@router.post("/actions/{action_id}/cancel")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.turn",
    rate_limit="customer-agent",
    idempotency="idempotent-per-prepared-action",
    audit_event="customer_agent.action.cancel",
    public_reason=PUBLIC_REASON,
)
async def cancel_action(action_id: str, row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    _acknowledged(row)
    action = await _prepared_action(row, action_id)
    dialogue = await runtime.load_dialogue(row.tenant_id, turns.session_key_for(row))
    payload: dict[str, Any]
    if dialogue.stage == dialogue_engine.STAGE_CONFIRMING and dialogue.intent == action.intent:
        turn = await turns.run_customer_turn(row, "no", channel=_channel(row))
        payload = turn.to_dict()
    else:
        payload = {"answer": "Cancelled.", "outcome": {"kind": "cancelled", "intent": action.intent}}
    settled = await turns.settle_action(
        row.tenant_id, action.id, status="cancelled", execution={"status": "cancelled", "by": "customer"}
    )
    if settled is not None:
        payload["card"] = {**(payload.get("card") or {}), "action_id": str(settled.id), "status": "cancelled"}
    return payload


@router.get("/permissions")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.permissions",
    rate_limit="customer-agent",
    idempotency="idempotent-read",
    public_reason=PUBLIC_REASON,
)
async def get_permissions(row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    """Every mandate the bank's agent holds for this customer, and the way to every outside agent."""
    return await permissions.permissions_page(row)


@router.put("/permissions/mandates")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.permissions",
    rate_limit="customer-agent",
    idempotency="idempotent-one-active-mandate-per-task-class",
    audit_event="customer_agent.mandate.set",
    public_reason=PUBLIC_REASON,
)
async def put_mandate(body: MandateIn, row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    """Set the autonomy level and limits for one task; replaces the active mandate for that task."""
    _acknowledged(row)
    try:
        cleaned = autonomy.validate_mandate(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, detail={"error": "mandate_invalid", "message": str(exc)}) from None
    mandate = await permissions.set_mandate(
        row.tenant_id, row.customer_ref, cleaned, created_by=f"customer:{row.customer_ref}"
    )
    return permissions.mandate_view(mandate)


@router.delete("/permissions/mandates/{mandate_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:customer_agent.permissions",
    rate_limit="customer-agent",
    idempotency="idempotent-revokes-the-mandate",
    audit_event="customer_agent.mandate.revoke",
    public_reason=PUBLIC_REASON,
)
async def delete_mandate(mandate_id: str, row: Any = Depends(require_customer_session)) -> dict[str, Any]:
    try:
        mid = uuid.UUID(mandate_id)
    except ValueError:
        raise HTTPException(404, detail={"error": "mandate_not_found"}) from None
    mandate = await permissions.revoke_mandate(row.tenant_id, row.customer_ref, mid)
    if mandate is None:
        raise HTTPException(404, detail={"error": "mandate_not_found"})
    return permissions.mandate_view(mandate)
