# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""BYO channel configuration and signed, tenant-bound inboxes."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.deps import get_current_tenant, require_scope
from api.route_metadata import route_meta
from core.channels.providers import (
    CATALOG,
    MAX_BODY_BYTES,
    ChannelError,
    connect_telegram,
    decode_messages,
    validate_credentials,
)
from core.channels.runtime import _request_for_user, receive, seal, unseal
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.channel import ChannelBinding, ChannelDelivery, ChannelSenderLink
from core.models.user import User
from core.models.workflow import WorkflowDefinition
from core.ownership import caller_from_request, require_agent_mutable
from core.rbac import get_scopes_for_role

router = APIRouter(prefix="/channels", tags=["Channels"], dependencies=[require_scope("agents:write")])
webhook_router = APIRouter(prefix="/channels/webhooks", tags=["Channel callbacks"])


class BindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    provider: str
    target_kind: Literal["agent", "workflow"]
    target_id: uuid.UUID
    credentials: dict[str, SecretStr]
    auto_reply: bool = False
    reply_mode: Literal["status", "agent_answer"] = "status"


class BindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    auto_reply: bool | None = None
    credentials: dict[str, SecretStr] | None = None
    reply_mode: Literal["status", "agent_answer"] | None = None
    target_kind: Literal["agent", "workflow"] | None = None
    target_id: uuid.UUID | None = None


async def _actor(request: Request, tenant_id: uuid.UUID) -> User:
    caller = caller_from_request(request)
    if not caller.is_human:
        raise HTTPException(403, "Sign in with a user account to configure a channel")
    async with get_tenant_session(tenant_id) as session:
        user = await session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.id == caller.user_id, User.status == "active")
        )
    if user is None or not (set(get_scopes_for_role(user.role)) & {"agents:write", "agenticorg:admin"}):
        raise HTTPException(403, "An active agent creator account is required")
    return user


async def _binding(tenant_id: uuid.UUID, binding_id: uuid.UUID, user: User) -> ChannelBinding:
    async with get_tenant_session(tenant_id) as session:
        row = await session.scalar(
            select(ChannelBinding).where(
                ChannelBinding.tenant_id == tenant_id,
                ChannelBinding.id == binding_id,
                ChannelBinding.owner_user_id == user.id,
            )
        )
    if row is None:
        raise HTTPException(404, "Channel not found")
    return row


def _out(row: ChannelBinding) -> dict:
    from core.config import normalize_env

    origin = os.getenv("AGENTICORG_BASE_URL", "").strip().rstrip("/")
    if not origin:
        # A local/staging binding must never register a callback on the production host.
        production = normalize_env(os.getenv("AGENTICORG_ENV")) in {"production", "prod"}
        origin = "https://app.agenticorg.ai" if production else "http://localhost:8000"
    return {
        "id": str(row.id),
        "name": row.name,
        "provider": row.provider,
        "target_kind": row.target_kind,
        "target_id": str(row.target_id),
        "enabled": row.enabled,
        "auto_reply": row.auto_reply,
        "reply_mode": getattr(row, "reply_mode", "status"),
        "credentials_configured": True,
        "callback_url": f"{origin}/api/v1/channels/webhooks/{row.tenant_id}/{row.id}",
    }


