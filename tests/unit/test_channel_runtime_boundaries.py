# SPDX-License-Identifier: Apache-2.0
"""Channel authority and durable-state branches, without external side effects.

Database outcomes are scripted here; PostgreSQL RLS and HTTP/worker integration
are independently exercised in the local-stack suites.
"""

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from api.v1 import channels
from core.channels import runtime
from core.channels.providers import ChannelError, Message
from core.models.channel import ChannelBinding, ChannelDelivery, ChannelSenderLink


class Session:
    def __init__(self, *, scalar=(), scalars=(), gets=()):
        self.values = iter(scalar)
        self.lists = iter(scalars)
        self.gets = iter(gets)
        self.queries = []
        self.added = []
        self.deleted = []

    async def scalar(self, query):
        self.queries.append(query)
        return next(self.values)

    async def scalars(self, query):
        self.queries.append(query)
        rows = next(self.lists)
        return SimpleNamespace(all=lambda: rows)

    async def get(self, model, key):
        self.queries.append((model, key))
        return next(self.gets)

    async def execute(self, query):
        self.deleted.append(query)

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.added.append(row)

    async def flush(self):
        return None


def database(monkeypatch, module, tenant_id, *frames):
    frames = iter(frames)

    @asynccontextmanager
    async def context(tid):
        assert tid == tenant_id
        yield next(frames)

    monkeypatch.setattr(module, "get_tenant_session", context)


@pytest.fixture
def state(monkeypatch):
    now = datetime.now(UTC)
    tid, owner = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=owner, tenant_id=tid, role="admin", domain="finance", sessions_invalid_before=None)
    binding = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tid,
        owner_user_id=owner,
        provider="telegram",
        name="Synthetic bot",
        target_kind="agent",
        target_id=uuid.uuid4(),
        enabled=True,
        auto_reply=False,
        credentials_ciphertext="encrypted-credentials",
    )
    receipt = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner,
        delegation_id=uuid.uuid4(),
        human_approved=False,
        status="pending",
        error_code=None,
        updated_at=now,
        message_ciphertext="encrypted-message",
        result_ciphertext=None,
        created_at=now,
    )
    delegation = SimpleNamespace(
        id=receipt.delegation_id,
        user_id=owner,
        created_at=now,
        challenge_hash=None,
        sender_hash="sender-digest",
        expires_at=now + timedelta(days=30),
    )
    target = SimpleNamespace(
        id=binding.target_id,
        tenant_id=tid,
        status="active",
        visibility="personal",
        owner_user_id=owner,
        domain="finance",
    )
    monkeypatch.setattr(runtime, "seal", AsyncMock(side_effect=lambda value, tid: "sealed:" + json.dumps(value)))
    monkeypatch.setattr(runtime, "unseal", AsyncMock(return_value={"sender": "10001", "text": "Read-only task"}))
    return SimpleNamespace(tid=tid, user=user, binding=binding, receipt=receipt, delegation=delegation, target=target)


@pytest.mark.asyncio
async def test_seal_roundtrip_is_encrypted_and_tenant_key_is_resolved(monkeypatch):
    # Exercise the real encryption helpers using the local test encryption key.
    tid = uuid.uuid4()
    resolve = AsyncMock(return_value="")
    monkeypatch.setattr(runtime, "resolve_tenant_kek", resolve)
    value = {"text": "synthetic private message"}
    encrypted = await runtime.seal(value, tid)
    assert "synthetic private message" not in encrypted
    assert await runtime.unseal(encrypted) == value
    resolve.assert_awaited_once_with(tid)


def test_sender_digest_is_bound_to_connection():
    first, second = uuid.uuid4(), uuid.uuid4()
    assert runtime.sender_hash(first, "10001") == runtime.sender_hash(first, "10001")
    assert runtime.sender_hash(first, "10001") != runtime.sender_hash(second, "10001")
    assert "10001" not in runtime.sender_hash(first, "10001")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "disabled", "rotated", "duplicate", "changed_duplicate"])
