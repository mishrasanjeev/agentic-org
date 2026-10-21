# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Durable channel intake. A linked human delegates only their own current authority."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from starlette.requests import Request

from core.channels.providers import CATALOG, ChannelError, Message, send_reply
from core.channels.replies import final_answer
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_with_kek, resolve_tenant_kek
from core.database import get_tenant_session
from core.models.channel import ChannelBinding, ChannelDelivery, ChannelSenderLink
from core.models.user import User
from core.ownership import caller_from_request
from core.rbac import get_scopes_for_role


async def seal(value: Any, tenant_id: uuid.UUID) -> str:
    kek = await resolve_tenant_kek(tenant_id)
    return await asyncio.to_thread(encrypt_with_kek, json.dumps(value, separators=(",", ":")), kek)


async def unseal(value: str) -> Any:
    return json.loads(await asyncio.to_thread(decrypt_for_tenant, value))


def sender_hash(binding_id: uuid.UUID, sender: str) -> str:
    from core.config import settings

    return hmac.new(settings.secret_key.encode(), f"channel:{binding_id}:{sender}".encode(), hashlib.sha256).hexdigest()


async def receive(binding: ChannelBinding, message: Message) -> str:
    tid = binding.tenant_id
    digest = sender_hash(binding.id, message.sender)
    normalized = {"sender": message.sender, "text": message.text}
    content_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    is_link_request = CATALOG[binding.provider]["identity_link"] and message.text.startswith("/link ")
    ciphertext = await seal(
        {"sender": message.sender, "text": "Account-link request"}
        if is_link_request
        else {**normalized, "sender_hash": digest},
        tid,
    )
    now = datetime.now(UTC)
    async with get_tenant_session(tid) as session:
        # Serialize link consumption and receipt insertion for this binding.
        current = await session.scalar(
            select(ChannelBinding)
            .where(ChannelBinding.id == binding.id, ChannelBinding.tenant_id == tid)
            .with_for_update()
        )
        if current is None or not current.enabled or current.credentials_ciphertext != binding.credentials_ciphertext:
            raise ChannelError("binding_changed_or_disabled")
        prior = await session.scalar(
            select(ChannelDelivery).where(
                ChannelDelivery.tenant_id == tid,
                ChannelDelivery.binding_id == binding.id,
                ChannelDelivery.event_id == message.event_id,
            )
        )
        if prior:
            if prior.content_hash != content_hash:
                raise ChannelError("event_id_content_mismatch")
            return "duplicate"
        linked = None
        if CATALOG[binding.provider]["identity_link"]:
            if message.text.startswith("/link "):
                code = message.text[6:].strip()
                challenge = await session.scalar(
                    select(ChannelSenderLink)
                    .where(
                        ChannelSenderLink.tenant_id == tid,
                        ChannelSenderLink.binding_id == binding.id,
                        ChannelSenderLink.challenge_hash == hashlib.sha256(code.encode()).hexdigest(),
                        ChannelSenderLink.expires_at > now,
                    )
                    .with_for_update()
                )
                if challenge and challenge.user_id == binding.owner_user_id:
                    # Re-linking replaces the previous delegation; never allows a sender to become an admin.
                    from sqlalchemy import delete

                    await session.execute(
                        delete(ChannelSenderLink).where(
                            ChannelSenderLink.tenant_id == tid,
                            ChannelSenderLink.binding_id == binding.id,
                            ChannelSenderLink.sender_hash == digest,
                        )
                    )
                    challenge.sender_hash, challenge.challenge_hash = digest, None
                    challenge.expires_at = now + timedelta(days=30)
                    status = "linked"
                else:
                    status = "link_rejected"
                # Never retain an account-link code in the message history.
            else:
                linked = await session.scalar(
                    select(ChannelSenderLink).where(
                        ChannelSenderLink.tenant_id == tid,
                        ChannelSenderLink.binding_id == binding.id,
                        ChannelSenderLink.sender_hash == digest,
                        ChannelSenderLink.challenge_hash.is_(None),
                        ChannelSenderLink.expires_at > now,
                    )
                )
                status = "pending" if linked else "needs_review"
        else:
            status = "needs_review"
        session.add(
            ChannelDelivery(
                tenant_id=tid,
                binding_id=binding.id,
                event_id=message.event_id,
                content_hash=content_hash,
                message_ciphertext=ciphertext,
                user_id=linked.user_id if linked else None,
                delegation_id=linked.id if linked else None,
                status=status,
                reply_mode=getattr(current, "reply_mode", "status")
                if getattr(current, "auto_reply", False)
                else "status",
            )
        )
    return status