@router.get("/catalog")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="channels.catalog",
)
async def catalog() -> dict:
    return {
        "providers": [{"id": key, **value} for key, value in CATALOG.items()],
        "existing_setup": {
            "voice_calls": "/dashboard/voice-setup",
            "mcp": "/dashboard/connectors/remote-mcp",
            "ai_credentials": "/dashboard/settings/ai-credentials",
        },
    }


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="channels.list",
)
async def list_bindings(request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    async with get_tenant_session(tid) as session:
        rows = (
            await session.scalars(
                select(ChannelBinding).where(ChannelBinding.tenant_id == tid, ChannelBinding.owner_user_id == user.id)
            )
        ).all()
    return {"bindings": [_out(row) for row in rows]}


@router.post("", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="security-admin-write",
    idempotency="creates-binding",
    audit_event="channels.create",
)
async def create_binding(body: BindingIn, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    _validate_reply_mode(body.provider, body.target_kind, body.reply_mode)
    try:
        credentials = validate_credentials(
            body.provider, {k: v.get_secret_value() for k, v in body.credentials.items()}
        )
    except ChannelError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.auto_reply and not CATALOG[body.provider]["reply"]:
        raise HTTPException(422, "This channel returns results in AgenticOrg; native replies are unavailable")
    async with get_tenant_session(tid) as session:
        if body.target_kind == "agent":
            target = await session.scalar(select(Agent).where(Agent.tenant_id == tid, Agent.id == body.target_id))
            require_agent_mutable(target, caller_from_request(_request_for_user(user)))
        else:
            if user.role != "admin":
                raise HTTPException(403, "Workflow channel bindings require a tenant admin")
            workflow = await session.scalar(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.tenant_id == tid, WorkflowDefinition.id == body.target_id
                )
            )
            if workflow is None or not workflow.is_active:
                raise HTTPException(404, "Active workflow not found")
    ciphertext = await seal(credentials, tid)
    row = ChannelBinding(
        tenant_id=tid,
        owner_user_id=user.id,
        name=body.name.strip(),
        provider=body.provider,
        target_kind=body.target_kind,
        target_id=body.target_id,
        credentials_ciphertext=ciphertext,
        auto_reply=body.auto_reply,
        reply_mode=body.reply_mode,
        enabled=False,
    )
    async with get_tenant_session(tid) as session:
        session.add(row)
        await session.flush()
    return _out(row)


def _validate_reply_mode(provider: str, target_kind: str, mode: str) -> None:
    if mode == "agent_answer" and (provider != "telegram" or target_kind != "agent"):
        raise HTTPException(422, "Agent-answer replies require a Telegram agent destination")


@router.patch("/bindings/{binding_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="channels.update",
)
async def update_binding(
    binding_id: uuid.UUID, body: BindingUpdate, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    binding = await _binding(tid, binding_id, user)
    if (body.target_kind is None) != (body.target_id is None):
        raise HTTPException(422, "Provide both destination type and destination ID")
    ciphertext = None
    if body.credentials is not None:
        try:
            credentials = validate_credentials(
                binding.provider, {k: v.get_secret_value() for k, v in body.credentials.items()}
            )
        except ChannelError as exc:
            raise HTTPException(422, str(exc)) from exc
        ciphertext = await seal(credentials, tid)
    if body.auto_reply and not CATALOG[binding.provider]["reply"]:
        raise HTTPException(422, "Native replies are unavailable")
    async with get_tenant_session(tid) as session:
        row = await session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.id == binding_id,
                ChannelBinding.tenant_id == tid,
                ChannelBinding.owner_user_id == user.id,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Channel not found")
        kind = body.target_kind or row.target_kind
        target_id = body.target_id or row.target_id
        mode = body.reply_mode or getattr(row, "reply_mode", "status")
        _validate_reply_mode(row.provider, kind, mode)
        if body.target_id is not None:
            if kind == "agent":
                target = await session.scalar(select(Agent).where(Agent.tenant_id == tid, Agent.id == target_id))
                require_agent_mutable(target, caller_from_request(request))
            else:
                if user.role != "admin":
                    raise HTTPException(403, "Workflow channel bindings require a tenant admin")
                target = await session.scalar(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.tenant_id == tid, WorkflowDefinition.id == target_id
                    )
                )
                if target is None or not target.is_active:
                    raise HTTPException(404, "Active workflow not found")
            active = await session.scalar(
                select(ChannelDelivery.id)
                .where(
                    ChannelDelivery.tenant_id == tid,
                    ChannelDelivery.binding_id == binding_id,
                    ChannelDelivery.status.in_(["pending", "processing"]),
                )
                .limit(1)
            )
            if active is not None:
                raise HTTPException(409, "Wait for queued channel tasks before changing destination")
            row.target_kind, row.target_id = kind, target_id
        if body.reply_mode is not None:
            row.reply_mode = mode
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.auto_reply is not None:
            row.auto_reply = body.auto_reply
        if ciphertext:
            row.credentials_ciphertext, row.enabled = ciphertext, False
        if ciphertext or body.enabled is False:
            await session.execute(
                delete(ChannelSenderLink).where(
                    ChannelSenderLink.tenant_id == tid, ChannelSenderLink.binding_id == binding_id
                )
            )
            receipts = (
                await session.scalars(
                    select(ChannelDelivery).where(
                        ChannelDelivery.tenant_id == tid,
                        ChannelDelivery.binding_id == binding_id,
                        ChannelDelivery.status == "pending",
                    )
                )
            ).all()
            for receipt in receipts:
                receipt.status, receipt.error_code = "needs_review", "binding_authority_revoked"
    return _out(row)


@router.post("/bindings/{binding_id}/connect-telegram")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="security-admin-write",
    idempotency="register-same-binding-callback",
    audit_event="channels.telegram.connect",
)
async def register_telegram(
    binding_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    async with get_tenant_session(tid) as session:
        # Serialize provider registration with credential rotation and disable operations.
        row = await session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.tenant_id == tid,
                ChannelBinding.id == binding_id,
                ChannelBinding.owner_user_id == user.id,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Channel not found")
        if row.provider != "telegram":
            raise HTTPException(422, "This setup action is for Telegram only")
        try:
            credentials = await unseal(row.credentials_ciphertext)
            username = await asyncio.wait_for(connect_telegram(credentials, _out(row)["callback_url"]), timeout=45)
        except ChannelError as exc:
            status = 409 if str(exc) == "telegram_bot_connected_elsewhere" else 422
            raise HTTPException(status, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "Telegram setup is unconfirmed; check the connection before retrying") from exc
        row.enabled = True
    return {"binding": _out(row), "bot_username": username, "status": "callback_registered"}


@router.post("/bindings/{binding_id}/link-code")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="security-admin-write",
    idempotency="creates-one-time-link-code",
    audit_event="channels.link.request",
)
async def link_code(binding_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    binding = await _binding(tid, binding_id, user)
    if not binding.enabled or not CATALOG[binding.provider]["identity_link"]:
        raise HTTPException(409, "Enable a supported private messaging channel first; email requires review")
    code = secrets.token_urlsafe(24)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    async with get_tenant_session(tid) as session:
        current = await session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.tenant_id == tid,
                ChannelBinding.id == binding_id,
                ChannelBinding.owner_user_id == user.id,
            )
            .with_for_update()
        )
        if current is None or not current.enabled:
            raise HTTPException(409, "Channel is disabled")
        await session.execute(
            delete(ChannelSenderLink).where(
                ChannelSenderLink.tenant_id == tid,
                ChannelSenderLink.binding_id == binding_id,
                ChannelSenderLink.challenge_hash.is_not(None),
            )
        )
        session.add(
            ChannelSenderLink(
                tenant_id=tid,
                binding_id=binding_id,
                user_id=user.id,
                challenge_hash=hashlib.sha256(code.encode()).hexdigest(),
                expires_at=expires_at,
            )
        )
    return {"command": f"/link {code}", "expires_at": expires_at.isoformat(), "delegation_days": 30}