async def test_receive_refuses_changed_binding_and_content_replay(monkeypatch, state, mode):
    b = state.binding
    message = Message(event_id="event-1", sender="10001", text="Read-only task")
    content_hash = hashlib.sha256(
        json.dumps({"sender": message.sender, "text": message.text}, sort_keys=True).encode()
    ).hexdigest()
    current = SimpleNamespace(
        enabled=mode != "disabled", credentials_ciphertext="rotated" if mode == "rotated" else b.credentials_ciphertext
    )
    prior = SimpleNamespace(content_hash=content_hash if mode == "duplicate" else "different")
    session = Session(scalar=[None if mode == "missing" else current, prior])
    database(monkeypatch, runtime, state.tid, session)
    if mode == "duplicate":
        assert await runtime.receive(b, message) == "duplicate"
    else:
        with pytest.raises(
            ChannelError,
            match="event_id_content_mismatch" if mode == "changed_duplicate" else "binding_changed_or_disabled",
        ):
            await runtime.receive(b, message)
    assert session.added == []
    assert state.tid in session.queries[0].compile().params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["linked", "unlinked", "email", "valid_link", "wrong_owner_link", "expired_link"])
async def test_receive_links_once_and_never_persists_link_secret(monkeypatch, state, mode):
    b = state.binding
    if mode == "email":
        b.provider = "mailgun"
    link_request = mode.endswith("link")
    text = "/link synthetic-private-challenge" if link_request else "Read-only task"
    challenge = SimpleNamespace(
        user_id=state.user.id if mode == "valid_link" else uuid.uuid4(),
        sender_hash=None,
        challenge_hash="hash",
        expires_at=datetime.now(UTC),
    )
    third = challenge if mode in {"valid_link", "wrong_owner_link"} else state.delegation if mode == "linked" else None
    session = Session(scalar=[b, None, third])
    database(monkeypatch, runtime, state.tid, session)
    result = await runtime.receive(b, Message(event_id="event-2", sender="10001", text=text))
    expected = (
        "pending"
        if mode == "linked"
        else "linked"
        if mode == "valid_link"
        else "link_rejected"
        if link_request
        else "needs_review"
    )
    assert result == expected
    row = session.added[0]
    assert isinstance(row, ChannelDelivery)
    assert row.status == expected
    assert row.user_id == (state.user.id if mode == "linked" else None)
    if link_request:
        assert "synthetic-private-challenge" not in row.message_ciphertext
        assert "Account-link request" in row.message_ciphertext
        assert "expires_at" in str(session.queries[2])
    if mode == "valid_link":
        assert challenge.challenge_hash is None
        assert challenge.sender_hash == runtime.sender_hash(b.id, "10001")
        assert challenge.expires_at > datetime.now(UTC) + timedelta(days=29)
        assert state.tid in session.deleted[0].compile().params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "disabled", "running", "lost_worker", "empty"])
async def test_dispatch_never_replays_busy_or_unknown_execution(monkeypatch, state, mode):
    b, receipt = state.binding, state.receipt
    b.enabled = mode != "disabled"
    if mode == "lost_worker":
        receipt.updated_at -= timedelta(minutes=36)
    session = Session(
        scalar=[None if mode == "missing" else b, receipt if mode in {"running", "lost_worker"} else None, None]
    )
    database(monkeypatch, runtime, state.tid, session)
    result = await runtime.dispatch_one(state.tid, b.id)
    assert result == (
        "disabled_or_busy"
        if mode in {"missing", "disabled"}
        else "busy"
        if mode in {"running", "lost_worker"}
        else "empty"
    )
    runtime.unseal.assert_not_awaited()
    if mode == "lost_worker":
        assert (receipt.status, receipt.error_code) == ("needs_review", "execution_outcome_unconfirmed")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_user", "wrong_owner", "no_delegation", "session_revoked", "link_revoked"])