def _request_for_user(user: User) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/channel-dispatch",
            "headers": [],
            "query_string": b"",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 0),
            "scheme": "http",
        }
    )
    request.state.tenant_id = str(user.tenant_id)
    request.state.auth_mode = "legacy"
    request.state.scopes = get_scopes_for_role(user.role)
    request.state.claims = {
        "agenticorg:user_id": str(user.id),
        "role": user.role,
        "domain": user.domain,
        "sub": str(user.id),
    }
    return request


async def _answer_authorized(
    binding: ChannelBinding, receipt: ChannelDelivery, message: dict, expected_scope: dict | None = None
) -> bool:
    """Recheck private-sender delegation and current target access after execution."""
    from core.models.agent import Agent
    from core.ownership import require_agent_mutable

    # Intake authenticated this binding-scoped identity before tenant encryption.
    # Worker signing keys need not equal the API key; legacy receipts fail closed.
    digest = message.get("sender_hash")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    now = datetime.now(UTC)
    async with get_tenant_session(binding.tenant_id) as session:
        user = await session.scalar(
            select(User).where(
                User.tenant_id == binding.tenant_id,
                User.id == receipt.user_id,
                User.status == "active",
            )
        )
        if (
            user is None
            or user.id != binding.owner_user_id
            or (user.sessions_invalid_before and user.sessions_invalid_before > receipt.updated_at)
        ):
            return False
        link = await session.scalar(
            select(ChannelSenderLink).where(
                ChannelSenderLink.tenant_id == binding.tenant_id,
                ChannelSenderLink.binding_id == binding.id,
                ChannelSenderLink.id == receipt.delegation_id,
                ChannelSenderLink.user_id == user.id,
                ChannelSenderLink.sender_hash == digest,
                ChannelSenderLink.challenge_hash.is_(None),
                ChannelSenderLink.expires_at > now,
            )
        )
        if link is None or (user.sessions_invalid_before and user.sessions_invalid_before > link.created_at):
            return False
        if not (set(get_scopes_for_role(user.role)) & {"agents:write", "agenticorg:admin"}):
            return False
        target = await session.scalar(
            select(Agent).where(
                Agent.tenant_id == binding.tenant_id,
                Agent.id == binding.target_id,
            )
        )
        try:
            require_agent_mutable(target, caller_from_request(_request_for_user(user)))
        except HTTPException:
            return False
        return (
            target is not None
            and target.status in {"active", "shadow"}
            and (expected_scope is None or _conversation_scope(binding, receipt, target) == expected_scope)
        )


def _conversation_scope(binding: ChannelBinding, receipt: ChannelDelivery, target: Any) -> dict | None:
    # The existing explicit answer-mode opt-in gates text context, never execution state.
    config = getattr(target, "config", None) or {}
    if (
        binding.provider != "telegram"
        or binding.target_kind != "agent"
        or not binding.auto_reply
        or getattr(binding, "reply_mode", "status") != "agent_answer"
        or getattr(receipt, "reply_mode", "status") != "agent_answer"
        or getattr(target, "authorized_tools", None) != []
        or not isinstance(config, dict)
        or config.get("traffic_split") is not None
    ):
        return None
    return {"agent_id": str(target.id), "company_id": str(target.company_id) if target.company_id else None}


def _conversation_pair(
    prior: ChannelDelivery, message: dict, result: dict, digest: str, scope: dict, credentials: dict
) -> list[dict[str, str]]:
    if (
        prior.error_code
        or message.get("sender_hash") != digest
        or message.get("conversation_scope") != scope
        or message.get("answer_sent") is not True
        or result.get("agent_id") != scope["agent_id"]
        or result.get("requested_agent_id") != scope["agent_id"]
        or result.get("tool_calls")
        or execution_status(result)[0] != "completed"
    ):
        return []
    question = message.get("text")
    if not isinstance(question, str):
        return []
    safe_question = final_answer({"status": "completed", "output": {"answer": question}}, credentials)
    answer = final_answer(result, credentials)
    if safe_question is None or answer is None:
        return []
    return [{"role": "user", "text": safe_question}, {"role": "assistant", "text": answer}]


async def _recent_answer_context(
    binding: ChannelBinding, receipt: ChannelDelivery, message: dict, scope: dict, credentials: dict
) -> dict:
    from core.conversation.context import context_block

    digest = message.get("sender_hash")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or receipt.delegation_id is None:
        return {}
    async with get_tenant_session(binding.tenant_id) as session:
        rows = (
            await session.scalars(
                select(ChannelDelivery)
                .where(
                    ChannelDelivery.tenant_id == binding.tenant_id,
                    ChannelDelivery.binding_id == binding.id,
                    ChannelDelivery.user_id == receipt.user_id,
                    ChannelDelivery.delegation_id == receipt.delegation_id,
                    ChannelDelivery.reply_mode == "agent_answer",
                    ChannelDelivery.status == "completed",
                    ChannelDelivery.created_at > datetime.now(UTC) - timedelta(minutes=30),
                    ChannelDelivery.created_at < receipt.created_at,
                )
                .order_by(ChannelDelivery.created_at.desc(), ChannelDelivery.id.desc())
                .limit(4)
            )
        ).all()
    pairs: list[list[dict[str, str]]] = []
    for prior in rows:
        if not prior.result_ciphertext:
            break
        pair = _conversation_pair(
            prior,
            await unseal(prior.message_ciphertext),
            await unseal(prior.result_ciphertext),
            digest,
            scope,
            credentials,
        )
        if not pair:
            break
        pairs.insert(0, pair)
    return context_block([turn for pair in pairs for turn in pair])