@router.get("/bindings/{binding_id}/deliveries")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="channels.inbox.list",
)
async def deliveries(binding_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    await _binding(tid, binding_id, user)
    async with get_tenant_session(tid) as session:
        rows = (
            await session.scalars(
                select(ChannelDelivery)
                .where(ChannelDelivery.tenant_id == tid, ChannelDelivery.binding_id == binding_id)
                .order_by(ChannelDelivery.created_at.desc())
                .limit(50)
            )
        ).all()
    items = []
    for row in rows:
        message = await unseal(row.message_ciphertext)
        items.append(
            {
                "id": str(row.id),
                "text": message["text"],
                "status": row.status,
                "error_code": row.error_code,
                "result": await unseal(row.result_ciphertext) if row.result_ciphertext else None,
                "created_at": row.created_at.isoformat(),
            }
        )
    return {"deliveries": items}


@router.post("/bindings/{binding_id}/deliveries/{delivery_id}/approve")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="agents:write",
    rate_limit="agent-execution",
    idempotency="approve-once",
    audit_event="channels.message.approve",
)
async def approve(
    binding_id: uuid.UUID, delivery_id: uuid.UUID, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict:
    tid = uuid.UUID(tenant_id)
    user = await _actor(request, tid)
    binding = await _binding(tid, binding_id, user)
    if not binding.enabled:
        raise HTTPException(409, "Channel is disabled")
    async with get_tenant_session(tid) as session:
        current = await session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.tenant_id == tid,
                ChannelBinding.id == binding_id,
                ChannelBinding.owner_user_id == user.id,
            )
            .with_for_update()
        )
        if current is None or not current.enabled:
            raise HTTPException(409, "Channel is disabled")
        row = await session.scalar(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.tenant_id == tid,
                ChannelDelivery.binding_id == binding_id,
                ChannelDelivery.id == delivery_id,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, "Message not found")
        if row.status != "needs_review" or row.error_code:
            raise HTTPException(
                409, "Only an unexecuted message can be approved. Review uncertain executions in run history"
            )
        row.user_id, row.status, row.updated_at = user.id, "pending", datetime.now(UTC)
        row.human_approved = True
    return {"status": "pending"}