async def test_dispatch_rechecks_current_human_authority(monkeypatch, state, mode):
    user, row = state.user, state.receipt
    if mode == "wrong_owner":
        user.id = uuid.uuid4()
    if mode in {"session_revoked", "link_revoked"}:
        user.sessions_invalid_before = datetime.now(UTC) - timedelta(seconds=1)
        if mode == "session_revoked":
            row.updated_at -= timedelta(minutes=1)
        else:
            state.delegation.created_at -= timedelta(minutes=1)
    session = Session(
        scalar=[
            state.binding,
            None,
            row,
            None if mode == "missing_user" else user,
            None if mode == "no_delegation" else state.delegation,
        ]
    )
    database(monkeypatch, runtime, state.tid, session)
    assert await runtime.dispatch_one(state.tid, state.binding.id) == "revoked"
    assert (row.status, row.error_code) == ("needs_review", "user_authority_revoked")
    runtime.unseal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "completed",
        "paused",
        "retired",
        "timeout",
        "unknown_error",
        "hitl",
        "reply_failed",
        "binding_disabled",
        "credentials_rotated",
        "permission_lost",
    ],
)
async def test_dispatch_agent_preserves_approval_and_never_retries_ambiguous_outcome(monkeypatch, state, mode):
    from api.v1 import agents

    b, row = state.binding, state.receipt
    row.human_approved = True
    b.auto_reply = mode in {"reply_failed", "binding_disabled", "credentials_rotated"}
    if mode in {"paused", "retired"}:
        state.target.status = mode
    if mode == "permission_lost":
        state.user.role = "viewer"
    live = SimpleNamespace(
        enabled=mode != "binding_disabled",
        credentials_ciphertext="rotated" if mode == "credentials_rotated" else b.credentials_ciphertext,
    )
    initial = Session(scalar=[b, None, row, state.user])
    target = Session(scalar=[state.target])
    saved = Session(gets=[row])
    current = Session(gets=[live])
    failure = Session(gets=[row])
    frames = [initial, saved, current] if mode == "permission_lost" else [initial, target, saved, current, failure]
    database(monkeypatch, runtime, state.tid, *frames)
    run = AsyncMock(
        return_value={"status": "waiting_hitl" if mode == "hitl" else "completed", "result": "private result"}
    )
    if mode in {"timeout", "unknown_error"}:
        run.side_effect = TimeoutError() if mode == "timeout" else RuntimeError("private provider detail")
    monkeypatch.setattr(agents, "run_agent", run)
    reply = AsyncMock(side_effect=ChannelError("provider_error"))
    monkeypatch.setattr(runtime, "send_reply", reply)
    result = await runtime.dispatch_one(state.tid, b.id)
    expected = (
        "needs_review"
        if mode in {"paused", "retired", "timeout", "unknown_error", "permission_lost"}
        else "awaiting_approval"
        if mode == "hitl"
        else "completed"
    )
    assert result == row.status == expected
    if mode in {"paused", "retired", "permission_lost"}:
        run.assert_not_awaited()
    else:
        run.assert_awaited_once()
        assert run.call_args.args[2]["inputs"] == {"task": "Read-only task"}
    assert "private provider detail" not in row.result_ciphertext
    if mode == "reply_failed":
        reply.assert_awaited_once()
        assert "private result" not in reply.call_args.args[3]
        assert row.error_code == "reply_delivery_unconfirmed"
    else:
        reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "approval", "missing_result", "not_admin"])
async def test_workflow_dispatch_waits_for_durable_execution_and_reads_final_state(monkeypatch, state, mode):
    from api.v1 import workflows

    state.binding.target_kind = "workflow"
    state.receipt.human_approved = True
    if mode == "not_admin":
        state.user.role = "developer"
    completed = SimpleNamespace(
        id=uuid.uuid4(),
        status="waiting_hitl" if mode == "approval" else "completed",
        result={"value": "synthetic"},
        error=None,
    )
    initial = Session(scalar=[state.binding, None, state.receipt, state.user])
    final = Session(scalar=[None if mode == "missing_result" else completed])
    saved, live = Session(gets=[state.receipt]), Session(gets=[state.binding])
    database(
        monkeypatch,
        runtime,
        state.tid,
        *([initial, saved, live] if mode == "not_admin" else [initial, final, saved, live]),
    )
    finished = AsyncMock()

    async def start(target_id, background: BackgroundTasks, request, body, tenant_id):
        assert target_id == state.binding.target_id
        assert body.payload["input"] == "Read-only task"
        background.add_task(finished)
        return {"run_id": str(completed.id), "status": "running"}

    start_mock = AsyncMock(side_effect=start)
    monkeypatch.setattr(workflows, "run_workflow", start_mock)
    result = await runtime.dispatch_one(state.tid, state.binding.id)
    if mode == "not_admin":
        start_mock.assert_not_awaited()
        assert result == "needs_review"
    else:
        finished.assert_awaited_once()
        assert result == (
            "needs_review" if mode == "missing_result" else "awaiting_approval" if mode == "approval" else "completed"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["human", "machine", "inactive", "viewer"])