def execution_status(result: dict[str, Any]) -> tuple[str, str | None]:
    """Never promote an absent, failed, or still-running result into success."""
    state = result.get("status")
    if (
        result.get("hitl_required")
        or result.get("hitl_trigger")
        or state
        in {
            "hitl_triggered",
            "waiting_hitl",
            "waiting",
            "paused",
            "awaiting_approval",
        }
    ):
        return "awaiting_approval", None
    if state in {"completed", "success", "succeeded"} and not result.get("error"):
        return "completed", None
    return "needs_review", "execution_not_completed"


async def dispatch_one(tenant_id: uuid.UUID, binding_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        binding = await session.scalar(
            select(ChannelBinding)
            .where(ChannelBinding.tenant_id == tenant_id, ChannelBinding.id == binding_id)
            .with_for_update(skip_locked=True)
        )
        if binding is None or not binding.enabled:
            return "disabled_or_busy"
        running = await session.scalar(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.tenant_id == tenant_id,
                ChannelDelivery.binding_id == binding_id,
                ChannelDelivery.status == "processing",
            )
            .limit(1)
        )
        if running:
            # A lost worker may have already performed side effects. Do not replay the action.
            if running.updated_at < now - timedelta(minutes=35):
                running.status, running.error_code = "needs_review", "execution_outcome_unconfirmed"
            return "busy"
        receipt = await session.scalar(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.tenant_id == tenant_id,
                ChannelDelivery.binding_id == binding_id,
                ChannelDelivery.status == "pending",
            )
            .order_by(ChannelDelivery.created_at)
            .limit(1)
            .with_for_update()
        )
        if receipt is None:
            return "empty"
        user = await session.scalar(
            select(User).where(User.id == receipt.user_id, User.tenant_id == tenant_id, User.status == "active")
        )
        delegation = None
        if not receipt.human_approved:
            delegation = await session.scalar(
                select(ChannelSenderLink).where(
                    ChannelSenderLink.tenant_id == tenant_id,
                    ChannelSenderLink.binding_id == binding_id,
                    ChannelSenderLink.id == receipt.delegation_id,
                    ChannelSenderLink.user_id == receipt.user_id,
                    ChannelSenderLink.challenge_hash.is_(None),
                    ChannelSenderLink.expires_at > now,
                )
            )
        if (
            user is None
            or user.id != binding.owner_user_id
            or (user.sessions_invalid_before and user.sessions_invalid_before > receipt.updated_at)
            or (not receipt.human_approved and delegation is None)
            or (delegation and user.sessions_invalid_before and user.sessions_invalid_before > delegation.created_at)
        ):
            receipt.status, receipt.error_code = "needs_review", "user_authority_revoked"
            return "revoked"
        receipt.status, receipt.updated_at = "processing", now
        delivery_id = receipt.id
    message = None
    conversation_scope = None
    conversation_credentials = None
    confirmed_message = None
    result: dict[str, Any] = {}
    try:
        message = await unseal(receipt.message_ciphertext)
        request = _request_for_user(user)
        scopes = request.state.scopes
        if "agents:write" not in scopes and "agenticorg:admin" not in scopes:
            raise ChannelError("user_cannot_execute")
        if binding.target_kind == "agent":
            from api.deps import get_user_domains
            from api.v1.agents import run_agent
            from core.models.agent import Agent
            from core.ownership import require_agent_mutable

            async with get_tenant_session(tenant_id) as session:
                target = await session.scalar(
                    select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == binding.target_id)
                )
                require_agent_mutable(target, caller_from_request(request))
                # New channel dispatch must not inherit the legacy paused-run opt-out.
                if target is None or target.status not in {"shadow", "active"}:
                    raise HTTPException(409, "Channel agent target is not runnable")

            conversation_scope = _conversation_scope(binding, receipt, target)
            payload: dict[str, Any] = {
                "inputs": {"task": message["text"]},
                "correlation_id": f"channel:{binding.id}:{sender_hash(binding.id, message['sender'])}",
            }
            if conversation_scope is not None:
                request.state.channel_answer_scope = conversation_scope
                conversation_credentials = await unseal(binding.credentials_ciphertext)
                payload["context"] = await _recent_answer_context(
                    binding, receipt, message, conversation_scope, conversation_credentials
                )
            result = await asyncio.wait_for(
                run_agent(
                    binding.target_id,
                    request,
                    payload,
                    str(tenant_id),
                    get_user_domains(request),
                    caller_from_request(request),
                ),
                timeout=1800,
            )
        else:
            if user.role != "admin":
                raise ChannelError("workflow_channel_requires_admin")
            from api.v1.workflows import WorkflowRunTrigger, run_workflow

            background = BackgroundTasks()
            result = await run_workflow(
                binding.target_id,
                background,
                request,
                WorkflowRunTrigger(payload={"input": message["text"], "channel": binding.provider}),
                str(tenant_id),
            )
            await asyncio.wait_for(background(), timeout=1800)
            from core.models.workflow import WorkflowRun

            async with get_tenant_session(tenant_id) as session:
                run = await session.scalar(
                    select(WorkflowRun).where(
                        WorkflowRun.tenant_id == tenant_id,
                        WorkflowRun.id == uuid.UUID(result["run_id"]),
                    )
                )
                if run is None:
                    raise ChannelError("workflow_result_unavailable")
                result = {"run_id": str(run.id), "status": run.status, "result": run.result, "error": bool(run.error)}
        sealed_result = await seal(result, tenant_id)
        status, error_code = execution_status(result)
    except (HTTPException, ChannelError, TimeoutError) as exc:
        sealed_result = await seal({"message": "Execution requires review in AgenticOrg."}, tenant_id)
        status, error_code = "needs_review", (str(exc) if isinstance(exc, ChannelError) else "execution_not_completed")
    # enterprise-gate: broad-except-ok reason=durable-channel-claim-is-not-replayed-after-ambiguous-execution
    except Exception:
        sealed_result = await seal({"message": "Execution outcome needs review in AgenticOrg."}, tenant_id)
        status, error_code = "needs_review", "execution_outcome_unconfirmed"
    async with get_tenant_session(tenant_id) as session:
        row = await session.get(ChannelDelivery, delivery_id)
        if row is not None:
            # Keep the binding claim until answer delivery is acknowledged; a follow-up
            # must not race the first turn's context stamp. Ambiguous sends are not retried.
            row.status = status if conversation_scope is None else "processing"
            row.result_ciphertext, row.error_code = sealed_result, error_code
    async with get_tenant_session(tenant_id) as session:
        live_binding = await session.get(ChannelBinding, binding_id)
    if (
        message is not None
        and binding.auto_reply
        and CATALOG[binding.provider]["reply"]
        and live_binding is not None
        and live_binding.enabled
        and getattr(live_binding, "auto_reply", binding.auto_reply)
        and live_binding.credentials_ciphertext == binding.credentials_ciphertext
    ):
        try:
            credentials = conversation_credentials or await unseal(binding.credentials_ciphertext)
            text = (
                f"Task status: {status.replace('_', ' ')}. Open AgenticOrg to view the result or approve the next step."
            )
            answer_requested = (
                binding.provider == "telegram"
                and binding.target_kind == "agent"
                and getattr(binding, "reply_mode", "status") == "agent_answer"
                and getattr(receipt, "reply_mode", "status") == "agent_answer"
                and getattr(live_binding, "reply_mode", "status") == "agent_answer"
                and live_binding.owner_user_id == binding.owner_user_id
                and live_binding.provider == binding.provider
                and live_binding.target_kind == binding.target_kind
                and live_binding.target_id == binding.target_id
            )
            if answer_requested and status == "completed":
                answer = final_answer(result, credentials)
                if answer is not None and await _answer_authorized(binding, receipt, message, conversation_scope):
                    text = answer
                    if conversation_scope is not None:
                        confirmed_message = {**message, "conversation_scope": conversation_scope, "answer_sent": True}
                else:
                    text = "Task status: completed. Answer withheld; open AgenticOrg to review the result."
            await send_reply(
                binding.provider,
                credentials,
                message["sender"],
                text,
            )
        except ChannelError:
            confirmed_message = None
            async with get_tenant_session(tenant_id) as session:
                row = await session.get(ChannelDelivery, delivery_id)
                if row:
                    row.error_code = "reply_delivery_unconfirmed"
    if conversation_scope is not None:
        confirmed_ciphertext = await seal(confirmed_message, tenant_id) if confirmed_message else None
        async with get_tenant_session(tenant_id) as session:
            row = await session.get(ChannelDelivery, delivery_id)
            if row is not None:
                row.status = status
                if confirmed_ciphertext is not None:
                    row.message_ciphertext = confirmed_ciphertext
    return status