@webhook_router.get("/{tenant_id}/{binding_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:channel-challenge",
    rate_limit="standard",
    idempotency="challenge",
    audit_event="none-public-challenge",
    public_reason="provider-verify-token-required",
)
async def challenge(tenant_id: uuid.UUID, binding_id: uuid.UUID, request: Request):
    async with get_tenant_session(tenant_id) as session:
        binding = await session.scalar(
            select(ChannelBinding).where(ChannelBinding.tenant_id == tenant_id, ChannelBinding.id == binding_id)
        )
    if binding is None or binding.provider not in {"messenger", "whatsapp"}:
        raise HTTPException(404, "Callback unavailable")
    credentials = await unseal(binding.credentials_ciphertext)
    if request.query_params.get("hub.mode") != "subscribe" or not hmac.compare_digest(
        request.query_params.get("hub.verify_token", "").encode(), credentials["verify_token"].encode()
    ):
        raise HTTPException(403, "Challenge rejected")
    return PlainTextResponse(request.query_params.get("hub.challenge", "")[:1024])


@webhook_router.post("/{tenant_id}/{binding_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:channel-callback",
    rate_limit="standard",
    idempotency="provider-event-id",
    audit_event="channels.message.receive",
    public_reason="provider-signature-and-binding-required",
)
async def webhook(tenant_id: uuid.UUID, binding_id: uuid.UUID, request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Message batch too large")
        body.extend(chunk)
    async with get_tenant_session(tenant_id) as session:
        binding = await session.scalar(
            select(ChannelBinding).where(ChannelBinding.tenant_id == tenant_id, ChannelBinding.id == binding_id)
        )
    if binding is None or not binding.enabled:
        raise HTTPException(404, "Callback unavailable")
    try:
        credentials = await unseal(binding.credentials_ciphertext)
        email_form = None
        if binding.provider == "mailgun" and request.headers.get("content-type", "").startswith("multipart/form-data"):
            # The stream was already bounded. Parse fields only; never fetch or persist attachments.
            async def replay_body():
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            bounded_request = Request(request.scope, replay_body)
            try:
                async with bounded_request.form(max_files=0, max_fields=30, max_part_size=MAX_BODY_BYTES) as form:
                    email_form = {str(key): value for key, value in form.items() if isinstance(value, str)}
            except (StarletteHTTPException, ValueError) as exc:
                raise ChannelError("unsupported_email_form") from exc
        messages = decode_messages(
            binding.provider,
            credentials,
            dict(request.headers),
            bytes(body),
            email_form=email_form,
        )
        for message in messages:
            await receive(binding, message)
    except ChannelError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"accepted": True}