async def test_channel_actor_requires_current_active_human_creator(monkeypatch, state, mode):
    if mode == "viewer":
        state.user.role = "viewer"
    request = runtime._request_for_user(state.user)
    if mode == "machine":
        request.state.auth_mode = "api_key"
    session = Session(scalar=[None if mode == "inactive" else state.user])
    database(monkeypatch, channels, state.tid, session)
    if mode == "human":
        assert await channels._actor(request, state.tid) is state.user
        assert state.tid in session.queries[0].compile().params.values()
    else:
        with pytest.raises(HTTPException) as error:
            await channels._actor(request, state.tid)
        assert error.value.status_code == 403


def management(monkeypatch, state, *frames):
    database(monkeypatch, channels, state.tid, *frames)
    monkeypatch.setattr(channels, "_actor", AsyncMock(return_value=state.user))
    monkeypatch.setattr(channels, "seal", AsyncMock(return_value="sealed-credentials"))
    monkeypatch.setattr(
        channels, "unseal", AsyncMock(return_value={"text": "Read-only task", "verify_token": "synthetic-verification"})
    )
    return runtime._request_for_user(state.user)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_binding_lookup_always_scopes_to_human_owner(monkeypatch, state, missing):
    session = Session(scalar=[None if missing else state.binding])
    database(monkeypatch, channels, state.tid, session)
    if missing:
        with pytest.raises(HTTPException) as error:
            await channels._binding(state.tid, state.binding.id, state.user)
        assert error.value.status_code == 404
    else:
        assert await channels._binding(state.tid, state.binding.id, state.user) is state.binding
    assert {state.tid, state.binding.id, state.user.id}.issubset(set(session.queries[0].compile().params.values()))


@pytest.mark.asyncio
async def test_catalog_and_binding_list_never_return_credentials(monkeypatch, state):
    result = await channels.catalog()
    assert result["existing_setup"]["mcp"].endswith("remote-mcp")
    assert any(row["id"] == "telegram" for row in result["providers"])
    session = Session(scalars=[[state.binding]])
    request = management(monkeypatch, state, session)
    result = await channels.list_bindings(request, str(state.tid))
    assert len(result["bindings"]) == 1
    assert "encrypted-credentials" not in json.dumps(result)
    assert {state.tid, state.user.id}.issubset(set(session.queries[0].compile().params.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "agent",
        "workflow",
        "bad_credentials",
        "reply_unavailable",
        "workflow_nonadmin",
        "inactive_workflow",
        "foreign_agent",
    ],
)
async def test_create_binding_validates_provider_target_and_starts_disabled(monkeypatch, state, mode):
    provider = "mailgun" if mode == "reply_unavailable" else "telegram"
    target_kind = "workflow" if mode in {"workflow", "workflow_nonadmin", "inactive_workflow"} else "agent"
    credentials = {"access_token": "123:synthetic", "webhook_secret": "synthetic-webhook-secret-123456"}
    if provider == "mailgun":
        credentials = {"signing_key": "synthetic-signing", "receiver_id": "inbox@example.test"}
    if mode == "bad_credentials":
        credentials = {}
    if mode == "workflow_nonadmin":
        state.user.role = "developer"
    target = (
        None
        if mode == "foreign_agent"
        else SimpleNamespace(is_active=False)
        if mode == "inactive_workflow"
        else SimpleNamespace(is_active=True)
        if target_kind == "workflow"
        else state.target
    )
    initial, save = Session(scalar=[target]), Session()
    request = management(monkeypatch, state, initial, save)
    body = channels.BindingIn(
        name=" Synthetic bot ",
        provider=provider,
        target_kind=target_kind,
        target_id=state.target.id,
        credentials=credentials,
        auto_reply=mode == "reply_unavailable",
    )
    if mode in {"agent", "workflow"}:
        result = await channels.create_binding(body, request, str(state.tid))
        assert result["enabled"] is False
        assert result["name"] == "Synthetic bot"
        assert "synthetic-secret" not in json.dumps(result)
        row = save.added[0]
        assert isinstance(row, ChannelBinding)
        assert (row.owner_user_id, row.tenant_id, row.credentials_ciphertext) == (
            state.user.id,
            state.tid,
            "sealed-credentials",
        )
    else:
        with pytest.raises(HTTPException) as error:
            await channels.create_binding(body, request, str(state.tid))
        assert (
            error.value.status_code
            == {
                "bad_credentials": 422,
                "reply_unavailable": 422,
                "workflow_nonadmin": 403,
                "inactive_workflow": 404,
                "foreign_agent": 404,
            }[mode]
        )
        assert save.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disable", "rotate", "enable", "missing", "bad_credentials", "reply_unavailable"])
async def test_rotation_and_disable_revoke_links_and_queued_work(monkeypatch, state, mode):
    if mode == "reply_unavailable":
        state.binding.provider = "mailgun"
    lookup = Session(scalar=[state.binding])
    update = Session(scalar=[None if mode == "missing" else state.binding], scalars=[[state.receipt]])
    request = management(monkeypatch, state, lookup, update)
    kwargs = {"enabled": mode != "disable", "auto_reply": mode in {"enable", "reply_unavailable"}}
    if mode in {"rotate", "bad_credentials"}:
        kwargs["credentials"] = (
            {}
            if mode == "bad_credentials"
            else {"access_token": "123:synthetic", "webhook_secret": "synthetic-webhook-secret-123456"}
        )
    body = channels.BindingUpdate(**kwargs)
    if mode in {"missing", "bad_credentials", "reply_unavailable"}:
        with pytest.raises(HTTPException) as error:
            await channels.update_binding(state.binding.id, body, request, str(state.tid))
        assert error.value.status_code == (404 if mode == "missing" else 422)
    else:
        result = await channels.update_binding(state.binding.id, body, request, str(state.tid))
        assert result["enabled"] is (mode == "enable")
        if mode in {"disable", "rotate"}:
            assert (state.receipt.status, state.receipt.error_code) == ("needs_review", "binding_authority_revoked")
            assert state.tid in update.deleted[0].compile().params.values()
        if mode == "rotate":
            assert state.binding.credentials_ciphertext == "sealed-credentials"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["enabled", "disabled", "email", "changed"])
async def test_link_code_is_hashed_bounded_and_requires_enabled_binding(monkeypatch, state, mode):
    state.binding.enabled = mode != "disabled"
    if mode == "email":
        state.binding.provider = "mailgun"
    lookup, write = Session(scalar=[state.binding]), Session(scalar=[None if mode == "changed" else state.binding])
    request = management(monkeypatch, state, lookup, write)
    if mode == "enabled":
        result = await channels.link_code(state.binding.id, request, str(state.tid))
        row = write.added[0]
        assert isinstance(row, ChannelSenderLink)
        assert row.challenge_hash == hashlib.sha256(result["command"][6:].encode()).hexdigest()
        assert row.expires_at < datetime.now(UTC) + timedelta(minutes=11)
        assert result["delegation_days"] == 30
    else:
        with pytest.raises(HTTPException) as error:
            await channels.link_code(state.binding.id, request, str(state.tid))
        assert error.value.status_code == 409
        assert write.added == []


@pytest.mark.asyncio
async def test_inbox_decrypts_results_only_after_owner_lookup(monkeypatch, state):
    state.receipt.result_ciphertext = "encrypted-result"
    lookup, listing = Session(scalar=[state.binding]), Session(scalars=[[state.receipt]])
    request = management(monkeypatch, state, lookup, listing)
    result = await channels.deliveries(state.binding.id, request, str(state.tid))
    item = result["deliveries"][0]
    assert item["text"] == "Read-only task"
    assert item["result"]["text"] == "Read-only task"
    assert "LIMIT" in str(listing.queries[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["approve", "disabled", "changed", "missing", "already_executed", "ambiguous"])
async def test_approval_cannot_replay_uncertain_or_completed_execution(monkeypatch, state, mode):
    state.binding.enabled = mode != "disabled"
    state.receipt.status = "completed" if mode == "already_executed" else "needs_review"
    state.receipt.error_code = "execution_outcome_unconfirmed" if mode == "ambiguous" else None
    lookup = Session(scalar=[state.binding])
    write = Session(scalar=[None if mode == "changed" else state.binding, None if mode == "missing" else state.receipt])
    request = management(monkeypatch, state, lookup, write)
    if mode == "approve":
        assert await channels.approve(state.binding.id, state.receipt.id, request, str(state.tid)) == {
            "status": "pending"
        }
        assert state.receipt.human_approved is True
        assert state.receipt.user_id == state.user.id
    else:
        with pytest.raises(HTTPException) as error:
            await channels.approve(state.binding.id, state.receipt.id, request, str(state.tid))
        assert error.value.status_code == (404 if mode == "missing" else 409)


def http_request(body=b"{}", headers=(), query=b""):
    sent = False

    async def receive_body():
        nonlocal sent
        assert not sent
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": headers,
            "query_string": query,
            "app": object(),
        },
        receive_body,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "missing", "wrong_provider", "bad_token"])
async def test_meta_challenge_is_scoped_and_secret_verified(monkeypatch, state, mode):
    state.binding.provider = "telegram" if mode == "wrong_provider" else "whatsapp"
    session = Session(scalar=[None if mode == "missing" else state.binding])
    management(monkeypatch, state, session)
    query = b"hub.mode=subscribe&hub.verify_token=synthetic-verification&hub.challenge=example-challenge"
    if mode == "bad_token":
        query = b"hub.mode=subscribe&hub.verify_token=wrong"
    request = http_request(query=query)
    if mode == "valid":
        result = await channels.challenge(state.tid, state.binding.id, request)
        assert result.body == b"example-challenge"
    else:
        with pytest.raises(HTTPException) as error:
            await channels.challenge(state.tid, state.binding.id, request)
        assert error.value.status_code == (403 if mode == "bad_token" else 404)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "missing", "disabled", "too_large", "bad_signature", "mailgun_multipart", "mailgun_invalid_form"]
)
async def test_callback_bounds_body_and_does_not_ingest_bad_signatures(monkeypatch, state, mode):
    state.binding.enabled = mode != "disabled"
    if mode.startswith("mailgun"):
        state.binding.provider = "mailgun"
    session = Session(scalar=[None if mode == "missing" else state.binding])
    management(monkeypatch, state, session)

    def decode(*args, **kwargs):
        if mode == "bad_signature":
            raise ChannelError("invalid_signature")
        return [Message(event_id="event-1", sender="10001", text="Read-only task")]

    decoder = []

    def decode_record(*args, **kwargs):
        decoder.append(kwargs)
        return decode(*args, **kwargs)

    monkeypatch.setattr(channels, "decode_messages", decode_record)
    receive = AsyncMock()
    monkeypatch.setattr(channels, "receive", receive)
    body, headers = b"{}", []
    if mode == "too_large":
        body = b"x" * (channels.MAX_BODY_BYTES + 1)
    if mode.startswith("mailgun"):
        headers = [(b"content-type", b"multipart/form-data; boundary=sample")]
        body = b'--sample\r\nContent-Disposition: form-data; name="body-plain"\r\n\r\nExample\r\n--sample--\r\n'
        if mode == "mailgun_invalid_form":
            body = (
                b'--sample\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n'
                b"\r\nfile\r\n--sample--\r\n"
            )
    request = http_request(body, headers)
    if mode in {"valid", "mailgun_multipart"}:
        assert await channels.webhook(state.tid, state.binding.id, request) == {"accepted": True}
        receive.assert_awaited_once()
        if mode == "mailgun_multipart":
            assert decoder[0]["email_form"] == {"body-plain": "Example"}
    else:
        with pytest.raises(HTTPException) as error:
            await channels.webhook(state.tid, state.binding.id, request)
        assert (
            error.value.status_code
            == {"missing": 404, "disabled": 404, "too_large": 413, "bad_signature": 403, "mailgun_invalid_form": 403}[
                mode
            ]
        )
        receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_enumerates_tenants_and_publishes_only_identifiers(monkeypatch):
    from core.tasks import channel_tasks

    tid, binding_id = uuid.uuid4(), uuid.uuid4()
    all_tenants, tenant_rows = Session(scalars=[[tid]]), Session(scalars=[[binding_id]])

    @asynccontextmanager
    async def all_session():
        yield all_tenants

    monkeypatch.setattr(channel_tasks, "async_session_factory", all_session)
    database(monkeypatch, channel_tasks, tid, tenant_rows)
    published = []
    monkeypatch.setattr(channel_tasks.dispatch, "apply_async", lambda **kwargs: published.append(kwargs))
    assert await channel_tasks._sweep() == 1
    assert published == [{"args": [str(tid), str(binding_id)], "queue": "workflows"}]
    assert tid in tenant_rows.queries[0].compile().params.values()


def test_task_entrypoints_use_async_runner_and_typed_identifiers(monkeypatch):
    from core.tasks import channel_tasks

    tid, bid = uuid.uuid4(), uuid.uuid4()
    captured = []

    def run(coro):
        captured.append(coro)
        coro.close()
        return "verified"

    monkeypatch.setattr(channel_tasks, "run_async", run)
    assert channel_tasks.dispatch.run(str(tid), str(bid)) == "verified"
    assert channel_tasks.sweep.run() == "verified"
    assert len(captured) == 2
